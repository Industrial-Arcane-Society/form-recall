import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("compare_quick_legacy_parity.py")
SPEC = importlib.util.spec_from_file_location("quick_legacy_parity", MODULE_PATH)
parity = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(parity)


class LegacyParityTests(unittest.TestCase):
    def test_null_radius2_and_omitted_radius2_match(self):
        structural, parameters, _tessellation, _descriptions = parity.feature_differences(
            [{"kind": "cylinder", "operation": "add", "parameters": {"radius2": None}}],
            [{"kind": "cylinder", "operation": "add", "parameters": {}}],
            1e-9,
        )
        self.assertEqual(structural, [])
        self.assertEqual(parameters, [])

    def test_null_radius2_and_zero_radius2_are_not_equivalent(self):
        structural, parameters, _tessellation, _descriptions = parity.feature_differences(
            [{"kind": "cylinder", "operation": "add", "parameters": {"radius2": None}}],
            [{"kind": "cylinder", "operation": "add", "parameters": {"radius2": 0.0}}],
            1e-9,
        )
        self.assertEqual(structural, [])
        self.assertEqual(parameters, ["feature 1 radius2: null -> 0.0"])

    def test_feature_kind_change_is_reported(self):
        structural, _parameters, _tessellation, _descriptions = parity.feature_differences(
            [{"kind": "cylinder", "operation": "add", "parameters": {"radius": 10}}],
            [{"kind": "prism", "operation": "add", "parameters": {"radius": 10}}],
            1e-9,
        )
        self.assertEqual(structural, ["feature 1 kind: 'cylinder' -> 'prism'"])

    def test_dimension_delta_is_reported_per_axis(self):
        self.assertAlmostEqual(
            parity.dimension_error_mm([10.0, 20.0, 30.0], [10.0, 20.01, 26.0]), 4.0
        )


if __name__ == "__main__":
    unittest.main()
