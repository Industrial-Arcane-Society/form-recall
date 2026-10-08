"""Triangulate the saved legacy B-Rep and compare only its surface to Rust Quick.

From repository root, after running the ignored F5.104 Rust geometry test:
  python artifacts/f5.104-base-plate-center-blind-hole/compare_brep_surface_to_rust.py

The old non-closed Document.build raw STL is deliberately not loaded here.
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

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepTools import BRepTools  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopoDS import TopoDS_Shape  # noqa: E402
from OCP.TopAbs import TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402


LEGACY_BREP = HERE / "legacy_document_build_brep.brep"
LEGACY_BREP_STL = HERE / "legacy_brep_oracle_stl.stl"
RUST_STL = HERE / "rust_native_quick.stl"
OUTPUT_JSON = HERE / "brep_surface_comparison.json"
SURFACE_TOLERANCE_MM = 0.08
WELD_TOLERANCE_MM = 1e-5
PROJECTION_LEVELS = (64, 128, 256)
MESH_ANGULAR_DEFLECTION_RAD = 0.35


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (LEGACY_BREP, RUST_STL):
        if not path.is_file():
            raise FileNotFoundError(f"required F5.104 evidence is missing: {path}")

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, str(LEGACY_BREP), BRep_Builder()):
        raise RuntimeError(f"OCP could not read legacy B-Rep: {LEGACY_BREP}")
    valid = bool(BRepCheck_Analyzer(shape, True).IsValid())
    if not valid:
        raise RuntimeError("saved legacy Document.build_brep B-Rep is invalid")
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solid_count = 0
    while explorer.More():
        solid_count += 1
        explorer.Next()
    if solid_count != 1:
        raise RuntimeError(f"expected one valid legacy B-Rep solid, found {solid_count}")

    BRepMesh_IncrementalMesh(
        shape, SURFACE_TOLERANCE_MM, False, MESH_ANGULAR_DEFLECTION_RAD, True
    )
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(LEGACY_BREP_STL)):
        raise RuntimeError("OCP binary STL writer failed for legacy B-Rep")

    legacy_triangles = mesh_compare.read_binary_stl(LEGACY_BREP_STL)
    rust_triangles = mesh_compare.read_binary_stl(RUST_STL)
    comparison = mesh_compare._compare_pair(
        "legacy_document_build_brep_mesh",
        legacy_triangles,
        "rust_native_quick",
        rust_triangles,
        SURFACE_TOLERANCE_MM,
        WELD_TOLERANCE_MM,
        PROJECTION_LEVELS,
    )
    result = {
        "evidence_kind": "f5_104_legacy_valid_brep_surface_vs_rust_native_quick_stl",
        "prompt": "基板 长度100 宽度80 厚度10 中心盲孔直径12深5",
        "coordinate_policy": "shared world coordinates in millimetres; no alignment or normalization",
        "legacy_brep": {
            "path": LEGACY_BREP.name,
            "sha256": sha256(LEGACY_BREP),
            "valid": valid,
            "solid_count": solid_count,
        },
        "tessellation": {
            "linear_deflection_mm": SURFACE_TOLERANCE_MM,
            "angular_deflection_radians": MESH_ANGULAR_DEFLECTION_RAD,
            "parallel": True,
        },
        "meshes": {
            "legacy_brep_oracle": {
                "path": LEGACY_BREP_STL.name,
                "sha256": sha256(LEGACY_BREP_STL),
                "triangle_count": len(legacy_triangles),
            },
            "rust_native_quick": {
                "path": RUST_STL.name,
                "sha256": sha256(RUST_STL),
                "triangle_count": len(rust_triangles),
            },
        },
        "comparison_settings": {
            "surface_tolerance_mm": SURFACE_TOLERANCE_MM,
            "weld_tolerance_mm": WELD_TOLERANCE_MM,
            "projection_levels": list(PROJECTION_LEVELS),
            "comparator": "tools.compare_mesh_surfaces._compare_pair",
        },
        "comparison": comparison,
        "pass_criterion": "comparison.surface_equivalence_passed (sampled symmetric max <= 0.08 mm and bbox max delta <= 0.08 mm)",
        "raw_mesh_policy": "legacy Document.build raw STL is not an input to this B-Rep parity comparison; it is non-closed and multi-component",
        "limitations": [
            "Four deterministic barycentric interior samples per non-degenerate triangle are used; this is a sampled approximation, not a continuous Hausdorff bound.",
            "The comparator's signed-volume diagnostics are secondary and do not determine surface parity.",
            "CATIA and desktop UI behavior were not validated.",
        ],
    }
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if comparison["surface_equivalence_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
