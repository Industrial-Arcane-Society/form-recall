//! Isolated fixture backend for the F7 real-WebView CLI acceptance harness.
//!
//! This module is only compiled by the dedicated `ai-modeler-webview-smoke`
//! binary with `--features agent-webview-smoke`; the production binary keeps
//! its normal AgentHost and command handlers.

use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;
use tauri::ipc::{Channel, Response};
use tauri::{AppHandle, Manager};

const SCENARIO_PREFIX: &str = "__f7_webview_smoke__:";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Scenario {
    PureQa,
    CadToolFailure,
    AcceptedCurrent,
    StaleFinish,
    GlbPreview,
}

impl Scenario {
    fn parse(text: &str) -> Result<Self, String> {
        match text.trim() {
            "__f7_webview_smoke__:pure-qa" => Ok(Self::PureQa),
            "__f7_webview_smoke__:cad-tool-failure" => Ok(Self::CadToolFailure),
            "__f7_webview_smoke__:accepted-current" => Ok(Self::AcceptedCurrent),
            "__f7_webview_smoke__:stale-finish" => Ok(Self::StaleFinish),
            "__f7_webview_smoke__:glb-preview" => Ok(Self::GlbPreview),
            _ => Err(format!(
                "仅接受隔离 F7 fixture prompt，前缀为 {SCENARIO_PREFIX}"
            )),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::PureQa => "pure-qa",
            Self::CadToolFailure => "cad-tool-failure",
            Self::AcceptedCurrent => "accepted-current",
            Self::StaleFinish => "stale-finish",
            Self::GlbPreview => "glb-preview",
        }
    }
}

struct FixtureTurn {
    events: Vec<Value>,
    result: Value,
}

fn wire_event(name: &str, request_id: &str, data: Value) -> Value {
    json!({"event":name,"request_id":request_id,"data":data})
}

fn fixture_document(revision: i64, finish_revision: i64, mesh_path: Option<String>) -> Value {
    let fingerprint = format!("f7-webview-fixture-{revision}");
    json!({
        "revision": revision,
        "cad_revision": revision,
        "geometry_fingerprint": fingerprint,
        "mesh_path": mesh_path,
        "name": format!("F7 WebView fixture r{revision}"),
        "source_prompt": "Isolated WebView transport fixture",
        "features": [{
            "index": 0,
            "kind": "box",
            "name": "Fixture base",
            "enabled": true,
            "params": {"length":80,"width":40,"height":10}
        }],
        "stats": {"volume":32000.0,"faces":6,"triangles":12},
        "build_log": ["F7 WebView transport fixture"],
        "geometric_status": "passed",
        "visual_status": "covered",
        "finish_summary": {
            "accepted": true,
            "finish_state": "accepted",
            "revision": finish_revision,
            "geometry_fingerprint": format!("f7-webview-fixture-{finish_revision}")
        }
    })
}

fn model_snapshot(document: &Value, accepted: bool) -> Value {
    if accepted {
        json!({
            "exists": true,
            "status": "accepted",
            "acceptance_confirmed": true,
            "revision": document["cad_revision"],
            "geometry_fingerprint": document["geometry_fingerprint"],
            "finish_status": "accepted",
            "finish_summary": document["finish_summary"],
            "geometric": {"status":"passed"},
            "visual": {"status":"covered"}
        })
    } else {
        json!({
            "exists": true,
            "status": "candidate",
            "acceptance_confirmed": false,
            "revision": document["cad_revision"],
            "geometry_fingerprint": document["geometry_fingerprint"],
            "finish_status": "not_finished",
            "finish_summary": Value::Null,
            "geometric": {"status":"passed"},
            "visual": {"status":"covered"}
        })
    }
}

