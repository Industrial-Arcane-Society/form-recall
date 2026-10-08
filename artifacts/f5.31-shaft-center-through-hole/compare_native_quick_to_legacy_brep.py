#!/usr/bin/env python3
"""Compare the fixed F5.31 Native Quick output against saved legacy evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.compare_mesh_surfaces import compare_three  # noqa: E402


HERE = Path(__file__).resolve().parent
ORACLE = json.loads((HERE / "legacy_oracle_report.json").read_text(encoding="utf-8"))
RUST = json.loads((HERE / "rust_native_quick_geometry_report.json").read_text(encoding="utf-8"))
mesh = compare_three(
    HERE / "legacy_document_build_raw.stl",
    HERE / "legacy_document_build_brep_mesh.stl",
    HERE / "rust_native_quick.stl",
    tolerance_mm=0.08,
    weld_tolerance_mm=1e-5,
    projection_resolutions=(64, 128, 256),
)
legacy_brep = ORACLE["document_build_brep"]["report"]
hole_record = RUST["hole_instances"][0] if len(RUST.get("hole_instances", [])) == 1 else None
hole = None if hole_record is None else {
    key: hole_record.get(key)
    for key in ("feature_id", "axis", "centerline_mm", "diameter_mm", "through", "status")
}
if hole is not None:
    hole["actual_radius_measurements_mm"] = hole_record.get("final_measurement", {}).get("actual_radius_measurements")
    hole["analytic_measurement_accepted"] = hole_record.get("final_measurement", {}).get("accepted")
    hole["full_angular_coverage"] = hole_record.get("final_measurement", {}).get("full_angular_coverage")
expected_bbox = [20.0, 20.0, 100.0]
actual_bbox = RUST.get("dimensions_mm")
if actual_bbox is None:
    # The geometry report schema stores the same-world B-Rep bounds as min/max.
    bounds = RUST.get("bbox_mm")
    actual_bbox = [bounds[i + 3] - bounds[i] for i in range(3)] if bounds and len(bounds) == 6 else None
volume_delta = abs(float(RUST.get("volume_mm3", 0.0)) - float(legacy_brep["volume_mm3"]))
brep_surface = mesh["comparisons"]["legacy_brep_oracle_vs_rust"]
result = {
    "schema_version": 1,
    "input": ORACLE["input"],
    "coordinate_frame": "shared world coordinates, mm; meshes are not translated, rotated, scaled, or normalized",
    "geometry_report_comparison": {
        "bbox_expected_mm": expected_bbox,
        "bbox_actual_mm": actual_bbox,
        "bbox_max_abs_delta_mm": max((abs(a - b) for a, b in zip(actual_bbox or [], expected_bbox)), default=None),
        "legacy_brep_volume_mm3": legacy_brep["volume_mm3"],
        "rust_volume_mm3": RUST.get("volume_mm3"),
        "volume_abs_delta_mm3": volume_delta,
        "closed_solid_count": RUST.get("closed_solid_count"),
        "valid": RUST.get("valid"),
        "hole": hole,
    },
    "surface_comparison": brep_surface,
    "limitations": [
        "Sampled surface distances are a deterministic approximation, not a continuous Hausdorff proof.",
        "The raw Document.build mesh has open boundaries and is diagnostic only; the valid old Document.build_brep solid is the volume authority.",
    ],
}
result["passed"] = bool(
    result["geometry_report_comparison"]["bbox_max_abs_delta_mm"] is not None
    and result["geometry_report_comparison"]["bbox_max_abs_delta_mm"] <= 1e-5
    and volume_delta <= 1e-3
    and RUST.get("valid") is True
    and RUST.get("closed_solid_count") == 1
    and hole is not None
    and hole.get("axis") == "Z"
    and hole.get("centerline_mm") == [0.0, 0.0, 0.0]
    and hole.get("diameter_mm") == 6.0
    and hole.get("through") is True
    and hole.get("status") == "verified_open"
    and brep_surface.get("surface_equivalence_passed") is True
)
output = HERE / "rust_vs_legacy_brep_comparison.json"
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["passed"] else 1)
