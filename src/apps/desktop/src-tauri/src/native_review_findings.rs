//! Host-owned, revision-bound lifecycle for independently reviewed stage findings.
//! Attribution proves ID membership, not whether the model understood the image.
use crate::cad_plan;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

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

fn stable_identity(value: &Value) -> Option<&str> {
    let value = value.as_str()?.trim();
    if value.is_empty() || value.len() > 200 {
        return None;
    }
    // normalize_observation creates these fallback IDs from an array position
    // and a description hash when the provider did not supply an identity.
    // They are useful within one observation but are not safe cross-observation
    // identities because the position or description can change.
    if let Some(rest) = value.strip_prefix("observation-") {
        if let Some((index, suffix)) = rest.split_once('-') {
            if !index.is_empty()
                && index.chars().all(|ch| ch.is_ascii_digit())
                && suffix.len() == 10
                && suffix.chars().all(|ch| ch.is_ascii_hexdigit())
            {
                return None;
            }
        }
    }
    Some(value)
}

fn identity_in_inventory(inventory: &Value, evidence_id: &str) -> Option<String> {
    if inventory["observation_valid"] != true
        || !inventory.get("roi_norm").is_none_or(Value::is_null)
    {
        return None;
    }
    let rows = inventory["visible_evidence"].as_array()?;
    let matches: Vec<_> = rows
        .iter()
        .filter(|row| row["evidence_id"].as_str() == Some(evidence_id))
        .collect();
    if matches.len() != 1 {
        return None;
    }
    let identity = stable_identity(&matches[0]["source_evidence_id"])?;
    let identity_count = rows
        .iter()
        .filter(|row| stable_identity(&row["source_evidence_id"]) == Some(identity))
        .count();
    (identity_count == 1).then(|| identity.to_owned())
}

fn current_stable_identity(state: &Value, reference: &str, evidence_id: &str) -> Option<String> {
    let inventory = state.get("reference_inventory")?.get(reference)?;
    identity_in_inventory(inventory, evidence_id)
}

fn historical_stable_identity(state: &Value, finding: &Value) -> Option<String> {
    if let Some(identity) = stable_identity(&finding["stable_evidence_identity"]) {
        return Some(identity.to_owned());
    }
    let last = &finding["last_mismatch_binding"];
    let (Some(reference), Some(source_sha), Some(observation_id), Some(evidence_id)) = (
        finding["reference_id"].as_str(),
        last["source_sha256"].as_str(),
        last["observation_id"].as_str(),
        finding["evidence_id"].as_str(),
    ) else {
        return None;
    };
    let mut candidates = BTreeMap::new();
    if let Some(current) = state
        .get("reference_inventory")
        .and_then(|rows| rows.get(reference))
        .filter(|row| row["observation_id"] == observation_id && row["source_sha256"] == source_sha)
    {
        candidates.insert(observation_id.to_owned(), current.clone());
    }
    if let Some(history) = state["reference_observation_history"].as_array() {
        for row in history.iter().filter(|row| {
            row["reference_id"] == reference
                && row["source_sha256"] == source_sha
                && row["observation_id"] == observation_id
                && row["roi_norm"].is_null()
        }) {
            match candidates.get(observation_id) {
                Some(existing) if existing != row => return None,
                Some(_) => {}
                None => {
                    candidates.insert(observation_id.to_owned(), row.clone());
                }
            }
        }
    }
    if candidates.len() != 1 {
        return None;
    }
    identity_in_inventory(candidates.values().next()?, evidence_id)
        .map(|identity| identity.to_owned())
}

fn row_binding(review: &Value, evidence_id: &str, stable_identity: Option<&str>) -> Value {
    let mut value = binding(review);
    value["evidence_id"] = json!(evidence_id);
    value["stable_evidence_identity"] = stable_identity.map_or(Value::Null, |id| json!(id));
    value
}

