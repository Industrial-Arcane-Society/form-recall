//! Minimal ACP v1 stdio client used by the desktop host.
//!
//! ACP stdio is newline-delimited JSON-RPC. This module deliberately owns only
//! the transport and process lifecycle; session policy and CAD state live above
//! it, and DSH owns the Agent loop.

use serde_json::{Value, json};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

type RpcResult = Result<Value, String>;

struct Shared {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, Sender<RpcResult>>>,
    subscribers: Mutex<Vec<Sender<Value>>>,
    next_id: AtomicU64,
    closed: Mutex<bool>,
}

/// A single ACP agent process. Responses are correlated by JSON-RPC id and
/// notifications are broadcast to subscribers so session routing can happen
/// in the higher-level DSH host.
pub struct AcpProcess {
    shared: Arc<Shared>,
}

impl AcpProcess {
    /// Starts an ACP stdio server. The caller must provide an explicit
    /// executable and its arguments; no shell or PATH-based command parsing is
    /// performed by this module.
    pub fn spawn(command: &mut Command) -> Result<Self, String> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }

        let mut child = command.spawn().map_err(|error| {
            format!(
                "无法启动 DSH ACP 进程（{}）：{error}",
                command.get_program().to_string_lossy()
            )
        })?;
        let stdin = child.stdin.take().ok_or("无法连接 DSH ACP 输入管道。")?;
        let stdout = child.stdout.take().ok_or("无法读取 DSH ACP 输出管道。")?;
        let stderr = child.stderr.take().ok_or("无法读取 DSH ACP 日志管道。")?;
        let shared = Arc::new(Shared {
            child: Mutex::new(Some(child)),
            stdin: Mutex::new(Some(stdin)),
            pending: Mutex::new(HashMap::new()),
            subscribers: Mutex::new(Vec::new()),
            next_id: AtomicU64::new(1),
            closed: Mutex::new(false),
        });

        spawn_stdout_reader(stdout, Arc::clone(&shared));
        spawn_stderr_reader(stderr);
        Ok(Self { shared })
    }

    /// Subscribes to ACP notifications and agent-to-client requests.
    pub fn subscribe(&self) -> Result<Receiver<Value>, String> {
        let (sender, receiver) = mpsc::channel();
        self.shared
            .subscribers
            .lock()
            .map_err(|_| "ACP 通知订阅表已损坏。")?
            .push(sender);
        Ok(receiver)
    }

    /// Sends one JSON-RPC request and waits for its matching response.
    pub fn request(
        &self,
        method: &str,
        params: Value,
        timeout: Option<Duration>,
    ) -> Result<Value, String> {
        self.ensure_running()?;
        let id = self.shared.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = mpsc::channel();
        self.shared
            .pending
            .lock()
            .map_err(|_| "ACP 请求表已损坏。")?
            .insert(id, sender);

        let message = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        if let Err(error) = self.write_message(&message) {
            if let Ok(mut pending) = self.shared.pending.lock() {
                pending.remove(&id);
            }
            return Err(error);
        }

        match timeout {
            Some(timeout) => match receiver.recv_timeout(timeout) {
                Ok(result) => result,
                Err(error) => {
                    if let Ok(mut pending) = self.shared.pending.lock() {
                        pending.remove(&id);
                    }
                    Err(match error {
                        mpsc::RecvTimeoutError::Timeout => {
                            format!("等待 DSH ACP `{method}` 响应超时。")
                        }
                        mpsc::RecvTimeoutError::Disconnected => "DSH ACP 响应通道已关闭。".into(),
                    })
                }
            },
            None => receiver
                .recv()
                .map_err(|_| "DSH ACP 响应通道已关闭。".to_owned())?,
        }
    }

    /// Sends a JSON-RPC notification, which has no response id.
    pub fn notify(&self, method: &str, params: Value) -> Result<(), String> {
        self.ensure_running()?;
        self.write_message(&json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }))
    }

    /// Returns whether the ACP child process and its stdio channel are still open.
    pub fn is_running(&self) -> bool {
        self.ensure_running().is_ok()
    }

    /// Closes stdin to let DSH drain its ACP sessions, then waits a bounded
    /// interval before terminating a process that did not exit.
    pub fn shutdown(&self) {
        if let Ok(mut closed) = self.shared.closed.lock() {
            *closed = true;
        }
        if let Ok(mut stdin) = self.shared.stdin.lock() {
            stdin.take();
        }

        let deadline = Instant::now() + SHUTDOWN_GRACE;
        if let Ok(mut child_slot) = self.shared.child.lock() {
            if let Some(child) = child_slot.as_mut() {
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => break,
                        Ok(None) if Instant::now() < deadline => {
                            thread::sleep(Duration::from_millis(50));
                        }
                        _ => {
                            let _ = child.kill();
                            let _ = child.wait();
                            break;
                        }
                    }
                }
            }
            child_slot.take();
        }
        fail_pending(&self.shared, "DSH ACP 进程已关闭。");
    }

    fn ensure_running(&self) -> Result<(), String> {
        if self
            .shared
            .closed
            .lock()
            .map(|closed| *closed)
            .unwrap_or(true)
        {
            return Err("DSH ACP 进程已关闭。".into());
        }
        let mut child = self
            .shared
            .child
            .lock()
            .map_err(|_| "DSH ACP 进程锁已损坏。")?;
        match child.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(None) => Ok(()),
                Ok(Some(status)) => Err(format!("DSH ACP 进程已退出（{status}）。")),
                Err(error) => Err(format!("无法检查 DSH ACP 进程状态：{error}")),
            },
            None => Err("DSH ACP 进程已关闭。".into()),
        }
    }

    fn write_message(&self, message: &Value) -> Result<(), String> {
        let mut stdin = self
            .shared
            .stdin
            .lock()
            .map_err(|_| "DSH ACP 输入管道锁已损坏。")?;
        let stdin = stdin.as_mut().ok_or("DSH ACP 输入管道已关闭。")?;
        serde_json::to_writer(&mut *stdin, message)
            .map_err(|error| format!("无法序列化 ACP 消息：{error}"))?;
        stdin
            .write_all(b"\n")
            .and_then(|_| stdin.flush())
            .map_err(|error| format!("无法写入 DSH ACP 输入管道：{error}"))
    }
}

