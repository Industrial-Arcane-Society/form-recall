//! Resolve per-stage model settings while keeping the existing desktop
//! provider profile as the default. This module is deliberately independent
//! of the UI and ACP lifecycle so it can be wired in after its contract is
//! reviewed.
//!
//! Accepted global inputs are either the persisted `settings.llm` object
//! (`active_provider_id` plus `providers`) or the flattened active provider
//! profile currently sent to `agent_send`. Stage overrides are keyed by
//! `perception`, `modeling`, and `review`; each may set `provider_id`, `model`,
//! and/or `reasoning_effort`.
//! A model override must equal that profile's configured default or match a
//! `models`/`available_models` catalog entry. This intentionally prevents
//! switching to an unverified model when no provider catalog was persisted.

use serde_json::{Map, Value, json};

pub const STAGE_PERCEPTION: &str = "perception";
pub const STAGE_MODELING: &str = "modeling";
pub const STAGE_REVIEW: &str = "review";
const STAGES: &[&str] = &[STAGE_PERCEPTION, STAGE_MODELING, STAGE_REVIEW];
const REASONING_EFFORTS: &[&str] = &["none", "low", "high", "max"];

/// Resolve one stage to a flattened provider config accepted by the current
/// host profile parser (`base_url`, `model`, `api_key`, and related fields).
/// Unknown provider IDs and models that are not present in the selected
/// provider's known model catalog are rejected instead of silently falling
/// back to another profile.
pub fn resolve_stage(
    global_config: &Value,
    stage_overrides: &Value,
    stage: &str,
) -> Result<Value, String> {
    if !STAGES.contains(&stage) {
        return Err(format!("未知 Agent 模型阶段：{stage}"));
    }
    let stage_overrides = stage_overrides
        .as_object()
        .ok_or("Agent 阶段模型覆盖根节点必须是对象。")?;
    if let Some(unknown) = stage_overrides
        .keys()
        .find(|key| !STAGES.contains(&key.as_str()))
    {
        return Err(format!("未知 Agent 模型阶段覆盖：{unknown}"));
    }

    let empty_override = Value::Object(Map::new());
    let overrides = stage_overrides
        .get(stage)
        .filter(|value| !value.is_null())
        .unwrap_or(&empty_override);
    if !overrides.is_object() {
        return Err(format!("{stage} 阶段模型覆盖必须是对象。"));
    }
    reject_unknown_keys(
        overrides,
        &["provider_id", "model", "reasoning_effort"],
        stage,
    )?;

    let (providers, default_profile) = profiles(global_config)?;
    let default_id = required_string(default_profile, "id", "供应商档案缺少档案 ID。")?;
    let requested_provider_id = override_string(overrides, "provider_id", stage)?
        .map(str::trim)
        .filter(|provider_id| !provider_id.is_empty())
        .unwrap_or(default_id);

    let selected_profile = if providers.is_empty() {
        if requested_provider_id != default_id {
            return Err(format!("未知供应商档案：{requested_provider_id}"));
        }
        default_profile
    } else {
        providers
            .iter()
            .copied()
            .find(|profile| {
                profile.get("id").and_then(Value::as_str) == Some(requested_provider_id)
            })
            .ok_or_else(|| format!("未知供应商档案：{requested_provider_id}"))?
    };

    let selected_id = required_string(selected_profile, "id", "所选供应商档案缺少档案 ID。")?;
    if selected_id != requested_provider_id {
        return Err(format!("所选供应商档案 ID 不匹配：{requested_provider_id}"));
    }
    required_string(
        selected_profile,
        "base_url",
        "所选供应商档案缺少 API 地址。",
    )?;
    let configured_model =
        required_string(selected_profile, "model", "所选供应商档案缺少模型名称。")?;
    let model = override_string(overrides, "model", stage)?
        .map(str::trim)
        .filter(|model| !model.is_empty())
        .unwrap_or(configured_model);
    validate_known_model(selected_profile, model)?;

    let configured_effort = match selected_profile.get("reasoning_effort") {
        None | Some(Value::Null) => "none",
        Some(Value::String(value)) => value,
        Some(_) => return Err("供应商档案 reasoning_effort 必须是字符串。".into()),
    };
    let effort = override_string(overrides, "reasoning_effort", stage)?
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(configured_effort)
        .to_ascii_lowercase();
    if !REASONING_EFFORTS.contains(&effort.as_str()) {
        return Err(format!("无效 reasoning_effort：{effort}"));
    }

    let mut effective = selected_profile
        .as_object()
        .cloned()
        .ok_or_else(|| "供应商档案必须是对象。".to_owned())?;
    effective.insert("provider_id".into(), json!(requested_provider_id));
    effective.insert("model".into(), json!(model));
    effective.insert("reasoning_effort".into(), json!(effort));
    Ok(Value::Object(effective))
}

