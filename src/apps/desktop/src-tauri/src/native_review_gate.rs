//! Host-owned aggregate review gate for geometry, sampled-hole, and image evidence.
//! Model-provided scores and standalone `match` claims are never acceptance inputs.
use crate::{cad_plan, native_cad_session, native_state_summary};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::Path;

const ISSUER: &str = "native_review_gate";
const REVIEW_SCHEMA_VERSION: u64 = 1;
const GEOMETRY_ISSUER: &str = "native_cad_session.complete_preview";

pub(crate) struct AggregateEvaluation {
    pub state: Value,
    pub summary: Value,
    pub review: Value,
}

/// Builds a current host aggregate, stores it in the returned state, and returns its result.
pub fn aggregate(
    state: &Value,
    reference_manifest_path: &Path,
    user_turns_manifest_path: &Path,
) -> Result<(Value, Value), String> {
    let evaluation = evaluate(state, reference_manifest_path, user_turns_manifest_path)?;
    let mut next = evaluation.state;
    next["latest_geometry_review"] = evaluation.review.clone();
    Ok((next, evaluation.review))
}

/// Recomputes the deterministic review used by both the aggregate tool and finish gate.
pub(crate) fn evaluate(
    state: &Value,
    reference_manifest_path: &Path,
    user_turns_manifest_path: &Path,
) -> Result<AggregateEvaluation, String> {
    let mut normalized_state = state.clone();
    let summary = native_state_summary::summary(
        &mut normalized_state,
        reference_manifest_path,
        user_turns_manifest_path,
    )?;
    let plan = normalized_state
        .get("plan")
        .filter(|value| value.is_object())
        .ok_or("整体几何复核需要当前 CADPlan。")?;
    let revision = normalized_state
        .get("revision")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let fingerprint = cad_plan::geometry_fingerprint(plan)?;
    let geometry = local_geometry_evidence(&normalized_state, revision, &fingerprint);
    let checks = geometry.get("checks").cloned().unwrap_or(Value::Null);
    let holes = summarize_holes(plan, &checks);
    let references = summarize_references(&normalized_state, &summary);
    let proportions = crate::native_proportion_gate::evaluate(&normalized_state, reference_manifest_path, revision);
    let mut findings = summary
        .get("unresolved_findings")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    let geometry_passed = geometry["binding_valid"] == true && geometry["passed"] == true;
    let holes_complete = holes["measurement_complete"] == true;
    let visual_complete = references["visual_coverage_complete"] == true;
    let active_reference_mismatch = references["mismatch_count"].as_u64().unwrap_or(0) > 0;
    let hard_geometry_failure =
        geometry["binding_valid"] == true && matches!(geometry["state"].as_str(), Some("failed"));
    let mut reasons = Vec::new();
    let mut needs_repair = false;
    let mut has_partial_evidence = false;

    if geometry["binding_valid"] != true {
        reasons.push("local_geometry_checks_missing_stale_or_unattested");
    } else if geometry["passed"] != true {
        reasons.push("local_geometry_checks_not_passed");
        needs_repair |= hard_geometry_failure;
        has_partial_evidence |= !hard_geometry_failure;
    }

    if holes["declared_feature_count"].as_u64().unwrap_or(0) > 0 && !holes_complete {
        if holes["sampling_only"] == true {
            reasons.push("sampled_hole_evidence_is_not_diameter_or_full_instance_metrology");
            has_partial_evidence = true;
        } else if holes["verified_sampled_instance_count"]
            .as_u64()
            .unwrap_or(0)
            > 0
            && holes["sampled_evidence_complete"] != true
        {
            reasons.push("some_sampled_holes_failed_or_are_missing");
            needs_repair = true;
        } else {
            reasons.push("hole_measurements_missing_or_unverified");
        }
    }

    if references["manifest_valid"] != true {
        reasons.push("reference_or_user_turn_manifest_invalid");
    }
    if references["has_references"] == true && visual_complete != true {
        if active_reference_mismatch {
            reasons.push("reference_evidence_contains_mismatch_or_conflict");
            needs_repair = true;
        } else {
            reasons.push("not_all_original_reference_evidence_is_covered");
            has_partial_evidence = true;
        }
    }
    let proportion_mismatch = proportions["status"] == "mismatch";
    if proportion_mismatch {
        for comparison in proportions["comparisons"].as_array().into_iter().flatten()
            .filter(|row| row["status"] == "mismatch")
        {
            findings.push(json!({
                "finding_id":format!("host:body_envelope_proportion:{}", comparison["reference_id"].as_str().unwrap_or("unknown")),
                "status":"open","severity":"major","kind":"body_envelope_proportion_mismatch",
                "evidence":comparison
            }));
        }
    }
    let findings_clear = findings.is_empty();
    if !findings_clear {
        reasons.push("unresolved_findings_remain");
        needs_repair = true;
    }
    if proportion_mismatch {
        reasons.push("body_envelope_proportion_mismatch");
        needs_repair = true;
    } else if proportions["status"] == "unknown" {
        // Reference manifests currently do not carry view/projection annotation. Keep the
        // result explicitly unknown and diagnostic-only until a host-owned annotation flow exists.
        reasons.push("body_envelope_proportion_unknown_diagnostic_only");
    }

    let accept = geometry_passed && holes_complete && visual_complete && findings_clear && !proportion_mismatch;
    let decision = if accept {
        "accept"
    } else if needs_repair {
        "needs_repair"
    } else if has_partial_evidence {
        "partial"
    } else {
        "unverified"
    };

    let evidence = json!({
        "geometry":geometry,
        "holes":holes,
        "references":references,
        "body_envelope_proportions":proportions,
        "unresolved_findings":findings,
        "acceptance_uses_score_or_match":false
    });
    let input = json!({
        "revision":revision,
        "geometry_fingerprint":fingerprint,
        "local_geometry_checks":checks,
        "evidence":evidence
    });
    let input_evidence_sha256 = digest_json(&input)?;
    let review = json!({
        "schema_version":REVIEW_SCHEMA_VERSION,
        "issuer":ISSUER,
        "review_type":"geometry",
        "revision":revision,
        "geometry_fingerprint":fingerprint,
        "input_evidence_sha256":input_evidence_sha256,
        "result":{"decision":decision,"reasons":reasons,"evidence":evidence}
    });
    Ok(AggregateEvaluation {
        state: normalized_state,
        summary,
        review,
    })
}

