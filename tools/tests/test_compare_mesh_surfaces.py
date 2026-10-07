from __future__ import annotations

import unittest

from tools.compare_mesh_surfaces import Triangle, _compare_pair


def cube(size: float = 1.0, offset=(0.0, 0.0, 0.0)) -> list[Triangle]:
    x0, y0, z0 = offset
    x1, y1, z1 = x0 + size, y0 + size, z0 + size
    v = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    # Twelve outward-oriented triangles; each quad uses a fixed diagonal.
    faces = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
    return [(v[a], v[b], v[c]) for a, b, c, d in faces for a, b, c in ((a, b, c), (a, c, d))]


def square_with_diagonal(diagonal: str) -> list[Triangle]:
    """Triangulate the same unit square along either of its diagonals."""
    a, b, c, d = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)
    if diagonal == "ac":
        return [(a, b, c), (a, c, d)]
    if diagonal == "bd":
        return [(b, c, d), (b, d, a)]
    raise ValueError(diagonal)


class SurfaceComparatorSyntheticTests(unittest.TestCase):
    def compare(self, left: list[Triangle], right: list[Triangle], tolerance=1e-8):
        return _compare_pair("left", left, "right", right, tolerance, 1e-5, (32,))

    def test_identical_surfaces_pass(self):
        mesh = cube()
        report = self.compare(mesh, list(mesh))
        self.assertTrue(report["passed"], report)
        self.assertAlmostEqual(0.0, report["surface_sampling"]["symmetric_sampled_max_mm"], places=12)
        self.assertTrue(report["topology"]["left"]["closed_manifold"])

    def test_one_mm_translation_is_detected_in_shared_world_coordinates(self):
        report = self.compare(cube(), cube(offset=(1.0, 0.0, 0.0)))
        self.assertFalse(report["passed"], report)
        self.assertAlmostEqual(1.0, report["bounds_max_abs_delta_mm"], places=8)
        self.assertGreaterEqual(report["surface_sampling"]["symmetric_sampled_max_mm"], 0.99)

    def test_two_x_scale_is_detected(self):
        report = self.compare(cube(), cube(size=2.0))
        self.assertFalse(report["passed"], report)
        self.assertGreater(report["bounds_max_abs_delta_mm"], 0.9)
        self.assertGreater(report["surface_sampling"]["symmetric_sampled_max_mm"], 0.9)

    def test_coplanar_surface_with_other_diagonal_and_face_order_passes(self):
        left = square_with_diagonal("ac")
        right = list(reversed(square_with_diagonal("bd")))
        report = self.compare(left, right)
        self.assertTrue(report["passed"], report)
        self.assertEqual(2, len(left))
        self.assertEqual(2, len(right))
        self.assertAlmostEqual(0.0, report["surface_sampling"]["symmetric_sampled_max_mm"], places=12)
        self.assertEqual(0.0, report["bounds_max_abs_delta_mm"])

    def test_reversed_face_winding_changes_orientation_diagnostic_not_surface_distance(self):
        left = cube()
        right = list(left)
        a, b, c = right[0]
        right[0] = (a, c, b)
        report = self.compare(left, right)
        self.assertTrue(report["passed"], report)
        self.assertAlmostEqual(0.0, report["surface_sampling"]["symmetric_sampled_max_mm"], places=12)
        left_topology = report["topology"]["left"]
        right_topology = report["topology"]["right"]
        self.assertTrue(left_topology["closed_oriented_manifold"], report)
        self.assertTrue(right_topology["closed_manifold"], report)
        self.assertFalse(right_topology["closed_oriented_manifold"], report)
        self.assertEqual(0, left_topology["orientation_conflict_edge_count"])
        self.assertGreater(right_topology["orientation_conflict_edge_count"], 0)


if __name__ == "__main__":
    unittest.main()
