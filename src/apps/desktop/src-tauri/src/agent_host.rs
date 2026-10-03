//! Rust-owned DSH ACP session lifecycle for Agent mode.
//!
//! Quick mode remains on its legacy path. DSH Agent tools are registered by
//! Cordis and executed by the Rust host without a Python tool worker.

use crate::acp::AcpProcess;
#[path = "agent_run_guard.rs"]
mod agent_run_guard;
use crate::agent_model_config::{
    STAGE_MODELING, STAGE_PERCEPTION, STAGE_REVIEW, resolve_all as resolve_agent_model_configs,
};
use crate::native_tool_bridge::NativeToolBridge;
use crate::native_tool_executor::NativeToolExecutor;
use crate::native_tool_schemas::native_tool_schemas;
use crate::native_vision_provider::{NativeVisionProvider, ProviderConfig};
use agent_run_guard::{Deadline, ProgressClock, WatchdogPolicy, WriteNoProgressBreaker};
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use reqwest::Url;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::AppHandle;
use tauri::ipc::Channel;

const AGENT_FOLDER: &str = "agent-host";
const SESSION_FILE: &str = "current-session.json";
const REFERENCE_FILE: &str = "reference-images.json";
const USER_TURNS_FILE: &str = "user-turns.json";
const MAX_IMAGE_BYTES: u64 = 20 * 1024 * 1024;
const MAX_MESSAGE_IMAGES: usize = 20;
const MAX_MESSAGE_IMAGE_BYTES: u64 = 200 * 1024 * 1024;
const CANCEL_NONE: u8 = 0;
const CANCEL_USER: u8 = 1;
const CANCEL_WATCHDOG_TOTAL: u8 = 2;
const CANCEL_WATCHDOG_IDLE: u8 = 3;
const CANCEL_NO_PROGRESS: u8 = 4;

#[derive(Clone, Default)]
pub struct AgentHostState {
    inner: Arc<Mutex<HostState>>,
}

#[derive(Default)]
struct HostState {
    current: Option<Arc<ActiveSession>>,
}

struct ActiveSession {
    process: Arc<AcpProcess>,
    tool_bridge: NativeToolBridge,
    id: String,
    fingerprint: String,
    cwd: PathBuf,
    metadata_path: PathBuf,
    image_supported: bool,
    active: AtomicBool,
    cancel_reason: AtomicU8,
    metadata: Mutex<Value>,
}

struct Provider {
    id: String,
    base_url: String,
    model: String,
    api_key: String,
    effort: String,
    budget: Option<u64>,
    context_window: Option<u64>,
    output_cap: Option<u64>,
}

struct Route {
    base_url: String,
    api: &'static str,
    vision: bool,
    reasoning: Vec<(&'static str, Option<&'static str>)>,
    thinking_format: Option<&'static str>,
    thinking_budget_field: bool,
    context_window: Option<u64>,
}

#[derive(Clone, Debug)]
struct ReasoningReport {
    requested: String,
    effective: Option<String>,
    status: &'static str,
}

impl ReasoningReport {
    fn to_value(&self) -> Value {
        json!({
            "reasoning_effort_requested":self.requested,
            "reasoning_effort_effective":self.effective,
            "reasoning_effective_status":self.status
        })
    }
}

struct Image {
    id: String,
    path: PathBuf,
    source_path: PathBuf,
    filename: String,
    media_type: &'static str,
    bytes: Vec<u8>,
}

/// Headless acceptance uses the exact desktop route and profile generator.
/// Credentials remain in memory; this result and the patch never contain them.
pub fn prepare_cli_profile(root: &Path, patch: &Path, config: &Value) -> Result<Value, String> {
    let provider = Provider::parse(config)?;
    let route = route_profile(&provider.base_url, &provider.model)?;
    let plugins = [
        root.join("ai_modeler/agent/dsh_native_tools.mjs"),
        root.join("ai_modeler/agent/dsh_attachment_store.mjs"),
        root.join("ai_modeler/agent/dsh_stream_bridge.mjs"),
    ];
    write_profile_patch(
        patch,
        root,
        [&plugins[0], &plugins[1], &plugins[2]],
        &provider,
        &route,
    )?;
    let prompt_path = root.join("prompts/dsh_agent/system.md");
    let prompt = fs::read(&prompt_path).map_err(|error| error.to_string())?;
    Ok(json!({
        "schema_version":1,"configuration_source":"desktop_production_profile",
        "model":provider.model,"api":route.api,"base_url":route.base_url,
        "image_supported_by_route":route.vision,"reasoning_effort_requested":provider.effort,
        "reasoning_configurable":!route.reasoning.is_empty(),
        "thinking_budget_requested":provider.budget,
        "main_output_cap":positive(provider.output_cap).unwrap_or(32_768),
        "vision_output_cap":positive(provider.output_cap).unwrap_or(4096),
        "context_window":positive(provider.context_window).or(route.context_window).unwrap_or(262_144),
        "prompt_path":prompt_path,"prompt_sha256":sha256(&prompt),"patch_path":patch
    }))
}

/// Apply reasoning through the same ACP option lookup as the desktop host.
pub fn configure_cli_reasoning(
    process: &AcpProcess,
    session_id: &str,
    response: &Value,
    config: &Value,
) -> Result<(), String> {
    let provider = Provider::parse(config)?;
    let route = route_profile(&provider.base_url, &provider.model)?;
    let report = configure_reasoning(process, session_id, response, &route, &provider.effort);
    match report.status {
        "unsupported_route" | "acknowledged" | "acknowledgment_missing_effective_value" => Ok(()),
        "requested_option_unavailable" => {
            Err("DSH ACP 未公布所请求的思考强度，没有启动本轮请求。".into())
        }
        _ => Err("无法设置 DSH 思考强度。".into()),
    }
}

pub fn select_cli_reasoning(config: &Value, session_response: &Value) -> Result<Value, String> {
    let provider = Provider::parse(config)?;
    let route = route_profile(&provider.base_url, &provider.model)?;
    let selected = reasoning_option(session_response, &route, &provider.effort)?;
    Ok(json!({"config_id":"reasoning_effort","value":selected,
        "requested":provider.effort,"configurable":selected.is_some()}))
}

pub fn send_turn(
    state: &AgentHostState,
    app: AppHandle,
    request_id: String,
    text: String,
    image_paths: Vec<String>,
    llm_config: Value,
    online_enabled: bool,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let text = text.trim().to_owned();
    if text.is_empty() && image_paths.is_empty() {
        return Err("请输入建模描述或添加参考图片。".into());
    }
    if image_paths.len() > MAX_MESSAGE_IMAGES {
        return Err(format!("单轮最多接收 {MAX_MESSAGE_IMAGES} 张参考图。"));
    }
    let stage_configs = resolve_stage_configs(&llm_config)?;
    let provider = Provider::parse(&stage_configs[STAGE_MODELING])?;
    let perception_provider = Provider::parse(&stage_configs[STAGE_PERCEPTION])?;
    let review_provider = Provider::parse(&stage_configs[STAGE_REVIEW])?;
    let fingerprint = session_fingerprint(&provider, &stage_configs, online_enabled);
    let session = get_or_create_session(
        state,
        &app,
        &request_id,
        &provider,
        &perception_provider,
        &review_provider,
        &fingerprint,
        online_enabled,
        &on_event,
    )?;
    if session
        .active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("当前 Agent 会话仍在处理上一条消息。".into());
    }
    session.cancel_reason.store(CANCEL_NONE, Ordering::Release);
    let _active_reset = ActiveReset(&session.active, &session.cancel_reason);
    let result = run_turn(
        &session,
        &app,
        &request_id,
        &text,
        &image_paths,
        &provider,
        online_enabled,
        &on_event,
    );
    result
}

struct ActiveReset<'a>(&'a AtomicBool, &'a AtomicU8);

impl Drop for ActiveReset<'_> {
    fn drop(&mut self) {
        self.1.store(CANCEL_NONE, Ordering::Release);
        self.0.store(false, Ordering::Release);
    }
}

fn resolve_stage_configs(llm_config: &Value) -> Result<serde_json::Map<String, Value>, String> {
    let llm = llm_config.get("llm").unwrap_or(llm_config);
    if !llm.is_object() {
        return Err("模型设置 llm 必须是对象。".into());
    }
    let mut global = llm.clone();
    // `agent_send` historically receives only a flattened profile. Preserve
    // that format by assigning an internal identity before stage resolution.
    if global.get("providers").and_then(Value::as_array).is_none()
        && global.get("id").and_then(Value::as_str).is_none()
    {
        global["id"] = json!("legacy-flat-profile");
    }
    let overrides = global
        .get("agent_stage_overrides")
        .cloned()
        .unwrap_or_else(|| json!({}));
    resolve_agent_model_configs(&global, &overrides)
}

fn session_fingerprint(
    modeling: &Provider,
    stage_configs: &serde_json::Map<String, Value>,
    online_enabled: bool,
) -> String {
    // This JSON contains API keys only in transient memory; persist and expose
    // only the final digest so changing any role's key invalidates its session.
    let bytes = serde_json::to_vec(stage_configs).unwrap_or_default();
    sha256(format!(
        "{}:{}",
        modeling.fingerprint(online_enabled),
        sha256(bytes)
    ))
}

pub fn cancel(state: &AgentHostState) -> Result<Value, String> {
    let session = state
        .inner
        .lock()
        .map_err(|_| "Rust Agent Host 状态锁已损坏。")?
        .current
        .clone()
        .ok_or("当前没有活动的 DSH 会话。")?;
    if !session.active.load(Ordering::Acquire) {
        return Ok(json!({"cancelled": false, "message": "当前没有正在运行的 Agent 请求。"}));
    }
    let _ = session.cancel_reason.compare_exchange(
        CANCEL_NONE,
        CANCEL_USER,
        Ordering::AcqRel,
        Ordering::Acquire,
    );
    session
        .process
        .notify("session/cancel", json!({"sessionId": session.id}))?;
    Ok(json!({"cancelled": true, "message": "已向 DSH 发送本轮取消请求。"}))
}

pub fn reset(state: &AgentHostState) -> Result<Value, String> {
    let session = state
        .inner
        .lock()
        .map_err(|_| "Rust Agent Host 状态锁已损坏。")?
        .current
        .clone();
    if let Some(session) = session {
        if session.active.load(Ordering::Acquire) {
            return Err("当前 Agent 正在处理请求，请先停止本轮再新建对话。".into());
        }
        close_session(&session);
        if let Ok(mut guard) = state.inner.lock() {
            if guard
                .current
                .as_ref()
                .is_some_and(|item| Arc::ptr_eq(item, &session))
            {
                guard.current = None;
            }
        }
        let _ = fs::remove_file(&session.metadata_path);
    }
    Ok(json!({"state": "reset"}))
}

pub fn shutdown(state: &AgentHostState) {
    let session = state
        .inner
        .lock()
        .ok()
        .and_then(|mut guard| guard.current.take());
    if let Some(session) = session {
        close_session(&session);
    }
}

pub fn history(app: &AppHandle) -> Result<Value, String> {
    let path = super::app_data_dir(app)?
        .join(AGENT_FOLDER)
        .join(SESSION_FILE);
    let Some(value) = read_json_optional(&path)? else {
        return Ok(json!({"turns": []}));
    };
    Ok(json!({
        "sessionId": value.get("session_id").cloned().unwrap_or(Value::Null),
        "turns": value.get("turns").cloned().unwrap_or_else(|| json!([])),
    }))
}

