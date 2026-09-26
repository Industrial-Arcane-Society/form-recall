"""Focused acceptance checks for the incremental AgentLoop runtime."""
from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_modeler.agent import agentloop, agent_runtime, image_analysis, plan_state
from ai_modeler.sdk import cadplan
from ai_modeler.ui import softrender


_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXcAAAAASUVORK5CYII=")


def _feature(source="user"):
    parameters = {"length": 100, "width": 50, "height": 20, "center": [0, 0, 0]}
    return {"id": "base", "kind": "box", "operation": "add",
            "parameters": parameters,
            "sources": {key: {"type": source, "evidence": "visible ratio"}
                        for key in parameters}}


def _call(name, arguments, number):
    return {"id": "call-%d" % number, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def _reading(**overrides):
    """A concise shape-independent inventory of visible feature groups."""
    reading = {
        "feature_groups": [
            {"id": "left-face-holes", "kind": "hole", "quantity": 3,
             "region": "left face",
             "arrangement": "three vertical positions",
             "evidence": "three distinct openings on the left face"},
            {"id": "center-face-holes", "kind": "hole", "quantity": 3,
             "region": "recessed center face",
             "arrangement": "three vertical positions",
             "evidence": "three distinct openings on the recessed center face"},
            {"id": "right-face-holes", "kind": "hole", "quantity": 3,
             "region": "right face",
             "arrangement": "three vertical positions",
             "evidence": "three distinct openings on the right face"},
            {"id": "main-body", "kind": "body", "quantity": 1,
             "region": "whole part",
             "arrangement": "single connected body",
             "evidence": "one connected outer body"},
        ],
    }
    reading.update(overrides)
    return reading


def _body_reading():
    return _reading(feature_groups=[
        {"id": "main-body", "kind": "body", "quantity": 1,
         "region": "whole part", "arrangement": "single body",
         "evidence": "one continuous outer body"}])


def _reading_with_regions(image="reference.png"):
    reading = _reading()
    reading["visible_regions"] = [
        {"image": image, "id": "left", "region": "left face",
         "surface_state": "solid_face", "image_bbox_norm": [0.1, 0.1, 0.35, 0.9],
         "evidence": "bounded front face", "features": [{
             "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
             "evidence": "three distinct openings"}]},
        {"image": image, "id": "center", "region": "recessed center face",
         "surface_state": "recessed_solid_face", "image_bbox_norm": [0.33, 0.2, 0.5, 0.9],
         "evidence": "bounded rear plane with visible holes", "features": [{
             "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
             "evidence": "three openings on the rear plane"}]},
        {"image": image, "id": "right", "region": "right face",
         "surface_state": "solid_face", "image_bbox_norm": [0.45, 0.1, 0.9, 0.95],
         "evidence": "bounded front face and continuous outer side", "features": [{
             "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
             "evidence": "three distinct openings"}]},
    ]
    return reading


def _blind_inventory(image="reference.png", source_view="iso", regions=None,
                     hole_layout_coverage="unknown"):
    return {"images": [{
        "image": image, "source_view": source_view,
        "coverage": "whole_object",
        "hole_layout_coverage": hole_layout_coverage,
        "visible_regions": regions or [{
            "id": "whole-body", "region": "whole part",
            "surface_state": "solid_face", "evidence": "continuous body",
            "features": [{"kind": "other", "quantity": 1,
                          "arrangement": "single", "evidence": "one body"}],
        }],
        "uncertainties": [],
    }], "cross_view_notes": []}


class IncrementalAgentTests(unittest.TestCase):
    def test_visual_review_context_omits_provenance_and_summarizes_mesh_checks(self):
        plan = {
            "schema_version": "1.0", "units": {"length": "mm"},
            "coordinate_system": {"axes": ["X", "Y", "Z"]},
            "features": [{
                "id": "base", "kind": "box", "operation": "add",
                "parameters": {"length": 100, "width": 36, "height": 105},
                "sources": {"length": {"type": "image_estimate",
                                         "evidence": "long provenance text"}},
            }],
        }
        summary = agentloop._review_plan_summary(plan)
        self.assertEqual("1.0", summary["schema_version"])
        self.assertEqual(plan["features"][0]["parameters"],
                         summary["features"][0]["parameters"])
        self.assertNotIn("sources", summary["features"][0])

        geometry = agentloop._review_geometry_summary({
            "state": "passed", "bbox_size_mm": [100, 36, 105],
            "bbox_ratios_from_mesh": {"front_width_height": 0.952},
            "feature_count_from_plan": 10,
            "hole_features_from_plan": [{"feature_id": "h1"}],
            "through_hole_mesh_checks": [
                {"status": "verified_open"}, {"status": "blocked"}],
        })
        self.assertEqual(1, geometry["verified_open_hole_count"])
        self.assertEqual(2, geometry["through_hole_check_count"])
        self.assertEqual([100, 36, 105], geometry["bbox_size_mm"])

    def test_json_recovery_can_cap_retry_without_changing_other_phases(self):
        token_budgets = []
        state = {"model_calls": []}

        def malformed_then_valid(_transcript, _cfg, _phase, call_state,
                                 _cancel_event, max_tokens=None):
            token_budgets.append(max_tokens)
            call_state["model_calls"].append({
                "finish_reason": "length", "response_chars": 4000,
            })
            return "not complete json" if len(token_budgets) == 1 else '{"ok":true}'

        with mock.patch.object(agentloop, "_run_tools", side_effect=malformed_then_valid):
            result = agentloop._run_json_object(
                [], {}, "multiview_check", "逐视图复核", state,
                max_tokens=4096, retry_max_tokens=4096)
        self.assertEqual({"ok": True}, result)
        self.assertEqual([4096, 4096], token_budgets)

    def test_multiview_review_is_submitted_as_a_required_bounded_tool(self):
        review = {
            "reference_images": [], "checks": [], "issues": [],
            "resolved_findings": [], "questions": [], "confidence": 0.5,
        }
        reply = {
            "content": None,
            "tool_calls": [{
                "id": "review-call", "type": "function",
                "function": {"name": "submit_multiview_review",
                             "arguments": json.dumps(review)},
            }],
            "_finish_reason": "tool_calls",
        }
        state = {
            "deadline": time.monotonic() + 30,
            "tool_calls": 0, "tool_trace": [], "model_calls": [],
            "model_cfg": {"model": "test-model"},
            "active_review_view": "iso",
            "active_review_areas": ["overall_shape", "depth_relationships"],
            "active_review_group_ids": ["blind-01-01-01"],
        }
        with mock.patch.object(agentloop.llm, "chat_completion", return_value=reply) as call:
            raw = agentloop._run_tools([], {}, "multiview_check", state)
        self.assertEqual(review, json.loads(raw))
        self.assertEqual("required", call.call_args.kwargs["tool_choice"])
        self.assertEqual("submit_multiview_review",
                         call.call_args.kwargs["tools"][0]["function"]["name"])
        properties = call.call_args.kwargs["tools"][0]["function"]["parameters"]["properties"]
        check_properties = properties["checks"]["items"]["properties"]
        self.assertEqual(["iso"], check_properties["view"]["enum"])
        self.assertEqual(["overall_shape", "depth_relationships", "feature_group"],
                         check_properties["area"]["enum"])
        self.assertEqual(["", "blind-01-01-01"],
                         check_properties["feature_group_id"]["enum"])
        self.assertEqual("submit_multiview_review", state["tool_trace"][0]["tool"])

    def test_reference_inventory_uses_a_required_structured_submission(self):
        inventory = {"images": [], "cross_view_notes": []}
        reply = {
            "content": None,
            "tool_calls": [{
                "id": "inventory-call", "type": "function",
                "function": {"name": "submit_reference_inventory",
                             "arguments": json.dumps(inventory)},
            }],
            "_finish_reason": "tool_calls",
        }
        state = {
            "deadline": time.monotonic() + 30,
            "tool_calls": 0, "tool_trace": [], "model_calls": [],
            "model_cfg": {"model": "test-model"},
        }
        with mock.patch.object(agentloop.llm, "chat_completion", return_value=reply) as call:
            raw = agentloop._run_tools([], {}, "blind_reference_inventory", state)
        self.assertEqual(inventory, json.loads(raw))
        self.assertEqual("required", call.call_args.kwargs["tool_choice"])
        self.assertEqual("submit_reference_inventory",
                         call.call_args.kwargs["tools"][0]["function"]["name"])
        feature_schema = agentloop.REFERENCE_INVENTORY_TOOL["function"]["parameters"][
            "properties"]["images"]["items"]["properties"]["visible_regions"][
            "items"]["properties"]["features"]["items"]
        self.assertIn("relative_size_order", feature_schema["required"])
        self.assertIn("size_order_confidence", feature_schema["required"])

    def test_blind_inventory_requires_size_evidence_for_repeated_holes(self):
        feature = {"kind": "hole", "quantity": 3,
                   "arrangement": "top middle bottom",
                   "evidence": "three circular openings",
                   "relative_size_order": ["large", "small", "large"],
                   "size_order_axis": "vertical_top_to_bottom",
                   "size_order_confidence": "high"}
        response = {"images": [{
            "image": "ref.png", "source_view": "iso", "coverage": "whole_object",
            "hole_layout_coverage": "partial",
            "visible_regions": [{"id": "face", "region": "front face",
                                 "surface_state": "solid_face",
                                 "evidence": "bounded front face",
                                 "features": [feature]}],
            "uncertainties": [],
        }], "cross_view_notes": []}
        state = {"deadline": time.monotonic() + 30}
        with mock.patch.object(agentloop, "_run_json_object", return_value=response):
            normalized, gaps = agentloop._independent_reference_inventory(
                [], ["ref.png"], "复刻", {}, state, None)
        self.assertEqual([], gaps)
        normalized_feature = normalized["images"][0]["visible_regions"][0]["features"][0]
        self.assertEqual(["large", "small", "large"],
                         normalized_feature["relative_size_order"])

        uncertain = json.loads(json.dumps(response))
        del uncertain["images"][0]["visible_regions"][0]["features"][0][
            "relative_size_order"]
        state = {"deadline": time.monotonic() + 30}
        with mock.patch.object(agentloop, "_run_json_object", return_value=uncertain):
            _normalized, gaps = agentloop._independent_reference_inventory(
                [], ["ref.png"], "复刻", {}, state, None)
        self.assertTrue(any("相对孔径顺序" in row for row in gaps), gaps)

    def test_front_review_camera_and_hole_axis_share_positive_y_face(self):
        _right, _up, front_fwd = softrender.view_matrix(
            *softrender.VIEWS["front"])
        _right, _up, back_fwd = softrender.view_matrix(
            *softrender.VIEWS["back"])
        self.assertGreater(front_fwd[1], 0)
        self.assertLess(back_fwd[1], 0)
        system = agent_runtime._agent_system(has_reference_images=True)
        self.assertIn("front 面孔的 axis 应为 Y", system)
        self.assertIn("right 面孔的 axis 应为 X", system)

    def test_front_projection_cannot_support_depth_findings(self):
        self.assertTrue(agentloop._front_depth_claim({
            "item": "中间区域退台深度与参考图不符",
        }))
        self.assertTrue(agentloop._front_depth_claim({
            "evidence": "The model back wall is set back from the front face.",
        }))
        self.assertFalse(agentloop._front_depth_claim({
            "item": "9 holes in the same size order",
        }))

    def test_iso_review_resolves_front_unobservable_recess_group(self):
        plan = plan_state.create("Part", _feature("user"))
        image_names = ["front.png", "iso.png"]
        inventory = {
            "images": [], "cross_view_notes": [],
        }
        for image_index, view in enumerate(("front", "iso"), 1):
            regions = []
            for region_index, region_name in enumerate(
                    ("左侧壁前表面", "右侧壁前表面", "槽内后壁表面"), 1):
                regions.append({
                    "id": f"region-{region_index}", "region": region_name,
                    "surface_state": "recessed_solid_face" if region_index == 3 else "solid_face",
                    "evidence": "bounded face",
                    "features": [{"kind": "hole", "quantity": 3,
                                  "arrangement": "vertical",
                                  "evidence": "three holes"}],
                })
            inventory["images"].append({
                "image": image_names[image_index - 1], "source_view": view,
                "coverage": "whole_object", "hole_layout_coverage": "partial",
                "visible_regions": regions, "uncertainties": [],
            })

        def group_check(group_id, view, name):
            return {
                "id": f"{view}.{name}", "view": view, "area": "feature_group",
                "feature_group_id": group_id, "category": "geometry",
                "status": "match", "item": name,
                "reference_evidence": "three visible holes",
                "model_evidence": "three visible holes",
                "evidence": "count and arrangement match",
                "unknown_reason": "", "feature_ids": [], "affected_parameters": [],
            }

        front_report = {
            "reference_images": [{"image": "front.png", "source_view": "front",
                                  "comparable": True, "visible_evidence": "front view"}],
            "checks": [
                {"id": "front.outline", "view": "front", "area": "outline",
                 "category": "geometry", "status": "match", "item": "front outline",
                 "reference_evidence": "same 2D silhouette", "model_evidence": "same 2D silhouette"},
                {"id": "front.features", "view": "front", "area": "features",
                 "category": "geometry", "status": "match", "item": "hole layout",
                 "reference_evidence": "three columns", "model_evidence": "three columns"},
                group_check("blind-01-01-01", "front", "left holes"),
                group_check("blind-01-02-01", "front", "right holes"),
                {"id": "front.bad-depth", "view": "front", "area": "features",
                 "category": "geometry", "status": "mismatch", "item": "recess depth",
                 "reference_evidence": "unknown from front", "model_evidence": "set back",
                 "evidence": "front projection claims a depth mismatch",
                 "feature_ids": ["recess"], "affected_parameters": []},
            ],
            "issues": [{"issue_id": "front.bad-depth", "view": "front",
                        "category": "geometry", "severity": "high",
                        "item": "recess depth mismatch", "evidence": "unobservable from front",
                        "feature_ids": ["recess"], "affected_parameters": []}],
            "resolved_findings": [], "questions": [], "confidence": 0.9,
        }
        iso_report = {
            "reference_images": [{"image": "iso.png", "source_view": "iso",
                                  "comparable": True, "visible_evidence": "iso view"}],
            "checks": [
                {"id": "iso.overall", "view": "iso", "area": "overall_shape",
                 "category": "geometry", "status": "match", "item": "outer shell",
                 "reference_evidence": "continuous body", "model_evidence": "continuous body"},
                {"id": "iso.layout", "view": "iso", "area": "feature_layout",
                 "category": "geometry", "status": "match", "item": "hole rows",
                 "reference_evidence": "three rows", "model_evidence": "three rows"},
                {"id": "iso.depth", "view": "iso", "area": "depth_relationships",
                 "category": "geometry", "status": "match", "item": "recess",
                 "reference_evidence": "center wall set back", "model_evidence": "center wall set back"},
                group_check("blind-02-01-01", "iso", "left holes"),
                group_check("blind-02-02-01", "iso", "right holes"),
                group_check("blind-02-03-01", "iso", "recess wall holes"),
            ],
            "issues": [], "resolved_findings": [], "questions": [], "confidence": 0.9,
        }
        with tempfile.TemporaryDirectory() as directory:
            source_images = {}
            for name, view in zip(image_names, ("front", "iso")):
                path = Path(directory) / name
                cadplan.preview_plan(plan, str(path), view_set=view)
                source_images[name] = str(path)
            preview = cadplan.preview_plan(
                plan, str(Path(directory) / "preview.png"), view_set="iso")
            state = {
                "deadline": time.monotonic() + 30, "session_dir": Path(directory),
                "preview_calls": 0, "active_reference_images": image_names,
                "reference_image_paths": source_images,
                "independent_reference_inventory": inventory,
                "temporary_review_images": [],
            }
            with mock.patch.object(agentloop, "_run_json_object",
                                   side_effect=[front_report, iso_report]):
                result = agentloop._split_view_review(
                    plan, preview, [], {}, state, None, "multiview_check", 1,
                    "复刻", views=["front", "iso"])
        self.assertEqual("accept", result["decision"])
        self.assertFalse(result["issues"])
        self.assertTrue(any(item["kind"] == "unobservable_depth_issue"
                            for item in result["view_incompatible_findings"]))
        center_group = next(item for item in result["feature_group_checks"]
                            if item["feature_group_id"] == "blind-01-03-01")
        self.assertEqual("passed", center_group["status"])
        self.assertEqual(["blind-02-03-01"], center_group["cross_view_verified_by"])

    def test_review_view_selection_follows_source_camera(self):
        selected, skipped = agentloop._review_views_for_inventory(
            ["front", "iso"], _blind_inventory(source_view="iso"))
        self.assertEqual(["iso"], selected)
        self.assertEqual(["front"], skipped)

        selected, skipped = agentloop._review_views_for_inventory(
            ["front"], _blind_inventory(source_view="iso"))
        self.assertEqual(["iso"], selected)
        self.assertEqual(["front"], skipped)

    def test_visual_review_rejects_cross_view_findings(self):
        plan = plan_state.create("Part", _feature("image_estimate"))
        inventory = _blind_inventory("reference.png", "iso")
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            cadplan.preview_plan(plan, str(reference), view_set="iso")
            preview = cadplan.preview_plan(
                plan, str(Path(directory) / "candidate.png"), view_set="iso")
            review = {
                "reference_images": [{
                    "image": "reference.png", "source_view": "front",
                    "comparable": True, "visible_evidence": "模型错误声称它是正视图",
                }],
                "checks": [{"id": "iso.overall_shape", "area": "overall_shape",
                            "status": "mismatch", "item": "错误的跨视角判断",
                            "reference_evidence": "unsupported",
                            "model_evidence": "unsupported"}],
                "issues": [{"issue_id": "iso.false-finding", "item": "false finding"}],
                "resolved_findings": [], "questions": [],
            }
            state = {
                "deadline": time.monotonic() + 30,
                "session_dir": Path(directory), "preview_calls": 0,
                "active_reference_images": ["reference.png"],
                "reference_image_paths": {"reference.png": str(reference)},
            }
            calls = []

            def multimodal(*args, **_kwargs):
                calls.append(args)
                return inventory if args[3] == "参考图独立盲读" else review

            with mock.patch.object(agentloop, "_run_json_object", side_effect=multimodal):
                result = agentloop._split_view_review(
                    plan, preview, [], {}, state, None, "multiview_check", 1,
                    "复刻", views=["front", "iso"])

        self.assertEqual(["iso"], result["reviewed_views"])
        self.assertEqual(["front"], result["skipped_views"])
        self.assertEqual("inspect", result["decision"])
        self.assertFalse(result["single_view_reports"][0]["reference_images"][0]["comparable"])
        self.assertEqual([], result["issues"])
        self.assertTrue(any("视角判断" in gap for gap in result["review_gaps"]))
        self.assertEqual(2, len(calls))
        review_payload = calls[1][0][-1]["content"][0]["text"]
        self.assertIn('"view_to_inspect":"iso"', review_payload)
        self.assertNotIn('"view_to_inspect":"front"', review_payload)

    def test_main_loop_drops_source_pixels_after_read_but_keeps_review_copy(self):
        image_part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
        transcript_message = {"role": "user", "content": [
            {"type": "text", "text": "复刻这个零件"}, image_part]}
        main_messages = [dict(transcript_message)]
        prefix_messages = list(main_messages)
        review_messages = [dict(transcript_message)]
        state = {"reference_reading": _reading(),
                 "active_reference_images": ["reference.png"]}

        removed = agent_runtime._compact_main_reference_images(main_messages, state)

        self.assertEqual(1, removed)
        self.assertTrue(state["reference_images_compacted"])
        self.assertEqual(1, state["reference_image_handling"][
            "image_parts_removed_from_main_context"])
        self.assertEqual(["reference.png"], state["reference_image_handling"][
            "preserved_for_multimodal_review"])
        self.assertEqual([{"type": "text", "text": "复刻这个零件"}],
                         main_messages[0]["content"])
        self.assertNotIn("image_url", json.dumps(prefix_messages, ensure_ascii=False))
        self.assertIn("image_url", json.dumps(review_messages, ensure_ascii=False))
        self.assertEqual(0, agent_runtime._compact_main_reference_images(main_messages, state))

    def test_siliconflow_none_really_disables_default_reasoning(self):
        self.assertEqual({"enable_thinking": False},
                         agent_runtime.llm._siliconflow_thinking_params(
                             {"reasoning_effort": "none"}))
        self.assertEqual({"enable_thinking": False},
                         agent_runtime.llm._siliconflow_thinking_params(
                             {"reasoning_effort": "disabled"}))
        self.assertEqual({"enable_thinking": True},
                         agent_runtime.llm._siliconflow_thinking_params(
                             {"reasoning_effort": "high"}))
        self.assertEqual({"enable_thinking": True, "thinking_budget": 32768},
                         agent_runtime.llm._siliconflow_thinking_params(
                             {"reasoning_effort": "max"}))
        self.assertEqual({}, agent_runtime.llm._siliconflow_thinking_params(
            {"reasoning_effort": "auto"}))

    def test_transaction_preserves_other_features_and_sources(self):
        plan = plan_state.create("Part", _feature())
        self.assertEqual("valid", cadplan.validate_plan(plan)["state"])
        with self.assertRaises(ValueError):
            plan_state.update(plan, "base", parameter_updates={"length": 120})
        revised = plan_state.update(
            plan, "base", parameter_updates={"length": 120},
            source_updates={"length": {"type": "user", "evidence": "new request"}})
        self.assertEqual(100, plan["features"][0]["parameters"]["length"])
        self.assertEqual(50, revised["features"][0]["parameters"]["width"])
        self.assertEqual(plan["features"][0]["sources"]["width"],
                         revised["features"][0]["sources"]["width"])

    def test_live_document_replaces_stale_plan(self):
        old = plan_state.create("Part", _feature())
        document = cadplan.plan_to_document(old)
        document.features[0].params["length"] = 120
        imported = plan_state.from_document(document.to_dict(), old)
        self.assertEqual(120, imported["features"][0]["parameters"]["length"])
        self.assertEqual("existing_model",
                         imported["features"][0]["sources"]["length"]["type"])
        self.assertEqual("user", imported["features"][0]["sources"]["width"]["type"])

    def test_initial_bootstrap_excludes_questions_and_existing_models(self):
        self.assertTrue(agent_runtime._requires_initial_model_creation(
            "复刻这个", ["reference.png"], None))
        self.assertFalse(agent_runtime._requires_initial_model_creation(
            "这张图片是什么？", ["reference.png"], None))
        for question in ("这张图怎么建模", "请问这张图怎么建模",
                         "请分析这张图能怎么建模"):
            self.assertTrue(agent_runtime._explicit_image_question(question), question)
            self.assertFalse(agent_runtime._requires_initial_model_creation(
                question, ["reference.png"], None), question)
        self.assertTrue(agent_runtime._requires_initial_model_creation(
            "按图复刻", ["reference.png"], None))
        self.assertFalse(agent_runtime._requires_initial_model_creation(
            "修改一下", ["reference.png"], {"features": []}))
        self.assertTrue(agent_runtime._requires_initial_model_creation(
            "建立一个长方体", [], None))
        self.assertTrue(agent_runtime._requires_initial_model_creation(
            "按刚才图片新建模型", [], None))
        self.assertFalse(agent_runtime._requires_initial_model_creation(
            "你好", [], None))
        text_tool = agent_runtime._initial_creation_tools(
            agent_runtime.AGENT_TOOLS, has_reference_images=False)[0]
        image_tool = agent_runtime._initial_creation_tools(
            agent_runtime.AGENT_TOOLS, has_reference_images=True)[0]
        self.assertNotIn("reference_reading",
                         text_tool["function"]["parameters"]["required"])
        self.assertNotIn("reference_reading",
                         image_tool["function"]["parameters"]["required"])
        inspect_tool = agent_runtime._initial_reference_inspection_tool(
            agent_runtime.AGENT_TOOLS)[0]
        self.assertEqual("cad_inspect_references",
                         inspect_tool["function"]["name"])
        self.assertEqual(["reference_reading"],
                         inspect_tool["function"]["parameters"]["required"])

    def test_model_feature_tools_require_parameter_source_metadata(self):
        feature_schema = agent_runtime._FEATURE_TOOL_SCHEMA

        self.assertIn("sources", feature_schema["required"])
        self.assertIn("每个数值参数", feature_schema["properties"]["sources"]["description"])
        add_tool = next(row for row in agent_runtime.AGENT_TOOLS
                        if row["function"]["name"] == "cad_add_feature")
        self.assertIn("pattern 的 count 和 step", add_tool["function"]["description"])

    def test_reference_inspection_requires_per_face_image_evidence(self):
        reading = _reading()
        reading["feature_groups"] = [
            {"id": "physical-through-hole-set", "kind": "hole", "quantity": 6,
             "region": "whole part; unique physical hole positions",
             "arrangement": "unique positions counted once across visible faces",
             "evidence": "physical hole estimate deduplicates openings visible on multiple faces"},
            {"id": "main-body", "kind": "body", "quantity": 1,
             "region": "whole part", "arrangement": "single connected body",
             "evidence": "one connected outer body"},
        ]
        reading["visible_regions"] = [
            {"image": "reference.png", "id": "left", "region": "left face",
             "surface_state": "solid_face", "image_bbox_norm": [0.1, 0.1, 0.35, 0.9],
             "evidence": "bounded front face", "features": [{
                 "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
                 "evidence": "three distinct openings"}]},
            {"image": "reference.png", "id": "center", "region": "recessed center face",
             "surface_state": "recessed_solid_face", "image_bbox_norm": [0.33, 0.2, 0.5, 0.9],
             "evidence": "bounded rear plane with visible holes", "features": [{
                 "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
                 "evidence": "three openings on the rear plane"}]},
            {"image": "reference.png", "id": "right", "region": "right face",
             "surface_state": "solid_face", "image_bbox_norm": [0.45, 0.3, 0.8, 0.98],
             "evidence": "bounded front face", "features": [{
                 "kind": "hole", "quantity": 3, "arrangement": "large-small-large",
                 "evidence": "three distinct openings"}]},
        ]
        state = {"has_reference_images": True,
                 "active_reference_images": ["reference.png"]}
        valid, _ = agent_runtime._execute_tool(
            "cad_inspect_references", {"reference_reading": reading}, state, [])
        self.assertEqual("reference_reading_ready", valid["state"])
        self.assertEqual(9, valid["visible_hole_aperture_count"])
        self.assertEqual(6, agent_runtime._reference_hole_quantity(
            state["reference_reading"]))
        self.assertEqual("cad_inspect_references", state["reference_reading_source"])
        self.assertEqual(3, len(state["reference_reading"]["visible_regions"]))
        self.assertEqual([3, 3, 3], [
            region["features"][0]["quantity"]
            for region in state["reference_reading"]["visible_regions"]])

    def test_model_tools_publish_structured_feature_and_relation_schema(self):
        tools = {row["function"]["name"]: row["function"]["parameters"]
                 for row in agent_runtime.AGENT_TOOLS}
        create_feature = tools["cad_create_model"]["properties"]["first_feature"]
        add_feature = tools["cad_add_feature"]["properties"]["feature"]
        self.assertEqual(create_feature, add_feature)
        self.assertEqual(["id", "kind", "operation", "parameters", "sources"],
                         add_feature["required"])
        self.assertEqual(["center", "point", "pcd", "corners"],
                         add_feature["properties"]["parameters"]["properties"]["pos"]["enum"])
        self.assertEqual("array", add_feature["properties"]["parameters"]["properties"]["point"]["type"])
        relation = add_feature["properties"]["relations"]["items"]
        self.assertEqual(["type", "feature_id"], relation["required"])
        self.assertEqual(["depends_on", "pattern_source"],
                         relation["properties"]["type"]["enum"])
        self.assertFalse(relation["additionalProperties"])
        self.assertIn("operation 必须与 pattern_source 源特征相同",
                      add_feature["properties"]["parameters"]["description"])
        self.assertIn("圆柱刀具加成实体",
                      add_feature["properties"]["parameters"]["description"])
        self.assertNotIn("reference_reading", tools["cad_create_model"]["properties"])
        reading_schema = tools["cad_inspect_references"]["properties"][
            "reference_reading"]
        self.assertIn("visible_regions", reading_schema["properties"])
        reading_group = reading_schema["properties"][
            "feature_groups"]["items"]
        self.assertEqual(["id", "kind", "quantity", "region", "arrangement",
                          "evidence"], reading_group["required"])

    def test_text_build_saves_editable_project_and_mesh(self):
        replies = [
            {"content": "create", "tool_calls": [
                _call("cad_create_model", {"name": "Part", "first_feature": _feature()}, 1)]},
            {"content": "finish", "tool_calls": [
                _call("cad_finish", {"summary": "已生成本地模型。"}, 2)]},
        ]
        requests = []

        def completion(_messages, _cfg, **kwargs):
            requests.append({
                "tool_choice": kwargs.get("tool_choice") or "auto",
                "tools": [row["function"]["name"] for row in kwargs.get("tools") or []],
                "required": next((row["function"]["parameters"].get("required")
                                  for row in kwargs.get("tools") or []), []),
            })
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                agent_runtime.llm, "chat_completion", side_effect=completion):
            result = agentloop.run_agent_loop(
                "建立长方体", cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                                   "online": {"enabled": False}}, output_dir=directory)
            self.assertEqual("previewed", result["state"])
            self.assertEqual("passed", result["local_geometry_checks"]["state"])
            self.assertTrue(Path(result["output_paths"]["aimodel"]).is_file())
            self.assertTrue(Path(result["output_paths"]["stl"]).is_file())
        self.assertEqual({"type": "function", "function": {
            "name": "cad_create_model"}}, requests[0]["tool_choice"])
        self.assertEqual(["cad_create_model"], requests[0]["tools"])
        self.assertNotIn("reference_reading", requests[0]["required"])
        self.assertEqual("required", requests[1]["tool_choice"])
        self.assertGreater(len(requests[1]["tools"]), 1)

    def test_image_question_stays_conversational_without_forced_tool(self):
        requests = []

        def completion(_messages, _cfg, **kwargs):
            requests.append({
                "tool_choice": kwargs.get("tool_choice") or "auto",
                "tools": [row["function"]["name"] for row in kwargs.get("tools") or []],
            })
            return {"content": "图中是带有孔位的板件。", "tool_calls": []}

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "reference.png"
            image.write_bytes(_PIXEL)
            with mock.patch.object(agent_runtime.llm, "chat_completion",
                                   side_effect=completion):
                results = [agentloop.run_agent_loop(
                    prompt, image_paths=[str(image)],
                    cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                         "online": {"enabled": False}}, output_dir=directory)
                    for prompt in ("这张图片是什么？", "这张图怎么建模",
                                   "请问这张图怎么建模")]

        self.assertEqual(["conversation"] * 3,
                         [result["state"] for result in results])
        self.assertEqual(["auto"] * 3,
                         [request["tool_choice"] for request in requests])
        self.assertTrue(all("cad_create_model" in request["tools"]
                            and len(request["tools"]) > 1 for request in requests))

    def test_existing_model_image_modification_requires_tool_action(self):
        requests = []

        def completion(_messages, _cfg, **kwargs):
            requests.append({
                "tool_choice": kwargs.get("tool_choice") or "auto",
                "tools": [row["function"]["name"] for row in kwargs.get("tools") or []],
            })
            return {"content": "我会基于当前模型调整比例。", "tool_calls": []}

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "reference.png"
            image.write_bytes(_PIXEL)
            with mock.patch.object(agent_runtime.llm, "chat_completion",
                                   side_effect=completion):
                result = agentloop.run_agent_loop(
                    "沿用现有模型调整比例", image_paths=[str(image)],
                    current_plan=plan_state.create("Part", _feature()),
                    cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                         "online": {"enabled": False}}, output_dir=directory)

        self.assertEqual("needs_user_input", result["state"])
        self.assertEqual(2, len(requests))
        self.assertIn("连续两次没有调用 CAD 工具", result["reply"])
        self.assertIsNotNone(result["plan"])
        self.assertEqual("required", requests[0]["tool_choice"])
        self.assertEqual("required", requests[1]["tool_choice"])
        self.assertIn("cad_create_model", requests[0]["tools"])
        self.assertGreater(len(requests[0]["tools"]), 1)

    def test_no_tool_response_guard_resets_after_a_tool_call(self):
        replies = [
            {"content": "我会继续处理。", "tool_calls": []},
            {"content": "读取当前特征树。", "tool_calls": [
                _call("cad_get_state", {}, 1)]},
            {"content": "我会继续修改。", "tool_calls": []},
            {"content": "仍无法继续。", "tool_calls": []},
        ]
        requests = []

        def completion(messages, _cfg, **kwargs):
            requests.append({
                "tool_choice": kwargs.get("tool_choice") or "auto",
                "nudge_count": sum(
                    1 for row in messages if row.get("role") == "user"
                    and isinstance(row.get("content"), str)
                    and "上一条回复没有调用 CAD 工具" in row["content"]),
            })
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "reference.png"
            image.write_bytes(_PIXEL)
            with mock.patch.object(agent_runtime.llm, "chat_completion",
                                   side_effect=completion):
                result = agentloop.run_agent_loop(
                    "沿用现有模型调整比例", image_paths=[str(image)],
                    current_plan=plan_state.create("Part", _feature()),
                    cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                         "online": {"enabled": False}}, output_dir=directory)

        self.assertEqual(4, len(requests))
        self.assertEqual(["required"] * 4,
                         [request["tool_choice"] for request in requests])
        self.assertEqual([0, 1, 1, 2],
                         [request["nudge_count"] for request in requests])
        self.assertEqual("needs_user_input", result["state"])
        self.assertIsNotNone(result["plan"])
        self.assertIn("连续两次没有调用 CAD 工具", result["reply"])
        self.assertTrue(result["questions"])

    def test_multiview_review_must_cover_both_reference_images(self):
        steps = [
            ("cad_inspect_references", {"reference_reading": {
                "feature_groups": [{"id": "main-body", "kind": "body", "quantity": 1,
                                    "region": "whole part", "arrangement": "single body",
                                    "evidence": "one continuous outer body"}],
                "visible_regions": [
                    {"image": name + ".png", "id": "body", "region": "whole part",
                     "surface_state": "solid_face", "image_bbox_norm": [0, 0, 1, 1],
                     "evidence": "one continuous body", "features": []}
                    for name in ("iso", "front")],
                "uncertainties": [],
            }}),
            ("cad_create_model", {"name": "Part", "first_feature": _feature("image_estimate")}),
            ("cad_review_model", {"review_type": "geometry", "views": ["iso"]}),
            ("cad_finish", {}),
            ("cad_review_model", {"review_type": "geometry", "views": ["front"]}),
            ("cad_finish", {}),
        ]

        requests = []

        def completion(*_args, **_kwargs):
            requests.append({
                "tool_choice": _kwargs.get("tool_choice") or "auto",
                "tools": [row["function"]["name"]
                          for row in _kwargs.get("tools") or []],
                "required": next((row["function"]["parameters"].get("required")
                                  for row in _kwargs.get("tools") or []), []),
            })
            name, arguments = steps.pop(0)
            return {"content": name, "tool_calls": [_call(name, arguments, len(steps))]}

        def review(*args, **_kwargs):
            if args[3] == "参考图独立盲读":
                return {"images": [
                    {"image": name + ".png", "source_view": name,
                     "coverage": "whole_object", "hole_layout_coverage": "unknown",
                     "visible_regions": [{
                         "id": "body", "region": "whole part",
                         "surface_state": "solid_face", "evidence": "one body",
                         "features": [{"kind": "other", "quantity": 1,
                                       "arrangement": "single", "evidence": "body"}],
                     }], "uncertainties": []}
                    for name in ("iso", "front")], "cross_view_notes": []}
            view = args[4]["active_review_view"]
            group_id = "blind-01-01-01" if view == "iso" else "blind-02-01-01"
            return {
                "reference_images": [
                    {"image": name + ".png", "source_view": name,
                     "comparable": name == view,
                     "visible_evidence": "part visible" if name == view else "other camera"}
                    for name in ("iso", "front")],
                "checks": [
                    {"id": view + "." + area, "view": view, "area": area,
                     "category": "geometry", "status": "match", "item": area,
                     "reference_evidence": "reference outline",
                     "model_evidence": "model outline", "evidence": "same outline"}
                    for area in agentloop.REVIEW_VIEW_AREAS[view]] + [
                    {"id": view + ".feature_group." + group_id,
                     "feature_group_id": group_id, "view": view,
                     "area": "feature_group", "category": "geometry",
                     "status": "match", "item": "outer body",
                     "reference_evidence": "one body",
                     "model_evidence": "one body", "evidence": "same outline"}],
                "issues": [], "resolved_findings": [], "questions": [],
                "confidence": 0.1,
            }

        with tempfile.TemporaryDirectory() as directory:
            images = []
            for name in ("iso", "front"):
                path = Path(directory) / (name + ".png")
                path.write_bytes(_PIXEL)
                images.append(str(path))
            with mock.patch.object(agent_runtime.llm, "chat_completion",
                                   side_effect=completion), mock.patch.object(
                    agentloop, "_run_json_object", side_effect=review):
                result = agentloop.run_agent_loop(
                    "按图复刻", image_paths=images,
                    cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                         "online": {"enabled": False}}, output_dir=directory)
            finishes = [row["result"]["state"] for row in result["tool_trace"]
                        if row["tool"] == "cad_finish"]
            self.assertEqual(["needs_visual_review", "reviewed"], finishes)
        self.assertEqual({"type": "function", "function": {
            "name": "cad_inspect_references"}}, requests[0]["tool_choice"])
        self.assertEqual(["cad_inspect_references"], requests[0]["tools"])
        self.assertEqual(["reference_reading"], requests[0]["required"])
        self.assertEqual({"type": "function", "function": {
            "name": "cad_create_model"}}, requests[1]["tool_choice"])
        self.assertEqual(["cad_create_model"], requests[1]["tools"])
        self.assertEqual("required", requests[2]["tool_choice"])
        self.assertGreater(len(requests[2]["tools"]), 1)

    def test_named_tool_choice_rejection_falls_back_to_auto_once(self):
        state = {"deadline": time.monotonic() + 10, "model_cfg": {
            "model": "mock", "base_url": "https://provider.example/v1"}}
        selected = {"type": "function", "function": {
            "name": "cad_create_model"}}
        with mock.patch.object(
                agent_runtime.llm, "chat_completion",
                side_effect=[RuntimeError(
                    "Thinking mode does not support tool_choice"),
                    {"content": "created", "tool_calls": []}]) as completion:
            result = agent_runtime._call_model(
                [{"role": "user", "content": "build"}], state,
                [agent_runtime.AGENT_TOOLS[2]],
                1024, tool_choice=selected)

        self.assertEqual("created", result["content"])
        self.assertEqual(selected, completion.call_args_list[0].kwargs["tool_choice"])
        self.assertIsNone(completion.call_args_list[1].kwargs["tool_choice"])

    def test_required_tool_choice_rejection_falls_back_and_surfaces_warning(self):
        state = {"deadline": time.monotonic() + 10, "model_cfg": {
            "model": "mock", "base_url": "https://provider.example/v1"}}
        with mock.patch.object(
                agent_runtime.llm, "chat_completion",
                side_effect=[RuntimeError(
                    "Thinking mode does not support tool_choice=required"),
                    {"content": "继续建模", "tool_calls": []}]) as completion:
            reply = agent_runtime._call_model(
                [{"role": "user", "content": "继续建模"}], state,
                agent_runtime.AGENT_TOOLS, 1024, tool_choice="required")

        self.assertEqual("继续建模", reply["content"])
        self.assertEqual("required", completion.call_args_list[0].kwargs["tool_choice"])
        self.assertIsNone(completion.call_args_list[1].kwargs["tool_choice"])
        self.assertTrue(state["tool_choice_required_unsupported"])
        result = agent_runtime._result_from_state(
            {"state": "needs_user_input"}, state, "当前候选已保留。")
        warning = state["tool_choice_compatibility_warning"]
        self.assertIn(warning, result["warnings"])
        self.assertIn(warning, result["reply"])

    def test_web_search_is_model_available_without_prompt_keywords(self):
        state = {"cfg": {"online": {"enabled": True}},
                 "web_search_allowed": True, "web_search_calls": 0}
        names = {row["function"]["name"] for row in agent_runtime._build_tools(state)}
        self.assertIn("web_search", names)
        with mock.patch.object(agentloop.online, "search", return_value=[
                {"title": "standard", "snippet": "12 mm", "url": "https://example.com/spec"}]), \
                mock.patch.object(agentloop.online, "fetch_page_text", return_value="12 mm"):
            found = agent_runtime._web_search({"query": "standard part"}, state)
            opened = agent_runtime._web_open_result(
                {"result_id": found["results"][0]["result_id"]}, state)
        self.assertEqual("opened", opened["state"])
        self.assertFalse(agent_runtime._safe_result_url("http://127.0.0.1/private"))

    def test_requested_view_image_reaches_next_model_call(self):
        replies = [
            {"content": "create", "tool_calls": [_call(
                "cad_create_model", {"name": "Part", "first_feature": _feature()}, 1)]},
            {"content": "look", "tool_calls": [_call(
                "cad_preview_plan", {"view_set": "iso"}, 2)]},
            {"content": "finish", "tool_calls": [_call("cad_finish", {}, 3)]},
        ]
        image_counts = []

        def completion(messages, *_args, **_kwargs):
            image_counts.append(sum(
                1 for row in messages
                for part in (row.get("content") if isinstance(row.get("content"), list) else [])
                if isinstance(part, dict) and part.get("type") == "image_url"))
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                agent_runtime.llm, "chat_completion", side_effect=completion):
            result = agentloop.run_agent_loop(
                "建立长方体", cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                                   "online": {"enabled": False}}, output_dir=directory)
        self.assertEqual("previewed", result["state"])
        self.assertEqual([0, 0, 1], image_counts)

    def test_visual_finding_requires_changed_geometry_and_new_match(self):
        first = plan_state.create("Part", _feature("image_estimate"))
        issue = {"issue_id": "iso.length", "view": "iso", "category": "geometry",
                 "severity": "high", "item": "length", "evidence": "too short",
                 "feature_ids": ["base"],
                 "affected_parameters": [{"feature_id": "base",
                                          "path": "parameters.length"}]}
        previous, _, _ = agentloop._update_multiview_findings(
            [], {"issues": [issue], "checks": []}, first, 1)
        check = {"id": "iso.overall_shape", "view": "iso", "area": "overall_shape",
                 "category": "geometry", "status": "match", "item": "outline",
                 "reference_evidence": "original longer profile",
                 "model_evidence": "new longer profile", "evidence": "ratio now aligned",
                 "feature_ids": ["base"],
                 "affected_parameters": [{"feature_id": "base",
                                          "path": "parameters.length"}]}
        recheck = {"issues": [], "checks": [check], "resolved_findings": [
            {"issue_id": "iso.length", "check_id": "iso.overall_shape",
             "evidence": "new view matches"}]}
        still_open, resolved, _ = agentloop._update_multiview_findings(
            previous, recheck, first, 2)
        self.assertEqual([], resolved)
        self.assertEqual(1, len(still_open))
        revised = plan_state.update(
            first, "base", parameter_updates={"length": 120},
            source_updates={"length": {"type": "image_estimate",
                                       "evidence": "revised visible ratio"}})
        open_after_change, resolved, _ = agentloop._update_multiview_findings(
            previous, recheck, revised, 2)
        self.assertEqual([], open_after_change)
        self.assertEqual(["iso.length"], resolved)

    def test_through_hole_is_checked_against_mesh(self):
        plan = plan_state.create("Plate", _feature())
        plan = plan_state.add(plan, {
            "id": "hole", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 5, "through": True, "pos": "point",
                           "point": [0, 0, 0], "axis": "Z"},
            "sources": {"diameter": {"type": "user"}, "point": {"type": "user"}},
        })
        with tempfile.TemporaryDirectory() as directory:
            preview = cadplan.preview_plan(
                plan, str(Path(directory) / "hole.png"), view_set="iso")
        holes = preview["local_geometry_checks"]["through_hole_mesh_checks"]
        self.assertEqual("verified_open", holes[0]["status"])
        self.assertEqual(0, holes[0]["axis_surface_crossings"])

    def test_timeout_returns_saved_candidate_instead_of_discarding_it(self):
        plan = plan_state.create("Part", _feature())
        with tempfile.TemporaryDirectory() as directory:
            preview = cadplan.preview_plan(
                plan, str(Path(directory) / "preview.png"), view_set="iso")

            def interrupted(*_args, **kwargs):
                state = kwargs["_state_out"]
                fingerprint = agentloop._geometry_plan_fingerprint(plan)
                state.update({
                    "current_plan": plan, "latest_preview": preview,
                    "plan_validation": cadplan.validate_plan(plan),
                    "plan_geometry_fingerprint": fingerprint,
                    "preview_geometry_fingerprint": fingerprint,
                    "has_reference_images": False,
                })
                raise agentloop.AgentLoopError("AgentLoop 达到超时上限")

            with mock.patch.object(agentloop, "_run_agent_loop_impl",
                                   side_effect=interrupted):
                result = agentloop.run_agent_loop(
                    "建立长方体", output_dir=directory)
            self.assertEqual("previewed", result["state"])
            self.assertEqual("timeout", result["interrupted_reason"])
            self.assertTrue(Path(result["output_paths"]["aimodel"]).is_file())

    def test_measured_silhouette_overrides_visual_match(self):
        reference_plan = plan_state.create("Reference", _feature("image_estimate"))
        candidate_feature = _feature("image_estimate")
        candidate_feature["parameters"].update({"length": 130, "width": 70,
                                                "height": 35})
        candidate_plan = plan_state.create("Candidate", candidate_feature)
        with tempfile.TemporaryDirectory() as directory:
            reference_image = Path(directory) / "reference.png"
            cadplan.preview_plan(reference_plan, str(reference_image), view_set="iso")
            candidate_preview = cadplan.preview_plan(
                candidate_plan, str(Path(directory) / "candidate.png"), view_set="iso")
            comparison = image_analysis.compare_same_renderer_silhouette(
                reference_image, candidate_preview["preview_image"])
            self.assertEqual("measured", comparison["state"])
            self.assertFalse(comparison["passed"])
            review = {
                "reference_images": [{"image": "reference.png", "source_view": "iso",
                                      "comparable": True, "visible_evidence": "box"}],
                "checks": [
                    {"id": "iso." + area, "area": area, "category": "geometry",
                     "status": "match", "item": area,
                     "reference_evidence": "box in reference",
                     "model_evidence": "box in candidate"}
                    for area in agentloop.REVIEW_VIEW_AREAS["iso"]],
                "issues": [], "resolved_findings": [], "questions": [],
            }
            state = {
                "deadline": time.monotonic() + 30,
                "session_dir": Path(directory), "preview_calls": 0,
                "active_reference_images": ["reference.png"],
                "reference_image_paths": {"reference.png": str(reference_image)},
            }
            with mock.patch.object(agentloop, "_run_json_object", side_effect=[
                    _blind_inventory("reference.png", "iso"), review]):
                result = agentloop._split_view_review(
                    candidate_plan, candidate_preview, [], {}, state, None,
                    "multiview_check", 1, "照图建模", views=["iso"])
            self.assertEqual("revise", result["decision"])
            self.assertTrue(any(row.get("area") == "silhouette_ratio"
                                and row.get("status") == "mismatch"
                                for row in result["checks"]))
            self.assertTrue(state["temporary_review_images"][0]["deleted"])

    def test_visual_review_checks_blind_inventory_not_builder_groups(self):
        plan = plan_state.create("Part", _feature("user"))
        independent = _blind_inventory(regions=[{
            "id": "front-face", "region": "front face",
            "surface_state": "solid_face", "evidence": "one bounded face",
            "features": [{"kind": "other", "quantity": 1,
                          "arrangement": "single connected body",
                          "evidence": "one outer silhouette"}],
        }])
        group_id = "blind-01-01-01"
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            model_image = Path(directory) / "candidate.png"
            cadplan.preview_plan(plan, str(reference), view_set="iso")
            preview = cadplan.preview_plan(plan, str(model_image), view_set="iso")
            preview["preview_image"] = str(model_image)
            base_checks = [
                {"id": "iso." + area, "area": area, "category": "geometry",
                 "status": "match", "item": area,
                 "reference_evidence": "same outline",
                 "model_evidence": "same outline", "evidence": "overlap"}
                for area in agentloop.REVIEW_VIEW_AREAS["iso"]]
            response = {
                "reference_images": [{"image": "reference.png", "source_view": "iso",
                                      "comparable": True, "visible_evidence": "same body"}],
                "checks": base_checks, "issues": [], "resolved_findings": [],
                "questions": [], "confidence": 1.0,
            }
            state = {
                "deadline": time.monotonic() + 30, "session_dir": Path(directory),
                "preview_calls": 0, "active_reference_images": ["reference.png"],
                "reference_image_paths": {"reference.png": str(reference)},
                "reference_reading": {"feature_groups": [{
                    "id": "builder-wrong-group", "kind": "hole", "quantity": 7,
                    "region": "misread face", "arrangement": "incorrect",
                    "evidence": "builder hypothesis, not source truth"}]},
            }
            calls = []

            def multimodal(*args, **_kwargs):
                calls.append(args)
                return independent if args[3] == "参考图独立盲读" else response

            with mock.patch.object(agentloop, "_run_json_object", side_effect=multimodal):
                missing = agentloop._split_view_review(
                    plan, preview, [], {}, state, None, "multiview_check", 1,
                    "复刻", views=["iso"])
            self.assertEqual("inspect", missing["decision"])
            self.assertTrue(any(group_id in gap for gap in missing["review_gaps"]))
            self.assertEqual("参考图独立盲读", calls[0][3])
            blind_request = json.dumps(calls[0][0], ensure_ascii=False)
            self.assertNotIn("builder-wrong-group", blind_request)
            self.assertNotIn("final_plan", blind_request)
            self.assertNotIn("candidate.png", blind_request)
            comparison_payload = calls[1][0][-1]["content"][0]["text"]
            self.assertIn("independent_reference_inventory", comparison_payload)
            self.assertNotIn("builder-wrong-group", comparison_payload)

            response["checks"].append({
                "id": "iso.feature_group." + group_id, "feature_group_id": group_id,
                "area": "feature_group", "category": "geometry", "status": "match",
                "item": "outer body", "reference_evidence": "single body",
                "model_evidence": "single body", "evidence": "same silhouette"})
            state["preview_calls"] = 0
            with mock.patch.object(agentloop, "_run_json_object", return_value=response):
                complete = agentloop._split_view_review(
                    plan, preview, [], {}, state, None, "multiview_check", 2,
                    "复刻", views=["iso"])
            self.assertEqual("accept", complete["decision"])
            self.assertEqual("passed", complete["feature_group_checks"][0]["status"])

    def test_unmeasured_scanline_ratio_evidence_is_refused(self):
        feature = _feature("image_estimate")
        feature["sources"]["length"]["evidence"] = (
            "front view width ratios ~0.14:0.71:0.15 from scanline")
        plan = plan_state.create("Part", feature)
        rejected = {"image_index": 1, "filename": "iso-screenshot.png",
                    "three_region_scanline_candidate": None,
                    "foreground_bbox_candidate": None,
                    "scanline_rejected": {"reason": "扫描线只在 19/544 px 高度上一致"}}
        state = {"raster_analysis": {"available": True, "images": [rejected]}}

        blocked = agent_runtime._unsupported_pixel_evidence(plan, state)
        self.assertTrue(blocked["offending"])
        self.assertEqual("base", blocked["offending"][0]["feature_id"])
        self.assertIn("扫描线", blocked["instruction"])
        self.assertEqual("invalid", agent_runtime._set_plan({"plan": plan}, state)["state"])

        # A measurement the scanline tool actually stands behind is allowed.
        usable = {"image_index": 1, "filename": "front-orthographic.png",
                  "three_region_scanline_candidate": {"usable_for_dimensions": True}}
        self.assertEqual({}, agent_runtime._unsupported_pixel_evidence(
            plan, {"raster_analysis": {"available": True, "images": [usable]}}))

        # A purely visual estimate is never blocked, and neither is a turn with
        # no reference image at all.
        visual = plan_state.create("Part", _feature("image_estimate"))
        self.assertEqual({}, agent_runtime._unsupported_pixel_evidence(visual, state))
        self.assertEqual({}, agent_runtime._unsupported_pixel_evidence(plan, {}))

    def test_source_only_repair_commits_without_republishing(self):
        plan = plan_state.create("Part", _feature("image_estimate"))
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": agent_runtime.cad._geometry_plan_fingerprint(plan),
            "plan_revision": 1, "tool_calls": 0, "preview_calls": 0,
            "session_dir": Path(tempfile.mkdtemp()),
        }
        result = agent_runtime._apply_incremental("cad_update_feature", {
            "feature_id": "base",
            "source_updates": {"length": {"type": "image_estimate",
                                          "evidence": "manual re-estimate from outline"}}},
            state)

        self.assertEqual("valid", result["state"])
        self.assertTrue(result["geometry_unchanged"])
        self.assertEqual(0, state["preview_calls"])
        self.assertEqual(
            "manual re-estimate from outline",
            state["current_plan"]["features"][0]["sources"]["length"]["evidence"])

    def test_cosmetic_parameter_nudge_is_not_progress(self):
        plan = plan_state.create("Part", _feature("user"))
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": agent_runtime.cad._geometry_plan_fingerprint(plan),
            "plan_revision": 1, "tool_calls": 0, "preview_calls": 0,
            "session_dir": Path(tempfile.mkdtemp()),
        }
        result = agent_runtime._apply_incremental("cad_update_feature", {
            "feature_id": "base",
            "parameter_updates": {"length": 100.001},
            "source_updates": {"length": {"type": "user"}}}, state)

        self.assertEqual("unchanged", result["state"])
        self.assertTrue(result["cosmetic"])
        self.assertEqual(1, state["cosmetic_updates"])
        self.assertEqual(100, state["current_plan"]["features"][0]["parameters"]["length"])

    def test_isometric_only_depth_claim_is_blocked(self):
        plan = plan_state.create("Part", _feature("image_estimate"))
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        checks = []
        for area in agentloop.REVIEW_VIEW_AREAS["iso"]:
            claim = ("孔口显示为前表面圆形开口，表明孔沿深度方向贯穿墙体"
                     if area == "depth_relationships" else "等轴测轮廓与参考图一致")
            checks.append({"id": "iso." + area, "view": "iso", "area": area,
                           "category": "geometry", "status": "match", "item": area,
                           "evidence": claim,
                           "reference_evidence": claim, "model_evidence": claim})
        review = {"decision": "accept", "checks": checks, "issues": [],
                  "resolved_findings": [], "questions": [], "review_gaps": []}
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": {"state": "passed"}},
            "preview_geometry_fingerprint": fingerprint,
            "has_reference_images": True,
            "reference_reading": _body_reading(),
            "reviewed_view_status": {"iso": "passed"},
            "active_reference_images": ["ref.png"],
            "reference_images_reviewed": ["ref.png"],
            "latest_geometry_review": {"geometry_fingerprint": fingerprint,
                                       "views": ["iso"], "result": review},
        }

        gate = agent_runtime._finish_gate(state)
        self.assertTrue(gate["ready"], gate.get("blockers"))
        self.assertNotEqual("needs_visual_review", gate.get("state"))

    def test_finish_gate_rejects_equal_holes_against_clear_large_small_large_reference(self):
        """A free-text visual accept cannot override a measurable source pattern."""
        body = {"id": "base", "kind": "box", "operation": "add",
                "parameters": {"length": 120, "width": 80, "height": 100,
                               "center": [0, 0, 50]},
                "sources": {key: {"type": "image_estimate", "evidence": "visual ratio"}
                            for key in ("length", "width", "height", "center")}}
        plan = plan_state.create("Part", body)
        grouped_ids = {}
        for group_name, x in (("left", -40), ("center", 0), ("right", 40)):
            ids = []
            for row_name, z in (("top", 80), ("mid", 50), ("bottom", 20)):
                feature_id = "%s-%s" % (group_name, row_name)
                ids.append(feature_id)
                hole = {"id": feature_id, "kind": "hole", "operation": "cut",
                        "parameters": {"diameter": 12, "through": True, "pos": "point",
                                       "point": [x, -40, z], "axis": "Y"},
                        "sources": {key: {"type": "image_estimate",
                                           "evidence": "visible hole proportion"}
                                    for key in ("diameter", "point", "axis")}}
                plan = plan_state.add(plan, hole)
            grouped_ids[group_name] = ids

        inventory_regions = []
        group_checks = []
        for index, group_name in enumerate(("left", "center", "right"), 1):
            inventory_regions.append({
                "id": group_name, "region": group_name + " panel",
                "surface_state": "recessed_solid_face" if group_name == "center" else "solid_face",
                "evidence": "three circular holes with visibly different diameters",
                "features": [{"kind": "hole", "quantity": 3,
                              "arrangement": "vertical top to bottom",
                              "evidence": "top and bottom are larger than middle",
                              "relative_size_order": ["large", "small", "large"],
                              "size_order_axis": "vertical_top_to_bottom",
                              "size_order_confidence": "high"}],
            })
            group_id = "blind-01-%02d-01" % index
            group_checks.append({
                "id": "iso.feature_group." + group_id,
                "view": "iso", "area": "feature_group",
                "feature_group_id": group_id, "category": "geometry",
                "status": "match", "item": "three holes",
                "reference_evidence": "large-small-large",
                "model_evidence": "three visible holes",
                "evidence": "same count", "unknown_reason": "",
                "feature_ids": grouped_ids[group_name],
                "affected_parameters": [],
            })
        inventory = {"source": "independent_source_only_multimodal_read", "images": [{
            "image": "ref.png", "source_view": "iso", "coverage": "whole_object",
            "hole_layout_coverage": "partial", "visible_regions": inventory_regions,
            "uncertainties": [],
        }], "cross_view_notes": []}

        geometry_checks = [
            {"id": "iso." + area, "view": "iso", "area": area,
             "category": "geometry", "status": "match", "item": area,
             "evidence": "paired image evidence", "reference_evidence": "visible",
             "model_evidence": "visible"}
            for area in agentloop.REVIEW_VIEW_AREAS["iso"]
        ] + group_checks
        review = {"decision": "accept", "checks": geometry_checks, "issues": [],
                  "resolved_findings": [], "questions": [], "review_gaps": [],
                  "reference_inventory": inventory}
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        preview = cadplan.preview_plan(plan)
        self.assertEqual("passed", preview["local_geometry_checks"]["state"])
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "preview_geometry_fingerprint": fingerprint,
            "latest_preview": preview,
            "has_reference_images": True,
            "reference_reading": _body_reading(),
            "independent_reference_inventory": inventory,
            "reviewed_view_status": {"iso": "passed"},
            "active_reference_images": ["ref.png"],
            "reference_images_reviewed": ["ref.png"],
            "latest_geometry_review": {"geometry_fingerprint": fingerprint,
                                       "views": ["iso"], "result": review},
        }

        gate = agent_runtime._finish_gate(state)
        self.assertFalse(gate["ready"], gate)
        self.assertEqual("relative_feature_size_mismatch", gate["state"])
        self.assertTrue(any("large—small—large" in row and "12 mm" in row
                            for row in gate["blockers"]), gate["blockers"])

        corrected = plan
        for group_name in ("left", "center", "right"):
            feature_id = group_name + "-mid"
            corrected = plan_state.update(
                corrected, feature_id, parameter_updates={"diameter": 8},
                source_updates={"diameter": {"type": "image_estimate",
                                              "evidence": "middle hole visibly smaller"}})
        self.assertEqual([], agent_runtime._reference_size_order_blockers(
            state, corrected, review))

    def test_iso_only_review_is_not_forced_to_add_orthographic_views(self):
        """A single isometric reference cannot be checked against front/right.

        An earlier rule demanded an orthographic review whenever the iso review
        mentioned depth. The model obeyed, those views came back `unknown`, the
        gate could not close, and it re-reviewed them for seventeen rounds. The
        through-slot risk that rule aimed at is covered by the mesh instead.
        """
        plan = plan_state.create("Part", _feature("image_estimate"))
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        review = {"decision": "accept", "issues": [], "resolved_findings": [],
                  "questions": [], "review_gaps": [],
                  "checks": [{"id": "iso." + area, "view": "iso", "area": area,
                              "category": "geometry", "status": "match", "item": area,
                              "evidence": "e", "reference_evidence": "e",
                              "model_evidence": "e"}
                             for area in agentloop.REVIEW_VIEW_AREAS["iso"]]}
        gate = agent_runtime._finish_gate({
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": {"state": "passed"}},
            "preview_geometry_fingerprint": fingerprint,
            "has_reference_images": True,
            "reference_reading": _body_reading(),
            "reviewed_view_status": {"iso": "passed"},
            "active_reference_images": ["ref.png"],
            "reference_images_reviewed": ["ref.png"],
            "latest_geometry_review": {"geometry_fingerprint": fingerprint,
                                       "views": ["iso"], "result": review},
        })
        self.assertTrue(gate["ready"], gate.get("blockers"))
        self.assertTrue(all("正交视图" not in str(row)
                            for row in gate.get("blockers") or []))

    def test_mismatched_canvas_yields_advisory_not_silence(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            model = Path(directory) / "model.png"
            # A CAD screenshot: the background is a magenta GRADIENT crossed by a
            # grid. One modal border colour would classify the whole canvas as
            # foreground, so this pins the per-row background estimator.
            grid = Image.new("RGB", (300, 300))
            for y in range(300):
                row_color = (100 + y // 3, 30 + y // 6, 80 + y // 4)
                for x in range(300):
                    grid.putpixel((x, y), row_color)
            for offset in range(0, 300, 20):
                for step in range(300):
                    grid.putpixel((offset, step), (220, 160, 190))
                    grid.putpixel((step, offset), (220, 160, 190))
            grid.paste(Image.new("RGB", (140, 160), (180, 185, 190)), (70, 60))
            grid.save(reference)
            render = Image.new("RGB", (420, 380), (25, 30, 40))
            render.paste(Image.new("RGB", (120, 220), (90, 100, 120)), (150, 80))
            render.save(model)

            comparison = image_analysis.compare_same_renderer_silhouette(reference, model)

        self.assertEqual("advisory", comparison["state"])
        self.assertEqual("canvas sizes differ", comparison["reason"])
        self.assertNotIn("passed", comparison)
        self.assertIsNotNone(comparison["aspect_delta"])
        self.assertIn("neither", comparison["scope"])
        # The measured reference silhouette must be the pasted part (140x160),
        # never the 300x300 canvas.
        self.assertAlmostEqual(140 / 160, comparison["reference_aspect"], delta=0.08)

    @staticmethod
    def _reading(**overrides):
        return _reading(**overrides)

    def test_reference_reading_is_required_before_geometry(self):
        state = {"has_reference_images": True, "tool_calls": 0, "plan_revision": 0,
                 "active_reference_images": ["reference.png"],
                 "session_dir": Path(tempfile.mkdtemp())}
        refused = agent_runtime._apply_incremental(
            "cad_create_model", {"name": "Part", "first_feature": _feature()}, state)
        self.assertEqual("needs_reference_reading", refused["state"])
        self.assertTrue(any("reference_reading" in row
                            for row in refused["reference_reading_blockers"]))
        self.assertIsNone(state.get("current_plan"))

        reading = _reading_with_regions()
        inspected, _ = agent_runtime._execute_tool(
            "cad_inspect_references", {"reference_reading": reading}, state, [])
        self.assertEqual("reference_reading_ready", inspected["state"])
        accepted = agent_runtime._apply_incremental(
            "cad_create_model", {"name": "Part", "first_feature": _feature()}, state)
        self.assertNotEqual("invalid", accepted["state"])
        self.assertEqual(9, agent_runtime._reference_hole_quantity(
            state["reference_reading"]))

    def test_reference_regions_reject_conflicting_surface_states_on_same_pixels(self):
        reading = _reading_with_regions()
        center = next(row for row in reading["visible_regions"]
                      if row["id"] == "center")
        reading["visible_regions"].append({
            "image": center["image"],
            "id": "center-opening",
            "region": "central opening",
            "surface_state": "background_opening",
            "image_bbox_norm": list(center["image_bbox_norm"]),
            "evidence": "same central pixels described as an opening",
            "features": [],
        })

        state = {"has_reference_images": True,
                 "active_reference_images": ["reference.png"]}
        result = agent_runtime._inspect_references(
            {"reference_reading": reading}, state)

        self.assertEqual("invalid_reference_reading", result["state"])
        self.assertTrue(any(
            "区域框几乎重合" in blocker
            for blocker in result["reference_reading_blockers"]))
        self.assertNotIn("reference_reading", state)

    def test_reference_regions_allow_partial_projection_overlap(self):
        reading = _reading_with_regions()
        center = next(row for row in reading["visible_regions"]
                      if row["id"] == "center")
        reading["visible_regions"].append({
            "image": center["image"],
            "id": "center-mouth",
            "region": "visible front lip around recessed face",
            "surface_state": "background_opening",
            "image_bbox_norm": [0.32, 0.18, 0.52, 0.92],
            "evidence": "the lip is visible around the smaller recessed floor",
            "features": [],
        })

        blockers = agent_runtime._reference_region_blockers(
            reading, ["reference.png"])

        self.assertFalse(any("区域框几乎重合" in blocker for blocker in blockers),
                         blockers)

    def test_reference_inventory_is_generic_and_preserves_uncertainty(self):
        """Reading does not require slot-specific opening counts or axis claims."""
        reading = self._reading()
        self.assertEqual([], agent_runtime._reference_reading_blockers(reading))
        self.assertEqual({"feature_groups"}, set(reading))
        partial = self._reading()
        del partial["feature_groups"]
        self.assertTrue(any("feature_groups" in row
                            for row in agent_runtime._reference_reading_blockers(partial)))

        uncertain = self._reading(uncertainties=["不同表面之间的隐藏连通关系不清楚"])
        self.assertEqual([], agent_runtime._reference_reading_blockers(uncertain))
        self.assertEqual(9, agent_runtime._reference_hole_quantity(uncertain))
        self.assertIsNone(agent_runtime._reference_hole_quantity(_body_reading()))

    def test_hole_coverage_counts_single_holes_and_repeated_layouts(self):
        plan = {"features": [
            {"id": "h1", "kind": "hole", "operation": "cut",
             "parameters": {"pos": "point"}},
            {"id": "h2", "kind": "hole", "operation": "cut",
             "parameters": {"pos": "pcd", "count": 3}},
            {"id": "h3", "kind": "hole", "operation": "cut",
             "parameters": {"pos": "corners"}},
        ]}
        self.assertEqual(8, agent_runtime._planned_hole_quantity(plan))
        patterned = {"features": [
            {"id": "h1", "kind": "hole", "operation": "cut",
             "parameters": {"pos": "point"}},
            {"id": "pattern", "kind": "pattern", "operation": "cut",
             "parameters": {"count": 3},
             "relations": [{"type": "pattern_source", "feature_id": "h1"}]},
        ]}
        self.assertEqual(3, agent_runtime._planned_hole_quantity(patterned))

    def test_reference_reading_does_not_assume_a_slot_or_hole_axis(self):
        """Arbitrary visible topology is handled by the generic multimodal review."""
        plan = plan_state.create("Part", _feature())
        plan = plan_state.add(plan, {
            "id": "slot", "kind": "box", "operation": "cut",
            "parameters": {"length": 15, "width": 50, "height": 35, "center": [0, 0, 42.5]},
            "sources": {key: {"type": "user"} for key in
                        ("length", "width", "height", "center")}})
        plan = plan_state.add(plan, {
            "id": "h1", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 10, "through": True, "pos": "point",
                           "point": [0, 0, 0], "axis": "Y"},
            "sources": {key: {"type": "user"} for key in ("diameter", "point", "axis")}})
        self.assertEqual([], agent_runtime._reference_reading_blockers(self._reading(), plan))

    def test_a_reading_written_in_the_message_is_adopted(self):
        """A concise JSON reading in the response can accompany the first tool call."""
        state = {"has_reference_images": True}
        message = ('读图清单：\n```json\n'
                   '{"feature_groups": [{"id":"body","kind":"body","quantity":1, '
                   '"region":"whole part","arrangement":"single body", '
                   '"evidence":"one continuous body"}], '
                   '"uncertainties":["顶部圆形是凸起而非孔口"]}\n```\n开始建模。')
        self.assertTrue(agent_runtime._capture_reference_reading(state, message))
        self.assertEqual(1, len(state["reference_reading"]["feature_groups"]))
        self.assertEqual("message_text", state["reference_reading_source"])

        # A CADPlan blob in the same message must not be mistaken for a reading.
        state = {"has_reference_images": True}
        cad_plan = json.dumps({"schema_version": "1.0", "name": "p",
                               "features": [{"id": "a", "kind": "box"}]})
        self.assertFalse(agent_runtime._capture_reference_reading(state, cad_plan))
        self.assertNotIn("reference_reading", state)

    def test_builder_hole_count_is_not_a_visual_finish_truth(self):
        """The first pass may misread holes; visual finish waits for blind review."""
        plan = plan_state.create("Part", _feature())
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": {"state": "passed"}},
            "preview_geometry_fingerprint": fingerprint,
            "has_reference_images": True,
            "reference_reading": _reading(),
        }
        blockers = agent_runtime._reference_reading_blockers(
            state["reference_reading"], plan, final=True)
        self.assertTrue(any("当前模型有 0" in row for row in blockers), blockers)
        gate = agent_runtime._finish_gate(state)
        self.assertEqual("needs_visual_review", gate["state"])
        self.assertFalse(any("特征组要求 9 个" in str(row)
                             for row in gate["blockers"]))

    def test_blind_inventory_hole_count_overrides_builder_hypothesis(self):
        plan = plan_state.create("Part", _feature("user"))
        plan = plan_state.add(plan, {
            "id": "one-hole", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 8, "through": True, "pos": "point",
                           "point": [0, 0, 0], "axis": "Y"},
            "sources": {key: {"type": "user"}
                        for key in ("diameter", "point", "axis")},
        })
        regions = [{
            "id": "face-%d" % index, "region": "visible face %d" % index,
            "surface_state": "solid_face", "evidence": "bounded solid surface",
            "features": [{"kind": "hole", "quantity": 3,
                          "arrangement": "vertical row",
                          "evidence": "three distinct circular openings"}],
        } for index in range(1, 4)]
        inventory = _blind_inventory(
            "reference.png", "front", regions, hole_layout_coverage="complete")
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            model_image = Path(directory) / "candidate.png"
            cadplan.preview_plan(plan, str(reference), view_set="front")
            preview = cadplan.preview_plan(plan, str(model_image), view_set="front")
            preview["preview_image"] = str(model_image)
            review = {
                "reference_images": [{"image": "reference.png", "source_view": "front",
                                      "comparable": True, "visible_evidence": "three faces"}],
                "checks": [
                    {"id": "front." + area, "area": area, "category": "geometry",
                     "status": "match", "item": area,
                     "reference_evidence": "three visible faces",
                     "model_evidence": "three visible faces", "evidence": "paired view"}
                    for area in agentloop.REVIEW_VIEW_AREAS["front"]] + [
                    {"id": "front.feature_group.blind-01-%02d-01" % index,
                     "feature_group_id": "blind-01-%02d-01" % index,
                     "area": "feature_group", "category": "geometry", "status": "match",
                     "item": "visible hole row", "reference_evidence": "three holes",
                     "model_evidence": "same row", "evidence": "paired row"}
                    for index in range(1, 4)],
                "issues": [], "resolved_findings": [], "questions": [],
            }
            state = {
                "deadline": time.monotonic() + 30, "session_dir": Path(directory),
                "preview_calls": 0, "active_reference_images": ["reference.png"],
                "reference_image_paths": {"reference.png": str(reference)},
                "reference_reading": {"feature_groups": [{
                    "id": "builder-saw-one-hole", "kind": "hole", "quantity": 1,
                    "region": "builder hypothesis", "arrangement": "single",
                    "evidence": "wrong initial estimate"}]},
            }
            calls = []

            def multimodal(*args, **_kwargs):
                calls.append(args)
                return inventory if args[3] == "参考图独立盲读" else review

            with mock.patch.object(agentloop, "_run_json_object", side_effect=multimodal):
                result = agentloop._split_view_review(
                    plan, preview, [], {}, state, None, "multiview_check", 1,
                    "复刻", views=["front"])

        self.assertEqual("revise", result["decision"])
        count_check = next(row for row in result["checks"]
                           if row["area"] == "blind_hole_count")
        self.assertEqual("mismatch", count_check["status"])
        self.assertIn("9 个可见孔位", count_check["reference_evidence"])
        self.assertIn("1 个", count_check["model_evidence"])
        comparison_payload = calls[1][0][-1]["content"][0]["text"]
        self.assertNotIn("builder-saw-one-hole", comparison_payload)


    def test_a_view_the_reference_cannot_speak_to_does_not_block(self):
        """A single isometric reference has no front/right view to compare with.

        The reviewer correctly marks those comparable=false; treating that as an
        unresolved defect made the gate demand evidence the user never provided,
        and the model answered by re-reviewing views in a loop for 17 rounds.
        """
        plan = plan_state.create("Part", _feature("image_estimate"))
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        review = {
            "decision": "accept",
            "checks": [{"id": "iso." + area, "view": "iso", "area": area,
                        "category": "geometry", "status": "match", "item": area,
                        "evidence": "e", "reference_evidence": "e", "model_evidence": "e"}
                       for area in agentloop.REVIEW_VIEW_AREAS["iso"]],
            "issues": [], "resolved_findings": [], "questions": [], "review_gaps": [],
        }
        single_view_reports = [
            {"view": "iso", "checks": review["checks"],
             "reference_images": [{"image": "ref.png", "comparable": True,
                                   "visible_evidence": "等轴测视图可见"}]},
            {"view": "front", "checks": [], "reference_images": [
                {"image": "ref.png", "comparable": False,
                 "visible_evidence": "参考图没有正视视图"}]},
        ]
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": {"state": "passed"}},
            "preview_geometry_fingerprint": fingerprint,
            "has_reference_images": True,
            "reference_reading": _body_reading(),
            "latest_geometry_review": {"geometry_fingerprint": fingerprint,
                                       "views": ["iso"], "result": review},
            "reviewed_view_status": {
                "iso": agent_runtime._view_status(
                    {"view": "iso", "checks": review["checks"],
                     "reference_images": [{"image": "ref.png", "comparable": True,
                                           "visible_evidence": "等轴测视图可见"}]}),
                "front": agent_runtime._view_status(
                    {"view": "front", "checks": [],
                     "reference_images": [{"image": "ref.png", "comparable": False,
                                           "visible_evidence": "参考图没有正视视图"}]}),
            },
        }
        self.assertEqual({"iso": "passed", "front": "not_comparable"},
                         state["reviewed_view_status"])

        gate = agent_runtime._finish_gate(state)
        self.assertTrue(gate["ready"], gate.get("blockers"))
        self.assertEqual("reviewed", gate["state"])

    def test_repeated_identical_finish_is_bounded(self):
        plan = plan_state.create("Part", _feature("user"))
        state = {"current_plan": plan, "tool_calls": 0}
        results = [agent_runtime._execute_tool("cad_finish", {}, state, [])[0]
                   for _ in range(agent_runtime.MAX_IDENTICAL_FINISHES)]
        self.assertEqual([1, 2, 3], [row["repeat_count"] for row in results])
        self.assertIn("finish_deadlock", state)

    def test_hole_opening_clipped_by_the_part_edge_is_caught(self):
        """The 47-round failure: a bore spilling past the wall edge.

        Mesh verification used to accept it (`any(ring)`), so the program said the
        holes were fine while the reviewer kept seeing half-circle notches, and
        the model rebuilt the whole part three times to explain the difference.
        """
        def box(fid, length, width, height, center):
            return {"id": fid, "kind": "box", "operation": "add",
                    "parameters": {"length": length, "width": width, "height": height,
                                   "center": center},
                    "sources": {key: {"type": "user"} for key in
                                ("length", "width", "height", "center")}}

        def hole(fid, point):
            return {"id": fid, "kind": "hole", "operation": "cut",
                    "parameters": {"diameter": 10, "through": True, "pos": "point",
                                   "point": point, "axis": "X"},
                    "sources": {key: {"type": "user"} for key in
                                ("diameter", "point", "axis")}}

        body = plan_state.create("Part", box("body", 60, 40, 70, [0, 0, 35]))
        spilling = plan_state.add(body, hole("hole_edge", [-20, -20, 55]))
        checks = cadplan.preview_plan(spilling)["local_geometry_checks"]
        self.assertEqual("failed", checks["state"])
        row = checks["through_hole_mesh_checks"][0]
        self.assertEqual("opening_clipped", row["status"])
        self.assertIn(False, row["ring_supported"])

        centered = plan_state.add(body, hole("hole_middle", [-20, -13.5, 55]))
        self.assertEqual(
            "passed",
            cadplan.preview_plan(centered)["local_geometry_checks"]["state"])

        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(spilling)
        gate = agent_runtime._finish_gate({
            "current_plan": spilling,
            "plan_geometry_fingerprint": fingerprint,
            "preview_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": checks},
        })
        self.assertFalse(gate["ready"])
        self.assertEqual("local_geometry_failed", gate["state"])
        self.assertTrue(any("半圆缺口" in row and "hole_edge" in row
                            for row in gate["blockers"]), gate["blockers"])

    def test_full_depth_recess_cut_is_attributed_to_cut_feature(self):
        """A through-cut that erases a recessed hole face must name the cut, not blame the hole."""
        def box(fid, operation, length, width, height, center):
            return {"id": fid, "kind": "box", "operation": operation,
                    "parameters": {"length": length, "width": width, "height": height,
                                   "center": center},
                    "sources": {key: {"type": "image_estimate",
                                       "evidence": "estimated from image proportions"}
                                for key in
                                ("length", "width", "height", "center")}}

        body = plan_state.create(
            "Part", box("base", "add", 80, 30, 60, [0, 0, 30]))
        plan = plan_state.add(
            body, box("front-slot", "cut", 18, 32, 44, [0, 0, 22]))
        plan = plan_state.add(plan, {
            "id": "center-hole", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 6, "through": True, "pos": "point",
                           "point": [0, 15, 42], "axis": "Y"},
            "sources": {key: {"type": "image_estimate",
                               "evidence": "estimated from image proportions"}
                        for key in ("diameter", "point", "axis")}})
        checks = cadplan.preview_plan(plan)["local_geometry_checks"]
        self.assertEqual("failed", checks["state"])
        fingerprint = agent_runtime.cad._geometry_plan_fingerprint(plan)
        state = {
            "current_plan": plan,
            "plan_geometry_fingerprint": fingerprint,
            "preview_geometry_fingerprint": fingerprint,
            "latest_preview": {"local_geometry_checks": checks},
        }

        gate = agent_runtime._finish_gate(state)
        self.assertEqual("local_geometry_failed", gate["state"])
        self.assertTrue(any("front-slot" in row and "width=32 mm" in row
                            and "主体厚度 30 mm" in row for row in gate["blockers"]),
                        gate["blockers"])
        self.assertTrue(any("不是孔中心靠边" in row for row in gate["blockers"]))

        with mock.patch.object(agent_runtime.cad, "_split_view_review") as review:
            result = agent_runtime._review_model(
                {"review_type": "geometry", "views": ["iso"]}, state, [])
        review.assert_not_called()
        self.assertEqual("local_geometry_failed", result["state"])
        self.assertTrue(result["review_skipped"])
        self.assertTrue(any("front-slot" in row for row in result["blockers"]))

    def test_coincident_cut_face_is_reported_not_measured(self):
        """Exact face coincidence corrupts this kernel's boolean.

        Measured: a box/prism cut whose bottom face is exactly coplanar with the
        solid's top face removes 300/346 mm^3 where it must remove 0; moving it
        0.001 mm gives 0.00. The screw run inferred its cuts from those volumes
        for eighteen rounds, so the coincidence is now reported outright.
        """
        solid = plan_state.create("Part", _feature())

        def fresh():
            state = {"current_plan": solid, "plan_revision": 1, "tool_calls": 0,
                     "preview_calls": 0, "session_dir": Path(tempfile.mkdtemp()),
                     "plan_geometry_fingerprint":
                         agent_runtime.cad._geometry_plan_fingerprint(solid)}
            agent_runtime._preview_plan({"view_set": "iso"}, state)
            return state

        def cut(center_z):
            return {"id": "c", "kind": "box", "operation": "cut",
                    "parameters": {"length": 5, "width": 5, "height": 5,
                                   "center": [0, 0, center_z]},
                    "sources": {key: {"type": "derived", "evidence": "e"}
                                for key in ("length", "width", "height", "center")}}

        solid_bbox = (agent_runtime.cad.cadplan.preview_plan(solid)
                      .get("dimensions") or {}).get("bbox")
        top = solid_bbox[5]
        # Bottom face exactly on the solid's top face.
        result = agent_runtime._apply_incremental(
            "cad_add_feature", {"feature": cut(top + 2.5)}, fresh())
        warning = str((result.get("feature_effect") or {}).get("warning"))
        self.assertIn("完全重合", warning)
        self.assertIn("0.001", warning)

        # 0.001 mm past it: no coincidence warning.
        result = agent_runtime._apply_incremental(
            "cad_add_feature", {"feature": cut(top + 2.501)}, fresh())
        self.assertNotIn("完全重合",
                         str((result.get("feature_effect") or {}).get("warning")))

    def test_relations_edit_is_not_treated_as_cosmetic_churn(self):
        """The screw run: fields={'relations': ...} answered "unchanged" forever.

        A relation moves the geometry fingerprint without touching a numeric
        parameter, so the anti-churn guard read it as a 0.000 mm micro-nudge and
        rejected it. The model retried six times, then deleted and rebuilt the
        feature.
        """
        base = _feature()
        base["parameters"] = {"length": 100, "width": 50, "height": 20,
                              "center": [0, 0, 10]}
        base["sources"] = {key: {"type": "user", "evidence": "test fixture"}
                           for key in base["parameters"]}
        plan = plan_state.create("Part", base)
        hole_parameters = {"diameter": 8, "through": True, "pos": "point",
                           "point": [0, -25, 5], "axis": "Y"}
        plan = plan_state.add(plan, {
            "id": "hole", "kind": "hole", "operation": "cut",
            "parameters": hole_parameters,
            "sources": {key: {"type": "user", "evidence": "test fixture"}
                        for key in hole_parameters},
        })
        plan = plan_state.add(plan, {
            "id": "pat", "kind": "pattern", "operation": "cut",
            "parameters": {"mode": "linear", "count": 3, "step": [0, 0, 5], "axis": "Z"},
            "relations": [{"feature_id": "hole", "type": "depends_on"}],
            "sources": {key: {"type": "derived", "evidence": "e"}
                        for key in ("mode", "count", "step", "axis")}})
        state = {"current_plan": plan, "plan_revision": 1, "tool_calls": 0,
                 "preview_calls": 0, "session_dir": Path(tempfile.mkdtemp()),
                 "plan_geometry_fingerprint":
                     agent_runtime.cad._geometry_plan_fingerprint(plan)}
        result = agent_runtime._apply_incremental("cad_update_feature", {
            "feature_id": "pat",
            "fields": {"relations": [{"feature_id": "hole",
                                      "type": "pattern_source"}]}}, state)
        self.assertNotEqual("unchanged", result["state"])
        stored = next(f for f in state["current_plan"]["features"] if f["id"] == "pat")
        self.assertEqual("pattern_source", stored["relations"][0]["type"])

    def test_repeated_deterministic_mutation_error_stops_and_keeps_candidate(self):
        base = plan_state.create("Part", _feature())
        calls = []

        def invalid_pattern(step):
            return {"feature": {
                "id": "bad-pattern", "kind": "pattern", "operation": "cut",
                "parameters": {"mode": "linear", "count": 3, "step": step},
                "relations": [{"relation_type": "pattern_source", "target": "base"}],
                "sources": {key: {"type": "derived", "evidence": "test"}
                            for key in ("mode", "count", "step")},
            }}

        for first_step, second_step in (([0, 0, 10], [0, 0, 10]),
                                        ([0, 0, 10], [0, 0, 11])):
            with self.subTest(second_step=second_step), tempfile.TemporaryDirectory() as directory:
                steps = [invalid_pattern(first_step), invalid_pattern(second_step)]
                requests = []

                def completion(*_args, **_kwargs):
                    requests.append(True)
                    return {"content": "尝试添加阵列", "tool_calls": [
                        _call("cad_add_feature", steps.pop(0), len(requests))]}

                with mock.patch.object(agent_runtime.llm, "chat_completion",
                                       side_effect=completion), mock.patch.object(
                        agent_runtime, "_execute_tool",
                        wraps=agent_runtime._execute_tool) as execute_tool:
                    result = agentloop.run_agent_loop(
                        "在现有模型中添加一个竖直阵列", current_plan=base,
                        cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                             "online": {"enabled": False}}, output_dir=directory)

                self.assertEqual(2, len(requests))
                self.assertEqual(1 if first_step == second_step else 2,
                                 execute_tool.call_count)
                self.assertEqual(2, len(result["tool_trace"]))
                first_result = result["tool_trace"][0]["result"]
                repeated_result = result["tool_trace"][1]["result"]
                self.assertEqual("invalid", first_result["state"])
                self.assertIn("retry_guidance", first_result)
                self.assertEqual("repeated_failed_action", repeated_result["state"])
                self.assertEqual("invalid_relation", repeated_result[
                    "validation"]["errors"][0]["code"])
                self.assertIn("停止重复提交", result["reply"])
                self.assertEqual(base, result["plan"])

    def test_each_feature_reports_what_it_did_to_the_material(self):
        """The screw run inferred its cuts from volume arithmetic for 18 rounds."""
        base = plan_state.create("Part", _feature())

        def fresh():
            state = {"current_plan": base, "plan_revision": 1, "tool_calls": 0,
                     "preview_calls": 0, "session_dir": Path(tempfile.mkdtemp()),
                     "plan_geometry_fingerprint":
                         agent_runtime.cad._geometry_plan_fingerprint(base)}
            agent_runtime._preview_plan({"view_set": "iso"}, state)
            return state

        def cut(fid, center):
            return {"id": fid, "kind": "box", "operation": "cut",
                    "parameters": {"length": 5, "width": 5, "height": 5,
                                   "center": center},
                    "sources": {key: {"type": "derived", "evidence": "e"}
                                for key in ("length", "width", "height", "center")}}

        air = agent_runtime._apply_incremental(
            "cad_add_feature", {"feature": cut("in_air", [0, 0, 500])}, fresh())
        effect = air.get("feature_effect") or {}
        self.assertEqual(0.0, effect.get("volume_delta_mm3"))
        self.assertIn("没有改变材料", str(effect.get("warning")))

        real = agent_runtime._apply_incremental(
            "cad_add_feature", {"feature": cut("real", [0, 0, 0])}, fresh())
        self.assertLess((real.get("feature_effect") or {}).get("volume_delta_mm3", 0), 0)


if __name__ == "__main__":
    unittest.main()
