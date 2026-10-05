//! Isolated fixture backend for the F7 real-WebView CLI acceptance harness.
//!
//! This module is only compiled by the dedicated `ai-modeler-webview-smoke`
//! binary with `--features agent-webview-smoke`; the production binary keeps
//! its normal AgentHost and command handlers.

use serde_json::{Value, json};
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
    AgentWorkspacePreview,
}

impl Scenario {
    fn parse(text: &str) -> Result<Self, String> {
        match text.trim() {
            "__f7_webview_smoke__:pure-qa" => Ok(Self::PureQa),
            "__f7_webview_smoke__:cad-tool-failure" => Ok(Self::CadToolFailure),
            "__f7_webview_smoke__:accepted-current" => Ok(Self::AcceptedCurrent),
            "__f7_webview_smoke__:stale-finish" => Ok(Self::StaleFinish),
            "__f7_webview_smoke__:agent-workspace-preview-path" => Ok(Self::AgentWorkspacePreview),
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
            Self::AgentWorkspacePreview => "agent-workspace-preview-path",
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

fn fixture_turn(scenario: Scenario, request_id: &str) -> FixtureTurn {
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
        Scenario::AgentWorkspacePreview => {
            unreachable!("production OCCT generation uses production_quick_turn")
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

fn production_quick_turn(request_id: &str, mut document: Value, reply: &str) -> FixtureTurn {
    let revision = document["revision"].as_i64().unwrap_or(1);
    document["cad_revision"] = json!(revision);
    let fingerprint = document["geometry_report"]["geometry_fingerprint"]
        .as_str()
        .unwrap_or("production-quick-occt")
        .to_owned();
    document["geometry_fingerprint"] = json!(fingerprint);
    let snapshot = json!({
        "exists":true,"status":"candidate","acceptance_confirmed":false,
        "revision":revision,"geometry_fingerprint":fingerprint,
        "finish_status":"not_finished","finish_summary":Value::Null,
        "geometric":{"status":"passed"},"visual":{"status":"not_reviewed"}
    });
    FixtureTurn {
        events: vec![
            wire_event("agent.message", request_id, json!({"text":reply})),
            wire_event("model.changed", request_id, document),
            wire_event(
                "agent.turn_complete",
                request_id,
                json!({
                    "turn_status":"completed","stop_reason":Value::Null,"current_model":snapshot
                }),
            ),
        ],
        result: json!({"reply":reply,"turn_status":"completed","stop_reason":Value::Null,"current_model":snapshot}),
    }
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
    let turn = if scenario == Scenario::AgentWorkspacePreview {
        let resources = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
        let data_dir = app
            .path()
            .app_local_data_dir()
            .map_err(|error| format!("无法解析隔离 WebView app-data：{error}"))?;
        let mut document = super::headless_quick_smoke(&resources, &data_dir)?;
        if document["geometry_report"]["state"] != "passed"
            || document["geometry_report"]["execution_engine"] != "rust_c_abi_occt"
            || document["mesh_path"].as_str().is_none()
        {
            return Err("生产 Quick 构建没有返回完整的 OCCT 几何报告与 GLB 路径。".into());
        }
        let source = PathBuf::from(
            document["mesh_path"]
                .as_str()
                .ok_or("生产几何结果没有 GLB 路径。")?,
        );
        let target = agent_workspace_fixture_path(&app)?;
        let parent = target
            .parent()
            .ok_or("Agent workspace fixture 路径缺少父目录。")?;
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        std::fs::copy(&source, &target).map_err(|error| error.to_string())?;
        document["mesh_path"] = json!(target.to_string_lossy());
        let reply = "WebView Agent workspace GLB path completed";
        production_quick_turn(&request_id, document, reply)
    } else {
        fixture_turn(scenario, &request_id)
    };
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
    let response = super::read_asset(app.clone(), path.clone())?;
    if let Ok(fixture_path) = agent_workspace_fixture_path(&app) {
        if PathBuf::from(&path) == fixture_path {
            // The nested Agent-path scenario only needs this copied GLB once.
            // Remove the test artifact after the production reader has served it.
            let _ = std::fs::remove_file(fixture_path);
        }
    }
    Ok(response)
}

fn agent_workspace_fixture_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("无法解析隔离 WebView app-data：{error}"))?
        .join("agent-host")
        .join("workspaces")
        .join("session-f7-webview-smoke")
        .join("native-output")
        .join("generation-f7-webview-smoke")
        .join("candidate.glb"))
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
            read_asset
        ])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                window.hide()?;
            }
            Ok(())
        })
        .run(tauri::generate_context!("tauri.webview-smoke.conf.json"))
        .expect("F7 WebView smoke application failed");
}

