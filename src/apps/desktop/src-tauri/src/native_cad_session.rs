//! Transactional native CAD session edits. Geometry execution is delegated to F4.
use crate::{cad_plan, cad_plan_validation};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

#[derive(Clone, Debug)]
pub enum SessionCommand {
    Create {
        name: String,
        first_feature: Value,
        source_prompt: String,
        units: Option<Value>,
    },
    Add {
        feature: Value,
        finding_ids: Vec<String>,
    },
    Update {
        feature_id: String,
        parameter_updates: Value,
        source_updates: Value,
        remove_parameters: Vec<String>,
        fields: Value,
        finding_ids: Vec<String>,
    },
    Remove {
        feature_id: String,
        finding_ids: Vec<String>,
    },
    RestoreBest,
    RestorePartial,
    RequestPreview {
        build: bool,
    },
}
#[derive(Clone, Debug)]
pub enum BackendRequest {
    Preview {
        revision: i64,
        geometry_fingerprint: String,
        plan: Value,
    },
    Build {
        revision: i64,
        geometry_fingerprint: String,
        plan: Value,
    },
}
#[derive(Clone, Debug)]
pub struct SessionResult {
    pub state: Value,
    pub value: Value,
    pub backend: Option<BackendRequest>,
}
#[derive(Clone, Debug)]
pub struct BackendOutcome {
    pub revision: i64,
    pub geometry_fingerprint: String,
    pub output_paths: Value,
    pub preview: Value,
}

/// Applies one domain operation to a clone; input remains untouched on every error.
pub fn apply(state: &Value, command: SessionCommand) -> Result<SessionResult, String> {
    let mut next = state.clone();
    let rev = revision(&next);
    if next.get("revision").is_none() {
        next["revision"] = json!(rev);
    }
    let tool: String;
    let before = next.get("plan").filter(|p| p.is_object()).cloned();
    let mut affected_feature_ids: Vec<String> = Vec::new();
    let mut changed_finding_ids: Vec<String> = Vec::new();
    let action_extra: Value;
    match command {
        SessionCommand::Create {
            name,
            first_feature,
            source_prompt,
            units,
        } => {
            if before.is_some() {
                return Err("已有模型；create 只能用于空 session。".into());
            }
            let (clean_feature, source_turn_ids) = strip_feature_turn_ids(&first_feature)?;
            let plan = cad_plan::create(name, clean_feature.clone(), source_prompt, units);
            next["plan"] = plan;
            let id = str_field(&clean_feature, "id");
            affected_feature_ids.push(id.clone());
            action_extra = json!({"feature_id":id,"explicit_replacement":false,"preserved_source_keys":[],"user_source_provenance":source_provenance_rows(&next["plan"], &id, &source_turn_ids),"archived_finding_count":0});
            tool = "cad_create_model".into();
        }
        SessionCommand::Add {
            feature,
            finding_ids,
        } => {
            let plan = before.as_ref().ok_or("当前没有 CADPlan。")?;
            let (clean_feature, source_turn_ids) = strip_feature_turn_ids(&feature)?;
            let id = str_field(&clean_feature, "id");
            if let Some(existing) = features(plan).iter().find(|f| str_field(f, "id") == id) {
                if geometry_projection(existing) == geometry_projection(&clean_feature) {
                    record(
                        &mut next,
                        "cad_add_feature",
                        json!({"feature_id":id,"no_op":true}),
                    );
                    return Ok(result(next, None));
                }
                return Err(format!("feature_id {id} 已存在且几何不同。"));
            }
            let candidate = cad_plan::add(plan, &clean_feature)?;
            guard_transition(&next, plan, &candidate, &[id.clone()], &finding_ids)?;
            next["plan"] = candidate;
            affected_feature_ids.push(id.clone());
            changed_finding_ids = finding_ids.clone();
            action_extra = json!({"feature_id":id,"user_source_provenance":source_provenance_rows(&next["plan"], &id, &source_turn_ids)});
            tool = "cad_add_feature".into();
        }
        SessionCommand::Update {
            feature_id,
            parameter_updates,
            source_updates,
            remove_parameters,
            fields,
            finding_ids,
        } => {
            let plan = before.as_ref().ok_or("当前没有 CADPlan。")?;
            let (clean_source_updates, source_turn_ids) =
                strip_source_update_turn_ids(&source_updates)?;
            let candidate = cad_plan::update(
                plan,
                &feature_id,
                &parameter_updates,
                &clean_source_updates,
                &remove_parameters,
                &fields,
            )?;
            if deep_equal(&candidate, plan) {
                record(
                    &mut next,
                    "cad_update_feature",
                    json!({"feature_id":feature_id,"no_op":true}),
                );
                return Ok(result(next, None));
            }
            let geom_changed = cad_plan::geometry_fingerprint(plan)?
                != cad_plan::geometry_fingerprint(&candidate)?;
            if geom_changed {
                guard_transition(&next, plan, &candidate, &[feature_id.clone()], &finding_ids)?;
            }
            next["plan"] = candidate.clone();
            if geom_changed {
                affected_feature_ids.push(feature_id.clone());
                changed_finding_ids = finding_ids.clone();
                action_extra = json!({"feature_id":feature_id,"finding_ids":finding_ids,"preserved_source_keys":preserved_source_keys(plan,&feature_id,&clean_source_updates,&remove_parameters),"provenance_changes":[],"user_source_provenance":source_provenance_rows(&candidate, &feature_id, &source_turn_ids)});
                tool = "cad_update_feature".into();
            } else {
                record(
                    &mut next,
                    "cad_update_feature",
                    json!({"feature_id":feature_id,"metadata_only":true,"user_source_provenance":source_provenance_rows(&candidate, &feature_id, &source_turn_ids)}),
                );
                return Ok(result(next, None));
            }
        }
        SessionCommand::Remove {
            feature_id,
            finding_ids,
        } => {
            let plan = before.as_ref().ok_or("当前没有 CADPlan。")?;
            let row = features(plan)
                .iter()
                .find(|f| str_field(f, "id") == feature_id)
                .ok_or_else(|| format!("未找到特征 id：{feature_id}"))?;
            if str_field(row, "operation") == "add"
                && features(plan)
                    .iter()
                    .filter(|f| str_field(f, "operation") == "add")
                    .count()
                    <= 1
            {
                return Err("不能移除唯一的 add 主体特征。".into());
            }
            let candidate = cad_plan::remove(plan, &feature_id)?;
            guard_transition(&next, plan, &candidate, &[feature_id.clone()], &finding_ids)?;
            next["plan"] = candidate;
            affected_feature_ids.push(feature_id.clone());
            changed_finding_ids = finding_ids.clone();
            action_extra = json!({"feature_id":feature_id,"finding_ids":finding_ids});
            tool = "cad_remove_feature".into();
        }
        SessionCommand::RestoreBest | SessionCommand::RestorePartial => {
            let partial = matches!(command, SessionCommand::RestorePartial);
            let cand = if partial {
                next.get("partial_candidate")
            } else {
                next.get("best_evidence_candidate")
                    .or_else(|| next.get("last_valid_candidate"))
                    .or_else(|| next.get("best_candidate"))
            }
            .cloned()
            .ok_or("没有可恢复的候选 CADPlan。")?;
            if partial && cand.get("accepted") != Some(&Value::Bool(false)) {
                return Err("当前 partial_candidate 不是明确标记为未验收的候选。".into());
            }
            let plan = cand
                .get("plan")
                .filter(|p| p.is_object())
                .cloned()
                .ok_or("候选中没有 CADPlan。")?;
            let old = before.as_ref().ok_or("当前没有 CADPlan。")?;
            if cad_plan::geometry_fingerprint(old)? == cad_plan::geometry_fingerprint(&plan)? {
                record(
                    &mut next,
                    if partial {
                        "cad_restore_partial_candidate"
                    } else {
                        "cad_restore_best_candidate"
                    },
                    json!({"no_op":true}),
                );
                return Ok(result(next, None));
            }
            // Restore is an explicit recovery operation. The Python domain does
            // not run the edit-cycle guard here; rejecting a known-good saved
            // candidate would make rollback impossible after a regression.
            next["plan"] = plan;
            affected_feature_ids = features(&next["plan"])
                .iter()
                .map(|feature| str_field(feature, "id"))
                .filter(|id| !id.is_empty())
                .collect();
            tool = if partial {
                "cad_restore_partial_candidate"
            } else {
                "cad_restore_best_candidate"
            }
            .into();
            action_extra = json!({
                "restored_revision":cand.get("revision"),
                "candidate_geometry_signature":cand.get("geometry_signature"),
                "accepted":cand.get("accepted")
            });
        }
        SessionCommand::RequestPreview { build } => {
            let plan = before.ok_or("当前没有 CADPlan。")?;
            let fingerprint = cad_plan::geometry_fingerprint(&plan)?;
            let req = if build {
                BackendRequest::Build {
                    revision: rev,
                    geometry_fingerprint: fingerprint,
                    plan,
                }
            } else {
                BackendRequest::Preview {
                    revision: rev,
                    geometry_fingerprint: fingerprint,
                    plan,
                }
            };
            return Ok(SessionResult {
                state: next.clone(),
                value: json!({"state":"dispatched","revision":rev,"geometry_fingerprint":match &req {BackendRequest::Preview{geometry_fingerprint,..}|BackendRequest::Build{geometry_fingerprint,..}=>geometry_fingerprint}}),
                backend: Some(req),
            });
        }
    }
    let after = next["plan"].clone();
    invalidate(&mut next);
    let transition_before = before
        .clone()
        .or_else(|| (tool == "cad_create_model").then(|| json!({"features":[]})));
    if let Some(old) = transition_before.as_ref() {
        record_transition(
            &mut next,
            old,
            &after,
            &tool,
            &affected_feature_ids,
            &changed_finding_ids,
        );
    }
    record(&mut next, &tool, action_extra);
    let value=next.get("plan").map(|p|json!({"validation":cad_plan_validation::validate_plan(p),"revision":revision(&next)})).unwrap_or(Value::Null);
    Ok(SessionResult {
        state: next,
        value,
        backend: None,
    })
}

