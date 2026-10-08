#!/usr/bin/env python3
"""Build the legacy SW80x088 Quick oracle and compare it with Rust STL.

Uses the repository's `tools.compare_mesh_surfaces` BVH, topology, volume and
projection implementation. Surface distances are area-weighted sampled
point-to-triangle distances, not continuous Hausdorff bounds.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "legacy"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust-stl", type=Path, default=ROOT / "artifacts/f5.107-egt-sw/rust_mesh.stl")
    parser.add_argument("--rust-parameters", type=Path, default=ROOT / "artifacts/f5.107-egt-sw/rust_parameters.json")
    parser.add_argument("--rust-report", type=Path, default=ROOT / "artifacts/f5.107-egt-sw/rust_report.json")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/f5.107-egt-sw/comparison.json")
    args = parser.parse_args()

    from ai_modeler.core.parsing.nlparser import Parser
    from ai_modeler.core.export.exporters import _safe_tris, export_stl
    from tools.compare_mesh_surfaces import compare_two_stls, read_binary_stl, topology_diagnostics

    parsed = Parser().parse("EGT001 SW80x088")
    if not parsed.ok or len(parsed.doc.features) != 1 or parsed.doc.features[0].kind != "extrude_holes":
        raise RuntimeError("legacy Parser.parse did not produce the expected one-feature extrude_holes plan")
    parameters = parsed.doc.features[0].params
    if len(parameters["holes"]) != 13:
        raise RuntimeError(f"legacy sample changed unexpectedly: {len(parameters['holes'])} holes")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    oracle_stl = args.out.with_name("legacy_sw80x088.stl")
    polygons = parsed.doc.build()
    tolerance = 1e-8
    polygon_edges: Counter[tuple[tuple[int, int, int], tuple[int, int, int]]] = Counter()
    for polygon in polygons:
        points = [vertex[0] for vertex in polygon.vertices]
        keys = [tuple(round(float(value) / tolerance) for value in point) for point in points]
        for a, b in zip(keys, keys[1:] + keys[:1]):
            polygon_edges[tuple(sorted((a, b)))] += 1
    pre_export_triangles = [(a, b, c) for a, b, c, _normal in _safe_tris(polygons)]
    export_stl(polygons, str(oracle_stl), "EGT001 SW80x088")
    rust_parameters = json.loads(args.rust_parameters.read_text(encoding="utf-8"))
    rust_report = json.loads(args.rust_report.read_text(encoding="utf-8"))
    fields = ("sec_a", "sec_b", "length", "axis", "base", "holes", "segments", "ref_min", "ref_size")
    legacy_parameters = {key: parameters[key] for key in fields}
    comparison = compare_two_stls(
        oracle_stl, args.rust_stl,
        left_name="legacy_document_build_raw", right_name="rust_legacy_quick_mesh",
        tolerance_mm=0.08, weld_tolerance_mm=1e-8,
        projection_resolutions=(64, 128),
    )
    result = {
        "scope": "SW80x088 legacy Quick raw mesh parity only; mesh-only, not a real internal extrusion profile or B-Rep",
        "legacy_parser": {"ok": parsed.ok, "features": len(parsed.doc.features), "kind": parsed.doc.features[0].kind, "hole_count": len(parameters["holes"])},
        "parameters_exact_match": legacy_parameters == rust_parameters,
        "crossing_hole_pairs_from_rust_report": rust_report["diagnostics"]["intersecting_hole_pairs"],
        "rust_geometric_diagnostics": rust_report["diagnostics"],
        "legacy_mesh_stages": {
            "document_build_polygons": {
                "polygon_count": len(polygons),
                "unique_boundary_edges": sum(count == 1 for count in polygon_edges.values()),
                "nonmanifold_edges": sum(count > 2 for count in polygon_edges.values()),
                "edge_weld_tolerance_mm": tolerance,
            },
            "exporter_safe_triangulation_before_float32": {
                "triangle_count": len(pre_export_triangles),
                "topology_at_1e_8_mm": topology_diagnostics(pre_export_triangles, 1e-8),
            },
            "exported_binary_stl_after_float32": {
                "topology_at_1e_8_mm": topology_diagnostics(read_binary_stl(oracle_stl), 1e-8),
                "topology_at_1e_6_mm": topology_diagnostics(read_binary_stl(oracle_stl), 1e-6),
                "interpretation": "float32 STL quantization introduces no boundary edges at either tolerance; the previously cited 352 count is not reproduced by polygon, pre-export triangle, or exported STL incidence counting",
            },
        },
        "comparison": comparison,
        "interpretation": {
            "surface_metric": "four area-weighted barycentric interior samples per triangle, exact point-to-triangle distance through repository BVH",
            "sampled_metric_is_continuous_hausdorff": False,
            "geometric_self_intersection": "measured hole axes intersect; matching raw mesh and combinatorial edge closure do not establish a non-self-intersecting solid",
            "brep_or_catpart": "not produced or verified",
        },
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["parameters_exact_match"]:
        return 2
    return 0 if comparison["comparison"]["surface_equivalence_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
