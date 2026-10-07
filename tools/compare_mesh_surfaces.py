#!/usr/bin/env python3
"""Compare STL surfaces in shared world coordinates without requiring CAD libraries.

This reports sampled symmetric point-to-triangle distances, welded-mesh topology,
and common-frame projections. It deliberately does not claim an exact continuous
Hausdorff distance or require identical triangle ordering/tessellation.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

Point = tuple[float, float, float]
Triangle = tuple[Point, Point, Point]


def read_binary_stl(path: Path) -> list[Triangle]:
    """Read binary STL and reject truncated or non-binary input explicitly."""
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"{path} is shorter than a binary STL header")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError(f"{path} is not a consistent binary STL")
    triangles: list[Triangle] = []
    for index in range(count):
        row = struct.unpack_from("<12fH", data, 84 + index * 50)
        triangle = (
            (float(row[3]), float(row[4]), float(row[5])),
            (float(row[6]), float(row[7]), float(row[8])),
            (float(row[9]), float(row[10]), float(row[11])),
        )
        if not all(math.isfinite(value) for point in triangle for value in point):
            raise ValueError(f"{path} has a non-finite vertex in triangle {index}")
        triangles.append(triangle)
    if not triangles:
        raise ValueError(f"{path} contains no triangles")
    return triangles


def _bounds(triangles: Sequence[Triangle]) -> tuple[Point, Point]:
    points = [point for triangle in triangles for point in triangle]
    if not points:
        raise ValueError("mesh has no vertices")
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),  # type: ignore[return-value]
        tuple(max(point[axis] for point in points) for axis in range(3)),  # type: ignore[return-value]
    )


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point, b: Point) -> Point:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _triangle_area(triangle: Triangle) -> float:
    return 0.5 * math.sqrt(_dot(_cross(_sub(triangle[1], triangle[0]), _sub(triangle[2], triangle[0])),
                                _cross(_sub(triangle[1], triangle[0]), _sub(triangle[2], triangle[0]))))


def _surface_area(triangles: Sequence[Triangle]) -> float:
    return sum(_triangle_area(triangle) for triangle in triangles)


def _signed_volume(triangles: Sequence[Triangle]) -> float:
    return sum(_dot(a, _cross(b, c)) / 6.0 for a, b, c in triangles)


def _point_triangle_distance_squared(point: Point, triangle: Triangle) -> float:
    """Exact closest-point distance to one triangle (including degenerate fallback)."""
    a, b, c = triangle
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        closest = a
    else:
        bp = _sub(point, b)
        d3, d4 = _dot(ab, bp), _dot(ac, bp)
        if d3 >= 0.0 and d4 <= d3:
            closest = b
        else:
            vc = d1 * d4 - d3 * d2
            if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
                v = d1 / (d1 - d3)
                closest = (a[0] + v * ab[0], a[1] + v * ab[1], a[2] + v * ab[2])
            else:
                cp = _sub(point, c)
                d5, d6 = _dot(ab, cp), _dot(ac, cp)
                if d6 >= 0.0 and d5 <= d6:
                    closest = c
                else:
                    vb = d5 * d2 - d1 * d6
                    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
                        w = d2 / (d2 - d6)
                        closest = (a[0] + w * ac[0], a[1] + w * ac[1], a[2] + w * ac[2])
                    else:
                        va = d3 * d6 - d5 * d4
                        if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
                            w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
                            bc = _sub(c, b)
                            closest = (b[0] + w * bc[0], b[1] + w * bc[1], b[2] + w * bc[2])
                        else:
                            denom = va + vb + vc
                            if abs(denom) < 1e-30:
                                return min(_point_segment_distance_squared(point, a, b),
                                           _point_segment_distance_squared(point, b, c),
                                           _point_segment_distance_squared(point, c, a))
                            v, w = vb / denom, vc / denom
                            closest = (a[0] + ab[0] * v + ac[0] * w,
                                       a[1] + ab[1] * v + ac[1] * w,
                                       a[2] + ab[2] * v + ac[2] * w)
    delta = _sub(point, closest)
    return _dot(delta, delta)


def _point_segment_distance_squared(point: Point, a: Point, b: Point) -> float:
    ab = _sub(b, a)
    denom = _dot(ab, ab)
    if denom <= 1e-30:
        delta = _sub(point, a)
        return _dot(delta, delta)
    t = max(0.0, min(1.0, _dot(_sub(point, a), ab) / denom))
    closest = (a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2])
    delta = _sub(point, closest)
    return _dot(delta, delta)


def _point_aabb_distance_squared(point: Point, minimum: Point, maximum: Point) -> float:
    total = 0.0
    for axis in range(3):
        if point[axis] < minimum[axis]:
            delta = minimum[axis] - point[axis]
            total += delta * delta
        elif point[axis] > maximum[axis]:
            delta = point[axis] - maximum[axis]
            total += delta * delta
    return total


@dataclass
class _BvhNode:
    minimum: Point
    maximum: Point
    indices: list[int] | None = None
    left: _BvhNode | None = None
    right: _BvhNode | None = None


class _TriangleBvh:
    def __init__(self, triangles: Sequence[Triangle], leaf_size: int = 12):
        self.triangles = triangles
        self.centroids = [tuple(sum(vertex[axis] for vertex in triangle) / 3.0 for axis in range(3))
                          for triangle in triangles]
        self.root = self._build(list(range(len(triangles))), leaf_size)

    def _build(self, indices: list[int], leaf_size: int) -> _BvhNode:
        vertices = [vertex for index in indices for vertex in self.triangles[index]]
        minimum = tuple(min(vertex[axis] for vertex in vertices) for axis in range(3))
        maximum = tuple(max(vertex[axis] for vertex in vertices) for axis in range(3))
        node = _BvhNode(minimum, maximum)  # type: ignore[arg-type]
        if len(indices) <= leaf_size:
            node.indices = indices
            return node
        spreads = [max(self.centroids[index][axis] for index in indices)
                   - min(self.centroids[index][axis] for index in indices) for axis in range(3)]
        axis = max(range(3), key=spreads.__getitem__)
        indices.sort(key=lambda index: self.centroids[index][axis])
        middle = len(indices) // 2
        node.left = self._build(indices[:middle], leaf_size)
        node.right = self._build(indices[middle:], leaf_size)
        return node

    def distance_squared(self, point: Point) -> float:
        best = math.inf
        queue: list[tuple[float, int, _BvhNode]] = []
        serial = 0
        heapq.heappush(queue, (_point_aabb_distance_squared(point, self.root.minimum, self.root.maximum), serial, self.root))
        while queue:
            lower_bound, _, node = heapq.heappop(queue)
            if lower_bound >= best:
                break
            if node.indices is not None:
                for index in node.indices:
                    best = min(best, _point_triangle_distance_squared(point, self.triangles[index]))
                continue
            for child in (node.left, node.right):
                if child is None:
                    continue
                child_bound = _point_aabb_distance_squared(point, child.minimum, child.maximum)
                if child_bound < best:
                    serial += 1
                    heapq.heappush(queue, (child_bound, serial, child))
        return best


def _surface_samples(triangles: Sequence[Triangle], per_triangle: int = 4) -> tuple[list[Point], list[float]]:
    """Area-weighted deterministic interior samples; no random seed or vertex bias."""
    barycentrics = ((1 / 3, 1 / 3, 1 / 3), (0.8, 0.1, 0.1),
                    (0.1, 0.8, 0.1), (0.1, 0.1, 0.8))
    if per_triangle != 4:
        raise ValueError("per_triangle must be 4; the fixed barycentric pattern is part of the report contract")
    points: list[Point] = []
    weights: list[float] = []
    for triangle in triangles:
        area = _triangle_area(triangle)
        if area <= 1e-30:
            continue
        for wa, wb, wc in barycentrics:
            points.append(tuple(wa * triangle[0][axis] + wb * triangle[1][axis] + wc * triangle[2][axis]
                                for axis in range(3)))  # type: ignore[arg-type]
            weights.append(area / 4.0)
    if not points:
        raise ValueError("mesh has no non-degenerate surface triangles")
    return points, weights


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    ordered = sorted(zip(values, weights), key=lambda row: row[0])
    threshold = q * sum(weights)
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= threshold:
            return value
    return ordered[-1][0]


def _distance_distribution(source: Sequence[Triangle], target_bvh: _TriangleBvh) -> dict[str, float | int]:
    points, weights = _surface_samples(source)
    distances = [math.sqrt(target_bvh.distance_squared(point)) for point in points]
    area = sum(weights)
    return {
        "sample_count": len(points),
        "sampled_surface_area_mm2": area,
        "weighted_mean_mm": sum(distance * weight for distance, weight in zip(distances, weights)) / area,
        "weighted_rms_mm": math.sqrt(sum(distance * distance * weight for distance, weight in zip(distances, weights)) / area),
        "weighted_p50_mm": _weighted_quantile(distances, weights, 0.50),
        "weighted_p95_mm": _weighted_quantile(distances, weights, 0.95),
        "weighted_p99_mm": _weighted_quantile(distances, weights, 0.99),
        "sampled_max_mm": max(distances),
    }


def _vertex_key(point: Point, weld_tolerance_mm: float) -> tuple[int, int, int]:
    return tuple(round(value / weld_tolerance_mm) for value in point)  # type: ignore[return-value]


def topology_diagnostics(triangles: Sequence[Triangle], weld_tolerance_mm: float = 1e-5) -> dict[str, int | bool | float]:
    """Inspect STL topology after quantized vertex welding at the stated tolerance."""
    if weld_tolerance_mm <= 0:
        raise ValueError("weld tolerance must be positive")
    vertices: dict[tuple[int, int, int], int] = {}
    edge_faces: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    duplicate_faces: Counter[tuple[int, int, int]] = Counter()
    parent = list(range(len(triangles)))
    degenerate = 0

    def vertex_id(point: Point) -> int:
        key = _vertex_key(point, weld_tolerance_mm)
        if key not in vertices:
            vertices[key] = len(vertices)
        return vertices[key]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for face_index, triangle in enumerate(triangles):
        ids = tuple(vertex_id(point) for point in triangle)
        if len(set(ids)) < 3 or _triangle_area(triangle) <= weld_tolerance_mm * weld_tolerance_mm:
            degenerate += 1
        duplicate_faces[tuple(sorted(ids))] += 1
        for edge in ((ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[0])):
            key = (min(edge), max(edge))
            direction = 1 if edge[0] < edge[1] else -1
            edge_faces[key].append((face_index, direction))
    for incident in edge_faces.values():
        if len(incident) > 1:
            first = incident[0][0]
            for other, _ in incident[1:]:
                union(first, other)
    boundary_edges = sum(len(incident) == 1 for incident in edge_faces.values())
    nonmanifold_edges = sum(len(incident) > 2 for incident in edge_faces.values())
    orientation_conflict_edges = sum(len(incident) == 2 and incident[0][1] == incident[1][1]
                                     for incident in edge_faces.values())
    components = len({find(index) for index in range(len(triangles))})
    duplicated_triangle_count = sum(count - 1 for count in duplicate_faces.values() if count > 1)
    return {
        "weld_tolerance_mm": weld_tolerance_mm,
        "welded_vertex_count": len(vertices),
        "edge_count": len(edge_faces),
        "boundary_edge_count": boundary_edges,
        "nonmanifold_edge_count": nonmanifold_edges,
        "orientation_conflict_edge_count": orientation_conflict_edges,
        "degenerate_triangle_count": degenerate,
        "duplicate_triangle_count": duplicated_triangle_count,
        "connected_triangle_components": components,
        "closed_manifold": bool(triangles) and boundary_edges == 0 and nonmanifold_edges == 0 and degenerate == 0,
        "closed_oriented_manifold": bool(triangles) and boundary_edges == 0 and nonmanifold_edges == 0
        and orientation_conflict_edges == 0 and degenerate == 0,
    }


def _inside_2d(point: tuple[float, float], triangle: tuple[tuple[float, float], ...]) -> bool:
    a, b, c = triangle
    def edge(p: tuple[float, float], q: tuple[float, float]) -> float:
        return (point[0] - p[0]) * (q[1] - p[1]) - (point[1] - p[1]) * (q[0] - p[0])
    d1, d2, d3 = edge(a, b), edge(b, c), edge(c, a)
    eps = 1e-12
    return not ((d1 < -eps or d2 < -eps or d3 < -eps) and (d1 > eps or d2 > eps or d3 > eps))


def _projection_mask(triangles: Sequence[Triangle], axes: tuple[int, int], size: int,
                     minimum: Point, maximum: Point) -> set[tuple[int, int]]:
    u, v = axes
    span_u = max(maximum[u] - minimum[u], 1e-12)
    span_v = max(maximum[v] - minimum[v], 1e-12)
    covered: set[tuple[int, int]] = set()
    for triangle in triangles:
        projected = tuple(((point[u] - minimum[u]) / span_u * (size - 1),
                           (point[v] - minimum[v]) / span_v * (size - 1)) for point in triangle)
        min_x = max(0, math.floor(min(point[0] for point in projected)))
        max_x = min(size - 1, math.ceil(max(point[0] for point in projected)))
        min_y = max(0, math.floor(min(point[1] for point in projected)))
        max_y = min(size - 1, math.ceil(max(point[1] for point in projected)))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if _inside_2d((x + 0.5, y + 0.5), projected):
                    covered.add((x, y))
    return covered


def _common_frame_projections(left: Sequence[Triangle], right: Sequence[Triangle], resolutions: Sequence[int]) -> dict[str, object]:
    left_min, left_max = _bounds(left)
    right_min, right_max = _bounds(right)
    minimum = tuple(min(left_min[i], right_min[i]) for i in range(3))
    maximum = tuple(max(left_max[i], right_max[i]) for i in range(3))
    result: dict[str, object] = {}
    for name, axes in (("XY", (0, 1)), ("XZ", (0, 2)), ("YZ", (1, 2))):
        result[name] = {}
        for size in resolutions:
            left_mask = _projection_mask(left, axes, size, minimum, maximum)
            right_mask = _projection_mask(right, axes, size, minimum, maximum)
            union = left_mask | right_mask
            result[name][str(size)] = {  # type: ignore[index]
                "legacy_covered_pixels": len(left_mask),
                "candidate_covered_pixels": len(right_mask),
                "xor_pixels": len(left_mask ^ right_mask),
                "iou": len(left_mask & right_mask) / len(union) if union else 1.0,
            }
    return {"world_frame_union_bounds_mm": [list(minimum), list(maximum)], "by_plane": result}


def _mesh_summary(path: Path, triangles: Sequence[Triangle], weld_tolerance_mm: float) -> dict[str, object]:
    minimum, maximum = _bounds(triangles)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "triangle_count": len(triangles),
        "bounds_mm": {"minimum": list(minimum), "maximum": list(maximum)},
        "signed_volume_mm3": _signed_volume(triangles),
        "surface_area_mm2": _surface_area(triangles),
        "topology": topology_diagnostics(triangles, weld_tolerance_mm),
    }


def _compare_pair(left_name: str, left: Sequence[Triangle], right_name: str, right: Sequence[Triangle],
                  tolerance_mm: float, weld_tolerance_mm: float, projection_resolutions: Sequence[int]) -> dict[str, object]:
    left_min, left_max = _bounds(left)
    right_min, right_max = _bounds(right)
    bbox_delta = max(abs(left_min[i] - right_min[i]) for i in range(3))
    bbox_delta = max(bbox_delta, *(abs(left_max[i] - right_max[i]) for i in range(3)))
    left_bvh, right_bvh = _TriangleBvh(left), _TriangleBvh(right)
    left_to_right = _distance_distribution(left, right_bvh)
    right_to_left = _distance_distribution(right, left_bvh)
    symmetric_max = max(float(left_to_right["sampled_max_mm"]), float(right_to_left["sampled_max_mm"]))
    left_topology = topology_diagnostics(left, weld_tolerance_mm)
    right_topology = topology_diagnostics(right, weld_tolerance_mm)
    volume_left, volume_right = _signed_volume(left), _signed_volume(right)
    volume_delta = abs(volume_left - volume_right)
    max_area = max(_surface_area(left), _surface_area(right))
    volume_limit = tolerance_mm * max_area
    topology_compatible = (
        bool(left_topology["closed_manifold"])
        and bool(right_topology["closed_manifold"])
        and left_topology["connected_triangle_components"] == right_topology["connected_triangle_components"]
    )
    volume_comparable = bool(left_topology["closed_oriented_manifold"]) and bool(right_topology["closed_oriented_manifold"])
    volume_within_tolerance = volume_delta <= volume_limit
    raw_mesh_volume_invalid = (
        "legacy_document_build_raw" in (left_name, right_name)
        and not volume_within_tolerance
    )
    surface_equivalence_passed = symmetric_max <= tolerance_mm and bbox_delta <= tolerance_mm
    return {
        "left": left_name,
        "right": right_name,
        "coordinate_policy": "shared world coordinates in millimetres; no per-mesh normalization or alignment",
        "surface_sampling": {
            "method": "four deterministic barycentric interior samples per non-degenerate triangle, weighted by triangle area",
            "distance": "exact point-to-triangle distance queried against a triangle AABB BVH",
            "limitation": "sampled approximation to continuous surface distance; not an exact continuous Hausdorff bound",
            "left_to_right": left_to_right,
            "right_to_left": right_to_left,
            "symmetric_sampled_max_mm": symmetric_max,
        },
        "surface_equivalence_passed": surface_equivalence_passed,
        "topology": {"left": left_topology, "right": right_topology, "compatible_closed_components": topology_compatible},
        "bounds_max_abs_delta_mm": bbox_delta,
        "signed_volume_mm3": {"left": volume_left, "right": volume_right, "absolute_delta": volume_delta,
                               "reported_allowance": volume_limit, "allowance_basis": "surface_tolerance_mm × larger triangulated surface area",
                               "comparable_as_oriented_solid": volume_comparable,
                               "within_allowance": volume_within_tolerance,
                               "raw_mesh_volume_invalid": raw_mesh_volume_invalid,
                               "status": ("within_allowance" if volume_within_tolerance else
                                          "not_comparable_due_to_orientation" if not volume_comparable else
                                          "oriented_solid_volume_mismatch")},
        "tolerance_mm": tolerance_mm,
        "tolerance_basis": "default 0.08 mm equals the linear deflection passed to the Rust native Quick and legacy BREP tessellators; override explicitly for other meshing settings",
        "common_frame_orthographic_projections": _common_frame_projections(left, right, projection_resolutions),
        # `passed` means sampled surface equivalence only. Topology and volume
        # remain separate evidence so a legacy winding/statistics defect does
        # not erase an independently established surface match.
        "passed": surface_equivalence_passed,
    }


def compare_three(raw_path: Path, brep_stl_path: Path, rust_path: Path,
                  tolerance_mm: float = 0.08, weld_tolerance_mm: float = 1e-5,
                  projection_resolutions: Sequence[int] = (64, 128, 256)) -> dict[str, object]:
    raw = read_binary_stl(raw_path)
    brep = read_binary_stl(brep_stl_path)
    rust = read_binary_stl(rust_path)
    return {
        "schema_version": 1,
        "units": "mm",
        "thresholds": {"surface_tolerance_mm": tolerance_mm,
                       "weld_tolerance_mm": weld_tolerance_mm,
                       "weld_tolerance_basis": "10 µm is larger than binary STL float32 ulp at these 20–40 mm extents, and much smaller than the 0.08 mm surface tolerance"},
        "inputs": {
            "legacy_document_build_raw": _mesh_summary(raw_path, raw, weld_tolerance_mm),
            "legacy_document_build_brep_mesh": _mesh_summary(brep_stl_path, brep, weld_tolerance_mm),
            "rust_headless_quick": _mesh_summary(rust_path, rust, weld_tolerance_mm),
        },
        "comparisons": {
            "legacy_raw_vs_rust": _compare_pair("legacy_document_build_raw", raw, "rust_headless_quick", rust,
                                                  tolerance_mm, weld_tolerance_mm, projection_resolutions),
            "legacy_brep_oracle_vs_rust": _compare_pair("legacy_document_build_brep_mesh", brep, "rust_headless_quick", rust,
                                                         tolerance_mm, weld_tolerance_mm, projection_resolutions),
            "legacy_raw_vs_legacy_brep_oracle": _compare_pair("legacy_document_build_raw", raw, "legacy_document_build_brep_mesh", brep,
                                                               tolerance_mm, weld_tolerance_mm, projection_resolutions),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_raw_stl", type=Path)
    parser.add_argument("legacy_brep_stl", type=Path)
    parser.add_argument("rust_stl", type=Path)
    parser.add_argument("--surface-tolerance-mm", type=float, default=0.08)
    parser.add_argument("--weld-tolerance-mm", type=float, default=1e-5)
    parser.add_argument("--projection-resolutions", type=int, nargs="+", default=(64, 128, 256))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.surface_tolerance_mm <= 0 or args.weld_tolerance_mm <= 0:
        parser.error("surface and weld tolerances must be positive")
    if any(size < 16 or size > 1024 for size in args.projection_resolutions):
        parser.error("projection resolutions must be in 16..=1024")
    result = compare_three(args.legacy_raw_stl, args.legacy_brep_stl, args.rust_stl,
                           args.surface_tolerance_mm, args.weld_tolerance_mm,
                           args.projection_resolutions)
    result["passed"] = all(row["surface_equivalence_passed"] for row in result["comparisons"].values())
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
