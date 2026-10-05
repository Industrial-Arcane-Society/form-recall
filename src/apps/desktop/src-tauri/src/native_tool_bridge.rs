//! Local JSON-RPC bridge between the DSH Cordis plugin and the Rust desktop host.
//!
//! Cordis remains the DSH-native tool registration surface. This loopback
//! transport carries tool requests to Rust without ACP tool translation, MCP,
//! or the Python tool worker.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 32 * 1024 * 1024;
const MAX_CONNECTIONS: usize = 32;
const MAX_CACHED_RESULTS: usize = 128;
const DEFAULT_TOOL_TIMEOUT: Duration = Duration::from_secs(210);
const MAX_TOOL_TIMEOUT: Duration = Duration::from_secs(240);

pub trait ToolExecutor: Send + Sync + 'static {
    fn execute(
        &self,
        tool_call_id: &str,
        name: &str,
        arguments: &Value,
        timeout: Duration,
    ) -> Result<Value, String>;

    fn cancel(&self, tool_call_id: &str) -> bool;

    fn finding_review_state(&self) -> Result<Value, String> {
        Err("当前原生工具执行器不支持 finding 人工确认。".into())
    }

    fn adjudicate_finding(&self, _request: &Value) -> Result<Value, String> {
        Err("当前原生工具执行器不支持 finding 人工确认。".into())
    }

    fn shutdown(&self) {}
}

struct Shared {
    address: SocketAddr,
    token: String,
    session_id: String,
    tools: Vec<Value>,
    tool_names: HashSet<String>,
    executor: Arc<dyn ToolExecutor>,
    stop: AtomicBool,
    calls: Mutex<CallState>,
    connection_count: AtomicUsize,
}

#[derive(Default)]
struct CallState {
    active: HashMap<String, String>,
    completed: HashMap<String, (String, Value)>,
    completed_order: VecDeque<String>,
}

/// One per-session, authenticated loopback endpoint owned by Rust.
pub struct NativeToolBridge {
    shared: Arc<Shared>,
    accept_thread: Mutex<Option<JoinHandle<()>>>,
    schemas_path: PathBuf,
}

