//! Transactional pointer to the one model currently shown and edited by the UI.
//!
//! Geometry producers may build immutable artifacts in their own staging
//! directories, then atomically publish a candidate here. Revision checks keep
//! a late Agent result from replacing a newer Quick/UI edit.
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

const ACTIVE_FILE: &str = "active-model.json";
const SCHEMA_VERSION: u64 = 1;
const MAX_RECORD_BYTES: u64 = 64 * 1024 * 1024;
static ACTIVE_MODEL_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn lock() -> Result<std::sync::MutexGuard<'static, ()>, String> {
    ACTIVE_MODEL_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .map_err(|_| "活动模型事务锁已损坏。".into())
}

/// Read the currently committed model record, if the workspace has one.
pub fn load(root: &Path) -> Result<Option<Value>, String> {
    let _guard = lock()?;
    load_unlocked(root)
}

/// Publish a complete model candidate only if the caller's snapshot generation
/// is still active. Artifacts must already be fully written before this call.
pub fn commit(root: &Path, expected_generation: u64, candidate: &Value) -> Result<Value, String> {
    let _guard = lock()?;
    commit_unlocked(root, expected_generation, candidate)
}

/// Commit an active model together with a user-visible file write. The target
/// file is staged beside its destination and only published after the active
/// generation has been checked under the transaction lock. If publishing the
/// active record fails, the previous target file is restored before returning.
pub fn commit_with_file(
    root: &Path,
    expected_generation: u64,
    candidate: &Value,
    target: &Path,
    bytes: &[u8],
) -> Result<Value, String> {
    let _guard = lock()?;
    fs::create_dir_all(root).map_err(|e| format!("无法创建活动模型目录：{e}"))?;
    let current = load_unlocked(root)?;
    let actual_generation = current
        .as_ref()
        .and_then(|record| record.get("generation"))
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if actual_generation != expected_generation {
        return Err(format!(
            "活动模型已变化：期望 generation {expected_generation}，当前为 {actual_generation}；拒绝提交旧事务。"
        ));
    }
    let staged_target = stage_file(target, bytes)?;
    let record = match build_record_with_staged(
        actual_generation,
        candidate,
        Some((target, staged_target.as_path())),
    ) {
        Ok(record) => record,
        Err(error) => {
            let _ = fs::remove_file(&staged_target);
            return Err(error);
        }
    };
    let active_bytes = match serde_json::to_vec_pretty(&record) {
        Ok(bytes) => bytes,
        Err(error) => {
            let _ = fs::remove_file(&staged_target);
            return Err(error.to_string());
        }
    };
    let backup = match backup_destination(target) {
        Ok(backup) => backup,
        Err(error) => {
            let _ = fs::remove_file(&staged_target);
            return Err(error);
        }
    };
    if let Err(error) = fs::rename(&staged_target, target) {
        let _ = fs::remove_file(&staged_target);
        if let Some(backup) = backup {
            let _ = fs::rename(backup, target);
        }
        return Err(format!("无法发布保存文件：{error}"));
    }
    if let Err(error) = crate::native_model_io::atomic_write(&root.join(ACTIVE_FILE), &active_bytes)
    {
        let rollback = rollback_destination(target, backup);
        return match rollback {
            Ok(()) => Err(format!("活动模型提交失败，已恢复目标文件：{error}")),
            Err(rollback_error) => Err(format!(
                "活动模型提交失败：{error}；并且目标文件回滚失败：{rollback_error}"
            )),
        };
    }
    if let Some(backup) = backup {
        let _ = fs::remove_file(backup);
    }
    Ok(record)
}

