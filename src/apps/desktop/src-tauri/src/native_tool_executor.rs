//! Session-owned native tool routing during the CAD domain migration.
//!
//! All calls share one transaction gate, including compatibility CAD calls, so
//! a native read cannot overwrite a concurrent Python CAD state commit.
use crate::native_feature_specs::feature_spec;
use crate::native_knowledge::{search_knowledge, search_recipes};
use crate::native_tool_bridge::ToolExecutor;
use crate::native_web::NativeWeb;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, TryLockError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub struct NativeToolExecutor {
    project_root: PathBuf,
    session_dir: PathBuf,
    fallback: Arc<dyn ToolExecutor>,
    web: NativeWeb,
    transaction: Mutex<()>,
    active: Mutex<HashMap<String, Arc<AtomicBool>>>,
    stopping: AtomicBool,
}

impl NativeToolExecutor {
    pub fn new(
        project_root: impl Into<PathBuf>,
        session_dir: impl Into<PathBuf>,
        online_enabled: bool,
        fallback: Arc<dyn ToolExecutor>,
    ) -> Result<Self, String> {
        let session_dir = session_dir.into();
        let web = NativeWeb::new(online_enabled);
        let web_state_path = session_dir.join("native-web-state.json");
        if web_state_path.exists() {
            web.import_state(&read_json_object(&web_state_path)?)?;
        }
        Ok(Self {
            project_root: project_root.into(),
            session_dir,
            fallback,
            web,
            transaction: Mutex::new(()),
            active: Mutex::new(HashMap::new()),
            stopping: AtomicBool::new(false),
        })
    }

    fn check_cancelled(&self, cancelled: &AtomicBool) -> Result<(), String> {
        if self.stopping.load(Ordering::Acquire) || cancelled.load(Ordering::Acquire) {
            Err("工具请求已取消。".into())
        } else {
            Ok(())
        }
    }

    fn run(
        &self,
        call_id: &str,
        name: &str,
        arguments: &Value,
        timeout: Duration,
        cancelled: &AtomicBool,
    ) -> Result<Value, String> {
        let started = Instant::now();
        let _transaction = loop {
            self.check_cancelled(cancelled)?;
            if started.elapsed() >= timeout {
                return Err("等待 CAD 会话事务超过工具期限。".into());
            }
            match self.transaction.try_lock() {
                Ok(guard) => break guard,
                Err(TryLockError::Poisoned(_)) => return Err("CAD 会话事务已损坏。".into()),
                Err(TryLockError::WouldBlock) => thread::sleep(Duration::from_millis(10)),
            }
        };
        let remaining = timeout.saturating_sub(started.elapsed());
        // Native reads and ledger refresh share the same locked state snapshot.
        // Compatibility CAD calls retain their existing state loader.
        let native = matches!(
            name,
            "cad_get_state"
                | "cad_get_feature_spec"
                | "cad_search_knowledge"
                | "cad_search_recipes"
                | "web_search"
                | "web_open_result"
        );
        if !native {
            return self.fallback.execute(call_id, name, arguments, remaining);
        }
        let mut state = self.load_cad_state()?;
        let (value, images, progress) = match name {
            "cad_get_state" => {
                check_keys(arguments, &[])?;
                (
                    crate::native_state_summary::summary(
                        &mut state,
                        &self.session_dir.join("reference-images.json"),
                        &self.session_dir.join("user-turns.json"),
                    )?,
                    vec![],
                    "已读取当前模型与证据状态。",
                )
            }
            "cad_get_feature_spec" => {
                check_keys(arguments, &["kind"])?;
                (
                    feature_spec(required_string(arguments, "kind")?)?,
                    vec![],
                    "已读取特征参数契约。",
                )
            }
            "cad_search_knowledge" => {
                check_keys(arguments, &["query", "limit", "include_visual_examples"])?;
                let query = optional_string(arguments, "query")?.unwrap_or("");
                let include_visual = match arguments.get("include_visual_examples") {
                    None => false,
                    Some(value) => value
                        .as_bool()
                        .ok_or("include_visual_examples 必须为布尔值。")?,
                };
                let reference_path = self.session_dir.join("reference-images.json");
                let has_references = if reference_path.exists() {
                    let references = read_json_object(&reference_path)?;
                    !references
                        .get("images")
                        .and_then(Value::as_array)
                        .ok_or("参考图清单 images 不是数组。")?
                        .is_empty()
                } else {
                    false
                };
                let response = search_knowledge(
                    &self.project_root,
                    query,
                    bounded_limit(arguments, 5, 10)?,
                    has_references,
                    include_visual,
                );
                (response.value, response.images, "本地工件知识检索完成。")
            }
            "cad_search_recipes" => {
                check_keys(arguments, &["query", "limit"])?;
                (
                    search_recipes(
                        &self.project_root,
                        optional_string(arguments, "query")?.unwrap_or(""),
                        bounded_limit(arguments, 8, 20)?,
                    )?,
                    vec![],
                    "本地建模配方检索完成。",
                )
            }
            "web_search" => {
                check_keys(arguments, &["query"])?;
                (
                    self.web
                        .search(required_string(arguments, "query")?, remaining, cancelled)?,
                    vec![],
                    "网页搜索完成。",
                )
            }
            "web_open_result" => {
                check_keys(arguments, &["result_id"])?;
                (
                    self.web.open_result(
                        required_string(arguments, "result_id")?,
                        remaining,
                        cancelled,
                    )?,
                    vec![],
                    "网页读取完成。",
                )
            }
            _ => return self.fallback.execute(call_id, name, arguments, remaining),
        };
        self.check_cancelled(cancelled)?;
        if name == "cad_search_knowledge" {
            state["knowledge_search"] = value.clone();
            state["knowledge_visual_examples"] =
                value.get("visual_examples").cloned().unwrap_or(json!([]));
        }
        let revision = state.get("revision").cloned().unwrap_or(json!(0));
        let action = json!({"kind": if name == "web_search" {"web_search"} else {"tool"},
            "tool":name,"revision":revision,"at":unix_seconds(),"execution_engine":"rust",
            "feature_kind":arguments.get("kind"),"query":arguments.get("query"),
            "result_id":arguments.get("result_id"),"diagnostics":value.get("diagnostics")});
        state
            .as_object_mut()
            .unwrap()
            .entry("actions")
            .or_insert(json!([]))
            .as_array_mut()
            .ok_or("CAD actions 不是数组。")?
            .push(action);
        if name.starts_with("web_") {
            let history = state
                .as_object_mut()
                .unwrap()
                .entry("web_search_history")
                .or_insert(json!([]))
                .as_array_mut()
                .ok_or("CAD web_search_history 不是数组。")?;
            history.push(json!({"kind":name,"query":arguments.get("query"),
                "result_id":arguments.get("result_id"),"url":value.get("url"),
                "results":value.get("results"),"diagnostics":value.get("diagnostics"),"at":unix_seconds()}));
            if history.len() > 64 {
                history.drain(..history.len() - 64);
            }
            write_json_atomic(
                &self.session_dir.join("native-web-state.json"),
                &self.web.export_state(),
            )?;
        }
        self.check_cancelled(cancelled)?;
        write_json_atomic(&self.session_dir.join("cad-state.json"), &state)?;
        Ok(tool_result(name, value, images, progress))
    }