impl NativeToolBridge {
    pub fn start(
        session_id: impl Into<String>,
        schemas_path: impl AsRef<Path>,
        tools: Vec<Value>,
        executor: Arc<dyn ToolExecutor>,
    ) -> Result<Self, String> {
        let mut tool_names = HashSet::new();
        for tool in &tools {
            let name = tool
                .get("name")
                .and_then(Value::as_str)
                .filter(|name| !name.trim().is_empty())
                .ok_or("Rust Host 收到没有名称的 Cordis 工具 schema。")?;
            if !tool_names.insert(name.to_owned()) {
                return Err(format!("Cordis 工具 schema 重复：{name}"));
            }
        }

        let schemas_path = schemas_path.as_ref().to_path_buf();
        if let Some(parent) = schemas_path.parent() {
            fs::create_dir_all(parent).map_err(|error| {
                format!(
                    "无法创建 Cordis schema 清单目录（{}）：{error}",
                    parent.display()
                )
            })?;
        }
        let manifest = json!({"schemaVersion": 1, "tools": tools});
        fs::write(
            &schemas_path,
            serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?,
        )
        .map_err(|error| format!("无法写入 Cordis schema 清单：{error}"))?;

        let listener = TcpListener::bind(("127.0.0.1", 0))
            .map_err(|error| format!("无法启动 Rust Cordis 本机端点：{error}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("无法配置 Rust Cordis 本机端点：{error}"))?;
        let address = listener
            .local_addr()
            .map_err(|error| format!("无法读取 Rust Cordis 本机端点：{error}"))?;
        let token = random_token()?;
        let shared = Arc::new(Shared {
            address,
            token,
            session_id: session_id.into(),
            tools: manifest["tools"].as_array().cloned().unwrap_or_default(),
            tool_names,
            executor,
            stop: AtomicBool::new(false),
            calls: Mutex::new(CallState::default()),
            connection_count: AtomicUsize::new(0),
        });
        let server_shared = Arc::clone(&shared);
        let accept_thread = thread::Builder::new()
            .name("ai-modeler-cordis-rpc".into())
            .spawn(move || accept_loop(listener, server_shared))
            .map_err(|error| format!("无法启动 Rust Cordis RPC 服务：{error}"))?;

        Ok(Self {
            shared,
            accept_thread: Mutex::new(Some(accept_thread)),
            schemas_path,
        })
    }

    pub fn address(&self) -> String {
        self.shared.address.to_string()
    }

    pub fn token(&self) -> &str {
        &self.shared.token
    }

    pub fn session_id(&self) -> &str {
        &self.shared.session_id
    }

    pub fn schemas_path(&self) -> &Path {
        &self.schemas_path
    }

    pub fn finding_review_state(&self) -> Result<Value, String> {
        self.shared.executor.finding_review_state()
    }

    pub fn adjudicate_finding(&self, request: &Value) -> Result<Value, String> {
        self.shared.executor.adjudicate_finding(request)
    }

    pub fn shutdown(&self) {
        if !self.shared.stop.swap(true, Ordering::AcqRel) {
            self.shared.executor.shutdown();
            let _ = TcpStream::connect_timeout(&self.shared.address, Duration::from_millis(100));
        }
        if let Ok(mut handle) = self.accept_thread.lock() {
            if let Some(handle) = handle.take() {
                let _ = handle.join();
            }
        }
    }
}

impl Drop for NativeToolBridge {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn accept_loop(listener: TcpListener, shared: Arc<Shared>) {
    while !shared.stop.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, _)) => {
                let count = shared.connection_count.fetch_add(1, Ordering::AcqRel) + 1;
                if count > MAX_CONNECTIONS {
                    shared.connection_count.fetch_sub(1, Ordering::AcqRel);
                    drop(stream);
                    continue;
                }
                let client_shared = Arc::clone(&shared);
                if thread::Builder::new()
                    .name("ai-modeler-cordis-rpc-client".into())
                    .spawn(move || {
                        handle_client(stream, client_shared);
                    })
                    .is_err()
                {
                    shared.connection_count.fetch_sub(1, Ordering::AcqRel);
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(10));
            }
            Err(_) => thread::sleep(Duration::from_millis(25)),
        }
    }
}

fn handle_client(mut stream: TcpStream, shared: Arc<Shared>) {
    let _connection = ConnectionCountGuard(Arc::clone(&shared));
    let _ = stream.set_read_timeout(Some(Duration::from_secs(10)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(10)));
    let request = match read_json_line(&stream) {
        Ok(value) => value,
        Err(error) => {
            let _ = write_response(
                &mut stream,
                json!({"jsonrpc":"2.0","id":Value::Null,"error":{"code":-32700,"message":error}}),
            );
            return;
        }
    };
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    if request.get("jsonrpc").and_then(Value::as_str) != Some("2.0") || !id.is_string() {
        let _ = write_error(
            &mut stream,
            id,
            -32600,
            "无效的 JSON-RPC 请求。",
            "INVALID_REQUEST",
        );
        return;
    }
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
    if !params.is_object() {
        let _ = write_error(
            &mut stream,
            id,
            -32602,
            "params 必须是 JSON 对象。",
            "INVALID_PARAMS",
        );
        return;
    }
    if !authorized(&shared, &params) {
        let _ = write_error(
            &mut stream,
            id,
            -32001,
            "本机工具桥鉴权失败。",
            "UNAUTHORIZED",
        );
        return;
    }
    let result = match method {
        "bridge/ping" => Ok(json!({"sessionId":shared.session_id,"protocolVersion":1})),
        "tools/list" => Ok(json!({"schemaVersion":1,"tools":shared.tools})),
        "tools/call" => call_tool(&shared, &params),
        "tools/cancel" => cancel_tool(&shared, &params),
        _ => {
            let _ = write_error(
                &mut stream,
                id,
                -32601,
                "不支持的 Rust 工具桥方法。",
                "METHOD_NOT_FOUND",
            );
            return;
        }
    };
    match result {
        Ok(value) => {
            let _ = write_response(&mut stream, json!({"jsonrpc":"2.0","id":id,"result":value}));
        }
        Err(error) => {
            let _ = write_dispatch_error(&mut stream, id, -32020, &error);
        }
    }
}

