//! CLI acceptance endpoint for Rust-owned Cordis tools; no Python worker exists.
use ai_modeler_ui_lib::native_tool_bridge::{NativeToolBridge, ToolExecutor};
use ai_modeler_ui_lib::native_tool_executor::NativeToolExecutor;
use ai_modeler_ui_lib::native_tool_schemas::native_tool_schemas;
use ai_modeler_ui_lib::native_vision_provider::{NativeVisionProvider, ProviderConfig};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let root = PathBuf::from(args.first().ok_or(
        "usage: native_read_probe LEGACY_ROOT SESSION_DIR [--online] [--serve] [--vision]",
    )?);
    let dir = PathBuf::from(args.get(1).ok_or("必须指定隔离会话目录。")?);
    let mut executor = NativeToolExecutor::new(root, &dir, args.iter().any(|s| s == "--online"))?;
    if let Some(index) = args.iter().position(|s| s == "--occt") {
        executor = executor.with_geometry_runtime(PathBuf::from(
            args.get(index + 1)
                .ok_or("--occt须提供runtime manifest路径。")?,
        ));
    }
    if args.iter().any(|s| s == "--vision") {
        if !args.iter().any(|s| s == "--online") {
            return Err("--vision 要求同时显式指定 --online。".into());
        }
        // The key is read from an existing environment variable and never
        // included in probe output, logs, or the ACP profile file.
        let key = std::env::var("AIMODELER_LLM_API_KEY")
            .or_else(|_| std::env::var("DEEPSEEK_API_KEY"))
            .map_err(|_| "--vision 需要 AIMODELER_LLM_API_KEY 或 DEEPSEEK_API_KEY。")?;
        let base_url = std::env::var("AI_MODELER_REVIEW_BASE_URL")
            .unwrap_or_else(|_| "https://api.deepseek.com/responses".into());
        let model =
            std::env::var("AI_MODELER_REVIEW_MODEL").unwrap_or_else(|_| "deepseek-flash".into());
        let effort =
            std::env::var("AI_MODELER_REVIEW_REASONING_EFFORT").unwrap_or_else(|_| "none".into());
        let thinking_budget = std::env::var("AI_MODELER_REVIEW_THINKING_BUDGET")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| "AI_MODELER_REVIEW_THINKING_BUDGET 必须是非负整数。")
            })
            .transpose()?;
        let max_output_tokens = std::env::var("AI_MODELER_REVIEW_OUTPUT_CAP")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| "AI_MODELER_REVIEW_OUTPUT_CAP 必须是正整数。")
            })
            .transpose()?
            .filter(|value| *value > 0)
            .unwrap_or(2048);
        let provider = NativeVisionProvider::new(ProviderConfig {
            base_url,
            model,
            api_key: key,
            effort,
            thinking_budget,
            max_output_tokens,
        })
        .map_err(|error| {
            format!(
                "无法初始化原生视觉provider：{}: {}",
                error.kind, error.message
            )
        })?;
        executor = executor.with_vision(Arc::new(provider));
    }
    let executor = Arc::new(executor);
    let mut stdout = io::stdout().lock();
    let bridge = if args.iter().any(|s| s == "--serve") {
        let bridge = NativeToolBridge::start(
            "native-read-probe",
            dir.join("schemas.json"),
            native_tool_schemas()?,
            executor.clone(),
        )?;
        writeln!(
            stdout,
            "{}",
            json!({"address":bridge.address(),"token":bridge.token(),
            "session_id":bridge.session_id(),"schemas_path":bridge.schemas_path()})
        )
        .map_err(|e| e.to_string())?;
        stdout.flush().map_err(|e| e.to_string())?;
        Some(bridge)
    } else {
        None
    };
    for (index, line) in io::stdin().lock().lines().enumerate() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim() == "quit" {
            break;
        }
        let result = serde_json::from_str::<Value>(&line)
            .map_err(|error| error.to_string())
            .and_then(|request| {
                let name = request
                    .get("name")
                    .and_then(Value::as_str)
                    .ok_or("缺少工具名称。")?;
                executor.execute(
                    &format!("probe-{index}"),
                    name,
                    request.get("arguments").unwrap_or(&json!({})),
                    Duration::from_secs(30),
                )
            });
        let response = match result {
            Ok(mut value) => {
                if let Some(images) = value.get_mut("images").and_then(Value::as_array_mut) {
                    for image in images {
                        if let Some(object) = image.as_object_mut() {
                            let size = object
                                .remove("data")
                                .and_then(|data| data.as_str().map(str::len))
                                .unwrap_or(0);
                            object.insert("base64_characters".into(), json!(size));
                        }
                    }
                }
                json!({"ok":true,"result":value})
            }
            Err(error) => json!({"ok":false,"error":error}),
        };
        writeln!(stdout, "{response}").map_err(|error| error.to_string())?;
        stdout.flush().map_err(|error| error.to_string())?;
    }
    if let Some(bridge) = bridge {
        bridge.shutdown();
    }
    executor.shutdown();
    Ok(())
}
