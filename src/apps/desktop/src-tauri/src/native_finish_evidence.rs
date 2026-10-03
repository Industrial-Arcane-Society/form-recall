//! Deterministic binding between a CAD finish result and the current acceptance evidence.
//!
//! Only the SHA-256 digest is persisted in `finish_summary`. The canonical payload stays
//! in memory and includes current host-computed evidence, not model-written scores.

use crate::{cad_plan, native_review_gate};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::path::Path;

const SNAPSHOT_SCHEMA: &str = "native_finish_evidence_v1";

pub(crate) struct CurrentSnapshot {
    pub fingerprint: String,
    pub summary: Value,
}

/// Recompute the complete host evidence snapshot used to bind a finish result.
///
/// The snapshot covers the complete CADPlan (including source provenance), the freshly
/// evaluated aggregate review, original reference manifest rows and visual summary,
/// findings, construction milestones, external facts, and current local geometry checks.
/// It returns only the digest alongside in-memory summaries; callers persist the digest.
pub(crate) fn current_snapshot(
    state: &Value,
    reference_manifest_path: &Path,
    user_turns_manifest_path: &Path,
) -> Result<CurrentSnapshot, String> {
    let plan = state
        .get("plan")
        .filter(|value| value.is_object())
        .ok_or("验收证据快照需要当前 CADPlan。")?;
    let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0);
    let geometry_fingerprint = cad_plan::geometry_fingerprint(plan)?;
    let evaluation =
        native_review_gate::evaluate(state, reference_manifest_path, user_turns_manifest_path)?;
    let summary = evaluation.summary;
    let aggregate = evaluation.review;

    // Use host-normalized summaries for manifest inputs: file paths and image bytes are
    // never persisted, while validated hashes/dimensions and target-turn evidence are bound.
    let original_references = json!({
        "reference_images": summary.get("reference_images").cloned().unwrap_or_else(|| json!([])),
        "reference_manifest_error": summary.get("reference_manifest_error").cloned().unwrap_or(Value::Null),
        "user_target_context_error": summary.get("user_target_context_error").cloned().unwrap_or(Value::Null),
        "authoritative_user_turns": summary.get("authoritative_user_turns").cloned().unwrap_or_else(|| json!([])),
    });
    let visual_evidence = json!({
        "summary": summary.get("visual_evidence_summary").cloned().unwrap_or(Value::Null),
        "inventory": state.get("reference_inventory").cloned().unwrap_or_else(|| json!({})),
        "observation_history": state.get("reference_observation_history").cloned().unwrap_or_else(|| json!([])),
        "similarity_history": state.get("reference_similarity_history").cloned().unwrap_or_else(|| json!([])),
        "similarity_reviews": state.get("reference_similarity_reviews").cloned().unwrap_or_else(|| json!({})),
        "similarity_review": state.get("reference_similarity_review").cloned().unwrap_or(Value::Null),
        "review_attempts": state.get("reference_similarity_attempts").cloned().unwrap_or_else(|| json!([])),
        "visual_reviews": state.get("visual_reviews").cloned().unwrap_or_else(|| json!({})),
    });
    let local_geometry = json!({
        "preview_revision": state.get("preview_revision").cloned().unwrap_or(Value::Null),
        "last_preview": state.get("last_preview").cloned().unwrap_or(Value::Null),
        "partial_preview": state.get("partial_preview").cloned().unwrap_or(Value::Null),
    });
    let payload = json!({
        "schema": SNAPSHOT_SCHEMA,
        "revision": revision,
        "geometry_fingerprint": geometry_fingerprint,
        "cad_plan": plan,
        "latest_host_aggregate": aggregate,
        "original_reference_manifest": original_references,
        "visual_evidence": visual_evidence,
        "finding_ledger": state.get("finding_ledger").cloned().unwrap_or_else(|| json!({})),
        "construction_milestone": state.get("construction_milestone").cloned().unwrap_or(Value::Null),
        "required_external_facts": state.get("required_external_facts").cloned().unwrap_or_else(|| json!({})),
        "local_geometry": local_geometry,
    });
    let canonical = canonical_json(&payload)?;
    let fingerprint = hex_sha256(&canonical);

    Ok(CurrentSnapshot {
        fingerprint,
        summary,
    })
}

/// Canonical JSON uses recursively lexicographically sorted object keys, preserves array
/// order, emits compact UTF-8 JSON, and uses serde_json's stable shortest number format.
fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&canonicalize(value)).map_err(|error| error.to_string())
}

fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Array(values) => Value::Array(values.iter().map(canonicalize).collect()),
        Value::Object(object) => {
            let mut keys: Vec<&String> = object.keys().collect();
            keys.sort_unstable();
            let mut sorted = Map::new();
            for key in keys {
                sorted.insert(key.clone(), canonicalize(&object[key]));
            }
            Value::Object(sorted)
        }
        scalar => scalar.clone(),
    }
}

