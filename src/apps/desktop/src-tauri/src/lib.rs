//! Tauri compatibility adapter. All domain state and commands are owned by cad-engine.
use formrecall_cad_engine as engine;
pub use formrecall_cad_engine::{
    acp, agent_host, agent_model_config, cad_plan, cad_plan_validation, context, dsh_runtime,
    headless_quick_smoke, headless_quick_smoke_with_text, native_active_model, native_bevel_gear,
    native_bootstrap, native_bore_metrology, native_cad_metadata, native_cad_session,
    native_cad_tools, native_catia_adapter, native_catia_runtime, native_diagnostics,
    native_fastener_specs, native_feature_specs, native_finish_evidence, native_geometry_backend,
    native_geometry_evidence, native_helix, native_impeller, native_knowledge, native_loft,
    native_model_io, native_occt, native_occt_runtime, native_online_lookup,
    native_parametric_features, native_parts_catalog, native_plan_geometry, native_plate_learning,
    native_proportion_gate, native_quick, native_quick_egt_sw, native_quick_parser,
    native_quick_socket_bolt, native_render, native_review_findings, native_review_gate,
    native_reviewed_cases, native_rotor, native_scroll_disc, native_settings, native_shell,
    native_standard_features, native_state_summary, native_timing_pulley, native_tool_bridge,
    native_tool_executor, native_tool_schemas, native_vbelt_pulley, native_vision_assets,
    native_vision_domain, native_vision_provider, native_web, native_worm, rpc, run_agent_send,
};
use serde_json::Value;
use std::path::PathBuf;
use tauri::ipc::{Channel, Response};

use tauri::{AppHandle, Manager, State};
#[cfg(feature = "agent-webview-smoke")]
pub mod webview_smoke;
fn context(app: &AppHandle) -> Result<engine::EngineContext, String> {
    let explicit = std::env::var_os("AIMODELER_RESOURCE_ROOT")
        .or_else(|| std::env::var_os("AIMODELER_ROOT"))
        .map(PathBuf::from);
    let bundle_root = app.path().resource_dir().map_err(|e| e.to_string())?;
    let packaged_root = bundle_root.join("app");
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
    let resource_root = engine::native_bootstrap::resolve_resource_root(
        explicit.as_deref(),
        Some(&packaged_root),
        Some(&development),
    )?;
    let data_dir = std::env::var_os("AIMODELER_DESKTOP_DATA_DIR")
        .map(PathBuf::from)
        .map(Ok)
        .unwrap_or_else(|| app.path().app_local_data_dir().map_err(|e| e.to_string()))?;
    if !data_dir.is_absolute() {
        return Err("桌面用户数据目录覆盖必须是绝对路径。".into());
    }
    let config_path = std::env::var_os("AIMODELER_CONFIG_PATH")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("AIMODELER_DATA_DIR").map(|p| PathBuf::from(p).join("config.json"))
        })
        .unwrap_or_else(|| {
            if cfg!(debug_assertions) {
                development.join("config.json")
            } else {
                data_dir.join("config.json")
            }
        });
    let packaged = explicit.is_none() && packaged_root.is_dir();
    Ok(engine::EngineContext {
        resource_root,
        data_dir,
        config_path,
        bundle_root,
        packaged,
    })
}
fn events(channel: Channel<Value>) -> agent_host::EventSink {
    agent_host::EventSink::new(move |value| {
        let _ = channel.send(value);
    })
}
pub fn active_model_snapshot_value(app: &AppHandle) -> Result<Value, String> {
    engine::active_model_snapshot_value(&context(app)?)
}
#[tauri::command]
fn native_resource_status(app: AppHandle) -> Result<Value, String> {
    engine::native_resource_status(context(&app)?)
}

#[tauri::command]
fn native_runtime_status(app: AppHandle) -> Result<Value, String> {
    engine::native_runtime_status(context(&app)?)
}

#[tauri::command]
fn native_about() -> Value {
    engine::native_about()
}

#[tauri::command]
fn native_selftest(app: AppHandle) -> Result<Value, String> {
    engine::native_selftest(context(&app)?)
}

#[tauri::command(rename_all = "camelCase")]
fn native_open_help_path(app: AppHandle, relative_path: String) -> Result<Value, String> {
    engine::native_open_help_path(context(&app)?, relative_path)
}