fn commit_unlocked(
    root: &Path,
    expected_generation: u64,
    candidate: &Value,
) -> Result<Value, String> {
    fs::create_dir_all(root).map_err(|e| format!("无法创建活动模型目录：{e}"))?;
    let current = load_unlocked(root)?;
    let actual_generation = current
        .as_ref()
        .and_then(|record| record.get("generation"))
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if actual_generation != expected_generation {
        return Err(format!(
            "活动模型已变化：期望 generation {expected_generation}，当前为 {actual_generation}；拒绝提交旧事务。"
        ));
    }
    let record = build_record(actual_generation, candidate)?;
    let bytes = serde_json::to_vec_pretty(&record).map_err(|e| e.to_string())?;
    crate::native_model_io::atomic_write(&root.join(ACTIVE_FILE), &bytes)?;
    Ok(record)
}

fn build_record(actual_generation: u64, candidate: &Value) -> Result<Value, String> {
    build_record_with_staged(actual_generation, candidate, None)
}

fn build_record_with_staged(
    actual_generation: u64,
    candidate: &Value,
    staged_output: Option<(&Path, &Path)>,
) -> Result<Value, String> {
    validate_candidate_with_staged(candidate, staged_output)?;
    let generation = actual_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_millis();
    let mut document = candidate["document"].clone();
    document["revision"] = json!(generation);
    document["model_generation"] = json!(generation);
    document["cad_revision"] = candidate["revision"].clone();
    document["geometry_fingerprint"] = candidate["geometry_fingerprint"].clone();
    document["geometric_status"] = candidate
        .get("geometric_status")
        .cloned()
        .unwrap_or_else(|| json!("not_built"));
    document["visual_status"] = candidate
        .get("visual_status")
        .cloned()
        .unwrap_or_else(|| json!("not_reviewed"));
    document["finish_summary"] = candidate
        .get("finish_summary")
        .cloned()
        .unwrap_or(Value::Null);
    let record = json!({
        "schema_version":SCHEMA_VERSION,
        "generation":generation,
        "model_id":format!("native-model-{}-{now_ms}", std::process::id()),
        "origin":candidate["origin"],
        "revision":candidate["revision"],
        "geometry_fingerprint":candidate["geometry_fingerprint"],
        "cadplan":candidate["cadplan"],
        "cad_state":candidate.get("cad_state").cloned().unwrap_or(Value::Null),
        "reference_manifest":candidate.get("reference_manifest").cloned().unwrap_or(Value::Null),
        "user_turns_manifest":candidate.get("user_turns_manifest").cloned().unwrap_or(Value::Null),
        "document":document,
        "output_paths":candidate.get("output_paths").cloned().unwrap_or_else(|| json!({})),
        "saved_project_path":candidate.get("saved_project_path").cloned().unwrap_or(Value::Null),
        "finish_summary":candidate.get("finish_summary").cloned().unwrap_or(Value::Null),
        "geometric_status":candidate.get("geometric_status").cloned().unwrap_or_else(|| json!("not_built")),
        "visual_status":candidate.get("visual_status").cloned().unwrap_or_else(|| json!("not_reviewed")),
        "committed_at_unix_ms":now_ms,
    });
    Ok(record)
}

fn stage_file(target: &Path, bytes: &[u8]) -> Result<PathBuf, String> {
    use std::io::Write;
    static NEXT_STAGE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let parent = target
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    if !parent.is_dir() {
        return Err("保存文件的目标目录不存在。".into());
    }
    let name = target
        .file_name()
        .ok_or("保存路径必须包含文件名。")?
        .to_string_lossy();
    let stage = parent.join(format!(
        ".{name}.active-txn-{}-{}.tmp",
        std::process::id(),
        NEXT_STAGE.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    ));
    let result = (|| {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&stage)
            .map_err(|error| format!("无法创建保存暂存文件：{error}"))?;
        file.write_all(bytes)
            .map_err(|error| format!("无法写入保存暂存文件：{error}"))?;
        file.sync_all()
            .map_err(|error| format!("无法同步保存暂存文件：{error}"))?;
        Ok(stage.clone())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&stage);
    }
    result
}

