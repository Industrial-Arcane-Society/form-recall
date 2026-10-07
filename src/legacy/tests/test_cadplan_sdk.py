import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from ai_modeler.cadplan import (
    CADPLAN_SCHEMA_VERSION,
    FEATURE_SPECS,
    build_plan,
    preview_plan,
    schema_document,
    validate_plan,
)
from ai_modeler.cadplan import _to_document
from ai_modeler.docmodel import Document, Feature, KIND_LABELS
from ai_modeler.catia_export import build_catia_features, render_push_vbs
from ai_modeler.softrender import BG_BOT, BG_TOP


CASE_PARAMETERS = {
    "box": {"length": 80, "width": 50, "height": 12},
    "cylinder": {"radius": 10, "height": 20},
    "sphere": {"radius": 10},
    "prism": {"profile": [[-5, -5], [5, -5], [0, 5]], "height": 10},
    "revolve": {"profile": [[0, 0], [5, 0], [5, 10], [0, 10]], "angle": 360},
    "gear": {"teeth": 20, "module": 2, "height": 8},
    "loft": {"root_chord": 30, "tip_chord": 15, "span": 60, "thickness": 0.12},
    "hole": {"diameter": 8, "through": True, "pos": "point", "point": [0, 0, 6]},
    "pocket": {"length": 20, "width": 12, "depth": 3},
    "pattern": {"mode": "linear", "count": 2, "step": [20, 0, 0]},
    "helix": {"radius": 10, "wire_r": 1, "height": 20, "turns": 4},
    "spoked_wheel": {"outer_r": 30, "rim_w": 4, "hub_r": 8, "height": 8, "spokes": 6, "spoke_w": 3},
    "vbelt_pulley": {"pitch_d": 80, "grooves": 3, "height": 51, "groove_type": "A"},
    "timing_pulley": {"pitch_d": 50, "teeth": 20, "height": 12, "tooth_type": "XL"},
    "worm": {"radius": 10, "height": 40, "thread_r": 8, "wire_r": 2, "turns": 3},
    "helical_gear": {"teeth": 24, "module": 2, "height": 12},
    "bevel_gear": {"teeth": 20, "module": 2, "height": 12, "cone_deg": 45},
    "crankshaft": {"length": 80, "main_d": 20, "pin_d": 16, "stroke": 30, "throws": 2},
    "camshaft": {"length": 80, "journal_d": 20, "base_d": 18, "lift": 5, "lobes": 4, "lobe_deg": 120},
    "impeller": {"hub_d": 20, "tip_d": 60, "height": 15, "blades": 6, "blade_t": 2, "twist_deg": 25},
    "piston": {"diameter": 40, "height": 35, "pin_d": 10, "grooves": 3},
    "conrod": {"length": 80, "big_d": 30, "small_d": 18, "big_bore": 20,
               "small_bore": 10, "shank_w": 10, "thick": 8},
    "cross_joint": {"span": 50, "journal_d": 12},
    "scroll_disc": {"outer_d": 60, "height": 8, "wall_t": 2, "turns": 2.5},
    "rotor": {"radius": 30, "n": 3, "height": 12, "bore_d": 8},
    "shell": {"thickness": 2, "offset_mode": "inward",
              "removed_faces": ["shell-face-v1:" + "a" * 64]},
    "socket_head_bolt": {"shank_d": 8, "thread_pitch": 1.25,
                         "length": 24, "head_d": 13, "head_h": 8,
                         "hex_s": 6, "hex_t": 4, "segments": 12},
}


def _has_numbers(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple)):
        return any(_has_numbers(item) for item in value)
    if isinstance(value, dict):
        return any(_has_numbers(item) for item in value.values())
    return False


def _source_map(parameters):
    return {key: {"type": "user"} for key, value in parameters.items() if _has_numbers(value)}


