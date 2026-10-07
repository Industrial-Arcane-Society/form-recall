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


if __name__ == "__main__":
    unittest.main()