fn get_or_create_session(
    state: &AgentHostState,
    app: &AppHandle,
    request_id: &str,
    provider: &Provider,
    perception_provider: &Provider,
    review_provider: &Provider,
    fingerprint: &str,
    online_enabled: bool,
    events: &Channel<Value>,
) -> Result<Arc<ActiveSession>, String> {
    let mut guard = state
        .inner
        .lock()
        .map_err(|_| "Rust Agent Host 状态锁已损坏。")?;
    if let Some(current) = guard.current.as_ref() {
        if current.fingerprint == fingerprint {
            if current.process.is_running() {
                return Ok(current.clone());
            }
            current.process.shutdown();
            guard.current = None;
        } else if current.active.load(Ordering::Acquire) {
            return Err("模型配置已变化，但当前 DSH 会话仍在运行；请先停止请求。".into());
        } else {
            let previous = current.clone();
            guard.current = None;
            close_session(&previous);
        }
    }

    let root = super::resource_root(app)?;
    let data_dir = super::app_data_dir(app)?;
    let agent_dir = data_dir.join(AGENT_FOLDER);
    let workspace_root = agent_dir.join("workspaces");
    fs::create_dir_all(&workspace_root)
        .map_err(|error| format!("无法创建 Agent 工作区：{error}"))?;
    let metadata_path = agent_dir.join(SESSION_FILE);
    let mut saved = read_json_optional(&metadata_path)?.unwrap_or_else(|| json!({}));
    if saved
        .get("fingerprint")
        .and_then(Value::as_str)
        .is_some_and(|value| value != fingerprint)
    {
        let _ = events.send(event("agent.warning", "", json!({
            "message":"模型或连接设置已变化，DSH 会新建会话。此前的消息保留在对话显示中，但不会发送给新模型。"
        })));
    }
    let runtime = crate::dsh_runtime::resolve(&root, &data_dir)?;
    if !runtime.node.is_file() || !runtime.dsh_cli.is_file() {
        return Err("应用私有 DSH Runtime 文件不完整，请重新安装运行时。".into());
    }
    let native_plugin = root.join("ai_modeler/agent/dsh_native_tools.mjs");
    let attachments_plugin = root.join("ai_modeler/agent/dsh_attachment_store.mjs");
    let stream_plugin = root.join("ai_modeler/agent/dsh_stream_bridge.mjs");
    for (name, path) in [
        ("CAD 工具", &native_plugin),
        ("图片附件", &attachments_plugin),
        ("事件桥接", &stream_plugin),
    ] {
        if !path.is_file() {
            return Err(format!("找不到 DSH {name}插件：{}", path.display()));
        }
    }
    let prompt_path = root.join("prompts/dsh_agent/system.md");
    let system_prompt = fs::read_to_string(&prompt_path).map_err(|error| {
        format!(
            "无法读取 DSH 系统提示词（{}）：{error}",
            prompt_path.display()
        )
    })?;
    let mut resume = saved.get("fingerprint").and_then(Value::as_str) == Some(fingerprint)
        && saved.get("session_id").and_then(Value::as_str).is_some()
        && saved
            .get("cwd")
            .and_then(Value::as_str)
            .is_some_and(|value| {
                let cwd = Path::new(value);
                cwd.is_absolute() && cwd.starts_with(&workspace_root)
            });
    if resume {
        let cwd = PathBuf::from(saved["cwd"].as_str().unwrap_or_default());
        let local_state = read_json_optional(&cwd.join("cad-state.json"))?.unwrap_or(Value::Null);
        let active = crate::native_active_model::load(
            &crate::native_active_model::root_from_data_dir(&data_dir),
        )?;
        let generation_matches = match active.as_ref() {
            Some(active) => {
                local_state
                    .get("active_model_generation")
                    .and_then(Value::as_u64)
                    == active.get("generation").and_then(Value::as_u64)
            }
            None => {
                local_state
                    .get("active_model_generation")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    == 0
            }
        };
        let state_matches = match active.as_ref() {
            Some(active) => {
                let active_state = active.get("cad_state").filter(|value| value.is_object());
                let session_plan = local_state.get("plan").filter(|value| value.is_object());
                let active_plan = active_state
                    .and_then(|state| state.get("plan"))
                    .filter(|value| value.is_object());
                session_plan == active_plan
                    && local_state.get("revision").and_then(Value::as_i64)
                        == active.get("revision").and_then(Value::as_i64)
            }
            None => local_state.get("plan").is_none_or(Value::is_null),
        };
        if !generation_matches || !state_matches {
            resume = false;
            let _ = events.send(event("agent.warning", "", json!({
                "message":"检测到 Agent 会话的 CADPlan generation 已过期，将从当前原生活动模型新建 DSH 会话。"
            })));
        }
    }
    let cwd = if resume {
        PathBuf::from(saved["cwd"].as_str().unwrap_or_default())
    } else {
        workspace_root.join(format!("session-{}", unique_id()))
    };
    fs::create_dir_all(&cwd).map_err(|error| format!("无法创建 DSH 会话工作区：{error}"))?;
    let session_id_saved = saved
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("pending")
        .to_owned();
    let reference_path = cwd.join(REFERENCE_FILE);
    ensure_json_file(
        &reference_path,
        json!({"schema_version": 1, "session_id": session_id_saved, "images": []}),
        "images",
    )?;
    let turns_path = cwd.join(USER_TURNS_FILE);
    ensure_json_file(
        &turns_path,
        json!({"schema_version": 1, "session_id": session_id_saved, "turns": []}),
        "turns",
    )?;
    if !resume {
        let active_root = crate::native_active_model::root_from_data_dir(&data_dir);
        if let Some(active) = crate::native_active_model::load(&active_root)? {
            let mut cad_state = active.get("cad_state").filter(|value| value.is_object()).cloned()
                .unwrap_or_else(|| json!({"plan":null,"revision":active["revision"],"actions":[],"web_search_history":[]}));
            cad_state["active_model_generation"] = active["generation"].clone();
            write_json_atomic(&cwd.join("cad-state.json"), &cad_state)?;
            if active["reference_manifest"].is_object() {
                write_json_atomic(&reference_path, &active["reference_manifest"])?;
            }
            if active["user_turns_manifest"].is_object() {
                write_json_atomic(&turns_path, &active["user_turns_manifest"])?;
            }
        }
    }
    let host_tool_session_id = format!("tool-{}", unique_id());
    let native_tools_path = cwd.join("native-tool-registration.json");
    let native_schemas_path = cwd.join("native-tool-schemas.json");
    let tool_environment = vec![
        (
            OsString::from("AI_MODELER_PROJECT_ROOT"),
            root.as_os_str().to_owned(),
        ),
        (
            OsString::from("AI_MODELER_STATE_DIR"),
            cwd.as_os_str().to_owned(),
        ),
        (
            OsString::from("AI_MODELER_REFERENCE_MANIFEST"),
            reference_path.as_os_str().to_owned(),
        ),
        (
            OsString::from("AI_MODELER_USER_TURNS_MANIFEST"),
            turns_path.as_os_str().to_owned(),
        ),
        (
            OsString::from("AI_MODELER_ACTIVE_TURN_FILE"),
            cwd.join("active-turn.json").into_os_string(),
        ),
        (
            OsString::from("AI_MODELER_STREAM_FILE"),
            cwd.join("stream-events.jsonl").into_os_string(),
        ),
        (
            OsString::from("AI_MODELER_TRANSPORT_IMAGE_MAP"),
            cwd.join("transport-image-map.json").into_os_string(),
        ),
        (
            OsString::from("AI_MODELER_NATIVE_TOOLS_FILE"),
            native_tools_path.clone().into_os_string(),
        ),
        (
            OsString::from("AI_MODELER_ONLINE_ENABLED"),
            online_enabled.to_string().into(),
        ),
        (OsString::from("AI_MODELER_HAS_REFERENCES"), "false".into()),
        (OsString::from("AI_MODELER_REFERENCE_IMAGES"), "[]".into()),
        (
            OsString::from("AI_MODELER_PROVIDER_BASE_URL"),
            provider.base_url.clone().into(),
        ),
        (
            OsString::from("AI_MODELER_PROVIDER_MODEL"),
            provider.model.clone().into(),
        ),
        (
            OsString::from("AI_MODELER_REVIEW_BASE_URL"),
            review_provider.base_url.clone().into(),
        ),
        (
            OsString::from("AI_MODELER_REVIEW_MODEL"),
            review_provider.model.clone().into(),
        ),
        (
            OsString::from("AI_MODELER_REVIEW_REASONING_EFFORT"),
            review_provider.effort.clone().into(),
        ),
        (
            OsString::from("AI_MODELER_REVIEW_THINKING_BUDGET"),
            review_provider
                .budget
                .map(|value| value.to_string())
                .unwrap_or_default()
                .into(),
        ),
        (
            OsString::from("AIMODELER_LLM_API_KEY"),
            provider.api_key.clone().into(),
        ),
    ];
    let schemas = native_tool_schemas()?;
    let vision_provider = NativeVisionProvider::new(vision_provider_config(perception_provider))
        .map_err(|error| format!("无法初始化感知视觉provider：{error}"))?
        .with_stage_identity(STAGE_PERCEPTION, perception_provider.id.clone());
    let review_vision_provider = NativeVisionProvider::new(vision_provider_config(review_provider))
        .map_err(|error| format!("无法初始化审查视觉provider：{error}"))?
        .with_stage_identity(STAGE_REVIEW, review_provider.id.clone());
    let mut native_executor = NativeToolExecutor::new(root.clone(), cwd.clone(), online_enabled)?
        .with_vision(Arc::new(vision_provider))
        .with_review_vision(Arc::new(review_vision_provider))
        .with_active_model_root(crate::native_active_model::root_from_data_dir(&data_dir));
    if let Some(manifest) = crate::native_occt_runtime::available_manifest(&root)? {
        native_executor = native_executor.with_geometry_runtime(manifest);
    }
    let tool_executor = Arc::new(native_executor);
    let tool_bridge = NativeToolBridge::start(
        host_tool_session_id,
        &native_schemas_path,
        schemas,
        tool_executor,
    )?;
    let profile = route_profile(&provider.base_url, &provider.model)?;
    let patch_path = agent_dir.join("profile-patch.yml");
    write_profile_patch(
        &patch_path,
        &root,
        [&native_plugin, &attachments_plugin, &stream_plugin],
        provider,
        &profile,
    )?;
    fs::create_dir_all(&runtime.home).map_err(|error| format!("无法创建私有 DSH_HOME：{error}"))?;
    let mut command = Command::new(&runtime.node);
    command
        .arg(&runtime.dsh_cli)
        .args(["--profile", "acp", "--patch"])
        .arg(&patch_path)
        .current_dir(&cwd)
        .envs(tool_environment.iter().map(|(key, value)| (key, value)))
        .env("DSH_HOME", &runtime.home)
        .env("DSH_TELEMETRY_DISABLED", "1")
        .env("DSH_SYSTEM_PROMPT", system_prompt)
        .env("AIMODELER_LLM_API_KEY", &provider.api_key)
        .env(
            "AI_MODELER_DSH_NODE_MODULES",
            runtime.root.join("dsh/node_modules"),
        )
        .env(
            "AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE",
            tool_bridge.schemas_path(),
        )
        .env("AI_MODELER_TOOL_BRIDGE_ADDR", tool_bridge.address())
        .env("AI_MODELER_TOOL_BRIDGE_TOKEN", tool_bridge.token())
        .env("AI_MODELER_TOOL_SESSION_ID", tool_bridge.session_id());
    let process = Arc::new(AcpProcess::spawn(&mut command)?);
    let initialize_events = process.subscribe()?;
    let initialized = process.request(
        "initialize",
        json!({
            "protocolVersion": 1,
            "clientInfo": {"name": "AI Modeler", "version": env!("CARGO_PKG_VERSION")},
            "clientCapabilities": {}
        }),
        Some(Duration::from_secs(30)),
    )?;
    let image_supported = initialized
        .pointer("/agentCapabilities/promptCapabilities/image")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let (session_id, session_response, resumed) = if resume {
        let result = process.request(
            "session/resume",
            json!({
                "sessionId": saved["session_id"],
                "cwd": cwd.to_string_lossy(),
                "mcpServers": []
            }),
            Some(Duration::from_secs(30)),
        );
        match result {
            Ok(value) => (
                saved["session_id"].as_str().unwrap_or_default().to_owned(),
                value,
                true,
            ),
            Err(error) => {
                let _ = events.send(event(
                    "agent.warning",
                    "",
                    json!({"message": format!("无法恢复 DSH 会话，将开始新会话：{error}")}),
                ));
                let result = process.request(
                    "session/new",
                    json!({"cwd": cwd.to_string_lossy(), "mcpServers": []}),
                    Some(Duration::from_secs(45)),
                )?;
                let id = result
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .ok_or("session/new 未返回 sessionId。")?
                    .to_owned();
                (id, result, false)
            }
        }
    } else {
        let result = process.request(
            "session/new",
            json!({"cwd": cwd.to_string_lossy(), "mcpServers": []}),
            Some(Duration::from_secs(45)),
        )?;
        let id = result
            .get("sessionId")
            .and_then(Value::as_str)
            .ok_or("session/new 未返回 sessionId。")?
            .to_owned();
        (id, result, false)
    };
    let modeling_reasoning = configure_reasoning(
        &process,
        &session_id,
        &session_response,
        &profile,
        &provider.effort,
    );
    let model_settings = json!({
        "modeling":{
            "provider_id":provider.id,"model":provider.model,
            "reasoning":modeling_reasoning.to_value()
        },
        "perception":{
            "provider_id":perception_provider.id,"model":perception_provider.model,
            "reasoning_effort_requested":perception_provider.effort,
            "reasoning_effort_effective":Value::Null,
            "reasoning_effective_status":"unknown_until_wire_request"
        },
        "review":{
            "provider_id":review_provider.id,"model":review_provider.model,
            "reasoning_effort_requested":review_provider.effort,
            "reasoning_effort_effective":Value::Null,
            "reasoning_effective_status":"unknown_until_wire_request"
        }
    });
    saved["stage_model_settings"] = model_settings;
    let (reasoning_event, message) =
        if let Some(effective) = modeling_reasoning.effective.as_deref() {
            (
                "agent.status",
                format!(
                    "建模模型 reasoning 已确认：requested={} effective={effective}。",
                    modeling_reasoning.requested
                ),
            )
        } else {
            (
                "agent.warning",
                format!(
                    "建模模型 reasoning effective 未知：requested={} status={}。",
                    modeling_reasoning.requested, modeling_reasoning.status
                ),
            )
        };
    let _ = events.send(event(reasoning_event, request_id, json!({
        "message":message,"stage":STAGE_MODELING,"provider_id":provider.id,"model":provider.model,
        "reasoning":modeling_reasoning.to_value()
    })));
    if !resumed
        && saved
            .get("turns")
            .and_then(Value::as_array)
            .is_some_and(|rows| !rows.is_empty())
    {
        archive_history(&agent_dir, &saved)?;
    }
    if !resumed {
        saved = json!({"schema_version":1, "turns": []});
    }
    saved["session_id"] = json!(session_id);
    saved["cwd"] = json!(cwd.to_string_lossy());
    saved["fingerprint"] = json!(fingerprint);
    saved["model"] = json!(provider.model);
    saved["reasoning_effort"] = json!(provider.effort);
    saved["reasoning_effort_requested"] = json!(modeling_reasoning.requested);
    saved["reasoning_effort_effective"] = modeling_reasoning
        .effective
        .as_ref()
        .map(|value| json!(value))
        .unwrap_or(Value::Null);
    saved["reasoning_effective_status"] = json!(modeling_reasoning.status);
    write_json_atomic(&metadata_path, &saved)?;
    drop(initialize_events);
    let session = Arc::new(ActiveSession {
        process,
        tool_bridge,
        id: session_id,
        fingerprint: fingerprint.to_owned(),
        cwd,
        metadata_path,
        image_supported,
        active: AtomicBool::new(false),
        cancel_reason: AtomicU8::new(CANCEL_NONE),
        metadata: Mutex::new(saved),
    });
    guard.current = Some(session.clone());
    Ok(session)
}

