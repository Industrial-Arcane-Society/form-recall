#!/usr/bin/env python3
"""Classify legacy Quick conrod raw samples against its source component union.

This is a finite-sample diagnostic, not a Boolean union or a continuous
Hausdorff proof. Source dimensions and phase are copied from legacy
``make_conrod`` for the documented F5.98 input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

import compare_mesh_surfaces as mesh

Point = mesh.Point
Triangle = mesh.Triangle
TOLERANCE_MM = 0.08


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k, a[2] * k)


def _inside_polygon(x: float, y: float, radius: float, segments: int = 32) -> bool:
    """Ray-cast against make_revolve's phase-zero regular polygon."""
    vertices = [(radius * math.cos(2 * math.pi * i / segments),
                 radius * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
    inside = False
    previous = vertices[-1]
    for current in vertices:
        x0, y0 = previous
        x1, y1 = current
        if (y0 > y) != (y1 > y):
            crossing_x = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _inside_ring(point: Point, cx: float, outer_r: float, inner_r: float) -> bool:
    if abs(point[2]) > 7.2:
        return False
    x, y = point[0] - cx, point[1]
    return _inside_polygon(x, y, outer_r) and not _inside_polygon(x, y, inner_r)


def _inside_source_union(point: Point) -> bool:
    # Exact nominal source components for F5.98: 48/40 mm and 24/18 mm rings,
    # centered at x=-60/+60; 19.2 mm web and 14.4 mm thickness.
    in_web = (-60.0 <= point[0] <= 60.0 and -9.6 <= point[1] <= 9.6
              and -7.2 <= point[2] <= 7.2)
    return (in_web or _inside_ring(point, -60.0, 24.0, 20.0)
            or _inside_ring(point, 60.0, 12.0, 9.0))


def classify(raw: Sequence[Triangle], candidate: Sequence[Triangle],
             epsilons: Sequence[float],
             raw_sha256: str = "", candidate_sha256: str = "") -> dict[str, object]:
    points, weights = mesh._surface_samples(raw, per_triangle=4)
    candidate_bvh = mesh._TriangleBvh(candidate)
    distances = [math.sqrt(candidate_bvh.distance_squared(point)) for point in points]
    # The compare script's deterministic barycentric ordering is four points
    # per non-degenerate triangle. Reconstruct matching unit normals in order.
    normals: list[Point] = []
    for tri in raw:
        cross = mesh._cross(mesh._sub(tri[1], tri[0]), mesh._sub(tri[2], tri[0]))
        length = math.sqrt(mesh._dot(cross, cross))
        if length > 1e-30:
            normals.extend([_scale(cross, 1.0 / length)] * 4)
    if len(normals) != len(points):
        raise ValueError("sample/normal count mismatch")

    result: dict[str, object] = {
        "raw_triangles": len(raw),
        "samples": len(points),
        "total_area_mm2": sum(weights),
        "inputs": {
            "legacy_raw_stl_sha256": raw_sha256,
            "rust_candidate_stl_sha256": candidate_sha256,
            "sampling": "compare_mesh_surfaces._surface_samples: 4 fixed barycentric samples per triangle, area/4 weights",
            "source_union": "phase-zero 32-gon rings at x=-60/+60, radii 24/20 and 12/9, z=+-7.2; web x[-60,60], y[-9.6,9.6], z[-7.2,7.2]",
            "distance": "compare_mesh_surfaces._TriangleBvh exact point-to-triangle distance",
            "tolerance_mm": TOLERANCE_MM,
        },
        "classification": {},
        "limitation": "Finite surface samples and epsilon-offset point-in-source occupancy; not a Boolean union and not a continuous Hausdorff bound.",
    }
    classes: dict[str, object] = result["classification"]  # type: ignore[assignment]
    for epsilon in epsilons:
        buried: list[int] = []
        exposed: list[int] = []
        outside_both: list[int] = []
        for index, (point, normal) in enumerate(zip(points, normals)):
            plus = _inside_source_union(_add(point, _scale(normal, epsilon)))
            minus = _inside_source_union(_add(point, _scale(normal, -epsilon)))
            (buried if plus and minus else exposed if plus != minus else outside_both).append(index)

        def summary(indices: list[int]) -> dict[str, float | int]:
            selected_d = [distances[i] for i in indices]
            selected_w = [weights[i] for i in indices]
            over = [i for i in indices if distances[i] > TOLERANCE_MM]
            return {
                "samples": len(indices),
                "sample_area_mm2": sum(selected_w),
                "max_to_candidate_mm": max(selected_d, default=0.0),
                "over_0_08_samples": len(over),
                "over_0_08_area_mm2": sum(weights[i] for i in over),
            }

        row: dict[str, object] = {
            "buried": summary(buried),
            "exposed": summary(exposed),
            "outside_both": summary(outside_both),
        }
        if abs(epsilon - 0.001) < 1e-12 and exposed:
            row["exposed_eps_1e-3_p95"] = mesh._weighted_quantile(
                [distances[i] for i in exposed], [weights[i] for i in exposed], 0.95)
        classes[f"{epsilon:g}"] = row
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_raw_stl", type=Path)
    parser.add_argument("rust_candidate_stl", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epsilon-mm", type=float, nargs="+", default=(0.0001, 0.001, 0.01))
    args = parser.parse_args()
    if any(value <= 0 for value in args.epsilon_mm):
        parser.error("epsilon values must be positive")
    raw = mesh.read_binary_stl(args.old_raw_stl)
    candidate = mesh.read_binary_stl(args.rust_candidate_stl)
    result = classify(raw, candidate, args.epsilon_mm,
                      hashlib.sha256(args.old_raw_stl.read_bytes()).hexdigest(),
                      hashlib.sha256(args.rust_candidate_stl.read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