#[cfg(test)]
mod tests {
    use super::{Scenario, fixture_document, fixture_turn, production_quick_turn};
    use serde_json::{Value, json};

    #[test]
    fn fixture_prompt_parser_rejects_non_fixture_requests() {
        assert_eq!(
            Scenario::parse("__f7_webview_smoke__:pure-qa").unwrap(),
            Scenario::PureQa
        );
        assert!(Scenario::parse("build a screw").is_err());
        assert!(Scenario::parse("__f7_webview_smoke__:arbitrary").is_err());
        assert_eq!(
            Scenario::parse("__f7_webview_smoke__:agent-workspace-preview-path").unwrap(),
            Scenario::AgentWorkspacePreview
        );
    }

    #[test]
    fn pure_qa_fixture_does_not_publish_a_model_change() {
        let turn = fixture_turn(Scenario::PureQa, "request-pure-qa");
        assert!(
            turn.events
                .iter()
                .all(|event| event["event"] != "model.changed")
        );
        assert_eq!(turn.result["turn_status"], "completed");
        assert_eq!(turn.result["current_model"]["exists"], false);
    }

    #[test]
    fn production_quick_turn_publishes_built_document_and_mesh_path() {
        let mesh_path = r"C:\Users\test\AppData\Local\com.aimodeler.f7-webview-smoke\native-quick\quick\r1\mesh.glb";
        let document = json!({"revision":1,"mesh_path":mesh_path,"geometry_report":{"state":"passed","execution_engine":"rust_c_abi_occt"},"features":[{"name":"Cuboid"}]});
        let turn = production_quick_turn(
            "request-production",
            document,
            "WebView production Quick model completed",
        );
        let model_change = turn
            .events
            .iter()
            .find(|event| event["event"] == "model.changed")
            .expect("publish a model change");
        assert_eq!(model_change["data"]["revision"], 1);
        assert_eq!(model_change["data"]["mesh_path"], mesh_path);
        assert_eq!(
            model_change["data"]["geometry_report"]["execution_engine"],
            "rust_c_abi_occt"
        );
        assert_eq!(turn.result["current_model"]["status"], "candidate");
    }

    #[test]
    fn production_quick_smoke_builds_and_commits_occt_glb_when_runtime_is_configured() {
        if std::env::var_os("AIMODELER_TEST_OCCT_MANIFEST").is_none() {
            eprintln!("skipped production OCCT integration: AIMODELER_TEST_OCCT_MANIFEST is unset");
            return;
        }
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let data_dir = std::env::temp_dir().join(format!(
            "f7-webview-production-quick-{}-{stamp}",
            std::process::id()
        ));
        struct RemoveDir(std::path::PathBuf);
        impl Drop for RemoveDir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _cleanup = RemoveDir(data_dir.clone());
        let resources =
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
        let result = crate::headless_quick_smoke(&resources, &data_dir).unwrap();
        assert_eq!(result["geometry_report"]["state"], "passed");
        assert_eq!(
            result["geometry_report"]["execution_engine"],
            "rust_c_abi_occt"
        );
        assert_eq!(result["geometry_report"]["closed_solid_count"], 1);
        assert_eq!(
            result["geometry_report"]["dimensions_mm"],
            json!([100.0, 60.0, 20.0])
        );
        assert_eq!(result["features"].as_array().unwrap().len(), 1);
        let mesh_path = std::path::PathBuf::from(result["mesh_path"].as_str().unwrap());
        let mesh = std::fs::read(&mesh_path).unwrap();
        assert_eq!(&mesh[..4], b"glTF");
        assert!(mesh.len() > 100);
        let active_root = crate::native_active_model::root_from_data_dir(&data_dir);
        let active = crate::native_active_model::load(&active_root)
            .unwrap()
            .unwrap();
        assert_eq!(active["document"]["mesh_path"], result["mesh_path"]);
        assert_eq!(active["generation"], 1);
    }

    #[test]
    fn failed_cad_fixture_remains_a_candidate() {
        let turn = fixture_turn(Scenario::CadToolFailure, "request-failure");
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
        let accepted = fixture_turn(Scenario::AcceptedCurrent, "request-accepted");
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

        let stale = fixture_turn(Scenario::StaleFinish, "request-stale");
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
