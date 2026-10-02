//! Host metadata tools; model self reports never become independent acceptance.
use crate::{cad_plan, native_web::NativeWeb};
use serde_json::{Value, json};

pub fn supports(name: &str) -> bool {
    matches!(
        name,
        "cad_set_construction_milestone"
            | "cad_record_visual_review"
            | "cad_require_external_fact"
            | "cad_attach_external_fact_evidence"
    )
}
pub fn run(
    state: &Value,
    name: &str,
    args: &Value,
    web: &NativeWeb,
) -> Result<(Value, Value), String> {
    let mut next = state.clone();
    let result = match name {
        "cad_set_construction_milestone" => {
            let completed = list(args, "completed_feature_ids")?;
            let expected = list(args, "expected_feature_ids")?;
            let actual = state["plan"]["features"]
                .as_array()
                .ok_or("里程碑需要已有计划。")?;
            for id in &completed {
                if !actual
                    .iter()
                    .any(|f| f["id"] == *id && f["enabled"] != false)
                {
                    return Err(format!("不能声称不存在或禁用的特征{id}已完成。"));
                }
            }
            let value = json!({"milestone":text(args,"milestone")?,"completed_feature_ids":completed,"expected_feature_ids":expected,"notes":args["notes"],"revision":state["revision"]});
            next["construction_milestone"] = value.clone();
            value
        }
        "cad_require_external_fact" => {
            let id = text(args, "fact_id")?;
            let impact = text(args, "impact")?;
            if !["geometry", "informational"].contains(&impact) {
                return Err("impact必须是geometry或informational。".into());
            }
            let claim = text(args, "claim")?;
            if !next["required_external_facts"].is_object() {
                next["required_external_facts"] = json!({});
            }
            if next["required_external_facts"][id]["claim"] == claim {
                next["required_external_facts"][id].clone()
            } else {
                let value = json!({"fact_id":id,"claim":claim,"impact":impact,"status":"unknown","source_verified":false});
                next["required_external_facts"][id] = value.clone();
                value
            }
        }
        "cad_attach_external_fact_evidence" => {
            let id = text(args, "fact_id")?;
            if !next["required_external_facts"][id].is_object() {
                return Err("请先登记该external fact。".into());
            }
            let quote = text(args, "quote")?;
            if quote.chars().count() < 12 {
                return Err("quote至少12字符。".into());
            }
            let evidence = web.verify_exact_quote(text(args, "result_id")?, quote)?;
            next["required_external_facts"][id]["source_evidence"] = evidence;
            next["required_external_facts"][id]["status"] = json!("verified");
            next["required_external_facts"][id]["source_verified"] = json!(true);
            next["required_external_facts"][id]["verification_scope"] = json!(
                "exact quote present in opened source; not independent verification of factual truth"
            );
            next["required_external_facts"][id].clone()
        }
        "cad_record_visual_review" => {
            let view = text(args, "view")?;
            let generation = args["preview_generation"]
                .as_u64()
                .ok_or("generation无效。")?;
            let revision = state["revision"].as_i64().ok_or("revision缺失。")?;
            let signature = cad_plan::geometry_fingerprint(&state["plan"])?;
            if !state["previews"].as_array().is_some_and(|rows| {
                rows.iter().any(|r| {
                    r["view"] == view
                        && r["revision"] == revision
                        && r["preview_generation"] == generation
                        && r["geometry_fingerprint"] == signature
                })
            }) {
                return Err("自审查必须绑定当前版本的真实单视图。".into());
            }
            let evidence = text(args, "visible_evidence")?;
            let issues = args["issues"].as_array().ok_or("issues须为数组。")?;
            let unresolved = list(args, "unresolved")?;
            let record = json!({"view":view,"revision":revision,"preview_generation":generation,"geometry_fingerprint":signature,
                "visible_evidence":evidence,"issues":issues,"unresolved":unresolved,"source":"agent_self_report","acceptance_authority":false});
            next["agent_visual_self_report"] = record.clone();
            // Independent comparison owns acceptance findings. Agent guesses
            // neither close those findings nor create impossible-to-close IDs.
            record
        }
        _ => return Err("未知metadata工具。".into()),
    };
    Ok((next, result))
}
fn text<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    args[key]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| format!("{key}须为非空文字。"))
}
fn list(args: &Value, key: &str) -> Result<Vec<String>, String> {
    match args.get(key) {
        None => Ok(vec![]),
        Some(Value::Array(a)) => a
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .ok_or("列表项须为字符串。".into())
            })
            .collect(),
        _ => Err(format!("{key}须为数组。")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn milestone_cannot_invent_completed_features() {
        let s = json!({"revision":2,"plan":{"features":[{"id":"body"}]}});
        let w = NativeWeb::new(false);
        assert!(
            run(
                &s,
                "cad_set_construction_milestone",
                &json!({"milestone":"done","completed_feature_ids":["missing"]}),
                &w
            )
            .is_err()
        );
        assert!(s.get("construction_milestone").is_none());
    }
    #[test]
    fn invented_page_quote_cannot_verify_fact() {
        let w = NativeWeb::new(false);
        let (s, _) = run(
            &json!({}),
            "cad_require_external_fact",
            &json!({"fact_id":"f","claim":"nominal standard size","impact":"geometry"}),
            &w,
        )
        .unwrap();
        assert!(run(&s,"cad_attach_external_fact_evidence",&json!({"fact_id":"f","result_id":"invented","quote":"this quote was never opened"}),&w).is_err());
        assert_eq!(s["required_external_facts"]["f"]["status"], "unknown");
    }
}