def _png_foreground_bbox(path, panel_width=330, panel_height=260):
    data = Path(path).read_bytes()
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    offset = 8
    compressed = bytearray()
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b"IDAT":
            compressed.extend(chunk)
        offset += 12 + size
    raw = zlib.decompress(bytes(compressed))
    bounds = [panel_width, panel_height, -1, -1]
    for y in range(panel_height):
        row = y * (width * 3 + 1)
        if raw[row] != 0:
            raise AssertionError("renderer PNG rows must use filter 0")
        t = y / max(1, height - 1)
        bg = tuple(int(top + (bottom - top) * t) for top, bottom in zip(BG_TOP, BG_BOT))
        for x in range(panel_width):
            pixel = tuple(raw[row + 1 + x * 3:row + 4 + x * 3])
            if pixel != bg:
                bounds[0] = min(bounds[0], x)
                bounds[1] = min(bounds[1], y)
                bounds[2] = max(bounds[2], x)
                bounds[3] = max(bounds[3], y)
    return tuple(bounds)


def _plan(kind, parameters=None):
    features = [{
        "id": "base",
        "kind": "box",
        "operation": "add",
        "parameters": {"length": 100, "width": 80, "height": 60},
        "sources": {"length": {"type": "user"}, "width": {"type": "user"},
                    "height": {"type": "user"}},
    }]
    item = {"id": "target", "kind": kind, "operation": "add",
            "parameters": dict(parameters or CASE_PARAMETERS[kind])}
    item["sources"] = _source_map(item["parameters"])
    if kind == "pattern":
        item["relations"] = [{"type": "pattern_source", "feature_id": "base"}]
    if kind == "shell":
        item["operation"] = "cut"
        item["relations"] = [{"type": "shell_target", "feature_id": "base"}]
        item["selection_binding"] = {
            "revision": 4,
            "geometry_fingerprint": "a" * 64,
            "kernel_version": "7.9.3",
            "inventory_version": 2,
            "target_feature_id": "base",
        }
    features.append(item)
    return {
        "schema_version": CADPLAN_SCHEMA_VERSION,
        "name": "SDK test",
        "units": {"length": "mm", "angle": "deg"},
        "coordinate_system": {"type": "global_cartesian", "handedness": "right",
                              "axes": ["X", "Y", "Z"], "origin": [0, 0, 0]},
        "features": features,
    }


