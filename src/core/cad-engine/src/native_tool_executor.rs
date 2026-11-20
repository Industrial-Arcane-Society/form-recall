//! Session-owned native Cordis tool routing.
//!
//! Every registered DSH tool is executed by Rust under one session transaction.
use crate::native_feature_specs::feature_spec;
use crate::native_knowledge::{search_knowledge, search_recipes};
use crate::native_tool_bridge::ToolExecutor;
use crate::native_vision_domain::{self, Context as VisionContext};
use crate::native_vision_provider::NativeVisionProvider;
use crate::native_web::NativeWeb;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
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
    web: NativeWeb,
    vision: Option<Arc<NativeVisionProvider>>,
    review_vision: Option<Arc<NativeVisionProvider>>,
    geometry_manifest: Option<PathBuf>,
    active_model_root: Option<PathBuf>,
    online_enabled: bool,
    transaction: Mutex<()>,
    audit_lock: Mutex<()>,
    active: Mutex<HashMap<String, Arc<AtomicBool>>>,
    shell_face_selectors: Mutex<HashMap<String, crate::native_shell::SelectorGrant>>,
    stopping: AtomicBool,
}

impl NativeToolExecutor {
    pub fn new(
        project_root: impl Into<PathBuf>,
        session_dir: impl Into<PathBuf>,
        online_enabled: bool,
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
            web,
            vision: None,
            review_vision: None,
            geometry_manifest: None,
            active_model_root: None,
            online_enabled,
            transaction: Mutex::new(()),
            audit_lock: Mutex::new(()),
            active: Mutex::new(HashMap::new()),
            shell_face_selectors: Mutex::new(HashMap::new()),
            stopping: AtomicBool::new(false),
        })
    }

    pub fn with_vision(mut self, provider: Arc<NativeVisionProvider>) -> Self {
        self.vision = Some(provider);
        self
    }

    pub fn with_review_vision(mut self, provider: Arc<NativeVisionProvider>) -> Self {
        self.review_vision = Some(provider);
        self
    }

    fn vision_provider(&self, name: &str) -> Option<&Arc<NativeVisionProvider>> {
        match name {
            "cad_inspect_references" => self.vision.as_ref(),
            "cad_review_reference_similarity" => {
                self.review_vision.as_ref().or(self.vision.as_ref())
            }
            _ => None,
        }
    }

    pub fn with_geometry_runtime(mut self, manifest: PathBuf) -> Self {
        self.geometry_manifest = Some(manifest);
        self
    }

    pub fn with_active_model_root(mut self, root: PathBuf) -> Self {
        self.active_model_root = Some(root);
        self
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
        // Every name in Cordis is routed into the native Rust domain.
        if !supports_native_tool(name) {
            return Err(format!(
                "Cordis 工具 {name} 没有 Rust 原生实现；拒绝执行未注册工具。"
            ));
        }
        let mut state = self.load_cad_state()?;
        let active_generation = if crate::native_cad_tools::supports(name) {
            if let Some(root) = self.active_model_root.as_deref() {
                let active = crate::native_active_model::load(root)?;
                match active {
                    Some(active) => {
                        if !active_model_state_matches(&state, &active) {
                            reconcile_session_to_active(&self.session_dir, &active)?;
                            return Err("Agent 会话中的 CADPlan 已过期，活动模型已由 Quick/UI/另一 Agent 更新；为避免覆盖新版本，请重置或重新打开 Agent 会话。".into());
                        }
                        active.get("generation").and_then(Value::as_u64)
                    }
                    None if state.get("plan").is_some_and(Value::is_object) => {
                        reset_session_without_active_model(&self.session_dir)?;
                        return Err(
                            "Agent 会话含 CADPlan，但活动模型记录已不存在；拒绝重建旧状态。".into(),
                        );
                    }
                    None => Some(0),
                }
            } else {
                None
            }
        } else {
            None
        };
        let (value, images, progress) = match name {
            name if crate::native_cad_metadata::supports(name) => {
                let (next, value) =
                    crate::native_cad_metadata::run(&state, name, arguments, &self.web)?;
                state = next;
                (value, vec![], "已更新宿主元数据；自述不作为独立验收证据。")
            }
            name if crate::native_cad_tools::supports(name) => {
                if name == "cad_add_feature" {
                    let feature = arguments
                        .get("feature")
                        .ok_or("cad_add_feature 缺少 feature。")?;
                    if feature.get("kind").and_then(Value::as_str) == Some("shell") {
                        let plan = state
                            .get("plan")
                            .filter(|value| value.is_object())
                            .ok_or("shell 必须作用于当前已有实体。")?;
                        let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
                        let fingerprint = crate::cad_plan::geometry_fingerprint(plan)?;
                        let target_feature_id =
                            crate::native_shell::current_target_feature_id(plan)?;
                        let manifest = self
                            .geometry_manifest
                            .as_deref()
                            .ok_or("原生OCCT runtime尚未配置；拒绝调用Python几何fallback。")?;
                        let kernel_version =
                            crate::native_occt::Kernel::load_manifest(manifest)?.version();
                        let grants = self
                            .shell_face_selectors
                            .lock()
                            .map_err(|_| "shell selector registry 已损坏。")?;
                        crate::native_shell::validate_request_feature(
                            feature,
                            revision,
                            &fingerprint,
                            &kernel_version,
                            &target_feature_id,
                            &grants,
                        )?;
                    }
                }
                let active_path = self.session_dir.join("active-turn.json");
                let user_turn_id = if active_path.is_file() {
                    read_json_object(&active_path)?
                        .get("turn_id")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                } else {
                    None
                };
                let outcome = crate::native_cad_tools::run(
                    &state,
                    name,
                    arguments,
                    &crate::native_cad_tools::Context {
                        project_root: self.project_root.clone(),
                        session_dir: self.session_dir.clone(),
                        runtime_manifest: self.geometry_manifest.clone(),
                        user_turn_id,
                    },
                    cancelled,
                )?;
                let changed_geometry = outcome.state.get("revision") != state.get("revision");
                state = outcome.state;
                if changed_geometry {
                    self.shell_face_selectors
                        .lock()
                        .map_err(|_| "shell selector registry 已损坏。")?
                        .clear();
                }
                (
                    outcome.value,
                    outcome.images,
                    "已原生更新特征及当前版本预览。",
                )
            }
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
            "cad_list_shell_faces" => {
                check_keys(arguments, &[])?;
                let plan = state
                    .get("plan")
                    .filter(|value| value.is_object())
                    .ok_or("cad_list_shell_faces 需要当前模型中的唯一闭合实体。")?;
                let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
                let manifest = self
                    .geometry_manifest
                    .as_deref()
                    .ok_or("原生OCCT runtime尚未配置；拒绝调用Python几何fallback。")?;
                let built = crate::native_plan_geometry::build(plan, manifest, cancelled)?;
                if built.report["state"] != "built" {
                    return Err("当前 CADPlan 只有部分几何，不能生成 shell 面选择器。".into());
                }
                let fingerprint = crate::cad_plan::geometry_fingerprint(plan)?;
                let kernel_version = built.report["occt_version"]
                    .as_str()
                    .ok_or("OCCT 几何报告缺少 kernel version。")?;
                let target_feature_id = crate::native_shell::current_target_feature_id(plan)?;
                let (inventory, grants) = crate::native_shell::inventory(
                    &built.shape,
                    revision,
                    &fingerprint,
                    kernel_version,
                    &target_feature_id,
                )?;
                let mut registry = self
                    .shell_face_selectors
                    .lock()
                    .map_err(|_| "shell selector registry 已损坏。")?;
                registry.retain(|_, grant| {
                    grant.revision == revision
                        && grant.geometry_fingerprint == fingerprint
                        && grant.kernel_version == kernel_version
                        && grant.target_feature_id == target_feature_id
                });
                for (selector, grant) in grants {
                    registry.insert(selector, grant);
                }
                (
                    inventory,
                    vec![],
                    "已为当前 CADPlan revision 枚举可支持的平面移除面。",
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
            "cad_inspect_references" | "cad_review_reference_similarity" => {
                let outcome = self.run_vision(name, &state, arguments, cancelled)?;
                state = outcome.state;
                (
                    outcome.value,
                    vec![],
                    if name == "cad_inspect_references" {
                        "已原生观察所选原图；结果保持unknown直到独立验证。"
                    } else {
                        "已原生完成独立单视图复核。"
                    },
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
            _ => return Err(format!("未知原生工具：{name}")),
        };
        self.check_cancelled(cancelled)?;
        if name == "cad_search_knowledge" {
            state["knowledge_search"] = value.clone();
            state["knowledge_visual_examples"] =
                value.get("visual_examples").cloned().unwrap_or(json!([]));
        }
        let vision_domain_recorded = matches!(
            name,
            "cad_inspect_references" | "cad_review_reference_similarity"
        );
        if !vision_domain_recorded {
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
        }
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
        if started.elapsed() >= timeout {
            return Err("工具超过本次截止时间，生成文件未提交为当前模型。".into());
        }
        if let (Some(root), Some(expected_generation)) =
            (self.active_model_root.as_deref(), active_generation)
            && state["plan"].is_object()
            && state["last_preview"].is_object()
        {
            let references = read_json_object(&self.session_dir.join("reference-images.json"))?;
            let user_turns = read_json_object(&self.session_dir.join("user-turns.json"))?;
            let active = commit_agent_model_state(
                &self.session_dir,
                root,
                expected_generation,
                &mut state,
                &references,
                &user_turns,
            )?;
            let mut value = value;
            value["active_model_generation"] = active["generation"].clone();
            publish_committed_preview(&self.session_dir, &state);
            return Ok(tool_result(name, value, images, progress));
        }
        write_json_atomic(&self.session_dir.join("cad-state.json"), &state)?;
        if crate::native_cad_tools::supports(name) {
            publish_committed_preview(&self.session_dir, &state);
        }
        Ok(tool_result(name, value, images, progress))
    }

    fn audit_revision(&self) -> Option<i64> {
        let path = self.session_dir.join("cad-state.json");
        if path.exists() {
            read_json_object(&path)
                .ok()
                .and_then(|state| state.get("revision").and_then(Value::as_i64))
        } else {
            None
        }
    }

    fn append_tool_audit(
        &self,
        call_id: &str,
        name: &str,
        arguments: &Value,
        result: &Result<Value, String>,
        revision_before: Option<i64>,
        revision_after: Option<i64>,
    ) -> Result<(), String> {
        let (success, validation, error, result_summary) = match result {
            Ok(value) => {
                let text = value.get("text").and_then(Value::as_str).unwrap_or("");
                let summary = json!({
                    "text_bytes": text.len(),
                    "text_sha256": format!("{:x}", Sha256::digest(text.as_bytes())),
                    "image_count": value.get("images").and_then(Value::as_array).map(Vec::len).unwrap_or(0),
                    "payload_is_error": value.pointer("/payload/isError").cloned().unwrap_or(Value::Null),
                    "execution_engine": value.pointer("/payload/execution_engine").cloned().unwrap_or(Value::Null),
                    "tool": value.pointer("/payload/tool").cloned().unwrap_or(Value::Null)
                });
                (
                    true,
                    json!({"status":"accepted_by_executor","basis":"executor returned successfully; no standalone schema validation is claimed"}),
                    Value::Null,
                    summary,
                )
            }
            Err(message) => {
                let error_type = classify_tool_error(message);
                let validation_status = if error_type == "argument_validation" {
                    "rejected_by_executor"
                } else {
                    "not_proven"
                };
                (
                    false,
                    json!({"status":validation_status,"basis":error_type}),
                    json!({"type":error_type,"message":message}),
                    Value::Null,
                )
            }
        };
        let record = json!({
            "schema_version":1,
            "at":unix_seconds(),
            "call_id":call_id,
            "tool":name,
            "arguments":redact_tool_arguments(arguments),
            "argument_redaction_policy":"Preserve arguments except values under credential, image, and binary-content keys; those values are replaced with [REDACTED].",
            "parameter_validation":validation,
            "success":success,
            "error":error,
            "result_summary":result_summary,
            "revision_before":revision_before,
            "revision_after":revision_after
        });
        let mut line = serde_json::to_vec(&record).map_err(|error| error.to_string())?;
        line.push(b'\n');
        use std::io::Write;
        let _audit = self.audit_lock.lock().map_err(|_| "工具审计锁已损坏。")?;
        fs::create_dir_all(&self.session_dir).map_err(|error| error.to_string())?;
        let path = self.session_dir.join("native-tool-audit.jsonl");
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|error| error.to_string())?;
        file.write_all(&line).map_err(|error| error.to_string())?;
        file.flush().map_err(|error| error.to_string())
    }

    fn run_vision(
        &self,
        name: &str,
        state: &Value,
        args: &Value,
        cancelled: &AtomicBool,
    ) -> Result<native_vision_domain::DomainResult, String> {
        if !self.online_enabled {
            return Err("在线视觉工具已禁用。".into());
        }
        let provider = self
            .vision_provider(name)
            .ok_or("原生视觉provider未配置；视觉请求不能执行。")?;
        let active_path = self.session_dir.join("active-turn.json");
        let turn_id = if active_path.exists() {
            read_json_object(&active_path)?
                .get("turn_id")
                .and_then(Value::as_str)
                .map(str::to_owned)
        } else {
            None
        };
        let context = VisionContext {
            session_dir: self.session_dir.clone(),
            reference_manifest: self.session_dir.join("reference-images.json"),
            user_turns_manifest: self.session_dir.join("user-turns.json"),
            stream_file: self.session_dir.join("stream-events.jsonl"),
            turn_id,
        };
        self.check_cancelled(cancelled)?;
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build()
            .map_err(|error| format!("无法启动视觉任务运行时：{error}"))?;
        let result = runtime.block_on(async {
            match name {
                "cad_inspect_references" => {
                    native_vision_domain::inspect(state, args, provider, cancelled, &context).await
                }
                "cad_review_reference_similarity" => {
                    native_vision_domain::review(state, args, provider, cancelled, &context)
                        .await
                        .map_err(|failure| failure.safe_message().to_string())
                }
                _ => Err(format!("未知原生视觉工具：{name}")),
            }
        })?;
        self.check_cancelled(cancelled)?;
        Ok(result)
    }
    fn load_cad_state(&self) -> Result<Value, String> {
        let path = self.session_dir.join("cad-state.json");
        if path.exists() {
            read_json_object(&path)
        } else {
            Ok(json!({"plan":null,"revision":0,"actions":[],"web_search_history":[]}))
        }
    }

    fn adjudication_state(&self) -> Result<(Value, Value, u64), String> {
        let root = self.active_model_root.as_deref().ok_or("当前 Agent 会话没有活动模型 CAS 根目录。")?;
        let state = self.load_cad_state()?;
        let active = crate::native_active_model::load(root)?.ok_or("当前活动模型不存在；拒绝 adjudication。")?;
        if !active_model_state_matches(&state, &active) {
            return Err("Agent 会话与当前活动模型不一致；拒绝 adjudication。".into());
        }
        let generation = active.get("generation").and_then(Value::as_u64).ok_or("活动模型 generation 无效。")?;
        Ok((state, active, generation))
    }

    fn finding_review_state_locked(&self) -> Result<Value, String> {
        let (state, _, generation) = self.adjudication_state()?;
        Ok(crate::native_review_findings::pending_adjudications(&state, generation))
    }

    fn adjudicate_finding_locked(&self, request: &Value) -> Result<Value, String> {
        let allowed = ["expected_generation", "expected_revision", "finding_id", "candidate_evidence_id", "candidate_observation_id", "reference_id", "source_sha256", "user_target_fingerprint", "view", "affected_feature_ids"];
        let object = request.as_object().ok_or("请求必须为 JSON 对象。")?;
        if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
            return Err(format!("未知 adjudication 字段：{key}"));
        }
        let expected_generation = request["expected_generation"].as_u64().ok_or("expected_generation 必须为非负整数。")?;
        let expected_revision = request["expected_revision"].as_i64().ok_or("expected_revision 必须为整数。")?;
        let (mut state, active, generation) = self.adjudication_state()?;
        if generation != expected_generation {
            return Err(format!("活动模型 generation 已变化：期望 {expected_generation}，当前为 {generation}。"));
        }
        if state["revision"].as_i64() != Some(expected_revision) || active["revision"].as_i64() != Some(expected_revision) {
            return Err("CAD revision 已变化；请刷新人工确认列表。".into());
        }
        let adjudication = crate::native_review_findings::adjudicate_rebind(&mut state, request)?;
        state["finish_summary"] = Value::Null;
        let references = read_json_object(&self.session_dir.join("reference-images.json"))?;
        let user_turns = read_json_object(&self.session_dir.join("user-turns.json"))?;
        let active = commit_agent_model_state(&self.session_dir, self.active_model_root.as_deref().unwrap(), generation, &mut state, &references, &user_turns)?;
        let new_generation = active["generation"].as_u64().ok_or("adjudication 提交后的 generation 无效。")?;
        let pending = crate::native_review_findings::pending_adjudications(&state, new_generation);
        Ok(json!({
            "adjudication": adjudication,
            "generation": new_generation,
            "revision": state["revision"],
            "pending": pending["pending"],
            "finding_status": state["finding_ledger"][adjudication["finding_id"].as_str().unwrap_or("")]["status"],
            "resolved": false,
        }))
    }
}

fn commit_agent_model_state(
    session_dir: &Path,
    active_root: &Path,
    expected_generation: u64,
    state: &mut Value,
    references: &Value,
    user_turns: &Value,
) -> Result<Value, String> {
    let next_generation = expected_generation
        .checked_add(1)
        .ok_or("活动模型 generation 已溢出。")?;
    state["active_model_generation"] = json!(next_generation);
    let candidate = crate::native_active_model::candidate_from_cad_state(
        "agent",
        state,
        references,
        user_turns,
    )?;
    match crate::native_active_model::commit(active_root, expected_generation, &candidate) {
        Ok(active) => {
            if let Err(write_error) = write_json_atomic(&session_dir.join("cad-state.json"), state) {
                let resync = crate::native_active_model::load(active_root)
                    .and_then(|record| record.ok_or_else(|| "活动模型提交后记录消失。".into()))
                    .and_then(|record| reconcile_session_to_active(session_dir, &record));
                return match resync {
                    Ok(synced) => {
                        *state = synced;
                        Err(format!("活动模型已提交，但会话状态写入失败；已从活动模型恢复：{write_error}"))
                    }
                    Err(sync_error) => Err(format!(
                        "活动模型已提交，但会话状态写入失败：{write_error}；重新同步也失败：{sync_error}"
                    )),
                };
            }
            Ok(active)
        }
        Err(commit_error) => {
            let resync = crate::native_active_model::load(active_root)
                .and_then(|record| record.ok_or_else(|| "活动模型冲突后记录不存在。".into()))
                .and_then(|record| reconcile_session_to_active(session_dir, &record));
            match resync {
                Ok(synced) => {
                    *state = synced;
                    Err(commit_error)
                }
                Err(sync_error) => Err(format!(
                    "{commit_error}；Agent 会话同步至活动模型也失败：{sync_error}"
                )),
            }
        }
    }
}

fn reconcile_session_to_active(session_dir: &Path, active: &Value) -> Result<Value, String> {
    let mut state = active
        .get("cad_state")
        .filter(|value| value.is_object())
        .cloned()
        .ok_or("活动模型记录缺少 CAD 会话状态。")?;
    state["active_model_generation"] = active["generation"].clone();
    write_json_atomic(&session_dir.join("cad-state.json"), &state)?;
    let session_id = active["model_id"].as_str().unwrap_or("active-model");
    for (manifest_key, file_name, rows_key) in [
        ("reference_manifest", "reference-images.json", "images"),
        ("user_turns_manifest", "user-turns.json", "turns"),
    ] {
        let manifest = active
            .get(manifest_key)
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| {
                json!({"schema_version":1,"session_id":session_id,rows_key:[]})
            });
        write_json_atomic(&session_dir.join(file_name), &manifest)?;
    }
    sync_session_preview(session_dir, &state);
    Ok(state)
}

