"""Capture old Python Quick A100 three-groove overlap baseline.

Repository root, PowerShell:
  $env:PYTHONPATH = 'src/legacy'
  $env:PYTHONDONTWRITEBYTECODE = '1'
  python artifacts/f5.105-vbelt-a100-overlap/capture_legacy_baseline.py

Calls only the legacy Parser -> Document.build raw mesh path and separately
attempts legacy Document.build_brep. It does not invoke Rust, Cargo, CATIA, or UI.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections import defaultdict
from pathlib import Path

PROMPT = "V带轮 基准直径100 槽数3 槽型A 内孔25 宽度30"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.geometry import kernel as K  # noqa: E402
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402
from ai_modeler.parts import std_pulley  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def profile_self_intersections(profile, eps=1e-9):
    segments = [(profile[i], profile[(i + 1) % len(profile)]) for i in range(len(profile))]
    found = []
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            if j == i + 1 or (i == 0 and j == len(segments) - 1):
                continue
            c, d = segments[j]
            o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
            scale = max(1.0, *(abs(v) for p in (a, b, c, d) for v in p))
            tol = eps * scale
            if abs(o1) <= tol and abs(o2) <= tol and abs(o3) <= tol and abs(o4) <= tol:
                axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
                left = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                right = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                if right - left > eps:
                    found.append({"segment_indices": [i, j], "kind": "collinear_overlap",
                                  "overlap_length_mm": right - left})
                continue
            if ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and \
               ((o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol)):
                denominator = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
                det_a = a[0] * b[1] - a[1] * b[0]
                det_c = c[0] * d[1] - c[1] * d[0]
                x = (det_a * (c[0] - d[0]) - (a[0] - b[0]) * det_c) / denominator
                y = (det_a * (c[1] - d[1]) - (a[1] - b[1]) * det_c) / denominator
                found.append({"segment_indices": [i, j], "kind": "proper_crossing", "point_mm": [x, y]})
    return found


def raw_topology(polygons, weld_tolerance_mm=1e-5):
    triangles = K.polygons_to_triangles(polygons)
    edge_uses = defaultdict(list)
    triangle_keys = []
    def key(point):
        return tuple(round(float(axis) / weld_tolerance_mm) for axis in point)
    for ti, triangle in enumerate(triangles):
        keys = [key(point) for point in triangle[:3]]
        triangle_keys.append(keys)
        for i in range(3):
            a, b = keys[i], keys[(i + 1) % 3]
            edge = tuple(sorted((a, b)))
            direction = 1 if a <= b else -1
            edge_uses[edge].append((ti, direction))
    boundary = sum(1 for uses in edge_uses.values() if len(uses) == 1)
    nonmanifold = sum(1 for uses in edge_uses.values() if len(uses) > 2)
    winding_conflicts = sum(1 for uses in edge_uses.values()
                            if len(uses) == 2 and uses[0][1] == uses[1][1])
    neighbors = [[] for _ in triangles]
    for uses in edge_uses.values():
        if len(uses) >= 2:
            ids = [use[0] for use in uses]
            for ti in ids:
                neighbors[ti].extend(other for other in ids if other != ti)
    seen, components = set(), 0
    for start in range(len(triangles)):
        if start in seen:
            continue
        components += 1
        seen.add(start)
        todo = [start]
        while todo:
            ti = todo.pop()
            for other in neighbors[ti]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
    return {
        "weld_tolerance_mm": weld_tolerance_mm,
        "triangle_count": len(triangles),
        "unique_welded_vertices": len({v for tri in triangle_keys for v in tri}),
        "undirected_edge_count": len(edge_uses),
        "boundary_edge_count": boundary,
        "nonmanifold_edge_count": nonmanifold,
        "winding_conflict_edge_count": winding_conflicts,
        "connected_triangle_components": components,
        "closed_by_welded_triangle_edge_incidence": bool(triangles) and boundary == 0 and nonmanifold == 0,
        "method_limit": "Raw mesh edge-incidence diagnostic only; it does not test all triangle-triangle intersections or B-Rep validity.",
    }


def main():
    parsed = Parser().parse(PROMPT)
    document = parsed.doc
    polygons = document.build()
    stats = document.stats()
    stl_path = HERE / "legacy_document_build_raw.stl"
    export_stl(polygons, str(stl_path), document.name, binary=True)

    if not document.features or document.features[0].kind != "vbelt_pulley":
        raise RuntimeError("Old Quick did not produce the expected vbelt_pulley feature.")
    feature = document.features[0]
    params = dict(feature.params)
    spec = std_pulley.spec(
        float(params["pitch_d"]), int(params["grooves"]), params["groove_type"],
        float(params["height"]), float(params["bore_d"]), keep_width=True,
    )
    profile = std_pulley.profile(spec)
    root_radius = float(params["pitch_d"]) / 2.0 - float(spec["hf"])
    root_points = [(index, point) for index, point in enumerate(profile)
                   if abs(float(point[0]) - root_radius) <= 1e-8]
    slot_centers = []
    for i in range(0, len(root_points) - 1, 2):
        pair = root_points[i:i + 2]
        if len(pair) == 2:
            slot_centers.append({
                "profile_point_indices": [pair[0][0], pair[1][0]],
                "root_interval_mm": [float(pair[0][1][1]), float(pair[1][1][1])],
                "center_mm": (float(pair[0][1][1]) + float(pair[1][1][1])) / 2.0,
            })
    crossings = profile_self_intersections(profile)
    root_overlap_mm = max(0.0, float(spec["root_w"]) - min(
        (slot_centers[i + 1]["center_mm"] - slot_centers[i]["center_mm"])
        for i in range(len(slot_centers) - 1))) if len(slot_centers) > 1 else None

    brep_path = HERE / "legacy_document_build_brep.brep"
    try:
        shape = document.build_brep()
        brep_report = json_value(document.brep_report)
        brep = {"attempted": True, "available": shape is not None,
                "report": brep_report, "path": None, "sha256": None, "bytes": None}
        if shape is not None:
            from ai_modeler.core.geometry import occ_kernel
            written = bool(occ_kernel.write_brep(shape, str(brep_path)))
            brep["written"] = written
            if written:
                brep.update({"path": brep_path.name, "sha256": sha256(brep_path),
                             "bytes": brep_path.stat().st_size})
    except Exception as exc:
        brep = {"attempted": True, "available": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "report": json_value(document.brep_report)}

    feature_json = json_value(vars(feature))
    report = {
        "evidence_kind": "old_python_quick_vbelt_a100_overlap_baseline",
        "oracle_scope": "legacy Parser.parse -> Document.build -> binary STL; separate Document.build_brep attempt",
        "prompt": PROMPT,
        "prompt_sha256_utf8": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "parser_result": {
            "class": type(parsed).__name__, "ok": parsed.ok, "recognized": parsed.recognized,
            "message": parsed.message, "steps": json_value(parsed.steps),
            "warnings": json_value(parsed.warnings), "questions": json_value(parsed.questions),
            "assumed": json_value(parsed.assumed),
        },
        "document_feature": feature_json,
        "standard_profile_reconstructed_from_actual_feature_parameters": {
            "spec": json_value(spec),
            "profile_points_radius_axis_mm": json_value(profile),
            "root_radius_mm": root_radius,
            "actual_slot_root_points_and_centers": slot_centers,
            "minimum_adjacent_root_center_pitch_mm": min(
                (slot_centers[i + 1]["center_mm"] - slot_centers[i]["center_mm"])
                for i in range(len(slot_centers) - 1)) if len(slot_centers) > 1 else None,
            "root_interval_overlap_mm": root_overlap_mm,
            "self_intersection_pairs": crossings,
            "self_intersection_pair_count": len(crossings),
            "method": "Runtime std_pulley.profile(spec) called with the actual parsed Feature parameters, matching the legacy Document.build() vbelt_pulley branch; cyclic 2D segment pairs checked for proper crossings and collinear overlap while adjacent segments are excluded.",
        },
        "document_build_stats": json_value(stats),
        "raw_mesh": {
            "path": stl_path.name, "format": "binary STL from legacy Document.build polygons",
            "sha256": sha256(stl_path), "bytes": stl_path.stat().st_size,
            "topology": raw_topology(polygons),
            "self_intersection_assessment": "The raw mesh is produced by revolving the self-intersecting axial profile edge-by-edge; profile crossings/overlaps are recorded above. No complete 3D triangle-triangle collision count is claimed.",
            "volume_semantics": "Document.stats().volume is the legacy polygon-mesh signed-volume scalar, not a valid-solid volume.",
        },
        "brep": brep,
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation()},
        "limitations": [
            "Parser, Document.build mesh, profile and B-Rep attempt are runtime-observed on the old Python path.",
            "Raw mesh volume and B-Rep volume are separate metrics and must not be conflated.",
            "Profile intersection counting is a 2D axial-profile segment test; it does not enumerate all 3D triangle intersections.",
            "No Rust parser, CADPlan validator, Cargo, CATIA, or UI was run or validated.",
        ],
    }
    report_path = HERE / "legacy_raw_baseline.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path.relative_to(REPO)),
        "report_sha256": sha256(report_path),
        "stl": str(stl_path.relative_to(REPO)),
        "stl_sha256": report["raw_mesh"]["sha256"],
        "parameters": {key: params.get(key) for key in ("pitch_d", "grooves", "height", "groove_type", "outer_d", "bore_d")},
        "width_minimum_mm": spec["rim_w_min"],
        "slot_centers_mm": [item["center_mm"] for item in slot_centers],
        "root_overlap_mm": root_overlap_mm,
        "profile_self_intersection_pairs": crossings,
        "stats": json_value(stats),
        "raw_topology": report["raw_mesh"]["topology"],
        "brep": brep,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
