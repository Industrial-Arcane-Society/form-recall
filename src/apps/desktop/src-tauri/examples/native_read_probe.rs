//! CLI acceptance endpoint for Rust-owned read tools, with no Python fallback.
use ai_modeler_ui_lib::native_tool_bridge::{NativeToolBridge, ToolExecutor};
use ai_modeler_ui_lib::native_tool_executor::NativeToolExecutor;
use ai_modeler_ui_lib::native_tool_schemas::native_tool_schemas;
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

struct RejectCompatibility;
impl ToolExecutor for RejectCompatibility {
    fn execute(&self, _: &str, name: &str, _: &Value, _: Duration) -> Result<Value, String> {
        Err(format!("该验收端点禁止 Python compatibility 执行：{name}"))
    }
    fn cancel(&self, _: &str) -> bool {
        false
    }
}

fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let root = PathBuf::from(
        args.first()
            .ok_or("usage: native_read_probe LEGACY_ROOT SESSION_DIR [--online] [--serve]")?,
    );
    let dir = PathBuf::from(args.get(1).ok_or("必须指定隔离会话目录。")?);
    let executor = Arc::new(NativeToolExecutor::new(
        root,
        &dir,
        args.iter().any(|s| s == "--online"),
        Arc::new(RejectCompatibility),
    )?);
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