fn reset_session_without_active_model(session_dir: &Path) -> Result<(), String> {
    let unavailable = json!({"plan":null,"revision":0,"active_model_generation":0,
        "output_paths":{},"actions":[],"last_preview":null,"preview_revision":null,
        "finish_summary":null,"active_model_status":"unavailable"});
    write_json_atomic(&session_dir.join("cad-state.json"), &unavailable)?;
    let session_id = "active-model-unavailable";
    write_json_atomic(
        &session_dir.join("reference-images.json"),
        &json!({"schema_version":1,"session_id":session_id,"images":[]}),
    )?;
    write_json_atomic(
        &session_dir.join("user-turns.json"),
        &json!({"schema_version":1,"session_id":session_id,"turns":[]}),
    )?;
    let _ = fs::remove_file(session_dir.join("current-preview.png"));
    Ok(())
}

fn publish_committed_preview(session_dir: &Path, state: &Value) {
    sync_session_preview(session_dir, state);
}

fn sync_session_preview(session_dir: &Path, state: &Value) {
    let Some(path) = state["last_preview"]["preview_image_path"].as_str() else {
        let _ = fs::remove_file(session_dir.join("current-preview.png"));
        return;
    };
    match fs::read(path) {
        Ok(bytes) => {
            let _ = crate::native_model_io::atomic_write(
                &session_dir.join("current-preview.png"),
                &bytes,
            );
        }
        Err(_) => {
            let _ = fs::remove_file(session_dir.join("current-preview.png"));
        }
    }
}