fn backup_destination(target: &Path) -> Result<Option<PathBuf>, String> {
    static NEXT_BACKUP: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    match fs::symlink_metadata(target) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("无法检查保存目标：{error}")),
        Ok(metadata) if !metadata.is_file() => Err("保存目标已存在但不是普通文件。".into()),
        Ok(_) => {
            let parent = target
                .parent()
                .filter(|path| !path.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            let name = target
                .file_name()
                .ok_or("保存路径必须包含文件名。")?
                .to_string_lossy();
            let backup = parent.join(format!(
                ".{name}.active-backup-{}-{}",
                std::process::id(),
                NEXT_BACKUP.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            ));
            fs::rename(target, &backup).map_err(|error| format!("无法备份原保存目标：{error}"))?;
            Ok(Some(backup))
        }
    }
}

fn rollback_destination(target: &Path, backup: Option<PathBuf>) -> Result<(), String> {
    if target.exists() {
        fs::remove_file(target).map_err(|error| format!("无法删除未提交的目标文件：{error}"))?;
    }
    if let Some(backup) = backup {
        fs::rename(backup, target).map_err(|error| format!("无法恢复目标文件备份：{error}"))?;
    }
    Ok(())
}

/// Return the next transaction generation; zero means there is no active model.
pub fn generation(root: &Path) -> Result<u64, String> {
    Ok(load(root)?
        .and_then(|record| record.get("generation").and_then(Value::as_u64))
        .unwrap_or(0))
}

pub fn root_from_data_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("native-active-model")
}

/// Adapt a committed native CAD state into the DocumentState contract consumed
/// by the Vue tree and viewport. The active generation replaces the
/// session-local CAD revision in `document.revision` during commit.
pub fn candidate_from_cad_state(
    origin: &str,
    state: &Value,
    reference_manifest: &Value,
    user_turns_manifest: &Value,
) -> Result<Value, String> {
    let plan = state.get("plan").filter(|value| value.is_object()).cloned();
    let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
    let output_paths = state
        .get("output_paths")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let (mut document, fingerprint) = if let Some(plan) = plan.as_ref() {
        let mesh_only_vbelt = origin == "quick"
            && state
                .pointer("/last_preview/local_geometry_checks/mesh_only")
                .and_then(Value::as_bool)
                == Some(true);
        let mut document = if mesh_only_vbelt {
            crate::native_model_io::cadplan_to_project_mesh_only_vbelt(plan)?
        } else {
            crate::native_model_io::cadplan_to_project(plan)?
        };
        if let Some(features) = document["features"].as_array_mut() {
            for (index, feature) in features.iter_mut().enumerate() {
                feature["index"] = json!(index);
                feature["description"] = json!(
                    feature
                        .get("name")
                        .and_then(Value::as_str)
                        .unwrap_or("CADPlan feature")
                );
            }
        }
        document["name"] = plan.get("name").cloned().unwrap_or_else(|| json!("Part1"));
        document["source_prompt"] = plan.get("source_prompt").cloned().unwrap_or(Value::Null);
        document["revision"] = json!(revision);
        document["current_path"] = state
            .get("saved_project_path")
            .cloned()
            .unwrap_or(Value::Null);
        document["mesh_path"] = output_paths.get("glb").cloned().unwrap_or(Value::Null);
        document["output_paths"] = output_paths.clone();
        document["finish_summary"] = state.get("finish_summary").cloned().unwrap_or(Value::Null);
        document["build_log"] = json!(
            state
                .get("actions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .rev()
                .take(8)
                .map(|action| format!(
                    "{} · revision {}",
                    action
                        .get("tool")
                        .and_then(Value::as_str)
                        .unwrap_or("CAD action"),
                    action
                        .get("revision")
                        .and_then(Value::as_i64)
                        .unwrap_or(revision)
                ))
                .collect::<Vec<_>>()
        );
        let preview = state
            .get("last_preview")
            .filter(|preview| preview.get("revision").and_then(Value::as_i64) == Some(revision));
        let checks = preview.and_then(|preview| preview.get("local_geometry_checks"));
        document["stats"] = json!({"faces":0,"triangles":checks.and_then(|v| v.get("triangle_count")).cloned().unwrap_or(json!(0)),
            "volume":checks.and_then(|v| v.get("volume_mm3")).cloned().unwrap_or(json!(0)),
            "size":checks.and_then(|v| v.get("dimensions_mm")).cloned().unwrap_or(json!([]))});
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan)?;
        (document, json!(fingerprint))
    } else {
        (
            json!({"format":"ai-modeler","version":1,"name":"Part1","source_prompt":"","features":[],
            "revision":revision,"current_path":null,"mesh_path":null,"output_paths":output_paths,
            "stats":{},"build_log":[],"finish_summary":null}),
            Value::Null,
        )
    };
    let geometric_status = state
        .get("last_preview")
        .filter(|preview| preview.get("revision").and_then(Value::as_i64) == Some(revision))
        .and_then(|preview| preview.pointer("/local_geometry_checks/state"))
        .cloned()
        .unwrap_or_else(|| json!("not_built"));
    let visual_status = state
        .get("finish_summary")
        .and_then(|summary| summary.get("visual_evidence"))
        .and_then(|summary| summary.get("all_references_covered"))
        .map(|covered| {
            if covered == &Value::Bool(true) {
                json!("covered")
            } else {
                json!("incomplete")
            }
        })
        .unwrap_or_else(|| json!("not_reviewed"));
    document["finish_summary"] = state.get("finish_summary").cloned().unwrap_or(Value::Null);
    Ok(
        json!({"origin":origin,"revision":revision,"geometry_fingerprint":fingerprint,
        "cadplan":plan,"document":document,"output_paths":output_paths,
        "finish_summary":state.get("finish_summary").cloned().unwrap_or(Value::Null),
        "geometric_status":geometric_status,"visual_status":visual_status,
        "cad_state":state,"reference_manifest":reference_manifest,"user_turns_manifest":user_turns_manifest,
        "saved_project_path":state.get("saved_project_path").cloned().unwrap_or(Value::Null)}),
    )
}

