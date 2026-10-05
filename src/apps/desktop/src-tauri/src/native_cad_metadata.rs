//! Host metadata tools; model self reports never become independent acceptance.
use crate::{cad_plan, native_web::NativeWeb};
use serde_json::{json, Value};

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
            let completed = validated_id_list(args, "completed_feature_ids")?;
            let expected = validated_id_list(args, "expected_feature_ids")?;
            let actual = state["plan"]["features"]
                .as_array()
                .ok_or("里程碑需要已有计划。")?;
            if completed.is_empty() {
                return Err("已有CADPlan时completed_feature_ids不能为空。".into());
            }
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
fn validated_id_list(args: &Value, key: &str) -> Result<Vec<String>, String> {
    if args.get(key).is_none() {
        return Err(format!("必须提供{key}数组。"));
    }
    let values = list(args, key)?;
    let mut seen = std::collections::BTreeSet::new();
    for id in &values {
        if id.trim() != id
            || id.is_empty()
            || id.chars().count() > 160
            || id.chars().any(char::is_control)
        {
            return Err(format!("{key}包含格式无效的特征ID。"));
        }
        if !seen.insert(id.as_str()) {
            return Err(format!("{key}不能包含重复特征ID。"));
        }
    }
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn milestone_cannot_invent_completed_features() {
        let s = json!({"revision":2,"plan":{"features":[{"id":"body"}]}});
        let w = NativeWeb::new(false);
        assert!(run(
            &s,
            "cad_set_construction_milestone",
            &json!({
                "milestone": "done",
                "completed_feature_ids": ["missing"],
                "expected_feature_ids": ["missing"]
            }),
            &w
        )
        .is_err());
        assert!(s.get("construction_milestone").is_none());
    }
    #[test]
    fn milestone_rejects_malformed_or_duplicate_ids_but_allows_future_expected_ids() {
        let s = json!({"revision":2,"plan":{"features":[{"id":"body"}]}});
        let w = NativeWeb::new(false);
        for ids in [
            json!(["body", "body"]),
            json!([" body"]),
            json!(["bad\nID"]),
        ] {
            assert!(run(
                &s,
                "cad_set_construction_milestone",
                &json!({
                    "milestone": "partial",
                    "completed_feature_ids": ids,
                    "expected_feature_ids": []
                }),
                &w
            )
            .is_err());
        }
        assert!(run(&s, "cad_set_construction_milestone", &json!({"milestone":"partial","completed_feature_ids":["body"],"expected_feature_ids":["future-hole"]}), &w).is_ok());
    }

    #[test]
    fn milestone_requires_both_scope_arrays_and_rejects_empty_completed_scope() {
        let s = json!({"revision":2,"plan":{"features":[{"id":"body"}]}});
        let w = NativeWeb::new(false);

        for args in [
            json!({
                "milestone": "partial",
                "expected_feature_ids": ["body"]
            }),
            json!({
                "milestone": "partial",
                "completed_feature_ids": ["body"]
            }),
            json!({
                "milestone": "partial",
                "completed_feature_ids": [],
                "expected_feature_ids": ["body"]
            }),
        ] {
            assert!(
                run(&s, "cad_set_construction_milestone", &args, &w).is_err(),
                "accepted {args}"
            );
        }
        assert!(s.get("construction_milestone").is_none());
    }

    #[test]
    fn milestone_accepts_nonempty_partial_and_full_scopes() {
        let w = NativeWeb::new(false);
        let partial_state = json!({
            "revision": 2,
            "plan": {"features": [{"id": "body"}]}
        });
        let (partial_next, partial) = run(
            &partial_state,
            "cad_set_construction_milestone",
            &json!({
                "milestone": "body",
                "completed_feature_ids": ["body"],
                "expected_feature_ids": ["body", "holes"]
            }),
            &w,
        )
        .expect("nonempty partial scope should be accepted");
        assert_eq!(partial["completed_feature_ids"], json!(["body"]));
        assert_eq!(partial["expected_feature_ids"], json!(["body", "holes"]));
        assert_eq!(partial_next["construction_milestone"], partial);

        let full_state = json!({
            "revision": 3,
            "plan": {"features": [{"id": "body"}, {"id": "holes"}]}
        });
        let (_, full) = run(
            &full_state,
            "cad_set_construction_milestone",
            &json!({
                "milestone": "complete",
                "completed_feature_ids": ["body", "holes"],
                "expected_feature_ids": ["body", "holes"]
            }),
            &w,
        )
        .expect("full scope should be accepted");
        assert_eq!(full["completed_feature_ids"], json!(["body", "holes"]));
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
        assert!(run(
            &s,
            "cad_attach_external_fact_evidence",
            &json!({"fact_id":"f","result_id":"invented","quote":"this quote was never opened"}),
            &w
        )
        .is_err());
        assert_eq!(s["required_external_facts"]["f"]["status"], "unknown");
    }
}
