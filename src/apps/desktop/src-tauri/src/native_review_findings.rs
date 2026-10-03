//! Host-owned, revision-bound lifecycle for independently reviewed stage findings.
//! Attribution proves ID membership, not whether the model understood the image.
use crate::cad_plan;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

fn ids(value: &Value) -> BTreeSet<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
fn digest(value: &Value) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).unwrap_or_default())
    )
}
fn nonempty(value: &Value) -> bool {
    value.as_str().is_some_and(|text| !text.trim().is_empty())
}

/// Keep model-reported IDs before filtering. A future/empty attribution cannot
/// become a current-stage defect even if its evidence ID maps to the inventory.
pub fn attribute_review(review: &mut Value, scope: &BTreeSet<String>) {
    let mut rejected = BTreeSet::new();
    for row in review["evidence"].as_array_mut().into_iter().flatten() {
        let reported = ids(&row["reported_affected_feature_ids"]);
        let attributed = !reported.is_empty() && reported.is_subset(scope);
        row["stage_attribution_status"] = json!(if row["status"] == "out_of_scope" {
            "out_of_scope"
        } else if attributed {
            "verified_ids"
        } else {
            "unattributed"
        });
        if matches!(
            row["status"].as_str(),
            Some("mismatch" | "partial_mismatch")
        ) && !attributed
        {
            row["reported_status"] = row["status"].clone();
            row["status"] = json!("unknown");
            row["scope_diagnostic"] = json!(
                "Defect attribution is empty or includes features outside the verified construction stage."
            );
            if let Some(id) = row["evidence_id"].as_str() {
                rejected.insert(id.to_owned());
            }
        }
    }
    let covered = ids(&review["covered_evidence_ids"]);
    review["covered_evidence_ids"] = json!(covered.difference(&rejected).collect::<Vec<_>>());
}

fn binding_is_current(state: &Value, review: &Value) -> bool {
    let Some(revision) = review["revision"].as_i64().filter(|r| *r >= 0) else {
        return false;
    };
    if state["revision"].as_i64() != Some(revision)
        || review["view_comparable"] != true
        || review["partial_candidate_diagnostic_only"] == true
        || !nonempty(&review["pair_fingerprint"])
        || !nonempty(&review["user_target_fingerprint"])
        || cad_plan::geometry_fingerprint(&state["plan"])
            .ok()
            .as_deref()
            != review["geometry_fingerprint"].as_str()
    {
        return false;
    }
    let milestone = &state["construction_milestone"];
    let completed = ids(&milestone["completed_feature_ids"]);
    let scope = review["stage_scope"]["features"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|row| row["id"].as_str())
                .map(str::to_owned)
                .collect::<BTreeSet<_>>()
        })
        .unwrap_or_default();
    if milestone["revision"].as_i64() != Some(revision)
        || review["stage_scope"]["revision"].as_i64() != Some(revision)
        || scope != completed
        || !completed.iter().all(|id| {
            state["plan"]["features"]
                .as_array()
                .is_some_and(|rows| rows.iter().any(|f| f["id"] == *id && f["enabled"] != false))
        })
    {
        return false;
    }
    let inventory = &state["reference_inventory"][review["reference_id"].as_str().unwrap_or("")];
    if inventory["observation_valid"] != true
        || !nonempty(&inventory["observation_id"])
        || review["observation_id"] != inventory["observation_id"]
        || review["source_sha256"] != inventory["source_sha256"]
        || review["observation_fingerprint"].as_str() != Some(digest(inventory).as_str())
    {
        return false;
    }
    state["previews"].as_array().is_some_and(|rows| {
        rows.iter().any(|preview| {
            preview["revision"] == review["revision"]
                && preview["preview_generation"] == review["preview_generation"]
                && preview["view"] == review["view"]
                && nonempty(&preview["preview_sha256"])
                && preview["preview_sha256"] == review["preview_sha256"]
                && preview["geometry_fingerprint"] == review["geometry_fingerprint"]
        })
    })
}

fn binding(review: &Value) -> Value {
    let mut value = json!({});
    for key in [
        "revision",
        "geometry_fingerprint",
        "preview_generation",
        "preview_sha256",
        "pair_fingerprint",
        "view",
        "observation_id",
        "observation_fingerprint",
        "reference_id",
        "source_sha256",
        "user_target_fingerprint",
    ] {
        value[key] = review[key].clone();
    }
    value
}