fn load_unlocked(root: &Path) -> Result<Option<Value>, String> {
    let path = root.join(ACTIVE_FILE);
    let metadata = match fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("无法读取活动模型元数据：{error}")),
    };
    if !metadata.is_file() || metadata.len() > MAX_RECORD_BYTES {
        return Err("活动模型记录不是普通文件或超过 64 MiB。".into());
    }
    let bytes = fs::read(&path).map_err(|e| format!("无法读取活动模型记录：{e}"))?;
    let record: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("活动模型记录 JSON 无效：{e}"))?;
    if record["schema_version"].as_u64() != Some(SCHEMA_VERSION)
        || record["generation"].as_u64().is_none()
        || record["model_id"].as_str().is_none()
    {
        return Err("活动模型记录版本或身份字段无效；未覆盖原记录。".into());
    }
    validate_candidate(&record)?;
    Ok(Some(record))
}

fn validate_candidate(candidate: &Value) -> Result<(), String> {
    validate_candidate_with_staged(candidate, None)
}

fn validate_candidate_with_staged(
    candidate: &Value,
    staged_output: Option<(&Path, &Path)>,
) -> Result<(), String> {
    if !candidate.is_object() {
        return Err("活动模型候选必须是 JSON 对象。".into());
    }
    if candidate
        .get("origin")
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
    {
        return Err("活动模型候选缺少 origin。".into());
    }
    if candidate
        .get("revision")
        .and_then(Value::as_i64)
        .is_none_or(|revision| revision < 0)
    {
        return Err("活动模型 revision 必须是非负整数。".into());
    }
    let document = candidate
        .get("document")
        .filter(|value| value.is_object())
        .ok_or("活动模型候选缺少 document 对象。")?;
    if !document.get("features").is_some_and(Value::is_array) {
        return Err("活动模型 document.features 必须为数组。".into());
    }
    let Some(plan) = candidate.get("cadplan").filter(|value| !value.is_null()) else {
        if !document["features"].as_array().is_some_and(Vec::is_empty) {
            return Err("没有 CADPlan 的活动文档必须是空模型。".into());
        }
        if candidate
            .get("geometry_fingerprint")
            .is_some_and(|value| !value.is_null())
        {
            return Err("空模型不能带 geometry fingerprint。".into());
        }
        return validate_artifacts(candidate, staged_output);
    };
    let validation = crate::cad_plan_validation::validate_plan(plan);
    let mesh_only_vbelt = candidate["origin"].as_str() == Some("quick")
        && document
            .pointer("/geometry_report/mesh_only")
            .and_then(Value::as_bool)
            == Some(true)
        && document
            .pointer("/geometry_report/state")
            .and_then(Value::as_str)
            == Some("mesh_only")
        && document
            .pointer("/geometry_report/execution_engine")
            .and_then(Value::as_str)
            == Some("rust_legacy_quick_triangle_mesh")
        && document
            .pointer("/geometry_report/brep_available")
            .and_then(Value::as_bool)
            == Some(false)
        && document
            .pointer("/geometry_report/catpart_available")
            .and_then(Value::as_bool)
            == Some(false)
        && document
            .pointer("/geometry_report/closed_solid_count")
            .is_some_and(Value::is_null)
        && crate::native_vbelt_pulley::is_mesh_only_legacy_quick_plan(plan);
    if validation.get("valid").and_then(Value::as_bool) != Some(true) && !mesh_only_vbelt {
        return Err(format!("活动模型 CADPlan 无效：{validation}"));
    }
    let fingerprint = crate::cad_plan::geometry_fingerprint(plan)?;
    if candidate
        .get("geometry_fingerprint")
        .and_then(Value::as_str)
        != Some(fingerprint.as_str())
    {
        return Err("活动模型 geometry fingerprint 与 CADPlan 不匹配。".into());
    }
    let expected_features = plan["features"].as_array().map_or(0, Vec::len);
    if document["features"].as_array().map_or(usize::MAX, Vec::len) != expected_features {
        return Err("活动模型 document 特征数与 CADPlan 不匹配。".into());
    }
    for (index, (planned, saved)) in plan["features"]
        .as_array()
        .into_iter()
        .flatten()
        .zip(document["features"].as_array().into_iter().flatten())
        .enumerate()
    {
        let planned_enabled = planned
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let saved_enabled = saved
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        if planned_enabled != saved_enabled {
            return Err(format!(
                "活动模型第 {index} 个特征的 enabled 与 CADPlan 不匹配。"
            ));
        }
    }
    validate_artifacts(candidate, staged_output)
}

