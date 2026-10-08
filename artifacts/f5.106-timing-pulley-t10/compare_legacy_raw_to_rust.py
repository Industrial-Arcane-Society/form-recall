"""Compare the captured old Quick raw T10 STL with Rust Quick mesh-only output.

From the repository root, after running the focused Rust Quick test:
  python artifacts/f5.106-timing-pulley-t10/compare_legacy_raw_to_rust.py
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402

LEGACY_RAW = HERE / "legacy_document_build_raw.stl"
RUST_STL = HERE / "rust_quick_mesh_only.stl"
OUTPUT_JSON = HERE / "surface_comparison.json"
SURFACE_TOLERANCE_MM = 0.08
WELD_TOLERANCE_MM = 1e-5
PROJECTION_LEVELS = (64, 128, 256)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binary_stl_face_differences(left_path: Path, right_path: Path) -> dict[str, object]:
    left_bytes, right_bytes = left_path.read_bytes(), right_path.read_bytes()
    left_count = struct.unpack_from("<I", left_bytes, 80)[0]
    right_count = struct.unpack_from("<I", right_bytes, 80)[0]
    if left_count != right_count:
        return {"same_triangle_count": False, "left": left_count, "right": right_count}
    changed_vertex_triangles = 0
    changed_normal_triangles = 0
    max_vertex_delta = 0.0
    first_vertex_difference = None
    for index in range(left_count):
        left = struct.unpack_from("<12fH", left_bytes, 84 + index * 50)
        right = struct.unpack_from("<12fH", right_bytes, 84 + index * 50)
        if left[:3] != right[:3]:
            changed_normal_triangles += 1
        if left[3:12] != right[3:12]:
            changed_vertex_triangles += 1
            deltas = [abs(a - b) for a, b in zip(left[3:12], right[3:12])]
            max_vertex_delta = max(max_vertex_delta, *deltas)
            if first_vertex_difference is None:
                first_vertex_difference = {"triangle": index, "legacy": left[3:12], "rust": right[3:12]}
    return {
        "same_triangle_count": True,
        "triangle_count": left_count,
        "triangles_with_different_vertex_coordinates": changed_vertex_triangles,
        "maximum_vertex_coordinate_delta_mm": max_vertex_delta,
        "first_vertex_difference": first_vertex_difference,
        "triangles_with_different_stored_normals": changed_normal_triangles,
    }


def main() -> int:
    for path in (LEGACY_RAW, RUST_STL):
        if not path.is_file():
            raise FileNotFoundError(f"required F5.106 evidence is missing: {path}")
    old = mesh_compare.read_binary_stl(LEGACY_RAW)
    rust = mesh_compare.read_binary_stl(RUST_STL)
    comparison = mesh_compare._compare_pair(
        "legacy_document_build_raw",
        old,
        "rust_quick_mesh_only",
        rust,
        SURFACE_TOLERANCE_MM,
        WELD_TOLERANCE_MM,
        PROJECTION_LEVELS,
    )
    comparison["tolerance_basis"] = (
        "0.08 mm is the sampled surface acceptance threshold and native Quick configuration reference; "
        "it is not evidence that the old Document.build STL used this linear deflection, nor a continuous Hausdorff bound."
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
        "original_comparator_interpretation": raw_volume,
    }
    result = {
        "evidence_kind": "f5_106_legacy_t10_raw_vs_rust_quick_mesh_only",
        "prompt": "同步带轮 T10 节圆直径95.493 齿数30 宽度16 内孔10",
        "coordinate_policy": "shared world coordinates in millimetres; no alignment or normalization",
        "inputs": {
            "legacy_document_build_raw": {
                "path": LEGACY_RAW.name,
                "sha256": sha256(LEGACY_RAW),
                "triangle_count": len(old),
                "topology": mesh_compare.topology_diagnostics(old, WELD_TOLERANCE_MM),
                "volume_semantics": "raw mesh diagnostic only; not a B-Rep volume oracle",
            },
            "rust_quick_mesh_only": {
                "path": RUST_STL.name,
                "sha256": sha256(RUST_STL),
                "triangle_count": len(rust),
                "topology": mesh_compare.topology_diagnostics(rust, WELD_TOLERANCE_MM),
                "volume_semantics": "mesh diagnostic only; mesh-only output does not claim a solid",
            },
        },
        "comparison_settings": {
            "surface_tolerance_mm": SURFACE_TOLERANCE_MM,
            "weld_tolerance_mm": WELD_TOLERANCE_MM,
            "projection_levels": list(PROJECTION_LEVELS),
            "comparator": "tools.compare_mesh_surfaces._compare_pair",
        },
        "binary_stl_face_record_check": binary_stl_face_differences(LEGACY_RAW, RUST_STL),
        "comparison": comparison,
        "limitations": [
            "The comparator uses four fixed barycentric samples per non-degenerate triangle; sampled results are not a continuous Hausdorff bound.",
            "Old Document.build() returned 1,592 raw triangles. Its separate B-Rep attempt produced a failed 31-solid compound and is not an oracle for this comparison.",
            "Signed mesh volume is diagnostic only and is not treated as a valid entity volume.",
            "CATIA and desktop UI execution were not validated.",
        ],
    }
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if comparison["surface_equivalence_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
