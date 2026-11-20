//! Window-independent command dispatch shared by the native host and its tests.
use crate::{
    EngineContext,
    agent_host::{AgentHostState, EventSink},
};
use serde_json::{Value, json};

fn string(params: &Value, name: &str) -> Result<String, String> {
    params[name]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("参数 {name} 必须是字符串。"))
}
fn optional_string(params: &Value, name: &str) -> Option<String> {
    params[name].as_str().map(str::to_owned)
}
fn number(params: &Value, name: &str) -> Result<f64, String> {
    params[name]
        .as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("参数 {name} 必须是有限数值。"))
}
fn unsigned(params: &Value, name: &str) -> Result<u64, String> {
    params[name]
        .as_u64()
        .ok_or_else(|| format!("参数 {name} 必须是非负整数。"))
}

pub fn mutates_document(method: &str) -> bool {
    matches!(
        method,
        "quick_build"
            | "active_model_new"
            | "active_model_save"
            | "active_model_import_project"
            | "active_model_update_feature"
            | "active_model_export"
            | "active_model_export_snapshot"
            | "active_model_push_catia"
            | "plate_layout_build"
            | "agent_send"
    )
}

pub async fn dispatch(
    context: EngineContext,
    state: &AgentHostState,
    method: &str,
    params: Value,
    events: EventSink,
) -> Result<Value, String> {
    match method {
        "handshake" => Ok(
            json!({"protocol":1,"app_version":env!("CARGO_PKG_VERSION"),"engine":"rust-occt","ui_independent":true,
            "data_dir":context.data_dir,"resource_root":context.resource_root,"config_path":context.config_path}),
        ),
        "native_resource_status" => crate::native_resource_status(context),
        "native_runtime_status" => crate::native_runtime_status(context),
        "native_about" => Ok(crate::native_about()),
        "native_selftest" => crate::native_selftest(context),
        "native_open_help_path" => {
            crate::native_open_help_path(context, string(&params, "relativePath")?)
        }
        "install_dsh_runtime" => crate::install_dsh_runtime(context, events).await,
        "quick_build" => {
            crate::quick_build(
                context,
                string(&params, "text")?,
                params["revision"].as_i64().unwrap_or(1),
            )
            .await
        }
        "active_model_snapshot" => crate::active_model_snapshot(context),
        "active_model_new" => crate::active_model_new(context, string(&params, "name")?),
        "active_model_save" => crate::active_model_save(context, string(&params, "path")?),
        "active_model_import_project" => {
            crate::active_model_import_project(
                context,
                string(&params, "path")?,
                params["confirmed"].as_bool().unwrap_or(false),
            )
            .await
        }
        "active_model_update_feature" => {
            crate::active_model_update_feature(
                context,
                unsigned(&params, "index")? as usize,
                params.get("params").cloned().unwrap_or_else(|| json!({})),
                optional_string(&params, "name"),
                optional_string(&params, "action"),
                params["delta"]
                    .as_i64()
                    .map(i32::try_from)
                    .transpose()
                    .map_err(|_| "delta 超出范围")?,
                params["enabled"].as_bool(),
            )
            .await
        }
        "active_model_export" => {
            crate::active_model_export(context, string(&params, "kind")?, string(&params, "path")?)
                .map(|_| json!({"ok":true}))
        }
        "active_model_push_catia" => crate::active_model_push_catia(context),
        "active_model_export_snapshot" => crate::active_model_export_snapshot(
            context,
            string(&params, "path")?,
            string(&params, "viewSet")?,
        )
        .await
        .map(|_| json!({"ok":true})),
        "agent_send" => {
            let images = match params.get("images") {
                Some(value) => value
                    .as_array()
                    .ok_or("images 必须是数组")?
                    .iter()
                    .map(|item| {
                        item.as_str()
                            .map(str::to_owned)
                            .ok_or("图片路径必须是字符串")
                    })
                    .collect::<Result<Vec<_>, _>>()?,
                None => Vec::new(),
            };
            #[cfg(feature = "test-support")]
            if let Some(setup) = fixture_setup(&context)? {
                let state = state.clone();
                let request_id = string(&params, "requestId")?;
                let text = optional_string(&params, "text").unwrap_or_default();
                let config = params.get("llmConfig").cloned().ok_or("缺少 llmConfig")?;
                let online = params["onlineEnabled"].as_bool().unwrap_or(false);
                let use_cases = params["useReviewedCases"].as_bool().unwrap_or(true);
                return crate::run_agent_send(move || {
                    crate::agent_host::send_turn_with_fixture(
                        &state, setup, request_id, text, images, config, online, use_cases, events,
                    )
                })
                .await;
            }
            crate::agent_send(
                state,
                context,
                string(&params, "requestId")?,
                optional_string(&params, "text").unwrap_or_default(),
                images,
                params.get("llmConfig").cloned().ok_or("缺少 llmConfig")?,
                params["onlineEnabled"].as_bool().unwrap_or(false),
                params["useReviewedCases"].as_bool(),
                events,
            )
            .await
        }
        "agent_cancel" => crate::agent_cancel(state),
        "agent_reset" => crate::agent_reset(state).await,
        "agent_history" => crate::agent_history(context),
        "agent_finding_review_state" => crate::agent_finding_review_state(state),
        "agent_adjudicate_finding" => {
            crate::agent_adjudicate_finding(state, params.get("request").cloned().unwrap_or(params))
        }
        "native_settings_load" => crate::native_settings::native_settings_load(context),
        "native_settings_save" => crate::native_settings::native_settings_save(
            context,
            params.get("settings").cloned().ok_or("缺少 settings")?,
        ),
        "native_provider_templates" => Ok(json!(crate::native_settings::provider_templates())),
        "native_provider_template" => Ok(crate::native_settings::provider_template(&string(
            &params, "id",
        )?)),
        "native_provider_fetch_models" => {
            let base_url = string(&params, "baseUrl")?;
            let api_key = string(&params, "apiKey")?;
            tokio::task::spawn_blocking(move || {
                crate::native_settings::native_provider_fetch_models(base_url, api_key)
            })
            .await
            .map_err(|error| format!("获取模型列表任务失败 {error}"))?
        }
        "native_parts_list" => Ok(json!(crate::native_parts_catalog::list_parts(
            params["query"].as_str()
        ))),
        "native_samples_list" => Ok(json!(crate::native_parts_catalog::list_samples())),
        "native_online_ask" => crate::native_online_ask(string(&params, "query")?).await,
        "reviewed_cases_list" => crate::reviewed_cases_list(context).map(|rows| json!(rows)),
        "reviewed_case_save" => {
            crate::reviewed_case_save(context, unsigned(&params, "generation")?)
                .map(|rows| json!(rows))
        }
        "reviewed_case_remove" => {
            crate::reviewed_case_remove(context, string(&params, "id")?).map(|rows| json!(rows))
        }
        "plate_layout_learn" => {
            crate::plate_layout_learn(
                context,
                string(&params, "group")?,
                unsigned(&params, "generation")?,
            )
            .await
        }
        "plate_layout_build" => {
            crate::plate_layout_build(
                context,
                string(&params, "group")?,
                number(&params, "length")?,
                number(&params, "width")?,
                number(&params, "thickness")?,
                number(&params, "diameter")?,
            )
            .await
        }
        "stage_input_image" => {
            crate::stage_input_image(string(&params, "sourcePath")?).map(|path| json!(path))
        }
        "save_input_image" => {
            let data = params["data"]
                .as_array()
                .ok_or("data 必须是字节数组")?
                .iter()
                .map(|item| {
                    item.as_u64()
                        .and_then(|n| u8::try_from(n).ok())
                        .ok_or("data 包含无效字节")
                })
                .collect::<Result<Vec<_>, _>>()?;
            crate::save_input_image(string(&params, "name")?, data).map(|path| json!(path))
        }
        "read_asset" => {
            use base64::Engine;
            crate::read_asset(context,string(&params,"path")?).map(|bytes|json!({"encoding":"base64","data":base64::engine::general_purpose::STANDARD.encode(bytes)}))
        }
        _ => Err(format!("未知核心命令 {method}")),
    }
}