impl Drop for AcpProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn spawn_stdout_reader(stdout: impl Read + Send + 'static, shared: Arc<Shared>) {
    thread::Builder::new()
        .name("ai-modeler-dsh-acp-out".into())
        .spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut line = Vec::new();
            loop {
                line.clear();
                match reader.read_until(b'\n', &mut line) {
                    Ok(0) => break,
                    Ok(_) => {}
                    Err(error) => {
                        eprintln!("[dsh-acp] stdout 读取失败：{error}");
                        break;
                    }
                }
                while matches!(line.last(), Some(b'\n' | b'\r')) {
                    line.pop();
                }
                if line.is_empty() {
                    continue;
                }
                let message: Value = match serde_json::from_slice(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        eprintln!("[dsh-acp] 收到无效 ACP JSON-RPC 帧：{error}");
                        continue;
                    }
                };
                dispatch_message(&shared, message);
            }
            if let Ok(mut closed) = shared.closed.lock() {
                *closed = true;
            }
            fail_pending(&shared, "DSH ACP 输出管道已关闭。");
        })
        .expect("failed to create DSH ACP stdout reader");
}

fn spawn_stderr_reader(stderr: impl Read + Send + 'static) {
    thread::Builder::new()
        .name("ai-modeler-dsh-acp-log".into())
        .spawn(move || {
            for line in BufReader::new(stderr).lines().flatten() {
                eprintln!("[dsh-acp] {line}");
            }
        })
        .expect("failed to create DSH ACP stderr reader");
}

fn dispatch_message(shared: &Shared, message: Value) {
    if let Some(id) = message.get("id").and_then(Value::as_u64) {
        if message.get("method").and_then(Value::as_str).is_none() {
            let result = if let Some(error) = message.get("error") {
                Err(format!("ACP 请求失败：{}", error))
            } else if let Some(result) = message.get("result") {
                Ok(result.clone())
            } else {
                Err("ACP 响应缺少 result 或 error。".into())
            };
            if let Ok(mut pending) = shared.pending.lock() {
                if let Some(sender) = pending.remove(&id) {
                    let _ = sender.send(result);
                }
            }
            return;
        }

        // This client does not advertise filesystem, terminal, or elicitation
        // capabilities. Fail closed on an unsupported agent-to-client method.
        let response = json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": { "code": -32601, "message": "Unsupported ACP client method." },
        });
        if let Ok(mut stdin) = shared.stdin.lock() {
            if let Some(stdin) = stdin.as_mut() {
                let _ = serde_json::to_writer(&mut *stdin, &response);
                let _ = stdin.write_all(b"\n").and_then(|_| stdin.flush());
            }
        }
        return;
    }

    if let Ok(mut subscribers) = shared.subscribers.lock() {
        subscribers.retain(|sender| sender.send(message.clone()).is_ok());
    }
}