/// Resolve all three configured stages. This validates every override before
/// callers start any stage provider, avoiding partial startup with bad config.
pub fn resolve_all(
    global_config: &Value,
    stage_overrides: &Value,
) -> Result<Map<String, Value>, String> {
    let mut resolved = Map::new();
    for stage in STAGES {
        resolved.insert(
            (*stage).to_owned(),
            resolve_stage(global_config, stage_overrides, stage)?,
        );
    }
    Ok(resolved)
}

fn profiles(global_config: &Value) -> Result<(Vec<&Value>, &Value), String> {
    let llm = global_config.get("llm").unwrap_or(global_config);
    let providers = llm
        .get("providers")
        .and_then(Value::as_array)
        .map(|profiles| profiles.iter().collect::<Vec<_>>())
        .unwrap_or_default();
    let active_id = llm
        .get("active_provider_id")
        .and_then(Value::as_str)
        .unwrap_or("");

    if providers.is_empty() {
        if !llm.is_object() {
            return Err("当前模型设置必须是对象。".into());
        }
        return Ok((providers, llm));
    }

    let active = providers
        .iter()
        .copied()
        .find(|profile| profile.get("id").and_then(Value::as_str) == Some(active_id))
        .or_else(|| providers.first().copied())
        .ok_or_else(|| "模型设置没有可用供应商档案。".to_owned())?;

    // The first-profile fallback matches the Vue settings selector behavior.
    Ok((providers, active))
}

fn required_string<'a>(value: &'a Value, key: &str, message: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| message.to_owned())
}

fn validate_known_model(profile: &Value, model: &str) -> Result<(), String> {
    let default_model = profile.get("model").and_then(Value::as_str).unwrap_or("");
    let catalog = profile
        .get("models")
        .or_else(|| profile.get("available_models"))
        .and_then(Value::as_array);
    let known = catalog.is_some_and(|rows| {
        rows.iter().any(|row| {
            row.as_str().is_some_and(|known| known == model)
                || row.get("id").and_then(Value::as_str) == Some(model)
        })
    });
    if model == default_model || known {
        Ok(())
    } else {
        Err(format!(
            "未知模型 {model}；请先将它加入所选供应商档案的 models 列表。"
        ))
    }
}

fn override_string<'a>(
    overrides: &'a Value,
    key: &str,
    stage: &str,
) -> Result<Option<&'a str>, String> {
    match overrides.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        Some(_) => Err(format!("{stage} 阶段 {key} 必须是字符串。")),
    }
}