class CADPlanSDKTests(unittest.TestCase):
    def test_converter_fails_closed_on_unknown_operation(self):
        plan = _plan("box", {"length": 20, "width": 10, "height": 8})
        plan["features"][1]["operation"] = "modify"
        with self.assertRaisesRegex(ValueError, "unsupported CADPlan operation"):
            _to_document(plan)

    def test_quick_conrod_geometry_variant_is_schema_scoped(self):
        import jsonschema

        plan = _plan("conrod")
        conrod = plan["features"][1]
        conrod["geometry_variant"] = "legacy_quick_rectangular_shank"
        self.assertEqual("valid", validate_plan(plan)["state"])
        validator = jsonschema.Draft202012Validator(schema_document())
        self.assertEqual([], list(validator.iter_errors(plan)))
        conrod["geometry_variant"] = "unexpected"
        self.assertEqual("invalid", validate_plan(plan)["state"])
        self.assertTrue(list(validator.iter_errors(plan)))
        conrod["geometry_variant"] = "legacy_quick_rectangular_shank"
        conrod["kind"] = "box"
        self.assertEqual("invalid", validate_plan(plan)["state"])
        self.assertTrue(list(validator.iter_errors(plan)))

    def test_shell_schema_1_1_requires_explicit_bound_planar_face_selection(self):
        import jsonschema

        plan = _plan("shell")
        self.assertEqual("valid", validate_plan(plan)["state"])
        self.assertEqual([], list(jsonschema.Draft202012Validator(
            schema_document()).iter_errors(plan)))
        self.assertEqual("https://ai-modeler.local/schemas/cadplan-1.1.schema.json",
                         schema_document()["$id"])

        invalid_plans = []
        missing_offset = json.loads(json.dumps(plan))
        missing_offset["features"][1]["parameters"].pop("offset_mode")
        invalid_plans.append(missing_offset)
        missing_target = json.loads(json.dumps(plan))
        missing_target["features"][1]["relations"] = []
        invalid_plans.append(missing_target)
        duplicate_faces = json.loads(json.dumps(plan))
        duplicate_faces["features"][1]["parameters"]["removed_faces"].append(
            duplicate_faces["features"][1]["parameters"]["removed_faces"][0])
        invalid_plans.append(duplicate_faces)
        wrong_inventory = json.loads(json.dumps(plan))
        wrong_inventory["features"][1]["selection_binding"]["inventory_version"] = 1
        invalid_plans.append(wrong_inventory)

        validator = jsonschema.Draft202012Validator(schema_document())
        for invalid in invalid_plans:
            with self.subTest(invalid=invalid["features"][1]):
                self.assertEqual("invalid", validate_plan(invalid)["state"])
                self.assertTrue(list(validator.iter_errors(invalid)))

    def test_legacy_v1_shell_is_readable_but_needs_confirmation_without_guesses(self):
        import jsonschema

        plan = _plan("shell")
        plan["schema_version"] = "1.0"
        feature = plan["features"][1]
        feature["parameters"] = {"thickness": 2}
        feature["sources"] = {"thickness": {"type": "user"}}
        feature["operation"] = "add"
        feature["relations"] = []
        feature.pop("selection_binding")

        result = validate_plan(plan)
        self.assertEqual("needs_confirmation", result["state"], result)
        self.assertEqual("1.0", result["schema_version"])
        self.assertIn("shell_migration", {item["parameter"]
                                           for item in result["confirmations"]})
        shell = plan["features"][1]
        self.assertEqual({"thickness": 2}, shell["parameters"])
        self.assertNotIn("shell_target", str(shell))
        self.assertEqual([], list(jsonschema.Draft202012Validator(
            schema_document()).iter_errors(plan)))

    def test_shell_thickness_uses_plan_length_units_while_selectors_remain_opaque(self):
        plan = _plan("shell")
        plan["units"]["length"] = "cm"
        document, _ = _to_document(plan)
        shell = document.features[1]
        self.assertEqual(20.0, shell.params["thickness"])
        self.assertEqual("inward", shell.params["offset_mode"])
        self.assertEqual(plan["features"][1]["parameters"]["removed_faces"],
                         shell.params["removed_faces"])

    def test_disabled_feature_is_schema_valid_serialized_and_ignored_by_preview(self):
        import jsonschema

        plan = _plan("box", {"length": 20, "width": 10, "height": 8})
        plan["features"][1]["enabled"] = False
        self.assertEqual("valid", validate_plan(plan)["state"])
        self.assertEqual([], list(jsonschema.Draft202012Validator(
            schema_document()).iter_errors(plan)))

        document, _ = _to_document(plan)
        self.assertFalse(document.features[1].enabled)
        preview = preview_plan(plan)
        self.assertEqual("previewed", preview["state"], preview)
        self.assertEqual(["base"], [item["feature_id"] for item in
                                     preview["local_geometry_checks"]["feature_results"]])
        self.assertEqual([100.0, 80.0, 60.0], preview["dimensions"]["size"])

    def test_enabled_must_be_boolean(self):
        plan = _plan("box", {"length": 20, "width": 10, "height": 8})
        plan["features"][0]["enabled"] = "false"
        result = validate_plan(plan)
        self.assertEqual("invalid", result["state"], result)
        self.assertIn("invalid_enabled", {item["code"] for item in result["errors"]})

    def test_explicit_blind_entry_schema_and_legacy_high_side_plan(self):
        parameters = {
            "diameter": 6, "through": False, "depth": 6, "axis": "Y",
            "pos": "point", "point": [0, 25, 0],
            "entry_point": [0, 25, 0], "direction": "negative",
        }
        plan = _plan("hole", parameters)
        base = {"length": 40, "width": 50, "height": 40,
                "center": [0, 0, 0], "base": False}
        plan["features"][0]["parameters"] = base
        plan["features"][0]["sources"] = _source_map(base)
        plan["features"][1]["operation"] = "cut"
        validation = validate_plan(plan)
        self.assertEqual("valid", validation["state"], validation)
        import jsonschema
        self.assertEqual([], list(jsonschema.Draft202012Validator(
            schema_document()).iter_errors(plan)))

        hole_schema = next(item for item in schema_document()["properties"]["features"]
                           ["items"]["oneOf"]
                           if item["properties"]["kind"]["const"] == "hole")
        parameter_props = hole_schema["properties"]["parameters"]["properties"]
        self.assertIn("entry_point", parameter_props)
        self.assertEqual(["negative", "positive"], parameter_props["direction"]["enum"])

        explicit_result = preview_plan(plan)
        self.assertEqual("previewed", explicit_result["state"], explicit_result)
        self.assertEqual("passed", explicit_result["local_geometry_checks"]["geometry_state"])
        explicit_hole = explicit_result["local_geometry_checks"]["hole_instances"][0]
        self.assertEqual("verified_blind", explicit_hole["status"])
        self.assertAlmostEqual(6.0, explicit_hole["measured_depth_mm"], places=5)
        self.assertAlmostEqual(-math.pi * 3.0 ** 2 * 6.0,
                               explicit_result["local_geometry_checks"]["feature_results"][-1]
                               ["volume_delta_mm3"], delta=1e-3)

        legacy_plan = copy.deepcopy(plan)
        legacy_parameters = dict(legacy_plan["features"][1]["parameters"])
        legacy_parameters.pop("entry_point")
        legacy_parameters.pop("direction")
        legacy_plan["features"][1]["parameters"] = legacy_parameters
        legacy_plan["features"][1]["sources"] = _source_map(legacy_parameters)
        legacy_validation = validate_plan(legacy_plan)
        self.assertEqual("valid", legacy_validation["state"], legacy_validation)
        legacy_result = preview_plan(legacy_plan)
        self.assertEqual("previewed", legacy_result["state"], legacy_result)
        legacy_hole = legacy_result["local_geometry_checks"]["hole_instances"][0]
        self.assertEqual("verified_blind", legacy_hole["status"])
        self.assertAlmostEqual(6.0, legacy_hole["measured_depth_mm"], places=5)

    def test_explicit_blind_entry_requires_pair_and_matches_legacy_hole_axis(self):
        base_parameters = {"diameter": 6, "through": False, "depth": 6,
                           "axis": "Y", "pos": "point",
                           "point": [0, -24.5, 0],
                           "entry_point": [1, 10, 0], "direction": "positive"}
        mismatched = validate_plan(_plan("hole", base_parameters))
        self.assertEqual("invalid", mismatched["state"], mismatched)
        self.assertIn("hole_entry_point_mismatch",
                      {row["code"] for row in mismatched["errors"]})

        unpaired_parameters = dict(base_parameters)
        unpaired_parameters.pop("direction")
        unpaired = validate_plan(_plan("hole", unpaired_parameters))
        self.assertEqual("invalid", unpaired["state"], unpaired)
        self.assertIn("incomplete_hole_entry",
                      {row["code"] for row in unpaired["errors"]})
        import jsonschema
        schema_errors = list(jsonschema.Draft202012Validator(schema_document()).iter_errors(
            _plan("hole", unpaired_parameters)))
        self.assertTrue(schema_errors)

    def test_vbelt_pulley_rejects_sourced_width_that_cannot_fit_standard_grooves(self):
        result = validate_plan(_plan("vbelt_pulley", {
            "pitch_d": 80, "grooves": 3, "height": 20, "groove_type": "A"}))
        self.assertEqual("invalid", result["state"], result)
        self.assertTrue(any(row.get("code") == "vbelt_width_below_profile_minimum"
                            and row.get("path") == "$.features[1].parameters.height"
                            for row in result["errors"]), result)

    def test_preview_renders_last_valid_partial_without_marking_it_accepted(self):
        plan = _plan("hole", {"diameter": 8, "through": True,
                               "pos": "point", "point": [0, 0, 0], "axis": "X"})
        duplicate = {"id": "right-hole", "kind": "hole", "operation": "cut",
                     "parameters": {"diameter": 8, "through": True,
                                    "pos": "point", "point": [5, 0, 0], "axis": "X"},
                     "sources": _source_map({"diameter": 8, "point": [5, 0, 0]})}
        plan["features"].append(duplicate)
        with tempfile.TemporaryDirectory() as directory:
            result = preview_plan(plan, str(Path(directory) / "partial.png"))
            self.assertTrue(Path(result["partial_image"]).is_file())
        self.assertEqual("preview_failed", result["state"], result)
        self.assertTrue(result["partial_preview"], result)
        self.assertEqual("candidate_partial_not_accepted", result["partial_state"])
        self.assertEqual("duplicate_through_axis", result["failure_code"])
        self.assertEqual(["right-hole", "target"], result["related_feature_ids"])
        self.assertEqual("needs_review", result["partial_geometry_checks"]["state"])
        self.assertFalse(result["partial_geometry_checks"]["accepted"])
        self.assertEqual(2, result["last_valid_dimensions"]["feature_count"])
        self.assertEqual(["base", "target"],
                         result["last_valid_dimensions"]["applied_feature_ids"])
        self.assertEqual("right-hole",
                         result["last_valid_dimensions"]["failed_feature_id"])

    def test_schema_and_validation_cover_all_public_document_kinds(self):
        schema = schema_document()
        variants = schema["properties"]["features"]["items"]["oneOf"]
        schema_kinds = {item["properties"]["kind"]["const"] for item in variants}
        self.assertEqual(set(KIND_LABELS), set(FEATURE_SPECS))
        self.assertEqual(set(KIND_LABELS), schema_kinds)
        self.assertEqual(set(KIND_LABELS), set(schema["x-feature-support-matrix"]))
        for kind in KIND_LABELS:
            with self.subTest(kind=kind):
                result = validate_plan(_plan(kind))
                self.assertEqual("valid", result["state"], result)
                self.assertEqual(kind, result["support"][-1]["kind"])
                self.assertIn(result["support"][-1]["status"],
                              {"exact", "approximate", "unsupported"})

    def test_json_schema_accepts_each_valid_feature_plan(self):
        import jsonschema

        validator = jsonschema.Draft202012Validator(schema_document())
        for kind in KIND_LABELS:
            with self.subTest(kind=kind):
                errors = list(validator.iter_errors(_plan(kind)))
                self.assertEqual([], errors, [error.message for error in errors])

    def test_length_units_convert_to_document_millimetres(self):
        plan = _plan("box", {"length": 1, "width": 2, "height": 3})
        plan["features"] = plan["features"][1:]
        plan["units"]["length"] = "in"
        result = preview_plan(plan)
        self.assertEqual("previewed", result["state"], result)
        for actual, expected in zip(result["dimensions"]["size"], [25.4, 50.8, 76.2]):
            self.assertAlmostEqual(expected, actual)

    def test_box_center_is_geometric_center_in_preview(self):
        plan = _plan("box", {"length": 20, "width": 10, "height": 8,
                             "center": [5, 7, 20]})
        plan["features"] = plan["features"][1:]
        plan["features"][0]["sources"]["center"] = {"type": "user"}

        result = preview_plan(plan)

        self.assertEqual("previewed", result["state"], result)
        for actual, expected in zip(result["dimensions"]["bbox"],
                                    [-5, 2, 16, 15, 12, 24]):
            self.assertAlmostEqual(expected, actual)

    def test_centered_box_places_three_rows_of_front_through_holes_inside_solid(self):
        plan = _plan("box", {"length": 100, "width": 40, "height": 84,
                             "center": [0, 0, 42]})
        plan["features"] = plan["features"][1:]
        base = plan["features"][0]
        base["id"] = "plate"
        base["sources"]["center"] = {"type": "user"}
        for index, z in enumerate((18, 42, 66), start=1):
            plan["features"].append({
                "id": "hole-%d" % index,
                "kind": "hole",
                "operation": "cut",
                "parameters": {"diameter": 8, "through": True, "pos": "point",
                               "axis": "Y", "point": [0, 0, z]},
                "sources": {"diameter": {"type": "user"},
                            "point": {"type": "user"}},
            })

        result = preview_plan(plan)

        self.assertEqual("previewed", result["state"], result)
        self.assertEqual([100.0, 40.0, 84.0], result["dimensions"]["size"])
        self.assertFalse(any("不相交" in message for message in result["build_log"]))

    def test_preview_writes_inspectable_front_right_top_image(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "model.three-view.png"
            result = preview_plan(_plan("box"), image_path=str(image))
            self.assertEqual("previewed", result["state"], result)
            self.assertEqual(str(image), result["preview_image"])
            self.assertEqual(["front", "right", "top"], result["preview_image_views"])
            self.assertEqual("OCCT BREP tessellated for display; not a CATIA screenshot",
                             result["preview_image_source"])
            layout = result["preview_image_layout"]
            self.assertTrue(layout["uniform_scale_across_views"])
            self.assertEqual({"front": (100.0, 60.0), "right": (80.0, 60.0),
                              "top": (100.0, 80.0)},
                             {view: (data["horizontal"], data["vertical"])
                              for view, data in layout["view_extents"].items()})
            png = image.read_bytes()
            self.assertEqual(b"\x89PNG\r\n\x1a\n", png[:8])
            self.assertEqual((990, 260),
                             (int.from_bytes(png[16:20], "big"),
                              int.from_bytes(png[20:24], "big")))
            x0, y0, x1, y1 = _png_foreground_bbox(image)
            self.assertAlmostEqual(100 / 60, (x1 - x0) / (y1 - y0), delta=0.04)
            self.assertAlmostEqual(165, (x0 + x1) / 2, delta=2)
            self.assertAlmostEqual(130, (y0 + y1) / 2, delta=2)

    def test_cli_validate_returns_json(self):
        plan = _plan("box")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            # The child writes UTF-8 (its support matrix carries Chinese notes).
            # Without an explicit encoding, Windows decodes its stdout as GBK,
            # the reader thread dies, and stdout comes back as None.
            process = subprocess.run(
                [sys.executable, "-m", "ai_modeler", "--cadplan", "validate", str(path)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
        self.assertEqual(0, process.returncode, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual("valid", result["state"])
        self.assertEqual(2, len(result["support"]))

    def test_missing_parameters_and_missing_provenance_request_confirmation(self):
        plan = _plan("box")
        del plan["features"][1]["parameters"]["width"]
        del plan["features"][1]["sources"]["width"]
        result = validate_plan(plan)
        self.assertEqual("needs_confirmation", result["state"])
        self.assertIn("width", {item["parameter"] for item in result["confirmations"]})

        plan = _plan("box")
        del plan["features"][1]["sources"]["height"]
        result = validate_plan(plan)
        self.assertEqual("needs_confirmation", result["state"])
        self.assertIn("height", {item["parameter"] for item in result["confirmations"]})

    def test_image_estimate_requires_evidence_and_zero_dimension_is_invalid(self):
        plan = _plan("box")
        plan["features"][1]["sources"]["height"] = {"type": "image_estimate"}
        result = validate_plan(plan)
        self.assertEqual("needs_confirmation", result["state"])
        self.assertIn("height", {item["parameter"] for item in result["confirmations"]})

        plan = _plan("box")
        plan["features"][1]["parameters"]["length"] = 0
        result = validate_plan(plan)
        self.assertEqual("invalid", result["state"])
        self.assertIn("non_positive_dimension", {item["code"] for item in result["errors"]})

    def test_invalid_pattern_relation_feedback_shows_exact_json_shape(self):
        self.assertIn('"type":"pattern_source"',
                      FEATURE_SPECS["pattern"]["reason"])
        self.assertIn('"feature_id"', FEATURE_SPECS["pattern"]["reason"])

        plan = _plan("pattern")
        plan["features"][1]["relations"] = [
            {"relation_type": "pattern_source", "target": "base"}]

        result = validate_plan(plan)

        self.assertEqual("invalid", result["state"])
        messages = " ".join(item["message"] for item in result["errors"])
        self.assertIn("type", messages)
        self.assertIn("feature_id", messages)
        self.assertIn("pattern_source", messages)

        plan["features"][1]["relations"] = {"pattern_source": "base"}
        result = validate_plan(plan)
        messages = " ".join(item["message"] for item in result["errors"])
        self.assertIn("relations 必须是数组", messages)
        self.assertIn("feature_id", messages)

    def test_pattern_operation_must_match_source_and_cut_holes_as_a_cut(self):
        base_parameters = {"length": 100, "width": 80, "height": 60,
                           "center": [0, 0, 30]}
        hole_parameters = {"diameter": 8, "through": True, "pos": "point",
                           "point": [0, 0, 0], "axis": "Z"}
        # A through-hole array must move across the part face. Moving a hole
        # along its own axis only overlaps the source bore and adds no geometry.
        pattern_parameters = {"mode": "linear", "count": 3,
                              "step": [15, 0, 0]}
        plan = _plan("box")
        plan["features"] = [
            {"id": "base", "kind": "box", "operation": "add",
             "parameters": base_parameters,
             "sources": _source_map(base_parameters)},
            {"id": "hole", "kind": "hole", "operation": "cut",
             "parameters": hole_parameters,
             "sources": _source_map(hole_parameters)},
            {"id": "hole-pattern", "kind": "pattern", "operation": "add",
             "parameters": pattern_parameters,
             "sources": _source_map(pattern_parameters),
             "relations": [{"type": "pattern_source", "feature_id": "hole"}]},
        ]

        result = validate_plan(plan)
        self.assertEqual("invalid", result["state"])
        operation_error = next(row for row in result["errors"]
                               if row["code"] == "pattern_operation_mismatch")
        self.assertIn("圆柱刀具加成实体", operation_error["message"])

        plan["features"][2]["operation"] = "cut"
        result = validate_plan(plan)
        self.assertEqual("valid", result["state"], result)
        preview = preview_plan(plan)
        self.assertEqual("previewed", preview["state"], preview)
        self.assertEqual([100.0, 80.0, 60.0], preview["dimensions"]["size"])
        self.assertLess(preview["dimensions"]["volume_mm3"], 100 * 80 * 60)

    def test_existing_model_provenance_requires_an_explanatory_note(self):
        import jsonschema

        plan = _plan("box")
        plan["features"][1]["sources"]["length"] = {
            "type": "existing_model", "note": "继承自当前本地特征树，原始来源未知。"}
        result = validate_plan(plan)
        self.assertEqual("valid", result["state"], result)
        self.assertEqual([], list(jsonschema.Draft202012Validator(
            schema_document()).iter_errors(plan)))

        del plan["features"][1]["sources"]["length"]["note"]
        result = validate_plan(plan)
        self.assertEqual("needs_confirmation", result["state"])
        self.assertIn("length", {item["parameter"] for item in result["confirmations"]})

    def test_strict_mode_blocks_before_catia(self):
        plan = _plan("sphere")
        with mock.patch("ai_modeler.catia_backend.catia.push_to_catia") as push, \
             mock.patch("ai_modeler.sdk.cadplan._preview_internal") as preview:
            result = build_plan(plan, strict=True)
        push.assert_not_called()
        preview.assert_not_called()
        self.assertEqual("strict_blocked", result["state"])
        self.assertFalse(result["catia_executed"])

    def test_build_plan_blocks_native_self_intersection_before_export(self):
        plan = _plan("box")
        preview = {"state": "previewed", "dimensions": {"size": [1, 1, 1]},
                   "validation": {"support": []}, "warnings": [],
                   "local_geometry_checks": {
                       "state": "failed", "geometry_state": "failed",
                       "self_intersection": {"status": "self_intersection"}}}
        with mock.patch("ai_modeler.sdk.cadplan._preview_internal",
                        return_value=(preview, None, {}, {}, [])):
            result = build_plan(plan)
        self.assertEqual("geometry_failed", result["state"], result)
        self.assertFalse(result["catia_invoked"])
        self.assertFalse(result["catia_executed"])
        self.assertFalse(result["catia_saved"])
        self.assertEqual({}, result["output_paths"])
        self.assertEqual("self_intersection",
                         result["local_geometry_checks"]["self_intersection"]["status"])

    def test_build_plan_saves_unverified_cylindrical_cut_as_candidate_only(self):
        plan = _plan("cylinder", {"radius": 3, "height": 10,
                                   "center": [0, 0, 30], "axis": "Z",
                                   "base": False})
        plan["features"][-1]["operation"] = "cut"
        with tempfile.TemporaryDirectory() as temp:
            catpart = Path(temp) / "candidate.CATPart"
            with mock.patch("ai_modeler.catia_backend.catia.push_to_catia") as push:
                result = build_plan(plan, catpart_path=str(catpart))
            push.assert_not_called()
            self.assertEqual("candidate_unverified", result["state"], result)
            self.assertTrue(result["candidate_saved"])
            self.assertEqual("not_accepted", result["acceptance"])
            self.assertEqual("needs_review", result["geometry_state"])
            self.assertFalse(result["catia_invoked"])
            self.assertFalse(result["catia_saved"])
            self.assertNotIn("catpart", result["output_paths"])
            self.assertFalse(catpart.exists())
            self.assertTrue(all(Path(path).is_file()
                                for path in result["output_paths"].values()))
            report = json.loads(Path(result["output_paths"]["report"]).read_text(encoding="utf-8"))
            self.assertEqual("incomplete_or_unverified",
                             report["local_geometry_checks"]["hole_measurement_state"])
            self.assertEqual("unverified", report["local_geometry_checks"]
                             ["unverified_cut_intents"][0]["status"])

    def test_auto_mode_exports_files_and_reports_skipped_features(self):
        plan = _plan("shell")
        with tempfile.TemporaryDirectory() as temp:
            catpart = Path(temp) / "auto.CATPart"

            def fake_push(doc, save_catpart=None, **kwargs):
                Path(save_catpart).write_bytes(b"CATPart test stub")
                self.assertIn("shell", kwargs["post_build_notice"])
                return True, "saved", "push.vbs"

            with mock.patch("ai_modeler.catia_backend.catia.push_to_catia", side_effect=fake_push):
                result = build_plan(plan, catpart_path=str(catpart))

            self.assertEqual("partial", result["state"])
            self.assertTrue(result["catia_executed"])
            self.assertTrue(result["catia_saved"])
            for path in result["output_paths"].values():
                self.assertTrue(Path(path).is_file(), path)
            report = json.loads(Path(result["output_paths"]["report"]).read_text(encoding="utf-8"))
            self.assertIn("CATIA_UNSUPPORTED", {item["code"] for item in report["warnings"]})
            self.assertEqual("unsupported", report["support"][-1]["status"])

    def test_hole_coordinates_follow_global_axes_and_catia_sketch_axes(self):
        expected = {"X": (12, 34), "Y": (34, 12), "Z": (12, 34)}
        for axis, local_xy in expected.items():
            with self.subTest(axis=axis):
                doc = Document("hole-axis")
                doc.add(Feature("box", {"length": 100, "width": 80, "height": 60}))
                # CADPlan point [X,Y,Z] is projected to Document's axis-local x/y.
                point = {"X": [0, 12, 34], "Y": [12, 0, 34], "Z": [12, 34, 0]}[axis]
                a, b = ((point[1], point[2]) if axis == "X" else
                        ((point[0], point[2]) if axis == "Y" else (point[0], point[1])))
                doc.add(Feature("hole", {"diameter": 8, "through": True, "axis": axis,
                                         "pos": "point", "x": a, "y": b}, op="cut"))
                features, _ = build_catia_features(doc)
                hole = next(item for item in features if item["kind"] == "circle_cut")
                self.assertEqual(axis, hole["plane"])
                self.assertAlmostEqual(local_xy[0], hole["cx"])
                self.assertAlmostEqual(local_xy[1], hole["cy"])

    def test_post_build_notice_is_escaped_in_catiascript_host_script(self):
        doc = Document('name"\nWScript.Echo "INJECTED"')
        doc.source_prompt = 'description\r\nWScript.Echo "INJECTED"'
        doc.add(Feature("box", {"length": 10, "width": 10, "height": 10}))
        text = render_push_vbs(doc, [{"kind": "rect_pad", "cx": 0, "cy": 0, "z": 0,
                                      "l": 10, "w": 10, "h": 10,
                                      "name": 'feature"\r\nWScript.Echo "INJECTED"'}],
                               [], post_build_notice='approximation "A"\nskipped B')
        self.assertIn("MsgBox POST_BUILD_NOTICE", text)
        self.assertIn('approximation ""A""', text)
        self.assertIn("skipped B", text)
        self.assertNotIn('\nWScript.Echo "INJECTED"', text)


if __name__ == "__main__":
    unittest.main()
