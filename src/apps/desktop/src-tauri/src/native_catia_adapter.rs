//! On-demand handoff from the authoritative Rust active model to the minimal
//! CATIA COM adapter. The payload is the F5 `.aimodel` document plus its source
//! CADPlan; this module never reconstructs a legacy Python `Document`.

use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

const MAX_INPUT_BYTES: usize = 128 * 1024 * 1024;

pub fn request_payload(
    record: &Value,
    output_path: &Path,
    open_in_catia: bool,
) -> Result<Value, String> {
    let generation = record["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效，不能推送到 CATIA。")?;
    let model_id = record["model_id"]
        .as_str()
        .filter(|value| !value.is_empty())
        .ok_or("活动模型 model_id 无效，不能推送到 CATIA。")?;
    let document = record
        .get("document")
        .filter(|value| value.is_object())
        .ok_or("活动模型缺少 F5 原生工程 document。")?;
    if document["format"].as_str() != Some("ai-modeler") || document["version"].as_u64() != Some(1)
    {
        return Err("活动模型不是受支持的 F5 `.aimodel` 格式。".into());
    }
    let features = document["features"]
        .as_array()
        .filter(|rows| !rows.is_empty())
        .ok_or("空模型没有可推送的 CATIA 特征。")?;
    if features.len() > 100_000 {
        return Err("活动模型特征数超过 CATIA adapter 上限。".into());
    }
    let cadplan = record
        .get("cadplan")
        .filter(|value| value.is_object())
        .cloned()
        .unwrap_or(Value::Null);
    let request = json!({
        "protocol": "ai-modeler-catia-com-v1",
        "action": if open_in_catia { "push" } else { "export" },
        "identity": {
            "model_id": model_id,
            "generation": generation,
            "revision": record.get("revision").cloned().unwrap_or(Value::Null),
            "geometry_fingerprint": record.get("geometry_fingerprint").cloned().unwrap_or(Value::Null),
        },
        // This object is the exact legacy-compatible project representation used
        // by native F5 save/export. Preserve its feature IDs and provenance.
        "project": document,
        // Keep full CADPlan evidence, including per-parameter evidence strings.
        "cadplan": cadplan,
        "output_path": output_path,
        "open_in_catia": open_in_catia,
    });
    let bytes = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
    if bytes.len() > MAX_INPUT_BYTES {
        return Err("CATIA adapter 输入超过 128 MiB。".into());
    }
    Ok(request)
}

pub fn transfer_path(data_dir: &Path, model_id: &str, generation: u64) -> PathBuf {
    let safe_id = model_id
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || *ch == '-' || *ch == '_')
        .take(64)
        .collect::<String>();
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    data_dir
        .join("catia-transfer")
        .join(format!("{safe_id}-g{generation}-{nonce}.json"))
}