/// Commits only a real F4 result for the exact still-current plan/revision.
pub fn complete_preview(state: &Value, outcome: BackendOutcome) -> Result<SessionResult, String> {
    let mut next = state.clone();
    let rev = revision(&next);
    let plan = next
        .get("plan")
        .filter(|p| p.is_object())
        .cloned()
        .ok_or("当前没有 CADPlan。")?;
    let sig = cad_plan::geometry_fingerprint(&plan)?;
    if outcome.revision != rev || outcome.geometry_fingerprint != sig {
        return Err("后端结果对应旧 revision 或不同 geometry fingerprint。".into());
    }
    if !outcome.output_paths.is_object()
        || outcome
            .output_paths
            .as_object()
            .is_some_and(|o| o.is_empty())
    {
        return Err("后端必须返回已生成的输出路径。".into());
    }
    if !outcome.output_paths.as_object().is_some_and(|m| {
        m.values()
            .all(|v| v.as_str().is_some_and(|s| !s.trim().is_empty()))
    }) {
        return Err("后端输出路径必须是非空字符串。".into());
    }
    let mut preview = outcome.preview;
    preview["revision"] = json!(rev);
    preview["geometry_fingerprint"] = json!(sig);
    preview["output_paths"] = outcome.output_paths.clone();
    stamp_local_geometry_checks(&mut preview, rev, &sig)?;
    next["last_preview"] = preview.clone();
    next["preview_revision"] = json!(rev);
    next["output_paths"] = outcome.output_paths;
    next["partial_preview"] = Value::Null;
    match preview
        .get("local_geometry_checks")
        .and_then(|v| v.get("state"))
        .and_then(Value::as_str)
    {
        Some("passed") => {
            let candidate = json!({"plan":plan,"revision":rev,"geometry_signature":sig,"preview":preview,"accepted":false});
            next["last_valid_candidate"] = candidate.clone();
            next["best_candidate"] = candidate.clone();
            next["best_evidence_candidate"] = candidate;
            next["partial_candidate"] = Value::Null;
        }
        Some("partial") => {
            if let Some(partial_plan) = preview
                .get("partial_candidate_plan")
                .filter(|p| p.is_object())
                .cloned()
            {
                let partial_signature = cad_plan::geometry_fingerprint(&partial_plan)?;
                next["partial_candidate"] = json!({"plan":partial_plan,"revision":rev,"geometry_signature":partial_signature,"preview":preview,"accepted":false});
            } else {
                // A partial diagnostic image is not itself a restorable CADPlan.
                // Do not relabel the full requested plan as a built prefix.
                next["partial_candidate"] = Value::Null;
            }
            let checks = preview
                .get("local_geometry_checks")
                .cloned()
                .unwrap_or_else(|| json!({"state":"partial"}));
            next["partial_preview"] = json!({
                "state":"candidate_partial_not_accepted",
                "revision":rev,
                "requested_geometry_fingerprint":sig,
                "partial_geometry_checks":checks,
                "preview_generation":preview.get("preview_generation"),
                "output_paths":preview.get("output_paths")
            });
        }
        Some("failed") => {
            next["partial_candidate"] = Value::Null;
        }
        _ => return Err("F4 preview 缺少 local_geometry_checks.state；不可据此记录候选。".into()),
    }
    record(
        &mut next,
        "f4_preview_completed",
        json!({"revision":rev,"geometry_fingerprint":sig}),
    );
    Ok(SessionResult {
        state: next,
        value: json!({"preview":preview,"revision":rev}),
        backend: None,
    })
}

fn stamp_local_geometry_checks(
    preview: &mut Value,
    rev: i64,
    fingerprint: &str,
) -> Result<(), String> {
    if preview.get("execution_engine").and_then(Value::as_str) != Some("rust_c_abi_occt") {
        return Err("F4 preview 不是 Rust/OCCT 本地几何后端生成的结果。".into());
    }
    let checks = preview
        .get_mut("local_geometry_checks")
        .filter(|value| value.is_object())
        .ok_or("F4 preview 缺少 local_geometry_checks 对象。")?;
    if checks.get("execution_engine").and_then(Value::as_str) != Some("rust_c_abi_occt")
        || checks.get("revision").and_then(Value::as_i64) != Some(rev)
        || checks.get("geometry_fingerprint").and_then(Value::as_str) != Some(fingerprint)
        || !matches!(
            checks.get("state").and_then(Value::as_str),
            Some("passed" | "partial" | "failed")
        )
    {
        return Err("F4 local_geometry_checks 缺少匹配当前版本的 Rust/OCCT 报告。".into());
    }
    let mut unsigned = checks.clone();
    unsigned
        .as_object_mut()
        .expect("local_geometry_checks was checked as object")
        .remove("host_attestation");
    let bytes = serde_json::to_vec(&unsigned).map_err(|error| error.to_string())?;
    let digest = format!("{:x}", Sha256::digest(bytes));
    checks["host_attestation"] = json!({
        "schema_version":1,
        "issuer":"native_cad_session.complete_preview",
        "revision":rev,
        "geometry_fingerprint":fingerprint,
        "report_sha256":digest
    });
    Ok(())
}

/// `turn_id` is transient tool input: keep it out of the public CADPlan source
/// schema and persist a host-generated audit row beside the successful edit.
fn strip_feature_turn_ids(
    feature: &Value,
) -> Result<(Value, std::collections::BTreeMap<String, String>), String> {
    let mut clean = feature.clone();
    let mut turn_ids = std::collections::BTreeMap::new();
    match clean.get_mut("sources") {
        Some(Value::Object(sources)) => {
            for (parameter, source) in sources {
                strip_source_turn_id(parameter, source, &mut turn_ids)?;
            }
        }
        None | Some(Value::Null) => {}
        Some(_) => return Err("feature.sources 必须是对象。".into()),
    }
    Ok((clean, turn_ids))
}

fn strip_source_update_turn_ids(
    source_updates: &Value,
) -> Result<(Value, std::collections::BTreeMap<String, String>), String> {
    if source_updates.is_null() {
        return Ok((Value::Null, std::collections::BTreeMap::new()));
    }
    let mut clean = source_updates.clone();
    let sources = clean.as_object_mut().ok_or("source_updates 必须是对象。")?;
    let mut turn_ids = std::collections::BTreeMap::new();
    for (parameter, source) in sources {
        strip_source_turn_id(parameter, source, &mut turn_ids)?;
    }
    Ok((clean, turn_ids))
}

fn strip_source_turn_id(
    parameter: &str,
    source: &mut Value,
    turn_ids: &mut std::collections::BTreeMap<String, String>,
) -> Result<(), String> {
    let Some(object) = source.as_object_mut() else {
        return Ok(());
    };
    let is_user = object.get("type").and_then(Value::as_str) == Some("user");
    let turn_id = object
        .remove("turn_id")
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default();
    if is_user {
        if turn_id.trim().is_empty() {
            return Err(format!(
                "用户来源校验失败：参数 {parameter} 缺少本次工具调用绑定的 turn_id。"
            ));
        }
        if !object
            .get("evidence")
            .and_then(Value::as_str)
            .is_some_and(|quote| !quote.trim().is_empty())
        {
            return Err(format!(
                "用户来源校验失败：参数 {parameter} 缺少逐字原文 evidence。"
            ));
        }
        turn_ids.insert(parameter.to_owned(), turn_id.trim().to_owned());
    }
    Ok(())
}

fn source_provenance_rows(
    plan: &Value,
    feature_id: &str,
    turn_ids: &std::collections::BTreeMap<String, String>,
) -> Value {
    let Some(feature) = features(plan)
        .iter()
        .find(|feature| str_field(feature, "id") == feature_id)
    else {
        return json!([]);
    };
    let params = feature.get("parameters").and_then(Value::as_object);
    let sources = feature.get("sources").and_then(Value::as_object);
    let mut rows = Vec::new();
    for (parameter, turn_id) in turn_ids {
        let Some(source) = sources.and_then(|map| map.get(parameter)) else {
            continue;
        };
        if source.get("type").and_then(Value::as_str) != Some("user") {
            continue;
        }
        let Some(value) = params.and_then(|map| map.get(parameter)) else {
            continue;
        };
        rows.push(json!({
            "feature_id":feature_id,
            "parameter":parameter,
            "turn_id":turn_id,
            "user_parameter":parameter,
            "value":value,
            "source":source
        }));
    }
    json!(rows)
}

fn trusted_user_turns(
    path: &std::path::Path,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    if path.as_os_str().is_empty() {
        return Err("用户来源校验失败：宿主未提供 user turns manifest。".into());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("用户来源校验失败：无法读取宿主 user turns manifest：{error}"))?;
    let payload: Value = serde_json::from_str(&text)
        .map_err(|error| format!("用户来源校验失败：user turns manifest JSON 无效：{error}"))?;
    let rows = payload
        .get("turns")
        .and_then(Value::as_array)
        .ok_or("用户来源校验失败：宿主 user turns manifest 缺少 turns 数组。")?;
    let mut turns = std::collections::BTreeMap::new();
    for (index, row) in rows.iter().enumerate() {
        let turn_id = row
            .get("turn_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        let Some(prompt) = row.get("raw_user_prompt").and_then(Value::as_str) else {
            return Err(format!(
                "用户来源校验失败：宿主 turn 第 {} 项没有原始用户文本。",
                index + 1
            ));
        };
        if turn_id.is_empty()
            || row
                .get("reference_ids")
                .is_some_and(|value| !value.is_null() && !value.is_array())
            || turns
                .insert(turn_id.to_owned(), prompt.to_owned())
                .is_some()
        {
            return Err(format!(
                "用户来源校验失败：宿主 turn 第 {} 项的 turn_id 重复或格式无效。",
                index + 1
            ));
        }
    }
    Ok(turns)
}