struct BridgeError {
    code: String,
    message: String,
    dispatch_stage: &'static str,
}

impl BridgeError {
    fn new(code: &str, message: impl Into<String>) -> Self {
        let dispatch_stage = match code {
            "INVALID_PARAMS" | "UNKNOWN_TOOL" | "CALL_ID_REUSED" | "CALL_IN_PROGRESS" => {
                "pre_executor_validation"
            }
            "DEADLINE_EXCEEDED" => "pre_executor_deadline",
            "TOOL_FAILED" => "executor_return",
            "RESPONSE_TOO_LARGE" => "response_writeback",
            _ => "unknown",
        };
        Self {
            code: code.to_owned(),
            message: message.into(),
            dispatch_stage,
        }
    }
}

fn call_tool(shared: &Shared, params: &Value) -> Result<Value, BridgeError> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| BridgeError::new("INVALID_PARAMS", "缺少工具名称。"))?;
    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));
    if !arguments.is_object() {
        return Err(BridgeError::new(
            "INVALID_PARAMS",
            "工具参数必须为 JSON 对象。",
        ));
    }
    if !shared.tool_names.contains(name) {
        return Err(BridgeError::new(
            "UNKNOWN_TOOL",
            format!("未注册 Cordis 工具：{name}"),
        ));
    }
    let tool_call_id = params
        .get("toolCallId")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty() && value.len() <= 128)
        .ok_or_else(|| BridgeError::new("INVALID_PARAMS", "toolCallId 缺失或无效。"))?;
    let fingerprint = call_fingerprint(name, &arguments)
        .map_err(|error| BridgeError::new("INVALID_PARAMS", error))?;
    {
        let mut calls = shared
            .calls
            .lock()
            .map_err(|_| BridgeError::new("INTERNAL", "工具桥调用表已损坏。"))?;
        if let Some((existing_fingerprint, result)) = calls.completed.get(tool_call_id) {
            if existing_fingerprint == &fingerprint {
                return Ok(result.clone());
            }
            return Err(BridgeError::new(
                "CALL_ID_REUSED",
                "toolCallId 已用于不同的工具参数。",
            ));
        }
        if let Some(existing_fingerprint) = calls.active.get(tool_call_id) {
            let code = if existing_fingerprint == &fingerprint {
                "CALL_IN_PROGRESS"
            } else {
                "CALL_ID_REUSED"
            };
            return Err(BridgeError::new(code, "toolCallId 已在使用中。"));
        }
        calls
            .active
            .insert(tool_call_id.to_owned(), fingerprint.clone());
    }
    let mut active_call = ActiveCallGuard {
        shared,
        tool_call_id,
        fingerprint: fingerprint.clone(),
        completed: false,
    };
    let timeout = match params.get("deadlineMs").and_then(Value::as_u64) {
        Some(deadline_ms) => {
            let Some(deadline) = UNIX_EPOCH.checked_add(Duration::from_millis(deadline_ms)) else {
                return Err(BridgeError::new(
                    "INVALID_PARAMS",
                    "deadlineMs 超出了系统时间范围。",
                ));
            };
            let remaining = deadline
                .duration_since(SystemTime::now())
                .unwrap_or_default();
            if remaining.is_zero() {
                return Err(BridgeError::new(
                    "DEADLINE_EXCEEDED",
                    "工具请求已超过截止时间。",
                ));
            }
            remaining.min(MAX_TOOL_TIMEOUT)
        }
        None => DEFAULT_TOOL_TIMEOUT,
    };
    let execution = shared
        .executor
        .execute(tool_call_id, name, &arguments, timeout);
    let result = execution.map_err(|message| BridgeError::new("TOOL_FAILED", message))?;
    active_call.complete(result.clone())?;
    Ok(result)
}