fn run_turn(
    session: &Arc<ActiveSession>,
    app: &AppHandle,
    request_id: &str,
    text: &str,
    paths: &[String],
    provider: &Provider,
    online_enabled: bool,
    events: &Channel<Value>,
) -> Result<Value, String> {
    let result = run_turn_inner(
        session,
        app,
        request_id,
        text,
        paths,
        provider,
        online_enabled,
        events,
    );
    let requested = session.cancel_reason.load(Ordering::Acquire);
    let current_model = result
        .as_ref()
        .ok()
        .and_then(|value| value.get("current_model"))
        .cloned()
        .unwrap_or_else(|| current_model_summary(&session.cwd));
    let turns = session
        .metadata
        .lock()
        .ok()
        .and_then(|metadata| metadata.get("turns").cloned())
        .unwrap_or_else(|| json!([]));
    let error_category = if termination_from_cancel(requested).is_some() {
        None
    } else {
        result
            .as_ref()
            .err()
            .map(|error| classify_host_error(error))
    };
    let result =
        normalize_terminal_result(result, requested, &session.id, current_model.clone(), turns);
    let (turn_status, stop_reason, termination_reason) = match termination_from_cancel(requested) {
        Some(value) => value,
        None => match &result {
            Ok(value) => (
                value
                    .get("turn_status")
                    .and_then(Value::as_str)
                    .unwrap_or("ended"),
                value
                    .get("stop_reason")
                    .and_then(Value::as_str)
                    .unwrap_or("ended"),
                "acp_completed",
            ),
            Err(error) => ("error", "host_error", classify_host_error(error)),
        },
    };
    emit(
        events,
        "agent.turn_complete",
        request_id,
        json!({
            "turn_status":turn_status,
            "cancelled":turn_status == "cancelled",
            "stop_reason":stop_reason,
            "termination_reason":termination_reason,
        "error_category":error_category,
            "current_model":current_model
        }),
    );
    result
}

fn normalize_terminal_result(
    result: Result<Value, String>,
    cancel_reason: u8,
    session_id: &str,
    current_model: Value,
    turns: Value,
) -> Result<Value, String> {
    let Some((status, stop_reason, termination_reason)) = termination_from_cancel(cancel_reason)
    else {
        return result;
    };
    let mut value = result.unwrap_or_else(|_| json!({}));
    if !value.is_object() {
        value = json!({});
    }
    let fallback_reply = match termination_reason {
        "user_cancelled" => "本轮已按用户请求取消。",
        "watchdog_total_timeout" => "本轮因达到总时限而停止。",
        "watchdog_idle_timeout" => "本轮因长时间没有有效进展而停止。",
        "repeated_write_no_progress" => "本轮因重复写操作没有进展而停止。",
        _ => "本轮已停止。",
    };
    let reply = value
        .get("reply")
        .and_then(Value::as_str)
        .filter(|reply| !reply.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| fallback_reply.to_owned());
    value["turn_status"] = json!(status);
    value["stop_reason"] = json!(stop_reason);
    value["termination_reason"] = json!(termination_reason);
    value["session_id"] = json!(session_id);
    value["reply"] = json!(reply);
    value["current_model"] = current_model;
    if value.get("turns").is_none() {
        value["turns"] = turns;
    }
    Ok(value)
}

fn classify_host_error(error: &str) -> &'static str {
    let lower = error.to_ascii_lowercase();
    if lower.contains("acp") || lower.contains("json-rpc") || lower.contains("rpc") {
        "acp_rpc_or_transport"
    } else if lower.contains("provider") || lower.contains("模型设置") || lower.contains("配置")
    {
        "configuration"
    } else if lower.contains("图片") || lower.contains("输入") || lower.contains("reference") {
        "input_validation"
    } else if lower.contains("cad") || lower.contains("工具") || lower.contains("geometry") {
        "native_tool_or_geometry"
    } else {
        "host_internal"
    }
}

fn termination_from_cancel(reason: u8) -> Option<(&'static str, &'static str, &'static str)> {
    match reason {
        CANCEL_USER => Some(("cancelled", "cancelled", "user_cancelled")),
        CANCEL_WATCHDOG_TOTAL => Some((
            "stopped",
            "watchdog_total_timeout",
            "watchdog_total_timeout",
        )),
        CANCEL_WATCHDOG_IDLE => Some(("stopped", "watchdog_idle_timeout", "watchdog_idle_timeout")),
        CANCEL_NO_PROGRESS => Some((
            "stopped",
            "repeated_write_no_progress",
            "repeated_write_no_progress",
        )),
        _ => None,
    }
}