/// Called only after the independent provider response has been normalized.
/// Invalid/stale review bindings do not mutate the ledger. Closure requires an
/// exact match in a new revision, a new preview/pair, the same view/source/target,
/// and host-certified complete stage coverage.
pub fn reconcile(state: &mut Value, review: &Value) -> Result<Value, String> {
    if !binding_is_current(state, review) {
        return Ok(
            json!({"binding_status":"rejected", "created":[], "reopened":[], "resolved":[]}),
        );
    }
    if state
        .get("finding_ledger")
        .is_some_and(|v| !v.is_null() && !v.is_object())
    {
        return Err("视觉 finding ledger 格式无效；未覆盖原记录。".into());
    }
    let reference = review["reference_id"].as_str().unwrap_or("");
    let inventory_ids: BTreeSet<_> = state["reference_inventory"][reference]["visible_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| row["evidence_id"].as_str())
        .map(str::to_owned)
        .collect();
    let revision = review["revision"].as_i64().unwrap();
    let current_binding = binding(review);
    let completed = ids(&state["construction_milestone"]["completed_feature_ids"]);
    let expected = ids(&state["construction_milestone"]["expected_feature_ids"]);
    let all_enabled: BTreeSet<_> = state["plan"]["features"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|f| f["enabled"] != false)
        .filter_map(|f| f["id"].as_str())
        .map(str::to_owned)
        .collect();
    let may_close = expected.is_subset(&completed)
        && all_enabled.is_subset(&completed)
        && review["stage_complete"] == true
        && review["full_current_geometry_in_scope"] == true
        && review["stage_diagnostic_only"] == false
        && review["coverage_integrity"] == true
        && review["coverage_status"] == "verified";
    let covered = ids(&review["covered_evidence_ids"]);
    let evidence = review["evidence"].as_array().cloned().unwrap_or_default();
    let mut ledger = state["finding_ledger"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    let (mut created, mut reopened, mut resolved) = (Vec::new(), Vec::new(), Vec::new());
    for row in &evidence {
        let eid = row["evidence_id"].as_str().unwrap_or("");
        let affected = ids(&row["affected_feature_ids"]);
        let unique = evidence.iter().filter(|r| r["evidence_id"] == eid).count() == 1;
        if !unique
            || !inventory_ids.contains(eid)
            || row["evidence_mapping_status"] != "mapped"
            || row["stage_attribution_status"] != "verified_ids"
            || affected.is_empty()
            || !affected.is_subset(&completed)
        {
            continue;
        }
        if matches!(
            row["status"].as_str(),
            Some("mismatch" | "partial_mismatch")
        ) {
            let id = format!(
                "visual-{}",
                digest(&json!([
                    reference,
                    review["observation_id"],
                    eid,
                    review["view"],
                    affected
                ]))
            );
            let mut finding = ledger.get(&id).cloned().unwrap_or_else(|| {
                json!({
                    "finding_id":id,"created_revision":revision,"history":[]
                })
            });
            if !ledger.contains_key(&id) {
                created.push(id.clone());
            } else if finding["status"] == "resolved" {
                reopened.push(id.clone());
            }
            finding["status"] = json!("open");
            finding["reference_id"] = json!(reference);
            finding["evidence_id"] = json!(eid);
            finding["affected_feature_ids"] = json!(affected);
            finding["item"] = row["item"].clone();
            finding["severity"] = row["mismatch_severity"].clone();
            finding["reference_evidence"] = row["reference"].clone();
            finding["model_evidence"] = row["model"].clone();
            finding["difference"] = row["difference"].clone();
            finding["suggested_change"] = row["suggested_change"].clone();
            finding["last_revision"] = json!(revision);
            finding["last_mismatch_revision"] = json!(revision);
            finding["last_mismatch_binding"] = current_binding.clone();
            finding["closure_binding"] = Value::Null;
            append_history(&mut finding, "mismatch", &current_binding);
            ledger.insert(id, finding);
        } else if may_close && row["status"] == "match" && covered.contains(eid) {
            for (id, finding) in &mut ledger {
                let old = &finding["last_mismatch_binding"];
                if !matches!(
                    finding["status"].as_str(),
                    Some("open" | "awaiting_verification")
                ) || finding["evidence_id"] != eid
                    || finding["reference_id"] != reference
                    || ids(&finding["affected_feature_ids"]) != affected
                    || finding["last_mismatch_revision"]
                        .as_i64()
                        .is_none_or(|r| revision <= r)
                    || old["observation_id"] != review["observation_id"]
                    || old["observation_fingerprint"] != review["observation_fingerprint"]
                    || old["source_sha256"] != review["source_sha256"]
                    || old["user_target_fingerprint"] != review["user_target_fingerprint"]
                    || old["view"] != review["view"]
                    || old["pair_fingerprint"] == review["pair_fingerprint"]
                    || old["geometry_fingerprint"] == review["geometry_fingerprint"]
                    || old["preview_sha256"] == review["preview_sha256"]
                {
                    continue;
                }
                finding["status"] = json!("resolved");
                finding["last_revision"] = json!(revision);
                finding["closure_binding"] = current_binding.clone();
                append_history(finding, "resolved", &current_binding);
                resolved.push(id.clone());
            }
        }
    }
    let active: Vec<_> = ledger
        .values()
        .filter(|f| {
            matches!(
                f["status"].as_str(),
                Some("open" | "awaiting_verification" | "needs_reference_check")
            )
        })
        .cloned()
        .collect();
    state["finding_ledger"] = json!(ledger);
    state["unresolved_findings"] = json!(active);
    Ok(
        json!({"binding_status":"current", "created":created, "reopened":reopened, "resolved":resolved}),
    )
}

fn append_history(finding: &mut Value, event: &str, binding: &Value) {
    if !finding["history"].is_array() {
        finding["history"] = json!([]);
    }
    let row = json!({"event":event,"binding":binding});
    let history = finding["history"].as_array_mut().unwrap();
    if history.last() != Some(&row) {
        history.push(row);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::native_cad_session::{self, SessionCommand};

    fn state() -> Value {
        json!({"revision":2,"plan":{"schema_version":"1.0","name":"stage-fixture",
            "units":{"length":"mm","angle":"deg"},"coordinate_system":{"handedness":"right","up_axis":"Z","forward_axis":"Y"},
            "features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":20,"width":10,"height":8},
                "sources":{"length":{"type":"image_estimate"},"width":{"type":"image_estimate"},"height":{"type":"image_estimate"}}},
                {"id":"hole1","kind":"hole","operation":"cut","parameters":{"diameter":2,"through":true,"pos":"point","point":[3,0,0],"axis":"Z"},
                "sources":{"diameter":{"type":"image_estimate"},"point":{"type":"image_estimate"}}}],"pending_questions":[]},
            "reference_inventory":{"ref":{"observation_valid":true,"observation_id":"obs1","source_sha256":"source-sha",
                "visible_evidence":[{"evidence_id":"hole-evidence"}]}},"actions":[],"geometry_signatures":[],"finding_ledger":{}})
    }
    fn review(state: &mut Value, status: &str, reported: Value) -> Value {
        let revision = state["revision"].clone();
        let fingerprint = cad_plan::geometry_fingerprint(&state["plan"]).unwrap();
        state["construction_milestone"] = json!({"revision":revision,"completed_feature_ids":["body","hole1"],"expected_feature_ids":["body","hole1"]});
        state["previews"] = json!([{"revision":revision,"preview_generation":revision,"view":"front",
            "preview_sha256":format!("preview-{fingerprint}"),"geometry_fingerprint":fingerprint}]);
        let mut norm = json!({"covered_evidence_ids":["hole-evidence"],"evidence":[{"evidence_id":"hole-evidence",
            "item":"hole position","reference":"centered hole","model":"hole shifted to the right","difference":"visible horizontal offset",
            "status":status,"mismatch_severity":"major","evidence_mapping_status":"mapped",
            "reported_affected_feature_ids":reported,"affected_feature_ids":reported}]});
        attribute_review(&mut norm, &BTreeSet::from(["body".into(), "hole1".into()]));
        let mut review = json!({"revision":revision,"preview_generation":revision,"view":"front","view_comparable":true,
            "reference_id":"ref","source_sha256":"source-sha","observation_id":"obs1",
            "observation_fingerprint":digest(&state["reference_inventory"]["ref"]),"user_target_fingerprint":"target1",
            "geometry_fingerprint":fingerprint,"preview_sha256":format!("preview-{fingerprint}"),"pair_fingerprint":format!("pair-{fingerprint}-{revision}"),
            "stage_scope":{"revision":revision,"features":[{"id":"body"},{"id":"hole1"}]},
            "stage_complete":true,"full_current_geometry_in_scope":true,"stage_diagnostic_only":false,
            "coverage_integrity":true,"coverage_status":"verified"});
        review["evidence"] = norm["evidence"].clone();
        review["covered_evidence_ids"] = norm["covered_evidence_ids"].clone();
        review
    }
    fn edit(state: &Value, finding: &str) -> Value {
        native_cad_session::apply(state, SessionCommand::Update {
            feature_id:"hole1".into(),parameter_updates:json!({"point":[0,0,0]}),
            source_updates:json!({"point":{"type":"image_estimate","evidence":"independent mismatch finding"}}),
            remove_parameters:vec![],fields:Value::Null,finding_ids:vec![finding.into()]
        }).unwrap().state
    }

    #[test]
    fn unbuilt_hole_mismatch_is_unattributed_and_never_creates_a_finding() {
        let mut state = state();
        state["plan"]["features"].as_array_mut().unwrap().pop();
        let mut norm = json!({"covered_evidence_ids":["hole-evidence"],"evidence":[{"evidence_id":"hole-evidence",
            "status":"mismatch","reported_affected_feature_ids":["hole1"],"affected_feature_ids":[],"evidence_mapping_status":"mapped"}]});
        attribute_review(&mut norm, &BTreeSet::from(["body".into()]));
        assert_eq!(norm["evidence"][0]["status"], "unknown");
        assert_eq!(norm["evidence"][0]["reported_status"], "mismatch");
        assert_eq!(norm["covered_evidence_ids"], json!([]));
        let mut r = review(&mut state, "mismatch", json!(["hole1"]));
        state["construction_milestone"] = json!({"revision":2,"completed_feature_ids":["body"],"expected_feature_ids":["body","hole1"]});
        r["stage_scope"] = json!({"revision":2,"features":[{"id":"body"}]});
        r["evidence"] = norm["evidence"].clone();
        r["stage_complete"] = json!(false);
        reconcile(&mut state, &r).unwrap();
        assert_eq!(state["finding_ledger"], json!({}));
    }

    #[test]
    fn built_misaligned_hole_creates_waits_for_edit_and_closes_only_on_new_review() {
        let mut state = state();
        let r2 = review(&mut state, "mismatch", json!(["hole1"]));
        let output = reconcile(&mut state, &r2).unwrap();
        let fid = output["created"][0].as_str().unwrap();
        assert_eq!(state["finding_ledger"][fid]["status"], "open");
        assert_eq!(state["unresolved_findings"].as_array().unwrap().len(), 1);
        state = edit(&state, fid);
        assert_eq!(state["revision"], 3);
        assert_eq!(
            state["finding_ledger"][fid]["status"],
            "awaiting_verification"
        );
        let r3 = review(&mut state, "match", json!(["hole1"]));
        assert_eq!(
            reconcile(&mut state, &r3).unwrap()["resolved"],
            json!([fid])
        );
        assert_eq!(state["finding_ledger"][fid]["status"], "resolved");
        assert_eq!(
            state["finding_ledger"][fid]["closure_binding"]["revision"],
            3
        );
        assert_eq!(
            state["finding_ledger"][fid]["history"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(state["unresolved_findings"], json!([]));
    }

    #[test]
    fn stale_unmapped_out_of_scope_and_incomplete_reviews_never_close() {
        let mut state = state();
        let old = review(&mut state, "mismatch", json!(["hole1"]));
        let output = reconcile(&mut state, &old).unwrap();
        let fid = output["created"][0].as_str().unwrap();
        state = edit(&state, fid);
        let current = review(&mut state, "match", json!(["hole1"]));
        for invalid in [
            {
                let mut r = current.clone();
                r["revision"] = json!(2);
                r
            },
            {
                let mut r = current.clone();
                r["preview_sha256"] = old["preview_sha256"].clone();
                r
            },
            {
                let mut r = current.clone();
                r["evidence"][0]["evidence_mapping_status"] = json!("unmapped");
                r
            },
            {
                let mut r = current.clone();
                r["evidence"][0]["status"] = json!("out_of_scope");
                r
            },
            {
                let mut r = current.clone();
                r["covered_evidence_ids"] = json!([]);
                r
            },
            {
                let mut r = current.clone();
                r["stage_complete"] = json!(false);
                r
            },
            {
                let mut r = current.clone();
                r["coverage_status"] = json!("partial");
                r
            },
            {
                let mut r = current.clone();
                r["full_current_geometry_in_scope"] = json!(false);
                r
            },
            {
                let mut r = current.clone();
                r["partial_candidate_diagnostic_only"] = json!(true);
                r
            },
            {
                let mut r = current.clone();
                r["observation_id"] = json!("old-observation");
                r
            },
            {
                let mut r = current.clone();
                r["user_target_fingerprint"] = json!("new-target");
                r
            },
            {
                let mut r = current.clone();
                r["evidence"][0]["stage_attribution_status"] = json!("unattributed");
                r
            },
        ] {
            let mut candidate = state.clone();
            assert_eq!(
                reconcile(&mut candidate, &invalid).unwrap()["resolved"],
                json!([])
            );
            assert_eq!(candidate["finding_ledger"], state["finding_ledger"]);
        }
    }

    #[test]
    fn exact_same_revision_match_cannot_close_and_duplicate_reviews_do_not_duplicate_findings() {
        let mut state = state();
        let bad = review(&mut state, "mismatch", json!(["hole1"]));
        let first = reconcile(&mut state, &bad).unwrap();
        let fid = first["created"][0].as_str().unwrap();
        assert_eq!(reconcile(&mut state, &bad).unwrap()["created"], json!([]));
        let good = review(&mut state, "match", json!(["hole1"]));
        assert_eq!(reconcile(&mut state, &good).unwrap()["resolved"], json!([]));
        assert_eq!(
            state["finding_ledger"][fid]["history"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }
}