fn same_rebind_scope(
    state: &Value,
    finding: &Value,
    review: &Value,
    affected: &BTreeSet<String>,
    identity: &str,
) -> bool {
    let old = &finding["last_mismatch_binding"];
    let (Some(old_identity), Some(source_sha), Some(target), Some(view)) = (
        historical_stable_identity(state, finding),
        old["source_sha256"].as_str(),
        old["user_target_fingerprint"].as_str(),
        old["view"].as_str(),
    ) else {
        return false;
    };
    finding["reference_id"] == review["reference_id"]
        && finding["affected_feature_ids"].as_array().is_some()
        && ids(&finding["affected_feature_ids"]) == *affected
        && old_identity == identity
        && review["source_sha256"].as_str() == Some(source_sha)
        && review["user_target_fingerprint"].as_str() == Some(target)
        && review["view"].as_str() == Some(view)
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
        let stable_id = current_stable_identity(state, reference, eid);
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
            let rebind_candidates: Vec<_> =
                stable_id.as_deref().map_or_else(Vec::new, |identity| {
                    ledger
                        .iter()
                        .filter_map(|(key, finding)| {
                            (matches!(
                                finding["status"].as_str(),
                                Some("open" | "awaiting_verification")
                            ) && same_rebind_scope(state, finding, review, &affected, identity)
                                && finding["last_mismatch_binding"]["observation_id"]
                                    != review["observation_id"])
                                .then(|| key.clone())
                        })
                        .collect()
                });
            let current_candidates: Vec<_> = ledger
                .iter()
                .filter_map(|(key, finding)| {
                    (matches!(
                        finding["status"].as_str(),
                        Some("open" | "awaiting_verification")
                    ) && finding["evidence_id"] == eid
                        && finding["reference_id"] == reference
                        && ids(&finding["affected_feature_ids"]) == affected
                        && finding["last_mismatch_binding"]
                            == row_binding(review, eid, stable_id.as_deref()))
                    .then(|| key.clone())
                })
                .collect();
            // A new observation may continue one old finding only when its
            // structured source identity maps to exactly one active finding.
            let existing_key = if ledger.contains_key(&id) {
                Some(id.clone())
            } else if current_candidates.len() == 1 {
                current_candidates.first().cloned()
            } else if rebind_candidates.len() == 1 {
                rebind_candidates.first().cloned()
            } else {
                None
            };
            let storage_key = existing_key.clone().unwrap_or_else(|| id.clone());
            let mut finding = existing_key
                .as_ref()
                .and_then(|key| ledger.get(key))
                .cloned()
                .unwrap_or_else(
                    || json!({"finding_id":id,"created_revision":revision,"history":[]}),
                );
            if !ledger.contains_key(&storage_key) {
                created.push(id.clone());
            } else if finding["status"] == "resolved" {
                reopened.push(storage_key.clone());
            }
            finding["status"] = json!("open");
            finding["reference_id"] = json!(reference);
            finding["evidence_id"] = json!(eid);
            finding["stable_evidence_identity"] =
                stable_id.as_ref().map_or(Value::Null, |value| json!(value));
            finding["affected_feature_ids"] = json!(affected);
            finding["item"] = row["item"].clone();
            finding["severity"] = row["mismatch_severity"].clone();
            finding["reference_evidence"] = row["reference"].clone();
            finding["model_evidence"] = row["model"].clone();
            finding["difference"] = row["difference"].clone();
            finding["suggested_change"] = row["suggested_change"].clone();
            finding["last_revision"] = json!(revision);
            finding["last_mismatch_revision"] = json!(revision);
            finding["last_mismatch_binding"] = row_binding(review, eid, stable_id.as_deref());
            finding["closure_binding"] = Value::Null;
            let mismatch_binding = finding["last_mismatch_binding"].clone();
            append_history(&mut finding, "mismatch", &mismatch_binding);
            ledger.insert(storage_key, finding);
        } else if may_close && row["status"] == "match" && covered.contains(eid) {
            let candidates: Vec<_> = ledger
                .iter()
                .filter_map(|(id, finding)| {
                    if !matches!(
                        finding["status"].as_str(),
                        Some("open" | "awaiting_verification")
                    ) || finding["reference_id"] != reference
                        || ids(&finding["affected_feature_ids"]) != affected
                        || finding["last_mismatch_revision"]
                            .as_i64()
                            .is_none_or(|r| revision <= r)
                    {
                        return None;
                    }
                    let old = &finding["last_mismatch_binding"];
                    let exact_observation = finding["evidence_id"] == eid
                        && old["observation_id"] == review["observation_id"]
                        && old["observation_fingerprint"] == review["observation_fingerprint"];
                    let refreshed_observation = stable_id.as_deref().is_some_and(|identity| {
                        finding["evidence_id"] != eid
                            && old["observation_id"] != review["observation_id"]
                            && same_rebind_scope(state, finding, review, &affected, identity)
                    });
                    if !exact_observation && !refreshed_observation {
                        return None;
                    }
                    if old["source_sha256"] != review["source_sha256"]
                        || old["user_target_fingerprint"] != review["user_target_fingerprint"]
                        || old["view"] != review["view"]
                        || old["pair_fingerprint"] == review["pair_fingerprint"]
                        || old["geometry_fingerprint"] == review["geometry_fingerprint"]
                        || old["preview_sha256"] == review["preview_sha256"]
                    {
                        return None;
                    }
                    Some((id.clone(), refreshed_observation))
                })
                .collect();
            // If multiple prior findings share this identity, retain them for
            // explicit adjudication rather than guessing which one was fixed.
            if candidates.len() != 1 {
                // Keep refreshes without an unambiguous structured identity visible
                // for explicit adjudication; never infer a replacement from prose.
                for finding in ledger.values_mut().filter(|finding| {
                    matches!(
                        finding["status"].as_str(),
                        Some("open" | "awaiting_verification" | "needs_reference_check")
                    ) && finding["reference_id"] == reference
                        && ids(&finding["affected_feature_ids"]) == affected
                        && finding["last_mismatch_binding"]["source_sha256"]
                            == review["source_sha256"]
                        && finding["last_mismatch_binding"]["user_target_fingerprint"]
                            == review["user_target_fingerprint"]
                        && finding["last_mismatch_binding"]["view"] == review["view"]
                        && finding["last_mismatch_binding"]["observation_id"]
                            != review["observation_id"]
                }) {
                    finding["rebind_status"] = json!("confirmation_required");
                    finding["rebind_attempt_binding"] =
                        row_binding(review, eid, stable_id.as_deref());
                }
                continue;
            }
            let (id, refreshed_observation) = candidates[0].clone();
            let finding = ledger.get_mut(&id).expect("candidate key came from ledger");
            let close_binding = row_binding(review, eid, stable_id.as_deref());
            if refreshed_observation {
                finding["rebound_from_evidence_id"] = finding["evidence_id"].clone();
                finding["evidence_id"] = json!(eid);
                finding["stable_evidence_identity"] =
                    json!(stable_id.as_deref().unwrap_or_default());
            }
            finding["status"] = json!("resolved");
            finding["last_revision"] = json!(revision);
            finding["closure_binding"] = close_binding.clone();
            append_history(finding, "resolved", &close_binding);
            resolved.push(id);
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
        let mut state = json!({"revision":2,"plan":{"schema_version":"1.0","name":"stage-fixture",
            "units":{"length":"mm","angle":"deg"},"coordinate_system":{"handedness":"right","up_axis":"Z","forward_axis":"Y"},
            "features":[{"id":"body","kind":"box","operation":"add","parameters":{"length":20,"width":10,"height":8},
                "sources":{"length":{"type":"image_estimate"},"width":{"type":"image_estimate"},"height":{"type":"image_estimate"}}},
                {"id":"hole1","kind":"hole","operation":"cut","parameters":{"diameter":2,"through":true,"pos":"point","point":[3,0,0],"axis":"Z"},
                "sources":{"diameter":{"type":"image_estimate"},"point":{"type":"image_estimate"}}}],"pending_questions":[]},
            "reference_inventory":{"ref":{"observation_valid":true,"observation_id":"obs1","source_sha256":"source-sha",
                "visible_evidence":[{"evidence_id":"hole-evidence"}]}},"actions":[],"geometry_signatures":[],"finding_ledger":{}});
        state["reference_inventory"]["ref"]["roi_norm"] = Value::Null;
        state["reference_inventory"]["ref"]["visible_evidence"][0]["source_evidence_id"] =
            json!("hole-source");
        state
    }
    fn review(state: &mut Value, status: &str, reported: Value) -> Value {
        let revision = state["revision"].clone();
        let fingerprint = cad_plan::geometry_fingerprint(&state["plan"]).unwrap();
        let inventory = state["reference_inventory"]["ref"].clone();
        let evidence_id = inventory["visible_evidence"][0]["evidence_id"].clone();
        let source_sha = inventory["source_sha256"].clone();
        let observation_id = inventory["observation_id"].clone();
        state["construction_milestone"] = json!({"revision":revision,"completed_feature_ids":["body","hole1"],"expected_feature_ids":["body","hole1"]});
        state["previews"] = json!([{"revision":revision,"preview_generation":revision,"view":"front",
            "preview_sha256":format!("preview-{fingerprint}"),"geometry_fingerprint":fingerprint}]);
        let mut norm = json!({"covered_evidence_ids":[evidence_id],"evidence":[{"evidence_id":evidence_id,
            "item":"hole position","reference":"centered hole","model":"hole shifted to the right","difference":"visible horizontal offset",
            "status":status,"mismatch_severity":"major","evidence_mapping_status":"mapped",
            "reported_affected_feature_ids":reported,"affected_feature_ids":reported}]});
        attribute_review(&mut norm, &BTreeSet::from(["body".into(), "hole1".into()]));
        let mut review = json!({"revision":revision,"preview_generation":revision,"view":"front","view_comparable":true,
            "reference_id":"ref","source_sha256":source_sha,"observation_id":observation_id,
            "observation_fingerprint":digest(&inventory),"user_target_fingerprint":"target1",
            "geometry_fingerprint":fingerprint,"preview_sha256":format!("preview-{fingerprint}"),"pair_fingerprint":format!("pair-{fingerprint}-{revision}"),
            "stage_scope":{"revision":revision,"features":[{"id":"body"},{"id":"hole1"}]},
            "stage_complete":true,"full_current_geometry_in_scope":true,"stage_diagnostic_only":false,
            "coverage_integrity":true,"coverage_status":"verified"});
        review["evidence"] = norm["evidence"].clone();
        review["covered_evidence_ids"] = norm["covered_evidence_ids"].clone();
        review
    }
    fn refresh_inventory(state: &mut Value, source_identity: Value) {
        let old = state["reference_inventory"]["ref"].clone();
        state["reference_observation_history"] = json!([old]);
        state["reference_inventory"]["ref"]["observation_id"] = json!("obs2");
        state["reference_inventory"]["ref"]["visible_evidence"][0]["evidence_id"] =
            json!("hole-evidence-v2");
        state["reference_inventory"]["ref"]["visible_evidence"][0]["source_evidence_id"] =
            source_identity;
    }
    fn edit(state: &Value, finding: &str) -> Value {
        native_cad_session::apply(state, SessionCommand::Update {
            feature_id:"hole1".into(),parameter_updates:json!({"point":[0,0,0]}),
            source_updates:json!({"point":{"type":"image_estimate","evidence":"independent mismatch finding"}}),
            remove_parameters:vec![],fields:Value::Null,finding_ids:vec![finding.into()]
        }).unwrap().state
    }
    fn open_refreshed_finding() -> (Value, String) {
        let mut state = state();
        let mismatch = review(&mut state, "mismatch", json!(["hole1"]));
        let finding = reconcile(&mut state, &mismatch).unwrap()["created"][0]
            .as_str()
            .unwrap()
            .to_owned();
        state = edit(&state, &finding);
        refresh_inventory(&mut state, json!("hole-source"));
        (state, finding)
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
    fn refreshed_observation_rebinds_same_structured_evidence_and_new_review_closes_finding() {
        let (mut state, finding) = open_refreshed_finding();
        let current = review(&mut state, "match", json!(["hole1"]));
        let result = reconcile(&mut state, &current).unwrap();

        assert_eq!(result["resolved"], json!([finding]));
        let ledger = &state["finding_ledger"][finding.as_str()];
        assert_eq!(ledger["status"], "resolved");
        assert_eq!(ledger["rebound_from_evidence_id"], "hole-evidence");
        assert_eq!(ledger["evidence_id"], "hole-evidence-v2");
        assert_eq!(ledger["closure_binding"]["observation_id"], "obs2");
        assert_eq!(ledger["closure_binding"]["evidence_id"], "hole-evidence-v2");
        let history = ledger["history"].as_array().unwrap();
        assert_eq!(history.len(), 2);
        assert_eq!(history[0]["binding"]["observation_id"], "obs1");
        assert_eq!(history[0]["binding"]["evidence_id"], "hole-evidence");
        assert_eq!(history[1]["binding"]["observation_id"], "obs2");
        assert_eq!(history[1]["binding"]["evidence_id"], "hole-evidence-v2");
        assert_eq!(state["unresolved_findings"], json!([]));
    }

    #[test]
    fn refreshed_mismatch_rebinds_but_remains_open_until_a_later_match() {
        let (mut state, finding) = open_refreshed_finding();
        let still_wrong = review(&mut state, "mismatch", json!(["hole1"]));
        let result = reconcile(&mut state, &still_wrong).unwrap();
        assert_eq!(result["resolved"], json!([]));
        assert_eq!(state["finding_ledger"][finding.as_str()]["status"], "open");
        assert_eq!(
            state["finding_ledger"][finding.as_str()]["evidence_id"],
            "hole-evidence-v2"
        );
        assert_eq!(
            state["finding_ledger"][finding.as_str()]["last_mismatch_binding"]["observation_id"],
            "obs2"
        );
        assert_eq!(state["unresolved_findings"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn refreshed_finding_does_not_close_across_sha_target_view_or_feature_changes() {
        for changed in ["sha", "target", "view", "features"] {
            let (mut state, finding) = open_refreshed_finding();
            if changed == "sha" {
                state["reference_inventory"]["ref"]["source_sha256"] =
                    json!("different-source-sha");
            }
            let mut current = review(&mut state, "match", json!(["hole1"]));
            match changed {
                "target" => current["user_target_fingerprint"] = json!("different-target"),
                "view" => {
                    current["view"] = json!("right");
                    state["previews"][0]["view"] = json!("right");
                }
                "features" => current["evidence"][0]["affected_feature_ids"] = json!(["body"]),
                _ => {}
            }
            let result = reconcile(&mut state, &current).unwrap();
            assert_eq!(result["resolved"], json!([]), "changed binding: {changed}");
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["status"],
                "awaiting_verification",
                "changed binding: {changed}"
            );
            assert_eq!(
                state["unresolved_findings"].as_array().unwrap().len(),
                1,
                "changed binding: {changed}"
            );
        }
    }

    #[test]
    fn refreshed_finding_requires_unique_nonempty_provider_identity() {
        for identity in [json!(""), json!("observation-01-abcdef1234")] {
            let (mut state, finding) = open_refreshed_finding();
            state["reference_inventory"]["ref"]["visible_evidence"][0]["source_evidence_id"] =
                identity;
            let current = review(&mut state, "match", json!(["hole1"]));
            assert_eq!(
                reconcile(&mut state, &current).unwrap()["resolved"],
                json!([])
            );
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["status"],
                "awaiting_verification"
            );
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["rebind_status"],
                "confirmation_required"
            );
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["rebind_attempt_binding"]["evidence_id"],
                "hole-evidence-v2"
            );
        }

        let (mut state, finding) = open_refreshed_finding();
        let mut duplicate = state["finding_ledger"][finding.as_str()].clone();
        duplicate["finding_id"] = json!("duplicate-old-finding");
        state["finding_ledger"]["duplicate-old-finding"] = duplicate;
        let current = review(&mut state, "match", json!(["hole1"]));
        assert_eq!(
            reconcile(&mut state, &current).unwrap()["resolved"],
            json!([])
        );
        assert_eq!(
            state["finding_ledger"][finding.as_str()]["status"],
            "awaiting_verification"
        );
        assert_eq!(
            state["finding_ledger"]["duplicate-old-finding"]["status"],
            "awaiting_verification"
        );
        assert_eq!(
            state["finding_ledger"][finding.as_str()]["rebind_status"],
            "confirmation_required"
        );
        assert_eq!(
            state["finding_ledger"]["duplicate-old-finding"]["rebind_status"],
            "confirmation_required"
        );
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

    #[test]
    fn out_of_scope_match_cannot_close_a_finding_for_a_removed_feature() {
        let mut state = state();
        let bad = review(&mut state, "mismatch", json!(["hole1"]));
        let created = reconcile(&mut state, &bad).unwrap();
        let finding_id = created["created"][0].as_str().unwrap().to_owned();
        assert_eq!(state["finding_ledger"][&finding_id]["status"], "open");

        state = native_cad_session::apply(
            &state,
            SessionCommand::Remove {
                feature_id: "hole1".into(),
                finding_ids: vec![finding_id.clone()],
            },
        )
        .unwrap()
        .state;
        let revision = state["revision"].clone();
        assert_eq!(revision, 3);
        assert_eq!(
            state["finding_ledger"][&finding_id]["status"],
            "awaiting_verification"
        );

        // A complete current review can legitimately be scoped to the remaining body,
        // but a provider must not resolve the old hole finding by attributing it to the
        // feature that is no longer present in that stage.
        let mut current = review(&mut state, "match", json!(["hole1"]));
        let body_scope = BTreeSet::from(["body".to_owned()]);
        state["construction_milestone"] = json!({
            "revision": revision,
            "completed_feature_ids": ["body"],
            "expected_feature_ids": ["body"]
        });
        current["stage_scope"] = json!({"revision":revision,"features":[{"id":"body"}]});
        current["full_current_geometry_in_scope"] = json!(true);
        current["stage_diagnostic_only"] = json!(false);
        attribute_review(&mut current, &body_scope);

        assert_eq!(current["coverage_status"], "verified");
        assert_eq!(current["evidence"][0]["status"], "match");
        assert_eq!(
            current["evidence"][0]["stage_attribution_status"],
            "unattributed"
        );
        assert_eq!(
            current["evidence"][0]["affected_feature_ids"],
            json!(["hole1"])
        );
        let output = reconcile(&mut state, &current).unwrap();
        assert_eq!(output["binding_status"], "current");
        assert_eq!(output["resolved"], json!([]));
        assert_eq!(
            state["finding_ledger"][&finding_id]["status"],
            "awaiting_verification"
        );
        assert_eq!(state["unresolved_findings"].as_array().unwrap().len(), 1);
    }
}