fn fail_pending(shared: &Shared, diagnostic: &str) {
    if let Ok(mut pending) = shared.pending.lock() {
        for (_, sender) in pending.drain() {
            let _ = sender.send(Err(diagnostic.to_owned()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::AcpProcess;
    use serde_json::{Value, json};
    use std::io::{BufRead, BufReader, Write};
    use std::process::Command;
    use std::time::Duration;

    const FIXTURE_ENV: &str = "AIMODELER_ACP_TEST_FIXTURE";

    #[test]
    fn fake_acp_child_process() {
        if std::env::var_os(FIXTURE_ENV).is_none() {
            return;
        }
        let stdin = std::io::stdin();
        let stdout = std::io::stdout();
        let mut input = BufReader::new(stdin.lock());
        let mut output = stdout.lock();
        let mut line = String::new();
        while input.read_line(&mut line).unwrap_or(0) > 0 {
            let message: Value = serde_json::from_str(line.trim()).expect("valid client frame");
            let method = message["method"].as_str().unwrap_or_default();
            let id = message.get("id").cloned();
            match method {
                "initialize" => write_frame(
                    &mut output,
                    json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": { "protocolVersion": 1, "agentCapabilities": {} },
                    }),
                ),
                "session/prompt" => {
                    let session_id = message["params"]["sessionId"].clone();
                    write_frame(
                        &mut output,
                        json!({
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": { "sessionUpdate": "agent_message_chunk", "content": { "type": "text", "text": "fixture" } },
                            },
                        }),
                    );
                    write_frame(
                        &mut output,
                        json!({
                            "jsonrpc": "2.0",
                            "id": id,
                            "result": { "stopReason": "end_turn" },
                        }),
                    );
                }
                "fixture/error" => write_frame(
                    &mut output,
                    json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "error": { "code": -32000, "message": "fixture error" },
                    }),
                ),
                "fixture/no-response" | "session/cancel" => {}
                "fixture/exit" => std::process::exit(0),
                _ => {}
            }
            line.clear();
        }
        // Skip the Rust test-harness summary so stdout stays protocol-pure.
        std::process::exit(0);
    }

    #[test]
    fn process_correlates_responses_and_streams_notifications() {
        let mut process = fixture_command();
        let client = AcpProcess::spawn(&mut process).expect("spawn fake ACP process");
        let updates = client.subscribe().expect("subscribe to updates");

        let initialized = client
            .request(
                "initialize",
                json!({ "protocolVersion": 1 }),
                Some(Duration::from_secs(2)),
            )
            .expect("initialize response");
        assert_eq!(initialized["protocolVersion"], 1);

        let prompted = client
            .request(
                "session/prompt",
                json!({ "sessionId": "session-1", "prompt": [{ "type": "text", "text": "hello" }] }),
                Some(Duration::from_secs(2)),
            )
            .expect("prompt response");
        assert_eq!(prompted["stopReason"], "end_turn");
        let update = updates
            .recv_timeout(Duration::from_secs(2))
            .expect("agent update");
        assert_eq!(update["params"]["sessionId"], "session-1");
        assert_eq!(update["params"]["update"]["content"]["text"], "fixture");
        client.shutdown();
    }

    #[test]
    fn process_surfaces_json_rpc_errors() {
        let mut process = fixture_command();
        let client = AcpProcess::spawn(&mut process).expect("spawn fake ACP process");
        let error = client
            .request("fixture/error", json!({}), Some(Duration::from_secs(2)))
            .expect_err("JSON-RPC error should become a Rust error");
        assert!(error.contains("fixture error"));
        client.shutdown();
    }

    #[test]
    fn request_timeout_does_not_poison_later_requests() {
        let mut process = fixture_command();
        let client = AcpProcess::spawn(&mut process).expect("spawn fake ACP process");
        let timeout = client
            .request(
                "fixture/no-response",
                json!({}),
                Some(Duration::from_millis(20)),
            )
            .expect_err("fixture request should time out");
        assert!(timeout.contains("超时"));

        let prompted = client
            .request(
                "session/prompt",
                json!({ "sessionId": "session-2", "prompt": [] }),
                Some(Duration::from_secs(2)),
            )
            .expect("later request remains correlated");
        assert_eq!(prompted["stopReason"], "end_turn");
        client
            .notify("session/cancel", json!({ "sessionId": "session-2" }))
            .expect("send cancellation notification");
        client.shutdown();
    }

    #[test]
    fn process_exit_releases_pending_requests() {
        let mut process = fixture_command();
        let client = AcpProcess::spawn(&mut process).expect("spawn fake ACP process");
        let error = client
            .request("fixture/exit", json!({}), None)
            .expect_err("child exit must release the waiting request");
        assert!(error.contains("关闭") || error.contains("退出"));
        client.shutdown();
    }

    fn fixture_command() -> Command {
        let mut command = Command::new(std::env::current_exe().expect("test executable"));
        command
            .args([
                "--exact",
                "acp::tests::fake_acp_child_process",
                "--nocapture",
            ])
            .env(FIXTURE_ENV, "1");
        command
    }

    fn write_frame(output: &mut impl Write, message: Value) {
        serde_json::to_writer(&mut *output, &message).expect("serialize frame");
        output.write_all(b"\n").expect("write line delimiter");
        output.flush().expect("flush frame");
    }
}
