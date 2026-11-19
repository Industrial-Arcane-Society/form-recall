//! Local, explicitly saved construction histories for exact repeated requests.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::ErrorKind,
    path::{Path, PathBuf},
};

const MAX_CASES: usize = 64;
const MAX_PROMPT_BYTES: usize = 2048;
const MAX_FEATURES: usize = 64;
const MAX_STORE_BYTES: u64 = 512 * 1024;
const STORE_FILE: &str = "reviewed-cases.json";

fn root(data_dir: &Path) -> PathBuf {
    data_dir.join("reviewed-cases")
}

fn digest(bytes: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn is_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn record_id(prompt: &str, geometry_fingerprint: &str) -> String {
    digest(format!("{geometry_fingerprint}:{prompt}"))
}

fn is_feature(feature: &Value) -> bool {
    matches!(feature["operation"].as_str(), Some("add" | "cut"))
        && feature["kind"]
            .as_str()
            .is_some_and(crate::native_plan_geometry::supports_native_kind)
}

fn valid_row(row: &Value) -> bool {
    row["prompt"]
        .as_str()
        .zip(row["source_geometry_fingerprint"].as_str())
        .is_some_and(|(prompt, fingerprint)| {
            !prompt.is_empty()
                && prompt.len() <= MAX_PROMPT_BYTES
                && is_digest(fingerprint)
                && row["id"].as_str() == Some(&record_id(prompt, fingerprint))
                && row["features"].as_array().is_some_and(|features| {
                    !features.is_empty()
                        && features.len() <= MAX_FEATURES
                        && features.iter().all(is_feature)
                })
        })
}

fn read(root: &Path) -> Result<Vec<Value>, String> {
    let path = root.join(STORE_FILE);
    let metadata = match fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("无法读取已验收案例：{error}")),
    };
    if metadata.len() > MAX_STORE_BYTES {
        return Err("已验收案例文件超过大小限制。".into());
    }
    let bytes = fs::read(path).map_err(|e| format!("无法读取已验收案例：{e}"))?;
    let value: Value = serde_json::from_slice(&bytes).map_err(|e| format!("案例文件无效：{e}"))?;
    if value["schema_version"] != 1 {
        return Err("已验收案例文件版本不受支持。".into());
    }
    value["cases"]
        .as_array()
        .filter(|rows| rows.len() <= MAX_CASES && rows.iter().all(valid_row))
        .cloned()
        .ok_or_else(|| "已验收案例内容无法验证。".into())
}

fn update(data_dir: &Path, edit: impl FnOnce(&mut Vec<Value>)) -> Result<Vec<Value>, String> {
    let root = root(data_dir);
    fs::create_dir_all(&root).map_err(|e| format!("无法创建案例目录：{e}"))?;
    let lock = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .open(root.join("store.lock"))
        .map_err(|e| format!("无法打开案例写入锁：{e}"))?;
    lock.lock().map_err(|e| format!("无法锁定案例文件：{e}"))?;
    let mut rows = read(&root)?;
    edit(&mut rows);
    let bytes = serde_json::to_vec_pretty(&json!({"schema_version":1,"cases":rows}))
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_STORE_BYTES {
        return Err("案例将超过存储大小限制。".into());
    }
    crate::native_model_io::atomic_write(&root.join(STORE_FILE), &bytes)?;
    Ok(rows)
}

pub(crate) fn list(data_dir: &Path) -> Result<Vec<Value>, String> {
    read(&root(data_dir))
}

pub(crate) fn remove(data_dir: &Path, id: &str) -> Result<Vec<Value>, String> {
    if !is_digest(id) {
        return Err("案例 ID 无效。".into());
    }
    update(data_dir, |rows| rows.retain(|row| row["id"] != id))
}