fn local_geometry_evidence(state: &Value, revision: i64, fingerprint: &str) -> Value {
    let partial = state
        .get("partial_preview")
        .and_then(|value| value.get("state"))
        .and_then(Value::as_str)
        == Some("candidate_partial_not_accepted");
    let preview = state.get("last_preview").filter(|value| value.is_object());
    let checks = if partial {
        state
            .get("partial_preview")
            .and_then(|value| value.get("partial_geometry_checks"))
    } else {
        preview.and_then(|value| value.get("local_geometry_checks"))
    };
    let Some(checks) = checks.filter(|value| value.is_object()) else {
        return json!({"present":false,"binding_valid":false,"passed":false,"state":"missing"});
    };

    let preview_bound = if partial {
        state["partial_preview"]["revision"].as_i64() == Some(revision)
            && state["partial_preview"]["requested_geometry_fingerprint"].as_str()
                == Some(fingerprint)
    } else {
        state.get("preview_revision").and_then(Value::as_i64) == Some(revision)
            && preview
                .and_then(|value| value.get("revision"))
                .and_then(Value::as_i64)
                == Some(revision)
            && preview
                .and_then(|value| value.get("geometry_fingerprint"))
                .and_then(Value::as_str)
                == Some(fingerprint)
            && preview
                .and_then(|value| value.get("execution_engine"))
                .and_then(Value::as_str)
                == Some("rust_c_abi_occt")
    };
    let attestation_valid = verify_local_attestation(checks, revision, fingerprint);
    let binding_valid = preview_bound
        && attestation_valid
        && checks.get("execution_engine").and_then(Value::as_str) == Some("rust_c_abi_occt")
        && checks.get("revision").and_then(Value::as_i64) == Some(revision)
        && checks.get("geometry_fingerprint").and_then(Value::as_str) == Some(fingerprint);
    let state_name = checks
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let report_passed = state_name == "passed"
        && checks.get("valid").and_then(Value::as_bool) == Some(true)
        && checks.get("closed_solid_count").and_then(Value::as_u64) == Some(1)
        && checks.get("validation").and_then(|v| v.get("state"))
            == Some(&Value::String("valid".into()));
    json!({
        "present":true,
        "preview_kind":if partial {"partial"} else {"complete"},
        "binding_valid":binding_valid,
        "passed":binding_valid && report_passed,
        "state":state_name,
        "execution_engine":checks.get("execution_engine"),
        "revision":checks.get("revision"),
        "geometry_fingerprint":checks.get("geometry_fingerprint"),
        "valid":checks.get("valid"),
        "closed_solid_count":checks.get("closed_solid_count"),
        "host_attestation_valid":attestation_valid,
        "host_attestation":checks.get("host_attestation"),
        "checks":checks
    })
}