fn hex_sha256(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{canonical_json, current_snapshot};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "native-finish-evidence-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn plan() -> Value {
        crate::cad_plan::create(
            "fixture",
            json!({"id":"body","kind":"box","operation":"add","parameters":{"length":10,"width":8,"height":5},"sources":{"length":{"type":"user","turn_id":"target"},"width":{"type":"image_estimate"}}}),
            "fixture",
            None,
        )
    }

    fn base_state() -> Value {
        json!({
            "revision": 7,
            "plan": plan(),
            "preview_revision": 7,
            "preview_generation": 3,
            "last_preview": {"revision":7,"geometry_fingerprint":crate::cad_plan::geometry_fingerprint(&plan()).unwrap(),"local_geometry_checks":{"state":"passed","checks":[{"name":"closed_solid","passed":true}] }},
            "finding_ledger": {},
            "required_external_facts": {},
            "construction_milestone": {"expected_feature_ids":["body"],"completed_feature_ids":["body"]},
            "reference_inventory": {},
            "reference_observation_history": [],
            "reference_similarity_history": [],
            "reference_similarity_reviews": {},
            "reference_similarity_attempts": [],
            "visual_reviews": {}
        })
    }

    fn manifests(root: &Path) -> (PathBuf, PathBuf, String) {
        let image = root.join("original.png");
        let bytes = b"sanitized-reference-fixture";
        fs::write(&image, bytes).unwrap();
        let hash = format!("{:x}", Sha256::digest(bytes));
        let reference_id = format!("sha256:{hash}");
        let refs = root.join("reference-images.json");
        fs::write(
            &refs,
            serde_json::to_vec(&json!({"images":[{"path":image,"id":reference_id,"sha256":hash,"filename":"original.png","width":64,"height":48,"media_type":"image/png"}]})).unwrap(),
        )
        .unwrap();
        let turns = root.join("user-turns.json");
        fs::write(
            &turns,
            serde_json::to_vec(&json!({"session_id":"fixture","turns":[{"turn_id":"target","raw_user_prompt":"Make the referenced bracket.","reference_ids":[reference_id],"intent":"model_target"}]})).unwrap(),
        )
        .unwrap();
        (refs, turns, reference_id)
    }

    fn snapshot(state: &Value, refs: &Path, turns: &Path) -> String {
        current_snapshot(state, refs, turns).unwrap().fingerprint
    }

    #[test]
    fn canonical_json_sorts_nested_object_keys_but_preserves_array_order() {
        let left: Value =
            serde_json::from_str(r#"{"z":1,"a":{"y":2,"b":3},"items":[{"z":1,"a":2},4]}"#).unwrap();
        let right: Value =
            serde_json::from_str(r#"{"items":[{"a":2,"z":1},4],"a":{"b":3,"y":2},"z":1}"#).unwrap();
        assert_eq!(
            canonical_json(&left).unwrap(),
            canonical_json(&right).unwrap()
        );
        assert_ne!(
            canonical_json(&json!({"items":[1,2]})).unwrap(),
            canonical_json(&json!({"items":[2,1]})).unwrap()
        );
    }

    #[test]
    fn same_revision_acceptance_snapshot_changes_when_any_evidence_changes() {
        let root = temp_dir();
        let (refs, turns, reference_id) = manifests(&root);
        let original_manifest: Value = serde_json::from_slice(&fs::read(&refs).unwrap()).unwrap();
        let mut base = base_state();
        let original = snapshot(&base, &refs, &turns);

        let mut cases: Vec<(&str, Value, Value)> = Vec::new();

        let mut inventory = base.clone();
        inventory["reference_inventory"][&reference_id] = json!({"reference_id":reference_id,"source_sha256":reference_id.trim_start_matches("sha256:"),"observation_valid":true,"visible_evidence":[{"evidence_id":"hole-1","description":"visible bore"}]});
        cases.push(("visual inventory", inventory, original_manifest.clone()));

        let mut review = base.clone();
        review["reference_similarity_history"] =
            json!([{"revision":7,"view":"front","summary":"hole alignment reviewed"}]);
        cases.push(("visual review", review, original_manifest.clone()));

        let mut finding = base.clone();
        finding["finding_ledger"] =
            json!({"finding-1":{"status":"open","severity":"major","item":"unresolved alignment"}});
        cases.push(("finding ledger", finding, original_manifest.clone()));

        let mut external_fact = base.clone();
        external_fact["required_external_facts"] =
            json!({"standard":"ISO 4017","status":"unknown"});
        cases.push(("external facts", external_fact, original_manifest.clone()));

        let mut milestone = base.clone();
        milestone["construction_milestone"]["completed_feature_ids"] = json!([]);
        cases.push((
            "construction milestone",
            milestone,
            original_manifest.clone(),
        ));

        let mut geometry = base.clone();
        geometry["last_preview"]["local_geometry_checks"]["checks"][0]["passed"] = json!(false);
        cases.push(("local geometry", geometry, original_manifest.clone()));

        let mut changed_manifest = original_manifest.clone();
        changed_manifest["images"][0]["width"] = json!(65);
        cases.push((
            "original reference manifest",
            base.clone(),
            changed_manifest,
        ));

        for (label, changed_state, changed_manifest) in cases {
            let changed_manifest_path = root.join(format!("manifest-{label}.json"));
            fs::write(
                &changed_manifest_path,
                serde_json::to_vec(&changed_manifest).unwrap(),
            )
            .unwrap();
            let changed = snapshot(&changed_state, &changed_manifest_path, &turns);
            assert_ne!(
                original, changed,
                "{label} must invalidate an accepted finish at the same CAD revision"
            );
        }

        // The unchanged snapshot remains deterministic and therefore preserves the host binding.
        assert_eq!(original, snapshot(&base, &refs, &turns));
        base["plan"]["features"][0]["sources"]["width"]["type"] = json!("user");
        assert_ne!(
            original,
            snapshot(&base, &refs, &turns),
            "source provenance is part of the bound CADPlan"
        );
        fs::remove_dir_all(root).unwrap();
    }
}
