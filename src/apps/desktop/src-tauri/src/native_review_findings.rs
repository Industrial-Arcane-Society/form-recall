//! Host-owned, revision-bound lifecycle for independently reviewed stage findings.
//! Attribution proves ID membership, not whether the model understood the image.
use crate::cad_plan;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::time::{SystemTime, UNIX_EPOCH};

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

fn active_finding_status(finding: &Value) -> bool {
    matches!(
        finding["status"].as_str(),
        Some("open" | "awaiting_verification" | "needs_reference_check")
    )
}

fn projected_binding(binding: &Value) -> Value {
    let mut out = serde_json::Map::new();
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
        "evidence_id",
        "stable_evidence_identity",
    ] {
        if let Some(value) = binding.get(key) {
            out.insert(key.to_owned(), value.clone());
        }
    }
    Value::Object(out)
}

/// Return the safe, finite subset needed for a human to adjudicate pending
/// observation identity bindings. This intentionally omits provider payloads.
pub fn pending_adjudications(state: &Value, generation: u64) -> Value {
    let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
    let mut pending = Vec::new();
    if let Some(ledger) = state.get("finding_ledger").and_then(Value::as_object) {
        for (id, finding) in ledger {
            if !active_finding_status(&finding)
                || finding["rebind_status"] != "confirmation_required"
            {
                continue;
            }
            let binding = &finding["rebind_attempt_binding"];
            let candidate_id = binding["evidence_id"].as_str().unwrap_or_default();
            let reference_id = binding["reference_id"].as_str().unwrap_or_default();
            let inventory = state
                .get("reference_inventory")
                .and_then(|rows| rows.get(reference_id))
                .filter(|row| {
                    row["observation_valid"] == true
                        && row["observation_id"] == binding["observation_id"]
                        && row["source_sha256"] == binding["source_sha256"]
                });
            let matches: Vec<_> = inventory
                .and_then(|row| row["visible_evidence"].as_array())
                .into_iter()
                .flatten()
                .filter(|row| row["evidence_id"].as_str() == Some(candidate_id))
                .collect();
            if matches.len() != 1 {
                continue;
            }
            let evidence = matches[0];
            pending.push(json!({
                "finding_id": id,
                "finding_status": finding["status"],
                "item": finding["item"],
                "severity": finding["severity"],
                "previous_evidence_id": finding["evidence_id"],
                "previous_observation_id": finding["last_mismatch_binding"]["observation_id"],
                "reference_evidence": finding["reference_evidence"],
                "model_evidence": finding["model_evidence"],
                "affected_feature_ids": finding["affected_feature_ids"],
                "candidate_evidence_id": candidate_id,
                "candidate_observation_id": binding["observation_id"],
                "candidate_reference_evidence": evidence["description"],
                "candidate_source_evidence_id": evidence["source_evidence_id"],
                "binding": projected_binding(binding),
                "expected_generation": generation,
                "expected_revision": revision,
            }));
        }
    }
    json!({"available": true, "generation": generation, "revision": revision, "pending": pending})
}