fn fixture_turn(scenario: Scenario, request_id: &str, mesh_path: Option<String>) -> FixtureTurn {
    let reply = format!("WebView fixture completed: {}", scenario.name());
    let mut events = vec![wire_event(
        "agent.message",
        request_id,
        json!({"text":reply}),
    )];

    let (turn_status, stop_reason, current_model) = match scenario {
        Scenario::PureQa => (
            "completed",
            Value::Null,
            json!({
                "exists":false,
                "status":"none",
                "finish_status":"not_finished",
                "finish_summary":Value::Null,
                "geometric":{"status":"not_built"},
                "visual":{"status":"not_reviewed"}
            }),
        ),
        Scenario::CadToolFailure => {
            let document = fixture_document(17, 17, None);
            events.push(wire_event("model.changed", request_id, document.clone()));
            events.push(wire_event(
                "agent.tool",
                request_id,
                json!({
                    "tool_call_id":"f7-fixture-tool",
                    "title":"cad_preview_model",
                    "status":"failed",
                    "error":"deterministic WebView fixture failure"
                }),
            ));
            (
                "stopped",
                json!("tool_error"),
                model_snapshot(&document, false),
            )
        }
        Scenario::AcceptedCurrent => {
            let document = fixture_document(18, 18, None);
            events.push(wire_event("model.changed", request_id, document.clone()));
            ("completed", Value::Null, model_snapshot(&document, true))
        }
        Scenario::StaleFinish => {
            let document = fixture_document(19, 18, None);
            events.push(wire_event("model.changed", request_id, document.clone()));
            ("completed", Value::Null, model_snapshot(&document, false))
        }
        Scenario::GlbPreview => {
            let document = fixture_document(20, 20, mesh_path);
            events.push(wire_event("model.changed", request_id, document.clone()));
            ("completed", Value::Null, model_snapshot(&document, false))
        }
    };

    events.push(wire_event(
        "agent.turn_complete",
        request_id,
        json!({
            "turn_status":turn_status,
            "stop_reason":stop_reason,
            "current_model":current_model
        }),
    ));

    FixtureTurn {
        events,
        result: json!({
            "reply":reply,
            "turn_status":turn_status,
            "stop_reason":stop_reason,
            "current_model":current_model
        }),
    }
}

const FIXTURE_GLB_RELATIVE_PATH: &str = "native-quick/f7-webview-smoke.glb";

fn fixture_glb_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|root| root.join(FIXTURE_GLB_RELATIVE_PATH))
        .map_err(|error| format!("无法解析 WebView fixture 数据目录：{error}"))
}

fn fixture_glb_bytes() -> Vec<u8> {
    let document = json!({
        "asset":{"version":"2.0"},
        "scene":0,
        "scenes":[{"nodes":[0]}],
        "nodes":[{"mesh":0}],
        "meshes":[{"primitives":[{"attributes":{"POSITION":0},"indices":1}]}],
        "buffers":[{"byteLength":44}],
        "bufferViews":[
            {"buffer":0,"byteOffset":0,"byteLength":36,"target":34962},
            {"buffer":0,"byteOffset":36,"byteLength":6,"target":34963}
        ],
        "accessors":[
            {"bufferView":0,"componentType":5126,"count":3,"type":"VEC3","min":[-40,-40,0],"max":[40,40,0]},
            {"bufferView":1,"componentType":5123,"count":3,"type":"SCALAR"}
        ]
    });
    let mut json_chunk = serde_json::to_vec(&document).expect("serialize fixture glTF");
    while json_chunk.len() % 4 != 0 {
        json_chunk.push(b' ');
    }

    let mut binary_chunk = Vec::with_capacity(44);
    for value in [-40.0_f32, -40.0, 0.0, 40.0, -40.0, 0.0, 0.0, 40.0, 0.0] {
        binary_chunk.extend_from_slice(&value.to_le_bytes());
    }
    for index in [0_u16, 1, 2] {
        binary_chunk.extend_from_slice(&index.to_le_bytes());
    }
    binary_chunk.resize(44, 0);

    let total_length = 12 + 8 + json_chunk.len() + 8 + binary_chunk.len();
    let mut glb = Vec::with_capacity(total_length);
    glb.extend_from_slice(b"glTF");
    glb.extend_from_slice(&2_u32.to_le_bytes());
    glb.extend_from_slice(&(total_length as u32).to_le_bytes());
    glb.extend_from_slice(&(json_chunk.len() as u32).to_le_bytes());
    glb.extend_from_slice(&0x4E4F_534A_u32.to_le_bytes());
    glb.extend_from_slice(&json_chunk);
    glb.extend_from_slice(&(binary_chunk.len() as u32).to_le_bytes());
    glb.extend_from_slice(&0x004E_4942_u32.to_le_bytes());
    glb.extend_from_slice(&binary_chunk);
    glb
}