/// Test binaries alone can substitute the ACP child process. Production builds
/// do not include this environment hook or the fixture setup implementation.
#[cfg(feature = "test-support")]
fn fixture_setup(context: &EngineContext) -> Result<Option<crate::agent_host::TestSetup>, String> {
    let Some(script) = std::env::var_os("AIMODELER_RPC_TEST_ACP_FIXTURE") else {
        return Ok(None);
    };
    let node = std::env::var_os("AIMODELER_TEST_NODE").ok_or("fixture requires Node")?;
    let root = context.data_dir.join("test-runtime");
    let cli = root.join("dsh/lib/bin.js");
    std::fs::create_dir_all(cli.parent().ok_or("fixture path")?).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(root.join("dsh/node_modules")).map_err(|e| e.to_string())?;
    std::fs::write(&cli, b"isolated ACP fixture marker").map_err(|e| e.to_string())?;
    Ok(Some(crate::agent_host::TestSetup {
        root: context.resource_root.clone(),
        data_dir: context.data_dir.clone(),
        runtime: crate::dsh_runtime::RuntimePaths {
            root,
            node: node.into(),
            dsh_cli: cli,
            home: context.data_dir.join("test-dsh-home"),
        },
        dsh_args: vec![script],
        dsh_env: Vec::new(),
    }))
}
