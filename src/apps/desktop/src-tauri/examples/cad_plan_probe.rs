//! JSONL migration probe: use the same Rust CADPlan library as the application.
use ai_modeler_ui_lib::cad_plan;
use serde_json::{Value, json};
use std::io::{self, BufRead};

fn dispatch(request: &Value) -> Result<Value, String> {
    let plan = request.get("plan").unwrap_or(&Value::Null);
    let id = request
        .get("feature_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    match request.get("method").and_then(Value::as_str) {
        Some("fingerprint") => {
            Ok(json!({"geometry_fingerprint":cad_plan::geometry_fingerprint(plan)?}))
        }
        Some("create") => Ok(cad_plan::create(
            request.get("name").and_then(Value::as_str).unwrap_or(""),
            request.get("first_feature").cloned().unwrap_or(Value::Null),
            request
                .get("source_prompt")
                .and_then(Value::as_str)
                .unwrap_or(""),
            request.get("units").cloned(),
        )),
        Some("add") => cad_plan::add(plan, request.get("feature").unwrap_or(&Value::Null)),
        Some("remove") => cad_plan::remove(plan, id),
        Some("update") => {
            let remove = request
                .get("remove_parameters")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .map(|item| {
                            item.as_str()
                                .map(str::to_owned)
                                .ok_or_else(|| "remove_parameters 必须是字符串数组。".to_owned())
                        })
                        .collect::<Result<Vec<_>, _>>()
                })
                .transpose()?
                .unwrap_or_default();
            cad_plan::update(
                plan,
                id,
                request.get("parameter_updates").unwrap_or(&Value::Null),
                request.get("source_updates").unwrap_or(&Value::Null),
                &remove,
                request.get("fields").unwrap_or(&Value::Null),
            )
        }
        _ => Err("未知 probe method。".into()),
    }
}

fn main() {
    for line in io::stdin().lock().lines() {
        let result = line
            .map_err(|error| error.to_string())
            .and_then(|line| {
                serde_json::from_str::<Value>(&line).map_err(|error| error.to_string())
            })
            .and_then(|request| dispatch(&request));
        println!(
            "{}",
            match result {
                Ok(value) => json!({"ok":true,"result":value}),
                Err(error) => json!({"ok":false,"error":error}),
            }
        );
    }
}
