#!/usr/bin/env python3
"""Build an independent F5.98 legacy-Quick conrod union oracle and compare it.

The oracle is reconstructed from the old Quick source recipe (two phase-zero
32-gon annular prisms plus the full-length rectangular web). It does not call
Document.build_brep(), and it never removes whole raw triangles by sample class.
All distance results are finite deterministic samples, not continuous bounds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import OCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon  # noqa: E402
from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402
from OCP.BRepClass3d import BRepClass3d_SolidClassifier  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopAbs import TopAbs_IN, TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.gp import gp_Pnt, gp_Vec  # noqa: E402

from tools import compare_mesh_surfaces as mesh  # noqa: E402
from tools.f5_98_conrod_external_surface import classify as classify_raw  # noqa: E402

PROMPT = "连杆 中心距120 大头外径48 大头孔径40 小头外径24 小头孔径18"
SURFACE_TOLERANCE_MM = 0.08
WELD_TOLERANCE_MM = 1e-5
MESH_DEFLECTION_MM = 0.005
MESH_ANGULAR_DEFLECTION_RAD = 0.1
EXPECTED_RAW_SHA256 = "44d1f05ee473e174b5430008443cf4475577ccfe604c36064ed556cc671e3d7b"
EXPECTED_RUST_CANDIDATE_SHA256 = "b885ee55f77a9e31127069faa05c220f9e18340dc3e9e35fe28cc76041637c60"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _wire(cx: float, radius: float, z: float, clockwise: bool = False):
    polygon = BRepBuilderAPI_MakePolygon()
    indices = range(31, -1, -1) if clockwise else range(32)
    for index in indices:
        angle = 2.0 * math.pi * index / 32.0
        polygon.Add(gp_Pnt(cx + radius * math.cos(angle), radius * math.sin(angle), z))
    polygon.Close()
    if not polygon.IsDone():
        raise RuntimeError("OCP failed to close a 32-gon wire")
    return polygon.Wire()


def _annular_prism(cx: float, inner_radius: float, outer_radius: float):
    face = BRepBuilderAPI_MakeFace(_wire(cx, outer_radius, -7.2))
    face.Add(_wire(cx, inner_radius, -7.2, clockwise=True))
    if not face.IsDone():
        raise RuntimeError(f"OCP failed to make annular face at x={cx}")
    prism = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(0.0, 0.0, 14.4))
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError(f"OCP failed to extrude annular face at x={cx}")
    shape = prism.Shape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise RuntimeError(f"OCP produced invalid annular prism at x={cx}")
    return shape


def _build_union():
    big_ring = _annular_prism(-60.0, 20.0, 24.0)
    small_ring = _annular_prism(60.0, 9.0, 12.0)
    web = BRepPrimAPI_MakeBox(gp_Pnt(-60.0, -9.6, -7.2), 120.0, 19.2, 14.4).Shape()
    if not BRepCheck_Analyzer(web).IsValid():
        raise RuntimeError("OCP produced invalid rectangular web")

    first = BRepAlgoAPI_Fuse(big_ring, small_ring)
    first.SetFuzzyValue(1e-7)
    first.Build()
    if not first.IsDone() or first.Shape().IsNull():
        raise RuntimeError("OCP Boolean fuse failed for the two annular prisms")
    second = BRepAlgoAPI_Fuse(first.Shape(), web)
    second.SetFuzzyValue(1e-7)
    second.Build()
    if not second.IsDone() or second.Shape().IsNull():
        raise RuntimeError("OCP Boolean fuse failed when adding the rectangular web")
    shape = second.Shape()
    valid = BRepCheck_Analyzer(shape).IsValid()
    solid_count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solid_count += 1
        explorer.Next()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    if not valid or solid_count != 1 or props.Mass() <= 0.0:
        raise RuntimeError(f"source union is not one valid solid: valid={valid}, solids={solid_count}, volume={props.Mass()}")
    return shape, {"valid": valid, "solid_count": solid_count, "volume_mm3": props.Mass()}


def _triangles(path: Path) -> list[mesh.Triangle]:
    return mesh.read_binary_stl(path)


def _refine_once(triangles: Sequence[mesh.Triangle]) -> list[mesh.Triangle]:
    refined: list[mesh.Triangle] = []
    for a, b, c in triangles:
        ab = tuple((a[i] + b[i]) * 0.5 for i in range(3))
        bc = tuple((b[i] + c[i]) * 0.5 for i in range(3))
        ca = tuple((c[i] + a[i]) * 0.5 for i in range(3))
        refined.extend(((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)))
    return refined


def _sample_distribution(source: Sequence[mesh.Triangle], target: Sequence[mesh.Triangle]) -> dict[str, Any]:
    # Four fixed barycentric samples on each of four midpoint subdivisions:
    # 16 area-weighted samples per original triangle.
    points, weights = mesh._surface_samples(_refine_once(source), per_triangle=4)
    target_bvh = mesh._TriangleBvh(target)
    distances = [math.sqrt(target_bvh.distance_squared(point)) for point in points]
    ordered = sorted(zip(distances, weights), key=lambda row: row[0])
    total_weight = sum(weights)
    mean = sum(distance * weight for distance, weight in ordered) / total_weight
    rms = math.sqrt(sum(distance * distance * weight for distance, weight in ordered) / total_weight)
    return {
        "method": "16 deterministic area-weighted barycentric samples per source triangle (four per each of four midpoint subtriangles); exact point-to-triangle distance via AABB BVH",
        "sample_count": len(points),
        "sampled_surface_area_mm2": total_weight,
        "weighted_mean_mm": mean,
        "weighted_rms_mm": rms,
        "weighted_p95_mm": mesh._weighted_quantile(distances, weights, 0.95),
        "weighted_p99_mm": mesh._weighted_quantile(distances, weights, 0.99),
        "sampled_max_mm": max(distances, default=0.0),
    }


def _vertex_edge_max(source: Sequence[mesh.Triangle], target: Sequence[mesh.Triangle]) -> dict[str, Any]:
    bvh = mesh._TriangleBvh(target)
    points: list[mesh.Point] = []
    for a, b, c in source:
        points.extend((a, b, c,
                       tuple((a[i] + b[i]) * 0.5 for i in range(3)),
                       tuple((b[i] + c[i]) * 0.5 for i in range(3)),
                       tuple((c[i] + a[i]) * 0.5 for i in range(3))))  # type: ignore[arg-type]
    distances = [math.sqrt(bvh.distance_squared(point)) for point in points]
    return {"probe_count": len(points), "sampled_max_mm": max(distances, default=0.0),
            "method": "all triangle vertices and edge midpoints; finite probes, not a continuous bound"}


def _inside_solid(shape, point: mesh.Point) -> bool:
    classifier = BRepClass3d_SolidClassifier(shape, gp_Pnt(*point), 1e-7)
    return classifier.State() == TopAbs_IN


def _ray_hit(point: mesh.Point, direction: mesh.Point, triangle: mesh.Triangle) -> float | None:
    a, b, c = triangle
    e1, e2 = mesh._sub(b, a), mesh._sub(c, a)
    h = mesh._cross(direction, e2)
    determinant = mesh._dot(e1, h)
    if abs(determinant) < 1e-12:
        return None
    inverse = 1.0 / determinant
    s = mesh._sub(point, a)
    u = inverse * mesh._dot(s, h)
    if u < 1e-10 or u > 1.0 - 1e-10:
        return None
    q = mesh._cross(s, e1)
    v = inverse * mesh._dot(direction, q)
    if v < 1e-10 or u + v > 1.0 - 1e-10:
        return None
    distance = inverse * mesh._dot(e2, q)
    return distance if distance > 1e-9 else None


def _inside_mesh(point: mesh.Point, triangles: Sequence[mesh.Triangle]) -> tuple[bool | None, list[int]]:
    directions: tuple[mesh.Point, ...] = ((1.0, 0.371, 0.157), (0.217, 1.0, 0.413), (0.193, 0.293, 1.0))
    results: list[bool] = []
    for direction in directions:
        hits = sum(_ray_hit(point, direction, triangle) is not None for triangle in triangles)
        results.append(hits % 2 == 1)
    return (results[0] if all(value == results[0] for value in results) else None), [int(value) for value in results]


PROBES: tuple[tuple[str, mesh.Point, str], ...] = (
    ("web_midplane_material", (0.0, 0.0, 0.0), "in"),
    ("web_near_top_endface_material", (0.0, 0.0, 7.19), "in"),
    ("above_web_top_endface_void", (0.0, 0.0, 7.21), "out"),
    ("big_ring_inner_wall_material", (-60.0, 21.0, 0.0), "in"),
    ("big_ring_bore_away_from_web_void", (-60.0, 15.0, 0.0), "out"),
    ("big_ring_outer_clearance_void", (-60.0, 24.2, 0.0), "out"),
    ("big_end_bore_after_web_termination_void", (-70.0, 0.0, 0.0), "out"),
    ("big_ring_inner_wall_thinwall_material", (-60.0, 20.5, 0.0), "in"),
    ("big_ring_inner_wall_near_bore_material", (-60.0, 20.2, 0.0), "in"),
    ("big_ring_outer_wall_near_exterior_material", (-60.0, 23.8, 0.0), "in"),
    ("web_occludes_big_nominal_bore", (-59.0, 0.0, 0.0), "in"),
    ("big_web_ring_intersection_material_side", (-42.7, 9.4, 0.0), "in"),
    ("big_web_inner_ring_intersection_void_side", (-42.7, 9.8, 0.0), "out"),
    ("big_web_outer_ring_intersection_material_side", (-40.0, 13.0, 0.0), "in"),
    ("big_web_outer_ring_intersection_void_side", (-40.0, 13.6, 0.0), "out"),
    ("web_occludes_small_nominal_bore", (59.0, 0.0, 0.0), "in"),
    ("small_web_ring_intersection_material_side", (53.0, 9.4, 0.0), "in"),
    ("small_web_ring_intersection_void_side", (53.0, 9.8, 0.0), "out"),
    ("small_ring_inner_wall_material", (60.0, 10.5, 0.0), "in"),
    ("small_ring_inner_wall_thinwall_material", (60.0, 11.5, 0.0), "in"),
    ("small_end_bore_after_web_termination_void", (65.0, 0.0, 0.0), "out"),
    ("small_end_bore_near_wall_void", (65.0, 6.5, 0.0), "out"),
    ("small_ring_inner_wall_after_web_material", (65.0, 7.6, 0.0), "in"),
    ("small_ring_outer_wall_after_web_material", (65.0, 10.8, 0.0), "in"),
    ("small_ring_outer_clearance_void", (60.0, 12.2, 0.0), "out"),
)


def _probe_report(shape, candidate: Sequence[mesh.Triangle]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, point, expected in PROBES:
        source_state = "in" if _inside_solid(shape, point) else "out"
        candidate_state, ray_states = _inside_mesh(point, candidate)
        candidate_label = "in" if candidate_state is True else "out" if candidate_state is False else "ambiguous"
        rows.append({"name": name, "point_mm": list(point), "source_union": source_state,
                     "expected": expected, "candidate_mesh": candidate_label,
                     "candidate_ray_parity": ray_states,
                     "passed": source_state == expected and candidate_label == expected})
    return {"method": "OCP solid point classifier vs three-direction parity ray tests on closed candidate STL; points are deliberately away from surfaces",
            "probes": rows, "passed": all(row["passed"] for row in rows)}


def run(raw_path: Path, candidate_path: Path, output_dir: Path) -> dict[str, Any]:
    raw_sha, candidate_sha = _sha(raw_path), _sha(candidate_path)
    result: dict[str, Any] = {
        "schema_version": 1,
        "case": {"id": "F5.98-conrod-legacy-quick-union", "prompt": PROMPT,
                 "parameters": {"length_mm": 120.0, "big_center_x_mm": -60.0,
                                "big_outer_radius_mm": 24.0, "big_inner_radius_mm": 20.0,
                                "small_center_x_mm": 60.0, "small_outer_radius_mm": 12.0,
                                "small_inner_radius_mm": 9.0, "segments": 32,
                                "web_x_mm": [-60.0, 60.0], "web_y_mm": [-9.6, 9.6],
                                "web_z_mm": [-7.2, 7.2]},
                 "oracle_definition": "independent source-recipe construction; not Document.build_brep()",
                 "raw_sha256": raw_sha, "candidate_rust_sha256": candidate_sha},
        "inputs": {
            "legacy_document_build_raw": {"path": _display_path(raw_path), "sha256": raw_sha},
            "rust_quick_candidate": {"path": _display_path(candidate_path), "sha256": candidate_sha},
        },
        "thresholds": {"surface_tolerance_mm": SURFACE_TOLERANCE_MM,
                       "weld_tolerance_mm": WELD_TOLERANCE_MM,
                       "oracle_mesh_deflection_mm": MESH_DEFLECTION_MM,
                       "oracle_mesh_angular_deflection_rad": MESH_ANGULAR_DEFLECTION_RAD},
        "geometry_runtime": {"python_ocp_version": getattr(OCP, "__version__", "unknown"),
                             "construction": "Python OCP annular planar faces + prisms + box + sequential fuzzy Boolean fuse (1e-7 mm)"},
        "limitations": ["All surface maxima are finite sampled values, not a continuous Hausdorff bound.",
                        "The independent oracle is reconstructed from the legacy source dimensions and phase using OCP polygon/prism solids; it is not the failed legacy Document.build_brep() path.",
                        "Probe points and sampled triangle subdivisions do not prove full semantic or all-input equivalence.",
                        "CATIA and visible UI behavior were not tested."],
    }
    if raw_sha != EXPECTED_RAW_SHA256 or candidate_sha != EXPECTED_RUST_CANDIDATE_SHA256:
        result["status"] = "inconclusive"
        result["reason"] = "input SHA-256 differs from the fixed F5.98 raw/candidate evidence"
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    source_union_stl = output_dir / "legacy_quick_source_union.stl"
    try:
        shape, solid = _build_union()
        result["source_union"] = solid
        BRepMesh_IncrementalMesh(shape, MESH_DEFLECTION_MM, False, MESH_ANGULAR_DEFLECTION_RAD, True)
        writer = StlAPI_Writer()
        writer.ASCIIMode = False
        if not writer.Write(shape, str(source_union_stl)):
            raise RuntimeError("OCP STL writer returned false")
        result["source_union_stl"] = {"path": source_union_stl.name, "sha256": _sha(source_union_stl),
                                      "size_bytes": source_union_stl.stat().st_size}
    except Exception as exc:
        result["status"] = "inconclusive"
        result["reason"] = f"OCP source-union construction/validation failed: {type(exc).__name__}: {exc}"
        return result

    union_triangles = _triangles(source_union_stl)
    candidate_triangles = _triangles(candidate_path)
    union_topology = mesh.topology_diagnostics(union_triangles, WELD_TOLERANCE_MM)
    candidate_topology = mesh.topology_diagnostics(candidate_triangles, WELD_TOLERANCE_MM)
    result["meshes"] = {
        "source_union": {"triangle_count": len(union_triangles), "topology": union_topology},
        "rust_candidate": {"triangle_count": len(candidate_triangles), "topology": candidate_topology},
    }
    directions = {
        "source_union_to_rust_candidate": _sample_distribution(union_triangles, candidate_triangles),
        "rust_candidate_to_source_union": _sample_distribution(candidate_triangles, union_triangles),
    }
    vertex_edge = {
        "source_union_to_rust_candidate": _vertex_edge_max(union_triangles, candidate_triangles),
        "rust_candidate_to_source_union": _vertex_edge_max(candidate_triangles, union_triangles),
    }
    max_sampled = max(row["sampled_max_mm"] for row in directions.values())
    max_feature_probe = max(row["sampled_max_mm"] for row in vertex_edge.values())
    union_bounds = mesh._bounds(union_triangles)
    candidate_bounds = mesh._bounds(candidate_triangles)
    bounds_delta = max(abs(a - b) for a, b in zip(union_bounds[0] + union_bounds[1],
                                                   candidate_bounds[0] + candidate_bounds[1]))
    result["surface_comparison"] = {
        "coordinate_policy": "shared world millimetres; no alignment or normalization",
        "sampled_bidirectional": directions,
        "sampled_symmetric_max_mm": max_sampled,
        "vertex_and_edge_midpoint_probes": vertex_edge,
        "vertex_edge_symmetric_max_mm": max_feature_probe,
        "bounds_max_abs_delta_mm": bounds_delta,
        "passed": max_sampled <= SURFACE_TOLERANCE_MM and max_feature_probe <= SURFACE_TOLERANCE_MM
                  and bounds_delta <= SURFACE_TOLERANCE_MM,
    }
    result["signed_volume_diagnostic_mm3"] = {
        "source_union_brep": result["source_union"]["volume_mm3"],
        "source_union_mesh": mesh._signed_volume(union_triangles),
        "rust_candidate_mesh": mesh._signed_volume(candidate_triangles),
        "note": "Diagnostic only; both exported meshes are closed and oriented, but surface acceptance uses distance/probes separately.",
    }
    result["semantic_probes"] = _probe_report(shape, candidate_triangles)
    try:
        raw_triangles = _triangles(raw_path)
        result["legacy_raw_occupancy_classification"] = classify_raw(
            raw_triangles, candidate_triangles, (0.0001, 0.001, 0.01), raw_sha, candidate_sha)
        classes = result["legacy_raw_occupancy_classification"]["classification"]
        result["legacy_raw_occupancy_classification_passed"] = all(
            row["outside_both"]["samples"] == 0 and row["exposed"]["over_0_08_samples"] == 0
            for row in classes.values())
    except Exception as exc:
        result["legacy_raw_occupancy_classification_passed"] = False
        result["legacy_raw_occupancy_classification_error"] = f"{type(exc).__name__}: {exc}"

    topologies_ok = all(bool(row["closed_oriented_manifold"]) and row["connected_triangle_components"] == 1
                        for row in (union_topology, candidate_topology))
    result["status"] = "pass" if (topologies_ok and result["surface_comparison"]["passed"]
                                   and result["semantic_probes"]["passed"]
                                   and result.get("legacy_raw_occupancy_classification_passed", False)) else "fail"
    result["pass_contract"] = [
        "OCP source recipe union validates as exactly one solid and meshes as one closed oriented component",
        "Rust candidate is one closed oriented mesh component",
        "Both directions have 16 area-weighted samples per triangle plus every vertex and edge midpoint within 0.08 mm",
        "Shared-frame bounding-box maximum absolute delta is within 0.08 mm",
        "All named ring, bore, web, end-face, thin-wall and web-termination occupancy probes match the independent source union",
        "Legacy raw occupancy classifier has no outside-both samples and no exposed sample beyond 0.08 mm at epsilon 0.0001, 0.001 and 0.01 mm",
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_raw_stl", type=Path)
    parser.add_argument("rust_candidate_stl", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.legacy_raw_stl, args.rust_candidate_stl, args.output_dir)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "surface_comparison.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"report: {report_path}")
    return 0 if result["status"] == "pass" else 2 if result["status"] == "inconclusive" else 1


if __name__ == "__main__":
    raise SystemExit(main())