fn write_fixture_glb(app: &AppHandle) -> Result<String, String> {
    let path = fixture_glb_path(app)?;
    let parent = path.parent().ok_or("WebView fixture GLB 路径无父目录。")?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建 GLB fixture 目录：{error}"))?;
    fs::write(&path, fixture_glb_bytes())
        .map_err(|error| format!("无法写入 GLB fixture：{error}"))?;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
fn native_resource_status() -> Value {
    json!({"available":true,"verified":true,"root":"isolated-f7-webview-fixture"})
}

#[tauri::command]
fn native_runtime_status() -> Value {
    json!({
        "appVersion":"f7-webview-smoke",
        "dsh":{"available":true,"installed":true,"verified":true,"state":"fixture"},
        "occt":{"available":false,"verified":false,"loaded":false,"state":"fixture-not-used"},
        "catia":{"available":false,"state":"fixture-not-used"},
        "cordis":{"available":false},
        "externalMcp":{"available":false}
    })
}

#[tauri::command]
fn native_about() -> Value {
    json!({
        "app_version":"f7-webview-smoke",
        "description":"Isolated real-WebView transport acceptance fixture"
    })
}

#[tauri::command]
fn native_settings_load() -> Value {
    json!({
        "llm":{"providers":[],"active_provider_id":""},
        "online":{"enabled":false},
        "viewer":{"edges":true,"grid":true,"axes":true,"perspective":true}
    })
}

#[tauri::command]
fn active_model_snapshot() -> Value {
    Value::Null
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_new(name: String) -> Value {
    json!({
        "revision":0,
        "cad_revision":0,
        "name":name,
        "source_prompt":"",
        "features":[],
        "stats":{},
        "build_log":[],
        "finish_summary":Value::Null,
        "geometric_status":"not_built",
        "visual_status":"not_reviewed"
    })
}

#[tauri::command]
fn agent_history() -> Value {
    json!({"turns":[]})
}

#[tauri::command(rename_all = "camelCase")]
async fn agent_send(
    app: AppHandle,
    request_id: String,
    text: String,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let scenario = Scenario::parse(&text)?;
    let mesh_path = if scenario == Scenario::GlbPreview {
        Some(write_fixture_glb(&app)?)
    } else {
        None
    };
    let turn = fixture_turn(scenario, &request_id, mesh_path);
    for event in turn.events {
        on_event
            .send(event)
            .map_err(|error| format!("无法发送隔离 WebView fixture 事件：{error}"))?;
    }
    Ok(turn.result)
}

/// The WebView harness uses the exact production path policy and IPC payload.
#[tauri::command]
fn read_asset(app: AppHandle, path: String) -> Result<Response, String> {
    super::read_asset(app, path)
}

#[tauri::command]
fn cleanup_fixture_glb(app: AppHandle) -> Result<(), String> {
    let path = fixture_glb_path(&app)?;
    match fs::remove_file(&path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("无法删除 GLB fixture：{error}")),
    }
    if let Some(parent) = path.parent() {
        let _ = fs::remove_dir(parent);
    }
    Ok(())
}

/// Starts the production frontend in the separate, hidden test application.
/// `tauri-driver` launches this binary; this entrypoint is never used by the
/// normal `ai-modeler` binary.
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            native_resource_status,
            native_runtime_status,
            native_about,
            native_settings_load,
            active_model_snapshot,
            active_model_new,
            agent_history,
            agent_send,
            read_asset,
            cleanup_fixture_glb
        ])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                window.hide()?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                let _ = cleanup_fixture_glb(window.app_handle().clone());
            }
        })
        .run(tauri::generate_context!("tauri.webview-smoke.conf.json"))
        .expect("F7 WebView smoke application failed");
}

#[cfg(test)]
mod tests {
    use super::{Scenario, fixture_document, fixture_glb_bytes, fixture_turn};
    use serde_json::Value;

    #[test]
    fn fixture_prompt_parser_rejects_non_fixture_requests() {
        assert_eq!(
            Scenario::parse("__f7_webview_smoke__:pure-qa").unwrap(),
            Scenario::PureQa
        );
        assert!(Scenario::parse("build a screw").is_err());
        assert!(Scenario::parse("__f7_webview_smoke__:arbitrary").is_err());
        assert_eq!(
            Scenario::parse("__f7_webview_smoke__:glb-preview").unwrap(),
            Scenario::GlbPreview
        );
    }