    fn load_cad_state(&self) -> Result<Value, String> {
        let path = self.session_dir.join("cad-state.json");
        if path.exists() {
            read_json_object(&path)
        } else {
            Ok(json!({"plan":null,"revision":0,"actions":[],"web_search_history":[]}))
        }
    }
}

impl ToolExecutor for NativeToolExecutor {
    fn execute(
        &self,
        call_id: &str,
        name: &str,
        arguments: &Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        if !arguments.is_object() {
            return Err("工具参数必须为 JSON 对象。".into());
        }
        let cancelled = Arc::new(AtomicBool::new(false));
        {
            let mut active = self.active.lock().map_err(|_| "工具取消表已损坏。")?;
            if active.contains_key(call_id) {
                return Err("重复的 toolCallId。".into());
            }
            active.insert(call_id.to_owned(), Arc::clone(&cancelled));
        }
        let result = self.run(call_id, name, arguments, timeout, &cancelled);
        if let Ok(mut active) = self.active.lock() {
            active.remove(call_id);
        }
        result
    }

    fn cancel(&self, call_id: &str) -> bool {
        let native = self
            .active
            .lock()
            .ok()
            .and_then(|active| active.get(call_id).cloned());
        if let Some(cancelled) = &native {
            cancelled.store(true, Ordering::Release);
        }
        self.fallback.cancel(call_id) || native.is_some()
    }

    fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        if let Ok(active) = self.active.lock() {
            for cancelled in active.values() {
                cancelled.store(true, Ordering::Release);
            }
        }
        self.fallback.shutdown();
    }
}

fn required_string<'a>(arguments: &'a Value, key: &str) -> Result<&'a str, String> {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("{key} 必须为非空字符串。"))
}

fn optional_string<'a>(arguments: &'a Value, key: &str) -> Result<Option<&'a str>, String> {
    arguments
        .get(key)
        .map(|value| {
            value
                .as_str()
                .ok_or_else(|| format!("{key} 必须为字符串。"))
        })
        .transpose()
}

fn bounded_limit(arguments: &Value, default: usize, maximum: usize) -> Result<usize, String> {
    match arguments.get("limit") {
        None => Ok(default),
        Some(value) => value
            .as_u64()
            .filter(|n| *n >= 1 && *n <= maximum as u64)
            .map(|n| n as usize)
            .ok_or_else(|| format!("limit 必须为 1–{maximum} 的整数。")),
    }
}

fn check_keys(arguments: &Value, allowed: &[&str]) -> Result<(), String> {
    for key in arguments.as_object().ok_or("工具参数必须为对象。")?.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(format!("未知工具参数：{key}"));
        }
    }
    Ok(())
}