struct ActiveCallGuard<'a> {
    shared: &'a Shared,
    tool_call_id: &'a str,
    fingerprint: String,
    completed: bool,
}

impl ActiveCallGuard<'_> {
    fn complete(&mut self, result: Value) -> Result<(), BridgeError> {
        let mut calls = self
            .shared
            .calls
            .lock()
            .map_err(|_| BridgeError::new("INTERNAL", "工具桥调用表已损坏。"))?;
        calls.active.remove(self.tool_call_id);
        calls.completed.insert(
            self.tool_call_id.to_owned(),
            (self.fingerprint.clone(), result),
        );
        calls
            .completed_order
            .push_back(self.tool_call_id.to_owned());
        while calls.completed_order.len() > MAX_CACHED_RESULTS {
            if let Some(oldest) = calls.completed_order.pop_front() {
                calls.completed.remove(&oldest);
            }
        }
        self.completed = true;
        Ok(())
    }
}

impl Drop for ActiveCallGuard<'_> {
    fn drop(&mut self) {
        if self.completed {
            return;
        }
        if let Ok(mut calls) = self.shared.calls.lock() {
            if calls.active.get(self.tool_call_id) == Some(&self.fingerprint) {
                calls.active.remove(self.tool_call_id);
            }
        }
    }
}

fn cancel_tool(shared: &Shared, params: &Value) -> Result<Value, BridgeError> {
    let tool_call_id = params
        .get("toolCallId")
        .and_then(Value::as_str)
        .ok_or_else(|| BridgeError::new("INVALID_PARAMS", "缺少 toolCallId。"))?;
    let active = shared
        .calls
        .lock()
        .map(|calls| calls.active.contains_key(tool_call_id))
        .unwrap_or(false);
    let cancelled = active && shared.executor.cancel(tool_call_id);
    Ok(json!({"cancelled":cancelled,"toolCallId":tool_call_id}))
}

fn authorized(shared: &Shared, params: &Value) -> bool {
    if params.get("sessionId").and_then(Value::as_str) != Some(shared.session_id.as_str()) {
        return false;
    }
    let Some(candidate) = params.get("token").and_then(Value::as_str) else {
        return false;
    };
    constant_time_equal(candidate.as_bytes(), shared.token.as_bytes())
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn call_fingerprint(name: &str, arguments: &Value) -> Result<String, String> {
    let encoded = serde_json::to_vec(&json!({"name":name,"arguments":arguments}))
        .map_err(|error| error.to_string())?;
    Ok(hex(&Sha256::digest(encoded)))
}

fn read_json_line(stream: &TcpStream) -> Result<Value, String> {
    let limited = stream
        .try_clone()
        .map_err(|error| error.to_string())?
        .take((MAX_REQUEST_BYTES + 1) as u64);
    let mut reader = BufReader::new(limited);
    let mut line = Vec::new();
    reader
        .read_until(b'\n', &mut line)
        .map_err(|error| error.to_string())?;
    if line.len() > MAX_REQUEST_BYTES {
        return Err("JSON-RPC 请求超过大小上限。".into());
    }
    if line.last() != Some(&b'\n') {
        return Err("JSON-RPC 请求没有完整换行帧。".into());
    }
    let value = serde_json::from_slice::<Value>(&line)
        .map_err(|error| format!("JSON-RPC 请求格式错误：{error}"))?;
    if !value.is_object() {
        return Err("JSON-RPC 请求必须是 JSON 对象。".into());
    }
    Ok(value)
}

fn write_response(stream: &mut TcpStream, response: Value) -> io::Result<()> {
    let bytes = serde_json::to_vec(&response)?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return write_error(
            stream,
            response.get("id").cloned().unwrap_or(Value::Null),
            -32030,
            "工具桥响应超过大小上限。",
            "RESPONSE_TOO_LARGE",
        );
    }
    stream.write_all(&bytes)?;
    stream.write_all(b"\n")?;
    stream.flush()
}