#[tauri::command(rename_all = "camelCase")]
async fn install_dsh_runtime(app: AppHandle, on_event: Channel<Value>) -> Result<Value, String> {
    engine::install_dsh_runtime(context(&app)?, events(on_event)).await
}

#[tauri::command]
async fn quick_build(app: AppHandle, text: String, revision: i64) -> Result<Value, String> {
    engine::quick_build(context(&app)?, text, revision).await
}

#[tauri::command]
fn active_model_snapshot(app: AppHandle) -> Result<Value, String> {
    engine::active_model_snapshot(context(&app)?)
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_new(app: AppHandle, name: String) -> Result<Value, String> {
    engine::active_model_new(context(&app)?, name)
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_save(app: AppHandle, path: String) -> Result<Value, String> {
    engine::active_model_save(context(&app)?, path)
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_push_catia(app: AppHandle) -> Result<Value, String> {
    engine::active_model_push_catia(context(&app)?)
}

#[tauri::command(rename_all = "camelCase")]
fn active_model_export(app: AppHandle, kind: String, path: String) -> Result<(), String> {
    engine::active_model_export(context(&app)?, kind, path)
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_export_snapshot(
    app: AppHandle,
    path: String,
    view_set: String,
) -> Result<(), String> {
    engine::active_model_export_snapshot(context(&app)?, path, view_set).await
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_import_project(
    app: AppHandle,
    path: String,
    confirmed: bool,
) -> Result<Value, String> {
    engine::active_model_import_project(context(&app)?, path, confirmed).await
}

#[tauri::command(rename_all = "camelCase")]
async fn active_model_update_feature(
    app: AppHandle,
    index: usize,
    params: Value,
    name: Option<String>,
    action: Option<String>,
    delta: Option<i32>,
    enabled: Option<bool>,
) -> Result<Value, String> {
    engine::active_model_update_feature(context(&app)?, index, params, name, action, delta, enabled)
        .await
}

#[tauri::command(rename_all = "camelCase")]
async fn agent_send(
    state: State<'_, agent_host::AgentHostState>,
    app: AppHandle,
    request_id: String,
    text: String,
    images: Vec<String>,
    llm_config: Value,
    online_enabled: bool,
    use_reviewed_cases: Option<bool>,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    engine::agent_send(
        state.inner(),
        context(&app)?,
        request_id,
        text,
        images,
        llm_config,
        online_enabled,
        use_reviewed_cases,
        events(on_event),
    )
    .await
}

#[tauri::command]
fn reviewed_cases_list(app: AppHandle) -> Result<Vec<Value>, String> {
    engine::reviewed_cases_list(context(&app)?)
}

#[tauri::command]
fn reviewed_case_save(app: AppHandle, generation: u64) -> Result<Vec<Value>, String> {
    engine::reviewed_case_save(context(&app)?, generation)
}

#[tauri::command]
fn reviewed_case_remove(app: AppHandle, id: String) -> Result<Vec<Value>, String> {
    engine::reviewed_case_remove(context(&app)?, id)
}

#[tauri::command]
async fn plate_layout_learn(
    app: AppHandle,
    group: String,
    generation: u64,
) -> Result<Value, String> {
    engine::plate_layout_learn(context(&app)?, group, generation).await
}

#[tauri::command]
async fn plate_layout_build(
    app: AppHandle,
    group: String,
    length: f64,
    width: f64,
    thickness: f64,
    diameter: f64,
) -> Result<Value, String> {
    engine::plate_layout_build(context(&app)?, group, length, width, thickness, diameter).await
}

#[tauri::command]
fn agent_finding_review_state(
    state: State<'_, agent_host::AgentHostState>,
) -> Result<Value, String> {
    engine::agent_finding_review_state(state.inner())
}

#[tauri::command]
fn agent_adjudicate_finding(
    state: State<'_, agent_host::AgentHostState>,
    request: Value,
) -> Result<Value, String> {
    engine::agent_adjudicate_finding(state.inner(), request)
}

#[tauri::command(rename_all = "camelCase")]
async fn native_online_ask(query: String) -> Result<Value, String> {
    engine::native_online_ask(query).await
}

#[tauri::command]
fn agent_cancel(state: State<'_, agent_host::AgentHostState>) -> Result<Value, String> {
    engine::agent_cancel(state.inner())
}

#[tauri::command]
async fn agent_reset(state: State<'_, agent_host::AgentHostState>) -> Result<Value, String> {
    engine::agent_reset(state.inner()).await
}

#[tauri::command]
fn agent_history(app: AppHandle) -> Result<Value, String> {
    engine::agent_history(context(&app)?)
}

#[tauri::command]
fn read_asset(app: AppHandle, path: String) -> Result<Response, String> {
    engine::read_asset(context(&app)?, path).map(Response::new)
}

#[tauri::command]
fn save_input_image(name: String, data: Vec<u8>) -> Result<String, String> {
    engine::save_input_image(name, data)
}

#[tauri::command(rename_all = "camelCase")]
fn stage_input_image(source_path: String) -> Result<String, String> {
    engine::stage_input_image(source_path)
}

#[tauri::command]
fn native_settings_load(app: AppHandle) -> Result<Value, String> {
    engine::native_settings::native_settings_load(context(&app)?)
}
#[tauri::command]
fn native_settings_save(app: AppHandle, settings: Value) -> Result<Value, String> {
    engine::native_settings::native_settings_save(context(&app)?, settings)
}
#[tauri::command]
fn native_provider_templates() -> Vec<Value> {
    engine::native_settings::provider_templates()
}
#[tauri::command]
fn native_provider_template(id: String) -> Value {
    engine::native_settings::provider_template(&id)
}
#[tauri::command(rename_all = "camelCase")]
fn native_provider_fetch_models(base_url: String, api_key: String) -> Result<Value, String> {
    engine::native_settings::native_provider_fetch_models(base_url, api_key)
}
#[tauri::command]
fn native_parts_list(query: Option<String>) -> Vec<Value> {
    engine::native_parts_catalog::list_parts(query.as_deref())
}
#[tauri::command]
fn native_samples_list() -> Vec<Value> {
    engine::native_parts_catalog::list_samples()
}
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(agent_host::AgentHostState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            native_resource_status,
            native_runtime_status,
            native_about,
            native_selftest,
            native_open_help_path,
            install_dsh_runtime,
            quick_build,
            active_model_snapshot,
            active_model_new,
            active_model_save,
            active_model_export,
            active_model_push_catia,
            active_model_update_feature,
            active_model_import_project,
            active_model_export_snapshot,
            native_parts_list,
            native_samples_list,
            native_online_ask,
            native_settings_load,
            native_settings_save,
            native_provider_templates,
            native_provider_template,
            native_provider_fetch_models,
            agent_send,
            reviewed_cases_list,
            reviewed_case_save,
            reviewed_case_remove,
            plate_layout_learn,
            plate_layout_build,
            agent_cancel,
            agent_reset,
            agent_history,
            agent_finding_review_state,
            agent_adjudicate_finding,
            read_asset,
            save_input_image,
            stage_input_image
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                agent_host::shutdown(&window.state::<agent_host::AgentHostState>());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AI Modeler desktop application");
}

#[cfg(test)]
mod agent_ipc_integration_tests {
    use super::*;
    use crate::agent_host::TestSetup;
    use serde_json::{Value, json};
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::{Arc, Mutex};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[derive(Clone)]
    struct FixtureSetup(TestSetup);

    // Tauri's production AppHandle alias is Wry-backed and cannot be supplied
    // by MockRuntime. Keep this command as a thin argument adapter: it calls
    // the same threaded runner and AgentHost core, substituting only fake ACP
    // runtime paths and app-data for the test fixture.
    #[tauri::command(rename = "agent_send", rename_all = "camelCase")]
    async fn agent_send_fixture(
        state: State<'_, agent_host::AgentHostState>,
        setup: State<'_, FixtureSetup>,
        request_id: String,
        text: String,
        images: Vec<String>,
        llm_config: Value,
        online_enabled: bool,
        on_event: Channel<Value>,
    ) -> Result<Value, String> {
        let host = state.inner().clone();
        let setup = setup.0.clone();
        run_agent_send(move || {
            agent_host::send_turn_with_fixture(
                &host,
                setup,
                request_id,
                text,
                images,
                llm_config,
                online_enabled,
                true,
                events(on_event),
            )
        })
        .await
    }

    struct TestRoot(PathBuf);

    impl TestRoot {
        fn new() -> Self {
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "aimodeler-agent-ipc-test-{}-{stamp}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("create isolated test root");
            Self(path)
        }
    }

    impl Drop for TestRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn agent_send_routes_stale_finish_as_candidate_through_tauri_channel() {
        let scenario = std::env::var("AIMODELER_TEST_AGENT_SCENARIO")
            .unwrap_or_else(|_| "stale-finish".to_owned());
        assert!(
            [
                "stale-finish",
                "pure-qa",
                "cad-tool-failure",
                "accepted-current"
            ]
            .contains(&scenario.as_str()),
            "unknown Channel/Vue acceptance scenario: {scenario}"
        );
        let scratch = TestRoot::new();
        let app_data = scratch.0.join("app-data");
        fs::create_dir_all(&app_data).expect("create isolated app data directory");

        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../legacy")
            .canonicalize()
            .expect("resolve checkout legacy resources");
        let fake_runtime_root = scratch.0.join("fake-runtime");
        let fake_cli = fake_runtime_root.join("dsh/lib/bin.js");
        let fake_node_modules = fake_runtime_root.join("dsh/node_modules");
        fs::create_dir_all(fake_cli.parent().unwrap()).expect("create fake CLI parent");
        fs::create_dir_all(&fake_node_modules).expect("create fake DSH modules directory");
        fs::write(
            &fake_cli,
            "fixture ACP process is supplied by the Rust test harness\n",
        )
        .expect("create fake CLI marker");
        let runtime = crate::dsh_runtime::RuntimePaths {
            root: fake_runtime_root.clone(),
            node: PathBuf::from(
                std::env::var_os("AIMODELER_TEST_NODE")
                    .expect("CLI acceptance provides the Node executable"),
            ),
            dsh_cli: fake_cli,
            home: scratch.0.join("dsh-home"),
        };
        let fixture_script = std::env::var_os("AIMODELER_TEST_ACP_FIXTURE")
            .expect("CLI acceptance provides the standalone ACP fixture");
        let release_file = scratch.0.join("release-fake-acp.prompt");
        let host_state = agent_host::AgentHostState::default();

        let plan = cad_plan::create(
            "IPC fixture model",
            json!({"id":"fixture-body","kind":"box","operation":"add","parameters":{"length":20,"width":10,"height":5},"sources":{
                "length":{"type":"user","evidence":"20 mm"},"width":{"type":"user","evidence":"10 mm"},"height":{"type":"user","evidence":"5 mm"}}}),
            "fixture prompt",
            None,
        );
        let plan_fingerprint = crate::cad_plan::geometry_fingerprint(&plan)
            .expect("calculate fixture plan fingerprint");
        let finish_revision = if scenario == "accepted-current" { 1 } else { 0 };
        let mut cad_state = json!({"plan":plan,"revision":1,"actions":[],"output_paths":{},
            "finish_summary":{"accepted":true,"finish_state":"accepted","revision":finish_revision,
                "geometry_fingerprint":plan_fingerprint}});
        let evidence_dir = scratch.0.join("finish-evidence");
        fs::create_dir_all(&evidence_dir).expect("create fixture finish evidence directory");
        let references_path = evidence_dir.join("reference-images.json");
        let turns_path = evidence_dir.join("user-turns.json");
        let reference_manifest = json!({"schema_version":1,"images":[]});
        let user_turns_manifest = json!({"schema_version":1,"session_id":"ipc-fixture","turns":[]});
        fs::write(
            &references_path,
            serde_json::to_vec(&reference_manifest).unwrap(),
        )
        .expect("write fixture reference manifest");
        fs::write(
            &turns_path,
            serde_json::to_vec(&user_turns_manifest).unwrap(),
        )
        .expect("write fixture user turns");
        let evidence = crate::native_finish_evidence::current_snapshot(
            &cad_state,
            &references_path,
            &turns_path,
        )
        .expect("calculate current host evidence binding for finish fixture");
        cad_state["finish_summary"]["evidence_fingerprint"] = json!(evidence.fingerprint);
        let candidate = native_active_model::candidate_from_cad_state(
            "agent",
            &cad_state,
            &reference_manifest,
            &user_turns_manifest,
        )
        .expect("make isolated candidate model");
        let active_model_root = native_active_model::root_from_data_dir(&app_data);
        let captured_events = Arc::new(Mutex::new(Vec::<Value>::new()));
        let sink_events = Arc::clone(&captured_events);
        let sink_candidate = candidate;
        let sink_cad_state = cad_state;
        let sink_active_model_root = active_model_root;
        let sink_app_data = app_data.clone();
        let sink_release_file = release_file.clone();

        let setup = FixtureSetup(TestSetup {
            root,
            data_dir: app_data.clone(),
            runtime,
            dsh_args: vec![fixture_script],
            dsh_env: vec![
                (
                    "AIMODELER_ACP_TEST_RELEASE_FILE".into(),
                    release_file.clone().into_os_string(),
                ),
                (
                    "AIMODELER_TEST_AGENT_SCENARIO".into(),
                    scenario.clone().into(),
                ),
            ],
        });

        let app = tauri::test::mock_builder()
            .manage(host_state.clone())
            .manage(setup)
            .channel_interceptor(move |_webview, _callback, index, body| {
                let value = match body {
                    tauri::ipc::InvokeResponseBody::Json(value) => {
                        serde_json::from_str::<Value>(value).expect("decode AgentHost event JSON")
                    }
                    tauri::ipc::InvokeResponseBody::Raw(_) => {
                        panic!("AgentHost events are sent as JSON")
                    }
                };
                if value["event"] == "agent.message"
                    && value["request_id"] == "headless-agent-ipc"
                    && !sink_release_file.exists()
                {
                    let scenario = std::env::var("AIMODELER_TEST_AGENT_SCENARIO")
                        .unwrap_or_else(|_| "stale-finish".to_owned());
                    if scenario != "pure-qa" {
                        let committed = native_active_model::commit(
                            &sink_active_model_root,
                            0,
                            &sink_candidate,
                        )
                        .expect("publish fixture CAD transaction");
                        let mut committed_cad_state = sink_cad_state.clone();
                        committed_cad_state["active_model_generation"] =
                            committed["generation"].clone();
                        let session_meta: Value = serde_json::from_slice(
                            &fs::read(sink_app_data.join("agent-host/current-session.json"))
                                .expect("Agent Host must persist the active session path"),
                        )
                        .expect("session metadata must be valid JSON");
                        let session_dir = Path::new(
                            session_meta["cwd"]
                                .as_str()
                                .expect("session metadata has workspace path"),
                        );
                        if scenario == "accepted-current" {
                            let evidence = crate::native_finish_evidence::current_snapshot(
                                &committed_cad_state,
                                &session_dir.join("reference-images.json"),
                                &session_dir.join("user-turns.json"),
                            )
                            .expect("compute finish binding from the active session evidence");
                            committed_cad_state["finish_summary"]["evidence_fingerprint"] =
                                json!(evidence.fingerprint);
                        }
                        fs::write(
                            session_dir.join("cad-state.json"),
                            serde_json::to_vec(&committed_cad_state)
                                .expect("serialize fixture CAD state"),
                        )
                        .expect("publish session CAD state");
                    }
                    fs::write(&sink_release_file, b"ready").expect("release fake ACP child");
                }
                sink_events
                    .lock()
                    .expect("event sink lock")
                    .push(json!({"index":index,"message":value}));
                true
            })
            .invoke_handler(tauri::generate_handler![agent_send_fixture])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("build headless Tauri MockRuntime app");
        let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("build MockRuntime webview");
        let request = tauri::webview::InvokeRequest {
            cmd: "agent_send".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "http://tauri.localhost"
                .parse()
                .expect("parse local mock URL"),
            body: tauri::ipc::InvokeBody::Json(json!({
                "requestId":"headless-agent-ipc",
                "text":if scenario == "pure-qa" { "What is a box bracket used for?" } else { "fixture prompt reaches ACP" },
                "images":[],
                "llmConfig":{"base_url":"https://api.deepseek.com/responses","model":"deepseek-flash","api_key":"fixture","reasoning_effort":"none"},
                "onlineEnabled":false,
                "onEvent":"__CHANNEL__:97"
            })),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_owned(),
        };
        let response = tauri::test::get_ipc_response(&webview, request)
            .expect("invoke shared agent_send core through MockRuntime");
        let result = response
            .deserialize::<Value>()
            .expect("deserialize AgentHost command result");

        let events = captured_events.lock().expect("event sink lock").clone();
        let expected_turn_status = if scenario == "cad-tool-failure" {
            "stopped"
        } else {
            "completed"
        };
        assert_eq!(result["turn_status"], expected_turn_status);
        let expected_prompt = if scenario == "pure-qa" {
            "What is a box bracket used for?"
        } else {
            "fixture prompt reaches ACP"
        };
        assert_eq!(result["reply"], expected_prompt);
        assert!(events.iter().any(|row| {
            row["message"]["event"] == "agent.message"
                && row["message"]["request_id"] == "headless-agent-ipc"
                && row["message"]["data"]["text"] == expected_prompt
        }));
        if scenario != "pure-qa" {
            assert!(events.iter().any(|event| {
                event["message"]["event"] == "agent.thought"
                    && event["message"]["data"]["text"] == "fixture thought"
            }));
            assert!(events.iter().any(|event| {
                event["message"]["event"] == "agent.tool"
                    && event["message"]["data"]["tool_call_id"] == "fixture-tool-1"
                    && event["message"]["data"]["status"] == "running"
            }));
        }
        assert!(events.iter().any(|event| {
            event["message"]["event"] == "agent.status"
                && event["message"]["data"]["message"] == "DSH 正在处理本轮请求。"
        }));
        let terminal = events
            .iter()
            .find(|event| event["message"]["event"] == "agent.turn_complete")
            .expect("turn completion should be emitted through Tauri Channel");
        assert_eq!(
            terminal["message"]["data"]["turn_status"],
            expected_turn_status
        );
        if scenario == "pure-qa" {
            assert!(
                !events
                    .iter()
                    .any(|event| event["message"]["event"] == "model.changed")
            );
            assert_eq!(
                terminal["message"]["data"]["current_model"]["exists"],
                false
            );
        } else {
            let model_changed = events
                .iter()
                .find(|event| event["message"]["event"] == "model.changed")
                .expect("fixture CAD transaction should emit model.changed through Tauri Channel");
            assert_eq!(model_changed["message"]["request_id"], "headless-agent-ipc");
            assert_eq!(model_changed["message"]["data"]["revision"], 1);
            assert_eq!(
                model_changed["message"]["data"]["name"],
                "IPC fixture model"
            );
            assert_eq!(model_changed["message"]["data"]["cad_revision"], 1);
            let terminal_model = &terminal["message"]["data"]["current_model"];
            assert_eq!(terminal_model["exists"], true);
            assert_eq!(terminal_model["revision"], 1);
            assert!(terminal["index"].as_u64().unwrap() > model_changed["index"].as_u64().unwrap());
            if scenario == "accepted-current" {
                assert_eq!(
                    model_changed["message"]["data"]["finish_summary"]["accepted"],
                    true
                );
                assert_eq!(
                    model_changed["message"]["data"]["acceptance_confirmed"],
                    true
                );
                assert_eq!(
                    model_changed["message"]["data"]["finish_summary"]["revision"],
                    1
                );
                assert_eq!(
                    model_changed["message"]["data"]["finish_summary"]["geometry_fingerprint"],
                    model_changed["message"]["data"]["geometry_fingerprint"]
                );
                assert_eq!(terminal_model["status"], "accepted");
                assert_eq!(terminal_model["finish_status"], "accepted");
                assert_eq!(terminal_model["finish_summary"]["accepted"], true);
            } else {
                assert_eq!(
                    model_changed["message"]["data"]["finish_summary"],
                    Value::Null
                );
                assert_eq!(
                    model_changed["message"]["data"]["acceptance_confirmed"],
                    false
                );
                assert_eq!(terminal_model["status"], "candidate");
                assert_eq!(terminal_model["finish_status"], "not_finished");
                assert_eq!(terminal_model["finish_summary"], Value::Null);
            }
        }
        if scenario == "cad-tool-failure" {
            assert_eq!(terminal["message"]["data"]["stop_reason"], "tool_error");
            assert!(events.iter().any(|event| {
                event["message"]["event"] == "agent.tool"
                    && event["message"]["data"]["status"] == "failed"
            }));
        } else {
            assert_eq!(terminal["message"]["data"]["stop_reason"], "end_turn");
        }

        println!(
            "AIMODELER_AGENT_IPC_CAPTURE={}",
            json!({"scenario":scenario,"result":result,"events":events})
        );

        agent_host::shutdown(&host_state);
        drop(scratch);
    }
}