pub(crate) fn save(data_dir: &Path, generation: u64) -> Result<Vec<Value>, String> {
    let active_root = crate::native_active_model::root_from_data_dir(data_dir);
    crate::native_active_model::with_generation(&active_root, generation, |active| {
        let projection = crate::agent_host::active_model_acceptance_projection(active);
        let row = accepted_case(active, &projection)?;
        update(data_dir, |rows| {
            rows.retain(|old| old["prompt"] != row["prompt"]);
            rows.insert(0, row);
            rows.truncate(MAX_CASES);
        })
    })
}

fn accepted_case(active: &Value, projection: &Value) -> Result<Value, String> {
    if active["origin"] != "agent"
        || projection["model"]["acceptance_confirmed"] != true
        || projection["active_generation"] != active["generation"]
        || projection["model"]["geometric"]["status"] != "passed"
        || projection["model"]["visual"]["status"] != "no_references"
    {
        return Err("只有通过当前宿主验收的纯文字 Agent 模型可以保存为案例。".into());
    }
    let turns = &active["user_turns_manifest"]["turns"];
    if !turns.as_array().is_some_and(|rows| rows.len() == 1)
        || turns[0]["started_from_empty_model"] != true
        || !turns[0]["reference_ids"]
            .as_array()
            .is_some_and(Vec::is_empty)
        || !active["reference_manifest"]["images"]
            .as_array()
            .is_some_and(Vec::is_empty)
    {
        return Err("案例需要从空白零件开始的单轮纯文字建模请求。".into());
    }
    let fingerprint = active["geometry_fingerprint"]
        .as_str()
        .ok_or("案例缺少几何指纹。")?;
    let prompt = turns[0]["raw_user_prompt"]
        .as_str()
        .ok_or("案例缺少原始描述。")?
        .trim();
    let models = &active["cad_state"]["plan"]["features"];
    let features = models
        .as_array()
        .ok_or("案例缺少 CADPlan 特征。")?
        .iter()
        .filter(|feature| feature["enabled"] != false)
        .map(|feature| json!({"kind":feature["kind"],"operation":feature["operation"]}))
        .collect::<Vec<_>>();
    let row = json!({"id":record_id(prompt, fingerprint),"prompt":prompt,
        "source_geometry_fingerprint":fingerprint,"features":features});
    if !valid_row(&row) {
        return Err("案例的文字、几何指纹或特征数量超出可保存范围。".into());
    }
    Ok(row)
}