fn reject_unknown_keys(value: &Value, allowed: &[&str], stage: &str) -> Result<(), String> {
    if let Some(object) = value.as_object() {
        if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
            return Err(format!("{stage} 阶段模型覆盖含未知字段：{key}"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn global() -> Value {
        json!({"llm":{"active_provider_id":"primary","providers":[
            {"id":"primary","base_url":"https://api.deepseek.com/responses","model":"deepseek-flash","api_key":"secret","reasoning_effort":"none"},
            {"id":"vision","base_url":"https://api.example.test/v1","model":"vision-default","models":["vision-default","vision-pro"],"api_key":"other","reasoning_effort":"low"}
        ]}})
    }

    #[test]
    fn defaults_all_stages_to_current_profile_and_accepts_stage_overrides() {
        let inherited = resolve_stage(&global(), &json!({}), "modeling").unwrap();
        assert_eq!(inherited["provider_id"], "primary");
        assert_eq!(inherited["model"], "deepseek-flash");
        assert_eq!(inherited["reasoning_effort"], "none");
        assert_eq!(inherited["api_key"], "secret");

        let overridden = resolve_stage(
            &global(),
            &json!({"review":{"provider_id":"vision","model":"vision-pro","reasoning_effort":"high"}}),
            "review",
        )
        .unwrap();
        assert_eq!(overridden["provider_id"], "vision");
        assert_eq!(overridden["base_url"], "https://api.example.test/v1");
        assert_eq!(overridden["model"], "vision-pro");
        assert_eq!(overridden["reasoning_effort"], "high");
    }

    #[test]
    fn blank_provider_ids_in_all_stage_overrides_inherit_current_profile() {
        let overrides = json!({
            "perception":{"provider_id":""},
            "modeling":{"provider_id":"   "},
            "review":{"provider_id":"\t"}
        });

        let resolved = resolve_all(&global(), &overrides).unwrap();
        for stage in STAGES {
            assert_eq!(resolved[*stage]["provider_id"], "primary");
            assert_eq!(resolved[*stage]["model"], "deepseek-flash");
            assert_eq!(resolved[*stage]["api_key"], "secret");
        }
    }

    #[test]
    fn rejects_unknown_provider_model_and_reasoning_effort() {
        assert!(
            resolve_stage(
                &global(),
                &json!({"perception":{"provider_id":"missing"}}),
                "perception"
            )
            .unwrap_err()
            .contains("未知供应商")
        );
        assert!(
            resolve_stage(
                &global(),
                &json!({"review":{"provider_id":"vision","model":"made-up"}}),
                "review"
            )
            .unwrap_err()
            .contains("未知模型")
        );
        assert!(
            resolve_stage(
                &global(),
                &json!({"modeling":{"reasoning_effort":"extreme"}}),
                "modeling"
            )
            .unwrap_err()
            .contains("无效 reasoning_effort")
        );
    }

    #[test]
    fn rejects_malformed_override_root_unknown_stage_and_incomplete_profiles() {
        assert!(
            resolve_stage(&global(), &json!([]), "perception")
                .unwrap_err()
                .contains("根节点必须是对象")
        );
        assert!(
            resolve_stage(&global(), &json!({"percepton":{}}), "perception")
                .unwrap_err()
                .contains("未知 Agent 模型阶段覆盖")
        );

        let missing_id = json!({"base_url":"https://api.example.test/v1","model":"m"});
        assert!(
            resolve_stage(&missing_id, &json!({}), "modeling")
                .unwrap_err()
                .contains("缺少档案 ID")
        );

        let missing_base_url = json!({"id":"p1","model":"m"});
        assert!(
            resolve_stage(&missing_base_url, &json!({}), "modeling")
                .unwrap_err()
                .contains("缺少 API 地址")
        );

        let missing_model = json!({"id":"p1","base_url":"https://api.example.test/v1"});
        assert!(
            resolve_stage(&missing_model, &json!({}), "modeling")
                .unwrap_err()
                .contains("缺少模型名称")
        );
    }

    #[test]
    fn resolves_legacy_flat_provider_profile_without_stage_overrides() {
        let legacy = json!({"id":"legacy","base_url":"https://api.deepseek.com/responses",
            "model":"deepseek-flash","reasoning_effort":"high"});
        let resolved = resolve_all(&legacy, &json!({})).unwrap();
        for stage in STAGES {
            assert_eq!(resolved[*stage]["provider_id"], "legacy");
            assert_eq!(resolved[*stage]["reasoning_effort"], "high");
        }
    }
}