impl ToolExecutor for NativeToolExecutor {
    fn finding_review_state(&self) -> Result<Value, String> {
        let _transaction = self.transaction.lock().map_err(|_| "CAD 会话事务已损坏。")?;
        self.finding_review_state_locked()
    }

    fn adjudicate_finding(&self, request: &Value) -> Result<Value, String> {
        let _transaction = self.transaction.lock().map_err(|_| "CAD 会话事务已损坏。")?;
        self.adjudicate_finding_locked(request)
    }

    fn execute(
        &self,
        call_id: &str,
        name: &str,
        arguments: &Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        let revision_before = self.audit_revision();
        let result = (|| {
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
            let result = self.run(name, arguments, timeout, &cancelled);
            if let Ok(mut active) = self.active.lock() {
                active.remove(call_id);
            }
            result
        })();
        let revision_after = self.audit_revision();
        if let Err(audit_error) = self.append_tool_audit(
            call_id,
            name,
            arguments,
            &result,
            revision_before,
            revision_after,
        ) {
            // An audit write failure must never replace the original CAD/tool error, nor
            // turn an already committed successful operation into a reported failure.
            eprintln!("native tool audit write failed for {call_id}: {audit_error}");
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
        native.is_some()
    }

    fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        if let Ok(active) = self.active.lock() {
            for cancelled in active.values() {
                cancelled.store(true, Ordering::Release);
            }
        }
    }
}

fn supports_native_tool(name: &str) -> bool {
    crate::native_cad_tools::supports(name)
        || crate::native_cad_metadata::supports(name)
        || matches!(
            name,
            "cad_get_state"
                | "cad_get_feature_spec"
                | "cad_list_shell_faces"
                | "cad_search_knowledge"
                | "cad_search_recipes"
                | "web_search"
                | "web_open_result"
                | "cad_inspect_references"
                | "cad_review_reference_similarity"
        )
}

fn classify_tool_error(message: &str) -> &'static str {
    let lower = message.to_ascii_lowercase();
    if [
        "参数",
        "必须",
        "缺少",
        "无效",
        "须为",
        "须有",
        "不支持原生cad工具",
        "不支持未注册工具",
        "unknown native tool",
        "must be",
        "required",
        "invalid",
        "unsupported",
    ]
    .iter()
    .any(|marker| lower.contains(&marker.to_ascii_lowercase()))
    {
        "argument_validation"
    } else if message.contains("过期")
        || message.contains("generation")
        || message.contains("旧 revision")
    {
        "state_conflict"
    } else {
        "execution"
    }
}

