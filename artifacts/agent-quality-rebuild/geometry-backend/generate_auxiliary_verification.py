"""Rebuild the direct-SDK geometry verification artifact (not a DSH run)."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from ai_modeler.core.geometry import occ_kernel as K
from ai_modeler.core.model.docmodel import Document, Feature


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "auxiliary-brep-verification.json"


def base_box(doc, center=(0, 0, 0)):
    doc.add(Feature("box", {
        "length": 100, "width": 100, "height": 30,
        "center": center, "axis": "Y", "base": False,
    }, feature_id="base"))


def nine_hole_recess():
    doc = Document("auxiliary-nine-hole-recess")
    base_box(doc)
    doc.add(Feature("pocket", {
        "length": 90, "width": 30, "depth": 5,
        "axis": "Y", "x": 0, "y": 0,
    }, op="cut", feature_id="center-recess"))
    for col, x in enumerate((-35, 0, 35)):
        for row, z in enumerate((-32, 0, 32)):
            diameter = 8 if row == 1 else 12
            doc.add(Feature("hole", {
                "diameter": diameter, "through": True, "pos": "point",
                "point": (x, 0, z), "axis": "Y",
            }, op="cut", feature_id="hole-%d-%d" % (col + 1, row + 1)))
    shape, report = K.build_document(doc)
    expected_body = 100 * 100 * 30
    expected_recess = -(30 * 90 * 5)
    # Two large holes and one small hole in each x-column. The middle column
    # passes through 25 mm after the recess; outer columns pass through 30 mm.
    expected_holes = -math.pi * ((2 * 6**2 + 4**2) * (30 + 25 + 30))
    expected_final = expected_body + expected_recess + expected_holes
    tolerance = 0.001
    return {
        "fixture": {
            "box_xyz_extents_mm": [100, 30, 100],
            "center_recess_width_x_length_z_depth_mm": [30, 90, 5],
            "hole_columns_x_mm": [-35, 0, 35],
            "hole_rows_z_mm": [-32, 0, 32],
            "hole_diameters_by_row_top_to_bottom_mm": [12, 8, 12],
            "provenance": "synthetic direct-SDK fixture; not image-derived dimensions",
        },
        "report": report,
        "volume_formula_mm3": {
            "body": "100*100*30",
            "recess": "-(30*90*5)",
            "holes": "-pi*((2*6^2+4^2)*(30+25+30))",
            "body_mm3": expected_body,
            "recess_mm3": expected_recess,
            "holes_mm3": expected_holes,
            "expected_final_mm3": expected_final,
            "measured_final_mm3": report["volume_mm3"],
            "error_mm3": report["volume_mm3"] - expected_final,
            "tolerance_mm3": tolerance,
            "within_tolerance": abs(report["volume_mm3"] - expected_final) <= tolerance,
        },
        "asserted_recess_volume_removed_mm3": -13500.0,
        "actual_recess_volume_delta_mm3": next(
            row["volume_delta_mm3"] for row in report["feature_results"]
            if row["feature_id"] == "center-recess"),
        "actual_recess_depth_mm": next(
            row["depth_measurement"]["measured_depth_mm"]
            for row in report["feature_results"]
            if row["feature_id"] == "center-recess"),
    }


def pocket_side(side):
    doc = Document("pocket-%s-side" % side)
    base_box(doc)
    doc.add(Feature("pocket", {
        "length": 90, "width": 30, "depth": 5,
        "axis": "Y", "side": side, "x": 0, "y": 0,
    }, op="cut", feature_id="pocket"))
    _shape, report = K.build_document(doc)
    expected = 100 * 100 * 30 - 30 * 90 * 5
    return {
        "side": side,
        "report_state": report["state"],
        "bbox_mm": report["bbox_mm"],
        "volume_mm3": report["volume_mm3"],
        "volume_delta_mm3": report["feature_results"][1]["volume_delta_mm3"],
        "expected_volume_mm3": expected,
        "volume_error_mm3": report["volume_mm3"] - expected,
        "volume_tolerance_mm3": 0.001,
        "depth_measurement": report["feature_results"][1]["depth_measurement"],
        "separate_fixture": True,
    }


def appended_revolve():
    doc = Document("append-revolve")
    doc.add(Feature("box", {
        "length": 40, "width": 40, "height": 40,
        "center": (0, 0, 10), "axis": "Z", "base": False,
    }, feature_id="base"))
    doc.add(Feature("revolve", {
        "profile": [(10, 0), (30, 0), (30, 25), (10, 25)],
        "angle": 360, "center": (0, 0, 0), "axis": "Z",
    }, op="add", feature_id="appended-revolve"))
    _shape, report = K.build_document(doc)
    return {"report": report}


def self_intersecting_revolve():
    profile = [(0, 0), (4, 4), (0, 4), (3, 0), (4, 0)]
    try:
        K.validate_simple_profile(profile, radial=True)
    except ValueError as exc:
        validation_error = str(exc)
    else:
        validation_error = None
    doc = Document("invalid-revolve-rollback")
    doc.add(Feature("box", {
        "length": 40, "width": 40, "height": 40,
        "center": (0, 0, 0), "axis": "Z", "base": False,
    }, feature_id="valid-base"))
    doc.add(Feature("revolve", {
        "profile": profile, "angle": 360,
        "center": (0, 0, 0), "axis": "Z",
    }, op="add", feature_id="invalid-revolve"))
    try:
        K.build_document(doc)
    except K.GeometryBuildError as exc:
        transaction = {
            "state": exc.report.get("state"),
            "failed_feature_id": exc.report.get("failed_feature_id"),
            "error": str(exc),
            "last_valid_shape_volume_mm3": K.volume(exc.last_valid_shape),
            "last_valid_shape_is_valid": K.inspect_shape(
                exc.last_valid_shape, check_self_intersection=False)["brep_valid"],
        }
    else:
        transaction = {"state": "unexpected_success"}
    return {"profile_radius_axial_mm": profile,
            "validation_error": validation_error,
            "transaction": transaction}


def main():
    report = {
        "artifact_type": "geometry-backend-deterministic-auxiliary-verification",
        "verification_mode": "sdk_direct_auxiliary_not_agent_ui",
        "generated_at": "2026-10-01",
        "runtime": {"kernel": K.status()},
        "boundary": {
            "is_dsh_agent_output": False,
            "is_catpart_or_catia_validation": False,
            "image_visual_acceptance": "not_performed",
            "fixture_dimensions_are_from_reference_image": False,
        },
        "box_center_recess_nine_holes": nine_hole_recess(),
        "positive_negative_face_pocket_depth_checks": [
            pocket_side("positive"), pocket_side("negative")],
        "appended_analytic_revolve": appended_revolve(),
        "self_intersecting_revolve_profile": self_intersecting_revolve(),
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("wrote", OUTPUT)
    print(json.dumps(report["box_center_recess_nine_holes"]["volume_formula_mm3"],
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