/// Confirm one exact old-finding/new-evidence pairing. The decision is
/// recorded as human provenance but never resolves the finding; a later
/// independently produced, current complete review must still close it.
pub fn adjudicate_rebind(state: &mut Value, request: &Value) -> Result<Value, String> {
    let finding_id = request["finding_id"].as_str().unwrap_or_default();
    let candidate_evidence_id = request["candidate_evidence_id"]
        .as_str()
        .unwrap_or_default();
    let candidate_observation_id = request["candidate_observation_id"]
        .as_str()
        .unwrap_or_default();
    let reference_id = request["reference_id"].as_str().unwrap_or_default();
    let source_sha256 = request["source_sha256"].as_str().unwrap_or_default();
    let user_target_fingerprint = request["user_target_fingerprint"]
        .as_str()
        .unwrap_or_default();
    let view = request["view"].as_str().unwrap_or_default();
    let Some(expected_revision) = request["expected_revision"].as_i64() else {
        return Err("expected_revision 必须为整数。".into());
    };
    let Some(expected_generation) = request["expected_generation"].as_u64() else {
        return Err("expected_generation 必须为非负整数。".into());
    };
    let Some(features) = request["affected_feature_ids"].as_array() else {
        return Err("affected_feature_ids 必须为数组。".into());
    };
    let affected = ids(&request["affected_feature_ids"]);
    if finding_id.is_empty()
        || candidate_evidence_id.is_empty()
        || candidate_observation_id.is_empty()
        || reference_id.is_empty()
        || source_sha256.is_empty()
        || user_target_fingerprint.is_empty()
        || view.is_empty()
        || affected.is_empty()
        || affected.len() != features.len()
    {
        return Err("人工确认缺少唯一、完整的 finding/evidence/观察/范围标识。".into());
    }
    let revision = state["revision"].as_i64().unwrap_or(-1);
    if revision != expected_revision {
        return Err(format!(
            "CAD revision 已变化：期望 {expected_revision}，当前为 {revision}。"
        ));
    }
    if state["active_model_generation"].as_u64() != Some(expected_generation) {
        return Err("活动模型 generation 已变化；请刷新人工确认列表。".into());
    }
    let Some(finding) = state
        .get_mut("finding_ledger")
        .and_then(Value::as_object_mut)
        .and_then(|ledger| ledger.get(finding_id))
        .cloned()
    else {
        return Err("指定 finding 不存在。".into());
    };
    if !active_finding_status(&finding) {
        return Err("finding 已关闭或不处于可 adjudicate 状态。".into());
    }
    if finding["rebind_status"] != "confirmation_required" {
        return Err("finding 当前没有待人工确认的重绑候选。".into());
    }
    if finding["human_adjudication"].is_object() {
        return Err("该 finding 已有人工关联确认；拒绝重复提交。".into());
    }
    let binding = &finding["rebind_attempt_binding"];
    if binding["evidence_id"].as_str() != Some(candidate_evidence_id)
        || binding["observation_id"].as_str() != Some(candidate_observation_id)
        || binding["reference_id"].as_str() != Some(reference_id)
        || binding["source_sha256"].as_str() != Some(source_sha256)
        || binding["user_target_fingerprint"].as_str() != Some(user_target_fingerprint)
        || binding["view"].as_str() != Some(view)
        || finding["reference_id"].as_str() != Some(reference_id)
        || ids(&finding["affected_feature_ids"]) != affected
        || finding["last_mismatch_binding"]["source_sha256"].as_str() != Some(source_sha256)
        || finding["last_mismatch_binding"]["user_target_fingerprint"].as_str()
            != Some(user_target_fingerprint)
        || finding["last_mismatch_binding"]["view"].as_str() != Some(view)
    {
        return Err("人工确认参数与 finding 的 pending binding 不一致。".into());
    }
    let inventory = state
        .get("reference_inventory")
        .and_then(|rows| rows.get(reference_id))
        .filter(|row| {
            row["observation_id"].as_str() == Some(candidate_observation_id)
                && row["source_sha256"].as_str() == Some(source_sha256)
                && row["observation_valid"] == true
        })
        .ok_or("候选不属于当前有效 observation。")?;
    let matches: Vec<_> = inventory["visible_evidence"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|row| row["evidence_id"].as_str() == Some(candidate_evidence_id))
        .collect();
    if matches.len() != 1 {
        return Err("候选 evidence 不在当前 observation，或 ID 不唯一。".into());
    }
    let ledger = state["finding_ledger"].as_object().unwrap();
    if ledger.iter().any(|(other_id, other)| {
        other_id != finding_id
            && active_finding_status(other)
            && other["human_adjudication"]["candidate_evidence_id"] == candidate_evidence_id
            && other["human_adjudication"]["candidate_observation_id"] == candidate_observation_id
    }) {
        return Err("此候选已人工关联到另一个未解决 finding。".into());
    }
    let binding = projected_binding(&binding);
    let at_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let finding = state["finding_ledger"].get_mut(finding_id).unwrap();
    finding["human_adjudication"] = json!({
        "decision":"same_source",
        "adjudicator":"human",
        "candidate_evidence_id":candidate_evidence_id,
        "candidate_observation_id":candidate_observation_id,
        "binding":binding,
                        "adjudicated_revision":expected_revision,
        "at_unix_ms":at_ms,
    });
    finding["rebind_status"] = json!("human_confirmed");
    finding["status"] = json!("awaiting_verification");
    let event = finding["human_adjudication"].clone();
    append_history(finding, "human_rebind_confirmed", &event);
    let unresolved: Vec<_> = state["finding_ledger"]
        .as_object()
        .into_iter()
        .flat_map(|ledger| ledger.values())
        .filter(|row| active_finding_status(row))
        .cloned()
        .collect();
    state["unresolved_findings"] = json!(unresolved);
    Ok(
        json!({"finding_id":finding_id,"candidate_evidence_id":candidate_evidence_id,
        "candidate_observation_id":candidate_observation_id,"status":"awaiting_verification",
        "resolved":false,"adjudication":event}),
    )
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
                    let claimed_by_another_finding = ledger.iter().any(|(other_id, other)| {
                        other_id != id
                            && active_finding_status(other)
                            && other["human_adjudication"]["candidate_evidence_id"] == eid
                            && other["human_adjudication"]["candidate_observation_id"]
                                == review["observation_id"]
                    });
                    if claimed_by_another_finding {
                        return None;
                    }
                    let old = &finding["last_mismatch_binding"];
                    let exact_observation = finding["evidence_id"] == eid
                        && old["observation_id"] == review["observation_id"]
                        && old["observation_fingerprint"] == review["observation_fingerprint"];
                    let human_authorized = finding["human_adjudication"]["decision"]
                        == "same_source"
                        && finding["human_adjudication"]["candidate_evidence_id"] == eid
                        && finding["human_adjudication"]["candidate_observation_id"]
                            == review["observation_id"]
                        && finding["human_adjudication"]["binding"]["source_sha256"]
                            == review["source_sha256"]
                        && finding["human_adjudication"]["binding"]["user_target_fingerprint"]
                            == review["user_target_fingerprint"]
                        && finding["human_adjudication"]["binding"]["view"] == review["view"]
                        && ids(&finding["affected_feature_ids"]) == affected;
                    let after_human_adjudication = finding["human_adjudication"].is_null()
                        || (revision
                            >= finding["human_adjudication"]["adjudicated_revision"]
                                .as_i64()
                                .unwrap_or(i64::MAX)
                            && review["preview_generation"].as_i64().unwrap_or(-1)
                                > finding["human_adjudication"]["binding"]["preview_generation"]
                                    .as_i64()
                                    .unwrap_or(i64::MAX)
                            && review["pair_fingerprint"]
                                != finding["human_adjudication"]["binding"]["pair_fingerprint"]);
                    let refreshed_observation = finding["evidence_id"] != eid
                        && old["observation_id"] != review["observation_id"]
                        && (human_authorized
                            || stable_id.as_deref().is_some_and(|identity| {
                                same_rebind_scope(state, finding, review, &affected, identity)
                            }));
                    if (!exact_observation && !refreshed_observation) || !after_human_adjudication {
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

    fn request_for(state: &Value, finding_id: &str, generation: u64) -> Value {
        let rows = pending_adjudications(state, generation);
        let row = rows["pending"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["finding_id"] == finding_id)
            .unwrap();
        let binding = &row["binding"];
        json!({
            "expected_generation":generation,
            "expected_revision":state["revision"],
            "finding_id":finding_id,
            "candidate_evidence_id":row["candidate_evidence_id"],
            "candidate_observation_id":row["candidate_observation_id"],
            "reference_id":binding["reference_id"],
            "source_sha256":binding["source_sha256"],
            "user_target_fingerprint":binding["user_target_fingerprint"],
            "view":binding["view"],
            "affected_feature_ids":row["affected_feature_ids"],
        })
    }

    #[test]
    fn human_adjudication_handles_zero_and_multiple_automatic_matches_without_resolving() {
        for (candidate_identity, duplicate_finding) in
            [(json!(""), false), (json!("hole-source"), true)]
        {
            let (mut state, finding) = open_refreshed_finding();
            state["active_model_generation"] = json!(9);
            state["reference_inventory"]["ref"]["visible_evidence"][0]["source_evidence_id"] =
                candidate_identity;
            if duplicate_finding {
                let mut duplicate = state["finding_ledger"][finding.as_str()].clone();
                duplicate["finding_id"] = json!("duplicate-old-finding");
                state["finding_ledger"]["duplicate-old-finding"] = duplicate;
            }
            let current = review(&mut state, "match", json!(["hole1"]));
            assert_eq!(
                reconcile(&mut state, &current).unwrap()["resolved"],
                json!([])
            );
            let rows = pending_adjudications(&state, 9)["pending"]
                .as_array()
                .unwrap()
                .clone();
            assert_eq!(rows.len(), if duplicate_finding { 2 } else { 1 });
            assert!(
                rows.iter()
                    .all(|row| row["candidate_evidence_id"] == "hole-evidence-v2")
            );

            let request = request_for(&state, &finding, 9);
            let mut stale_generation = request.clone();
            stale_generation["expected_generation"] = json!(8);
            let mut stale_revision = request.clone();
            stale_revision["expected_revision"] = json!(state["revision"].as_i64().unwrap() + 1);
            for stale in [stale_generation, stale_revision] {
                let mut candidate = state.clone();
                assert!(adjudicate_rebind(&mut candidate, &stale).is_err());
                assert_eq!(candidate["finding_ledger"], state["finding_ledger"]);
            }

            for (key, value) in [
                ("candidate_evidence_id", json!("not-pending")),
                ("source_sha256", json!("different-sha")),
                ("user_target_fingerprint", json!("different-target")),
                ("view", json!("right")),
                ("affected_feature_ids", json!(["body"])),
            ] {
                let mut invalid = request.clone();
                invalid[key] = value;
                let mut candidate = state.clone();
                assert!(
                    adjudicate_rebind(&mut candidate, &invalid).is_err(),
                    "field {key}"
                );
                assert_eq!(candidate["finding_ledger"], state["finding_ledger"]);
            }

            let result = adjudicate_rebind(&mut state, &request).unwrap();
            assert_eq!(result["resolved"], false);
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["status"],
                "awaiting_verification"
            );
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["human_adjudication"]["adjudicator"],
                "human"
            );
            assert_eq!(
                state["unresolved_findings"].as_array().unwrap().len(),
                if duplicate_finding { 2 } else { 1 }
            );
            assert!(
                state["finding_ledger"][finding.as_str()]["history"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|entry| entry["event"] == "human_rebind_confirmed")
            );

            let mut duplicate_request = state.clone();
            assert!(adjudicate_rebind(&mut duplicate_request, &request).is_err());

            // Replaying the pre-confirmation review cannot close it. A later
            // preview and complete review at the same CAD revision can.
            let mut replay = state.clone();
            assert_eq!(
                reconcile(&mut replay, &current).unwrap()["resolved"],
                json!([])
            );
            assert_eq!(
                replay["finding_ledger"][finding.as_str()]["status"],
                "awaiting_verification"
            );
            state["revision"] = json!(state["revision"].as_i64().unwrap() + 1);
            let later = review(&mut state, "match", json!(["hole1"]));
            let outcome = reconcile(&mut state, &later).unwrap();
            assert_eq!(outcome["resolved"], json!([finding]));
            assert_eq!(
                state["finding_ledger"][finding.as_str()]["status"],
                "resolved"
            );
        }
    }

    #[test]
    fn human_adjudication_rejects_closed_and_already_claimed_candidates() {
        let (mut state, finding) = open_refreshed_finding();
        state["active_model_generation"] = json!(4);
        state["reference_inventory"]["ref"]["visible_evidence"][0]["source_evidence_id"] =
            json!("");
        let current = review(&mut state, "match", json!(["hole1"]));
        reconcile(&mut state, &current).unwrap();
        let request = request_for(&state, &finding, 4);

        let mut closed = state.clone();
        closed["finding_ledger"][finding.as_str()]["status"] = json!("resolved");
        assert!(adjudicate_rebind(&mut closed, &request).is_err());

        let mut claimed = state.clone();
        claimed["finding_ledger"]["other"] = json!({
            "status":"awaiting_verification",
            "human_adjudication":{
                "candidate_evidence_id":"hole-evidence-v2",
                "candidate_observation_id":"obs2"
            }
        });
        assert!(adjudicate_rebind(&mut claimed, &request).is_err());
        assert_eq!(
            claimed["finding_ledger"][finding.as_str()]["status"],
            "awaiting_verification"
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
