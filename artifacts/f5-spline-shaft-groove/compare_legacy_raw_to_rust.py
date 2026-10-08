"""Compare the old open raw mesh surface with the Rust native Quick STL.

From repository root, after running the ignored F5.103 geometry test:
  python artifacts/f5-spline-shaft-groove/compare_legacy_raw_to_rust.py

The old raw STL is open and is never treated as an oriented-solid volume oracle.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402

LEGACY_RAW = HERE / "legacy_document_build_raw.stl"
LEGACY_BREP = HERE / "legacy_document_build_brep.stl"
RUST_STL = HERE / "rust_native_quick.stl"
OUTPUT_JSON = HERE / "surface_comparison.json"
SURFACE_TOLERANCE_MM = 0.08
WELD_TOLERANCE_MM = 1e-5
PROJECTION_LEVELS = (64, 128, 256)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (LEGACY_RAW, LEGACY_BREP, RUST_STL):
        if not path.is_file():
            raise FileNotFoundError(f"required F5.103 evidence is missing: {path}")
    legacy_triangles = mesh_compare.read_binary_stl(LEGACY_RAW)
    brep_triangles = mesh_compare.read_binary_stl(LEGACY_BREP)
    rust_triangles = mesh_compare.read_binary_stl(RUST_STL)
    raw_comparison = mesh_compare._compare_pair(
        "legacy_document_build_raw",
        legacy_triangles,
        "rust_native_quick",
        rust_triangles,
        SURFACE_TOLERANCE_MM,
        WELD_TOLERANCE_MM,
        PROJECTION_LEVELS,
    )
    # The shared comparator reports a numeric signed-mesh-volume delta even
    # when one side is open. Preserve that raw diagnostic, but do not expose
    # its allowance result as a meaningful solid-volume comparison.
    raw_volume = raw_comparison["signed_volume_mm3"]
    comparator_volume_interpretation = {
        "status": raw_volume["status"],
        "comparable_as_oriented_solid": raw_volume["comparable_as_oriented_solid"],
        "raw_mesh_volume_invalid": raw_volume["raw_mesh_volume_invalid"],
        "within_allowance": raw_volume["within_allowance"],
    }
    raw_comparison["signed_volume_mm3"] = {
        "status": "not_comparable_open_raw_mesh",
        "left_diagnostic_mm3": raw_volume["left"],
        "right_diagnostic_mm3": raw_volume["right"],
        "absolute_delta_diagnostic_mm3": raw_volume["absolute_delta"],
        "reported_allowance_diagnostic_mm3": raw_volume["reported_allowance"],
        "allowance_use": "diagnostic_only",
        "comparable_as_oriented_solid": False,
        "raw_mesh_volume_invalid": True,
        "comparator_original_interpretation": comparator_volume_interpretation,
    }
    brep_comparison = mesh_compare._compare_pair(
        "legacy_document_build_brep",
        brep_triangles,
        "rust_native_quick",
        rust_triangles,
        SURFACE_TOLERANCE_MM,
        WELD_TOLERANCE_MM,
        PROJECTION_LEVELS,
    )
    result = {
        "evidence_kind": "f5_103_legacy_raw_and_brep_surface_comparisons_to_rust_native_quick_stl",
        "prompt": "花键轴 小径20 大径25 长度40 齿数8 键槽宽5",
        "coordinate_policy": "shared world coordinates in millimetres; no alignment or normalization",
        "inputs": {
            "legacy_document_build_raw": {
                "path": LEGACY_RAW.name,
                "sha256": sha256(LEGACY_RAW),
                "triangle_count": len(legacy_triangles),
                "topology": mesh_compare.topology_diagnostics(legacy_triangles, WELD_TOLERANCE_MM),
                "volume_semantics": "open legacy triangle mesh scalar is not a valid solid-volume oracle",
            },
            "legacy_document_build_brep": {
                "path": LEGACY_BREP.name,
                "sha256": sha256(LEGACY_BREP),
                "triangle_count": len(brep_triangles),
                "topology": mesh_compare.topology_diagnostics(brep_triangles, WELD_TOLERANCE_MM),
                "volume_semantics": "valid closed legacy OCCT B-Rep; its B-Rep volume is recorded separately in legacy_brep_oracle.json",
            },
            "rust_native_quick": {
                "path": RUST_STL.name,
                "sha256": sha256(RUST_STL),
                "triangle_count": len(rust_triangles),
                "topology": mesh_compare.topology_diagnostics(rust_triangles, WELD_TOLERANCE_MM),
            },
        },
        "comparison_settings": {
            "surface_tolerance_mm": SURFACE_TOLERANCE_MM,
            "weld_tolerance_mm": WELD_TOLERANCE_MM,
            "projection_levels": list(PROJECTION_LEVELS),
            "comparator": "tools.compare_mesh_surfaces._compare_pair",
        },
        "comparisons": {
            "legacy_raw_vs_rust": raw_comparison,
            "legacy_brep_oracle_vs_rust": brep_comparison,
            "legacy_raw_vs_legacy_brep_oracle": mesh_compare._compare_pair(
                "legacy_document_build_raw",
                legacy_triangles,
                "legacy_document_build_brep",
                brep_triangles,
                SURFACE_TOLERANCE_MM,
                WELD_TOLERANCE_MM,
                PROJECTION_LEVELS,
            ),
        },
        "pass_criterion": "legacy_brep_oracle_vs_rust.surface_equivalence_passed only (sampled symmetric max <= 0.08 mm and bbox max delta <= 0.08 mm); legacy raw comparison is diagnostic; signed mesh volume is not an acceptance criterion",
        "limitations": [
            "Four deterministic barycentric interior samples per non-degenerate triangle are used; this is a sampled approximation, not a continuous Hausdorff bound.",
            "The old raw mesh is open (810 triangles, 150 boundary edges in the captured baseline) while Rust native Quick must build one closed solid; topology differences are reported separately.",
            "The legacy Document.stats mesh volume is not an oriented-solid volume and is not used for B-Rep or volume parity.",
            "CATIA and desktop UI behavior were not validated.",
        ],
    }
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if brep_comparison["surface_equivalence_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
