//! Rust-owned DSH ACP session lifecycle for Agent mode.
//!
//! Quick mode remains on the Python bridge. The DSH Cordis CAD plugin still
//! uses its legacy one-shot Python geometry worker until the Rust CAD features
//! replace that transitional boundary.

use crate::acp::AcpProcess;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use reqwest::Url;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
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
    id: String,
    fingerprint: String,
    cwd: PathBuf,
    metadata_path: PathBuf,
    image_supported: bool,
    active: AtomicBool,
    metadata: Mutex<Value>,
}

struct Provider {
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

struct Image {
    id: String,
    path: PathBuf,
    source_path: PathBuf,
    filename: String,
    media_type: &'static str,
    bytes: Vec<u8>,
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
    let provider = Provider::parse(&llm_config)?;
    let fingerprint = provider.fingerprint(online_enabled);
    let session = get_or_create_session(
        state,
        &app,
        &provider,
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
    session.active.store(false, Ordering::Release);
    result
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
    provider: &Provider,
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

    let (root, packaged) = super::project_root(app)?;
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
    let (_, prefix_args) = super::python_command(app, &root, packaged)?;
    if !prefix_args.is_empty() {
        return Err("当前 Python 启动方式带有参数，暂不能作为 DSH Cordis 工具解释器。".into());
    }

    let resume = saved.get("fingerprint").and_then(Value::as_str) == Some(fingerprint)
        && saved.get("session_id").and_then(Value::as_str).is_some()
        && saved
            .get("cwd")
            .and_then(Value::as_str)
            .is_some_and(|value| {
                let cwd = Path::new(value);
                cwd.is_absolute() && cwd.starts_with(&workspace_root)
            });
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
    let python_path = super::python_command(app, &root, packaged)?.0;
    let mut command = Command::new(&runtime.node);
    command
        .arg(&runtime.dsh_cli)
        .args(["--profile", "acp", "--patch"])
        .arg(&patch_path)
        .current_dir(&cwd)
        .env("DSH_HOME", &runtime.home)
        .env("DSH_TELEMETRY_DISABLED", "1")
        .env("DSH_SYSTEM_PROMPT", system_prompt)
        .env("AIMODELER_LLM_API_KEY", &provider.api_key)
        .env("AI_MODELER_PYTHON", python_path)
        .env("AI_MODELER_PROJECT_ROOT", &root)
        .env("AI_MODELER_STATE_DIR", &cwd)
        .env("AI_MODELER_REFERENCE_MANIFEST", &reference_path)
        .env("AI_MODELER_USER_TURNS_MANIFEST", &turns_path)
        .env("AI_MODELER_ACTIVE_TURN_FILE", cwd.join("active-turn.json"))
        .env("AI_MODELER_STREAM_FILE", cwd.join("stream-events.jsonl"))
        .env(
            "AI_MODELER_TRANSPORT_IMAGE_MAP",
            cwd.join("transport-image-map.json"),
        )
        .env(
            "AI_MODELER_DSH_NODE_MODULES",
            runtime.root.join("dsh/node_modules"),
        )
        .env(
            "AI_MODELER_NATIVE_TOOLS_FILE",
            cwd.join("native-tool-registration.json"),
        )
        .env("AI_MODELER_ONLINE_ENABLED", online_enabled.to_string())
        .env("AI_MODELER_HAS_REFERENCES", "false")
        .env("AI_MODELER_REFERENCE_IMAGES", "[]")
        .env("AI_MODELER_PROVIDER_BASE_URL", &provider.base_url)
        .env("AI_MODELER_PROVIDER_MODEL", &provider.model)
        .env("AI_MODELER_REVIEW_BASE_URL", &provider.base_url)
        .env("AI_MODELER_REVIEW_MODEL", &provider.model)
        .env("AI_MODELER_REVIEW_REASONING_EFFORT", &provider.effort)
        .env(
            "AI_MODELER_REVIEW_THINKING_BUDGET",
            provider
                .budget
                .map(|value| value.to_string())
                .unwrap_or_default(),
        );
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
    configure_reasoning(
        &process,
        &session_id,
        &session_response,
        &profile,
        &provider.effort,
    )?;
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
    write_json_atomic(&metadata_path, &saved)?;
    drop(initialize_events);
    let session = Arc::new(ActiveSession {
        process,
        id: session_id,
        fingerprint: fingerprint.to_owned(),
        cwd,
        metadata_path,
        image_supported,
        active: AtomicBool::new(false),
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
    let response = loop {
        match receiver.recv_timeout(Duration::from_millis(120)) {
            Ok(message) => {
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
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        match rx.try_recv() {
            Ok(result) => break result?,
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err("DSH ACP 请求线程意外退出。".into());
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
    };
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
    }
    publish_preview(
        &session.cwd,
        app,
        request_id,
        events,
        &mut last_preview_hash,
        true,
    );
    let cancelled = response.get("stopReason").and_then(Value::as_str) == Some("cancelled");
    append_transcript(
        session,
        json!({
            "role":"assistant", "text":answer, "thought":thought,
            "stop_reason":response.get("stopReason").cloned().unwrap_or(Value::Null)
        }),
    )?;
    emit(
        events,
        "agent.turn_complete",
        request_id,
        json!({"cancelled":cancelled}),
    );
    let metadata = session
        .metadata
        .lock()
        .map_err(|_| "Agent 会话记录锁已损坏。")?;
    Ok(json!({
        "state": if cancelled { "cancelled" } else { "complete" },
        "session_id": session.id,
        "reply": answer,
        "stop_reason": response.get("stopReason").cloned().unwrap_or(Value::Null),
        "turns": metadata.get("turns").cloned().unwrap_or_else(|| json!([]))
    }))
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
) -> Result<(), String> {
    if route.reasoning.is_empty() {
        return Ok(());
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
    process
        .request(
            "session/set_config_option",
            json!({
                "sessionId":session_id,"configId":"reasoning_effort","value":value
            }),
            Some(Duration::from_secs(15)),
        )
        .map_err(|error| format!("无法设置 DSH 思考强度 `{target}`：{error}"))?;
    Ok(())
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
        let positive_json = |key: &str| {
            value
                .get(key)
                .and_then(Value::as_u64)
                .filter(|value| *value > 0)
        };
        let output_cap = positive_json("dsh_output_cap").or_else(|| positive_json("max_tokens"));
        route_profile(&base_url, &model)?;
        Ok(Self {
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
        Image, Provider, active_turn_record, find_config_value, route_profile,
        validate_image_signature, write_profile_patch,
    };
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;

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
    fn profile_patch_has_the_native_route_and_never_contains_credentials() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
        let plugins = [
            root.join("ai_modeler/agent/dsh_native_tools.mjs"),
            root.join("ai_modeler/agent/dsh_attachment_store.mjs"),
            root.join("ai_modeler/agent/dsh_stream_bridge.mjs"),
        ];
        let provider = Provider {
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
