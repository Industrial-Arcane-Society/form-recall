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


class IncrementalAgentTests(unittest.TestCase):
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

    def test_text_build_saves_editable_project_and_mesh(self):
        replies = [
            {"content": "create", "tool_calls": [
                _call("cad_create_model", {"name": "Part", "first_feature": _feature()}, 1)]},
            {"content": "finish", "tool_calls": [
                _call("cad_finish", {"summary": "已生成本地模型。"}, 2)]},
        ]
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                agent_runtime.llm, "chat_completion", side_effect=lambda *_a, **_k: replies.pop(0)):
            result = agentloop.run_agent_loop(
                "建立长方体", cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                                   "online": {"enabled": False}}, output_dir=directory)
            self.assertEqual("previewed", result["state"])
            self.assertEqual("passed", result["local_geometry_checks"]["state"])
            self.assertTrue(Path(result["output_paths"]["aimodel"]).is_file())
            self.assertTrue(Path(result["output_paths"]["stl"]).is_file())

    def test_multiview_review_must_cover_both_reference_images(self):
        steps = [
            ("cad_create_model", {"name": "Part", "first_feature": _feature("image_estimate")}),
            ("cad_review_model", {"review_type": "geometry", "views": ["iso"]}),
            ("cad_finish", {}),
            ("cad_review_model", {"review_type": "geometry", "views": ["front"]}),
            ("cad_finish", {}),
        ]

        def completion(*_args, **_kwargs):
            name, arguments = steps.pop(0)
            return {"content": name, "tool_calls": [_call(name, arguments, len(steps))]}

        def review(*args, **_kwargs):
            view = args[4]["active_review_view"]
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
                    for area in agentloop.REVIEW_VIEW_AREAS[view]],
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
                    "按两张图建模", image_paths=images,
                    cfg={"llm": {"model": "mock", "base_url": "http://mock"},
                         "online": {"enabled": False}}, output_dir=directory)
            finishes = [row["result"]["state"] for row in result["tool_trace"]
                        if row["tool"] == "cad_finish"]
            self.assertEqual(["needs_visual_review", "reviewed"], finishes)

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
            with mock.patch.object(agentloop, "_run_json_object", return_value=review):
                result = agentloop._split_view_review(
                    candidate_plan, candidate_preview, [], {}, state, None,
                    "multiview_check", 1, "照图建模", views=["iso"])
            self.assertEqual("revise", result["decision"])
            self.assertTrue(any(row.get("area") == "silhouette_ratio"
                                and row.get("status") == "mismatch"
                                for row in result["checks"]))
            self.assertTrue(state["temporary_review_images"][0]["deleted"])

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
            "reviewed_view_status": {"iso": "passed"},
            "active_reference_images": ["ref.png"],
            "reference_images_reviewed": ["ref.png"],
            "latest_geometry_review": {"geometry_fingerprint": fingerprint,
                                       "views": ["iso"], "result": review},
        }

        gate = agent_runtime._finish_gate(state)
        self.assertFalse(gate["ready"])
        self.assertEqual("needs_visual_review", gate["state"])
        self.assertTrue(any("正交视图" in str(row) for row in gate["blockers"]))

        # Reviewing the same claim from an orthographic view clears this rule.
        state["latest_geometry_review"]["views"] = ["front"]
        next_gate = agent_runtime._finish_gate(state)
        self.assertFalse(any("正交视图" in str(row)
                             for row in next_gate.get("blockers") or []))

    def test_mismatched_canvas_yields_advisory_not_silence(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            model = Path(directory) / "model.png"
            grid = Image.new("RGB", (300, 300), (120, 30, 90))
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


if __name__ == "__main__":
    unittest.main()