fn validate_artifacts(
    candidate: &Value,
    staged_output: Option<(&Path, &Path)>,
) -> Result<(), String> {
    let Some(paths) = candidate.get("output_paths") else {
        return Ok(());
    };
    let Some(paths) = paths.as_object() else {
        return Err("活动模型 output_paths 必须是对象。".into());
    };
    for (kind, value) in paths {
        let text = value
            .as_str()
            .filter(|text| !text.trim().is_empty())
            .ok_or_else(|| format!("活动模型输出 {kind} 路径无效。"))?;
        let path = PathBuf::from(text);
        let check_path = staged_output
            .filter(|(target, _)| path == *target)
            .map(|(_, staged)| staged)
            .unwrap_or(path.as_path());
        let metadata = fs::metadata(check_path).map_err(|error| {
            format!(
                "活动模型输出 {kind} 不可读取（{}）：{error}",
                path.display()
            )
        })?;
        if !metadata.is_file() {
            return Err(format!("活动模型输出 {kind} 不是普通文件。"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_root(label: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "active-model-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    fn candidate(origin: &str) -> Value {
        let plan = crate::cad_plan::create(
            "fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{
                "length":{"type":"user","evidence":"10 mm"},"width":{"type":"user","evidence":"8 mm"},"height":{"type":"user","evidence":"5 mm"}}}),
            "fixture",
            None,
        );
        let document = crate::native_model_io::cadplan_to_project(&plan).unwrap();
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        json!({"origin":origin,"revision":1,"geometry_fingerprint":fingerprint,"cadplan":plan,
            "document":document,"output_paths":{},"finish_summary":null,
            "geometric_status":"not_built","visual_status":"not_reviewed"})
    }

    #[test]
    fn candidate_commits_atomically_and_increments_generation() {
        let root = temp_root("commit");
        let first = commit(&root, 0, &candidate("quick")).unwrap();
        assert_eq!(first["generation"], 1);
        assert_eq!(first["origin"], "quick");
        let second = commit(&root, 1, &candidate("agent")).unwrap();
        assert_eq!(second["generation"], 2);
        assert_eq!(load(&root).unwrap().unwrap(), second);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn stale_generation_or_invalid_fingerprint_cannot_replace_active_model() {
        let root = temp_root("stale");
        let first = commit(&root, 0, &candidate("quick")).unwrap();
        assert!(commit(&root, 0, &candidate("agent")).is_err());
        let mut invalid = candidate("agent");
        invalid["geometry_fingerprint"] = json!("wrong");
        assert!(commit(&root, 1, &invalid).is_err());
        assert_eq!(load(&root).unwrap().unwrap(), first);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn mesh_only_vbelt_conversion_is_restricted_to_quick_origin() {
        let parsed = crate::native_quick_parser::parse(
            "V带轮 基准直径100 槽数3 槽型A 内孔25 宽度30",
        );
        let plan = parsed["plan"].clone();
        assert!(crate::native_vbelt_pulley::is_mesh_only_legacy_quick_plan(&plan));
        let state = json!({
            "plan": plan,
            "revision": 1,
            "last_preview": {
                "revision": 1,
                "local_geometry_checks": {"mesh_only": true}
            }
        });

        let agent_result = candidate_from_cad_state("agent", &state, &json!({}), &json!({}));
        assert!(
            agent_result
                .unwrap_err()
                .contains("CADPlan validation failed")
        );
        assert!(candidate_from_cad_state("quick", &state, &json!({}), &json!({})).is_ok());
    }

    #[test]
    fn file_commit_publishes_target_and_active_record_together() {
        let root = temp_root("file-commit");
        let destination_dir = temp_root("file-target");
        fs::create_dir_all(&destination_dir).unwrap();
        let target = destination_dir.join("part.aimodel");
        fs::write(&target, b"previous project").unwrap();

        let mut save_candidate = candidate("save");
        save_candidate["output_paths"]["aimodel"] = json!(target.to_string_lossy());
        save_candidate["saved_project_path"] = json!(target.to_string_lossy());
        let committed =
            commit_with_file(&root, 0, &save_candidate, &target, b"new project").unwrap();
        assert_eq!(committed["generation"], 1);
        assert_eq!(fs::read(&target).unwrap(), b"new project");
        assert_eq!(
            committed["output_paths"]["aimodel"],
            target.to_string_lossy().as_ref()
        );
        assert_eq!(load(&root).unwrap().unwrap(), committed);
        assert_eq!(fs::read_dir(&destination_dir).unwrap().count(), 1);

        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(destination_dir).unwrap();
    }

    #[test]
    fn stale_file_commit_does_not_create_or_replace_target() {
        let root = temp_root("file-stale");
        let destination_dir = temp_root("file-stale-target");
        fs::create_dir_all(&destination_dir).unwrap();
        let missing_target = destination_dir.join("new.aimodel");
        let existing_target = destination_dir.join("existing.aimodel");
        fs::write(&existing_target, b"keep me").unwrap();
        let first = commit(&root, 0, &candidate("quick")).unwrap();

        assert!(commit_with_file(&root, 0, &candidate("save"), &missing_target, b"stale").is_err());
        assert!(
            commit_with_file(&root, 0, &candidate("save"), &existing_target, b"stale").is_err()
        );
        assert!(!missing_target.exists());
        assert_eq!(fs::read(&existing_target).unwrap(), b"keep me");
        assert_eq!(fs::read_dir(&destination_dir).unwrap().count(), 1);
        assert_eq!(load(&root).unwrap().unwrap(), first);

        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(destination_dir).unwrap();
    }
}
