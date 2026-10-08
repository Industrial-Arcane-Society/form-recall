"""Compare additional old T10 raw mesh cases with the Rust generator.

Run after `capture_mesh_matrix.py` and the focused Rust matrix test:
  python artifacts/f5.106-timing-pulley-t10/compare_parameter_matrix.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from compare_legacy_raw_to_rust import (  # noqa: E402
    PROJECTION_LEVELS,
    SURFACE_TOLERANCE_MM,
    WELD_TOLERANCE_MM,
    binary_stl_face_differences,
    sha256,
)
from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402

CASE_IDS = (
    "d80-z24-w12-bore8-origin",
    "d127_324-z40-w25-bore18-origin",
    "baseline-nonzero-center",
)


def main() -> int:
    rows = []
    for case_id in CASE_IDS:
        legacy_path = HERE / f"legacy_raw_{case_id}.stl"
        rust_path = HERE / f"rust_raw_{case_id}.stl"
        old = mesh_compare.read_binary_stl(legacy_path)
        rust = mesh_compare.read_binary_stl(rust_path)
        comparison = mesh_compare._compare_pair(
            f"legacy_raw_{case_id}",
            old,
            f"rust_raw_{case_id}",
            rust,
            SURFACE_TOLERANCE_MM,
            WELD_TOLERANCE_MM,
            PROJECTION_LEVELS,
        )
        comparison["tolerance_basis"] = (
            "0.08 mm is the surface sampling acceptance threshold/configuration reference, "
            "not proof of legacy meshing deflection and not a continuous Hausdorff bound"
        )
        raw_volume = comparison["signed_volume_mm3"]
        comparison["signed_volume_mm3"] = {
            "status": "not_comparable_raw_mesh",
            "legacy_raw_diagnostic_mm3": raw_volume["left"],
            "rust_mesh_diagnostic_mm3": raw_volume["right"],
            "absolute_delta_diagnostic_mm3": raw_volume["absolute_delta"],
            "reported_allowance_diagnostic_mm3": raw_volume["reported_allowance"],
            "allowance_use": "diagnostic_only",
            "comparable_as_oriented_solid": False,
        }
        rows.append(
            {
                "case": case_id,
                "inputs": {
                    "legacy_raw": {
                        "path": legacy_path.name,
                        "sha256": sha256(legacy_path),
                        "triangle_count": len(old),
                        "topology": mesh_compare.topology_diagnostics(old, WELD_TOLERANCE_MM),
                    },
                    "rust_raw_mesh": {
                        "path": rust_path.name,
                        "sha256": sha256(rust_path),
                        "triangle_count": len(rust),
                        "topology": mesh_compare.topology_diagnostics(rust, WELD_TOLERANCE_MM),
                    },
                },
                "binary_stl_face_record_check": binary_stl_face_differences(legacy_path, rust_path),
                "comparison": comparison,
            }
        )
    result = {
        "evidence_kind": "f5_106_t10_parameterized_raw_mesh_surface_comparisons",
        "oracle_scope": "legacy Parser.parse -> Document.build; nonzero-center case changes only parsed Feature.params.center before build",
        "comparison_settings": {
            "surface_tolerance_mm": SURFACE_TOLERANCE_MM,
            "weld_tolerance_mm": WELD_TOLERANCE_MM,
            "projection_levels": list(PROJECTION_LEVELS),
            "comparator": "tools.compare_mesh_surfaces._compare_pair",
        },
        "tolerance_basis": "0.08 mm is the surface sampling acceptance threshold/configuration reference, not proof of legacy meshing deflection and not a continuous Hausdorff bound",
        "cases": rows,
        "limitations": [
            "The old parser itself defaults the pulley center to global origin; the translated case tests the legacy geometry constructor parameter contract after parse.",
            "Samples approximate symmetric surface distance and are not a continuous Hausdorff bound.",
            "Signed raw mesh volumes are diagnostic only, not solid volumes.",
            "No B-Rep or CATIA validation is implied.",
        ],
    }
    output = HERE / "surface_comparison_matrix.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(row["comparison"]["surface_equivalence_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
