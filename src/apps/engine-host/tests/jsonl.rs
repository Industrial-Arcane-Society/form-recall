use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("formrecall-rpc-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct Host {
    child: Child,
    input: ChildStdin,
    output: Receiver<Value>,
    pending: HashMap<u64, Value>,
    events: Vec<Value>,
    next_id: u64,
}

impl Host {
    fn start(scratch: &Scratch, fixture: bool) -> Self {
        let resources = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../legacy")
            .canonicalize()
            .unwrap();
        let mut command = Command::new(env!("CARGO_BIN_EXE_formrecall-engine-host"));
        command.args([
            "--resources",
            resources.to_str().unwrap(),
            "--data",
            scratch.0.to_str().unwrap(),
        ]);
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        command.env_remove("AIMODELER_RPC_TEST_ACP_FIXTURE");
        command.env_remove("AIMODELER_CONFIG_PATH");
        command.env_remove("AIMODELER_DATA_DIR");
        if fixture {
            command.env(
                "AIMODELER_RPC_TEST_ACP_FIXTURE",
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures/cancellable-acp.mjs"),
            );
        }
        let mut child = command.spawn().unwrap();
        let input = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (sender, output) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let line = line.unwrap();
                let value = serde_json::from_str(&line)
                    .unwrap_or_else(|_| panic!("Host wrote non-JSON stdout: {line}"));
                if sender.send(value).is_err() {
                    break;
                }
            }
        });
        Self {
            child,
            input,
            output,
            pending: HashMap::new(),
            events: Vec::new(),
            next_id: 0,
        }
    }

    fn send(&mut self, method: &str, params: Value) -> u64 {
        self.next_id += 1;
        let id = self.next_id;
        writeln!(
            self.input,
            "{}",
            json!({"id":id,"method":method,"params":params})
        )
        .unwrap();
        self.input.flush().unwrap();
        id
    }

    fn response(&mut self, id: u64) -> Value {
        if let Some(reply) = self.pending.remove(&id) {
            return reply;
        }
        loop {
            let reply = self
                .output
                .recv_timeout(Duration::from_secs(90))
                .expect("RPC response timeout");
            if reply["event"].is_string() {
                self.events.push(reply);
                continue;
            }
            let response_id = reply["id"].as_u64().expect("response id");
            if response_id == id {
                return reply;
            }
            self.pending.insert(response_id, reply);
        }
    }

    fn call(&mut self, method: &str, params: Value) -> Value {
        let id = self.send(method, params);
        let reply = self.response(id);
        assert!(reply.get("error").is_none(), "{method}: {reply}");
        reply["result"].clone()
    }

    fn close(&mut self) {
        assert_eq!(self.call("shutdown", json!({}))["closed"], true);
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                assert!(status.success());
                return;
            }
            assert!(
                Instant::now() < deadline,
                "host did not exit after shutdown"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Host {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[test]
fn protocol_errors_settings_and_restart_preserve_state() {
    let scratch = Scratch::new();
    let mut host = Host::start(&scratch, false);
    let handshake = host.call("handshake", json!({}));
    assert_eq!(handshake["protocol"], 1);
    assert_eq!(handshake["ui_independent"], true);
    assert!(host.call("active_model_snapshot", json!({})).is_null());
    writeln!(host.input, "invalid json").unwrap();
    host.input.flush().unwrap();
    let invalid = host.output.recv_timeout(Duration::from_secs(10)).unwrap();
    assert!(invalid["id"].is_null());
    assert!(invalid["error"].is_string());
    let unknown = host.send("unknown_method", json!({}));
    assert!(host.response(unknown)["error"].is_string());
    let settings = host.call("native_settings_load", json!({}));
    assert!(settings["llm"].is_object());
    let saved = host.call(
        "native_settings_save",
        json!({"settings":{"viewer":{"grid":false}}}),
    );
    assert_eq!(saved["viewer"]["grid"], false);
    assert!(
        host.call("native_provider_templates", json!({}))
            .as_array()
            .unwrap()
            .len()
            >= 3
    );
    assert!(
        !host
            .call("native_parts_list", json!({}))
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert!(
        !host
            .call("native_samples_list", json!({}))
            .as_array()
            .unwrap()
            .is_empty()
    );
    host.call("active_model_new", json!({"name":"RPC empty"}));
    let active = host.call("active_model_snapshot", json!({}));
    assert_eq!(active["document"]["name"], "RPC empty");
    assert_eq!(
        active["acceptance_projection"]["model"]["acceptance_confirmed"],
        false
    );
    host.close();
    let mut restarted = Host::start(&scratch, false);
    assert_eq!(
        restarted.call("native_settings_load", json!({}))["viewer"]["grid"],
        false
    );
    assert_eq!(
        restarted.call("active_model_snapshot", json!({}))["document"]["name"],
        "RPC empty"
    );
    restarted.close();
}

#[test]
#[ignore = "requires the verified OCCT runtime"]
fn real_occt_quick_edit_save_reopen_export_and_snapshot() {
    let scratch = Scratch::new();
    let mut host = Host::start(&scratch, false);
    let built = host.call(
        "quick_build",
        json!({"text":"创建 100×60×20 毫米长方体","revision":1}),
    );
    assert_eq!(built["state"], "complete");
    assert!(built["stats"]["triangles"].as_u64().unwrap() > 0);
    let first = host.call("active_model_snapshot", json!({}));
    assert_eq!(first["document"]["features"].as_array().unwrap().len(), 1);
    assert_eq!(
        first["acceptance_projection"]["model"]["acceptance_confirmed"],
        false
    );
    let changed = host.call(
        "active_model_update_feature",
        json!({"index":0,"params":{"length":120},"action":"update"}),
    );
    assert!((changed["stats"]["size"][0].as_f64().unwrap() - 120.0).abs() < 0.001);
    let second = host.call("active_model_snapshot", json!({}));
    assert!(second["generation"].as_u64().unwrap() > first["generation"].as_u64().unwrap());
    assert_ne!(
        second["geometry_fingerprint"],
        first["geometry_fingerprint"]
    );
    let project = scratch.0.join("project.aimodel");
    host.call("active_model_save", json!({"path":project}));
    assert!(project.is_file());
    host.close();

    let mut host = Host::start(&scratch, false);
    let restored = host.call("active_model_snapshot", json!({}));
    assert_eq!(
        restored["geometry_fingerprint"],
        second["geometry_fingerprint"]
    );
    host.call("active_model_new", json!({"name":"Fresh"}));
    let imported = host.call(
        "active_model_import_project",
        json!({"path":project,"confirmed":true}),
    );
    assert_eq!(imported["features"].as_array().unwrap().len(), 1);
    let mesh_path = imported["mesh_path"].as_str().unwrap();
    assert_eq!(&fs::read(mesh_path).unwrap()[..4], b"glTF");
    for kind in ["stl", "obj"] {
        let path = scratch.0.join(format!("export.{kind}"));
        host.call("active_model_export", json!({"kind":kind,"path":path}));
        assert!(fs::metadata(&path).unwrap().len() > 80);
    }
    let snapshot = scratch.0.join("views.png");
    host.call(
        "active_model_export_snapshot",
        json!({"path":snapshot,"viewSet":"four"}),
    );
    assert_eq!(&fs::read(snapshot).unwrap()[..8], b"\x89PNG\r\n\x1a\n");
    host.close();
}

#[cfg(feature = "test-support")]
#[test]
fn agent_events_and_cancel_are_reachable_during_send() {
    let scratch = Scratch::new();
    let mut host = Host::start(&scratch, true);
    let send = host.send("agent_send", json!({"requestId":"cancel-fixture","text":"Explain a box","images":[],
        "llmConfig":{"base_url":"https://api.deepseek.com/responses","model":"deepseek-flash","api_key":"fixture","reasoning_effort":"none"}}));
    loop {
        let event = host
            .output
            .recv_timeout(Duration::from_secs(30))
            .expect("stream did not start");
        assert_eq!(event["event"], "agent", "{event}");
        assert_eq!(event["request_id"], send);
        let streamed = event["data"]["event"] == "agent.message";
        host.events.push(event);
        if streamed {
            break;
        }
    }
    let started = Instant::now();
    let busy = host.send("active_model_new", json!({"name":"Should remain absent"}));
    assert!(
        host.response(busy)["error"]
            .as_str()
            .unwrap()
            .contains("模型正在更新")
    );
    let cancel = host.call("agent_cancel", json!({}));
    assert_eq!(cancel["cancelled"], true);
    assert!(started.elapsed() < Duration::from_secs(5));
    let result = host.response(send);
    assert!(result.get("error").is_none(), "{result}");
    assert_eq!(result["result"]["turn_status"], "cancelled");
    assert!(host.call("active_model_snapshot", json!({})).is_null());
    assert_eq!(host.call("agent_reset", json!({}))["state"], "reset");
    host.close();
}