fn verify_local_attestation(checks: &Value, revision: i64, fingerprint: &str) -> bool {
    let Some(attestation) = checks
        .get("host_attestation")
        .filter(|value| value.is_object())
    else {
        return false;
    };
    if attestation.get("schema_version").and_then(Value::as_u64) != Some(1)
        || attestation.get("issuer").and_then(Value::as_str) != Some(GEOMETRY_ISSUER)
        || attestation.get("revision").and_then(Value::as_i64) != Some(revision)
        || attestation
            .get("geometry_fingerprint")
            .and_then(Value::as_str)
            != Some(fingerprint)
    {
        return false;
    }
    let mut unsigned = checks.clone();
    unsigned
        .as_object_mut()
        .expect("checks were checked as object")
        .remove("host_attestation");
    digest_json(&unsigned).ok().as_deref()
        == attestation.get("report_sha256").and_then(Value::as_str)
}

fn summarize_holes(plan: &Value, checks: &Value) -> Value {
    let hole_features: Vec<&Value> = plan["features"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|feature| {
            feature["kind"] == "hole"
                && feature["operation"] == "cut"
                && feature.get("enabled").and_then(Value::as_bool) != Some(false)
        })
        .collect();
    let declared_ids: BTreeSet<String> = hole_features
        .iter()
        .filter_map(|feature| feature.get("id").and_then(Value::as_str).map(str::to_owned))
        .collect();
    let requested = checks
        .get("requested_holes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let samples = checks
        .get("sampled_hole_evidence")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let requested_keys = instance_keys(&requested, &declared_ids);
    let mut sampled_keys = BTreeSet::new();
    let mut valid_sample_count = 0usize;
    let mut invalid_sample_count = 0usize;
    let mut ray_counts = Vec::new();
    for row in &samples {
        let Some(key) = instance_key(row, &declared_ids) else {
            invalid_sample_count += 1;
            continue;
        };
        if !sampled_keys.insert(key.clone()) {
            invalid_sample_count += 1;
            continue;
        }
        let sample = row.get("sampled_evidence").unwrap_or(&Value::Null);
        let ray = sample.get("ray_evidence").unwrap_or(&Value::Null);
        let interior = ray["summary"]["interior_rays_total"].as_u64().unwrap_or(0);
        let outside = ray["summary"]["outside_rays_total"].as_u64().unwrap_or(0);
        let valid = row.get("status").and_then(Value::as_str) == Some("verified_by_sampled_rays")
            && sample.get("status").and_then(Value::as_str) == Some("verified_by_sampled_rays")
            && ray.get("accepted").and_then(Value::as_bool) == Some(true)
            && interior > 0
            && outside > 0
            && sample
                .get("measurement_scope")
                .and_then(Value::as_str)
                .is_some_and(|scope| {
                    scope.contains("sampled") && scope.contains("not fitted diameter metrology")
                });
        if valid {
            valid_sample_count += 1;
            ray_counts.push(json!({"feature_id":key.0,"instance_index":key.1,"interior_rays":interior,"outside_rays":outside}));
        } else {
            invalid_sample_count += 1;
        }
    }
    let samples_cover_requested =
        !requested_keys.is_empty() && invalid_sample_count == 0 && requested_keys == sampled_keys;
    let no_holes = declared_ids.is_empty();
    let sampling_complete = no_holes || samples_cover_requested;
    let exact_measurement = if no_holes {
        true
    } else {
        native_cad_session::hole_measurement(plan, checks)["complete"] == true
    };
    json!({
        "declared_feature_count":declared_ids.len(),
        "declared_feature_ids":declared_ids,
        "requested_instance_count":requested.len(),
        "requested_instance_count_is_evidence":false,
        "sampled_evidence_instance_count":samples.len(),
        "verified_sampled_instance_count":valid_sample_count,
        "sampled_evidence_complete":!samples.is_empty() && invalid_sample_count == 0 && valid_sample_count == samples.len(),
        "invalid_or_duplicate_sampled_instance_count":invalid_sample_count,
        "all_requested_instances_have_sampled_evidence":sampling_complete,
        "sampled_ray_counts":ray_counts,
        "measurement_complete":exact_measurement,
        "sampling_only":!no_holes && sampling_complete && !exact_measurement,
        "measurement_scope":if no_holes {"no hole features declared"} else if exact_measurement {"backend hole metrology records"} else {"sampled material intervals only; not diameter metrology"}
    })
}

fn instance_keys(rows: &[Value], declared_ids: &BTreeSet<String>) -> BTreeSet<(String, u64)> {
    rows.iter()
        .filter_map(|row| instance_key(row, declared_ids))
        .collect()
}

fn instance_key(row: &Value, declared_ids: &BTreeSet<String>) -> Option<(String, u64)> {
    let feature_id = row.get("feature_id").and_then(Value::as_str)?.to_owned();
    let index = row.get("instance_index").and_then(Value::as_u64)?;
    if index == 0 || !declared_ids.contains(&feature_id) {
        return None;
    }
    Some((feature_id, index))
}

fn summarize_references(state: &Value, summary: &Value) -> Value {
    let images = summary
        .get("reference_images")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let manifest_valid = summary
        .get("reference_manifest_error")
        .is_none_or(Value::is_null)
        && summary
            .get("user_target_context_error")
            .is_none_or(Value::is_null);
    let has_references = !images.is_empty()
        || state
            .get("has_references")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let visual = summary
        .get("visual_evidence_summary")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let visual_records = visual.get("references").and_then(Value::as_object);
    let rows: Vec<Value> = images
        .iter()
        .map(|image| {
            let id = image.get("id").and_then(Value::as_str).unwrap_or_default();
            let evidence = visual_records
                .and_then(|map| map.get(id))
                .cloned()
                .unwrap_or_else(|| json!({}));
            json!({
                "id":id,
                "sha256":image.get("sha256"),
                "filename":image.get("filename"),
                "width":image.get("width"),
                "height":image.get("height"),
                "coverage_status":evidence.get("coverage_status"),
                "covered":evidence.get("covered")==Some(&Value::Bool(true)),
                "inventory_valid":evidence.get("inventory_valid"),
                "required_evidence_ids":evidence.get("required_evidence_ids"),
                "supported_evidence_ids":evidence.get("supported_evidence_ids"),
                "estimated_evidence_ids":evidence.get("estimated_evidence_ids"),
                "missing_required_evidence_ids":evidence.get("missing_required_evidence_ids"),
                "unknown_required_evidence_ids":evidence.get("unknown_required_evidence_ids"),
                "unobservable_evidence_ids":evidence.get("unobservable_evidence_ids"),
                "mismatch_evidence_ids":evidence.get("mismatch_evidence_ids"),
                "reference_conflicts":evidence.get("reference_conflicts")
            })
        })
        .collect();
    let mismatches = rows
        .iter()
        .map(|row| {
            row["mismatch_evidence_ids"].as_array().map_or(0, Vec::len)
                + row["reference_conflicts"].as_array().map_or(0, Vec::len)
        })
        .sum::<usize>();
    let all_covered = !rows.is_empty()
        && rows
            .iter()
            .all(|row| row["covered"] == true && row["inventory_valid"] == true)
        && visual.get("all_references_covered") == Some(&Value::Bool(true));
    let visual_coverage_complete =
        manifest_valid && if has_references { all_covered } else { true };
    json!({
        "manifest_valid":manifest_valid,
        "reference_manifest_error":summary.get("reference_manifest_error"),
        "user_target_context_error":summary.get("user_target_context_error"),
        "has_references":has_references,
        "original_reference_count":images.len(),
        "original_reference_sources":rows,
        "visual_coverage_complete":visual_coverage_complete,
        "native_visual_summary":visual,
        "mismatch_count":mismatches
    })
}

fn digest_json(value: &Value) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::native_cad_session::{complete_preview, BackendOutcome};
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn dir(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "native-review-gate-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn write_json(path: &Path, value: &Value) {
        fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
    }

    fn body_plan(with_hole: bool) -> Value {
        let mut features = vec![json!({
            "id":"body","kind":"box","operation":"add",
            "parameters":{"length":20,"width":10,"height":5,"center":[0,0,0]},
            "sources":{"length":{"type":"image_estimate","evidence":"visual"},"width":{"type":"image_estimate","evidence":"visual"},"height":{"type":"image_estimate","evidence":"visual"}}
        })];
        if with_hole {
            features.push(json!({
                "id":"hole-1","kind":"hole","operation":"cut",
                "parameters":{"diameter":2,"axis":"Y","pos":"point","point":[0,0,0],"through":true},
                "sources":{"diameter":{"type":"image_estimate","evidence":"visual"},"point":{"type":"image_estimate","evidence":"visual"}}
            }));
        }
        json!({"schema_version":"1.0","name":"review-fixture","units":{"length":"mm","angle":"deg"},
            "coordinate_system":{"type":"global_cartesian","handedness":"right","axes":["X","Y","Z"],"origin":[0,0,0]},
            "pending_questions":[],"features":features})
    }

    fn geometry_report(plan: &Value, revision: i64, holes: bool) -> Value {
        let fingerprint = cad_plan::geometry_fingerprint(plan).unwrap();
        json!({
            "state":"passed","execution_engine":"rust_c_abi_occt","revision":revision,
            "geometry_fingerprint":fingerprint,"valid":true,"closed_solid_count":1,
            "validation":{"state":"valid"},"hole_measurements_verified":false,
            "hole_measurement_complete":false,
            "requested_holes":if holes {vec![json!({"feature_id":"hole-1","instance_index":1,"status":"requested_not_measured"})]} else {vec![]},
            "sampled_hole_evidence":[]
        })
    }

    fn base_state(plan: Value, revision: i64, report: Value) -> Value {
        let fingerprint = cad_plan::geometry_fingerprint(&plan).unwrap();
        let state = json!({"revision":revision,"plan":plan,"has_references":false,"actions":[],
            "finding_ledger":{},"required_external_facts":{},"reference_inventory":{},
            "last_preview":Value::Null});
        complete_preview(
            &state,
            BackendOutcome {
                revision,
                geometry_fingerprint: fingerprint.clone(),
                output_paths: json!({"preview":"fixture.png","geometry_report":"report.json"}),
                preview: json!({"state":"candidate","execution_engine":"rust_c_abi_occt",
                "revision":revision,"geometry_fingerprint":fingerprint,
                "local_geometry_checks":report}),
            },
        )
        .unwrap()
        .state
    }

    #[test]
    fn valid_local_geometry_without_holes_can_be_aggregated_as_accept() {
        let dir = dir("simple");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let plan = body_plan(false);
        let report = geometry_report(&plan, 3, false);
        let state = base_state(plan, 3, report);
        let (next, result) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(result["result"]["decision"], "accept");
        assert_eq!(next["latest_geometry_review"], result);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn requested_hole_rows_do_not_count_as_sampling_or_metrology() {
        let dir = dir("missing-hole-samples");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let plan = body_plan(true);
        let mut report = geometry_report(&plan, 3, true);
        report["requested_holes"] = json!([
            {"feature_id":"hole-1","instance_index":1,"status":"requested_not_measured"}
        ]);
        let state = base_state(plan, 3, report);
        let (next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(review["result"]["decision"], "unverified");
        assert_eq!(
            review["result"]["evidence"]["holes"]["requested_instance_count"],
            1
        );
        assert_eq!(
            review["result"]["evidence"]["holes"]["requested_instance_count_is_evidence"],
            false
        );
        assert_eq!(
            review["result"]["evidence"]["holes"]["verified_sampled_instance_count"],
            0
        );
        assert_eq!(
            review["result"]["evidence"]["holes"]["measurement_complete"],
            false
        );
        assert_eq!(next["latest_geometry_review"], review);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn sampled_holes_are_reported_as_partial_and_never_claim_diameter_metrology() {
        let dir = dir("sampled-hole-only");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let plan = body_plan(true);
        let mut report = geometry_report(&plan, 3, true);
        report["sampled_hole_evidence"] = json!([{
            "feature_id":"hole-1","instance_index":1,"status":"verified_by_sampled_rays",
            "sampled_evidence":{
                "status":"verified_by_sampled_rays",
                "measurement_scope":"sampled material intervals; not fitted diameter metrology",
                "ray_evidence":{"accepted":true,"summary":{"interior_rays_total":6,"outside_rays_total":6}}
            }
        }]);
        let state = base_state(plan, 3, report);
        let (_next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(review["result"]["decision"], "partial");
        assert_eq!(review["result"]["evidence"]["holes"]["sampling_only"], true);
        assert_eq!(
            review["result"]["evidence"]["holes"]["measurement_complete"],
            false
        );
        assert_eq!(
            review["result"]["evidence"]["holes"]["measurement_scope"],
            "sampled material intervals only; not diameter metrology"
        );
        assert_eq!(
            review["result"]["evidence"]["holes"]["verified_sampled_instance_count"],
            1
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn disabled_hole_features_do_not_require_measurement() {
        let dir = dir("disabled-hole");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let mut plan = body_plan(false);
        plan["features"].as_array_mut().unwrap().push(json!({
            "id":"disabled-hole","kind":"hole","operation":"cut","enabled":false,
            "parameters":{"diameter":2,"axis":"Y","pos":"point","point":[0,0,0],"through":true},
            "sources":{"diameter":{"type":"image_estimate","evidence":"visual"}}
        }));
        let report = geometry_report(&plan, 3, false);
        let state = base_state(plan, 3, report);
        let (_next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(review["result"]["decision"], "accept");
        assert_eq!(
            review["result"]["evidence"]["holes"]["declared_feature_count"],
            0
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn tampering_with_local_report_breaks_its_current_attestation() {
        let dir = dir("stale-report");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let plan = body_plan(false);
        let report = geometry_report(&plan, 3, false);
        let mut state = base_state(plan, 3, report);
        state["last_preview"]["local_geometry_checks"]["valid"] = json!(false);
        let (_next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(review["result"]["decision"], "unverified");
        assert_eq!(
            review["result"]["evidence"]["geometry"]["host_attestation_valid"],
            false
        );
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_high_similarity_score_without_all_reference_evidence_cannot_accept() {
        let dir = dir("fake-confidence");
        let image = dir.join("ref.png");
        let bytes = b"fixture reference image";
        fs::write(&image, bytes).unwrap();
        let sha = format!("{:x}", Sha256::digest(bytes));
        let id = format!("sha256:{sha}");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(
            &refs,
            &json!({"images":[{"path":image,"id":id,"sha256":sha,"filename":"ref.png","width":64,"height":48,"media_type":"image/png"}]}),
        );
        write_json(
            &turns,
            &json!({"turns":[{"turn_id":"u1","raw_user_prompt":"请按图复刻","reference_ids":[id],"intent":"model_target"}]}),
        );
        let plan = body_plan(false);
        let report = geometry_report(&plan, 3, false);
        let mut state = base_state(plan, 3, report);
        let roi_key = digest_json(&json!({"source_sha256":sha,"roi_norm":null})).unwrap();
        state["has_references"] = json!(true);
        state["reference_inventory"][&id] = json!({"reference_id":id,"source_sha256":sha,"roi_norm":null,
            "roi_cache_key":roi_key,"observation_valid":true,
            "visible_evidence":[{"evidence_id":"edge-1","group_id":"outline","description":"visible outline"}]});
        state["visual_reviews"] = json!({"front":{"revision":3,"score":100,"match":true}});
        state["latest_geometry_review"] = json!({"review_type":"geometry","revision":3,
            "geometry_fingerprint":cad_plan::geometry_fingerprint(&state["plan"]).unwrap(),"score":100,
            "result":{"decision":"accept","confidence":1.0}});
        let (_next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_ne!(review["result"]["decision"], "accept");
        assert_eq!(
            review["result"]["evidence"]["references"]["visual_coverage_complete"],
            false
        );
        assert_eq!(review["result"]["evidence"]["body_envelope_proportions"]["status"], "unknown");
        assert!(review["result"]["reasons"].as_array().unwrap().contains(&json!("body_envelope_proportion_unknown_diagnostic_only")));
        assert_eq!(review["issuer"], ISSUER);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn open_findings_force_needs_repair_even_when_local_geometry_passes() {
        let dir = dir("open-finding");
        let refs = dir.join("refs.json");
        let turns = dir.join("turns.json");
        write_json(&refs, &json!({"images":[]}));
        write_json(&turns, &json!({"turns":[]}));
        let plan = body_plan(false);
        let report = geometry_report(&plan, 3, false);
        let mut state = base_state(plan, 3, report);
        state["finding_ledger"] = json!({"f1":{"finding_id":"f1","status":"open","severity":"major","item":"profile mismatch"}});
        let (_next, review) = aggregate(&state, &refs, &turns).unwrap();
        assert_eq!(review["result"]["decision"], "needs_repair");
        assert_eq!(
            review["result"]["evidence"]["unresolved_findings"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        fs::remove_dir_all(dir).unwrap();
    }
}