/// Invoke the fixed, packaged COM adapter only for a user-requested CATIA
/// operation. The child receives one JSON data file; no generated Python code
/// or legacy Agent bridge is involved.
pub fn run(
    python: &Path,
    python_prefix_args: &[String],
    adapter: &Path,
    data_dir: &Path,
    record: &Value,
    output: &Path,
    open_in_catia: bool,
) -> Result<Value, String> {
    if !cfg!(windows) {
        return Err("CATIA COM adapter 仅支持 Windows。".into());
    }
    if !adapter.is_file() {
        return Err(format!("CATIA COM adapter 资源缺失：{}", adapter.display()));
    }
    if !output
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("CATPart"))
    {
        return Err("CATIA 输出路径必须使用 `.CATPart` 扩展名。".into());
    }
    if output.exists() {
        return Err(format!(
            "CATIA 输出文件已存在，为避免静默覆盖请选择新文件名：{}",
            output.display()
        ));
    }
    let model_id = record["model_id"]
        .as_str()
        .ok_or("活动模型 model_id 无效。")?;
    let generation = record["generation"]
        .as_u64()
        .ok_or("活动模型 generation 无效。")?;
    let request = request_payload(record, output, open_in_catia)?;
    let request_path = transfer_path(data_dir, model_id, generation);
    let parent = request_path.parent().ok_or("CATIA transfer path invalid")?;
    fs::create_dir_all(parent).map_err(|error| format!("无法创建 CATIA 临时传输目录：{error}"))?;
    let bytes = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
    crate::native_model_io::atomic_write(&request_path, &bytes)?;

    let mut command = Command::new(python);
    command
        .args(python_prefix_args)
        .args(["-s", "-B"])
        .arg(adapter)
        .arg("--input")
        .arg(&request_path)
        .env("PYTHONNOUSERSITE", "1")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let output_result = command.output().map_err(|error| {
        format!(
            "无法启动按需 CATIA Python COM adapter（{}）：{error}",
            python.display()
        )
    });
    let _ = fs::remove_file(&request_path);
    let child = output_result?;
    let stdout = String::from_utf8_lossy(&child.stdout);
    let response: Value = serde_json::from_str(stdout.trim()).map_err(|error| {
        let stderr = String::from_utf8_lossy(&child.stderr);
        format!("CATIA adapter 未返回合法结果（{error}）；{}", stderr.trim())
    })?;
    if response["protocol"].as_str() != Some("ai-modeler-catia-com-v1") {
        return Err("CATIA adapter 响应协议版本不匹配。".into());
    }
    if !child.status.success() || response["state"] == "failed" {
        let message = response["error"].as_str().unwrap_or("CATIA COM 操作失败。");
        return Err(format!("{message}（状态码 {:?}）", child.status.code()));
    }
    if response["identity"]["model_id"] != record["model_id"]
        || response["identity"]["generation"] != record["generation"]
        || response["output_path"] != output.to_string_lossy().as_ref()
    {
        return Err("CATIA adapter 响应与本次活动模型或输出路径不匹配。".into());
    }
    if !output.is_file() {
        return Err("CATIA adapter 未生成 CATPart 文件。".into());
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record() -> Value {
        json!({"model_id":"model-abc","generation":4,"revision":7,
            "geometry_fingerprint":"fingerprint","cadplan":{"schema_version":"1.0","features":[{
                "id":"body","kind":"box","operation":"add","parameters":{"length":100},
                "sources":{"length":{"type":"image_estimate","evidence":"front-view ratio"}}}]},
            "document":{"format":"ai-modeler","version":1,"name":"sample","source_prompt":"image",
                "features":[{"kind":"box","params":{"length":100},"op":"add","name":"Body",
                    "feature_id":"body","enabled":true,"provenance":{"length":"image_estimate"}}]}})
    }

    #[test]
    fn payload_carries_exact_active_project_identity_and_source_evidence() {
        let active = record();
        let path = PathBuf::from("sample.CATPart");
        let request = request_payload(&active, &path, true).unwrap();
        assert_eq!(request["protocol"], "ai-modeler-catia-com-v1");
        assert_eq!(request["identity"]["generation"], 4);
        assert_eq!(request["identity"]["geometry_fingerprint"], "fingerprint");
        assert_eq!(request["project"], active["document"]);
        assert_eq!(
            request["cadplan"]["features"][0]["sources"]["length"]["evidence"],
            "front-view ratio"
        );
        assert_eq!(request["open_in_catia"], true);
    }

    #[test]
    fn refuses_empty_or_non_f5_active_models_before_starting_adapter() {
        let mut active = record();
        active["document"]["features"] = json!([]);
        assert!(request_payload(&active, Path::new("empty.CATPart"), false).is_err());
        let mut active = record();
        active["document"]["format"] = json!("other");
        assert!(request_payload(&active, Path::new("other.CATPart"), false).is_err());
    }

    #[test]
    fn transfer_path_is_scoped_and_uniquely_names_generation() {
        let first = transfer_path(Path::new("data"), "m:/odel?1", 3);
        let second = transfer_path(Path::new("data"), "m:/odel?1", 3);
        assert!(first.starts_with(Path::new("data/catia-transfer")));
        assert!(
            first
                .file_name()
                .unwrap()
                .to_string_lossy()
                .contains("model1-g3")
        );
        assert_ne!(first, second);
    }
}
