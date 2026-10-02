//! Generate the production Agent profile without starting a window or model.
//! Provider configuration is received on stdin, never through process arguments.
use ai_modeler_ui_lib::agent_host::{prepare_cli_profile, select_cli_reasoning};
use serde_json::Value;
use std::{
    io::{self, Read},
    path::PathBuf,
};

fn main() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 && args.as_slice() != ["--select-reasoning"] {
        return Err("usage: native_agent_profile LEGACY_ROOT PATCH_PATH < provider.json; or --select-reasoning < request.json".into());
    }
    let mut input = String::new();
    io::stdin()
        .take(1_048_577)
        .read_to_string(&mut input)
        .map_err(|_| "配置输入读取失败。")?;
    if input.len() > 1_048_576 {
        return Err("配置输入超过 1 MiB。".into());
    }
    let config: Value = serde_json::from_str(&input).map_err(|_| "provider配置必须是JSON对象。")?;
    if args.len() == 1 {
        let selected = select_cli_reasoning(&config["provider"], &config["session_response"])?;
        println!("{selected}");
        return Ok(());
    }
    let root = PathBuf::from(&args[0]);
    let patch = PathBuf::from(&args[1]);
    let result = prepare_cli_profile(&root, &patch, &config)?;
    println!(
        "{}",
        serde_json::to_string(&result).map_err(|_| "无法编码安全配置摘要。")?
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn reasoning_selection_uses_advertised_options_and_rejects_missing_strength() {
        let config = json!({"base_url":"https://api.deepseek.com/responses", "model":"deepseek-flash", "reasoning_effort":"high"});
        let session = json!({"configOptions":[{"id":"reasoning_effort","options":[
            {"value":"off","name":"Off"},{"value":"high","name":"High"}]}]});
        let result = select_cli_reasoning(&config, &session).unwrap();
        assert_eq!(result["value"], "high");
        assert_eq!(result["configurable"], true);
        assert!(select_cli_reasoning(&config, &json!({"configOptions":[]})).is_err());
    }

    #[test]
    fn profile_uses_production_settings_and_prompt_without_disclosing_keys() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../legacy");
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!("native-agent-profile-{nonce}"));
        let patch = directory.join("profile.yml");
        let config = json!({"base_url":"https://api.deepseek.com/responses",
            "model":"deepseek-flash","api_key":"fixture-secret-not-for-disk",
            "reasoning_effort":"high","dsh_output_cap":12000,"dsh_context_window":1048576});
        let result = prepare_cli_profile(&root, &patch, &config).unwrap();
        let text = fs::read_to_string(&patch).unwrap();
        assert_eq!(result["main_output_cap"], 12000);
        assert_eq!(result["vision_output_cap"], 12000);
        assert_eq!(result["context_window"], 1048576);
        assert_eq!(result["reasoning_effort_requested"], "high");
        assert!(text.contains("maxTokens: 12000"));
        assert!(text.contains("contextWindow: 1048576"));
        assert!(!text.contains("fixture-secret"));
        assert!(!result.to_string().contains("fixture-secret"));
        assert!(result["prompt_sha256"].as_str().unwrap().len() == 64);
        fs::remove_file(&patch).unwrap();
        fs::remove_dir(&directory).unwrap();
    }
}