fn write_error(
    stream: &mut TcpStream,
    id: Value,
    code: i64,
    message: &str,
    detail_code: &str,
) -> io::Result<()> {
    let mut response = json!({
        "jsonrpc":"2.0", "id":id,
        "error":{"code":code,"message":message,"data":{"code":detail_code,
            "dispatch_stage": if detail_code == "RESPONSE_TOO_LARGE" { "response_writeback" } else { "unknown" }}}
    });
    let mut bytes = serde_json::to_vec(&response)?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        response["error"]["message"] = json!("工具桥响应错误。");
        bytes = serde_json::to_vec(&response)?;
    }
    stream.write_all(&bytes)?;
    stream.write_all(b"\n")?;
    stream.flush()
}

fn write_dispatch_error(
    stream: &mut TcpStream,
    id: Value,
    code: i64,
    error: &BridgeError,
) -> io::Result<()> {
    let response = json!({
        "jsonrpc":"2.0", "id":id,
        "error":{"code":code,"message":error.message,"data":{
            "code":error.code,
            "dispatch_stage":error.dispatch_stage,
        }}
    });
    let bytes = serde_json::to_vec(&response)?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return write_error(stream, id, -32030, "工具桥响应错误。", "RESPONSE_TOO_LARGE");
    }
    stream.write_all(&bytes)?;
    stream.write_all(b"\n")?;
    stream.flush()
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| format!("无法生成本机工具桥访问令牌：{error}"))?;
    Ok(hex(&bytes))
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

struct ConnectionCountGuard(Arc<Shared>);

impl Drop for ConnectionCountGuard {
    fn drop(&mut self) {
        self.0.connection_count.fetch_sub(1, Ordering::AcqRel);
    }
}

#[cfg(test)]
mod tests {
    use super::{NativeToolBridge, ToolExecutor};
    use serde_json::{json, Value};
    use std::collections::HashSet;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpStream;
    use std::path::PathBuf;
    use std::sync::{Arc, Condvar, Mutex};
    use std::thread;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    #[derive(Default)]
    struct EchoExecutor {
        calls: Mutex<Vec<(String, String, Value)>>,
    }

    impl ToolExecutor for EchoExecutor {
        fn execute(
            &self,
            tool_call_id: &str,
            name: &str,
            arguments: &Value,
            _timeout: Duration,
        ) -> Result<Value, String> {
            self.calls.lock().unwrap().push((
                tool_call_id.to_owned(),
                name.to_owned(),
                arguments.clone(),
            ));
            Ok(json!({"text":"ok","payload":{"name":name,"arguments":arguments}}))
        }

        fn cancel(&self, _tool_call_id: &str) -> bool {
            false
        }
    }

    #[derive(Default)]
    struct BlockingExecutor {
        active: Mutex<HashSet<String>>,
        cancelled: Mutex<HashSet<String>>,
        notify: Condvar,
    }

    impl ToolExecutor for BlockingExecutor {
        fn execute(
            &self,
            tool_call_id: &str,
            _name: &str,
            _arguments: &Value,
            timeout: Duration,
        ) -> Result<Value, String> {
            self.active.lock().unwrap().insert(tool_call_id.to_owned());
            self.notify.notify_all();
            let deadline = std::time::Instant::now() + timeout;
            loop {
                if self.cancelled.lock().unwrap().contains(tool_call_id) {
                    self.active.lock().unwrap().remove(tool_call_id);
                    return Err("cancelled".into());
                }
                if std::time::Instant::now() >= deadline {
                    self.active.lock().unwrap().remove(tool_call_id);
                    return Err("deadline".into());
                }
                thread::sleep(Duration::from_millis(5));
            }
        }

