from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "f5.95"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class F595SpringSurfaceAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = read_json(ARTIFACTS / "acceptance_report.json")
        cls.oracle = read_json(ARTIFACTS / "oracle" / "legacy_oracle_report.json")

    def test_both_fixed_samples_have_independent_saved_inputs(self):
        self.assertEqual({"cylindrical", "conical"}, {case["name"] for case in self.report["cases"]})
        for case in self.report["cases"]:
            with self.subTest(case=case["name"]):
                comparison = read_json(ARTIFACTS / "rust" / case["name"] / "surface_comparison.json")
                oracle = next(row for row in self.oracle["cases"] if row["name"] == case["name"])
                self.assertTrue(oracle["parser"]["ok"])
                self.assertEqual([], oracle["parser"]["warnings"])
                self.assertEqual([], oracle["parser"]["questions"])
                self.assertEqual("passed", oracle["brep_report"]["geometry_state"])
                self.assertTrue(oracle["brep_report"]["brep_valid"])
                self.assertEqual("passed", case["rust"]["geometry_report"]["state"])
                for row in oracle["files"].values():
                    path = ARTIFACTS / row["path"]
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(row["sha256"], sha256(path), path)
                rust_file = case["rust"]["files"]["rust_headless_quick_stl"]
                rust_path = ARTIFACTS / rust_file["path"]
                self.assertTrue(rust_path.is_file(), rust_path)
                self.assertEqual(rust_file["sha256"], sha256(rust_path), rust_path)
                hashes = {
                    "legacy_document_build_raw": oracle["files"]["legacy_raw_stl"]["sha256"],
                    "legacy_document_build_brep_mesh": oracle["files"]["legacy_brep_tessellated_stl"]["sha256"],
                    "rust_headless_quick": rust_file["sha256"],
                }
                for label, expected_hash in hashes.items():
                    self.assertEqual(expected_hash, comparison["inputs"][label]["sha256"])
                self.assertEqual(3, len(set(hashes.values())), hashes)

    def test_surface_equivalence_and_raw_mesh_validity_are_separate(self):
        self.assertTrue(self.report["surface_equivalence_passed"])
        for case in self.report["cases"]:
            with self.subTest(case=case["name"]):
                comparison = read_json(ARTIFACTS / "rust" / case["name"] / "surface_comparison.json")
                pairs = comparison["comparisons"]
                self.assertTrue(pairs["legacy_raw_vs_rust"]["surface_equivalence_passed"])
                self.assertTrue(pairs["legacy_brep_oracle_vs_rust"]["surface_equivalence_passed"])
                self.assertTrue(pairs["legacy_raw_vs_legacy_brep_oracle"]["surface_equivalence_passed"])
                raw_pair = pairs["legacy_raw_vs_legacy_brep_oracle"]
                self.assertTrue(raw_pair["signed_volume_mm3"]["raw_mesh_volume_invalid"])
                self.assertGreater(raw_pair["topology"]["left"]["orientation_conflict_edge_count"], 0)
                brep_pair = pairs["legacy_brep_oracle_vs_rust"]
                self.assertEqual(0.0, brep_pair["signed_volume_mm3"]["absolute_delta"])
                self.assertEqual(0, brep_pair["topology"]["left"]["orientation_conflict_edge_count"])
                self.assertEqual(0, brep_pair["topology"]["right"]["orientation_conflict_edge_count"])


if __name__ == "__main__":
    unittest.main()