    #[test]
    fn glb_preview_fixture_has_consistent_glb_header_and_mesh_payload() {
        let bytes = fixture_glb_bytes();
        assert_eq!(&bytes[..4], b"glTF");
        assert_eq!(u32::from_le_bytes(bytes[4..8].try_into().unwrap()), 2);
        assert_eq!(
            u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize,
            bytes.len()
        );
        let json_length = u32::from_le_bytes(bytes[12..16].try_into().unwrap()) as usize;
        assert_eq!(
            u32::from_le_bytes(bytes[16..20].try_into().unwrap()),
            0x4E4F_534A
        );
        let document: Value = serde_json::from_slice(&bytes[20..20 + json_length]).unwrap();
        assert_eq!(
            document["meshes"][0]["primitives"][0]["attributes"]["POSITION"],
            0
        );
        assert_eq!(document["meshes"][0]["primitives"][0]["indices"], 1);
        assert_eq!(document["accessors"][0]["count"], 3);
        assert_eq!(document["accessors"][1]["count"], 3);
    }

    #[test]
    fn pure_qa_fixture_does_not_publish_a_model_change() {
        let turn = fixture_turn(Scenario::PureQa, "request-pure-qa", None);
        assert!(
            turn.events
                .iter()
                .all(|event| event["event"] != "model.changed")
        );
        assert_eq!(turn.result["turn_status"], "completed");
        assert_eq!(turn.result["current_model"]["exists"], false);
    }

    #[test]
    fn glb_preview_fixture_attaches_mesh_path_to_current_model() {
        let mesh_path = r"C:\Users\test\AppData\Local\com.aimodeler.f7-webview-smoke\native-quick\f7-webview-smoke.glb";
        let turn = fixture_turn(
            Scenario::GlbPreview,
            "request-glb-preview",
            Some(mesh_path.to_owned()),
        );
        let model_change = turn
            .events
            .iter()
            .find(|event| event["event"] == "model.changed")
            .expect("publish a model change");
        assert_eq!(model_change["data"]["revision"], 20);
        assert_eq!(model_change["data"]["mesh_path"], mesh_path);
        assert_eq!(turn.result["current_model"]["status"], "candidate");
    }

    #[test]
    fn failed_cad_fixture_remains_a_candidate() {
        let turn = fixture_turn(Scenario::CadToolFailure, "request-failure", None);
        assert!(turn.events.iter().any(|event| {
            event["event"] == "agent.tool"
                && event["data"]["status"] == "failed"
                && event["data"]["title"] == "cad_preview_model"
        }));
        assert_eq!(turn.result["turn_status"], "stopped");
        assert_eq!(turn.result["current_model"]["status"], "candidate");
    }

    #[test]
    fn only_current_revision_and_geometry_fixture_is_accepted() {
        let accepted = fixture_turn(Scenario::AcceptedCurrent, "request-accepted", None);
        assert_eq!(accepted.result["current_model"]["status"], "accepted");
        assert_eq!(
            accepted.result["current_model"]["acceptance_confirmed"],
            true
        );
        assert_eq!(
            accepted.result["current_model"]["finish_summary"]["revision"],
            accepted.result["current_model"]["revision"]
        );
        assert_eq!(
            accepted.result["current_model"]["finish_summary"]["geometry_fingerprint"],
            accepted.result["current_model"]["geometry_fingerprint"]
        );

        let stale = fixture_turn(Scenario::StaleFinish, "request-stale", None);
        let document = stale
            .events
            .iter()
            .find(|event| event["event"] == "model.changed")
            .expect("stale scenario has a model update");
        assert_eq!(document["data"]["finish_summary"]["accepted"], true);
        assert_ne!(
            document["data"]["finish_summary"]["revision"],
            document["data"]["cad_revision"]
        );
        assert_eq!(stale.result["current_model"]["status"], "candidate");
        assert_eq!(stale.result["current_model"]["finish_summary"], Value::Null);
    }

    #[test]
    fn model_fixture_document_is_self_consistent() {
        let document = fixture_document(23, 23, None);
        assert_eq!(document["revision"], document["cad_revision"]);
        assert_eq!(
            document["geometry_fingerprint"],
            document["finish_summary"]["geometry_fingerprint"]
        );
        assert_eq!(document["features"].as_array().unwrap().len(), 1);
    }
}