fn read_json_object(path: &Path) -> Result<Value, String> {
    let metadata =
        fs::metadata(path).map_err(|error| format!("无法读取状态 {}：{error}", path.display()))?;
    if metadata.len() > 32 * 1024 * 1024 {
        return Err("会话状态超过 32 MiB；原文件已保留。".into());
    }
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("状态文件损坏，原文件已保留（{}）：{error}", path.display()))?;
    if !value.is_object() {
        return Err("会话状态必须为 JSON 对象；原文件已保留。".into());
    }
    Ok(value)
}

fn write_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("rust.tmp");
    let bytes = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
    fs::rename(&temporary, path).map_err(|error| format!("会话状态提交失败：{error}"))
}

fn unix_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn tool_result(name: &str, mut value: Value, images: Vec<Value>, progress: &str) -> Value {
    if name == "cad_search_knowledge" && !images.is_empty() {
        value["visual_attachment_order"] =
            json!(images.iter().enumerate().map(|(index,image)|
            json!({"attachment_index":index+1,"model":image.get("model"),
                "category":image.get("category"),"role":image.get("role"),
                "source_kind":"knowledge_example","is_user_reference":false})).collect::<Vec<_>>());
    }
    json!({"text":format!("AI_MODELER_PROGRESS:{progress}\n{}",value),"images":images,
        "payload":{"isError":false,"execution_engine":"rust","tool":name}})
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    #[derive(Default)]
    struct Fallback {
        calls: AtomicUsize,
    }
    impl ToolExecutor for Fallback {
        fn execute(&self, _: &str, _: &str, _: &Value, _: Duration) -> Result<Value, String> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(json!({"text":"legacy"}))
        }
        fn cancel(&self, _: &str) -> bool {
            false
        }
    }
    fn fixture() -> (PathBuf, Arc<NativeToolExecutor>, Arc<Fallback>) {
        let dir = std::env::temp_dir().join(format!(
            "aimodeler-native-tool-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        let fallback = Arc::new(Fallback::default());
        let executor =
            Arc::new(NativeToolExecutor::new(&dir, &dir, false, fallback.clone()).unwrap());
        (dir, executor, fallback)
    }

    #[test]
    fn native_contract_and_disabled_web_do_not_execute_compatibility_backend() {
        let (dir, executor, fallback) = fixture();
        let original = json!({"plan":{"features":[{"id":"body","sources":{"length":{"type":"image_estimate","evidence":"original"}}}]},
            "revision":7,"actions":[],"extension":{"keep":true}});
        write_json_atomic(&dir.join("cad-state.json"), &original).unwrap();
        let result = executor
            .execute(
                "spec",
                "cad_get_feature_spec",
                &json!({"kind":"cylinder"}),
                Duration::from_secs(1),
            )
            .unwrap();
        assert_eq!(result["payload"]["execution_engine"], "rust");
        let domain: Value =
            serde_json::from_str(result["text"].as_str().unwrap().split_once('\n').unwrap().1)
                .unwrap();
        assert_eq!(domain["parameter_semantics"]["base_default"], true);
        let search = executor
            .execute(
                "search",
                "web_search",
                &json!({"query":"metric screw pitch"}),
                Duration::from_secs(1),
            )
            .unwrap();
        assert!(search["text"].as_str().unwrap().contains("disabled"));
        assert_eq!(fallback.calls.load(Ordering::SeqCst), 0);
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        assert_eq!(saved["plan"], original["plan"]);
        assert_eq!(saved["revision"], 7);
        assert_eq!(saved["extension"], original["extension"]);
        executor
            .execute("state", "cad_get_state", &json!({}), Duration::from_secs(1))
            .unwrap();
        assert_eq!(fallback.calls.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn corrupt_or_invalid_state_is_preserved_instead_of_silently_reset() {
        let (dir, executor, _) = fixture();
        fs::write(dir.join("cad-state.json"), b"{not-json").unwrap();
        assert!(
            executor
                .execute(
                    "corrupt",
                    "cad_get_feature_spec",
                    &json!({"kind":"box"}),
                    Duration::from_secs(1)
                )
                .is_err()
        );
        assert_eq!(fs::read(dir.join("cad-state.json")).unwrap(), b"{not-json");
        assert!(
            executor
                .execute(
                    "invalid",
                    "cad_get_feature_spec",
                    &json!({"kind":"box","ignore":true}),
                    Duration::from_secs(1)
                )
                .is_err()
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn queued_native_call_can_be_cancelled_without_waiting_for_cad_transaction() {
        let (dir, executor, fallback) = fixture();
        let lock = executor.transaction.lock().unwrap();
        let spawned = executor.clone();
        let worker = thread::spawn(move || {
            spawned.execute(
                "queued",
                "cad_get_feature_spec",
                &json!({"kind":"box"}),
                Duration::from_secs(10),
            )
        });
        let started = Instant::now();
        while !executor.active.lock().unwrap().contains_key("queued") {
            assert!(started.elapsed() < Duration::from_secs(1));
            thread::sleep(Duration::from_millis(2));
        }
        assert!(executor.cancel("queued"));
        assert!(worker.join().unwrap().unwrap_err().contains("取消"));
        assert_eq!(fallback.calls.load(Ordering::SeqCst), 0);
        drop(lock);
        assert!(!dir.join("cad-state.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }
}