pub(crate) fn context(data_dir: &Path, prompt: &str) -> Result<Option<String>, String> {
    let Some(row) = list(data_dir)?
        .into_iter()
        .find(|row| row["prompt"] == prompt.trim())
    else {
        return Ok(None);
    };
    let steps = row["features"]
        .as_array()
        .expect("case file already validated")
        .iter()
        .map(|feature| {
            let operation = feature["operation"].as_str().unwrap_or_default();
            let kind = feature["kind"].as_str().unwrap_or_default();
            format!("{operation} {kind}")
        })
        .collect::<Vec<_>>()
        .join(" → ");
    Ok(Some(format!(
        "【本地已验收案例】相同文字请求曾通过宿主验收。历史特征顺序：{steps}。可参考构造顺序；请依据本轮用户描述、目录证据和当前内核确定尺寸并重新建模、复核。案例本身不提供尺寸或本轮验收证据。"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agent_host::{self, AgentHostState, TestSetup};
    use crate::native_active_model;
    use crate::native_cad_session::{self, BackendOutcome, ExportOutcome};
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};
    use tauri::ipc::{Channel, InvokeResponseBody};

    const PROMPT: &str = "创建长方体 10×8×5 mm";

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir()
                .join(format!("aimodeler-reviewed-{}-{nonce}", std::process::id()));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn accepted_fixture(data_dir: &Path) -> Value {
        let references = json!({"schema_version":1,"images":[]});
        let turns = json!({"schema_version":1,"session_id":"fixture","turns":[
            {"turn_id":"u1","raw_user_prompt":PROMPT,"reference_ids":[],"started_from_empty_model":true}
        ]});
        let references_path = data_dir.join("fixture-references.json");
        let turns_path = data_dir.join("fixture-turns.json");
        fs::write(&references_path, serde_json::to_vec(&references).unwrap()).unwrap();
        fs::write(&turns_path, serde_json::to_vec(&turns).unwrap()).unwrap();
        let plan = crate::cad_plan::create(
            "box",
            json!({"id":"body","kind":"box","operation":"add",
                "parameters":{"length":10,"width":8,"height":5},
                "sources":{"length":{"type":"derived","note":"fixture"},
                           "width":{"type":"derived","note":"fixture"},
                           "height":{"type":"derived","note":"fixture"}}}),
            PROMPT,
            None,
        );
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        let artifact = data_dir.join("fixture-report.json");
        fs::write(&artifact, b"{}").unwrap();
        let output_paths = json!({"geometry_report":artifact});
        let state = native_cad_session::complete_preview(
            &json!({"revision":1,"plan":plan,"active_model_generation":1}),
            BackendOutcome {
                revision: 1,
                geometry_fingerprint: fingerprint.clone(),
                output_paths: output_paths.clone(),
                preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":{
                    "state":"passed","execution_engine":"rust_c_abi_occt","revision":1,
                    "geometry_fingerprint":fingerprint,"valid":true,"closed_solid_count":1,
                    "validation":{"state":"valid"}
                }}),
            },
        )
        .unwrap()
        .state;
        let (reviewed, _) =
            crate::native_review_gate::aggregate(&state, &references_path, &turns_path).unwrap();
        let (assessed, dispatch) =
            native_cad_session::finish_assessment(&reviewed, &references_path, &turns_path)
                .unwrap();
        assert_eq!(dispatch.finish_state, "accepted");
        let mut finished = native_cad_session::complete_export(
            &assessed,
            ExportOutcome {
                revision: 1,
                geometry_fingerprint: fingerprint,
                output_paths,
            },
        )
        .unwrap();
        let evidence = crate::native_finish_evidence::current_snapshot(
            &finished,
            &references_path,
            &turns_path,
        )
        .unwrap();
        finished["finish_summary"]["evidence_fingerprint"] = json!(evidence.fingerprint);
        native_active_model::candidate_from_cad_state("agent", &finished, &references, &turns)
            .unwrap()
    }

    #[test]
    fn save_revalidates_generation_and_evidence_and_removal_survives_another_read() {
        let scratch = Scratch::new();
        let active_root = native_active_model::root_from_data_dir(&scratch.0);
        let mut candidate = accepted_fixture(&scratch.0);
        native_active_model::commit(&active_root, 0, &candidate).unwrap();
        assert!(save(&scratch.0, 0).is_err());
        let cases = save(&scratch.0, 1).unwrap();
        assert_eq!(save(&scratch.0, 1).unwrap(), cases);
        let id = cases[0]["id"].as_str().unwrap();
        let advice = context(&scratch.0, PROMPT).unwrap().unwrap();
        assert!(advice.contains("add box"));
        assert!(!advice.contains("10×8×5"));
        assert!(
            context(&scratch.0, "创建长方体 20×8×5 mm")
                .unwrap()
                .is_none()
        );
        assert!(remove(&scratch.0, id).unwrap().is_empty());
        assert!(context(&scratch.0, PROMPT).unwrap().is_none());

        candidate["cad_state"]["active_model_generation"] = json!(2);
        candidate["user_turns_manifest"]["turns"][0]["raw_user_prompt"] =
            json!("changed user intent");
        native_active_model::commit(&active_root, 1, &candidate).unwrap();
        assert!(save(&scratch.0, 2).is_err());
        assert!(list(&scratch.0).unwrap().is_empty());
    }

    #[test]
    fn saved_case_flows_to_acp_only_in_a_fresh_enabled_turn() {
        let scratch = Scratch::new();
        let dir = &scratch.0;
        let candidate = accepted_fixture(dir);
        let active_root = native_active_model::root_from_data_dir(dir);
        native_active_model::commit(&active_root, 0, &candidate).unwrap();
        let cases = save(dir, 1).unwrap();
        native_active_model::commit(
            &active_root,
            1,
            &json!({
                "origin":"native_ui","revision":2,"document":{"features":[]},
                "cad_state":{"plan":null,"revision":2,"active_model_generation":2},
                "output_paths":{}
            }),
        )
        .unwrap();

        let runtime = dir.join("runtime");
        let cli = runtime.join("dsh/lib/bin.js");
        fs::create_dir_all(cli.parent().unwrap()).unwrap();
        fs::create_dir_all(runtime.join("dsh/node_modules")).unwrap();
        fs::write(&cli, b"fixture marker").unwrap();
        let release = dir.join("acp-release");
        fs::write(&release, b"ready").unwrap();
        let setup = TestSetup {
            root: Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../../legacy")
                .canonicalize()
                .unwrap(),
            data_dir: dir.clone(),
            runtime: crate::dsh_runtime::RuntimePaths {
                root: runtime,
                node: std::env::var_os("AIMODELER_TEST_NODE")
                    .expect("fixture needs Node")
                    .into(),
                dsh_cli: cli,
                home: dir.join("dsh-home"),
            },
            dsh_args: vec![std::env::var_os("AIMODELER_TEST_ACP_FIXTURE").expect("fixture script")],
            dsh_env: vec![
                (
                    "AIMODELER_ACP_TEST_RELEASE_FILE".into(),
                    release.into_os_string(),
                ),
                ("AIMODELER_TEST_AGENT_SCENARIO".into(), "pure-qa".into()),
            ],
        };
        let host = AgentHostState::default();
        let send = |use_cases| {
            let events = Arc::new(Mutex::new(Vec::<Value>::new()));
            let collected = Arc::clone(&events);
            let channel = Channel::new(move |body| {
                if let InvokeResponseBody::Json(value) = body {
                    collected
                        .lock()
                        .unwrap()
                        .push(serde_json::from_str(&value).unwrap());
                }
                Ok(())
            });
            let response = agent_host::send_turn_with_fixture(
                &host,
                setup.clone(),
                "case-fixture".into(),
                PROMPT.into(),
                vec![],
                json!({"base_url":"https://api.deepseek.com/responses","model":"deepseek-flash",
                    "api_key":"fixture","reasoning_effort":"none"}),
                false,
                use_cases,
                channel,
            )
            .unwrap();
            let used = events
                .lock()
                .unwrap()
                .iter()
                .any(|event| event.pointer("/data/reviewed_case_used") == Some(&json!(true)));
            (response, used)
        };

        let (enabled, used) = send(true);
        assert!(used);
        assert!(enabled["reply"].as_str().unwrap().contains("add box"));
        let meta: Value =
            serde_json::from_slice(&fs::read(dir.join("agent-host/current-session.json")).unwrap())
                .unwrap();
        let manifest = Path::new(meta["cwd"].as_str().unwrap()).join("user-turns.json");
        let turns: Value = serde_json::from_slice(&fs::read(manifest).unwrap()).unwrap();
        assert_eq!(turns["turns"][0]["raw_user_prompt"], PROMPT);
        assert_eq!(turns["turns"][0]["started_from_empty_model"], true);
        let (follow_up, used) = send(true);
        assert!(!used);
        assert_eq!(follow_up["reply"], PROMPT);
        agent_host::reset(&host).unwrap();
        let (disabled, used) = send(false);
        assert!(!used);
        assert_eq!(disabled["reply"], PROMPT);
        agent_host::reset(&host).unwrap();
        remove(dir, cases[0]["id"].as_str().unwrap()).unwrap();
        let (removed, used) = send(true);
        assert!(!used);
        assert_eq!(removed["reply"], PROMPT);
        agent_host::shutdown(&host);
    }
}
