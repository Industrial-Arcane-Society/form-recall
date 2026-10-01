"""Execute one schema-valid example of every public CADPlan feature."""
from __future__ import annotations

import json
import sys
import argparse
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai_modeler.core.geometry import occ_kernel as K
from ai_modeler.core.model.docmodel import KIND_LABELS
from ai_modeler.sdk.cadplan import plan_to_document, validate_plan
from tests.test_cadplan_sdk import _plan, _source_map


def _execution_plan(kind):
    plan = _plan(kind)
    target = plan["features"][-1]
    if kind in {"hole", "pocket"}:
        target["operation"] = "cut"
    elif kind == "pattern":
        target["operation"] = "cut"
        hole_params = {"diameter": 8, "through": True, "pos": "point",
                       "point": [0, 0, 6], "axis": "Z"}
        hole = {"id": "hole-source", "kind": "hole", "operation": "cut",
                "parameters": hole_params, "sources": _source_map(hole_params)}
        target["relations"] = [{"type": "pattern_source", "feature_id": "hole-source"}]
        plan["features"].insert(1, hole)
    if kind == "shell":
        # Shell needs a source solid but remains explicitly unavailable because
        # this feature schema does not yet identify any face to remove.
        return plan
    if kind in {"hole", "pocket", "pattern"}:
        return plan
    # Execute additive features as the initial body, avoiding the test helper's
    # base block overlapping the feature and masking its volume/material effect.
    plan["features"] = [target]
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="resume from a saved partial matrix JSON")
    parser.add_argument("--self-intersection-face-limit", type=int,
                        default=K.SELF_INTERSECTION_FACE_LIMIT,
                        help="maximum BREP face count checked for self-interference")
    args = parser.parse_args()
    if args.self_intersection_face_limit < 1:
        parser.error("--self-intersection-face-limit must be a positive integer")
    rows = []
    completed_kinds = set()
    matrix_started = time.perf_counter()
    if args.resume:
        resume_path = Path(args.resume).resolve()
        checkpoint = json.loads(resume_path.read_text(encoding="utf-8"))
        if checkpoint.get("self_intersection_face_limit") != args.self_intersection_face_limit:
            parser.error("resume checkpoint self-intersection face limit differs from this run")
        rows = list(checkpoint.get("rows", []))
        completed_kinds = {row.get("kind") for row in rows}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(__file__).resolve().parent / ("feature-execution-matrix-%s.json" % stamp)
    checkpoint_path = output_path.with_suffix(".partial.json")
    for kind in KIND_LABELS:
        if kind in completed_kinds:
            continue
        print("starting", kind, flush=True)
        plan = _execution_plan(kind)
        validation = validate_plan(plan)
        support = next((row for row in validation.get("support", [])
                        if row.get("feature_id") == "target"), {})
        geometry_support = next((row for row in validation.get("geometry_support", [])
                                 if row.get("feature_id") == "target"), {})
        row = {
            "kind": kind,
            "validation_state": validation.get("state"),
            "catia_support": support.get("status"),
            "catia_reason": support.get("reason"),
            "local_geometry_support": geometry_support.get("status"),
            "local_geometry_reason": geometry_support.get("reason"),
        }
        if validation.get("state") != "valid":
            row.update({"execution_state": "validation_blocked",
                        "errors": validation.get("errors"),
                        "confirmations": validation.get("confirmations")})
            rows.append(row)
            _write_partial(rows, checkpoint_path, args.self_intersection_face_limit)
            print("done", kind, row["execution_state"], flush=True)
            continue
        try:
            doc = plan_to_document(plan)
            started = time.perf_counter()
            shape = doc.build_brep(
                self_intersection_face_limit=args.self_intersection_face_limit)
            build_elapsed_seconds = time.perf_counter() - started
            report = doc.brep_report or {}
            target_feature_result = next((item for item in report.get("feature_results", [])
                                          if item.get("feature_id") == "target"), None)
            feature_applied = bool(target_feature_result
                                   and target_feature_result.get("state") == "applied")
            self_status = (report.get("self_intersection") or {}).get("status")
            if self_status == "self_intersection":
                self_outcome = "self_intersection_failed"
            elif self_status in {"checked_no_intersection", "not_applicable"}:
                self_outcome = ("feature_shape_checked_clear" if feature_applied
                                else "preserved_shape_clear_feature_not_applied")
            else:
                self_outcome = "unknown_needs_review"
            row.update({
                "execution_state": report.get("state"),
                "geometry_quality_state": report.get("geometry_state", report.get("state")),
                "build_elapsed_seconds": round(build_elapsed_seconds, 3),
                "self_intersection_outcome_tag": self_outcome,
                "self_intersection_scope": (
                    "executed_feature_result_shape"
                    if target_feature_result and target_feature_result.get("state") == "applied"
                    else "preserved_last_valid_shape_feature_not_applied"),
                "backend": report.get("backend"),
                "brep_valid": report.get("brep_valid"),
                "solid_count": report.get("solid_count"),
                "connected_component_count": report.get("connected_component_count"),
                "closed": report.get("closed"),
                "free_edge_count": report.get("free_edge_count"),
                "face_count": report.get("face_count"),
                "self_intersection": report.get("self_intersection"),
                "bbox_mm": report.get("bbox_mm"),
                "volume_mm3": report.get("volume_mm3"),
                "feature_result": target_feature_result,
                "hole_instances": report.get("hole_instances", []),
                "feature_warnings": [item for item in report.get("feature_results", [])
                                      if item.get("feature_id") == "target"],
            })
        except Exception as exc:
            error_report = getattr(exc, "report", {})
            row.update({
                "execution_state": "build_failed",
                "error": str(exc),
                "brep_report": error_report,
            })
        rows.append(row)
        _write_partial(rows, checkpoint_path, args.self_intersection_face_limit)
        print("done", kind, row["execution_state"], flush=True)
    report = {
        "artifact_type": "cadplan-public-feature-execution-matrix",
        "verification_mode": "direct_sdk_not_agent_ui",
        "kernel": K.status(),
        "self_intersection_face_limit": args.self_intersection_face_limit,
        "matrix_process_elapsed_seconds": round(time.perf_counter() - matrix_started, 3),
        "public_document_feature_count": len(KIND_LABELS),
        "public_document_features": list(KIND_LABELS),
        "kernel_geometry_support_entries": len(K.GEOMETRY_SUPPORT),
        "internal_or_recipe_only_kinds": sorted(set(K.GEOMETRY_SUPPORT) - set(KIND_LABELS)),
        "rows": rows,
        "summary": {
            "total_feature_rows": len(rows),
            "brep_passed": [row["kind"] for row in rows
                             if row.get("execution_state") == "passed"],
            "needs_review": [row["kind"] for row in rows
                             if row.get("execution_state") == "needs_review"],
            "build_failed": [row["kind"] for row in rows
                             if row.get("execution_state") == "build_failed"],
            "validation_blocked": [row["kind"] for row in rows
                                   if row.get("execution_state") == "validation_blocked"],
            "timeout_or_unknown": [row["kind"] for row in rows
                                   if row.get("execution_state") == "timeout_unknown"],
            "brep_invalid": [row["kind"] for row in rows
                             if row.get("brep_valid") is False],
            "self_intersection_detected": [row["kind"] for row in rows
                                             if row.get("self_intersection", {}).get("status") == "self_intersection"],
            "self_intersection_analysis_failed": [row["kind"] for row in rows
                                                   if row.get("self_intersection", {}).get("status") == "failed"],
            "self_intersection_not_checked": [row["kind"] for row in rows
                                                if str(row.get("self_intersection", {}).get("status", "")).startswith("not_checked")
                                                or row.get("self_intersection", {}).get("status") == "deferred_to_final_shape"],
            "self_intersection_unknown_or_not_checked": [row["kind"] for row in rows
                                                           if row.get("self_intersection_outcome_tag") ==
                                                           "unknown_needs_review"],
            "self_intersection_checked_clear_on_final_shape": [row["kind"] for row in rows
                                                                 if row.get("self_intersection", {}).get("status") == "checked_no_intersection"],
            "self_intersection_feature_not_applied": [row["kind"] for row in rows
                                                        if row.get("self_intersection_scope") ==
                                                        "preserved_last_valid_shape_feature_not_applied"],
            "self_intersection_missing_or_unmeasured": [row["kind"] for row in rows
                                                          if not row.get("self_intersection")],
            "self_intersection_checked_clear": [row["kind"] for row in rows
                                                  if row.get("self_intersection_outcome_tag") ==
                                                  "feature_shape_checked_clear"],
            "self_intersection_outcome_tag_counts": {
                tag: sum(1 for row in rows if row.get("self_intersection_outcome_tag") == tag)
                for tag in ("feature_shape_checked_clear", "self_intersection_failed",
                            "unknown_needs_review", "preserved_shape_clear_feature_not_applied")
            },
            "local_approximate": [row["kind"] for row in rows
                                  if row.get("local_geometry_support") == "approximate"],
            "local_unavailable": [row["kind"] for row in rows
                                  if row.get("local_geometry_support") == "unavailable"],
            "local_geometry_support_counts": {
                label: sum(1 for row in rows if row.get("local_geometry_support") == label)
                for label in ("exact", "approximate", "unavailable", "unsupported")
            },
        },
    }
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(output_path)
    print("wrote", output_path)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    print(json.dumps(report["summary"], ensure_ascii=False))


def _write_partial(rows, target, self_intersection_face_limit):
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps({"artifact_type": "cadplan-public-feature-execution-matrix-partial",
                                     "verification_mode": "direct_sdk_not_agent_ui",
                                     "kernel": K.status(),
                                     "self_intersection_face_limit": self_intersection_face_limit,
                                     "rows": rows},
                                    ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(target)


if __name__ == "__main__":
    main()
