//! Isolated fixture backend for the F7 real-WebView CLI acceptance harness.
//!
//! This module is only compiled by the dedicated `ai-modeler-webview-smoke`
//! binary with `--features agent-webview-smoke`; the production binary keeps
//! its normal AgentHost and command handlers.

use serde_json::{Value, json};
use tauri::Manager;
use tauri::ipc::Channel;

const SCENARIO_PREFIX: &str = "__f7_webview_smoke__:";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Scenario {
    PureQa,
    CadToolFailure,
    AcceptedCurrent,
    StaleFinish,
}

impl Scenario {
    fn parse(text: &str) -> Result<Self, String> {
        match text.trim() {
            "__f7_webview_smoke__:pure-qa" => Ok(Self::PureQa),
            "__f7_webview_smoke__:cad-tool-failure" => Ok(Self::CadToolFailure),
            "__f7_webview_smoke__:accepted-current" => Ok(Self::AcceptedCurrent),
            "__f7_webview_smoke__:stale-finish" => Ok(Self::StaleFinish),
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

fn fixture_document(revision: i64, finish_revision: i64) -> Value {
    let fingerprint = format!("f7-webview-fixture-{revision}");
    json!({
        "revision": revision,
        "cad_revision": revision,
        "geometry_fingerprint": fingerprint,
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
            let document = fixture_document(17, 17);
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
            let document = fixture_document(18, 18);
            events.push(wire_event("model.changed", request_id, document.clone()));
            ("completed", Value::Null, model_snapshot(&document, true))
        }
        Scenario::StaleFinish => {
            let document = fixture_document(19, 18);
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
    request_id: String,
    text: String,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let scenario = Scenario::parse(&text)?;
    let turn = fixture_turn(scenario, &request_id);
    for event in turn.events {
        on_event
            .send(event)
            .map_err(|error| format!("无法发送隔离 WebView fixture 事件：{error}"))?;
    }
    Ok(turn.result)
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
            agent_send
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
    use super::{Scenario, fixture_document, fixture_turn};
    use serde_json::Value;

    #[test]
    fn fixture_prompt_parser_rejects_non_fixture_requests() {
        assert_eq!(
            Scenario::parse("__f7_webview_smoke__:pure-qa").unwrap(),
            Scenario::PureQa
        );
        assert!(Scenario::parse("build a screw").is_err());
        assert!(Scenario::parse("__f7_webview_smoke__:arbitrary").is_err());
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
        let document = fixture_document(23, 23);
        assert_eq!(document["revision"], document["cad_revision"]);
        assert_eq!(
            document["geometry_fingerprint"],
            document["finish_summary"]["geometry_fingerprint"]
        );
        assert_eq!(document["features"].as_array().unwrap().len(), 1);
    }
}