fn run_turn_inner(
    session: &Arc<ActiveSession>,
    app: &AppHandle,
    request_id: &str,
    text: &str,
    paths: &[String],
    provider: &Provider,
    online_enabled: bool,
    events: &Channel<Value>,
) -> Result<Value, String> {
    let active_model_root =
        crate::native_active_model::root_from_data_dir(&super::app_data_dir(app)?);
    let mut last_active_generation = crate::native_active_model::generation(&active_model_root)?;
    let images = persist_images(&session.cwd, paths)?;
    if !images.is_empty() && !session.image_supported {
        return Err(
            "当前 DSH ACP 配置没有公布图片输入能力。请确认所选模型和 API 路由支持视觉输入。".into(),
        );
    }
    let total_bytes = images
        .iter()
        .map(|image| image.bytes.len() as u64)
        .sum::<u64>();
    if total_bytes > MAX_MESSAGE_IMAGE_BYTES {
        return Err("单轮参考图片总大小超过 200 MiB。".into());
    }
    update_reference_manifest(
        &session.cwd.join(REFERENCE_FILE),
        &session.id,
        &session.cwd,
        &images,
    )?;
    let turn_id = unique_id();
    let reference_ids = images
        .iter()
        .map(|image| image.id.clone())
        .collect::<Vec<_>>();
    write_json_atomic(
        &session.cwd.join("active-turn.json"),
        &active_turn_record(&session.id, &turn_id, &images),
    )?;
    append_user_turn(
        &session.cwd.join(USER_TURNS_FILE),
        &session.id,
        &turn_id,
        text,
        &reference_ids,
    )?;
    append_transcript(
        session,
        json!({
            "role": "user",
            "text": text,
            "images": images.iter().map(|image| json!({"path": image.source_path.to_string_lossy(), "name": image.filename})).collect::<Vec<_>>(),
            "turn_id": turn_id,
        }),
    )?;

    let mut prompt = Vec::with_capacity(images.len() + 1);
    if !text.is_empty() {
        prompt.push(json!({"type":"text", "text":text}));
    }
    for image in images {
        prompt.push(json!({"type":"image", "mimeType":image.media_type, "data":BASE64.encode(&image.bytes)}));
    }
    let receiver = session.process.subscribe()?;
    let process = Arc::clone(&session.process);
    let sid = session.id.clone();
    let (tx, rx) = mpsc::channel();
    let params = json!({"sessionId":sid, "prompt":prompt});
    thread::Builder::new()
        .name("ai-modeler-acp-prompt".into())
        .spawn(move || {
            let _ = tx.send(process.request("session/prompt", params, None));
        })
        .map_err(|error| format!("无法启动 ACP 请求线程：{error}"))?;

    let mut answer = String::new();
    let mut thought = String::new();
    let mut last_preview_hash = String::new();
    emit(
        events,
        "agent.status",
        request_id,
        json!({"message":"DSH 正在处理本轮请求。", "model":provider.model, "online_enabled":online_enabled}),
    );
    let policy = WatchdogPolicy::default();
    let started = std::time::Instant::now();
    let mut progress = ProgressClock::new(policy);
    let mut breaker = WriteNoProgressBreaker::with_limit(policy.repeated_write_limit);
    let mut audit_tail = ToolAuditTail::new(session.cwd.join("native-tool-audit.jsonl"));
    let mut tool_statuses = std::collections::HashMap::<String, String>::new();
    let mut previous_text_delta = String::new();
    let mut cancellation_started: Option<std::time::Instant> = None;
    let mut cancellation_deadline = None;
    let response = loop {
        let elapsed = started.elapsed();
        let cancel_reason = session.cancel_reason.load(Ordering::Acquire);
        if cancel_reason != CANCEL_NONE && cancellation_started.is_none() {
            cancellation_started = Some(std::time::Instant::now());
        }
        if cancel_reason == CANCEL_NONE {
            if let Some(deadline) = progress.expired(elapsed) {
                let (code, label) = match deadline {
                    Deadline::Total => (CANCEL_WATCHDOG_TOTAL, "watchdog_total_timeout"),
                    Deadline::Idle => (CANCEL_WATCHDOG_IDLE, "watchdog_idle_timeout"),
                };
                if session
                    .cancel_reason
                    .compare_exchange(CANCEL_NONE, code, Ordering::AcqRel, Ordering::Acquire)
                    .is_ok()
                {
                    cancellation_deadline = Some(label);
                    let _ = session
                        .process
                        .notify("session/cancel", json!({"sessionId":session.id}));
                    cancellation_started = Some(std::time::Instant::now());
                    emit(
                        events,
                        "agent.status",
                        request_id,
                        json!({"message":"Agent watchdog 已请求取消，并开始有界收尾。", "reason":label}),
                    );
                }
            }
        }
        if let Some(cancelled_at) = cancellation_started {
            if cancelled_at.elapsed() >= policy.cancel_grace {
                // ACP request(None) has no client timeout; killing the child releases it via fail_pending.
                session.process.shutdown();
                session.tool_bridge.shutdown();
                let reason = cancellation_deadline.unwrap_or("user_cancelled");
                break Err(format!(
                    "DSH ACP cancelled ({reason}); process shut down after bounded drain."
                ));
            }
        }
        match receiver.recv_timeout(Duration::from_millis(120)) {
            Ok(message) => {
                if meaningful_update(
                    &message,
                    &session.id,
                    &mut tool_statuses,
                    &mut previous_text_delta,
                ) {
                    progress.record_progress(started.elapsed());
                }
                route_update(
                    &message,
                    &session.id,
                    request_id,
                    events,
                    &mut answer,
                    &mut thought,
                );
                publish_preview(
                    &session.cwd,
                    app,
                    request_id,
                    events,
                    &mut last_preview_hash,
                    false,
                );
                publish_active_model(
                    &active_model_root,
                    request_id,
                    events,
                    &mut last_active_generation,
                )?;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                publish_preview(
                    &session.cwd,
                    app,
                    request_id,
                    events,
                    &mut last_preview_hash,
                    false,
                );
                publish_active_model(
                    &active_model_root,
                    request_id,
                    events,
                    &mut last_active_generation,
                )?;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        for row in audit_tail.read_new_rows() {
            if row["success"] == true
                && row["revision_before"]
                    .as_i64()
                    .zip(row["revision_after"].as_i64())
                    .is_some_and(|(before, after)| after > before)
            {
                progress.record_progress(started.elapsed());
            }
            if breaker.observe(&row) {
                if session
                    .cancel_reason
                    .compare_exchange(
                        CANCEL_NONE,
                        CANCEL_NO_PROGRESS,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    )
                    .is_ok()
                {
                    let _ = session
                        .process
                        .notify("session/cancel", json!({"sessionId":session.id}));
                    cancellation_started = Some(std::time::Instant::now());
                    emit(
                        events,
                        "agent.status",
                        request_id,
                        json!({"message":"检测到重复写操作无进展，已请求停止本轮。", "reason":"repeated_write_no_progress"}),
                    );
                }
            }
        }
        match rx.try_recv() {
            Ok(result) => break result,
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err("DSH ACP 请求线程意外退出。".into());
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
    };
    let mut response = response?;
    if let Some((_, stop_reason, _)) =
        termination_from_cancel(session.cancel_reason.load(Ordering::Acquire))
    {
        response["stopReason"] = json!(stop_reason);
    }
    while let Ok(message) = receiver.try_recv() {
        route_update(
            &message,
            &session.id,
            request_id,
            events,
            &mut answer,
            &mut thought,
        );
        publish_preview(
            &session.cwd,
            app,
            request_id,
            events,
            &mut last_preview_hash,
            false,
        );
        publish_active_model(
            &active_model_root,
            request_id,
            events,
            &mut last_active_generation,
        )?;
    }
    publish_preview(
        &session.cwd,
        app,
        request_id,
        events,
        &mut last_preview_hash,
        true,
    );
    publish_active_model(
        &active_model_root,
        request_id,
        events,
        &mut last_active_generation,
    )?;
    let stop_reason = response.get("stopReason").and_then(Value::as_str);
    let turn_status = classify_turn_status(stop_reason);
    let current_model = current_model_summary(&session.cwd);
    append_transcript(
        session,
        json!({
            "role":"assistant", "text":answer, "thought":thought,
            "turn_status":turn_status,
            "stop_reason":response.get("stopReason").cloned().unwrap_or(Value::Null)
        }),
    )?;
    let metadata = session
        .metadata
        .lock()
        .map_err(|_| "Agent 会话记录锁已损坏。")?;
    Ok(json!({
        "turn_status": turn_status,
        "session_id": session.id,
        "reply": answer,
        "stop_reason": response.get("stopReason").cloned().unwrap_or(Value::Null),
        "current_model": current_model,
        "turns": metadata.get("turns").cloned().unwrap_or_else(|| json!([]))
    }))
}

fn classify_turn_status(stop_reason: Option<&str>) -> &'static str {
    match stop_reason {
        Some("end_turn") => "completed",
        Some("cancelled") => "cancelled",
        Some(_) => "stopped",
        None => "ended",
    }
}

struct ToolAuditTail {
    path: PathBuf,
    offset: u64,
    partial: Vec<u8>,
}

impl ToolAuditTail {
    fn new(path: PathBuf) -> Self {
        let offset = fs::metadata(&path)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        Self {
            path,
            offset,
            partial: Vec::new(),
        }
    }

    fn read_new_rows(&mut self) -> Vec<Value> {
        let Ok(metadata) = fs::metadata(&self.path) else {
            return Vec::new();
        };
        if metadata.len() < self.offset {
            self.offset = 0;
            self.partial.clear();
        }
        if metadata.len() == self.offset {
            return Vec::new();
        }
        use std::io::{Read, Seek, SeekFrom};
        let Ok(mut file) = fs::File::open(&self.path) else {
            return Vec::new();
        };
        if file.seek(SeekFrom::Start(self.offset)).is_err() {
            return Vec::new();
        }
        let mut added = Vec::new();
        if file.read_to_end(&mut added).is_err() {
            return Vec::new();
        }
        self.offset += added.len() as u64;
        self.partial.extend_from_slice(&added);
        let complete = self
            .partial
            .iter()
            .rposition(|byte| *byte == b'\n')
            .map(|index| index + 1)
            .unwrap_or(0);
        let rows = self.partial[..complete]
            .split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
            .filter_map(|line| serde_json::from_slice::<Value>(line).ok())
            .collect();
        self.partial.drain(..complete);
        rows
    }
}

fn meaningful_update(
    message: &Value,
    session_id: &str,
    tool_statuses: &mut std::collections::HashMap<String, String>,
    previous_text_delta: &mut String,
) -> bool {
    if message.get("method").and_then(Value::as_str) != Some("session/update")
        || message.pointer("/params/sessionId").and_then(Value::as_str) != Some(session_id)
    {
        return false;
    }
    let update = &message["params"]["update"];
    match update
        .get("sessionUpdate")
        .and_then(Value::as_str)
        .unwrap_or_default()
    {
        "agent_message_chunk" | "agent_thought_chunk" => {
            let text = update
                .pointer("/content/text")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if text.is_empty() || *previous_text_delta == text {
                return false;
            }
            *previous_text_delta = text.to_owned();
            true
        }
        "tool_call" | "tool_call_update" => {
            let Some(id) = update
                .get("toolCallId")
                .or_else(|| update.get("id"))
                .and_then(Value::as_str)
            else {
                return false;
            };
            let state = format!(
                "{}|{}|{}",
                update
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                update
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                update
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
            );
            tool_statuses
                .insert(id.to_owned(), state.clone())
                .as_deref()
                != Some(state.as_str())
        }
        // Polling, repeated status, and usage counters are telemetry, not progress.
        _ => false,
    }
}

/// Report CAD state independently from whether this ACP conversation turn ended.
fn current_model_summary(session_dir: &Path) -> Value {
    let state_path = session_dir.join("cad-state.json");
    let state = match read_json_optional(&state_path) {
        Ok(Some(value)) => value,
        Ok(None) => {
            return json!({"exists":false,"status":"none","finish_summary":null,
            "geometric":{"status":"not_built"},"visual":{"status":"not_reviewed"}});
        }
        Err(error) => {
            return json!({"exists":false,"status":"unavailable","error":error,
            "finish_summary":null,"geometric":{"status":"unknown"},"visual":{"status":"unknown"}});
        }
    };
    let Some(plan) = state.get("plan").filter(|value| value.is_object()) else {
        return json!({"exists":false,"status":"none","revision":state.get("revision"),
            "finish_summary":null,"geometric":{"status":"not_built"},"visual":{"status":"not_reviewed"}});
    };
    let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
    let fingerprint = match crate::cad_plan::geometry_fingerprint(plan) {
        Ok(value) => value,
        Err(error) => {
            return json!({"exists":true,"status":"unavailable","revision":revision,
            "error":error,"finish_summary":null,"geometric":{"status":"unknown"},"visual":{"status":"unknown"}});
        }
    };
    let preview = state.get("last_preview").filter(|value| value.is_object());
    let preview_current = state.get("preview_revision").and_then(Value::as_i64) == Some(revision)
        && preview.is_some_and(|value| {
            value.get("revision").and_then(Value::as_i64) == Some(revision)
                && value.get("geometry_fingerprint").and_then(Value::as_str)
                    == Some(fingerprint.as_str())
        });
    let geometric_status = if preview_current {
        preview
            .and_then(|value| value.pointer("/local_geometry_checks/state"))
            .and_then(Value::as_str)
            .unwrap_or("unknown")
    } else if preview.is_some() {
        "stale"
    } else {
        "not_built"
    };
    let stored_finish = state
        .get("finish_summary")
        .filter(|value| value.is_object());
    let finish_current = stored_finish.is_some_and(|value| {
        value.get("revision").and_then(Value::as_i64) == Some(revision)
            && value.get("geometry_fingerprint").and_then(Value::as_str)
                == Some(fingerprint.as_str())
    });
    let finish_summary = if finish_current {
        stored_finish.cloned().unwrap_or(Value::Null)
    } else {
        Value::Null
    };
    let mut state_for_summary = state.clone();
    let evaluated = crate::native_state_summary::summary(
        &mut state_for_summary,
        &session_dir.join("reference-images.json"),
        &session_dir.join("user-turns.json"),
    );
    let visual_summary = evaluated
        .as_ref()
        .ok()
        .and_then(|value| value.get("visual_evidence_summary"));
    let visual_status = match visual_summary {
        None => "unknown",
        Some(summary)
            if summary.get("reference_count").and_then(Value::as_u64) == Some(0)
                || summary
                    .get("references")
                    .and_then(Value::as_object)
                    .is_some_and(|rows| rows.is_empty()) =>
        {
            "no_references"
        }
        Some(summary) if summary.get("all_references_covered") == Some(&Value::Bool(true)) => {
            "covered"
        }
        Some(summary)
            if summary
                .get("comparable_review_pairs")
                .and_then(Value::as_u64)
                .unwrap_or(0)
                > 0 =>
        {
            "partial"
        }
        Some(_) => "not_reviewed",
    };
    let finish_status = if finish_current {
        finish_summary
            .get("finish_state")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
    } else {
        "not_finished"
    };
    let status = if finish_current && finish_summary.get("accepted") == Some(&Value::Bool(true)) {
        "accepted"
    } else {
        "candidate"
    };
    json!({"exists":true,"status":status,"revision":revision,"geometry_fingerprint":fingerprint,
        "finish_status":finish_status,"finish_summary":finish_summary,
        "geometric":{"status":geometric_status,"revision":if preview_current {json!(revision)} else {Value::Null},
            "geometry_fingerprint":if preview_current {json!(fingerprint)} else {Value::Null}},
        "visual":{"status":visual_status,"summary":visual_summary.cloned().unwrap_or(Value::Null)},
        "summary_error":evaluated.err()})
}

fn route_update(
    message: &Value,
    session_id: &str,
    request_id: &str,
    events: &Channel<Value>,
    answer: &mut String,
    thought: &mut String,
) {
    if message.get("method").and_then(Value::as_str) != Some("session/update") {
        return;
    }
    let params = &message["params"];
    if params.get("sessionId").and_then(Value::as_str) != Some(session_id) {
        return;
    }
    let update = &params["update"];
    match update
        .get("sessionUpdate")
        .and_then(Value::as_str)
        .unwrap_or_default()
    {
        "agent_message_chunk" => {
            if let Some(text) = update.pointer("/content/text").and_then(Value::as_str) {
                answer.push_str(text);
                emit(events, "agent.message", request_id, json!({"text":text}));
            }
        }
        "agent_thought_chunk" => {
            if let Some(text) = update.pointer("/content/text").and_then(Value::as_str) {
                thought.push_str(text);
                emit(events, "agent.thought", request_id, json!({"text":text}));
            }
        }
        kind @ ("tool_call" | "tool_call_update") => emit(
            events,
            "agent.tool",
            request_id,
            json!({
                "update":kind,
                "tool_call_id":update.get("toolCallId").or_else(|| update.get("id")),
                "title":update.get("title"),
                "kind":update.get("kind"),
                "status":update.get("status")
            }),
        ),
        "usage_update" => emit(events, "agent.usage", request_id, update.clone()),
        "session_info_update" | "config_option_update" | "current_mode_update" => {
            emit(events, "agent.status", request_id, update.clone())
        }
        _ => {}
    }
}

fn publish_preview(
    cwd: &Path,
    app: &AppHandle,
    request_id: &str,
    events: &Channel<Value>,
    previous_hash: &mut String,
    force: bool,
) {
    let source = cwd.join("current-preview.png");
    let Ok(bytes) = fs::read(source) else { return };
    if bytes.is_empty() {
        return;
    }
    let hash = sha256(&bytes);
    if !force && hash == *previous_hash {
        return;
    }
    let Ok(root) = super::asset_root() else {
        return;
    };
    let target_dir = root.join("agent-previews");
    if fs::create_dir_all(&target_dir).is_err() {
        return;
    }
    let target = target_dir.join(format!("{hash}.png"));
    if !target.exists() {
        let temporary = target.with_extension(format!("{}.tmp", unique_id()));
        if fs::write(&temporary, &bytes).is_err() || fs::rename(&temporary, &target).is_err() {
            let _ = fs::remove_file(temporary);
            return;
        }
    }
    *previous_hash = hash.clone();
    emit(
        events,
        "model.preview",
        request_id,
        json!({
            "stage":"DSH 工具生成模型预览", "preview_path":target.to_string_lossy(), "sha256":hash
        }),
    );
    let _ = app;
}

fn publish_active_model(
    root: &Path,
    request_id: &str,
    events: &Channel<Value>,
    previous_generation: &mut u64,
) -> Result<(), String> {
    let Some(active) = crate::native_active_model::load(root)? else {
        return Ok(());
    };
    let generation = active["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效。")?;
    if generation <= *previous_generation {
        return Ok(());
    }
    let document = active
        .get("document")
        .filter(|value| value.is_object())
        .cloned()
        .ok_or("活动模型缺少有效的 DocumentState。")?;
    emit(events, "model.changed", request_id, document);
    *previous_generation = generation;
    Ok(())
}

fn emit(channel: &Channel<Value>, name: &str, request_id: &str, data: Value) {
    let _ = channel.send(event(name, request_id, data));
}

fn event(name: &str, request_id: &str, data: Value) -> Value {
    json!({"event":name, "request_id":request_id, "data":data})
}

fn active_turn_record(session_id: &str, turn_id: &str, images: &[Image]) -> Value {
    let reference_images = images
        .iter()
        .map(|image| {
            json!({
                "id": image.id,
                "sha256": image.id.strip_prefix("sha256:"),
                "filename": image.filename,
                "media_type": image.media_type,
                "bytes": image.bytes.len(),
                "source_kind": "user_reference"
            })
        })
        .collect::<Vec<_>>();
    json!({
        "schema_version": 1,
        "session_id": session_id,
        "turn_id": turn_id,
        "reference_images": reference_images,
    })
}

fn persist_images(cwd: &Path, paths: &[String]) -> Result<Vec<Image>, String> {
    let asset_root = super::asset_root()?
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let reference_dir = cwd.join("reference-assets");
    fs::create_dir_all(&reference_dir).map_err(|error| error.to_string())?;
    let mut images = Vec::new();
    let mut total = 0_u64;
    for value in paths {
        let source = PathBuf::from(value)
            .canonicalize()
            .map_err(|error| format!("参考图片不存在：{error}"))?;
        if !source.starts_with(&asset_root) {
            return Err("Agent 只能读取桌面端暂存的参考图片。".into());
        }
        let filename = source
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("reference-image")
            .to_owned();
        let extension = source
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        let media_type = match extension.as_str() {
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "webp" => "image/webp",
            "gif" => "image/gif",
            "bmp" => {
                return Err(format!(
                    "DSH ACP 不支持 BMP 图片（{filename}），请先另存为 PNG 或 JPEG。"
                ));
            }
            _ => {
                return Err(format!(
                    "不支持的 Agent 图片格式：{filename}。请使用 PNG、JPG、WEBP 或 GIF。"
                ));
            }
        };
        let bytes =
            fs::read(&source).map_err(|error| format!("无法读取参考图 {filename}：{error}"))?;
        if bytes.is_empty() || bytes.len() as u64 > MAX_IMAGE_BYTES {
            return Err(format!("参考图 {filename} 为空或超过 20 MiB。"));
        }
        validate_image_signature(&bytes, media_type, &filename)?;
        total = total.saturating_add(bytes.len() as u64);
        if total > MAX_MESSAGE_IMAGE_BYTES {
            return Err("单轮参考图片总大小超过 200 MiB。".into());
        }
        let hash = sha256(&bytes);
        let normalized_ext = if extension == "jpeg" {
            "jpg"
        } else {
            extension.as_str()
        };
        let target = reference_dir.join(format!("{hash}.{normalized_ext}"));
        if target.is_file() {
            if sha256(&fs::read(&target).map_err(|error| error.to_string())?) != hash {
                return Err(format!("参考图缓存校验失败：{}", target.display()));
            }
        } else {
            let temporary = target.with_extension(format!("{}.tmp", unique_id()));
            fs::write(&temporary, &bytes).map_err(|error| error.to_string())?;
            fs::rename(&temporary, &target).map_err(|error| {
                let _ = fs::remove_file(&temporary);
                error.to_string()
            })?;
        }
        images.push(Image {
            id: format!("sha256:{hash}"),
            path: target,
            source_path: source,
            filename,
            media_type,
            bytes,
        });
    }
    Ok(images)
}

fn validate_image_signature(bytes: &[u8], media_type: &str, filename: &str) -> Result<(), String> {
    let valid = match media_type {
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg" => bytes.starts_with(&[0xff, 0xd8, 0xff]),
        "image/webp" => bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP",
        "image/gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(format!("参考图扩展名与实际图片格式不匹配：{filename}"))
    }
}

fn update_reference_manifest(
    path: &Path,
    session_id: &str,
    cwd: &Path,
    added: &[Image],
) -> Result<(), String> {
    let mut manifest =
        read_json_optional(path)?.unwrap_or_else(|| json!({"schema_version":1,"images":[]}));
    if manifest.get("schema_version").and_then(Value::as_u64) != Some(1)
        || !manifest.get("images").is_some_and(Value::is_array)
    {
        return Err("Agent 参考图清单结构无效；保留原文件供诊断。".into());
    }
    let rows = manifest["images"]
        .as_array_mut()
        .expect("validated image list");
    for image in added {
        if rows
            .iter()
            .any(|row| row.get("id").and_then(Value::as_str) == Some(image.id.as_str()))
        {
            continue;
        }
        let hash = image.id.trim_start_matches("sha256:");
        rows.push(json!({
            "id":image.id,"sha256":hash,"filename":image.filename,
            "width":Value::Null,"height":Value::Null,"media_type":image.media_type,
            "path":image.path.to_string_lossy()
        }));
    }
    manifest["session_id"] = json!(session_id);
    let root = cwd
        .join("reference-assets")
        .canonicalize()
        .map_err(|error| error.to_string())?;
    for row in manifest["images"].as_array().expect("validated image list") {
        let value = row
            .get("path")
            .and_then(Value::as_str)
            .ok_or("参考图清单缺少 path。")?;
        let image_path = PathBuf::from(value)
            .canonicalize()
            .map_err(|error| error.to_string())?;
        if !image_path.starts_with(&root) {
            return Err("参考图清单路径越过会话资产目录。".into());
        }
        let hash = sha256(&fs::read(&image_path).map_err(|error| error.to_string())?);
        let expected_id = format!("sha256:{hash}");
        if row.get("sha256").and_then(Value::as_str) != Some(hash.as_str())
            || row.get("id").and_then(Value::as_str) != Some(expected_id.as_str())
        {
            return Err("会话参考图内容哈希与清单不匹配。".into());
        }
    }
    write_json_atomic(path, &manifest)
}

fn append_user_turn(
    path: &Path,
    session_id: &str,
    turn_id: &str,
    prompt: &str,
    references: &[String],
) -> Result<(), String> {
    let mut value = read_json_optional(path)?
        .unwrap_or_else(|| json!({"schema_version":1,"session_id":session_id,"turns":[]}));
    if value.get("schema_version").and_then(Value::as_u64) != Some(1)
        || !value.get("turns").is_some_and(Value::is_array)
    {
        return Err("Agent 用户轮次清单结构无效；保留原文件供诊断。".into());
    }
    value["session_id"] = json!(session_id);
    value["turns"].as_array_mut().expect("validated turns").push(json!({
        "turn_id":turn_id,"session_id":session_id,"raw_user_prompt":prompt,"reference_ids":references,
        "at_epoch_ms":SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis(),
        "intent_source":"unclassified"
    }));
    write_json_atomic(path, &value)
}

fn append_transcript(session: &ActiveSession, turn: Value) -> Result<(), String> {
    let mut metadata = session
        .metadata
        .lock()
        .map_err(|_| "Agent 会话记录锁已损坏。")?;
    let turns = metadata["turns"]
        .as_array_mut()
        .ok_or("Agent 会话记录 turns 字段无效。")?;
    if turn["role"] == "assistant" {
        if let Some(user_turn) = turns
            .iter_mut()
            .rev()
            .find(|item| item["role"] == "user" && item.get("assistant").is_none())
        {
            user_turn["assistant"] = turn.get("text").cloned().unwrap_or(Value::Null);
            user_turn["stop_reason"] = turn.get("stop_reason").cloned().unwrap_or(Value::Null);
        } else {
            turns.push(turn);
        }
    } else {
        turns.push(turn);
    }
    write_json_atomic(&session.metadata_path, &metadata)
}

fn route_profile(base_url: &str, model: &str) -> Result<Route, String> {
    let mut url = Url::parse(base_url.trim()).map_err(|error| format!("API 地址无效：{error}"))?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("API 地址必须是有效的 HTTP 或 HTTPS URL。".into());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("请把 API 密钥填在密钥字段中，不要嵌入 API 地址。".into());
    }
    let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
    let model = model.to_ascii_lowercase();
    let path = url.path().trim_end_matches('/').to_owned();
    let responses_path = path.to_ascii_lowercase().ends_with("/responses");
    let official_responses = host == "api.deepseek.com"
        && matches!(model.as_str(), "deepseek-flash" | "deepseek-v4-pro");
    let is_responses = responses_path || official_responses;
    if responses_path {
        let root_path = &path[..path.len() - "/responses".len()];
        url.set_path(root_path);
    }
    let base_url = url.as_str().trim_end_matches('/').to_owned();
    let siliconflow_kimi = host.contains("siliconflow") && model.starts_with("moonshotai/kimi-");
    let reasoning = if is_responses {
        vec![
            ("off", None),
            ("low", Some("low")),
            ("high", Some("high")),
            ("max", Some("max")),
        ]
    } else if siliconflow_kimi {
        vec![("off", None), ("low", Some("enabled"))]
    } else {
        Vec::new()
    };
    Ok(Route {
        base_url,
        api: if is_responses {
            "openai-responses"
        } else {
            "openai-completions"
        },
        vision: (host == "api.deepseek.com" && is_responses && model == "deepseek-flash")
            || (host.contains("siliconflow") && model == "moonshotai/kimi-k2.7-code"),
        reasoning,
        thinking_format: if siliconflow_kimi {
            Some("qwen")
        } else if host.contains("deepseek") && !is_responses {
            Some("deepseek")
        } else {
            None
        },
        thinking_budget_field: siliconflow_kimi,
        context_window: if host == "api.siliconflow.cn" && model == "moonshotai/kimi-k2.7-code" {
            Some(256_000)
        } else {
            None
        },
    })
}

fn write_profile_patch(
    path: &Path,
    root: &Path,
    plugins: [&Path; 3],
    provider: &Provider,
    route: &Route,
) -> Result<(), String> {
    let quote = |value: &str| serde_json::to_string(value).unwrap_or_else(|_| "\"\"".into());
    let model = quote(&provider.model);
    let context_window = positive(provider.context_window)
        .or(route.context_window)
        .unwrap_or(262_144);
    let output_cap = positive(provider.output_cap).unwrap_or(32_768);
    let mut provider_lines = vec![
        "        displayName: AI Modeler".to_owned(),
        "        apiKeyEnv: AIMODELER_LLM_API_KEY".to_owned(),
        format!("        api: {}", route.api),
        format!("        baseURL: {}", quote(&route.base_url)),
        "        retryPolicy:".to_owned(),
        "          mode: normal".to_owned(),
        "          maxRetries: 3".to_owned(),
        "        models:".to_owned(),
        format!("          - id: {model}"),
        format!("            name: {model}"),
        format!("            contextWindow: {context_window}"),
        format!("            maxTokens: {output_cap}"),
    ];
    if route.vision {
        provider_lines.push("            input: [text, image]".into());
    }
    if !route.reasoning.is_empty() {
        provider_lines.push("            reasoningEfforts:".into());
        for (name, value) in &route.reasoning {
            provider_lines.push(format!(
                "              {name}: {}",
                value.map(quote).unwrap_or_else(|| "null".into())
            ));
        }
        if route.thinking_budget_field {
            if let Some(budget) = positive(provider.budget) {
                if provider.effort != "none" {
                    provider_lines.push("            thinkingBudgets:".into());
                    provider_lines.push(format!("              low: {budget}"));
                }
            }
        }
    }
    if route.vision {
        provider_lines.extend([
            "        defaultInput: [text, image]".into(),
            "        maxRequestImageBytes: 20971520".into(),
            "        requestImagePixelBudget: 64000000".into(),
            "        requestImageMaxBytes: 20971520".into(),
        ]);
    }
    let mut compat = Vec::new();
    if route.api == "openai-completions" {
        compat.push("          supportsDeveloperRole: false".to_owned());
    }
    if let Some(format) = route.thinking_format {
        if format == "qwen" {
            compat.push("          thinkingTokenBudgetField: \"thinking_budget\"".into());
        }
        compat.push(format!("          thinkingFormat: {format}"));
    }
    if route.api == "openai-responses" {
        compat.push("          supportsMaxOutputTokens: false".into());
    }
    if !compat.is_empty() {
        provider_lines.push("        compat:".into());
        provider_lines.extend(compat);
    }

    let mut lines = vec![
        "# Generated by AI Modeler. Credentials are supplied through the child environment."
            .to_owned(),
        "- id: llm-pi-ai".into(),
        "  config:".into(),
        "    providers:".into(),
        "      ai-modeler:".into(),
    ];
    lines.extend(provider_lines);
    lines.extend([
        "- id: acp".into(),
        "  config:".into(),
        "    provider: ai-modeler".into(),
        format!("    model: {model}"),
        "- id: system-prompt".into(),
        "  config:".into(),
        "    includeHarnessIdentity: false".into(),
        "    includeRuntimeContext: false".into(),
        "    personaPrefix: !!js process.env.DSH_SYSTEM_PROMPT ?? ''".into(),
        "    personaSuffix: ''".into(),
    ]);
    for id in [
        "session-log-deepseek",
        "session-telemetry-otel",
        "attachment-local",
        "agent-instructions",
        "tool-bash",
        "tool-pwsh",
        "tool-fs",
        "tool-fs-search",
        "tool-skill",
        "tool-jobs",
        "tool-subagent-control",
        "tool-subagent-list-agents",
        "tool-subagent",
        "tool-subagent-fork",
        "tool-workflow",
        "tool-todo",
        "tool-goal",
        "tool-ralph",
        "tool-web",
    ] {
        lines.push(format!("- id: {id}"));
        lines.push("  disabled: true".into());
    }
    let plugin_ids = [
        "ai-modeler-native-tools",
        "ai-modeler-attachment-store",
        "ai-modeler-live-stream",
    ];
    for (id, plugin) in plugin_ids.into_iter().zip(plugins) {
        let uri = plugin.canonicalize().map_err(|error| {
            format!("无法解析 Cordis 插件路径（{}）：{error}", plugin.display())
        })?;
        let uri = Url::from_file_path(uri).map_err(|_| "无法编码 Cordis 插件 file URI。")?;
        lines.push("- insert:".into());
        lines.push(format!("    - id: {id}"));
        lines.push(format!("      name: {}", quote(uri.as_str())));
    }
    if !root.join("prompts/dsh_agent/system.md").is_file() {
        return Err("DSH Agent 系统提示词文件不存在。".into());
    }
    fs::create_dir_all(path.parent().ok_or("DSH patch 文件缺少父目录。")?)
        .map_err(|error| error.to_string())?;
    fs::write(path, format!("{}\n", lines.join("\n")))
        .map_err(|error| format!("无法写入 DSH ACP profile patch：{error}"))
}

fn configure_reasoning(
    process: &AcpProcess,
    session_id: &str,
    response: &Value,
    route: &Route,
    configured: &str,
) -> ReasoningReport {
    if route.reasoning.is_empty() {
        return ReasoningReport {
            requested: configured.to_owned(),
            effective: None,
            status: "unsupported_route",
        };
    }
    let selected = match reasoning_option(response, route, configured) {
        Ok(Some(value)) => value,
        Ok(None) => {
            return ReasoningReport {
                requested: configured.to_owned(),
                effective: None,
                status: "unsupported_route",
            };
        }
        Err(_) => {
            return ReasoningReport {
                requested: configured.to_owned(),
                effective: None,
                status: "requested_option_unavailable",
            };
        }
    };
    match process.request(
        "session/set_config_option",
        json!({
            "sessionId":session_id,"configId":"reasoning_effort","value":selected
        }),
        Some(Duration::from_secs(15)),
    ) {
        Ok(ack) => match acknowledged_config_value(&ack) {
            Some(effective) => ReasoningReport {
                requested: configured.to_owned(),
                effective: Some(effective),
                status: "acknowledged",
            },
            None => ReasoningReport {
                requested: configured.to_owned(),
                effective: None,
                status: "acknowledgment_missing_effective_value",
            },
        },
        Err(_) => ReasoningReport {
            requested: configured.to_owned(),
            effective: None,
            status: "set_config_option_failed",
        },
    }
}

fn acknowledged_config_value(response: &Value) -> Option<String> {
    let matches_reasoning = |object: &Value| {
        object
            .get("configId")
            .or_else(|| object.get("config_id"))
            .or_else(|| object.get("id"))
            .and_then(Value::as_str)
            == Some("reasoning_effort")
    };
    let value_from = |object: &Value| {
        [
            "currentValue",
            "current_value",
            "selectedValue",
            "selected_value",
            "value",
        ]
        .iter()
        .find_map(|key| object.get(*key).and_then(Value::as_str))
        .map(str::to_owned)
    };

    if matches_reasoning(response) {
        return value_from(response);
    }
    for key in ["configOptions", "config_options"] {
        if let Some(rows) = response.get(key).and_then(Value::as_array) {
            if let Some(value) = rows
                .iter()
                .find_map(|row| matches_reasoning(row).then(|| value_from(row)).flatten())
            {
                return Some(value);
            }
        }
    }
    if let Some(option) = response
        .get("configOption")
        .or_else(|| response.get("config_option"))
    {
        if matches_reasoning(option) {
            return value_from(option);
        }
    }
    if response
        .get("configId")
        .or_else(|| response.get("config_id"))
        .or_else(|| response.get("id"))
        .is_some()
    {
        return None;
    }
    // A bare explicit value in this method's response is an acknowledgement
    // because the request itself names exactly one config option.
    response
        .get("value")
        .and_then(Value::as_str)
        .map(str::to_owned)
}

fn reasoning_option(
    response: &Value,
    route: &Route,
    configured: &str,
) -> Result<Option<String>, String> {
    if route.reasoning.is_empty() {
        return Ok(None);
    }
    let configured = configured.to_ascii_lowercase();
    let target = match configured.as_str() {
        "" | "auto" | "default" | "none" | "off" | "disabled" => "off",
        "xhigh" | "ultra" => "max",
        "minimal" => "low",
        "medium" => "high",
        value => value,
    };
    let value = find_config_value(&response["configOptions"], "reasoning_effort", target)
        .ok_or_else(|| format!("DSH ACP 未公布思考强度 `{target}`，没有启动本轮请求。"))?;
    Ok(Some(value))
}

fn positive(value: Option<u64>) -> Option<u64> {
    value.filter(|value| *value > 0)
}

fn find_config_value(options: &Value, id: &str, target: &str) -> Option<String> {
    let option = options
        .as_array()?
        .iter()
        .find(|item| item.get("id").and_then(Value::as_str) == Some(id))?;
    let wanted = match target {
        "off" => ["off", "none", "disabled"].as_slice(),
        "low" => ["low", "minimal", "enabled"].as_slice(),
        "high" => ["high", "medium"].as_slice(),
        "max" => ["max", "xhigh", "ultra"].as_slice(),
        _ => return find_value_by_name_or_id(option.get("options")?, target),
    };
    let mut values = Vec::new();
    collect_config_values(option.get("options")?, &mut values);
    values.into_iter().find_map(|(value, name)| {
        let value_lower = value.to_ascii_lowercase();
        let name_lower = name.to_ascii_lowercase();
        if wanted
            .iter()
            .any(|item| *item == value_lower || *item == name_lower)
        {
            Some(value)
        } else {
            None
        }
    })
}

fn find_value_by_name_or_id(options: &Value, target: &str) -> Option<String> {
    let mut values = Vec::new();
    collect_config_values(options, &mut values);
    values
        .into_iter()
        .find(|(value, name)| {
            value.eq_ignore_ascii_case(target) || name.eq_ignore_ascii_case(target)
        })
        .map(|(value, _)| value)
}

fn collect_config_values(options: &Value, output: &mut Vec<(String, String)>) {
    if let Some(rows) = options.as_array() {
        for row in rows {
            if let (Some(value), Some(name)) = (
                row.get("value").and_then(Value::as_str),
                row.get("name").and_then(Value::as_str),
            ) {
                output.push((value.to_owned(), name.to_owned()));
            } else if let Some(nested) = row.get("options") {
                collect_config_values(nested, output);
            }
        }
    }
}

impl Provider {
    fn parse(value: &Value) -> Result<Self, String> {
        let base_url = value
            .get("base_url")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        let model = value
            .get("model")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        if base_url.is_empty() || model.is_empty() {
            return Err("请先在模型设置中填写 API 地址和模型名称。".into());
        }
        let api_key = value
            .get("api_key")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        let effort = value
            .get("reasoning_effort")
            .and_then(Value::as_str)
            .unwrap_or("none")
            .trim()
            .to_ascii_lowercase();
        let id = value
            .get("provider_id")
            .or_else(|| value.get("id"))
            .and_then(Value::as_str)
            .unwrap_or("legacy-flat-profile")
            .trim()
            .to_owned();
        let positive_json = |key: &str| {
            value
                .get(key)
                .and_then(Value::as_u64)
                .filter(|value| *value > 0)
        };
        let output_cap = positive_json("dsh_output_cap").or_else(|| positive_json("max_tokens"));
        route_profile(&base_url, &model)?;
        Ok(Self {
            id,
            base_url,
            model,
            api_key: if api_key.is_empty() {
                "sk-local".into()
            } else {
                api_key
            },
            effort,
            budget: positive_json("thinking_budget"),
            context_window: positive_json("dsh_context_window"),
            output_cap,
        })
    }

    fn fingerprint(&self, online: bool) -> String {
        let value = json!({
            "base_url":self.base_url, "model":self.model, "api_key_sha256":sha256(self.api_key.as_bytes()),
            "effort":self.effort, "budget":self.budget, "context_window":self.context_window,
            "output_cap":self.output_cap, "online":online
        });
        sha256(serde_json::to_vec(&value).unwrap_or_default())
    }
}

fn vision_provider_config(provider: &Provider) -> ProviderConfig {
    ProviderConfig {
        base_url: provider.base_url.clone(),
        model: provider.model.clone(),
        api_key: provider.api_key.clone(),
        effort: provider.effort.clone(),
        thinking_budget: provider.budget,
        max_output_tokens: positive(provider.output_cap).unwrap_or(4096),
    }
}

fn close_session(session: &ActiveSession) {
    if session.active.load(Ordering::Acquire) {
        let _ = session
            .process
            .notify("session/cancel", json!({"sessionId":session.id}));
    }
    let _ = session.process.request(
        "session/close",
        json!({"sessionId":session.id}),
        Some(Duration::from_secs(4)),
    );
    session.process.shutdown();
    session.tool_bridge.shutdown();
}

fn ensure_json_file(path: &Path, value: Value, list_key: &str) -> Result<(), String> {
    if !path.exists() {
        return write_json_atomic(path, &value);
    }
    let existing = read_json_optional(path)?.ok_or("Agent 清单意外丢失。")?;
    if !existing.get(list_key).is_some_and(Value::is_array) {
        return Err(format!(
            "Agent 清单 `{list_key}` 字段无效；保留原文件供诊断。"
        ));
    }
    Ok(())
}

fn read_json_optional(path: &Path) -> Result<Option<Value>, String> {
    match fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text)
            .map(Some)
            .map_err(|error| format!("无法解析会话记录（{}）：{error}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("无法读取会话记录（{}）：{error}", path.display())),
    }
}

fn write_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    fs::create_dir_all(path.parent().ok_or("JSON 文件缺少父目录。")?)
        .map_err(|error| error.to_string())?;
    let temporary = path.with_extension(format!("{}.tmp", unique_id()));
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(&temporary, path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("无法保存 Agent 会话记录：{error}")
    })
}