        fn cancel(&self, tool_call_id: &str) -> bool {
            let active = self.active.lock().unwrap().contains(tool_call_id);
            if active {
                self.cancelled
                    .lock()
                    .unwrap()
                    .insert(tool_call_id.to_owned());
                self.notify.notify_all();
            }
            active
        }
    }

    fn temp_schema_path() -> PathBuf {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        std::env::temp_dir()
            .join(format!(
                "ai-modeler-tool-bridge-test-{}-{id}",
                std::process::id()
            ))
            .join("schemas.json")
    }

    fn tools() -> Vec<Value> {
        vec![json!({
            "name":"cad_get_state",
            "description":"Read current model state",
            "inputSchema":{"type":"object","properties":{},"additionalProperties":false}
        })]
    }

    fn rpc(bridge: &NativeToolBridge, id: &str, method: &str, mut params: Value) -> Value {
        params["token"] = json!(bridge.token());
        params["sessionId"] = json!(bridge.session_id());
        let mut stream = TcpStream::connect(bridge.address()).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        write!(
            stream,
            "{}\n",
            json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
        )
        .unwrap();
        let mut response = String::new();
        BufReader::new(stream).read_line(&mut response).unwrap();
        serde_json::from_str(&response).unwrap()
    }

    #[test]
    fn rpc_lists_tools_authenticates_and_deduplicates_identical_calls() {
        let schemas_path = temp_schema_path();
        let executor = Arc::new(EchoExecutor::default());
        let bridge =
            NativeToolBridge::start("session-test", &schemas_path, tools(), executor.clone())
                .unwrap();

        let listed = rpc(&bridge, "list", "tools/list", json!({}));
        assert_eq!(listed["result"]["tools"][0]["name"], "cad_get_state");

        let params = json!({
            "toolCallId":"call-1",
            "name":"cad_get_state",
            "arguments":{"includeMeasurements":true}
        });
        let first = rpc(&bridge, "call-1", "tools/call", params.clone());
        let retry = rpc(&bridge, "call-2", "tools/call", params);
        assert_eq!(first["result"]["text"], "ok");
        assert_eq!(retry["result"], first["result"]);
        assert_eq!(executor.calls.lock().unwrap().len(), 1);

        let reused = rpc(
            &bridge,
            "call-3",
            "tools/call",
            json!({"toolCallId":"call-1","name":"cad_get_state","arguments":{"includeMeasurements":false}}),
        );
        assert_eq!(reused["error"]["data"]["code"], "CALL_ID_REUSED");
        assert_eq!(
            reused["error"]["data"]["dispatch_stage"],
            "pre_executor_validation"
        );

        let bad_tool = rpc(
            &bridge,
            "call-4",
            "tools/call",
            json!({"toolCallId":"call-2","name":"not_registered","arguments":{}}),
        );
        assert_eq!(bad_tool["error"]["data"]["code"], "UNKNOWN_TOOL");
        assert_eq!(
            bad_tool["error"]["data"]["dispatch_stage"],
            "pre_executor_validation"
        );

        bridge.shutdown();
        let _ = std::fs::remove_dir_all(schemas_path.parent().unwrap());
    }