fn validate_user_source_provenance(
    plan: &Value,
    state: &Value,
    user_turns_manifest_path: &std::path::Path,
) -> Result<usize, String> {
    let mut claims = Vec::new();
    for feature in features(plan) {
        let feature_id = str_field(feature, "id");
        let parameters = feature.get("parameters").and_then(Value::as_object);
        for (parameter, source) in feature
            .get("sources")
            .and_then(Value::as_object)
            .into_iter()
            .flat_map(|sources| sources.iter())
        {
            if source.get("type").and_then(Value::as_str) != Some("user") {
                continue;
            }
            let value = parameters
                .and_then(|values| values.get(parameter))
                .ok_or_else(|| {
                    format!("用户来源校验失败：{feature_id}.{parameter} 没有对应参数值。")
                })?;
            claims.push((
                feature,
                feature_id.clone(),
                parameter.to_owned(),
                source,
                value,
            ));
        }
    }
    if claims.is_empty() {
        return Ok(0);
    }

    let turns = trusted_user_turns(user_turns_manifest_path)?;
    let actions = state.get("actions").and_then(Value::as_array);
    for (feature, feature_id, parameter, source, value) in &claims {
        if source.get("turn_id").is_some() || source.get("verified").is_some() {
            return Err(format!(
                "用户来源校验失败：{feature_id}.{parameter} 的来源对象包含临时或自报字段；turn_id 必须由宿主审计记录绑定，verified 声明不作为证据。"
            ));
        }
        let quote = source
            .get("evidence")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        if quote.is_empty() {
            return Err(format!(
                "用户来源校验失败：{feature_id}.{parameter} 缺少逐字用户原文 evidence。"
            ));
        }
        let matching_audit = actions
            .into_iter()
            .flatten()
            .flat_map(|action| {
                action
                    .get("user_source_provenance")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .find(|audit| {
                audit.get("feature_id").and_then(Value::as_str) == Some(feature_id.as_str())
                    && audit.get("parameter").and_then(Value::as_str)
                        == Some(parameter.as_str())
                    && audit.get("value").is_some_and(|recorded| deep_equal(recorded, value))
                    && audit.get("source") == Some(*source)
            })
            .ok_or_else(|| {
                format!(
                    "用户来源校验失败：{feature_id}.{parameter} 缺少宿主写入的 turn/source/数值绑定记录。"
                )
            })?;
        let turn_id = matching_audit
            .get("turn_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let prompt = turns.get(turn_id).ok_or_else(|| {
            format!(
                "用户来源校验失败：{feature_id}.{parameter} 声明的 turn_id 不存在于宿主原始会话记录。"
            )
        })?;
        if !prompt.contains(quote) {
            return Err(format!(
                "用户来源校验失败：{feature_id}.{parameter} 的 evidence 不是该宿主 turn 的逐字连续片段。"
            ));
        }
        // Only the quoted span is evidence for this parameter. A number elsewhere
        // in the same user turn cannot be borrowed to justify this claim.
        if !user_turn_supports_value(quote, value, parameter, feature, plan) {
            return Err(format!(
                "用户来源校验失败：{feature_id}.{parameter} 的数值或参数名称无法由绑定的用户原文支持。"
            ));
        }
    }
    Ok(claims.len())
}

fn user_turn_supports_value(
    quote: &str,
    value: &Value,
    parameter: &str,
    feature: &Value,
    plan: &Value,
) -> bool {
    let Some(target) = value.as_f64().filter(|_| !value.is_boolean()) else {
        // Complex values receive a literal gate only. Every numeric component
        // must occur in the quote; enums/booleans must also be present literally.
        return literal_value_occurs(quote, value);
    };
    if !target.is_finite() {
        return false;
    }
    let length_parameter = is_length_parameter(parameter);
    let unit = plan["units"]["length"].as_str().unwrap_or("mm");
    let target_value = if length_parameter {
        match length_to_mm(target, unit) {
            Some(converted) => converted,
            None => return false,
        }
    } else {
        target
    };
    if str_field(feature, "kind") == "socket_head_bolt"
        && let Some(spec_value) = crate::native_fastener_specs::user_size_value(quote, parameter)
    {
        return (spec_value - target_value).abs() <= 1e-6;
    }
    let occurrences = numeric_occurrences(quote, unit, length_parameter);
    let matching: Vec<_> = occurrences
        .iter()
        .filter(|occurrence| (occurrence.value - target_value).abs() <= 1e-6)
        .collect();
    if matching.is_empty() {
        return false;
    }
    let text = quote.to_lowercase();
    let groups = parameter_groups(parameter, feature);
    let has_target_cue = groups
        .iter()
        .any(|group| group.iter().any(|cue| text.contains(cue)));
    if !has_target_cue {
        return matching.len() == 1 && !has_any_parameter_cue(&text);
    }
    matching.iter().any(|occurrence| {
        let start = quote
            .char_indices()
            .take_while(|(byte, _)| *byte < occurrence.start_byte)
            .count()
            .saturating_sub(64);
        let number_char = quote
            .char_indices()
            .take_while(|(byte, _)| *byte < occurrence.start_byte)
            .count();
        let suffix_end = (number_char + 64).min(quote.chars().count());
        let nearby: String = quote.chars().skip(start).take(suffix_end - start).collect();
        let nearby = nearby.to_lowercase();
        groups
            .iter()
            .any(|group| group.iter().any(|cue| nearby.contains(cue)))
    })
}

#[derive(Clone, Copy)]
struct NumericOccurrence {
    value: f64,
    start_byte: usize,
}

fn length_to_mm(value: f64, plan_unit: &str) -> Option<f64> {
    let scale = match plan_unit.trim().to_ascii_lowercase().as_str() {
        "mm" | "millimeter" | "millimeters" => 1.0,
        "cm" | "centimeter" | "centimeters" => 10.0,
        "m" | "meter" | "meters" => 1000.0,
        "in" | "inch" | "inches" => 25.4,
        _ => return None,
    };
    Some(value * scale)
}

fn is_length_parameter(parameter: &str) -> bool {
    let key = parameter.trim().to_ascii_lowercase();
    if ["count", "segments", "angle", "rotation", "index"].contains(&key.as_str()) {
        return false;
    }
    [
        "length",
        "width",
        "height",
        "thickness",
        "depth",
        "diameter",
        "radius",
        "pitch",
        "margin",
        "offset",
        "distance",
        "_mm",
        "_cm",
    ]
    .iter()
    .any(|term| key.contains(term))
}

fn literal_value_occurs(text: &str, value: &Value) -> bool {
    match value {
        Value::Number(number) => number.as_f64().is_some_and(|target| {
            target.is_finite()
                && numeric_occurrences(text, "mm", false)
                    .iter()
                    .any(|row| (row.value - target).abs() <= 1e-6)
        }),
        Value::String(target) => {
            !target.is_empty() && text.to_lowercase().contains(&target.to_lowercase())
        }
        Value::Bool(target) => text.to_lowercase().contains(&target.to_string()),
        Value::Array(values) => {
            !values.is_empty() && values.iter().all(|item| literal_value_occurs(text, item))
        }
        Value::Object(values) => {
            !values.is_empty() && values.values().all(|item| literal_value_occurs(text, item))
        }
        Value::Null => false,
    }
}

fn numeric_occurrences(
    text: &str,
    plan_unit: &str,
    convert_length_units: bool,
) -> Vec<NumericOccurrence> {
    let bytes = text.as_bytes();
    let mut rows = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if !bytes[i].is_ascii_digit()
            && !(bytes[i] == b'.' && bytes.get(i + 1).is_some_and(u8::is_ascii_digit))
            && !(matches!(bytes[i], b'+' | b'-')
                && bytes.get(i + 1).is_some_and(u8::is_ascii_digit))
        {
            i += 1;
            continue;
        }
        if i > 0 && (bytes[i - 1].is_ascii_alphanumeric() || bytes[i - 1] == b'_') {
            i += 1;
            continue;
        }
        let start = i;
        if matches!(bytes[i], b'+' | b'-') {
            i += 1;
        }
        while i < bytes.len() && (bytes[i].is_ascii_digit() || bytes[i] == b'.') {
            i += 1;
        }
        if i < bytes.len() && matches!(bytes[i], b'e' | b'E') {
            let exponent = i;
            i += 1;
            if i < bytes.len() && matches!(bytes[i], b'+' | b'-') {
                i += 1;
            }
            let digits = i;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            if digits == i {
                i = exponent;
            }
        }
        let Ok(value) = text[start..i].parse::<f64>() else {
            continue;
        };
        let mut suffix = i;
        while suffix < bytes.len() && bytes[suffix].is_ascii_whitespace() {
            suffix += 1;
        }
        let rest = text[suffix..].to_ascii_lowercase();
        let scale = if !convert_length_units {
            1.0
        } else if rest.starts_with("毫米") || rest.starts_with("mm") {
            1.0
        } else if rest.starts_with("厘米") || rest.starts_with("cm") {
            10.0
        } else if rest.starts_with("英寸") || rest.starts_with("inch") || rest.starts_with("in") {
            25.4
        } else if rest.starts_with("米") || rest.starts_with("m") {
            1000.0
        } else {
            match plan_unit.trim().to_ascii_lowercase().as_str() {
                "cm" | "centimeter" | "centimeters" => 10.0,
                "m" | "meter" | "meters" => 1000.0,
                "in" | "inch" | "inches" => 25.4,
                _ => 1.0,
            }
        };
        if value.is_finite() {
            rows.push(NumericOccurrence {
                value: value * scale,
                start_byte: start,
            });
        }
    }
    rows
}

fn parameter_groups(parameter: &str, feature: &Value) -> Vec<Vec<&'static str>> {
    let key = parameter.to_ascii_lowercase();
    let mut target = match key.as_str() {
        "length" => vec!["length", "长度", "长"],
        "width" => vec!["width", "宽度", "宽"],
        "height" => vec!["height", "高度", "高"],
        "thickness" => vec!["thickness", "厚度", "板厚", "壁厚"],
        "depth" => vec!["depth", "深度", "孔深"],
        "diameter" => vec!["diameter", "直径", "孔径", "外径"],
        "radius" => vec!["radius", "半径"],
        "count" | "segments" => vec!["count", "segments", "数量", "个数", "孔数", "分段"],
        "angle" => vec!["angle", "角度"],
        "pitch" | "thread_pitch" => vec!["pitch", "thread pitch", "螺距"],
        "center" | "point" | "position" | "origin" | "base" | "entry_point" | "centerline_mm" => {
            vec![
                "center", "中心", "point", "位置", "坐标", "origin", "原点", "base", "入口",
            ]
        }
        _ => vec![],
    };
    let kind = str_field(feature, "kind");
    let operation = str_field(feature, "operation");
    if key == "height" && kind == "box" && operation == "add" {
        target.extend(["thickness", "厚度", "板厚", "壁厚"]);
    }
    if key == "height" && kind == "cylinder" && operation == "cut" {
        target.extend(["depth", "深度", "孔深"]);
    }
    if target.is_empty() {
        vec![]
    } else {
        vec![target]
    }
}

fn has_any_parameter_cue(text: &str) -> bool {
    [
        ["length", "长度", "长"].as_slice(),
        ["width", "宽度", "宽"].as_slice(),
        ["height", "高度", "高"].as_slice(),
        ["thickness", "厚度", "板厚", "壁厚"].as_slice(),
        ["depth", "深度", "孔深"].as_slice(),
        ["diameter", "直径", "孔径", "外径"].as_slice(),
        ["radius", "半径"].as_slice(),
        ["count", "segments", "数量", "个数", "孔数", "分段"].as_slice(),
        ["angle", "角度"].as_slice(),
        ["pitch", "thread pitch", "螺距"].as_slice(),
    ]
    .iter()
    .any(|group| group.iter().any(|cue| text.contains(cue)))
}

fn revision(s: &Value) -> i64 {
    s.get("revision").and_then(Value::as_i64).unwrap_or(0)
}
fn features(p: &Value) -> &[Value] {
    p.get("features")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}