fn archive_history(directory: &Path, metadata: &Value) -> Result<(), String> {
    let turns = metadata
        .get("turns")
        .and_then(Value::as_array)
        .filter(|rows| !rows.is_empty());
    let Some(turns) = turns else {
        return Ok(());
    };
    let archive_dir = directory.join("history-archive");
    fs::create_dir_all(&archive_dir).map_err(|error| error.to_string())?;
    let session = metadata
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let path = archive_dir.join(format!("{}-{}.json", safe_component(session), unique_id()));
    write_json_atomic(&path, &json!({"session_id":session,"turns":turns}))
}

fn unique_id() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .to_string()
}

fn safe_component(value: &str) -> String {
    let output = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .take(80)
        .collect::<String>();
    if output.is_empty() {
        "session".into()
    } else {
        output
    }
}

fn sha256(bytes: impl AsRef<[u8]>) -> String {
    Sha256::digest(bytes.as_ref())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{
        ActiveReset, CANCEL_NONE, CANCEL_USER, CANCEL_WATCHDOG_IDLE, Image, Provider,
        ToolAuditTail, acknowledged_config_value, active_turn_record, classify_host_error,
        classify_turn_status, current_model_summary, find_config_value, normalize_terminal_result,
        resolve_stage_configs, route_profile, termination_from_cancel, validate_image_signature,
        write_profile_patch,
    };
    use crate::agent_model_config::{STAGE_MODELING, STAGE_PERCEPTION, STAGE_REVIEW};
    use serde_json::{Value, json};
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};

    #[test]
    fn tool_audit_tail_reads_only_new_complete_rows() {
        let path =
            std::env::temp_dir().join(format!("agent-tool-audit-{}.jsonl", super::unique_id()));
        fs::write(&path, b"").unwrap();
        let mut tail = ToolAuditTail::new(path.clone());
        assert!(tail.read_new_rows().is_empty());
        use std::io::Write;
        let mut file = fs::OpenOptions::new().append(true).open(&path).unwrap();
        file.write_all(b"{\"first\":1}").unwrap();
        assert!(tail.read_new_rows().is_empty());
        file.write_all(b"\n{\"second\":2}\n").unwrap();
        assert_eq!(
            tail.read_new_rows(),
            vec![json!({"first":1}), json!({"second":2})]
        );
        assert!(tail.read_new_rows().is_empty());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn terminal_reason_distinguishes_user_cancel_watchdog_and_error_classes() {
        assert_eq!(
            termination_from_cancel(CANCEL_USER),
            Some(("cancelled", "cancelled", "user_cancelled"))
        );
        assert_eq!(
            termination_from_cancel(2).unwrap().2,
            "watchdog_total_timeout"
        );
        assert_eq!(
            classify_host_error("DSH ACP response channel closed"),
            "acp_rpc_or_transport"
        );
        assert_eq!(
            classify_host_error("模型设置 llm 必须是对象"),
            "configuration"
        );
        assert_eq!(
            classify_host_error("cad geometry rejected"),
            "native_tool_or_geometry"
        );
    }

    #[test]
    fn active_guard_resets_state_when_turn_returns_with_error() {
        let active = AtomicBool::new(true);
        let cancel = AtomicU8::new(CANCEL_USER);
        {
            let _reset = ActiveReset(&active, &cancel);
            let simulated_error: Result<(), &str> = Err("RPC error");
            assert!(simulated_error.is_err());
        }
        assert!(!active.load(Ordering::Acquire));
        assert_eq!(cancel.load(Ordering::Acquire), CANCEL_NONE);
    }

    #[test]
    fn cancellation_errors_become_structured_results_but_host_errors_remain_errors() {
        let cancelled = normalize_terminal_result(
            Err("ACP process closed after cancel".into()),
            CANCEL_USER,
            "session-1",
            json!({"exists":false}),
            json!([]),
        )
        .unwrap();
        assert_eq!(cancelled["turn_status"], "cancelled");
        assert_eq!(cancelled["termination_reason"], "user_cancelled");
        assert!(
            cancelled["reply"]
                .as_str()
                .is_some_and(|reply| !reply.is_empty())
        );

        let stopped = normalize_terminal_result(
            Err("ACP request released by watchdog shutdown".into()),
            CANCEL_WATCHDOG_IDLE,
            "session-1",
            json!({"exists":false}),
            json!([]),
        )
        .unwrap();
        assert_eq!(stopped["turn_status"], "stopped");
        assert_eq!(stopped["termination_reason"], "watchdog_idle_timeout");
        assert!(stopped["reply"].as_str().unwrap().contains("没有有效进展"));

        let real_error = normalize_terminal_result(
            Err("ACP JSON-RPC request failed".into()),
            CANCEL_NONE,
            "session-1",
            json!({}),
            json!([]),
        );
        assert!(real_error.is_err());
    }

    #[test]
    fn only_verified_provider_routes_advertise_vision() {
        assert!(
            route_profile("https://api.deepseek.com/responses", "deepseek-flash")
                .unwrap()
                .vision
        );
        assert!(
            !route_profile("https://api.deepseek.com/responses", "deepseek-v4-pro")
                .unwrap()
                .vision
        );
        assert!(
            route_profile("https://api.siliconflow.cn/v1", "moonshotai/Kimi-K2.7-Code")
                .unwrap()
                .vision
        );
        assert!(
            !route_profile("https://api.siliconflow.cn/v1", "moonshotai/Kimi-K2.5")
                .unwrap()
                .vision
        );
        assert!(
            !route_profile("https://proxy.example/v1", "vision-model")
                .unwrap()
                .vision
        );
    }

    #[test]
    fn acp_reasoning_choices_can_be_grouped() {
        let options = json!([{"id":"reasoning_effort","options":[{"group":"effort","name":"Effort","options":[
            {"value":"off","name":"Off"},{"value":"high","name":"High"}
        ]}]}]);
        assert_eq!(
            find_config_value(&options, "reasoning_effort", "off").as_deref(),
            Some("off")
        );
        assert_eq!(
            find_config_value(&options, "reasoning_effort", "high").as_deref(),
            Some("high")
        );
        assert_eq!(find_config_value(&options, "reasoning_effort", "low"), None);
    }

    #[test]
    fn set_reasoning_ack_must_report_effective_value() {
        assert_eq!(
            acknowledged_config_value(&json!({"configOptions":[
                {"id":"reasoning_effort","currentValue":"high"}
            ]}))
            .as_deref(),
            Some("high")
        );
        assert_eq!(
            acknowledged_config_value(&json!({"configId":"reasoning_effort","value":"low"}))
                .as_deref(),
            Some("low")
        );
        assert_eq!(acknowledged_config_value(&json!({})), None);
        assert_eq!(
            acknowledged_config_value(&json!({"configId":"other","value":"high"})),
            None
        );
    }

    #[test]
    fn stage_profiles_route_per_role_and_keep_legacy_flat_config() {
        let settings = json!({"active_provider_id":"main","providers":[
            {"id":"main","base_url":"https://api.deepseek.com/responses","model":"deepseek-flash","api_key":"main-secret","reasoning_effort":"none"},
            {"id":"perception-p","base_url":"https://api.example.test/v1","model":"vision-a","models":["vision-a"],"api_key":"perception-secret","reasoning_effort":"low"},
            {"id":"review-p","base_url":"https://review.example.test/v1","model":"review-b","api_key":"review-secret","reasoning_effort":"high"}
        ],"agent_stage_overrides":{
            "perception":{"provider_id":"perception-p"},
            "review":{"provider_id":"review-p"}
        }});
        let resolved = resolve_stage_configs(&settings).unwrap();
        assert_eq!(
            Provider::parse(&resolved[STAGE_MODELING]).unwrap().id,
            "main"
        );
        assert_eq!(
            Provider::parse(&resolved[STAGE_PERCEPTION]).unwrap().model,
            "vision-a"
        );
        assert_eq!(
            Provider::parse(&resolved[STAGE_REVIEW]).unwrap().id,
            "review-p"
        );

        let legacy = json!({"base_url":"https://api.deepseek.com/responses",
            "model":"deepseek-flash","api_key":"legacy-secret","reasoning_effort":"high"});
        let resolved = resolve_stage_configs(&legacy).unwrap();
        for role in [STAGE_MODELING, STAGE_PERCEPTION, STAGE_REVIEW] {
            let provider = Provider::parse(&resolved[role]).unwrap();
            assert_eq!(provider.id, "legacy-flat-profile");
            assert_eq!(provider.model, "deepseek-flash");
        }
    }

    #[test]
    fn image_signature_must_match_declared_mime() {
        assert!(validate_image_signature(b"\x89PNG\r\n\x1a\n...", "image/png", "a.png").is_ok());
        assert!(validate_image_signature(b"GIF89a...", "image/gif", "a.gif").is_ok());
        assert!(validate_image_signature(b"GIF89a...", "image/png", "a.png").is_err());
    }

    #[test]
    fn active_turn_record_binds_user_image_hash_to_provider_observation() {
        let hash = "ab".repeat(32);
        let image = Image {
            id: format!("sha256:{hash}"),
            path: PathBuf::from("reference-assets/image.png"),
            source_path: PathBuf::from("image.png"),
            filename: "image.png".into(),
            media_type: "image/png",
            bytes: b"png-bytes".to_vec(),
        };
        let record = active_turn_record("session-1", "turn-2", &[image]);
        assert_eq!(record["schema_version"], 1);
        assert_eq!(record["session_id"], "session-1");
        assert_eq!(record["turn_id"], "turn-2");
        assert_eq!(
            record["reference_images"][0]["id"],
            format!("sha256:{hash}")
        );
        assert_eq!(record["reference_images"][0]["sha256"], hash);
        assert_eq!(
            record["reference_images"][0]["source_kind"],
            "user_reference"
        );
        let serialized = serde_json::to_string(&record).unwrap();
        assert!(!serialized.contains("png-bytes"));
    }

    #[test]
    fn turn_status_is_separate_from_model_completion() {
        assert_eq!(classify_turn_status(Some("end_turn")), "completed");
        assert_eq!(classify_turn_status(Some("cancelled")), "cancelled");
        assert_eq!(classify_turn_status(Some("max_tokens")), "stopped");
        assert_eq!(classify_turn_status(None), "ended");
    }

    #[test]
    fn current_model_reports_geometry_visual_and_finish_independently() {
        let root = std::env::temp_dir().join(format!("agent-current-model-{}", super::unique_id()));
        fs::create_dir_all(&root).unwrap();
        let empty = current_model_summary(&root);
        assert_eq!(empty["exists"], false);
        assert_eq!(empty["status"], "none");
        let plan = crate::cad_plan::create(
            "fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{}}),
            "fixture",
            None,
        );
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        let state = json!({"revision":2,"plan":plan,"preview_revision":2,"last_preview":{
            "revision":2,"geometry_fingerprint":fingerprint,"local_geometry_checks":{"state":"passed"}},
            "finish_summary":null});
        fs::write(
            root.join("cad-state.json"),
            serde_json::to_vec(&state).unwrap(),
        )
        .unwrap();
        fs::write(root.join("reference-images.json"), br#"{"images":[]}"#).unwrap();
        fs::write(root.join("user-turns.json"), br#"{"turns":[]}"#).unwrap();
        let summary = current_model_summary(&root);
        assert_eq!(summary["exists"], true);
        assert_eq!(summary["finish_status"], "not_finished");
        assert_eq!(summary["geometric"]["status"], "passed");
        assert_eq!(summary["visual"]["status"], "no_references");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn stale_finish_summary_cannot_make_current_model_accepted() {
        let root = std::env::temp_dir().join(format!("agent-stale-finish-{}", super::unique_id()));
        fs::create_dir_all(&root).unwrap();
        let plan = crate::cad_plan::create(
            "fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{}}),
            "fixture",
            None,
        );
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        fs::write(root.join("reference-images.json"), br#"{"images":[]}"#).unwrap();
        fs::write(root.join("user-turns.json"), br#"{"turns":[]}"#).unwrap();
        for finish in [
            json!({"accepted":true,"finish_state":"accepted","revision":1,"geometry_fingerprint":fingerprint.clone()}),
            json!({"accepted":true,"finish_state":"accepted","revision":2,"geometry_fingerprint":"old-fingerprint"}),
        ] {
            let state = json!({"revision":2,"plan":plan.clone(),"finish_summary":finish});
            fs::write(
                root.join("cad-state.json"),
                serde_json::to_vec(&state).unwrap(),
            )
            .unwrap();
            let summary = current_model_summary(&root);
            assert_eq!(summary["status"], "candidate");
            assert_eq!(summary["finish_status"], "not_finished");
            assert_eq!(summary["finish_summary"], Value::Null);
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn stale_preview_revision_or_fingerprint_is_not_current_geometry() {
        let root = std::env::temp_dir().join(format!("agent-stale-preview-{}", super::unique_id()));
        fs::create_dir_all(&root).unwrap();
        let plan = crate::cad_plan::create(
            "fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{}}),
            "fixture",
            None,
        );
        let fingerprint = crate::cad_plan::geometry_fingerprint(&plan).unwrap();
        fs::write(root.join("reference-images.json"), br#"{"images":[]}"#).unwrap();
        fs::write(root.join("user-turns.json"), br#"{"turns":[]}"#).unwrap();
        for (preview_revision, row_revision, preview_fingerprint) in
            [(1, 1, fingerprint.as_str()), (2, 2, "old-fingerprint")]
        {
            let state = json!({"revision":2,"plan":plan.clone(),"preview_revision":preview_revision,"last_preview":{
                "revision":row_revision,"geometry_fingerprint":preview_fingerprint,"local_geometry_checks":{"state":"passed"}}});
            fs::write(
                root.join("cad-state.json"),
                serde_json::to_vec(&state).unwrap(),
            )
            .unwrap();
            let summary = current_model_summary(&root);
            assert_eq!(summary["geometric"]["status"], "stale");
            assert_eq!(summary["geometric"]["revision"], Value::Null);
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn profile_patch_has_the_native_route_and_never_contains_credentials() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
        let plugins = [
            root.join("ai_modeler/agent/dsh_native_tools.mjs"),
            root.join("ai_modeler/agent/dsh_attachment_store.mjs"),
            root.join("ai_modeler/agent/dsh_stream_bridge.mjs"),
        ];
        let provider = Provider {
            id: "provider-test".into(),
            base_url: "https://api.deepseek.com/responses".into(),
            model: "deepseek-flash".into(),
            api_key: "test-secret-that-must-not-be-written".into(),
            effort: "high".into(),
            budget: None,
            context_window: None,
            output_cap: None,
        };
        let route = route_profile(&provider.base_url, &provider.model).unwrap();
        let path =
            std::env::temp_dir().join(format!("aimodeler-acp-profile-{}.yml", super::unique_id()));
        write_profile_patch(
            &path,
            &root,
            [&plugins[0], &plugins[1], &plugins[2]],
            &provider,
            &route,
        )
        .unwrap();
        let contents = fs::read_to_string(&path).unwrap();
        assert!(contents.contains("      ai-modeler:\n"));
        assert!(contents.contains("- id: acp\n  config:\n    provider: ai-modeler"));
        assert!(contents.contains("ai-modeler-native-tools"));
        assert!(contents.contains("ai-modeler-attachment-store"));
        assert!(contents.contains("ai-modeler-live-stream"));
        assert!(contents.contains("input: [text, image]"));
        assert!(!contents.contains("test-secret-that-must-not-be-written"));
        if std::env::var_os("AIMODELER_KEEP_PROFILE_PATCH").is_none() {
            let _ = fs::remove_file(path);
        } else {
            eprintln!("PROFILE_PATCH={}", path.display());
        }
    }
}