    #[test]
    fn rpc_rejects_wrong_session_token_and_expired_deadline() {
        let schemas_path = temp_schema_path();
        let executor = Arc::new(EchoExecutor::default());
        let bridge =
            NativeToolBridge::start("session-test", &schemas_path, tools(), executor.clone())
                .unwrap();

        let mut bad_auth = json!({"token":"wrong","sessionId":"session-test"});
        let unauthorized = raw_rpc(&bridge, "auth", "tools/list", &mut bad_auth);
        assert_eq!(unauthorized["error"]["data"]["code"], "UNAUTHORIZED");

        let wrong_session =
            rpc_with_session(&bridge, "session-other", "session", "tools/list", json!({}));
        assert_eq!(wrong_session["error"]["data"]["code"], "UNAUTHORIZED");

        let expired = rpc(
            &bridge,
            "expired",
            "tools/call",
            json!({
                "toolCallId":"expired-call",
                "name":"cad_get_state",
                "arguments":{},
                "deadlineMs":1
            }),
        );
        assert_eq!(expired["error"]["data"]["code"], "DEADLINE_EXCEEDED");
        assert_eq!(
            expired["error"]["data"]["dispatch_stage"],
            "pre_executor_deadline"
        );
        let future_deadline = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64
            + 5_000;
        let retried = rpc(
            &bridge,
            "retry-expired",
            "tools/call",
            json!({
                "toolCallId":"expired-call",
                "name":"cad_get_state",
                "arguments":{},
                "deadlineMs":future_deadline
            }),
        );
        assert_eq!(retried["result"]["text"], "ok");
        assert_eq!(executor.calls.lock().unwrap().len(), 1);

        bridge.shutdown();
        let _ = std::fs::remove_dir_all(schemas_path.parent().unwrap());
    }

    #[test]
    fn rpc_cancel_routes_by_tool_call_id() {
        let schemas_path = temp_schema_path();
        let executor = Arc::new(BlockingExecutor::default());
        let bridge = Arc::new(
            NativeToolBridge::start("session-test", &schemas_path, tools(), executor.clone())
                .unwrap(),
        );
        let call_bridge = Arc::clone(&bridge);
        let call = thread::spawn(move || {
            rpc(
                &call_bridge,
                "long-call",
                "tools/call",
                json!({
                    "toolCallId":"cancel-me",
                    "name":"cad_get_state",
                    "arguments":{},
                    "deadlineMs":(SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() + 20_000) as u64
                }),
            )
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        while !executor.active.lock().unwrap().contains("cancel-me") {
            assert!(
                std::time::Instant::now() < deadline,
                "tool call did not reach executor"
            );
            thread::sleep(Duration::from_millis(5));
        }
        let duplicate = rpc(
            &bridge,
            "duplicate-call",
            "tools/call",
            json!({
                "toolCallId":"cancel-me",
                "name":"cad_get_state",
                "arguments":{},
                "deadlineMs":(SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() + 20_000) as u64
            }),
        );
        assert_eq!(duplicate["error"]["data"]["code"], "CALL_IN_PROGRESS");
        let cancelled = rpc(
            &bridge,
            "cancel",
            "tools/cancel",
            json!({"toolCallId":"cancel-me"}),
        );
        assert_eq!(cancelled["result"]["cancelled"], true);
        let tool_result = call.join().unwrap();
        assert_eq!(tool_result["error"]["data"]["code"], "TOOL_FAILED");
        assert_eq!(
            tool_result["error"]["data"]["dispatch_stage"],
            "executor_return"
        );

        bridge.shutdown();
        let _ = std::fs::remove_dir_all(schemas_path.parent().unwrap());
    }

    fn raw_rpc(bridge: &NativeToolBridge, id: &str, method: &str, params: &mut Value) -> Value {
        let mut stream = TcpStream::connect(bridge.address()).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        write!(
            stream,
            "{}\n",
            json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
        )
        .unwrap();
        let mut response = String::new();
        BufReader::new(stream).read_line(&mut response).unwrap();
        serde_json::from_str(&response).unwrap()
    }

    fn rpc_with_session(
        bridge: &NativeToolBridge,
        session: &str,
        id: &str,
        method: &str,
        mut params: Value,
    ) -> Value {
        params["token"] = json!(bridge.token());
        params["sessionId"] = json!(session);
        raw_rpc(bridge, id, method, &mut params)
    }
}
