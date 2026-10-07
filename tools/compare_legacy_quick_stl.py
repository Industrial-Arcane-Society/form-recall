#!/usr/bin/env python3
"""Compare legacy and Rust Quick binary STL geometry without external CAD deps."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path
from typing import Iterable

Triangle = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def read_stl(path: Path) -> list[Triangle]:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"{path} is shorter than a binary STL header")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError(f"{path} has an inconsistent triangle count/byte length")
    triangles: list[Triangle] = []
    for index in range(count):
        row = struct.unpack_from("<12fH", data, 84 + index * 50)
        triangles.append(
            (
                (float(row[3]), float(row[4]), float(row[5])),
                (float(row[6]), float(row[7]), float(row[8])),
                (float(row[9]), float(row[10]), float(row[11])),
            )
        )
    return triangles


def bounds(triangles: Iterable[Triangle]) -> list[list[float]]:
    points = [point for triangle in triangles for point in triangle]
    return [[min(point[axis] for point in points), max(point[axis] for point in points)] for axis in range(3)]


def signed_volume(triangles: Iterable[Triangle]) -> float:
    total = 0.0
    for a, b, c in triangles:
        cross = (
            b[1] * c[2] - b[2] * c[1],
            b[2] * c[0] - b[0] * c[2],
            b[0] * c[1] - b[1] * c[0],
        )
        total += sum(a[i] * cross[i] for i in range(3)) / 6.0
    return total


def triangle_key(triangle: Triangle) -> tuple[tuple[float, float, float], ...]:
    return tuple(sorted(triangle))


def inside_triangle(px: float, py: float, a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    def edge(p: tuple[float, float], q: tuple[float, float], x: float, y: float) -> float:
        return (x - p[0]) * (q[1] - p[1]) - (y - p[1]) * (q[0] - p[0])

    d1, d2, d3 = edge(a, b, px, py), edge(b, c, px, py), edge(c, a, px, py)
    eps = 1e-10
    return not ((d1 < -eps or d2 < -eps or d3 < -eps) and (d1 > eps or d2 > eps or d3 > eps))


def projection_mask(triangles: list[Triangle], plane: tuple[int, int], size: int) -> set[tuple[int, int]]:
    box = bounds(triangles)
    u, v = plane
    span_u = max(box[u][1] - box[u][0], 1e-12)
    span_v = max(box[v][1] - box[v][0], 1e-12)

    def pixel(point: tuple[float, float, float]) -> tuple[float, float]:
        return (
            (point[u] - box[u][0]) / span_u * (size - 1),
            (point[v] - box[v][0]) / span_v * (size - 1),
        )

    covered: set[tuple[int, int]] = set()
    for triangle in triangles:
        projected = tuple(pixel(point) for point in triangle)
        min_x = max(0, math.floor(min(point[0] for point in projected)))
        max_x = min(size - 1, math.ceil(max(point[0] for point in projected)))
        min_y = max(0, math.floor(min(point[1] for point in projected)))
        max_y = min(size - 1, math.ceil(max(point[1] for point in projected)))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if inside_triangle(x + 0.5, y + 0.5, *projected):
                    covered.add((x, y))
    return covered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_stl", type=Path)
    parser.add_argument("rust_stl", type=Path)
    parser.add_argument("--projection-size", type=int, default=128)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-5)
    parser.add_argument("--relative-tolerance", type=float, default=1e-8)
    args = parser.parse_args()
    if args.projection_size < 16 or args.projection_size > 2048:
        parser.error("--projection-size must be in 16..=2048")
    if args.absolute_tolerance < 0 or args.relative_tolerance < 0:
        parser.error("tolerances must be non-negative")

    legacy = read_stl(args.legacy_stl)
    rust = read_stl(args.rust_stl)
    legacy_bounds, rust_bounds = bounds(legacy), bounds(rust)
    legacy_volume, rust_volume = signed_volume(legacy), signed_volume(rust)
    legacy_surface = Counter(map(triangle_key, legacy))
    rust_surface = Counter(map(triangle_key, rust))
    surface_equal = legacy_surface == rust_surface
    projection_diffs = {}
    for name, plane in (("XY", (0, 1)), ("XZ", (0, 2)), ("YZ", (1, 2))):
        left = projection_mask(legacy, plane, args.projection_size)
        right = projection_mask(rust, plane, args.projection_size)
        union = left | right
        projection_diffs[name] = {
            "legacy_covered_pixels": len(left),
            "rust_covered_pixels": len(right),
            "xor_pixels": len(left ^ right),
            "iou": len(left & right) / len(union) if union else 1.0,
        }

    bbox_deltas = [
        abs(legacy_bounds[axis][side] - rust_bounds[axis][side])
        for axis in range(3)
        for side in range(2)
    ]
    bbox_max_delta = max(bbox_deltas, default=0.0)
    volume_delta = abs(legacy_volume - rust_volume)
    volume_limit = args.absolute_tolerance + args.relative_tolerance * abs(legacy_volume)
    result = {
        "legacy_stl": {"path": str(args.legacy_stl), "sha256": hashlib.sha256(args.legacy_stl.read_bytes()).hexdigest()},
        "rust_stl": {"path": str(args.rust_stl), "sha256": hashlib.sha256(args.rust_stl.read_bytes()).hexdigest()},
        "triangle_count": {"legacy": len(legacy), "rust": len(rust), "equal": len(legacy) == len(rust)},
        "bounds_mm": {"legacy": legacy_bounds, "rust": rust_bounds, "max_abs_delta": bbox_max_delta},
        "signed_mesh_volume_mm3": {"legacy": legacy_volume, "rust": rust_volume, "abs_delta": volume_delta, "tolerance": volume_limit},
        "surface_triangle_multiset_exact": surface_equal,
        "orthographic_projection_masks": projection_diffs,
        "passed": len(legacy) == len(rust) and bbox_max_delta <= args.absolute_tolerance and volume_delta <= volume_limit and surface_equal and all(row["xor_pixels"] == 0 for row in projection_diffs.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