fn redact_tool_arguments(arguments: &Value) -> Value {
    const REDACTED_KEYS: &[&str] = &[
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "image",
        "image_data",
        "image_bytes",
        "image_base64",
        "data_url",
        "bytes",
        "binary",
    ];
    match arguments {
        Value::Object(object) => {
            let mut safe = serde_json::Map::new();
            for (key, value) in object {
                let normalized = key.to_ascii_lowercase().replace('-', "_");
                if REDACTED_KEYS.contains(&normalized.as_str())
                    || normalized.starts_with("image")
                    || normalized.contains("base64")
                    || normalized.ends_with("_bytes")
                    || normalized.ends_with("_api_key")
                    || normalized.ends_with("_token")
                    || normalized.ends_with("_password")
                    || normalized.ends_with("_secret")
                {
                    safe.insert(key.clone(), json!("[REDACTED]"));
                } else {
                    safe.insert(key.clone(), redact_tool_arguments(value));
                }
            }
            Value::Object(safe)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(redact_tool_arguments).collect()),
        _ => arguments.clone(),
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

fn active_model_state_matches(session_state: &Value, active_record: &Value) -> bool {
    let session_plan = session_state.get("plan").filter(|value| value.is_object());
    let active_plan = active_record
        .get("cadplan")
        .filter(|value| value.is_object());
    let active_state = active_record
        .get("cad_state")
        .filter(|value| value.is_object());
    let snapshot_fields_match = [
        "plan",
        "revision",
        "active_model_generation",
        "output_paths",
        "last_preview",
    ]
    .iter()
    .all(|key| session_state.get(*key) == active_state.and_then(|value| value.get(*key)));
    session_state
        .get("active_model_generation")
        .and_then(Value::as_u64)
        == active_record.get("generation").and_then(Value::as_u64)
        && snapshot_fields_match
        && session_plan.is_some() == active_plan.is_some()
        && session_plan.is_none_or(|plan| active_plan == Some(plan))
        && (session_plan.is_none()
            || session_state.get("revision").and_then(Value::as_i64)
                == active_record.get("revision").and_then(Value::as_i64))
}

fn tool_result(name: &str, mut value: Value, images: Vec<Value>, progress: &str) -> Value {
    if name == "cad_search_knowledge" && !images.is_empty() {
        value["visual_attachment_order"] =
            json!(images
            .iter()
            .enumerate()
            .map(
                |(index, image)| json!({"attachment_index":index+1,"model":image.get("model"),
                "category":image.get("category"),"role":image.get("role"),
                "source_kind":"knowledge_example","is_user_reference":false})
            )
            .collect::<Vec<_>>());
    }
    json!({"text":format!("AI_MODELER_PROGRESS:{progress}\n{}",value),"images":images,
        "payload":{"isError":false,"execution_engine":"rust","tool":name}})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vision_provider(base_url: &str, model: &str) -> Arc<NativeVisionProvider> {
        Arc::new(
            NativeVisionProvider::new(crate::native_vision_provider::ProviderConfig {
                base_url: base_url.into(),
                model: model.into(),
                api_key: "unit-test-secret".into(),
                effort: "none".into(),
                thinking_budget: None,
                max_output_tokens: 128,
            })
            .unwrap(),
        )
    }

    #[test]
    fn vision_tools_use_stage_provider_and_legacy_provider_fallback() {
        let (dir, _) = fixture();
        let perception = vision_provider("https://perception.example/v1", "perception-model");
        let review = vision_provider("https://review.example/v1", "review-model");
        let executor = NativeToolExecutor::new(&dir, &dir, true)
            .unwrap()
            .with_vision(perception.clone())
            .with_review_vision(review.clone());
        assert!(Arc::ptr_eq(
            executor.vision_provider("cad_inspect_references").unwrap(),
            &perception
        ));
        assert!(Arc::ptr_eq(
            executor
                .vision_provider("cad_review_reference_similarity")
                .unwrap(),
            &review
        ));
        assert!(executor.vision_provider("cad_unknown").is_none());

        let legacy = NativeToolExecutor::new(&dir, &dir, true)
            .unwrap()
            .with_vision(perception.clone());
        assert!(Arc::ptr_eq(
            legacy
                .vision_provider("cad_review_reference_similarity")
                .unwrap(),
            &perception
        ));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn rejects_agent_state_after_active_model_generation_or_artifacts_change() {
        let plan = json!({"name":"part","features":[{"id":"body","kind":"box"}]});
        let session = json!({"plan":plan,"revision":3,"active_model_generation":7,
            "output_paths":{"glb":"active.glb"},"last_preview":{"revision":3,"geometry_fingerprint":"same"}});
        let active = json!({"generation":7,"revision":3,"cadplan":plan,
            "cad_state":session});
        assert!(active_model_state_matches(&session, &active));

        let mut stale_generation = active.clone();
        stale_generation["generation"] = json!(8);
        assert!(!active_model_state_matches(&session, &stale_generation));

        let mut changed_artifacts = active.clone();
        changed_artifacts["cad_state"]["output_paths"]["glb"] = json!("new-model.glb");
        assert!(!active_model_state_matches(&session, &changed_artifacts));
    }

    fn cad_state(plan: Value, revision: i64, generation: u64) -> Value {
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        json!({"plan":plan,"revision":revision,"preview_revision":revision,
            "active_model_generation":generation,"output_paths":{},"actions":[],
            "last_preview":{"revision":revision,"geometry_fingerprint":fingerprint,
                "local_geometry_checks":{"state":"passed"}},"finish_summary":null})
    }

    fn box_plan(length: i64) -> Value {
        crate::cad_plan::create(
            "transaction fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":length,"width":8,"height":4},"sources":{
                "length":{"type":"user","evidence":"fixture"},"width":{"type":"user","evidence":"fixture"},"height":{"type":"user","evidence":"fixture"}}}),
            "fixture",
            None,
        )
    }

    #[test]
    fn cas_conflict_reconciles_session_to_the_committed_active_model() {
        let root = fixture().0;
        let session_dir = root.join("agent-session");
        let active_root = root.join("active-model");
        fs::create_dir_all(&session_dir).unwrap();
        let references = json!({"schema_version":1,"session_id":"fixture","images":[]});
        let turns = json!({"schema_version":1,"session_id":"fixture","turns":[]});

        let initial_state = cad_state(box_plan(10), 1, 1);
        let initial_candidate = crate::native_active_model::candidate_from_cad_state(
            "agent",
            &initial_state,
            &references,
            &turns,
        )
        .unwrap();
        crate::native_active_model::commit(&active_root, 0, &initial_candidate).unwrap();
        write_json_atomic(&session_dir.join("cad-state.json"), &initial_state).unwrap();

        let replacement_state = cad_state(box_plan(20), 2, 2);
        let replacement_references = json!({"schema_version":1,"session_id":"fixture-new","images":[{"id":"new-image"}]});
        let replacement_turns = json!({"schema_version":1,"session_id":"fixture-new","turns":[{"turn_id":"new-turn"}]});
        let replacement_candidate = crate::native_active_model::candidate_from_cad_state(
            "agent",
            &replacement_state,
            &replacement_references,
            &replacement_turns,
        )
        .unwrap();
        let active = crate::native_active_model::commit(&active_root, 1, &replacement_candidate)
            .unwrap();

        let mut losing_state = cad_state(box_plan(30), 2, 2);
        fs::write(session_dir.join("current-preview.png"), b"stale preview").unwrap();
        let error = commit_agent_model_state(
            &session_dir,
            &active_root,
            1,
            &mut losing_state,
            &references,
            &turns,
        )
        .unwrap_err();
        assert!(error.contains("活动模型已变化"), "{error}");
        let session_state = read_json_object(&session_dir.join("cad-state.json")).unwrap();
        assert_eq!(losing_state, active["cad_state"]);
        assert_eq!(session_state, active["cad_state"]);
        assert_eq!(
            session_state["active_model_generation"],
            active["generation"]
        );
        assert_eq!(
            read_json_object(&session_dir.join("reference-images.json")).unwrap(),
            replacement_references
        );
        assert_eq!(
            read_json_object(&session_dir.join("user-turns.json")).unwrap(),
            replacement_turns
        );
        assert!(!session_dir.join("current-preview.png").exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn agent_cad_edit_publishes_shared_generation_and_session_snapshot() {
        let root = fixture().0;
        let session_dir = root.join("agent-session");
        let active_root = root.join("active-model");
        fs::create_dir_all(&session_dir).unwrap();
        let references = json!({"schema_version":1,"session_id":"fixture","images":[]});
        let turns = json!({"schema_version":1,"session_id":"fixture","turns":[]});

        let initial_state = cad_state(box_plan(10), 1, 1);
        let initial_candidate = crate::native_active_model::candidate_from_cad_state(
            "agent",
            &initial_state,
            &references,
            &turns,
        )
        .unwrap();
        crate::native_active_model::commit(&active_root, 0, &initial_candidate).unwrap();
        write_json_atomic(&session_dir.join("cad-state.json"), &initial_state).unwrap();

        let mut edited_state = cad_state(box_plan(20), 2, 1);
        let committed = commit_agent_model_state(
            &session_dir,
            &active_root,
            1,
            &mut edited_state,
            &references,
            &turns,
        )
        .unwrap();
        let shared = crate::native_active_model::load(&active_root)
            .unwrap()
            .unwrap();
        let session_snapshot = read_json_object(&session_dir.join("cad-state.json")).unwrap();

        assert_eq!(committed["generation"], 2);
        assert_eq!(shared["generation"], 2);
        assert_eq!(shared["origin"], "agent");
        assert_eq!(shared["cad_state"], edited_state);
        assert_eq!(session_snapshot, shared["cad_state"]);
        assert_eq!(session_snapshot["active_model_generation"], 2);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_active_record_clears_stale_session_model_and_evidence() {
        let (root, _executor) = fixture();
        let session_dir = root.join("agent-session");
        fs::create_dir_all(&session_dir).unwrap();
        let old_plan = box_plan(10);
        let stale = cad_state(old_plan, 3, 7);
        write_json_atomic(&session_dir.join("cad-state.json"), &stale).unwrap();
        write_json_atomic(
            &session_dir.join("reference-images.json"),
            &json!({"schema_version":1,"session_id":"old","images":[{"id":"old-image"}]}),
        )
        .unwrap();
        write_json_atomic(
            &session_dir.join("user-turns.json"),
            &json!({"schema_version":1,"session_id":"old","turns":[{"turn_id":"old-turn"}]}),
        )
        .unwrap();
        fs::write(session_dir.join("current-preview.png"), b"stale image").unwrap();

        reset_session_without_active_model(&session_dir).unwrap();
        let state = read_json_object(&session_dir.join("cad-state.json")).unwrap();
        assert!(state["plan"].is_null());
        assert_eq!(state["active_model_status"], "unavailable");
        assert_eq!(state["active_model_generation"], 0);
        assert!(read_json_object(&session_dir.join("reference-images.json")).unwrap()["images"]
            .as_array().unwrap().is_empty());
        assert!(read_json_object(&session_dir.join("user-turns.json")).unwrap()["turns"]
            .as_array().unwrap().is_empty());
        assert!(!session_dir.join("current-preview.png").exists());

        fs::remove_dir_all(root).unwrap();
    }

    fn fixture() -> (PathBuf, Arc<NativeToolExecutor>) {
        fixture_with_online(false)
    }

    fn fixture_with_online(online_enabled: bool) -> (PathBuf, Arc<NativeToolExecutor>) {
        let dir = std::env::temp_dir().join(format!(
            "aimodeler-native-tool-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        let executor = Arc::new(NativeToolExecutor::new(&dir, &dir, online_enabled).unwrap());
        (dir, executor)
    }

    #[test]
    fn every_registered_cordis_tool_has_a_native_rust_route() {
        for schema in crate::native_tool_schemas::native_tool_schemas().unwrap() {
            let name = schema["name"].as_str().unwrap();
            assert!(supports_native_tool(name), "{name} has no Rust route");
        }
    }

    #[test]
    fn unknown_tool_is_rejected_without_a_worker_or_state_change() {
        let (dir, executor) = fixture();
        let error = executor
            .execute(
                "unknown",
                "legacy_python_tool",
                &json!({}),
                Duration::from_secs(1),
            )
            .unwrap_err();
        assert!(error.contains("没有 Rust 原生实现"), "{error}");
        assert!(!dir.join("cad-state.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn executor_uses_native_vision_provider_http_and_commits_only_domain_state() {
        use sha2::{Digest, Sha256};
        use std::io::{Read, Write};
        use std::net::TcpListener;

        let (dir, _) = fixture();
        let source = dir.join("source.png");
        image::RgbImage::from_pixel(8, 8, image::Rgb([80, 90, 100]))
            .save(&source)
            .unwrap();
        let bytes = fs::read(&source).unwrap();
        let sha = Sha256::digest(&bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let reference_id = format!("sha256:{sha}");
        fs::write(dir.join("reference-images.json"), serde_json::to_vec(&json!({
            "images":[{"id":reference_id,"sha256":sha,"path":source,"filename":"source.png","media_type":"image/png"}]
        })).unwrap()).unwrap();
        fs::write(dir.join("user-turns.json"), br#"{"turns":[]}"#).unwrap();
        fs::write(dir.join("active-turn.json"), br#"{"turn_id":"turn-http"}"#).unwrap();

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 4096];
            let header_end;
            loop {
                let n = stream.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..n]);
                if let Some(pos) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                    header_end = pos + 4;
                    break;
                }
            }
            let headers = String::from_utf8_lossy(&request[..header_end]).to_string();
            let content_length = headers
                .lines()
                .find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    key.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap();
            while request.len() < header_end + content_length {
                let n = stream.read(&mut buffer).unwrap();
                if n == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..n]);
            }
            let body: Value =
                serde_json::from_slice(&request[header_end..header_end + content_length]).unwrap();
            let response = json!({"choices":[{"message":{"content":json!({
                "summary":"visible object",
                "visible_evidence":[{"description":"a visible circular opening","visible_form":"hole","relative_size_group":"unknown","surface_plane_relation":"unknown","confidence":0.7}]
            }).to_string()},"finish_reason":"stop"}]});
            let response = serde_json::to_vec(&response).unwrap();
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", response.len()).unwrap();
            stream.write_all(&response).unwrap();
            (headers, body)
        });
        let provider = NativeVisionProvider::new(crate::native_vision_provider::ProviderConfig {
            base_url: format!("http://{addr}/v1"),
            model: "fixture-vlm".into(),
            api_key: "fixture-key".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1024,
        })
        .unwrap();
        let executor = NativeToolExecutor::new(&dir, &dir, true)
            .unwrap()
            .with_vision(Arc::new(provider));
        let result = executor
            .execute(
                "http",
                "cad_inspect_references",
                &json!({}),
                Duration::from_secs(10),
            )
            .unwrap();
        assert_eq!(result["payload"]["execution_engine"], "rust");
        let state = read_json_object(&dir.join("cad-state.json")).unwrap();
        assert_eq!(state["reference_inventory"].as_object().unwrap().len(), 1);
        assert_eq!(state["actions"].as_array().unwrap().len(), 1);
        let (headers, body) = server.join().unwrap();
        assert!(headers.starts_with("POST /v1/chat/completions "));
        assert_eq!(body["model"], "fixture-vlm");
        assert_eq!(body["stream"], false);
        fs::remove_dir_all(dir).unwrap();
    }

    // The preview is a previously committed, current host preview. Generating
    // a CAD preview is runtime-specific and is covered separately.
    fn current_vision_fixture() -> (PathBuf, String, String, String) {
        use sha2::{Digest, Sha256};

        let (dir, _) = fixture();
        let source = dir.join("reference.png");
        let preview = dir.join("preview-front.png");
        image::RgbImage::from_pixel(12, 10, image::Rgb([30, 80, 140]))
            .save(&source)
            .unwrap();
        image::RgbImage::from_pixel(12, 10, image::Rgb([140, 80, 30]))
            .save(&preview)
            .unwrap();
        let source_bytes = fs::read(&source).unwrap();
        let source_sha = format!("{:x}", Sha256::digest(&source_bytes));
        let reference_id = format!("sha256:{source_sha}");
        let manifest = dir.join("reference-images.json");
        fs::write(
            &manifest,
            serde_json::to_vec(&json!({"images":[{"id":reference_id,"sha256":source_sha,
                "path":source,"filename":"fixture.png","media_type":"image/png"}]})).unwrap(),
        )
        .unwrap();
        fs::write(&dir.join("user-turns.json"), br#"{"turns":[]}"#).unwrap();
        let selected = crate::native_vision_assets::reference_input(
            &manifest,
            Some(&reference_id),
            Some(1),
            None,
        )
        .unwrap();
        let evidence_id = format!(
            "{reference_id}:roi-{}:obs-1:outline",
            &selected.roi_cache_key[..10]
        );
        let plan = json!({"features":[{"id":"body","kind":"box","operation":"add"}]});
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        let preview_bytes = fs::read(&preview).unwrap();
        let preview_sha = format!("{:x}", Sha256::digest(&preview_bytes));
        let state = json!({
            "revision": 4,
            "preview_generation": 8,
            "plan": plan,
            "last_preview": preview,
            "preview_revision": 4,
            "previews": [{"revision":4,"preview_generation":9,"view":"front",
                "preview_image_path":preview,"preview_sha256":preview_sha,
                "geometry_fingerprint":fingerprint,"render_options":{"view":"front"}}],
            "reference_inventory": {},
            "construction_milestone":{"revision":4,"completed_feature_ids":["body"],"expected_feature_ids":["body"]},
            "actions": []
        });
        let state = crate::native_cad_session::complete_preview(
            &state,
            crate::native_cad_session::BackendOutcome {
                revision: 4,
                geometry_fingerprint: fingerprint,
                output_paths: json!({"preview":preview.display().to_string()}),
                preview: json!({
                    "execution_engine":"rust_c_abi_occt",
                    "preview_generation":9,
                    "local_geometry_checks": {
                        "execution_engine":"rust_c_abi_occt",
                        "revision":4,
                        "geometry_fingerprint":crate::cad_plan::geometry_fingerprint(&plan).unwrap(),
                        "state":"passed"
                    }
                }),
            },
        )
        .unwrap()
        .state;
        write_json_atomic(&dir.join("cad-state.json"), &state).unwrap();
        (dir, reference_id, source_sha, evidence_id)
    }

    fn mock_vision_responses(responses: Vec<Value>) -> String {
        use std::io::{Read, Write};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        thread::spawn(move || {
            for content in responses {
                let (mut stream, _) = listener.accept().unwrap();
                stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut request = Vec::new();
                let mut chunk = [0u8; 8192];
                let header_end = loop {
                    let n = stream.read(&mut chunk).unwrap();
                    assert_ne!(n, 0);
                    request.extend_from_slice(&chunk[..n]);
                    if let Some(i) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                        break i + 4;
                    }
                };
                let headers = String::from_utf8_lossy(&request[..header_end]);
                let length = headers.lines().find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    key.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok()).flatten()
                }).unwrap();
                while request.len() < header_end + length {
                    let n = stream.read(&mut chunk).unwrap();
                    assert_ne!(n, 0);
                    request.extend_from_slice(&chunk[..n]);
                }
                let content = serde_json::to_string(&content).unwrap();
                let body = serde_json::to_vec(&json!({
                    "choices":[{"message":{"content":content},"finish_reason":"stop"}]
                })).unwrap();
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                stream.write_all(&body).unwrap();
            }
        });
        format!("http://{address}/v1/chat/completions")
    }

    fn mock_two_vision_responses(first: Value, second: Value) -> String {
        mock_vision_responses(vec![first, second])
    }
    #[test]
    fn native_executor_inspect_current_review_and_get_state_preserve_current_bindings() {
        let (dir, reference_id, source_sha, evidence_id) = current_vision_fixture();
        let turns_path = dir.join("user-turns.json");
        let target_prompt = "请按参考图创建支架";
        write_json_atomic(
            &turns_path,
            &json!({
                "schema_version":1,
                "session_id":"fixture-session",
                "turns":[{
                    "turn_id":"fixture-turn",
                    "session_id":"fixture-session",
                    "raw_user_prompt":target_prompt,
                    "reference_ids":[reference_id.clone()],
                    "at_epoch_ms":123456789,
                    "intent_source":"unclassified"
                }]
            }),
        ).unwrap();
        let endpoint = mock_two_vision_responses(
            json!({"summary":"A visible outline.","visible_evidence":[{"id":"outline","description":"A closed outer outline is visible."}]}),
            json!({"view_comparable":true,"score":92,"coverage_status":"verified",
                "covered_evidence_ids":[evidence_id],"evidence":[{"evidence_id":evidence_id,
                    "item":"outer outline","reference":"A closed outline is visible.",
                    "model":"A closed outline is visible.","assessment_basis":"reference","status":"match",
                    "affected_feature_ids":["body"]}]}),
        );
        let provider = Arc::new(NativeVisionProvider::new(crate::native_vision_provider::ProviderConfig {
            base_url: endpoint,
            model: "headless-fixture".into(),
            api_key: "fixture-key".into(),
            effort: "none".into(),
            thinking_budget: None,
            max_output_tokens: 1024,
        }).unwrap());
        let executor = NativeToolExecutor::new(&dir, &dir, true).unwrap().with_vision(provider);

        for (call, tool, args) in [
            ("inspect", "cad_inspect_references", json!({"reference_id":reference_id})),
            ("review", "cad_review_reference_similarity", json!({"view":"front","preview_generation":9,"reference_id":reference_id})),
        ] {
            let result = executor.execute(call, tool, &args, Duration::from_secs(10)).unwrap();
            assert_eq!(result["payload"]["execution_engine"], "rust", "call={call}, result={result:#}");
        }
        let result = executor.execute("state", "cad_get_state", &json!({}), Duration::from_secs(3)).unwrap();
        let summary: Value = serde_json::from_str(result["text"].as_str().unwrap().split_once('\n').unwrap().1).unwrap();
        assert_eq!(summary["visual_evidence_summary"]["all_references_covered"], true, "{summary:#}");
        assert_eq!(summary["independent_reference_review_pairs"], 1, "{summary:#}");
        assert_eq!(summary["visual_evidence_summary"]["comparable_review_pairs"], 1, "{summary:#}");
        let reference = &summary["visual_evidence_summary"]["references"][reference_id.as_str()];
        assert_eq!(reference["inventory_valid"], true);
        assert_eq!(reference["covered"], true);
        assert_eq!(reference["missing_required_evidence_ids"], json!([]));
        assert_eq!(summary["reference_images"][0]["id"], reference_id);
        assert_eq!(summary["reference_images"][0]["sha256"], source_sha);
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        let inventory = &saved["reference_inventory"][reference_id.as_str()];
        assert_eq!(inventory["source_sha256"], source_sha);
        assert!(inventory["roi_norm"].is_null());
        assert_eq!(inventory["visible_evidence"][0]["evidence_id"], evidence_id);
        assert_eq!(saved["reference_similarity_history"][0]["revision"], 4);
        assert_eq!(saved["reference_similarity_history"][0]["preview_generation"], 9);
        assert_eq!(saved["reference_similarity_history"][0]["reference_id"], reference_id);
        assert_eq!(saved["reference_similarity_history"][0]["source_sha256"], source_sha);
        assert!(saved["reference_similarity_history"][0]["roi_norm"].is_null());
        assert_eq!(saved["reference_similarity_history"][0]["observation_id"], inventory["observation_id"]);
        assert_eq!(saved["reference_similarity_history"][0]["geometry_fingerprint"], saved["previews"][0]["geometry_fingerprint"]);
        assert_eq!(saved["reference_similarity_history"][0]["geometry_signature"], saved["previews"][0]["geometry_fingerprint"]);

        let mut turns = read_json_object(&turns_path).unwrap();
        turns["turns"][0]["at_epoch_ms"] = json!(987654321);
        turns["turns"][0]["intent_source"] = json!("classifier");
        write_json_atomic(&turns_path, &turns).unwrap();
        let metadata_only = executor.execute(
            "state-after-metadata-change",
            "cad_get_state",
            &json!({}),
            Duration::from_secs(3),
        ).unwrap();
        let metadata_summary: Value = serde_json::from_str(
            metadata_only["text"].as_str().unwrap().split_once('\n').unwrap().1,
        ).unwrap();
        assert_eq!(metadata_summary["independent_reference_review_pairs"], 1, "{metadata_summary:#}");
        assert_eq!(metadata_summary["visual_evidence_summary"]["all_references_covered"], true, "{metadata_summary:#}");

        turns["turns"][0]["raw_user_prompt"] = json!("请按参考图创建并加宽支架");
        write_json_atomic(&turns_path, &turns).unwrap();
        let target_changed = executor.execute(
            "state-after-target-change",
            "cad_get_state",
            &json!({}),
            Duration::from_secs(3),
        ).unwrap();
        let changed_summary: Value = serde_json::from_str(
            target_changed["text"].as_str().unwrap().split_once('\n').unwrap().1,
        ).unwrap();
        let changed_reference = &changed_summary["visual_evidence_summary"]["references"][reference_id.as_str()];
        assert_eq!(changed_summary["independent_reference_review_pairs"], 0, "{changed_summary:#}");
        assert_eq!(changed_summary["visual_evidence_summary"]["comparable_review_pairs"], 0, "{changed_summary:#}");
        assert_eq!(changed_reference["covered"], false, "{changed_reference:#}");
        assert!(
            changed_reference["missing_required_evidence_ids"]
                .as_array()
                .unwrap()
                .contains(&json!(evidence_id)),
            "{changed_reference:#}"
        );
        let stale = executor.execute(
            "stale-generation", "cad_review_reference_similarity",
            &json!({"view":"front","preview_generation":8,"reference_id":reference_id}),
            Duration::from_secs(3),
        ).unwrap_err();
        assert!(
            stale.contains("review_failed:") && stale.contains("candidate remains unverified"),
            "stale review should be rejected with the safe public failure message: {stale}"
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn native_executor_get_state_rejects_review_from_previous_observation() {
        let (dir, reference_id, _source_sha, evidence_id) = current_vision_fixture();
        let observation = || json!({
            "summary":"A visible outline.",
            "visible_evidence":[{"id":"outline","description":"A closed outer outline is visible."}]
        });
        let review = json!({
            "view_comparable":true,
            "score":92,
            "coverage_status":"verified",
            "covered_evidence_ids":[evidence_id.clone()],
            "evidence":[{
                "evidence_id":evidence_id.clone(),
                "item":"outer outline",
                "reference":"A closed outline is visible.",
                "model":"A closed outline is visible.",
                "assessment_basis":"reference",
                "status":"match",
                "affected_feature_ids":["body"]
            }]
        });
        let endpoint = mock_vision_responses(vec![observation(), review, observation()]);
        let provider = Arc::new(NativeVisionProvider::new(
            crate::native_vision_provider::ProviderConfig {
                base_url: endpoint,
                model: "headless-fixture".into(),
                api_key: "fixture-key".into(),
                effort: "none".into(),
                thinking_budget: None,
                max_output_tokens: 1024,
            },
        ).unwrap());
        let executor = NativeToolExecutor::new(&dir, &dir, true).unwrap().with_vision(provider);

        executor.execute(
            "inspect-v1",
            "cad_inspect_references",
            &json!({"reference_id":reference_id}),
            Duration::from_secs(10),
        ).unwrap();
        executor.execute(
            "review-v1",
            "cad_review_reference_similarity",
            &json!({"view":"front","preview_generation":9,"reference_id":reference_id}),
            Duration::from_secs(10),
        ).unwrap();
        let before = executor.execute("state-before-refresh", "cad_get_state", &json!({}), Duration::from_secs(3)).unwrap();
        let before_summary: Value = serde_json::from_str(
            before["text"].as_str().unwrap().split_once('\n').unwrap().1
        ).unwrap();
        assert_eq!(before_summary["visual_evidence_summary"]["all_references_covered"], true);

        executor.execute(
            "refresh-observation",
            "cad_inspect_references",
            &json!({"reference_id":reference_id,"refresh_reason":"reobserve"}),
            Duration::from_secs(10),
        ).unwrap();
        let after = executor.execute("state-after-refresh", "cad_get_state", &json!({}), Duration::from_secs(3)).unwrap();
        let after_summary: Value = serde_json::from_str(
            after["text"].as_str().unwrap().split_once('\n').unwrap().1
        ).unwrap();
        let reference = &after_summary["visual_evidence_summary"]["references"][reference_id.as_str()];
        assert_eq!(after_summary["visual_evidence_summary"]["all_references_covered"], false, "{after_summary:#}");
        assert_eq!(reference["covered"], false, "{reference:#}");
        assert_eq!(reference["comparable_pairs"], 0, "{reference:#}");
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        assert_eq!(saved["reference_inventory"][reference_id.as_str()]["observation_version"], 2);
        assert_eq!(saved["reference_similarity_history"][0]["observation_version"], 1);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn native_executor_does_not_promote_partial_construction_stage_coverage() {
        let (dir, reference_id, _source_sha, evidence_id) = current_vision_fixture();
        let mut state = read_json_object(&dir.join("cad-state.json")).unwrap();
        state["construction_milestone"] = json!({
            "revision":4,
            "completed_feature_ids":[],
            "expected_feature_ids":["body"]
        });
        write_json_atomic(&dir.join("cad-state.json"), &state).unwrap();
        let endpoint = mock_two_vision_responses(
            json!({"summary":"A visible outline.","visible_evidence":[{"id":"outline","description":"A closed outer outline is visible."}]}),
            json!({
                "view_comparable":true,
                "score":92,
                "coverage_status":"verified",
                "covered_evidence_ids":[evidence_id.clone()],
                "evidence":[{
                    "evidence_id":evidence_id,
                    "item":"outer outline",
                    "reference":"A closed outline is visible.",
                    "model":"A closed outline is visible.",
                    "assessment_basis":"reference",
                    "status":"match",
                    "affected_feature_ids":["body"]
                }]
            }),
        );
        let provider = Arc::new(NativeVisionProvider::new(
            crate::native_vision_provider::ProviderConfig {
                base_url: endpoint,
                model: "headless-fixture".into(),
                api_key: "fixture-key".into(),
                effort: "none".into(),
                thinking_budget: None,
                max_output_tokens: 1024,
            },
        ).unwrap());
        let executor = NativeToolExecutor::new(&dir, &dir, true).unwrap().with_vision(provider);

        executor.execute(
            "inspect",
            "cad_inspect_references",
            &json!({"reference_id":reference_id}),
            Duration::from_secs(10),
        ).unwrap();
        executor.execute(
            "review-partial-stage",
            "cad_review_reference_similarity",
            &json!({"view":"front","preview_generation":9,"reference_id":reference_id}),
            Duration::from_secs(10),
        ).unwrap();
        let result = executor.execute("partial-stage-state", "cad_get_state", &json!({}), Duration::from_secs(3)).unwrap();
        let summary: Value = serde_json::from_str(
            result["text"].as_str().unwrap().split_once('\n').unwrap().1
        ).unwrap();
        let reference = &summary["visual_evidence_summary"]["references"][reference_id.as_str()];
        assert_eq!(summary["visual_evidence_summary"]["all_references_covered"], false, "{summary:#}");
        assert_eq!(reference["covered"], false, "{reference:#}");
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        let review = &saved["reference_similarity_history"][0];
        assert_eq!(review["stage_complete"], false, "{review:#}");
        assert_eq!(review["stage_diagnostic_only"], true, "{review:#}");
        assert_eq!(review["covered_evidence_ids"], json!([]), "{review:#}");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn native_executor_get_state_keeps_mismatch_and_unsupported_coverage_unaccepted() {
        for case in ["mismatch", "unsupported"] {
            let (dir, reference_id, _source_sha, evidence_id) = current_vision_fixture();
            let (status, claim) = if case == "mismatch" {
                ("mismatch", evidence_id.clone())
            } else {
                ("match", "unlisted-evidence".to_string())
            };
            let mut covered = vec![evidence_id.clone()];
            if case == "unsupported" {
                covered.push(claim.clone());
            }
            let endpoint = mock_two_vision_responses(
                json!({"summary":"A visible outline.","visible_evidence":[{"id":"outline","description":"A closed outer outline is visible."}]}),
                json!({
                    "view_comparable":true,
                    "score":92,
                    "coverage_status":"verified",
                    "covered_evidence_ids":covered,
                    "evidence":[{
                        "evidence_id":evidence_id.clone(),
                        "item":"outer outline",
                        "reference":"A closed outline is visible.",
                        "model":if case == "mismatch" {"A different contour is visible."} else {"A closed outline is visible."},
                        "difference":if case == "mismatch" {"The visible outline differs from the model."} else {""},
                        "mismatch_severity":if case == "mismatch" {"major"} else {"none"},
                        "assessment_basis":"reference",
                        "status":status,
                        "affected_feature_ids":["body"]
                    }]
                }),
            );
            let provider = Arc::new(NativeVisionProvider::new(
                crate::native_vision_provider::ProviderConfig {
                    base_url: endpoint,
                    model: "headless-fixture".into(),
                    api_key: "fixture-key".into(),
                    effort: "none".into(),
                    thinking_budget: None,
                    max_output_tokens: 1024,
                },
            ).unwrap());
            let executor = NativeToolExecutor::new(&dir, &dir, true).unwrap().with_vision(provider);
            executor.execute(
                "inspect",
                "cad_inspect_references",
                &json!({"reference_id":reference_id}),
                Duration::from_secs(10),
            ).unwrap();
            executor.execute(
                "review",
                "cad_review_reference_similarity",
                &json!({"view":"front","preview_generation":9,"reference_id":reference_id}),
                Duration::from_secs(10),
            ).unwrap();
            let result = executor.execute("state", "cad_get_state", &json!({}), Duration::from_secs(3)).unwrap();
            let summary: Value = serde_json::from_str(
                result["text"].as_str().unwrap().split_once('\n').unwrap().1
            ).unwrap();
            let reference = &summary["visual_evidence_summary"]["references"][reference_id.as_str()];
            assert_eq!(reference["covered"], false, "case={case}, {reference:#}");
            assert_eq!(summary["visual_evidence_summary"]["all_references_covered"], false, "case={case}, {summary:#}");
            if case == "mismatch" {
                assert!(reference["mismatch_evidence_ids"].as_array().unwrap().contains(&json!(evidence_id)), "{reference:#}");
            } else {
                assert!(reference["unsupported_coverage_ids"].as_array().unwrap().contains(&json!(claim)), "{reference:#}");
            }
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    fn native_executor_rejects_misbound_reference_before_provider_request() {
        let (dir, _reference_id, _source_sha, _) = current_vision_fixture();
        let provider = vision_provider("http://127.0.0.1:1/v1", "must-not-be-called");
        let executor = NativeToolExecutor::new(&dir, &dir, true).unwrap().with_vision(provider);
        let error = executor.execute(
            "wrong-reference", "cad_inspect_references",
            &json!({"reference_id":"sha256:wrong"}), Duration::from_secs(2),
        ).unwrap_err();
        assert!(error.contains("原图") || error.contains("reference"), "{error}");
        assert!(read_json_object(&dir.join("cad-state.json")).unwrap()["reference_inventory"].as_object().unwrap().is_empty());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn native_executor_rejects_unbound_or_invalid_inventory_before_review_provider() {
        let cases = [
            "stale_source_sha",
            "roi_only",
            "empty_evidence",
            "duplicate_evidence",
        ];
        for case in cases {
            let (dir, reference_id, source_sha, evidence_id) = current_vision_fixture();
            let reference = crate::native_vision_assets::reference_input(
                &dir.join("reference-images.json"),
                Some(&reference_id),
                Some(1),
                None,
            )
            .unwrap();
            let mut inventory = json!({
                "reference_id":reference_id,
                "source_sha256":source_sha,
                "roi_norm":null,
                "roi_cache_key":reference.roi_cache_key,
                "observation_valid":true,
                "observation_id":"fixture-observation-v1",
                "observation_version":1,
                "visible_evidence":[{
                    "evidence_id":evidence_id,
                    "description":"A clearly visible outer outline"
                }]
            });
            let mut state = read_json_object(&dir.join("cad-state.json")).unwrap();
            match case {
                "stale_source_sha" => {
                    inventory["source_sha256"] = json!("0".repeat(64));
                    state["reference_inventory"][&reference_id] = inventory;
                }
                "roi_only" => {
                    state["reference_inventory_roi"] = json!({});
                    state["reference_inventory_roi"][&reference_id] = json!({});
                    state["reference_inventory_roi"][&reference_id][&reference.roi_cache_key] =
                        inventory;
                }
                "empty_evidence" => {
                    inventory["visible_evidence"] = json!([]);
                    state["reference_inventory"][&reference_id] = inventory;
                }
                "duplicate_evidence" => {
                    let evidence = inventory["visible_evidence"][0].clone();
                    inventory["visible_evidence"]
                        .as_array_mut()
                        .unwrap()
                        .push(evidence);
                    state["reference_inventory"][&reference_id] = inventory;
                }
                _ => unreachable!("test case is enumerated above"),
            }
            write_json_atomic(&dir.join("cad-state.json"), &state).unwrap();

            let provider = vision_provider("http://127.0.0.1:1/v1", "must-not-be-called");
            let executor = NativeToolExecutor::new(&dir, &dir, true)
                .unwrap()
                .with_vision(provider);
            let error = executor
                .execute(
                    "invalid-inventory",
                    "cad_review_reference_similarity",
                    &json!({"view":"front","preview_generation":9,"reference_id":reference_id}),
                    Duration::from_secs(2),
                )
                .unwrap_err();
            assert!(
                error.starts_with("review_failed:preconditions_missing;"),
                "case={case}, error={error}"
            );
            assert!(
                !error.contains("source inventory") && !error.contains("原图SHA/ROI"),
                "case={case} must not expose private inventory-validation details: {error}"
            );
            assert!(
                read_json_object(&dir.join("cad-state.json")).unwrap()["reference_similarity_history"]
                    .as_array()
                    .is_none_or(Vec::is_empty),
                "case={case} must not create a review record"
            );
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn vision_tools_require_the_native_provider_without_fallback() {
        let (dir, executor) = fixture_with_online(true);
        for tool in ["cad_inspect_references", "cad_review_reference_similarity"] {
            let error = executor
                .execute("vision", tool, &json!({}), Duration::from_secs(1))
                .unwrap_err();
            assert!(error.contains("原生视觉provider未配置"), "{error}");
        }
        assert!(!dir.join("cad-state.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn native_contract_and_disabled_web_keep_state_in_rust() {
        let (dir, executor) = fixture();
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
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        assert_eq!(saved["plan"], original["plan"]);
        assert_eq!(saved["revision"], 7);
        assert_eq!(saved["extension"], original["extension"]);
        executor
            .execute("state", "cad_get_state", &json!({}), Duration::from_secs(1))
            .unwrap();
        fs::remove_dir_all(dir).unwrap();
    }

    fn audit_rows(dir: &Path) -> Vec<Value> {
        fs::read_to_string(dir.join("native-tool-audit.jsonl"))
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    #[test]
    fn audit_records_success_call_id_arguments_and_result_summary() {
        let (dir, executor) = fixture();
        executor
            .execute(
                "dsh-call-17",
                "cad_get_feature_spec",
                &json!({"kind":"box"}),
                Duration::from_secs(1),
            )
            .unwrap();
        let rows = audit_rows(&dir);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["call_id"], "dsh-call-17");
        assert_eq!(rows[0]["tool"], "cad_get_feature_spec");
        assert_eq!(rows[0]["arguments"]["kind"], "box");
        assert_eq!(
            rows[0]["parameter_validation"]["status"],
            "accepted_by_executor"
        );
        assert_eq!(rows[0]["success"], true);
        assert!(rows[0]["result_summary"]["text_sha256"].as_str().is_some());
        assert_eq!(rows[0]["revision_before"], Value::Null);
        assert_eq!(rows[0]["revision_after"], 0);
        fs::remove_dir_all(dir).unwrap();

        let redacted = redact_tool_arguments(&json!({
            "api_key":"credential",
            "nested":{"image_bytes":[1,2,3],"image_preview":"data:image/png;base64,secret"},
            "kind":"box"
        }));
        assert_eq!(redacted["api_key"], "[REDACTED]");
        assert_eq!(redacted["nested"]["image_bytes"], "[REDACTED]");
        assert_eq!(redacted["nested"]["image_preview"], "[REDACTED]");
        assert_eq!(redacted["kind"], "box");
    }

    #[test]
    fn audit_records_duplicate_hole_geometry_noop_without_revision_change() {
        let (dir, executor) = fixture();
        let existing_hole = json!({
            "id":"hole-1","kind":"hole","operation":"cut",
            "parameters":{"diameter":4.0,"through":true,"pos":"point","point":[40.0,12.0,18.0],"axis":"X"},
            "sources":{"diameter":{"type":"image_estimate","evidence":"photo"},
                "point":{"type":"derived","evidence":"face placement"}}
        });
        let state = json!({
            "plan":{"units":{"length":"mm","angle":"deg"},"features":[
                {"id":"body","kind":"box","operation":"add","parameters":{}}, existing_hole
            ]},
            "revision":5,"actions":[],
            "last_preview":{"local_geometry_checks":{"bounds_mm":[0.0,0.0,0.0,40.0,24.0,36.0]}}
        });
        write_json_atomic(&dir.join("cad-state.json"), &state).unwrap();
        executor
            .execute(
                "dsh-hole-array-1",
                "cad_add_hole_array",
                &json!({"face_normal_axis":"X","face_side":"positive","source_type":"image_estimate","evidence":"same photo",
                    "holes":[{"id":"hole-1","face_center":[12.0,18.0],"diameter":4.0}]}),
                Duration::from_secs(2),
            )
            .unwrap();
        let rows = audit_rows(&dir);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["call_id"], "dsh-hole-array-1");
        assert_eq!(rows[0]["success"], true);
        assert_eq!(rows[0]["revision_before"], 5);
        assert_eq!(rows[0]["revision_after"], 5);
        let saved = read_json_object(&dir.join("cad-state.json")).unwrap();
        assert_eq!(saved["revision"], 5);
        assert_eq!(saved["plan"]["features"].as_array().unwrap().len(), 2);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn audit_records_argument_rejection_and_preserves_readable_error() {
        let (dir, executor) = fixture();
        let error = executor
            .execute(
                "dsh-feature-spec-bad-args",
                "cad_get_feature_spec",
                &json!({"kind":"box","unexpected":true}),
                Duration::from_secs(1),
            )
            .unwrap_err();
        let rows = audit_rows(&dir);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["call_id"], "dsh-feature-spec-bad-args");
        assert_eq!(rows[0]["success"], false);
        assert_eq!(
            rows[0]["parameter_validation"]["status"],
            "rejected_by_executor"
        );
        assert_eq!(rows[0]["error"]["type"], "argument_validation");
        assert!(
            error.contains("未知工具参数"),
            "unexpected rejection: {error}"
        );
        assert_eq!(rows[0]["error"]["message"], error);
        assert_eq!(rows[0]["revision_before"], Value::Null);
        assert_eq!(rows[0]["revision_after"], Value::Null);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn audit_records_execution_failure_type_and_original_message() {
        let (dir, executor) = fixture_with_online(true);
        let error = executor
            .execute(
                "dsh-vision-no-provider",
                "cad_inspect_references",
                &json!({}),
                Duration::from_secs(1),
            )
            .unwrap_err();
        assert!(error.contains("原生视觉provider未配置"));
        let rows = audit_rows(&dir);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["call_id"], "dsh-vision-no-provider");
        assert_eq!(rows[0]["success"], false);
        assert_eq!(rows[0]["parameter_validation"]["status"], "not_proven");
        assert_eq!(rows[0]["error"]["type"], "execution");
        assert_eq!(rows[0]["error"]["message"], error);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn corrupt_or_invalid_state_is_preserved_instead_of_silently_reset() {
        let (dir, executor) = fixture();
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
        let (dir, executor) = fixture();
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
        drop(lock);
        assert!(!dir.join("cad-state.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }
}