fn str_field(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}
fn geometry_projection(v: &Value) -> Value {
    let mut projection = json!({"id":v.get("id"),"kind":v.get("kind"),"operation":v.get("operation"),"parameters":v.get("parameters"),"relations":v.get("relations")});
    if let Some(binding) = v.get("selection_binding") {
        projection["selection_binding"] = binding.clone();
    }
    projection
}
fn result(state: Value, backend: Option<BackendRequest>) -> SessionResult {
    SessionResult {
        value: json!({"revision":revision(&state),"plan":state.get("plan")}),
        state,
        backend,
    }
}
fn invalidate(s: &mut Value) {
    s["revision"] = json!(revision(s) + 1);
    for k in [
        "last_preview",
        "partial_preview",
        "preview_revision",
        "previews",
        "visual_review",
        "visual_reviews",
        "reference_similarity_review",
        "reference_similarity_reviews",
    ] {
        s[k] = if k.ends_with("s") {
            json!([])
        } else {
            Value::Null
        };
    }
    s["output_paths"] = json!({});
    s["finished"] = json!(false);
    s["finish_state"] = Value::Null;
    s["finish_summary"] = Value::Null;
    let active: Vec<Value> =
        if let Some(ledger) = s.get_mut("finding_ledger").and_then(Value::as_object_mut) {
            for f in ledger.values_mut() {
                if matches!(
                    f.get("status").and_then(Value::as_str),
                    Some("open" | "awaiting_verification")
                ) {
                    f["status"] = json!("awaiting_verification");
                }
            }
            ledger
                .values()
                .filter(|f| {
                    matches!(
                        f.get("status").and_then(Value::as_str),
                        Some("open" | "awaiting_verification" | "needs_reference_check")
                    )
                })
                .cloned()
                .collect()
        } else {
            Vec::new()
        };
    s["unresolved_findings"] = json!(active);
}
fn record(s: &mut Value, tool: &str, extra: Value) {
    if !s.get("actions").is_some_and(Value::is_array) {
        s["actions"] = json!([]);
    }
    let mut row = serde_json::Map::new();
    row.insert("kind".into(), json!("tool"));
    row.insert("tool".into(), json!(tool));
    row.insert("revision".into(), json!(revision(s)));
    if let Some(fields) = extra.as_object() {
        for (k, v) in fields {
            row.insert(k.clone(), v.clone());
        }
    }
    s["actions"]
        .as_array_mut()
        .unwrap()
        .push(Value::Object(row));
}
fn record_transition(
    s: &mut Value,
    before: &Value,
    after: &Value,
    tool: &str,
    feature_ids: &[String],
    finding_ids: &[String],
) {
    let a = cad_plan::geometry_fingerprint(before).unwrap_or_default();
    let b = cad_plan::geometry_fingerprint(after).unwrap_or_default();
    let rev = revision(s);
    if !s.get("geometry_signatures").is_some_and(Value::is_array) {
        s["geometry_signatures"] = json!([]);
    }
    let sigs = s["geometry_signatures"].as_array_mut().unwrap();
    sigs.push(json!({"signature":b,"revision":rev}));
    if sigs.len() > 64 {
        let n = sigs.len() - 64;
        sigs.drain(0..n);
    }
    let mut changes = serde_json::Map::new();
    for id in feature_ids {
        let old = features(before)
            .iter()
            .find(|f| str_field(f, "id") == *id)
            .and_then(|f| f.get("parameters"))
            .and_then(Value::as_object);
        let new = features(after)
            .iter()
            .find(|f| str_field(f, "id") == *id)
            .and_then(|f| f.get("parameters"))
            .and_then(Value::as_object);
        let keys: std::collections::BTreeSet<String> = old
            .into_iter()
            .flat_map(|m| m.keys().cloned())
            .chain(new.into_iter().flat_map(|m| m.keys().cloned()))
            .collect();
        for k in keys {
            let ov = old.and_then(|m| m.get(&k));
            let nv = new.and_then(|m| m.get(&k));
            if ov == nv {
                continue;
            }
            let direction = match (ov.and_then(Value::as_f64), nv.and_then(Value::as_f64)) {
                (Some(x), Some(y)) if y > x => "increased",
                (Some(x), Some(y)) if y < x => "decreased",
                (Some(_), Some(_)) => "unchanged",
                _ => "changed",
            };
            changes.insert(k, json!({"before":ov,"after":nv,"direction":direction}));
        }
    }
    if !s.get("geometry_mutations").is_some_and(Value::is_array) {
        s["geometry_mutations"] = json!([]);
    }
    let muts = s["geometry_mutations"].as_array_mut().unwrap();
    muts.push(json!({"tool":tool,"feature_ids":feature_ids,"finding_ids":finding_ids,"before_signature":a,"after_signature":b,"parameter_changes":changes,"reversed_directions":[],"revision":rev}));
    if muts.len() > 96 {
        let n = muts.len() - 96;
        muts.drain(0..n);
    }
}
fn guard_transition(
    s: &Value,
    before: &Value,
    after: &Value,
    feature_ids: &[String],
    finding_ids: &[String],
) -> Result<(), String> {
    let target = cad_plan::geometry_fingerprint(after)?;
    let current = cad_plan::geometry_fingerprint(before)?;
    if target == current {
        return Ok(());
    }
    let seen = s
        .get("geometry_signatures")
        .and_then(Value::as_array)
        .is_some_and(|rows| {
            rows.iter()
                .any(|r| r.get("signature").and_then(Value::as_str) == Some(&target))
        });
    if !seen {
        return Ok(());
    }
    let allowed = s
        .get("finding_ledger")
        .and_then(Value::as_object)
        .is_some_and(|ledger| {
            finding_ids.iter().any(|id| {
                ledger.get(id).is_some_and(|f| {
                    f.get("status").and_then(Value::as_str) == Some("open")
                        && f.get("last_mismatch_revision").and_then(Value::as_i64)
                            == Some(revision(s))
                        && feature_ids.iter().any(|fid| {
                            f.get("affected_feature_ids")
                                .and_then(Value::as_array)
                                .is_some_and(|ids| ids.iter().any(|x| x.as_str() == Some(fid)))
                        })
                })
            })
        });
    if allowed {
        Ok(())
    } else {
        Err(format!(
            "本次几何会回到历史 CADPlan 状态（geometry hash={}）。",
            &target[..12]
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};
    fn state() -> Value {
        json!({"revision":4,"plan":{"name":"p","features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{"length":{"type":"user","turn_id":"t"},"width":{"type":"image_estimate"}}}]},"visual_review":{"revision":4},"finding_ledger":{"f":{"status":"open"}},"actions":[]})
    }
    fn temp_dir(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "native-cad-session-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&path).unwrap();
        path
    }
    fn write_json(path: &std::path::Path, value: &Value) {
        std::fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
    }
    fn turns_manifest(dir: &std::path::Path, turns: Value) -> PathBuf {
        let path = dir.join("user-turns.json");
        write_json(&path, &json!({"session_id":"session-1","turns":turns}));
        path
    }
    fn geometry_report(plan: &Value, revision: i64, state: &str) -> Value {
        json!({
            "state":state,
            "execution_engine":"rust_c_abi_occt",
            "revision":revision,
            "geometry_fingerprint":cad_plan::geometry_fingerprint(plan).unwrap(),
            "valid":state=="passed",
            "closed_solid_count":if state=="passed" {1} else {0},
            "validation":{"state":if state=="passed" {"valid"} else {"partial"}},
            "hole_measurements_verified":false,
            "hole_measurement_complete":false,
            "requested_holes":[],
            "sampled_hole_evidence":[]
        })
    }
    fn with_preview(state: &Value, geometry_state: &str) -> Value {
        let plan = state.get("plan").expect("fixture has a CADPlan");
        let revision = super::revision(state);
        let report = geometry_report(plan, revision, geometry_state);
        with_report_preview(state, report)
    }
    fn with_report_preview(state: &Value, report: Value) -> Value {
        let plan = state.get("plan").expect("fixture has a CADPlan");
        let revision = super::revision(state);
        let fingerprint = cad_plan::geometry_fingerprint(plan).unwrap();
        complete_preview(state, BackendOutcome {
            revision,
            geometry_fingerprint:fingerprint,
            output_paths:json!({"preview":"fixture-preview.png","geometry_report":"fixture-geometry.json"}),
            preview:json!({
                "execution_engine":"rust_c_abi_occt",
                "local_geometry_checks":report
            }),
        }).expect("fixture preview should pass current host binding").state
    }
    fn with_aggregate(state: &Value, refs: &std::path::Path, turns: &std::path::Path) -> Value {
        crate::native_review_gate::aggregate(state, refs, turns)
            .expect("fixture aggregate should run")
            .0
    }
    fn image_state_with_coverage(
        overall_review: Option<Value>,
    ) -> (Value, PathBuf, PathBuf, PathBuf) {
        let dir = temp_dir("image-gate");
        let image = dir.join("reference.png");
        let bytes = b"test reference image";
        std::fs::write(&image, bytes).unwrap();
        let digest = format!("{:x}", Sha256::digest(bytes));
        let reference_id = format!("sha256:{digest}");
        let refs = dir.join("references.json");
        write_json(
            &refs,
            &json!({"images":[{"path":image,"id":reference_id,"sha256":digest,"filename":"reference.png","width":64,"height":64,"media_type":"image/png"}]}),
        );
        let turns = turns_manifest(&dir, json!([]));
        let plan = valid_body_plan();
        let fingerprint = cad_plan::geometry_fingerprint(&plan).unwrap();
        let observation_id = "observation-1";
        let inventory_key = format!(
            "{:x}",
            Sha256::digest(
                serde_json::to_string(&json!({"source_sha256":digest,"roi_norm":null}))
                    .unwrap()
                    .as_bytes()
            )
        );
        let state = json!({
            "revision":2,
            "has_references":true,
            "plan":plan,
            "construction_milestone":{"revision":2,"completed_feature_ids":["body"],"expected_feature_ids":["body"]},
            "reference_inventory":{},
            "reference_similarity_history":[{
                "revision":2,"recorded_at":1,"reference_id":reference_id,
                "observation_id":observation_id,
                "view":"front","view_comparable":true,"geometry_signature":fingerprint,
                "source_sha256":digest,"roi_norm":null,"covered_evidence_ids":["edge-1"],
                "stage_scope":{"revision":2,"features":[{"id":"body"}]},
                "stage_complete":true,"full_current_geometry_in_scope":true,"stage_diagnostic_only":false,
                "evidence":[{"evidence_id":"edge-1","status":"match","evidence_mapping_status":"mapped",
                    "stage_attribution_status":"verified_ids","affected_feature_ids":["body"],
                    "reference":"one visible plate edge","model":"one visible plate edge"}]
            }],
            "finding_ledger":{},"required_external_facts":{},"actions":[]
        });
        let mut state = with_preview(&state, "passed");
        state["reference_inventory"][reference_id.as_str()] = json!({
            "reference_id":reference_id,
            "source_sha256":digest,
            "observation_id":observation_id,
            "roi_norm":null,
            "roi_cache_key":inventory_key,
            "observation_valid":true,
            "visible_evidence":[{"evidence_id":"edge-1","group_id":"outline","description":"visible plate edge"}]
        });
        if let Some(review) = overall_review {
            state["latest_geometry_review"] = review;
        }
        (state, refs, turns, dir)
    }
    #[test]
    fn remove_without_findings_is_allowed_until_it_reverts_to_a_seen_geometry_signature() {
        let base = valid_body_plan();
        let extra = json!({
            "id":"pocket","kind":"pocket","operation":"cut",
            "parameters":{"length":2,"width":2,"depth":1},
            "sources":{
                "length":{"type":"image_estimate","evidence":"visual"},
                "width":{"type":"image_estimate","evidence":"visual"},
                "depth":{"type":"image_estimate","evidence":"visual"}
            }
        });
        let with_extra = cad_plan::add(&base, &extra).unwrap();
        let ordinary_state = json!({"revision":3,"plan":with_extra,"actions":[]});
        let removed = apply(
            &ordinary_state,
            SessionCommand::Remove {
                feature_id: "pocket".into(),
                finding_ids: vec![],
            },
        )
        .unwrap();
        assert_eq!(
            removed.state["plan"]["features"].as_array().unwrap().len(),
            1
        );

        let base_signature = cad_plan::geometry_fingerprint(&base).unwrap();
        let mut seen_state = ordinary_state.clone();
        seen_state["geometry_signatures"] = json!([{"signature":base_signature}]);
        let rejected = apply(
            &seen_state,
            SessionCommand::Remove {
                feature_id: "pocket".into(),
                finding_ids: vec![],
            },
        )
        .unwrap_err();
        assert!(rejected.contains("历史 CADPlan 状态"));

        seen_state["finding_ledger"] = json!({
            "f-pocket":{
                "status":"open","last_mismatch_revision":3,
                "affected_feature_ids":["pocket"]
            }
        });
        let repaired = apply(
            &seen_state,
            SessionCommand::Remove {
                feature_id: "pocket".into(),
                finding_ids: vec!["f-pocket".into()],
            },
        )
        .unwrap();
        assert_eq!(
            repaired.state["plan"]["features"].as_array().unwrap().len(),
            1
        );
    }

    #[test]
    fn failed_edit_rolls_back_and_numeric_noop_keeps_revision() {
        let s = state();
        let bad = apply(
            &s,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: json!({"length":11}),
                source_updates: Value::Null,
                remove_parameters: vec![],
                fields: json!({"note":"must rollback"}),
                finding_ids: vec![],
            },
        );
        assert!(bad.is_err());
        assert_eq!(s["revision"], 4);
        assert!(s["plan"]["features"][0].get("note").is_none());
        let noop = apply(
            &s,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: json!({"length":10.0}),
                source_updates: Value::Null,
                remove_parameters: vec![],
                fields: Value::Null,
                finding_ids: vec![],
            },
        )
        .unwrap();
        assert_eq!(noop.state["revision"], 4);
    }
    #[test]
    fn geometry_edit_preserves_other_sources_and_invalidates_old_review() {
        let s = state();
        let out = apply(
            &s,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: json!({"length":11}),
                source_updates: json!({"length":{"type":"user","evidence":"length claim","turn_id":"t2"}}),
                remove_parameters: vec![],
                fields: Value::Null,
                finding_ids: vec![],
            },
        )
        .unwrap();
        assert_eq!(out.state["revision"], 5);
        assert!(out.state["visual_review"].is_null());
        assert_eq!(
            out.state["plan"]["features"][0]["sources"]["width"]["type"],
            "image_estimate"
        );
        assert_eq!(
            out.state["finding_ledger"]["f"]["status"],
            "awaiting_verification"
        );
    }
    #[test]
    fn source_only_update_preserves_geometry_revision_and_preview() {
        let mut s = state();
        let fingerprint = cad_plan::geometry_fingerprint(&s["plan"]).unwrap();
        s["preview_revision"] = json!(4);
        s["last_preview"] = json!({"revision":4,"geometry_fingerprint":fingerprint});
        let out = apply(
            &s,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: Value::Null,
                source_updates: json!({"width":{"type":"user","evidence":"width claim","turn_id":"t2"}}),
                remove_parameters: vec![],
                fields: Value::Null,
                finding_ids: vec![],
            },
        )
        .unwrap();
        assert_eq!(out.state["revision"], 4);
        assert_eq!(out.state["last_preview"], s["last_preview"]);
        assert_eq!(
            out.state["plan"]["features"][0]["sources"]["width"]["type"],
            "user"
        );
    }
    #[test]
    fn candidate_restore_keeps_candidate_and_advances_revision() {
        let mut s = state();
        let candidate = json!({"plan":{"features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":9,"width":8,"height":5},"sources":{"length":{"type":"user"},"width":{"type":"image_estimate"}}}]},"geometry_signature":"old"});
        let candidate_signature = cad_plan::geometry_fingerprint(&candidate["plan"]).unwrap();
        s["geometry_signatures"] = json!([{"signature":candidate_signature,"revision":2}]);
        s["best_evidence_candidate"] = candidate.clone();
        let out = apply(&s, SessionCommand::RestoreBest).unwrap();
        assert_eq!(out.state["revision"], 5);
        assert_eq!(out.state["best_evidence_candidate"], candidate);
        assert_eq!(out.state["plan"]["features"][0]["parameters"]["length"], 9);
    }
    #[test]
    fn finish_gate_requires_local_geometry_pass_and_exports_are_separate() {
        let plan = valid_body_plan();
        let fingerprint = cad_plan::geometry_fingerprint(&plan).unwrap();
        let base = json!({"revision":2,"has_references":false,"plan":plan,"finding_ledger":{},"required_external_facts":{},"actions":[]});
        let s = with_preview(&base, "passed");
        let s = with_aggregate(&s, std::path::Path::new(""), std::path::Path::new(""));
        let result = finish_assessment(&s, std::path::Path::new(""), std::path::Path::new(""));
        assert!(result.is_ok(), "{:?}", result.err());
        let (next, dispatch) = result.unwrap();
        assert_eq!(dispatch.finish_state, "accepted");
        assert_eq!(next["finished"], Value::Null);
        assert_eq!(next["finish_summary"]["accepted"], true);
        assert_eq!(next["finish_summary"]["revision"], 2);
        assert_eq!(next["finish_summary"]["geometry_fingerprint"], fingerprint);
    }
    #[test]
    fn image_task_with_high_per_view_coverage_still_needs_current_aggregate_acceptance() {
        let (state, refs, turns, dir) = image_state_with_coverage(None);
        let mut summary_state = state.clone();
        let summary = crate::native_state_summary::summary(&mut summary_state, &refs, &turns)
            .expect("fixture should produce visual evidence summary");
        assert_eq!(
            summary["visual_evidence_summary"]["all_references_covered"],
            true
        );
        let error = finish_assessment(&state, &refs, &turns).unwrap_err();
        assert!(
            error.contains("尚无宿主生成的整体 geometry_review"),
            "{error}"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn stale_overall_geometry_review_cannot_unlock_image_finish() {
        let (mut state, refs, turns, dir) = image_state_with_coverage(None);
        let fingerprint = cad_plan::geometry_fingerprint(&state["plan"]).unwrap();
        state["latest_geometry_review"] = json!({
            "review_type":"geometry","revision":1,"geometry_fingerprint":fingerprint,
            "result":{"decision":"accept"}
        });
        let error = finish_assessment(&state, &refs, &turns).unwrap_err();
        assert!(
            error.contains("旧 revision") || error.contains("不同 geometry fingerprint"),
            "{error}"
        );

        state["latest_geometry_review"]["revision"] = json!(2);
        state["latest_geometry_review"]["geometry_fingerprint"] = json!("stale-fingerprint");
        let error = finish_assessment(&state, &refs, &turns).unwrap_err();
        assert!(
            error.contains("旧 revision") || error.contains("不同 geometry fingerprint"),
            "{error}"
        );

        state["latest_geometry_review"] = json!({
            "review_type":"geometry","revision":2,"geometry_fingerprint":fingerprint,
            "score":100,"confidence":1.0,"result":{"decision":"accept"}
        });
        let error = finish_assessment(&state, &refs, &turns).unwrap_err();
        assert!(
            error.contains("不一致") || error.contains("过期"),
            "{error}"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn current_overall_geometry_acceptance_unlocks_image_finish() {
        let (state, refs, turns, dir) = image_state_with_coverage(None);
        let state = with_aggregate(&state, &refs, &turns);
        let (_, dispatch) = finish_assessment(&state, &refs, &turns).unwrap();
        assert_eq!(dispatch.finish_state, "accepted");
        assert_eq!(dispatch.report["overall_geometry_review_accepted"], true);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn complete_visual_coverage_cannot_override_an_open_finding_at_finish() {
        let (mut state, refs, turns, dir) = image_state_with_coverage(None);
        state["finding_ledger"] = json!({
            "f-open":{"finding_id":"f-open","status":"open","severity":"major","item":"unresolved reference mismatch"}
        });
        let state = with_aggregate(&state, &refs, &turns);
        let mut summary_state = state.clone();
        let summary = crate::native_state_summary::summary(&mut summary_state, &refs, &turns)
            .expect("fixture should produce visual evidence summary");
        assert_eq!(
            summary["visual_evidence_summary"]["all_references_covered"],
            true
        );
        assert_eq!(state["latest_geometry_review"]["result"]["decision"], "needs_repair");

        let (finished, dispatch) = finish_assessment(&state, &refs, &turns)
            .expect("open finding should return a rejected candidate report");
        assert_eq!(dispatch.finish_state, "candidate_needs_repair");
        assert_eq!(dispatch.report["accepted"], false);
        assert_eq!(dispatch.report["visual_evidence"]["all_references_covered"], true);
        assert_eq!(finished["finish_summary"]["accepted"], false);
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn trusted_user_turn_source_can_finish_but_forged_turn_is_rejected() {
        let dir = temp_dir("user-source");
        let turns = turns_manifest(
            &dir,
            json!([{"turn_id":"turn-1","raw_user_prompt":"请把长度设为 10 mm","reference_ids":[],"intent":"model_target"}]),
        );
        let empty = json!({"revision":0,"actions":[],"has_references":false});
        let first_feature = json!({
            "id":"body","kind":"box","operation":"add",
            "parameters":{"length":10,"width":8,"height":5},
            "sources":{
                "length":{"type":"user","evidence":"长度设为 10 mm","turn_id":"turn-1"},
                "width":{"type":"image_estimate","evidence":"比例估算"},
                "height":{"type":"image_estimate","evidence":"比例估算"}
            }
        });
        let mut created = apply(
            &empty,
            SessionCommand::Create {
                name: "user-source-fixture".into(),
                first_feature,
                source_prompt: "fixture".into(),
                units: None,
            },
        )
        .unwrap()
        .state;
        assert!(created["plan"]["features"][0]["sources"]["length"]
            .get("turn_id")
            .is_none());
        assert_eq!(
            created["actions"][0]["user_source_provenance"][0]["turn_id"],
            "turn-1"
        );
        let fingerprint = cad_plan::geometry_fingerprint(&created["plan"]).unwrap();
        created = complete_preview(
            &created,
            BackendOutcome {
                revision: 1,
                geometry_fingerprint: fingerprint,
                output_paths: json!({"preview":"preview.png"}),
                preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":geometry_report(&created["plan"],1,"passed")}),
            },
        )
        .unwrap()
        .state;
        created = with_aggregate(&created, std::path::Path::new(""), &turns);
        let (_, dispatch) = finish_assessment(&created, std::path::Path::new(""), &turns).unwrap();
        assert_eq!(dispatch.finish_state, "accepted");
        assert_eq!(dispatch.report["verified_user_source_count"], 1);

        let mut missing_audit = created.clone();
        missing_audit["actions"][0]["user_source_provenance"] = json!([]);
        let error =
            finish_assessment(&missing_audit, std::path::Path::new(""), &turns).unwrap_err();
        assert!(error.contains("缺少宿主写入的"), "{error}");

        let forged = apply(
            &empty,
            SessionCommand::Create {
                name: "forged-source-fixture".into(),
                first_feature: json!({
                    "id":"body","kind":"box","operation":"add",
                    "parameters":{"length":10,"width":8,"height":5},
                    "sources":{
                        "length":{"type":"user","evidence":"长度设为 10 mm","turn_id":"not-in-manifest"},
                        "width":{"type":"image_estimate","evidence":"比例估算"},
                        "height":{"type":"image_estimate","evidence":"比例估算"}
                    }
                }),
                source_prompt: "fixture".into(),
                units: None,
            },
        )
        .unwrap()
        .state;
        let fingerprint = cad_plan::geometry_fingerprint(&forged["plan"]).unwrap();
        let forged = complete_preview(
            &forged,
            BackendOutcome {
                revision: 1,
                geometry_fingerprint: fingerprint,
                output_paths: json!({"preview":"preview.png"}),
                preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":geometry_report(&forged["plan"],1,"passed")}),
            },
        )
        .unwrap()
        .state;
        let error = finish_assessment(&forged, std::path::Path::new(""), &turns).unwrap_err();
        assert!(error.contains("不存在于宿主原始会话记录"), "{error}");

        let self_verified = apply(
            &empty,
            SessionCommand::Create {
                name: "self-verified-source".into(),
                first_feature: json!({
                    "id":"body","kind":"box","operation":"add",
                    "parameters":{"length":10,"width":8,"height":5},
                    "sources":{
                        "length":{"type":"user","evidence":"长度设为 10 mm","turn_id":"turn-1","verified":true},
                        "width":{"type":"image_estimate","evidence":"比例估算"},
                        "height":{"type":"image_estimate","evidence":"比例估算"}
                    }
                }),
                source_prompt: "fixture".into(),
                units: None,
            },
        )
        .unwrap()
        .state;
        let fingerprint = cad_plan::geometry_fingerprint(&self_verified["plan"]).unwrap();
        let self_verified = complete_preview(
            &self_verified,
            BackendOutcome {
                revision: 1,
                geometry_fingerprint: fingerprint,
                output_paths: json!({"preview":"preview.png"}),
                preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":geometry_report(&self_verified["plan"],1,"passed")}),
            },
        )
        .unwrap()
        .state;
        assert!(finish_assessment(&self_verified, std::path::Path::new(""), &turns).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn source_quote_and_numeric_value_must_match_the_bound_turn() {
        let dir = temp_dir("user-source-quote");
        let turns = turns_manifest(
            &dir,
            json!([{"turn_id":"turn-1","raw_user_prompt":"请把长度设为 10 mm","reference_ids":[]}]),
        );
        let empty = json!({"revision":0,"actions":[],"has_references":false});
        for (evidence, value) in [("宽度设为 20 mm", 10.0), ("长度设为 10 mm", 20.0)] {
            let state = apply(
                &empty,
                SessionCommand::Create {
                    name: "bad-user-source".into(),
                    first_feature: json!({
                        "id":"body","kind":"box","operation":"add",
                        "parameters":{"length":value,"width":8,"height":5},
                        "sources":{
                            "length":{"type":"user","evidence":evidence,"turn_id":"turn-1"},
                            "width":{"type":"image_estimate","evidence":"比例估算"},
                            "height":{"type":"image_estimate","evidence":"比例估算"}
                        }
                    }),
                    source_prompt: "fixture".into(),
                    units: None,
                },
            )
            .unwrap()
            .state;
            let fingerprint = cad_plan::geometry_fingerprint(&state["plan"]).unwrap();
            let state = complete_preview(
                &state,
                BackendOutcome {
                    revision: 1,
                    geometry_fingerprint: fingerprint,
                    output_paths: json!({"preview":"preview.png"}),
                    preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":geometry_report(&state["plan"],1,"passed")}),
                },
            )
            .unwrap()
            .state;
            assert!(finish_assessment(&state, std::path::Path::new(""), &turns).is_err());
        }
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn finish_rejects_stale_preview_revision_and_fingerprint() {
        let plan = valid_body_plan();
        let fingerprint = cad_plan::geometry_fingerprint(&plan).unwrap();
        let stale_revision = json!({"revision":2,"preview_revision":2,"has_references":false,"plan":plan,"last_preview":{"revision":1,"geometry_fingerprint":fingerprint,"local_geometry_checks":{"state":"passed"}},"finding_ledger":{},"required_external_facts":{},"actions":[]});
        assert!(finish_assessment(
            &stale_revision,
            std::path::Path::new(""),
            std::path::Path::new("")
        )
        .is_err());
        let stale_fingerprint = json!({"revision":2,"preview_revision":2,"has_references":false,"plan":plan,"last_preview":{"revision":2,"geometry_fingerprint":"old-fingerprint","local_geometry_checks":{"state":"passed"}},"finding_ledger":{},"required_external_facts":{},"actions":[]});
        assert!(finish_assessment(
            &stale_fingerprint,
            std::path::Path::new(""),
            std::path::Path::new("")
        )
        .is_err());
    }
    #[test]
    fn partial_preview_is_revision_bound_and_never_accepted() {
        let s = json!({"revision":2,"has_references":false,"plan":valid_body_plan(),"partial_preview":{"state":"candidate_partial_not_accepted","revision":1,"requested_geometry_fingerprint":"old","partial_geometry_checks":{"state":"partial"}},"finding_ledger":{},"required_external_facts":{},"actions":[]});
        assert!(finish_assessment(&s, std::path::Path::new(""), std::path::Path::new("")).is_err());

        let plan = valid_body_plan();
        let fingerprint = cad_plan::geometry_fingerprint(&plan).unwrap();
        let s = json!({"revision":2,"has_references":false,"plan":plan,"actions":[]});
        let partial = complete_preview(
            &s,
            BackendOutcome {
                revision: 2,
                geometry_fingerprint: fingerprint,
                output_paths: json!({"preview":"partial.png"}),
                preview: json!({"execution_engine":"rust_c_abi_occt","local_geometry_checks":geometry_report(&s["plan"],2,"partial")}),
            },
        )
        .unwrap();
        assert!(partial.state["partial_candidate"].is_null());
        let partial_state = with_aggregate(
            &partial.state,
            std::path::Path::new(""),
            std::path::Path::new(""),
        );
        let (_, dispatch) = finish_assessment(
            &partial_state,
            std::path::Path::new(""),
            std::path::Path::new(""),
        )
        .unwrap();
        assert_eq!(dispatch.finish_state, "candidate_partial_not_accepted");
        assert_eq!(dispatch.report["accepted"], false);
    }
    #[test]
    fn requested_hole_count_confidence_and_status_rows_are_not_measurements() {
        let mut plan = valid_body_plan();
        plan["features"].as_array_mut().unwrap().push(json!({
            "id":"h1","kind":"hole","operation":"cut",
            "parameters":{"diameter":2,"axis":"Y","pos":"point","point":[0,0,0],"through":true},
            "sources":{"diameter":{"type":"image_estimate","evidence":"visual"},"point":{"type":"image_estimate","evidence":"visual"}}
        }));
        let mut geometry = geometry_report(&plan, 2, "passed");
        geometry["requested_holes"] = json!([{"feature_id":"h1"}]);
        geometry["count_confidence"] = json!(1.0);
        geometry["hole_measurement_complete"] = json!(true);
        geometry["hole_instances"] =
            json!([{"feature_id":"h1","through":true,"status":"verified_open"}]);
        let summary = hole_measurement(&plan, &geometry);
        assert_eq!(summary["complete"], false);
        assert_eq!(summary["geometry_backend_verified"], false);

        let base = json!({"revision":2,"has_references":false,"plan":plan,"finding_ledger":{},"required_external_facts":{},"actions":[]});
        let state = with_report_preview(&base, geometry);
        let state = with_aggregate(&state, std::path::Path::new(""), std::path::Path::new(""));
        let (_, dispatch) =
            finish_assessment(&state, std::path::Path::new(""), std::path::Path::new("")).expect(
                "the diagnostic candidate remains reportable without claiming hole verification",
            );
        assert_eq!(dispatch.finish_state, "candidate_holes_unverified");
        assert_eq!(dispatch.report["accepted"], false);
    }
    #[test]
    fn geometry_edit_clears_stale_findings_when_ledger_is_missing() {
        let mut s = state();
        s["unresolved_findings"] = json!([{"finding_id":"stale"}]);
        s.as_object_mut().unwrap().remove("finding_ledger");
        let out = apply(
            &s,
            SessionCommand::Update {
                feature_id: "body".into(),
                parameter_updates: json!({"length":11}),
                source_updates: json!({"length":{"type":"user","evidence":"length claim","turn_id":"t2"}}),
                remove_parameters: vec![],
                fields: Value::Null,
                finding_ids: vec![],
            },
        )
        .unwrap();
        assert_eq!(out.state["unresolved_findings"], json!([]));
        assert!(out.state["finish_summary"].is_null());
    }

    #[test]
    fn aggregate_with_open_findings_can_finish_as_an_unaccepted_candidate() {
        let base = json!({
            "revision":2,"has_references":false,"plan":valid_body_plan(),"actions":[],
            "finding_ledger":{"f1":{"finding_id":"f1","status":"open","severity":"major","item":"outline mismatch"}},
            "required_external_facts":{}
        });
        let state = with_preview(&base, "passed");
        let state = with_aggregate(&state, std::path::Path::new(""), std::path::Path::new(""));
        let (_, dispatch) =
            finish_assessment(&state, std::path::Path::new(""), std::path::Path::new(""))
                .expect("a current non-accept aggregate should remain deliverable as a candidate");
        assert_eq!(dispatch.finish_state, "candidate_needs_repair");
        assert_eq!(dispatch.report["accepted"], false);
        assert_eq!(
            dispatch.report["overall_geometry_review"]["result"]["decision"],
            "needs_repair"
        );
    }

    #[test]
    fn finish_acceptance_local_geometry_construction_and_external_facts_fail_closed() {
        let refs = std::path::Path::new("");
        let turns = std::path::Path::new("");
        let base = json!({
            "revision":2,"has_references":false,"plan":valid_body_plan(),"actions":[],
            "finding_ledger":{},"required_external_facts":{}
        });
        let accepted = with_preview(&base, "passed");
        let accepted = with_aggregate(&accepted, refs, turns);
        let (_, baseline) = finish_assessment(&accepted, refs, turns).unwrap();
        assert_eq!(baseline.report["accepted"], true);

        let mut local_geometry_failure = accepted.clone();
        local_geometry_failure["last_preview"]["local_geometry_checks"]["state"] = json!("failed");
        local_geometry_failure = with_aggregate(&local_geometry_failure, refs, turns);
        let (_, geometry_dispatch) = finish_assessment(&local_geometry_failure, refs, turns)
            .expect("failed local geometry remains a reportable candidate");
        assert_eq!(geometry_dispatch.report["local_geometry_state"], "failed");
        assert_eq!(geometry_dispatch.report["accepted"], false);

        let mut construction_incomplete = accepted.clone();
        construction_incomplete["construction_milestone"] = json!({
            "revision":2,"expected_feature_ids":["body"],"completed_feature_ids":[]
        });
        construction_incomplete = with_aggregate(&construction_incomplete, refs, turns);
        let (_, construction_dispatch) = finish_assessment(&construction_incomplete, refs, turns)
            .expect("incomplete construction remains a reportable candidate");
        assert_eq!(construction_dispatch.report["construction_complete"], false);
        assert_eq!(construction_dispatch.report["accepted"], false);
        assert_eq!(
            construction_dispatch.finish_state,
            "candidate_construction_incomplete"
        );

        let mut external_fact_unknown = accepted;
        external_fact_unknown["required_external_facts"] = json!({
            "fact-1":{"status":"unknown"}
        });
        external_fact_unknown = with_aggregate(&external_fact_unknown, refs, turns);
        let (_, facts_dispatch) = finish_assessment(&external_fact_unknown, refs, turns)
            .expect("unknown external fact remains a reportable candidate");
        assert_eq!(
            facts_dispatch.report["required_external_facts"]["unknown_count"],
            1
        );
        assert_eq!(facts_dispatch.report["accepted"], false);
        assert_eq!(
            facts_dispatch.finish_state,
            "candidate_external_facts_unverified"
        );
    }

    #[test]
    fn finish_reference_coverage_failure_remains_unaccepted() {
        let (state, refs, turns, dir) = image_state_with_coverage(None);
        let mut state = with_aggregate(&state, &refs, &turns);
        let (_, baseline) = finish_assessment(&state, &refs, &turns).unwrap();
        assert_eq!(baseline.report["accepted"], true);
        state["reference_inventory"]
            .as_object_mut()
            .unwrap()
            .clear();
        let state = with_aggregate(&state, &refs, &turns);
        let (_, dispatch) = finish_assessment(&state, &refs, &turns)
            .expect("missing visual inventory remains a reportable candidate");
        assert_eq!(dispatch.report["reference_coverage"], false);
        assert_eq!(dispatch.report["accepted"], false);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn same_revision_milestone_shrink_invalidates_review_before_finish_acceptance() {
        let (state, refs, turns, dir) = image_state_with_coverage(None);
        let state = with_aggregate(&state, &refs, &turns);
        let (_, baseline) = finish_assessment(&state, &refs, &turns).unwrap();
        assert_eq!(baseline.report["accepted"], true);

        let mut changed = state;
        changed["construction_milestone"] = json!({
            "revision":2,"completed_feature_ids":[],"expected_feature_ids":[]
        });
        let changed = with_aggregate(&changed, &refs, &turns);
        let (_, dispatch) = finish_assessment(&changed, &refs, &turns).unwrap();
        assert_eq!(dispatch.report["reference_coverage"], false);
        assert_eq!(dispatch.report["accepted"], false);
        assert_eq!(dispatch.finish_state, "candidate_review_partial");

        std::fs::remove_dir_all(dir).unwrap();
        let (state, refs, turns, dir) = image_state_with_coverage(None);
        let extra = json!({
            "id":"body-2","kind":"box","operation":"add",
            "parameters":{"length":2,"width":2,"height":2},
            "sources":{"length":{"type":"image_estimate","evidence":"visual"},
                "width":{"type":"image_estimate","evidence":"visual"},
                "height":{"type":"image_estimate","evidence":"visual"}}
        });
        let mut expanded = state;
        expanded["plan"] = cad_plan::add(&expanded["plan"], &extra).unwrap();
        expanded["construction_milestone"] = json!({
            "revision":2,"completed_feature_ids":["body","body-2"],"expected_feature_ids":["body","body-2"]
        });
        expanded = with_preview(&expanded, "passed");
        let expanded = with_aggregate(&expanded, &refs, &turns);
        let (_, dispatch) = finish_assessment(&expanded, &refs, &turns).unwrap();
        assert_eq!(dispatch.report["accepted"], false);
        assert_eq!(dispatch.finish_state, "candidate_review_partial");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn quoted_count_and_angle_are_not_converted_as_lengths_and_vectors_require_every_value() {
        let plan = json!({"units":{"length":"cm"}});
        let feature = json!({"kind":"pattern","operation":"add"});
        assert!(user_turn_supports_value(
            "孔数为 2 个",
            &json!(2),
            "count",
            &feature,
            &plan
        ));
        assert!(user_turn_supports_value(
            "角度为 90°",
            &json!(90),
            "angle",
            &feature,
            &plan
        ));
        assert!(!user_turn_supports_value(
            "坐标为 (1, 2) mm",
            &json!([1, 2, 3]),
            "point",
            &feature,
            &plan
        ));
    }

    #[test]
    fn socket_bolt_user_size_quote_binds_diameter_and_length_by_position() {
        let feature = json!({"kind":"socket_head_bolt","operation":"add"});
        let plan = json!({"units":{"length":"mm"}});
        assert!(user_turn_supports_value("M5x5", &json!(5.0), "shank_d", &feature, &plan));
        assert!(user_turn_supports_value("M5x5", &json!(5.0), "length", &feature, &plan));
        assert!(!user_turn_supports_value("M5x8", &json!(8.0), "shank_d", &feature, &plan));
        assert!(!user_turn_supports_value("M5x8", &json!(5.0), "length", &feature, &plan));
    }

    #[test]
    fn create_add_update_matches_python_domain_delta_fixture() {
        let empty = json!({"revision":0,"actions":[]});
        let sources = json!({"length":{"type":"image_estimate","evidence":"visual"},"width":{"type":"image_estimate","evidence":"visual"},"height":{"type":"image_estimate","evidence":"visual"}});
        let created=apply(&empty,SessionCommand::Create{name:"probe".into(),source_prompt:"probe".into(),units:None,first_feature:json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":sources})}).unwrap().state;
        assert_eq!(created["revision"], 1);
        assert_eq!(
            created["geometry_mutations"][0]["before_signature"],
            "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
        );
        assert_eq!(
            created["geometry_mutations"][0]["after_signature"],
            "4a39b8b319381ae73879022cd2823f4b8af3e2623281254b9533a94959d06b83"
        );
        let added=apply(&created,SessionCommand::Add{feature:json!({"id":"hole1","kind":"hole","operation":"cut","parameters":{"diameter":2,"depth":3},"sources":{"diameter":{"type":"image_estimate","evidence":"visual"},"depth":{"type":"image_estimate","evidence":"visual"}}}),finding_ids:vec![]}).unwrap().state;
        assert_eq!(added["revision"], 2);
        assert_eq!(
            added["geometry_mutations"][1]["after_signature"],
            "daf24fe94bf0306b740f61887f23c90976cc505cab0ece8eb01dea0e59164715"
        );
        let updated=apply(&added,SessionCommand::Update{feature_id:"hole1".into(),parameter_updates:json!({"diameter":2.5}),source_updates:json!({"diameter":{"type":"image_estimate","evidence":"new visual"}}),remove_parameters:vec![],fields:Value::Null,finding_ids:vec![]}).unwrap().state;
        assert_eq!(updated["revision"], 3);
        assert_eq!(
            updated["geometry_mutations"][2]["after_signature"],
            "11931aab7e719ea78df80b0a54602781fe592c163fccb9feea2f7ff196d43ddb"
        );
        assert_eq!(
            updated["geometry_mutations"][2]["parameter_changes"]["diameter"]["direction"],
            "increased"
        );
        assert_eq!(
            updated["plan"]["features"][1]["sources"]["depth"]["evidence"],
            "visual"
        );
        assert_eq!(
            updated["actions"][2]["preserved_source_keys"],
            json!(["depth"])
        );
    }
    #[test]
    fn stale_or_unbacked_f4_result_rejected() {
        let s = state();
        let fp = cad_plan::geometry_fingerprint(&s["plan"]).unwrap();
        assert!(complete_preview(
            &s,
            BackendOutcome {
                revision: 3,
                geometry_fingerprint: fp.clone(),
                output_paths: json!({"preview":"x"}),
                preview: json!({})
            }
        )
        .is_err());
        assert!(complete_preview(
            &s,
            BackendOutcome {
                revision: 4,
                geometry_fingerprint: fp,
                output_paths: json!({}),
                preview: json!({})
            }
        )
        .is_err());
    }
}

#[cfg(test)]
fn valid_body_plan() -> Value {
    json!({"name":"p","schema_version":"1.0","units":{"length":"mm","angle":"deg"},"coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},"pending_questions":[],"features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{"length":{"type":"image_estimate","evidence":"visual"},"width":{"type":"image_estimate","evidence":"visual"},"height":{"type":"image_estimate","evidence":"visual"}}}]})
}

fn deep_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.as_f64() == y.as_f64(),
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(a, b)| deep_equal(a, b))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.get(k).is_some_and(|w| deep_equal(v, w)))
        }
        _ => a == b,
    }
}

#[derive(Clone, Debug)]
pub struct ExportOutcome {
    pub revision: i64,
    pub geometry_fingerprint: String,
    pub output_paths: Value,
}
#[derive(Clone, Debug)]
pub struct FinishDispatch {
    pub revision: i64,
    pub geometry_fingerprint: String,
    pub finish_state: String,
    pub report: Value,
}

/// Legacy cad_finish_model decision gates, with file export delegated to F4.
pub fn finish_assessment(
    state: &Value,
    reference_manifest_path: &std::path::Path,
    user_turns_manifest_path: &std::path::Path,
) -> Result<(Value, FinishDispatch), String> {
    let mut next = state.clone();
    let plan = next
        .get("plan")
        .filter(|p| p.is_object())
        .cloned()
        .ok_or("当前没有 CADPlan。")?;
    let validation = cad_plan_validation::validate_plan(&plan);
    if validation.get("state").and_then(Value::as_str) != Some("valid") {
        return Err(format!(
            "模型仍需修正或补充参数：{}",
            validation
                .get("errors")
                .or_else(|| validation.get("confirmations"))
                .unwrap_or(&Value::Null)
        ));
    }
    let current_revision = revision(&next);
    let current_fingerprint = cad_plan::geometry_fingerprint(&plan)?;
    let partial_preview = next.get("partial_preview").filter(|v| v.is_object());
    let partial = partial_preview
        .and_then(|v| v.get("state"))
        .and_then(Value::as_str)
        == Some("candidate_partial_not_accepted");
    if partial {
        let row = partial_preview.expect("partial preview object was checked");
        let preview_revision = row.get("revision").and_then(Value::as_i64);
        let requested_fingerprint = row
            .get("requested_geometry_fingerprint")
            .or_else(|| row.get("requested_geometry_signature"))
            .or_else(|| row.get("geometry_fingerprint"))
            .and_then(Value::as_str);
        if preview_revision != Some(current_revision)
            || requested_fingerprint != Some(current_fingerprint.as_str())
        {
            return Err("部分预览对应旧 revision 或不同 CADPlan geometry fingerprint。".into());
        }
    } else {
        let preview = next
            .get("last_preview")
            .filter(|v| v.is_object())
            .ok_or("当前 CADPlan revision 尚未生成预览或可保存的失败部分预览。")?;
        if next.get("preview_revision").and_then(Value::as_i64) != Some(current_revision)
            || preview.get("revision").and_then(Value::as_i64) != Some(current_revision)
            || preview.get("geometry_fingerprint").and_then(Value::as_str)
                != Some(current_fingerprint.as_str())
        {
            return Err("当前预览对应旧 revision 或不同 CADPlan geometry fingerprint。".into());
        }
    }
    let user_source_count =
        validate_user_source_provenance(&plan, &next, user_turns_manifest_path)?;
    let aggregate = crate::native_review_gate::evaluate(
        &next,
        reference_manifest_path,
        user_turns_manifest_path,
    )?;
    next = aggregate.state;
    let summary = aggregate.summary;
    let expected_geometry_review = aggregate.review;
    let stored_geometry_review = next
        .get("latest_geometry_review")
        .filter(|value| value.is_object());
    if stored_geometry_review != Some(&expected_geometry_review) {
        let message = match stored_geometry_review {
            None => "当前 revision 尚无宿主生成的整体 geometry_review，请先运行整体复核再保存候选。".to_owned(),
            Some(review)
                if review.get("revision").and_then(Value::as_i64) != Some(current_revision)
                    || review.get("geometry_fingerprint").and_then(Value::as_str)
                        != Some(current_fingerprint.as_str()) =>
            {
                "整体 geometry_review 绑定的是旧 revision 或不同 geometry fingerprint，请重新运行整体复核。".to_owned()
            }
            Some(_) => "整体 geometry_review 与当前宿主证据不一致或已过期，请重新运行整体复核。".to_owned(),
        };
        return Err(message);
    }
    let geometry = if partial {
        next.get("partial_preview")
            .and_then(|v| v.get("partial_geometry_checks"))
    } else {
        next.get("last_preview")
            .and_then(|v| v.get("local_geometry_checks"))
    }
    .cloned()
    .unwrap_or_else(|| json!({}));
    let geometry_passed = geometry.get("state").and_then(Value::as_str) == Some("passed");
    let holes = hole_measurement(&plan, &geometry);
    let holes_complete = holes["complete"] == true;
    let findings = next
        .get("unresolved_findings")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let visual = summary
        .get("visual_evidence_summary")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let manifest_ok = summary
        .get("reference_manifest_error")
        .is_none_or(Value::is_null)
        && summary
            .get("user_target_context_error")
            .is_none_or(Value::is_null);
    let manifest_has_images = summary
        .get("reference_images")
        .and_then(Value::as_array)
        .is_some_and(|images| !images.is_empty());
    let refs = manifest_has_images
        || summary
            .get("reference_manifest_error")
            .is_some_and(|value| !value.is_null())
        || next
            .get("has_references")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let geometry_review = Some(&expected_geometry_review);
    let geometry_review_decision = expected_geometry_review
        .get("result")
        .and_then(|result| result.get("decision"))
        .and_then(Value::as_str)
        .unwrap_or("unverified");
    let overall_geometry_review_accepted = geometry_review_decision == "accept";
    let reference_coverage =
        manifest_ok && (!refs || visual.get("all_references_covered") == Some(&Value::Bool(true)));
    let milestone = next
        .get("construction_milestone")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let expected = string_set(&milestone["expected_feature_ids"]);
    let completed = string_set(&milestone["completed_feature_ids"]);
    let construction_complete = expected.is_subset(&completed);
    let facts = next
        .get("required_external_facts")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let verified = facts
        .values()
        .filter(|v| v.get("status").and_then(Value::as_str) == Some("verified"))
        .count();
    let failed = facts
        .values()
        .filter(|v| v.get("status").and_then(Value::as_str) == Some("failed"))
        .count();
    let unknown = facts.len().saturating_sub(verified + failed);
    let external_complete = verified == facts.len();
    let accepted = overall_geometry_review_accepted
        && geometry_passed
        && holes_complete
        && reference_coverage
        && construction_complete
        && external_complete
        && findings.is_empty();
    let finish_state = if partial {
        "candidate_partial_not_accepted"
    } else if accepted {
        "accepted"
    } else if !geometry_passed {
        "candidate_geometry_unverified"
    } else if !holes_complete {
        "candidate_holes_unverified"
    } else if geometry_review_decision == "needs_repair" {
        "candidate_needs_repair"
    } else if geometry_review_decision == "partial" {
        "candidate_review_partial"
    } else if geometry_review_decision != "accept" {
        "candidate_review_unverified"
    } else if !construction_complete {
        "candidate_construction_incomplete"
    } else if !external_complete {
        "candidate_external_facts_unverified"
    } else if !findings.is_empty() {
        "candidate_with_open_findings"
    } else {
        "candidate_not_verified"
    }
    .to_string();
    let report = json!({"finish_state":finish_state,"accepted":accepted&&!partial,"revision":current_revision,"geometry_fingerprint":current_fingerprint,"local_geometry_state":geometry.get("state").or_else(||geometry.get("geometry_state")).unwrap_or(&Value::Null),"hole_measurement":holes,"visual_evidence":visual,"reference_coverage":reference_coverage,"overall_geometry_review_accepted":overall_geometry_review_accepted,"overall_geometry_review":geometry_review.cloned().unwrap_or(Value::Null),"verified_user_source_count":user_source_count,"required_external_facts":{"required_count":facts.len(),"verified_count":verified,"failed_count":failed,"unknown_count":unknown,"facts":facts},"open_finding_count":findings.len(),"unresolved_findings":findings,"construction_complete":construction_complete,"independent_review_pair_count":visual.get("comparable_review_pairs").cloned().unwrap_or(json!(0)),"catia_invoked":false});
    next["finish_state"] = json!(finish_state);
    next["finish_summary"] = report.clone();
    Ok((
        next,
        FinishDispatch {
            revision: current_revision,
            geometry_fingerprint: current_fingerprint,
            finish_state,
            report,
        },
    ))
}
/// Commit candidate export only when F4 returns for this unchanged CADPlan.
pub fn complete_export(state: &Value, outcome: ExportOutcome) -> Result<Value, String> {
    let mut next = state.clone();
    let rev = revision(&next);
    let plan = next
        .get("plan")
        .filter(|p| p.is_object())
        .ok_or("当前没有 CADPlan。")?;
    let fingerprint = cad_plan::geometry_fingerprint(plan)?;
    if outcome.revision != rev || outcome.geometry_fingerprint != fingerprint {
        return Err("导出结果对应旧 revision 或不同 geometry fingerprint。".into());
    }
    if !outcome.output_paths.as_object().is_some_and(|m| {
        !m.is_empty()
            && m.values()
                .all(|v| v.as_str().is_some_and(|s| !s.trim().is_empty()))
    }) {
        return Err("F4 必须返回非空实际输出路径。".into());
    }
    next["output_paths"] = outcome.output_paths;
    next["finished"] = json!(true);
    let finish_state = next["finish_state"].clone();
    record(
        &mut next,
        "cad_finish_model",
        json!({"finish_state":finish_state}),
    );
    Ok(next)
}
fn string_set(v: &Value) -> std::collections::BTreeSet<String> {
    v.as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}
pub(crate) fn hole_measurement(plan: &Value, geometry: &Value) -> Value {
    let holes: Vec<&Value> = features(plan)
        .iter()
        .filter(|f| {
            str_field(f, "kind") == "hole"
                && f.get("enabled").and_then(Value::as_bool) != Some(false)
        })
        .collect();
    let rows = geometry
        .get("hole_instances")
        .or_else(|| geometry.get("through_hole_checks"))
        .or_else(|| geometry.get("through_hole_mesh_checks"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let invalid = geometry
        .get("unverified_cut_intents")
        .and_then(Value::as_array)
        .is_some_and(|a| !a.is_empty());
    let active: Vec<&Value> = rows
        .iter()
        .filter(|v| v.get("status").and_then(Value::as_str) != Some("superseded"))
        .collect();
    let statuses = active.iter().all(|r| {
        let through = r.get("through").and_then(Value::as_bool);
        let status = r.get("status").and_then(Value::as_str);
        matches!(
            (through, status),
            (Some(true), Some("verified_open" | "verified_counterbore"))
                | (
                    Some(false),
                    Some("verified_blind" | "verified_blind_segment" | "verified_counterbore")
                )
        )
    });
    let measured: std::collections::BTreeSet<String> = rows
        .iter()
        .filter_map(|r| {
            r.get("feature_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect();
    let superseded: std::collections::BTreeSet<String> = rows
        .iter()
        .filter(|r| {
            r.get("status").and_then(Value::as_str) == Some("superseded")
                && r.get("superseded_by")
                    .and_then(Value::as_str)
                    .is_some_and(|id| !id.trim().is_empty())
        })
        .filter_map(|r| {
            r.get("feature_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect();
    let declared: std::collections::BTreeSet<String> = holes
        .iter()
        .map(|f| str_field(f, "id"))
        .filter(|s| !s.is_empty())
        .collect();
    let covered = declared.is_subset(&measured.union(&superseded).cloned().collect());
    // Requested hole intents and confidence scores are not measurements. The
    // backend must explicitly attest that it verified the produced geometry.
    let backend_verified = geometry.get("hole_measurements_verified") == Some(&Value::Bool(true));
    let flag = if holes.is_empty() {
        true
    } else {
        backend_verified && geometry.get("hole_measurement_complete") == Some(&Value::Bool(true))
    };
    let complete = flag && statuses && covered && !invalid;
    json!({"complete":complete,"declared_feature_ids":declared,"measured_feature_ids":measured,"instance_count":rows.len(),"active_instance_count":active.len(),"superseded_instance_count":rows.len()-active.len(),"measurement_complete_flag":flag,"geometry_backend_verified":backend_verified,"unverified_cut_intents":geometry.get("unverified_cut_intents").cloned().unwrap_or(json!([]))})
}

fn preserved_source_keys(
    plan: &Value,
    feature_id: &str,
    updates: &Value,
    removed: &[String],
) -> Vec<String> {
    let changed: std::collections::BTreeSet<String> = updates
        .as_object()
        .into_iter()
        .flat_map(|m| m.keys().cloned())
        .chain(removed.iter().cloned())
        .collect();
    features(plan)
        .iter()
        .find(|f| str_field(f, "id") == feature_id)
        .and_then(|f| f.get("sources"))
        .and_then(Value::as_object)
        .map(|m| {
            m.keys()
                .filter(|k| !changed.contains(*k))
                .cloned()
                .collect()
        })
        .unwrap_or_default()
}
