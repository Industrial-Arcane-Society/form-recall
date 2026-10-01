import math
import unittest
from unittest import mock

from ai_modeler.core.geometry import occ_kernel as K
from ai_modeler.core.model.docmodel import Document, Feature
from ai_modeler.sdk.cadplan import (CADPLAN_SCHEMA_VERSION, _local_geometry_checks,
                                    preview_plan, schema_document, validate_plan)


@unittest.skipUnless(K.available(), "cadquery-ocp is required for BREP backend tests")
class OCCTBRepBackendTests(unittest.TestCase):
    @staticmethod
    def _sources(parameters):
        return {key: {"type": "user"} for key, value in parameters.items()
                if isinstance(value, (int, float, list, tuple))
                and not isinstance(value, bool)}

    def _nine_hole_recess_plan(self):
        features = []
        base = {"length": 100, "width": 100, "height": 30,
                "center": [0, 0, 0], "axis": "Y", "base": False}
        features.append({"id": "base", "kind": "box", "operation": "add",
                         "parameters": base, "sources": self._sources(base)})
        pocket = {"length": 90, "width": 30, "depth": 5, "axis": "Y",
                  "x": 0, "y": 0}
        features.append({"id": "center-recess", "kind": "pocket", "operation": "cut",
                         "parameters": pocket, "sources": self._sources(pocket)})
        for col, x in enumerate((-35, 0, 35)):
            for row, z in enumerate((-32, 0, 32)):
                params = {"diameter": 8 if row == 1 else 12,
                          "through": True, "pos": "point",
                          "point": [x, 0, z], "axis": "Y"}
                features.append({"id": "hole-%d-%d" % (col, row), "kind": "hole",
                                 "operation": "cut", "parameters": params,
                                 "sources": self._sources(params)})
        return {
            "schema_version": CADPLAN_SCHEMA_VERSION,
            "name": "nine-hole recess",
            "units": {"length": "mm", "angle": "deg"},
            "coordinate_system": {"type": "global_cartesian", "handedness": "right",
                                  "axes": ["X", "Y", "Z"], "origin": [0, 0, 0]},
            "features": features,
        }

    def test_socket_head_bolt_builds_one_valid_faceted_thread_brep(self):
        params = {"shank_d": 8, "thread_pitch": 1.25, "length": 20,
                  "head_d": 13, "head_h": 8, "hex_s": 6, "hex_t": 4,
                  "segments": 24}
        doc = Document("socket-bolt")
        doc.add(Feature("socket_head_bolt", params, feature_id="bolt"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertTrue(report["closed"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertEqual("analytic_socket_head_with_faceted_thread_brep",
                         report["feature_results"][0]["builder"])
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertLessEqual(report["face_count"], K.SELF_INTERSECTION_FACE_LIMIT)
        self.assertEqual("approximate", report["feature_results"][0]["geometry_support"])
        self.assertGreater(K.volume(shape), math.pi * (13 / 2) ** 2 * 8)

    def test_matrix_bevel_gear_self_interference_is_checked_under_budget(self):
        doc = Document("matrix-bevel-gear")
        doc.add(Feature("bevel_gear", {"teeth": 20, "module": 2, "height": 12,
                                        "cone_deg": 45}, feature_id="bevel-gear"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["geometry_state"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertLessEqual(report["face_count"], K.SELF_INTERSECTION_FACE_LIMIT)
        self.assertEqual("approximate", report["feature_results"][0]["geometry_support"])

    def test_spoked_wheel_builds_analytic_connected_brep(self):
        params = {"outer_r": 30, "rim_w": 4, "hub_r": 8,
                  "height": 8, "spokes": 6, "spoke_w": 3}
        doc = Document("spoked-wheel")
        doc.add(Feature("spoked_wheel", params, feature_id="wheel"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertTrue(report["closed"])
        self.assertTrue(report["brep_valid"])
        self.assertEqual("analytic_ring_hub_with_box_spokes",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(60, report["bbox_mm"][3] - report["bbox_mm"][0], places=5)
        self.assertAlmostEqual(8, report["bbox_mm"][5] - report["bbox_mm"][2], places=5)

    def test_piston_uses_analytic_profile_and_pin_bore(self):
        params = {"diameter": 40, "height": 35, "pin_d": 10, "grooves": 3}
        doc = Document("piston")
        doc.add(Feature("piston", params, feature_id="piston"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertTrue(report["closed"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("analytic_grooved_revolve_with_pin_bore",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(40, report["bbox_mm"][3] - report["bbox_mm"][0], places=5)

    def test_crankshaft_uses_connected_analytic_journals(self):
        params = {"length": 80, "main_d": 20, "pin_d": 16,
                  "stroke": 30, "throws": 2}
        doc = Document("crankshaft")
        doc.add(Feature("crankshaft", params, feature_id="crank"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertTrue(report["closed"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("analytic_journals_with_rectangular_webs",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(80, report["bbox_mm"][3] - report["bbox_mm"][0], places=5)

    def test_conrod_uses_annular_heads_and_nonintersecting_web(self):
        params = {"length": 80, "big_d": 30, "small_d": 18,
                  "big_bore": 20, "small_bore": 10,
                  "shank_w": 10, "thick": 8}
        doc = Document("conrod")
        doc.add(Feature("conrod", params, feature_id="conrod"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertTrue(report["closed"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("analytic_annular_ends_with_external_tangent_web",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(104, report["bbox_mm"][3] - report["bbox_mm"][0], places=5)
        self.assertAlmostEqual(30, report["bbox_mm"][4] - report["bbox_mm"][1], places=5)
        self.assertAlmostEqual(8, report["bbox_mm"][5] - report["bbox_mm"][2], places=5)

    def test_impeller_builds_closed_native_twisted_blade_lofts(self):
        params = {"hub_d": 20, "tip_d": 60, "height": 15,
                  "blades": 6, "blade_t": 2, "twist_deg": 25}
        doc = Document("impeller")
        doc.add(Feature("impeller", params, feature_id="impeller"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertEqual(0, report["free_edge_count"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertTrue(report["closed"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("analytic_hub_backplate_with_native_twisted_blade_lofts",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(60, report["bbox_mm"][3] - report["bbox_mm"][0], places=4)
        self.assertAlmostEqual(15, report["bbox_mm"][5] - report["bbox_mm"][2], places=4)

    def test_scroll_disc_uses_oriented_native_prismatic_spiral(self):
        params = {"outer_d": 60, "height": 8, "wall_t": 2, "turns": 2.5}
        doc = Document("scroll-disc")
        doc.add(Feature("scroll_disc", params, feature_id="scroll"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertEqual(0, report["free_edge_count"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertTrue(report["closed"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("analytic_prismatic_spiral_and_backplate",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(60, report["bbox_mm"][3] - report["bbox_mm"][0], places=5)
        self.assertAlmostEqual(8, report["bbox_mm"][5] - report["bbox_mm"][2], places=5)

    def test_rotor_uses_native_prism_and_analytic_bore_cut(self):
        params = {"radius": 30, "n": 3, "height": 12, "bore_d": 8}
        doc = Document("rotor")
        doc.add(Feature("rotor", params, feature_id="rotor"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual(1, report["connected_component_count"], report)
        self.assertEqual(0, report["free_edge_count"], report)
        self.assertTrue(report["brep_valid"], report)
        self.assertTrue(report["closed"], report)
        self.assertEqual("checked_no_intersection",
                         report["self_intersection"]["status"], report)
        self.assertEqual("native_prismatic_rounded_triangle_with_bore",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(12, report["bbox_mm"][5] - report["bbox_mm"][2], places=5)

    def test_standard_vbelt_pulley_uses_native_revolve_profile(self):
        params = {"pitch_d": 80, "grooves": 3, "height": 20,
                  "groove_type": "A"}
        doc = Document("v-belt-pulley")
        doc.add(Feature("vbelt_pulley", params, feature_id="pulley"))
        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        self.assertEqual("feature_build_failed", caught.exception.report["failure_code"])
        self.assertIsNone(caught.exception.last_valid_shape)
        self.assertIn("最小轮宽", caught.exception.report["error"])

    def test_standard_vbelt_pulley_keeps_sufficient_requested_width(self):
        params = {"pitch_d": 80, "grooves": 3, "height": 51,
                  "groove_type": "A"}
        doc = Document("v-belt-pulley")
        doc.add(Feature("vbelt_pulley", params, feature_id="pulley"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual("analytic_standard_vbelt_revolve",
                         report["feature_results"][0]["builder"])
        self.assertAlmostEqual(51.0, report["bbox_mm"][5] - report["bbox_mm"][2],
                               places=6)
        self.assertTrue(report["brep_valid"])
        self.assertTrue(report["closed"])

    def test_repeated_through_hole_axis_is_reported_with_related_feature(self):
        doc = Document("duplicate-through-hole")
        doc.add(Feature("box", {"length": 50, "width": 40, "height": 30,
                                 "center": (0, 0, 0), "base": False},
                        feature_id="base"))
        doc.add(Feature("hole", {"diameter": 16, "through": True,
                                  "pos": "point", "point": (0, 0, 0), "axis": "X"},
                        op="cut", feature_id="left-hole"))
        # For an X-axis hole, changing global X shifts the point along the
        # bore axis; it is still the same infinite line and bore instance.
        doc.add(Feature("hole", {"diameter": 8, "through": True,
                                  "pos": "point", "point": (5, 0, 0), "axis": "X"},
                        op="cut", feature_id="right-hole"))
        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        report = caught.exception.report
        self.assertEqual("duplicate_through_axis", report["failure_code"])
        self.assertEqual(["left-hole", "right-hole"], report["related_feature_ids"])
        self.assertEqual(1, report["feature_results"][-1]["solid_count"])
        self.assertIsNotNone(caught.exception.last_valid_shape)

    def test_cylindrical_cut_is_measured_as_hole_without_hole_kind(self):
        doc = Document("cylinder-cut-hole")
        doc.add(Feature("box", {"length": 20, "width": 20, "height": 20,
                                 "base": False}, feature_id="body"))
        doc.add(Feature("cylinder", {"radius": 3, "height": 40,
                                      "center": [0, 0, -20], "axis": "Z",
                                      "base": True}, op="cut", feature_id="subtract-tool"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["planned_hole_instance_count"], report)
        self.assertEqual(1, report["active_through_hole_count"], report)
        self.assertEqual("verified_open", report["hole_instances"][0]["status"], report)
        self.assertEqual("subtract-tool", report["hole_instances"][0]["feature_id"])
        intent = report["feature_results"][1]["cylindrical_cut_intents"][0]
        self.assertEqual("verified_intent_candidate", intent["classification"])
        bounds = report["bbox_mm"]
        checks = _local_geometry_checks(
            K.shape_to_polys(_shape),
            [bounds[i + 3] - bounds[i] for i in range(3)],
            {"body": {"kind": "box", "operation": "add", "parameters": {}},
             "subtract-tool": {"kind": "cylinder", "operation": "cut",
                               "parameters": {"radius": 3}}},
            brep_report=report)
        self.assertEqual("passed", checks["state"], checks)
        self.assertTrue(checks["hole_measurement_complete"], checks)
        self.assertEqual("complete", checks["hole_measurement_state"])
        self.assertEqual("subtract-tool", checks["hole_features_from_plan"][0]["feature_id"])

    def test_high_side_blind_cylindrical_cut_is_measured(self):
        doc = Document("blind-cylinder-cut")
        doc.add(Feature("box", {"length": 20, "width": 20, "height": 20,
                                 "base": False}, feature_id="body"))
        doc.add(Feature("cylinder", {"radius": 3, "height": 10,
                                      "center": [0, 0, 5], "axis": "Z",
                                      "base": False}, op="cut", feature_id="subtract-tool"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(0, report["unverified_cut_intent_count"], report)
        self.assertEqual("verified_blind", report["hole_instances"][0]["status"], report)
        bounds = report["bbox_mm"]
        checks = _local_geometry_checks(
            K.shape_to_polys(_shape),
            [bounds[i + 3] - bounds[i] for i in range(3)],
            {"body": {"kind": "box", "operation": "add", "parameters": {}},
             "subtract-tool": {"kind": "cylinder", "operation": "cut",
                               "parameters": {"radius": 3}}},
            brep_report=report)
        self.assertEqual("passed", checks["state"], checks)
        self.assertTrue(checks["hole_measurement_complete"], checks)
        self.assertEqual("complete", checks["hole_measurement_state"])

    def test_low_side_blind_cylindrical_cut_is_measured(self):
        doc = Document("low-side-blind-cylinder-cut")
        doc.add(Feature("box", {"length": 20, "width": 20, "height": 20,
                                 "base": False}, feature_id="body"))
        doc.add(Feature("cylinder", {"radius": 3, "height": 10,
                                      "center": [0, 0, -10], "axis": "Z",
                                      "base": True}, op="cut", feature_id="subtract-tool"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(0, report["unverified_cut_intent_count"], report)
        blind = report["hole_instances"][0]
        self.assertEqual("verified_blind", blind["status"], report)
        self.assertEqual("negative", blind["opening_side"], report)
        self.assertAlmostEqual(10.0, blind["measured_depth_mm"], places=5)

    def test_blind_and_through_cuts_use_local_intervals_on_xyz_faces(self):
        cases = (
            {
                "name": "X-positive",
                "axis": "X",
                "boss": {"radius": 6, "height": 8, "center": (50, -20, 10), "axis": "X"},
                "through": {"radius": 3, "height": 100, "center": (-50, 18, 10), "axis": "X"},
                "blind": {"radius": 4, "height": 6, "center": (44, 0, 10), "axis": "X"},
                "opening_side": "positive",
                "host_interval": [-50.0, 50.0],
                "extended_bound": 58.0,
            },
            {
                "name": "Y-positive",
                "axis": "Y",
                "boss": {"radius": 6, "height": 8, "center": (-40, 30, 10), "axis": "Y"},
                "through": {"radius": 3, "height": 60, "center": (18, -30, 10), "axis": "Y"},
                "blind": {"radius": 4, "height": 6, "center": (0, 24, 10), "axis": "Y"},
                "opening_side": "positive",
                "host_interval": [-30.0, 30.0],
                "extended_bound": 38.0,
            },
            {
                "name": "Z-negative-opening",
                "axis": "Z",
                "boss": {"radius": 6, "height": 8, "center": (-25, 0, 20), "axis": "Z"},
                "through": {"radius": 3, "height": 20, "center": (-15, 0, 0), "axis": "Z"},
                "blind": {"radius": 4, "height": 6, "center": (25, 0, 0), "axis": "Z"},
                "opening_side": "negative",
                "host_interval": [0.0, 20.0],
                "extended_bound": 28.0,
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                doc = Document("local-axis-%s" % case["name"])
                doc.add(Feature("box", {"length": 100, "width": 60, "height": 20,
                                         "center": (0, 0, 10), "base": False},
                                feature_id="base"))
                doc.add(Feature("cylinder", case["boss"], feature_id="remote-boss"))
                doc.add(Feature("cylinder", case["through"], op="cut",
                                feature_id="through-%s" % case["name"]))
                doc.add(Feature("cylinder", case["blind"], op="cut",
                                feature_id="blind-%s" % case["name"]))

                shape, report = K.build_document(doc)

                self.assertEqual("passed", report["geometry_state"], report)
                self.assertTrue(report["brep_valid"], report)
                self.assertEqual([], report["unverified_cut_intents"], report)
                self.assertEqual(["verified_open", "verified_blind"],
                                 [row["status"] for row in report["hole_instances"]], report)
                through, blind = report["hole_instances"]
                self.assertEqual(case["axis"], through["axis"])
                self.assertEqual(case["axis"], blind["axis"])
                self.assertAlmostEqual(6.0, through["measured_diameter_mm"], places=5)
                self.assertAlmostEqual(8.0, blind["measured_diameter_mm"], places=5)
                self.assertAlmostEqual(6.0, blind["measured_depth_mm"], places=5)
                self.assertEqual(case["opening_side"], blind["opening_side"])
                self.assertEqual(case["host_interval"], blind["host_axis_interval_mm"])
                self.assertAlmostEqual(case["extended_bound"],
                                       report["bbox_mm"][{"X": 3, "Y": 4, "Z": 5}[case["axis"]]],
                                       places=5)
                bounds = report["bbox_mm"]
                summary = _local_geometry_checks(
                    K.shape_to_polys(shape),
                    [bounds[i + 3] - bounds[i] for i in range(3)],
                    {feature.feature_id: {"kind": feature.kind, "operation": feature.op,
                                  "parameters": feature.params}
                     for feature in doc.features},
                    brep_report=report)
                self.assertEqual("passed", summary["state"], summary)
                self.assertTrue(summary["hole_measurement_complete"], summary)
                self.assertEqual(1, summary["through_hole_count_verified"], summary)
                self.assertEqual("measured_by_native_brep", summary["through_hole_result"])

    def test_blind_hole_from_recess_floor_uses_local_material_face(self):
        doc = Document("blind-hole-from-recess-floor")
        doc.add(Feature("box", {"length": 100, "width": 60, "height": 20,
                                 "center": (0, 0, 10), "base": False}, feature_id="base"))
        doc.add(Feature("pocket", {"length": 20, "width": 20, "depth": 5,
                                    "axis": "Z", "side": "positive", "x": 25, "y": 0},
                        op="cut", feature_id="recess"))
        doc.add(Feature("cylinder", {"radius": 4, "height": 6,
                                      "center": (25, 0, 9), "axis": "Z", "base": True},
                        op="cut", feature_id="blind-from-recess-floor"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["geometry_state"], report)
        self.assertEqual([], report["unverified_cut_intents"], report)
        blind = next(row for row in report["hole_instances"]
                     if row["feature_id"] == "blind-from-recess-floor")
        self.assertEqual("verified_blind", blind["status"], report)
        self.assertEqual([0.0, 15.0], blind["host_axis_interval_mm"])
        self.assertAlmostEqual(6.0, blind["measured_depth_mm"], places=5)

    def test_blind_cutter_crossing_separate_local_material_intervals_stays_unverified(self):
        doc = Document("blind-cylinder-crosses-separated-material-intervals")
        doc.add(Feature("box", {"length": 100, "width": 60, "height": 20,
                                 "center": (0, 0, 10), "base": False}, feature_id="base"))
        doc.add(Feature("box", {"length": 12, "width": 12, "height": 4,
                                 "center": (0, 0, 10), "base": False},
                        op="cut", feature_id="internal-cavity"))
        doc.add(Feature("cylinder", {"radius": 4, "height": 16,
                                      "center": (0, 0, 5), "axis": "Z", "base": True},
                        op="cut", feature_id="ambiguous-cylinder-cut"))

        shape, report = K.build_document(doc)

        self.assertTrue(report["brep_valid"], report)
        self.assertTrue(report["closed"], report)
        self.assertEqual(1, report["solid_count"], report)
        self.assertEqual("needs_review", report["geometry_state"], report)
        self.assertEqual(1, report["unverified_cut_intent_count"], report)
        intent = report["unverified_cut_intents"][0]
        self.assertEqual("unverified", intent["status"], report)
        self.assertEqual([[0.0, 8.0], [12.0, 20.0]],
                         intent["host_axis_intervals_mm"], report)
        self.assertEqual([[0.0, 8.0], [12.0, 20.0]],
                         intent["cutter_overlapped_material_intervals_mm"], report)
        self.assertFalse(any(row.get("status") == "verified_blind"
                             for row in report["hole_instances"]), report)
        bounds = report["bbox_mm"]
        summary = _local_geometry_checks(
            K.shape_to_polys(shape), [bounds[i + 3] - bounds[i] for i in range(3)],
            {feature.feature_id: {"kind": feature.kind, "operation": feature.op,
                          "parameters": feature.params} for feature in doc.features},
            brep_report=report)
        self.assertEqual("needs_review", summary["state"], summary)
        self.assertFalse(summary["hole_measurement_complete"], summary)
        self.assertEqual("incomplete_or_unverified", summary["hole_measurement_state"])

    def test_local_axis_interval_failure_stays_unverified_without_global_bbox_fallback(self):
        host = K.make_box(20, 20, 20, center=(0, 0, 10), base=False)
        tool = K.make_cylinder(3, 10, center=(0, 0, -10), axis="Z", base=True)
        bbox = K._bbox(host)
        outcomes = (
            ("empty material intervals", {"material_segments": []}),
            ("intersection failure", RuntimeError("synthetic line intersection failure")),
        )
        for label, outcome in outcomes:
            with self.subTest(label=label):
                with mock.patch("ai_modeler.core.geometry.occ_kernel._line_material_segments",
                                side_effect=outcome if isinstance(outcome, Exception)
                                else None, return_value=outcome if not isinstance(outcome, Exception)
                                else mock.DEFAULT):
                    intent = K._pure_cylindrical_tool_intent(
                        tool, "uncertain-cut", bbox, host)
                self.assertEqual("unverified", intent["status"], intent)
                self.assertEqual("unverified_cylindrical_cut",
                                 intent["classification"], intent)
                self.assertNotIn("through", intent)
                self.assertIn("不使用全局包围盒猜测", intent["reason"])

    def test_complexity_limited_self_interference_remains_needs_review(self):
        doc = Document("configured-self-interference-budget")
        doc.add(Feature("box", {"length": 20, "width": 20, "height": 20,
                                 "base": False}, feature_id="box"))
        shape = doc.build_brep(self_intersection_face_limit=5)
        report = doc.brep_report
        self.assertEqual("not_checked_complexity_limit",
                         report["self_intersection"]["status"], report)
        self.assertEqual(6, report["self_intersection"]["face_count"], report)
        self.assertEqual(5, report["self_intersection"]["face_limit"], report)
        self.assertEqual("needs_review", report["geometry_state"], report)
        checks = _local_geometry_checks(
            K.shape_to_polys(shape), [20, 20, 20],
            {"box": {"kind": "box", "operation": "add", "parameters": {}}},
            brep_report=report)
        self.assertEqual("needs_review", checks["state"], checks)

        doc.build_brep(self_intersection_face_limit=K.SELF_INTERSECTION_FACE_LIMIT)
        refreshed = doc.brep_report
        self.assertEqual("checked_no_intersection",
                         refreshed["self_intersection"]["status"], refreshed)
        self.assertEqual(K.SELF_INTERSECTION_FACE_LIMIT,
                         refreshed["self_intersection"]["face_limit"], refreshed)

    def test_geometry_support_labels_match_current_piston_and_crankshaft_builders(self):
        self.assertEqual("exact", K.GEOMETRY_SUPPORT["piston"][0])
        self.assertIn("原生 OCCT 回转", K.GEOMETRY_SUPPORT["piston"][1])
        self.assertEqual("approximate", K.GEOMETRY_SUPPORT["crankshaft"][0])
        self.assertIn("矩形曲柄臂", K.GEOMETRY_SUPPORT["crankshaft"][1])
        self.assertNotIn("网格配方", K.GEOMETRY_SUPPORT["crankshaft"][1])
        self.assertEqual("approximate", K.GEOMETRY_SUPPORT["bevel_gear"][0])
        self.assertIn("facet-BREP", K.GEOMETRY_SUPPORT["bevel_gear"][1])

    def test_renamed_revolved_cylindrical_cut_cannot_bypass_duplicate_guard(self):
        doc = Document("renamed-cylinder-cut")
        doc.add(Feature("box", {"length": 30, "width": 30, "height": 20,
                                 "base": False}, feature_id="body"))
        doc.add(Feature("revolve", {"profile": [[0, 0], [5, 0], [5, 30], [0, 30]],
                                     "angle": 360, "center": [0, 0, -15], "axis": "Z"},
                        op="cut", feature_id="revolved-cutter"))
        doc.add(Feature("cylinder", {"radius": 4, "height": 40,
                                      "center": [0, 0, -20], "axis": "Z",
                                      "base": True}, op="cut", feature_id="cylinder-cutter"))
        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        self.assertEqual("duplicate_through_axis", caught.exception.report["failure_code"])
        self.assertEqual(["cylinder-cutter", "revolved-cutter"],
                         caught.exception.report["related_feature_ids"])

    def test_transverse_through_hole_pattern_measures_three_instances(self):
        doc = Document("transverse-hole-pattern")
        doc.add(Feature("box", {"length": 80, "width": 80, "height": 60,
                                 "center": [0, 0, 0], "base": False}, feature_id="body"))
        doc.add(Feature("hole", {"diameter": 8, "through": True,
                                  "pos": "point", "point": [0, 0, 0], "axis": "Z"},
                        op="cut", feature_id="hole"))
        doc.add(Feature("pattern", {"mode": "linear", "count": 3,
                                     "step": [15, 0, 0]}, op="cut",
                        feature_id="pattern",
                        relations=[{"type": "pattern_source", "feature_id": "hole"}]))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual(3, report["active_through_hole_count"], report)
        self.assertEqual(3, report["through_hole_count"], report)
        self.assertEqual(["verified_open"] * 3,
                         [row["status"] for row in report["hole_instances"]])

    def test_larger_through_hole_supersedes_earlier_smaller_instance(self):
        doc = Document("through-hole-enlargement")
        doc.add(Feature("box", {"length": 50, "width": 40, "height": 30,
                                 "center": (0, 0, 0), "base": False}, feature_id="base"))
        doc.add(Feature("hole", {"diameter": 10, "through": True,
                                  "pos": "point", "point": (0, 0, 0), "axis": "Z"},
                        op="cut", feature_id="hole-10"))
        doc.add(Feature("hole", {"diameter": 16, "through": True,
                                  "pos": "point", "point": (0, 0, 5), "axis": "Z"},
                        op="cut", feature_id="hole-16"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        holes = report["hole_instances"]
        self.assertEqual("superseded", holes[0]["status"])
        self.assertEqual("hole-16", holes[0]["superseded_by"])
        self.assertEqual("verified_open", holes[1]["status"])
        self.assertAlmostEqual(16.0, holes[1]["measured_diameter_mm"], places=6)

    def test_blind_hole_can_be_extended_to_through(self):
        doc = Document("blind-to-through")
        doc.add(Feature("box", {"length": 50, "width": 40, "height": 30,
                                 "center": (0, 0, 0), "base": False}, feature_id="base"))
        doc.add(Feature("hole", {"diameter": 10, "through": False, "depth": 5,
                                  "pos": "point", "point": (0, 0, 0), "axis": "Z"},
                        op="cut", feature_id="blind-10"))
        doc.add(Feature("hole", {"diameter": 10, "through": True,
                                  "pos": "point", "point": (0, 0, 5), "axis": "Z"},
                        op="cut", feature_id="through-10"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual("superseded", report["hole_instances"][0]["status"])
        self.assertEqual("through-10", report["hole_instances"][0]["superseded_by"])
        self.assertEqual("verified_open", report["hole_instances"][1]["status"])

    def test_cylindrical_blind_cut_uses_local_host_interval_with_remote_revolve_boss(self):
        doc = Document("local-blind-cylinder-with-remote-boss")
        doc.add(Feature("box", {"length": 100, "width": 60, "height": 20,
                                 "center": (0, 0, 10), "base": False}, feature_id="base"))
        doc.add(Feature("cylinder", {"radius": 5, "height": 20,
                                      "center": (0, 0, 0), "axis": "Z"},
                        op="cut", feature_id="through-cylinder"))
        doc.add(Feature("revolve", {"profile": [[0, 20], [6, 20], [6, 28], [0, 28]],
                                     "angle": 360, "center": (-25, 0, 0), "axis": "Z"},
                        op="add", feature_id="remote-boss"))
        doc.add(Feature("cylinder", {"radius": 4, "height": 6,
                                      "center": (25, 0, 14), "axis": "Z", "base": True},
                        op="cut", feature_id="blind-cylinder"))
        shape, report = K.build_document(doc)
        self.assertEqual("passed", report["geometry_state"], report)
        self.assertEqual([], report["unverified_cut_intents"], report)
        self.assertTrue(report["all_declared_holes_verified"], report)
        self.assertEqual(["verified_open", "verified_blind"],
                         [row["status"] for row in report["hole_instances"]], report)
        blind = report["hole_instances"][1]
        self.assertAlmostEqual(8.0, blind["measured_diameter_mm"], places=5)
        self.assertAlmostEqual(6.0, blind["measured_depth_mm"], places=4)
        self.assertEqual([0.0, 20.0], blind["host_axis_interval_mm"])
        self.assertAlmostEqual(28.0, report["bbox_mm"][5] - report["bbox_mm"][2], places=5)

    def test_shallower_larger_blind_counterbore_preserves_deeper_wall(self):
        doc = Document("blind-counterbore")
        doc.add(Feature("box", {"length": 50, "width": 40, "height": 30,
                                 "center": (0, 0, 0), "base": False}, feature_id="base"))
        doc.add(Feature("hole", {"diameter": 10, "through": False, "depth": 10,
                                  "pos": "point", "point": (0, 0, 0), "axis": "Z"},
                        op="cut", feature_id="blind-10x10"))
        doc.add(Feature("hole", {"diameter": 16, "through": False, "depth": 4,
                                  "pos": "point", "point": (0, 0, 5), "axis": "Z"},
                        op="cut", feature_id="counterbore-16x4"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual("verified_blind_segment", report["hole_instances"][0]["status"])
        self.assertEqual([5.0, 11.0], report["hole_instances"][0]["preserved_wall_measurement"]["requested_interval_mm"])
        self.assertAlmostEqual(6.0, report["hole_instances"][0]["measured_depth_mm"], places=6)
        self.assertEqual("verified_counterbore", report["hole_instances"][1]["status"])

    def test_wide_shallow_blind_plus_narrow_through_remains_a_counterbore(self):
        doc = Document("wide-blind-plus-through")
        doc.add(Feature("box", {"length": 50, "width": 40, "height": 30,
                                 "center": (0, 0, 0), "base": False}, feature_id="base"))
        doc.add(Feature("hole", {"diameter": 20, "through": False, "depth": 5,
                                  "pos": "point", "point": (0, 0, 0), "axis": "Z"},
                        op="cut", feature_id="wide-blind-20x5"))
        doc.add(Feature("hole", {"diameter": 10, "through": True,
                                  "pos": "point", "point": (0, 0, 4), "axis": "Z"},
                        op="cut", feature_id="through-10"))
        _shape, report = K.build_document(doc)
        self.assertEqual("passed", report["state"], report)
        self.assertEqual("verified_blind_segment", report["hole_instances"][0]["status"])
        self.assertEqual("verified_open", report["hole_instances"][1]["status"])
        self.assertEqual(2, report["active_hole_instance_count"])
        self.assertEqual(0, report["superseded_hole_instance_count"])

    def _nine_hole_recess_document(self):
        doc = Document("nine-hole-recess")
        doc.add(Feature("box", {
            "length": 100, "width": 100, "height": 30,
            "center": (0, 0, 0), "axis": "Y", "base": False,
        }, feature_id="base"))
        doc.add(Feature("pocket", {
            "length": 90, "width": 30, "depth": 5,
            "axis": "Y", "x": 0, "y": 0,
        }, op="cut", feature_id="center-recess"))
        for col, x in enumerate((-35, 0, 35)):
            for row, z in enumerate((-32, 0, 32)):
                diameter = 8 if row == 1 else 12
                doc.add(Feature("hole", {
                    "diameter": diameter, "through": True,
                    "pos": "point", "point": (x, 0, z), "axis": "Y",
                }, op="cut", feature_id="hole-%d-%d" % (col, row)))
        return doc

    def test_native_box_recess_and_nine_through_holes_are_measured(self):
        shape, report = K.build_document(self._nine_hole_recess_document())

        self.assertEqual("passed", report["state"], report)
        self.assertTrue(report["brep_valid"])
        self.assertEqual(1, report["solid_count"])
        self.assertTrue(report["closed"])
        self.assertEqual(0, report["free_edge_count"])
        self.assertAlmostEqual(100, report["bbox_mm"][3] - report["bbox_mm"][0], places=4)
        self.assertAlmostEqual(30, report["bbox_mm"][4] - report["bbox_mm"][1], places=4)
        self.assertAlmostEqual(100, report["bbox_mm"][5] - report["bbox_mm"][2], places=4)
        self.assertEqual(9, report["planned_hole_instance_count"])
        self.assertEqual(9, len(report["hole_instances"]))
        self.assertTrue(all(row["status"] == "verified_open"
                            for row in report["hole_instances"]))
        self.assertEqual([8.0, 12.0], sorted({row["measured_diameter_mm"]
                                             for row in report["hole_instances"]}))
        self.assertTrue(all(not row["centerline_proof"]["material_segments"]
                            for row in report["hole_instances"]))
        self.assertGreater(K.volume(shape), 0)

        pocket = next(row for row in report["feature_results"]
                      if row["feature_id"] == "center-recess")
        self.assertEqual("measured", pocket["depth_measurement"]["state"])
        self.assertAlmostEqual(5, pocket["depth_measurement"]["measured_depth_mm"], places=3)
        self.assertAlmostEqual(-13_500, pocket["volume_delta_mm3"], delta=0.001)

    def test_cadplan_preview_exposes_brep_contract_without_claiming_visual_or_catia_review(self):
        result = preview_plan(self._nine_hole_recess_plan())

        self.assertEqual("previewed", result["state"], result)
        checks = result["local_geometry_checks"]
        self.assertEqual("passed", checks["state"])
        self.assertEqual("passed", checks["geometry_state"])
        self.assertEqual("occt_brep", checks["backend"])
        self.assertTrue(checks["brep_valid"])
        self.assertEqual(1, checks["solid_count"])
        self.assertTrue(checks["closed"])
        self.assertEqual(0, checks["free_edge_count"])
        self.assertEqual(9, checks["expected_hole_instance_count"])
        self.assertEqual(9, checks["hole_instance_count"])
        self.assertEqual(9, checks["through_hole_count_expected"])
        self.assertEqual(9, checks["through_hole_count_verified"])
        self.assertEqual("not_performed", checks["visual_validation"])
        self.assertEqual("not_performed", checks["catia_geometry_verification"])

    def test_negative_face_pocket_schema_is_local_exact_and_catia_export_is_limited(self):
        plan = self._nine_hole_recess_plan()
        plan["features"] = plan["features"][:2]
        plan["features"][1]["parameters"]["side"] = "negative"
        plan["features"][1]["sources"] = self._sources(plan["features"][1]["parameters"])

        errors = []
        try:
            import jsonschema
            errors = list(jsonschema.Draft202012Validator(schema_document()).iter_errors(plan))
        except ImportError:
            self.skipTest("jsonschema is not installed")

        self.assertEqual([], errors, [item.message for item in errors])
        validation = validate_plan(plan)
        self.assertEqual("valid", validation["state"], validation)
        self.assertEqual("unsupported", validation["support"][-1]["status"])
        self.assertEqual("exact", validation["geometry_support"][-1]["status"])

    def test_positive_and_negative_face_pockets_preserve_exact_depth_and_volume(self):
        results = []
        for side in ("positive", "negative"):
            doc = Document("pocket-%s" % side)
            doc.add(Feature("box", {
                "length": 100, "width": 100, "height": 30,
                "center": (0, 0, 0), "axis": "Y", "base": False,
            }, feature_id="base"))
            doc.add(Feature("pocket", {
                "length": 90, "width": 30, "depth": 5,
                "axis": "Y", "side": side, "x": 0, "y": 0,
            }, op="cut", feature_id="pocket"))
            _shape, report = K.build_document(doc)
            measure = report["feature_results"][1]["depth_measurement"]
            self.assertEqual("passed", report["state"], report)
            self.assertEqual("measured", measure["state"])
            self.assertEqual(side, measure["side"])
            self.assertAlmostEqual(5, measure["measured_depth_mm"], places=3)
            self.assertAlmostEqual(-13_500, report["feature_results"][1]["volume_delta_mm3"],
                                   delta=0.001)
            results.append(report["volume_mm3"])
        self.assertAlmostEqual(results[0], results[1], places=3)

    def test_appended_revolve_is_analytic_and_changes_the_valid_solid(self):
        doc = Document("revolve-add")
        doc.add(Feature("box", {
            "length": 40, "width": 40, "height": 40,
            "center": (0, 0, 10), "axis": "Z", "base": False,
        }, feature_id="base"))
        doc.add(Feature("revolve", {
            "profile": [(10, 0), (30, 0), (30, 25), (10, 25)],
            "angle": 360, "center": (0, 0, 0), "axis": "Z",
        }, op="add", feature_id="revolve"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["state"], report)
        self.assertEqual(1, report["solid_count"])
        self.assertTrue(report["closed"])
        revolve = report["feature_results"][1]
        self.assertEqual("exact", revolve["geometry_support"])
        self.assertEqual("analytic_occt_revolve", revolve["builder"])
        self.assertGreater(revolve["volume_delta_mm3"], 30_000)

    def test_self_intersecting_revolve_is_rejected_transactionally(self):
        bow_tie = [(0, 0), (4, 4), (0, 4), (3, 0), (4, 0)]
        with self.assertRaisesRegex(ValueError, "自相交"):
            K.validate_simple_profile(bow_tie, radial=True)

        doc = Document("revolve-rollback")
        doc.add(Feature("box", {
            "length": 40, "width": 40, "height": 40,
            "center": (0, 0, 0), "axis": "Z", "base": False,
        }, feature_id="valid-base"))
        doc.add(Feature("revolve", {
            "profile": bow_tie, "angle": 360,
            "center": (0, 0, 0), "axis": "Z",
        }, op="add", feature_id="invalid-revolve"))

        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        error = caught.exception
        self.assertEqual("invalid-revolve", error.report["failed_feature_id"])
        self.assertIsNotNone(error.last_valid_shape)
        previous = K.inspect_shape(error.last_valid_shape, check_self_intersection=False)
        self.assertTrue(previous["brep_valid"])
        self.assertEqual(1, previous["solid_count"])
        self.assertAlmostEqual(64_000, previous["volume_mm3"], places=3)

    def test_hole_verification_detects_actual_diameter_not_just_planned_diameter(self):
        doc = Document("diameter-measurement")
        doc.add(Feature("box", {
            "length": 80, "width": 80, "height": 30,
            "center": (0, 0, 0), "axis": "Y", "base": False,
        }, feature_id="base"))
        doc.add(Feature("hole", {
            "diameter": 8, "through": True, "pos": "point",
            "point": (20, 0, 15), "axis": "Y",
        }, op="cut", feature_id="hole"))
        shape, report = K.build_document(doc)
        wrong_expectation = dict(report["hole_instances"][0], diameter_mm=12)

        measured = K._check_hole_instances(shape, [wrong_expectation])[0]

        self.assertEqual("diameter_mismatch", measured["status"])
        self.assertEqual([8.0], measured["diameter_candidates_mm"])
        self.assertIsNone(measured["measured_diameter_mm"])

    def test_blind_hole_reports_measured_depth_and_rejects_through_depth(self):
        doc = Document("blind-depth")
        doc.add(Feature("box", {
            "length": 80, "width": 80, "height": 30,
            "center": (0, 0, 0), "axis": "Y", "base": False,
        }, feature_id="base"))
        doc.add(Feature("hole", {
            "diameter": 8, "through": False, "depth": 5,
            "pos": "point", "point": (20, 0, 15), "axis": "Y",
        }, op="cut", feature_id="blind"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["state"], report)
        hole = report["hole_instances"][0]
        self.assertEqual("verified_blind", hole["status"])
        self.assertAlmostEqual(8, hole["measured_diameter_mm"], places=6)
        self.assertAlmostEqual(5, hole["measured_depth_mm"], places=3)

        doc.features[-1].params["depth"] = 30
        with self.assertRaisesRegex(K.GeometryBuildError, "盲孔深度"):
            K.build_document(doc)

    def test_explicit_blind_entry_point_cuts_from_recess_floor_into_local_material(self):
        doc = Document("blind-hole-from-recess-floor")
        doc.add(Feature("box", {
            "length": 100, "width": 50, "height": 80,
            "center": (0, 0, 40), "base": False,
        }, feature_id="base"))
        doc.add(Feature("box", {
            "length": 30, "width": 35, "height": 65,
            "center": (0, -7.5, 40), "base": False,
        }, op="cut", feature_id="front-recess"))
        doc.add(Feature("hole", {
            "diameter": 8, "through": False, "depth": 8, "axis": "Y",
            # `point` keeps its old axis-plane projection. The entry coordinate
            # is explicit and lies on the recessed wall at y=10.
            "pos": "point", "point": (0, -24.5, 40),
            "entry_point": (0, 10, 40), "direction": "positive",
        }, op="cut", feature_id="back-blind"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["geometry_state"], report)
        blind = report["hole_instances"][-1]
        self.assertEqual("verified_blind", blind["status"], report)
        self.assertEqual("negative", blind["opening_side"])
        self.assertEqual([10.0, 25.0], blind["host_axis_interval_mm"])
        self.assertAlmostEqual(8.0, blind["measured_depth_mm"], places=5)
        self.assertEqual([10.0, 18.0], blind["matching_cylindrical_faces"][0]["axis_interval_mm"])
        hole_result = next(row for row in report["feature_results"]
                           if row["feature_id"] == "back-blind")
        self.assertAlmostEqual(-math.pi * 4.0 ** 2 * 8.0,
                               hole_result["volume_delta_mm3"], delta=1e-3)

    def test_explicit_blind_entry_point_supports_opposite_side_opening(self):
        doc = Document("blind-hole-from-positive-y-face")
        doc.add(Feature("box", {
            "length": 40, "width": 50, "height": 40,
            "center": (0, 0, 0), "base": False,
        }, feature_id="base"))
        doc.add(Feature("hole", {
            "diameter": 6, "through": False, "depth": 6, "axis": "Y",
            "pos": "point", "point": (0, 24.5, 0),
            "entry_point": (0, 25, 0), "direction": "negative",
        }, op="cut", feature_id="positive-face-blind"))

        _shape, report = K.build_document(doc)

        self.assertEqual("passed", report["geometry_state"], report)
        blind = report["hole_instances"][0]
        self.assertEqual("verified_blind", blind["status"], report)
        self.assertEqual("positive", blind["opening_side"])
        self.assertEqual([-25.0, 25.0], blind["host_axis_interval_mm"])
        self.assertAlmostEqual(6.0, blind["measured_depth_mm"], places=5)
        self.assertAlmostEqual(19.0, blind["matching_cylindrical_faces"][0]["axis_interval_mm"][0], places=5)
        hole_result = next(row for row in report["feature_results"]
                           if row["feature_id"] == "positive-face-blind")
        self.assertAlmostEqual(-math.pi * 3.0 ** 2 * 6.0,
                               hole_result["volume_delta_mm3"], delta=1e-3)

    def test_axis_base_and_center_semantics_are_consistent_across_xyz(self):
        expected_centered = {
            "X": (6, 12, 20),
            "Y": (20, 6, 12),
            "Z": (12, 20, 6),
        }
        for axis, expected_size in expected_centered.items():
            with self.subTest(axis=axis):
                doc = Document("axis-%s" % axis)
                doc.add(Feature("box", {
                    "length": 12, "width": 20, "height": 6,
                    "center": (10, 20, 30), "axis": axis, "base": False,
                }, feature_id="box"))
                shape, report = K.build_document(doc)
                actual_size = [report["bbox_mm"][i + 3] - report["bbox_mm"][i]
                               for i in range(3)]
                self.assertTrue(report["brep_valid"])
                for actual, expected in zip(actual_size, expected_size):
                    self.assertAlmostEqual(expected, actual, places=5)
                self.assertAlmostEqual(K.volume(shape), 12 * 20 * 6, places=4)

    def test_self_intersection_analyzer_reads_ocp_linked_check_result(self):
        shape = K.make_box(20, 30, 40)

        result = K._self_intersection_check(shape)

        self.assertEqual("checked_no_intersection", result["status"], result)
        self.assertEqual("BOPAlgo_ArgumentAnalyzer(SelfInterMode=True, UseOBB=True)",
                         result["method"])

    def test_failed_cut_reports_measured_zero_delta_and_coincident_face_hint(self):
        doc = Document("coincident-face-cut")
        doc.add(Feature("box", {
            "length": 100, "width": 50, "height": 20,
            "center": (0, 0, 0), "axis": "Z", "base": False,
        }, feature_id="base"))
        doc.add(Feature("box", {
            "length": 5, "width": 5, "height": 5,
            "center": (0, 0, 12.5), "axis": "Z", "base": False,
        }, op="cut", feature_id="coincident-cut"))

        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        effect = caught.exception.report["failed_feature_effect"]
        self.assertEqual("coincident-cut", effect["feature_id"])
        self.assertEqual("no_op", effect["state"])
        self.assertEqual("coincident_face", effect["failure_code"])
        self.assertAlmostEqual(100_000.0, effect["before_volume_mm3"], places=5)
        self.assertAlmostEqual(100_000.0, effect["after_volume_mm3"], places=5)
        self.assertAlmostEqual(0.0, effect["volume_delta_mm3"], places=5)
        self.assertIn("完全重合", effect["warning"])
        self.assertIn("0.001", effect["warning"])

    def test_unintersecting_cut_reports_measured_no_material_change(self):
        doc = Document("air-cut")
        doc.add(Feature("box", {
            "length": 100, "width": 50, "height": 20,
            "center": (0, 0, 0), "axis": "Z", "base": False,
        }, feature_id="base"))
        doc.add(Feature("box", {
            "length": 5, "width": 5, "height": 5,
            "center": (0, 0, 500), "axis": "Z", "base": False,
        }, op="cut", feature_id="air-cut"))

        with self.assertRaises(K.GeometryBuildError) as caught:
            K.build_document(doc)
        effect = caught.exception.report["failed_feature_effect"]
        self.assertEqual("no_op", effect["state"])
        self.assertEqual("cut_no_material_change", effect["failure_code"])
        self.assertEqual(0.0, effect["volume_delta_mm3"])
        self.assertIn("没有改变材料", effect["warning"])

    def test_through_hole_pattern_along_axis_is_explicit_noop(self):
        features = [
            {"id": "base", "kind": "box", "operation": "add",
             "parameters": {"length": 80, "width": 80, "height": 60,
                            "center": [0, 0, 0], "base": False},
             "sources": self._sources({"length": 80, "width": 80, "height": 60,
                                       "center": [0, 0, 0]})},
            {"id": "hole", "kind": "hole", "operation": "cut",
             "parameters": {"diameter": 8, "through": True, "pos": "point",
                            "point": [0, 0, 0], "axis": "Z"},
             "sources": self._sources({"diameter": 8, "point": [0, 0, 0],
                                       "axis": "Z"})},
            {"id": "pattern", "kind": "pattern", "operation": "cut",
             "parameters": {"mode": "linear", "count": 3, "step": [0, 0, 15]},
             "sources": self._sources({"count": 3, "step": [0, 0, 15]}),
             "relations": [{"type": "pattern_source", "feature_id": "hole"}]},
        ]
        plan = self._nine_hole_recess_plan()
        plan["features"] = features

        result = preview_plan(plan)

        self.assertEqual("previewed", result["state"], result)
        checks = result["local_geometry_checks"]
        self.assertEqual("needs_review", checks["state"])
        pattern = next(row for row in checks["feature_results"]
                       if row["feature_id"] == "pattern")
        self.assertEqual("no_op", pattern["state"])
        self.assertEqual(0.0, pattern["volume_delta_mm3"])
        self.assertIn("没有去除新增材料", pattern["warning"])
        self.assertEqual(1, checks["hole_instance_count"])


if __name__ == "__main__":
    unittest.main()
