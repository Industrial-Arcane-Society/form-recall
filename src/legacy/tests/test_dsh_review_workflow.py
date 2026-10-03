"""Regression tests for the DSH construction/review loop contract."""
import base64
import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_modeler.agent import dsh_tool_domain as server
from ai_modeler.agent import dsh_agent
from ai_modeler.agent import plan_state
from ai_modeler.sdk import cadplan


def _box_plan(with_hole=False, through=True):
    features = [{
        "id": "base", "kind": "box", "operation": "add",
        "parameters": {"length": 40, "width": 30, "height": 50,
                       "center": [0, 0, 0]},
        "sources": {key: {"type": "user"} for key in
                    ("length", "width", "height", "center")},
    }]
    if with_hole:
        features.append({
            "id": "hole-1", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 6, "through": through,
                           "pos": "point", "point": [0, 15, 0], "axis": "Y"},
            "sources": {key: {"type": "user"} for key in
                        ("diameter", "point", "axis")},
        })
    return {
        "schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
        "name": "loop test", "units": {"length": "mm", "angle": "deg"},
        "coordinate_system": {"type": "global_cartesian", "handedness": "right",
                              "axes": ["X", "Y", "Z"], "origin": [0, 0, 0]},
        "features": features,
    }


class DSHReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.png"
        self.reference.write_bytes(b"test-reference-image-bytes")
        digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        self.reference_record = {
            "id": "sha256:" + digest, "sha256": digest,
            "filename": self.reference.name, "path": str(self.reference),
            "width": 100, "height": 100, "media_type": "image/png",
        }
        full_image_roi_key = server._inventory_roi_cache_key(digest, None)
        self.valid_inventory = {
            "reference_id": self.reference_record["id"],
            "source_sha256": digest,
            "roi_norm": None,
            "roi_cache_key": full_image_roi_key,
            "observation_valid": True,
            "observation_status": "observed",
            "verification_status": "model_observation_unverified",
            "summary": "visible surface facts",
            "visible_evidence": [{
                "evidence_id": "%s:roi-%s:surface-a" % (
                    self.reference_record["id"], full_image_roi_key[:10]),
                "description": "正面可见一个独立退入面",
            }],
            "uncertain": [], "inferred": [],
        }
        self.patches = [
            mock.patch.object(server, "_STATE_DIR", self.root / "mcp"),
            mock.patch.object(server, "_STATE_PATH", self.root / "mcp" / "cad-state.json"),
            mock.patch.object(server, "_PREVIEW_PATH", self.root / "mcp" / "current-preview.png"),
            mock.patch.object(server, "_REFERENCE_IMAGE_RECORDS", [self.reference_record]),
            mock.patch.object(server, "_REFERENCE_IMAGES", [str(self.reference)]),
            mock.patch.object(server, "_HAS_REFERENCES", True),
            mock.patch.object(server, "_REFERENCE_MANIFEST_ERROR", None),
            mock.patch.dict(os.environ, {
                "AI_MODELER_REFERENCE_MANIFEST": "",
                "AI_MODELER_USER_TURNS_MANIFEST": "",
                "AI_MODELER_TRANSPORT_IMAGE_MAP": str(self.root / "transport-image-map.json"),
            }),
            mock.patch.object(server, "_USER_TURNS", []),
            mock.patch.object(server, "_USER_TURNS_MANIFEST_ERROR", None),
        ]
        for patcher in self.patches:
            patcher.start()
        initial_state = server._load()
        initial_state["reference_inventory"] = {
            self.reference_record["id"]: copy.deepcopy(self.valid_inventory)}
        server._save(initial_state)

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def _write_state(self, plan=None, *, revision=1, reviews=None, findings=None,
                     geometry=None):
        state = server._load()
        state.update({
            "plan": plan or _box_plan(),
            "revision": revision,
            "last_preview": {"local_geometry_checks": geometry or {
                "state": "passed", "brep_valid": True,
                "hole_measurement_complete": True, "hole_instances": [],
                "feature_results": [{"feature_id": "base", "state": "applied"}],
            }, "dimensions": {"bbox_size_mm": [40, 30, 50]}},
            "preview_revision": revision,
            "reference_similarity_history": reviews or [],
            "reference_inventory": {
                self.reference_record["id"]: copy.deepcopy(self.valid_inventory)},
            "finding_ledger": findings or {},
            "has_references": True,
        })
        server._save(state)
        return state

    def _review(self, *, status="match", recorded_at=1, view="iso",
                comparable=True, score=10, evidence_id=None):
        evidence_id = evidence_id or self.valid_inventory["visible_evidence"][0]["evidence_id"]
        return {
            "revision": 1, "reference_id": self.reference_record["id"],
            "reference_image_index": 1, "view": view,
            "view_comparable": comparable, "recorded_at": recorded_at,
            "score": score, "coverage_status": "verified",
            "covered_evidence_ids": ([evidence_id] if comparable and status in {
                "match", "approximate_match", "approximate match"} else []),
            "evidence": [{
                "evidence_id": evidence_id, "item": "body outline",
                "reference": "visible rectangular silhouette",
                "model": "visible rectangular silhouette",
                "status": status, "mismatch_severity": "none",
                "affected_feature_ids": ["base"],
            }] if comparable else [{
                "evidence_id": evidence_id, "item": "body outline",
                "reference": "projection cannot compare",
                "model": "projection cannot compare",
                "status": "mismatch", "mismatch_severity": "major",
                "affected_feature_ids": ["base"],
            }],
        }

    def _review_with_rows(self, rows, *, view="iso", recorded_at=1,
                          comparable=True, score=10):
        review = self._review(view=view, recorded_at=recorded_at,
                              comparable=comparable, score=score,
                              evidence_id=rows[0]["evidence_id"] if rows else "empty")
        review["evidence"] = copy.deepcopy(rows)
        review["covered_evidence_ids"] = [
            row["evidence_id"] for row in rows
            if comparable and row.get("status") in {"match", "approximate_match"}]
        return review

    def test_partial_failed_preview_is_visible_but_never_promoted_to_valid(self):
        state = self._write_state(_box_plan(with_hole=True))
        state.update({"last_preview": None, "preview_revision": None,
                      "best_candidate": None, "best_evidence_candidate": None})
        partial_image = self.root / "mcp" / "previews" / "partial.png"
        partial_image.parent.mkdir(parents=True)
        partial_image.write_bytes(b"png-bytes")
        server._save(state)
        report = {
            "state": "needs_review", "accepted": False,
            "feature_results": [{"feature_id": "base", "state": "applied", "applied": True},
                                {"feature_id": "hole-1", "state": "failed", "applied": False}],
            "failed_feature_id": "hole-1", "failure_code": "duplicate_through_axis",
            "related_feature_ids": ["hole-1", "hole-2"],
        }
        with mock.patch.object(server.cadplan, "preview_plan", return_value={
                "state": "preview_failed", "partial_preview": True,
                "partial_state": "candidate_partial_not_accepted",
                "partial_image": str(partial_image), "partial_geometry_checks": report,
                "failed_feature_id": "hole-1", "failure_code": "duplicate_through_axis",
                "related_feature_ids": ["hole-1", "hole-2"], "error": "duplicate line"}):
            result = server._handle_tool("cad_preview_model", {"view": "front"})
        saved = server._load()
        self.assertEqual(saved["partial_preview"]["state"], "candidate_partial_not_accepted")
        self.assertEqual(saved["partial_preview"]["applied_feature_ids"], ["base"])
        self.assertEqual(saved["partial_preview"]["not_applied_feature_ids"], ["hole-1"])
        self.assertIsNone(saved["last_preview"])
        self.assertIsNone(saved["best_candidate"])
        self.assertEqual(saved["preview_generation"], 1)
        self.assertFalse(any(block.get("type") == "image" for block in result["content"]))

    def test_preview_reuses_same_geometry_signature_view_and_render_options(self):
        state = self._write_state(_box_plan())
        server._save(state)
        def render(plan, image_path, view_set):
            Path(image_path).parent.mkdir(parents=True, exist_ok=True)
            Path(image_path).write_bytes(b"preview pixels")
            return {"state": "previewed", "preview_image": str(image_path),
                    "dimensions": {}, "local_geometry_checks": {"state": "passed"}}
        with mock.patch.object(server.cadplan, "preview_plan", side_effect=render) as preview:
            first = server._handle_tool("cad_preview_model", {"view": "front"})
            second = server._handle_tool("cad_preview_model", {"view": "front"})
        self.assertEqual(1, preview.call_count)
        self.assertEqual(1, server._load()["preview_generation"])
        self.assertIn('"cached":true', second["content"][0]["text"])

    def test_geometry_issue_stays_open_across_revisions_until_verified_preview(self):
        state = self._write_state(_box_plan(with_hole=True))
        failure_result = {"failed_feature_id": "hole-1", "failure_code": "cut_no_material_change",
                          "partial_geometry_checks": {"failure_code": "cut_no_material_change"}}
        first = server._geometry_failure_record(state, revision=2, generation=1,
                                                view="front", result=failure_result)
        changed = _box_plan(with_hole=True)
        changed["features"][0]["parameters"]["length"] = 41
        state["plan"] = changed
        second = server._geometry_failure_record(state, revision=5, generation=2,
                                                 view="front", result=failure_result)
        self.assertEqual(first["failure_id"], second["failure_id"])
        self.assertEqual(2, state["geometry_issues"][first["failure_id"]]["repeat_count"])
        self.assertEqual("open", state["geometry_issues"][first["failure_id"]]["status"])
        server._geometry_failure_record(state, revision=9, generation=3,
                                        view="front", result=failure_result)
        with self.assertRaisesRegex(ValueError, "blocked_needs_new_evidence"):
            server._guard_repeated_failed_feature(state, ["hole-1"])
        self.assertEqual("blocked_needs_new_evidence", state["stop_reason"])

    def test_partial_candidate_can_be_restored_without_best_candidate_promotion(self):
        state = self._write_state(_box_plan(with_hole=True))
        state.update({"last_preview": None, "preview_revision": None,
                      "best_candidate": None, "best_evidence_candidate": None})
        partial_image = self.root / "mcp" / "previews" / "partial-recover.png"
        partial_image.parent.mkdir(parents=True, exist_ok=True)
        partial_image.write_bytes(b"partial image")
        report = {"brep_valid": True, "feature_results": [
            {"feature_id": "base", "state": "applied", "applied": True},
            {"feature_id": "hole-1", "state": "failed", "applied": False}],
            "failed_feature_id": "hole-1", "failure_code": "cut_no_material_change"}
        with mock.patch.object(server.cadplan, "preview_plan", return_value={
                "state": "preview_failed", "partial_preview": True,
                "partial_image": str(partial_image), "partial_geometry_checks": report,
                "failed_feature_id": "hole-1", "failure_code": "cut_no_material_change"}):
            server._handle_tool("cad_preview_model", {"view": "front"})
        saved = server._load()
        self.assertEqual("partial_candidate", saved["partial_candidate"]["state"])
        self.assertFalse(saved["partial_candidate"]["accepted"])
        self.assertEqual(["base"], saved["partial_candidate"]["applied_feature_ids"])
        self.assertEqual(["hole-1"], saved["partial_candidate"]["not_applied_feature_ids"])
        self.assertIsNone(saved["best_candidate"])
        result = server._handle_tool("cad_restore_partial_candidate", {})
        self.assertEqual(["base"], [row["id"] for row in server._load()["plan"]["features"]])
        self.assertIsNone(server._load()["best_candidate"])

    def test_preview_failure_localizes_and_saves_longest_verified_prefix_without_accepting_it(self):
        state = self._write_state(_box_plan(with_hole=True))
        state.update({"last_preview": None, "preview_revision": None,
                      "partial_candidate": None, "best_candidate": None})
        server._save(state)
        prefix_checks = {"state": "passed", "brep_valid": True,
                         "feature_results": [{"feature_id": "base", "state": "applied"}]}
        with mock.patch.object(server.cadplan, "preview_plan", return_value={
                "state": "preview_failed", "error": ""}), \
                mock.patch.object(server.cadplan, "_preview_internal", return_value=(
                    {"state": "previewed", "local_geometry_checks": prefix_checks},
                    object(), None, None, [object()])) as prefix_build:
            result = server._handle_tool("cad_preview_model", {"view": "iso"})
        saved = server._load()
        candidate = saved["partial_candidate"]
        self.assertEqual(1, prefix_build.call_count)
        self.assertEqual("partial_candidate", candidate["state"])
        self.assertFalse(candidate["accepted"])
        self.assertEqual(["base"], [row["id"] for row in candidate["plan"]["features"]])
        self.assertEqual("hole-1", candidate["failed_feature_id"])
        self.assertEqual(server._signature_key(candidate["plan"]), candidate["geometry_signature"])
        self.assertEqual(server._signature_key(saved["plan"]), candidate["requested_geometry_signature"])
        self.assertEqual("first_failure_after_valid_prefix", candidate["failure_localization"])
        self.assertTrue(candidate["repair_hint"])
        self.assertEqual("", saved["last_preview_failure"]["reported_error"])
        self.assertIsNone(saved["last_preview"])
        self.assertFalse(json.loads(result["content"][0]["text"].splitlines()[-1])
                         ["preview"]["accepted"])

    def test_preview_failure_without_valid_prefix_does_not_fabricate_candidate_or_error(self):
        state = self._write_state(_box_plan(with_hole=True))
        state.update({"last_preview": None, "preview_revision": None,
                      "partial_candidate": None, "best_candidate": None})
        server._save(state)
        with mock.patch.object(server.cadplan, "preview_plan", return_value={
                "state": "preview_failed"}), \
                mock.patch.object(server.cadplan, "_preview_internal", return_value=(
                    {"state": "preview_failed"}, None, None, None, None)):
            result = server._handle_tool("cad_preview_model", {"view": "iso"})
        saved = server._load()
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertIsNone(saved["partial_candidate"])
        self.assertIsNone(saved["partial_preview"]["plan"])
        self.assertFalse(saved["partial_preview"]["accepted"])
        self.assertEqual("", saved["last_preview_failure"]["reported_error"])
        self.assertEqual("unknown", saved["last_preview_failure"]["failure_localization"])
        self.assertIn("没有可验证的有效特征前缀", result["content"][0]["text"])
        self.assertIsNone(payload["preview"]["plan"])

    def test_cannot_remove_the_only_additive_base_feature(self):
        state = self._write_state(_box_plan(with_hole=True))
        with self.assertRaisesRegex(ValueError, "唯一的 operation=add 基础实体"):
            server._handle_tool("cad_remove_feature", {"feature_id": "base"})
        saved = server._load()
        self.assertEqual(1, saved["revision"])
        self.assertEqual(["base", "hole-1"],
                         [row["id"] for row in saved["plan"]["features"]])

    def test_duplicate_feature_geometry_is_noop_but_same_diameter_at_new_position_adds(self):
        self._write_state(_box_plan(with_hole=True))
        original_hole = copy.deepcopy(_box_plan(with_hole=True)["features"][1])
        duplicate = copy.deepcopy(original_hole)
        duplicate["id"] = "hole-duplicate-id"
        duplicate["sources"] = {key: {"type": "derived", "evidence": "same geometry"}
                                for key in duplicate["sources"]}
        duplicate_result = server._handle_tool("cad_add_feature", {"feature": duplicate})
        self.assertIn("未重复添加", duplicate_result["content"][0]["text"])
        self.assertEqual(1, server._load()["revision"])
        self.assertEqual(2, len(server._load()["plan"]["features"]))

        other_position = copy.deepcopy(original_hole)
        other_position["id"] = "hole-same-diameter-other-position"
        other_position["parameters"]["point"] = [10, 15, 0]
        other_position["sources"] = {key: {"type": "derived", "evidence": "test position"}
                                      for key in other_position["sources"]}
        added = server._handle_tool("cad_add_feature", {"feature": other_position})
        self.assertFalse(added["isError"])
        saved = server._load()
        self.assertEqual(2, saved["revision"])
        self.assertEqual(3, len(saved["plan"]["features"]))
        self.assertEqual([0, 15, 0], saved["plan"]["features"][1]["parameters"]["point"])
        self.assertEqual([10, 15, 0], saved["plan"]["features"][2]["parameters"]["point"])

    def test_successful_preview_closes_issue_only_with_applied_feature_evidence(self):
        state = self._write_state(_box_plan(with_hole=True))
        state["geometry_issues"] = {"geometry:cut_no_material_change:hole-1": {
            "failure_id": "geometry:cut_no_material_change:hole-1", "status": "open",
            "failed_feature_id": "hole-1", "repeat_count": 3}}
        state["last_preview_failure"] = {"failure_id": "geometry:cut_no_material_change:hole-1"}
        server._save(state)
        image = self.root / "mcp" / "previews" / "verified.png"
        def render(plan, image_path, view_set):
            Path(image_path).parent.mkdir(parents=True, exist_ok=True)
            Path(image_path).write_bytes(b"preview")
            return {"state": "previewed", "preview_image": str(image_path),
                    "local_geometry_checks": {"state": "passed", "feature_results": [
                        {"feature_id": "hole-1", "state": "applied"}]}}
        with mock.patch.object(server.cadplan, "preview_plan", side_effect=render):
            server._handle_tool("cad_preview_model", {"view": "front"})
        issue = server._load()["geometry_issues"]["geometry:cut_no_material_change:hole-1"]
        self.assertEqual("closed", issue["status"])
        self.assertTrue(issue["resolution_evidence"]["preview_generation"] > 0)

    def test_reference_image_inventory_is_required_before_image_dependent_modeling(self):
        state = server._load()
        state["reference_inventory"] = {}
        server._save(state)
        with self.assertRaisesRegex(ValueError, "cad_inspect_references"):
            server._handle_tool("cad_create_model", {"plan": _box_plan()})

    def test_reference_inventory_gate_rejects_wrong_sha(self):
        state = server._load()
        stale = copy.deepcopy(self.valid_inventory)
        stale["source_sha256"] = "stale-sha256"
        state["reference_inventory"] = {self.reference_record["id"]: stale}
        server._save(state)
        with self.assertRaisesRegex(ValueError, "原图 SHA"):
            server._handle_tool("cad_create_model", {
                "name": "must-be-blocked", "first_feature": _box_plan()["features"][0]})

    def test_review_pair_cache_fingerprint_includes_every_acceptance_input(self):
        base = {"reference_id": "sha256:ref", "source_sha256": "refsha",
                "geometry_signature": "geom", "view": "front",
                "preview_sha256": "pixels", "user_target_fingerprint": "target",
                "scope": "base shell", "render_options": {}}
        original = server._review_pair_fingerprint(**base)
        self.assertEqual(original, server._review_pair_fingerprint(**base))
        for key, value in (("source_sha256", "new-ref"), ("geometry_signature", "new-geom"),
                           ("view", "top"), ("preview_sha256", "new-pixels"),
                           ("user_target_fingerprint", "new-target"), ("scope", "holes")):
            changed = dict(base, **{key: value})
            self.assertNotEqual(original, server._review_pair_fingerprint(**changed), key)

    def test_update_feature_preserves_untouched_parameter_sources(self):
        plan = _box_plan()
        base = plan["features"][0]
        base["sources"] = {
            "length": {"type": "user", "evidence": "用户原话 100 毫米"},
            "width": {"type": "image_estimate", "evidence": "原图估计宽度"},
            "height": {"type": "user", "evidence": "用户原话 20 毫米"},
            "center": {"type": "derived", "evidence": "由实体原点定义"},
        }
        state = self._write_state(plan)
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "t1",
            "raw_user_prompt": "请把长度改为110毫米", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        server._handle_tool("cad_update_feature", {
            "feature_id": "base", "parameter_updates": {"length": 110},
            "source_updates": {
                "length": {"type": "user", "evidence": "请把长度改为110毫米", "turn_id": "t1"},
                "width": {"type": "user", "evidence": "用户指定，保持不变"},
                "height": {"type": "user", "evidence": "用户指定，保持不变"},
                "center": {"type": "user", "evidence": "用户指定，保持不变"},
            },
        })
        updated = server._load()["plan"]["features"][0]
        self.assertEqual(updated["parameters"]["length"], 110)
        self.assertEqual(updated["sources"]["length"]["evidence"], "请把长度改为110毫米")
        self.assertEqual(updated["sources"]["width"], base["sources"]["width"])
        self.assertEqual(updated["sources"]["height"], base["sources"]["height"])
        self.assertEqual(updated["sources"]["center"], base["sources"]["center"])
        self.assertTrue(cadplan.validate_plan(server._load()["plan"])["valid"])
        self.assertNotIn("turn_id", updated["sources"]["length"])

    def test_repeated_unchanged_feature_update_is_scoped_and_recovers_after_geometry_change(self):
        self._write_state(_box_plan())
        noop_update = {"feature_id": "base", "parameter_updates": {"length": 40}}
        for _ in range(2):
            result = server._handle_tool("cad_update_feature", noop_update)
            self.assertFalse(result["isError"])
        with self.assertRaisesRegex(ValueError, "连续达到 3 次"):
            server._handle_tool("cad_update_feature", noop_update)

        saved = server._load()
        signature = server._signature_key(saved["plan"])
        breaker = saved["update_noop_breakers"][signature]["base"]
        self.assertEqual(3, breaker["count"])
        self.assertTrue(breaker["blocked"])
        self.assertEqual("blocked_repeated_unchanged_update", breaker["stop_reason"])
        with self.assertRaisesRegex(ValueError, "已熔断"):
            server._handle_tool("cad_update_feature", noop_update)

        # The update breaker is scoped to this feature; it does not lock out new features.
        hole = copy.deepcopy(_box_plan(with_hole=True)["features"][1])
        hole["sources"] = {key: {"type": "derived", "evidence": "test fixture geometry"}
                           for key in hole["sources"]}
        added = server._handle_tool("cad_add_feature", {"feature": hole})
        self.assertFalse(added["isError"])

        # A changed geometry signature leaves the old blocker inert for the feature.
        updated = server._handle_tool("cad_update_feature", {
            "feature_id": "base", "parameter_updates": {"length": 41},
            "source_updates": {"length": {"type": "derived",
                                             "evidence": "test geometry change"}}})
        self.assertFalse(updated["isError"])
        final_state = server._load()
        self.assertEqual(41, final_state["plan"]["features"][0]["parameters"]["length"])
        self.assertEqual(2, len(final_state["plan"]["features"]))

    def test_user_value_must_match_the_parameter_not_another_dimension_in_the_turn(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-dimensions",
            "raw_user_prompt": "把主体长度改为120毫米，宽度改为60毫米", "reference_ids": []}]}),
            encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)

        with self.assertRaisesRegex(ValueError, "authoritative_user_turns"):
            server._handle_tool("cad_update_feature", {
                "feature_id": "base", "parameter_updates": {"length": 60},
                "source_updates": {"length": {"type": "user", "turn_id": "turn-dimensions",
                                                "evidence": "宽度改为60毫米"}}})
        self.assertEqual(40, server._load()["plan"]["features"][0]["parameters"]["length"])

        server._handle_tool("cad_update_feature", {
            "feature_id": "base", "parameter_updates": {"length": 120},
            "source_updates": {"length": {"type": "user", "turn_id": "turn-dimensions",
                                            "evidence": "主体长度改为120毫米"}}})
        saved = server._load()["plan"]
        self.assertTrue(cadplan.validate_plan(saved)["valid"])
        self.assertNotIn("turn_id", saved["features"][0]["sources"]["length"])

    def test_create_model_consumes_transient_user_turn_ids_before_cadplan_validation(self):
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-create",
            "raw_user_prompt": "创建长度40毫米、宽度30毫米、高度50毫米的主体",
            "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = copy.deepcopy(_box_plan()["features"][0])
        feature["sources"] = {
            "length": {"type": "user", "evidence": "长度40毫米", "turn_id": "turn-create"},
            "width": {"type": "user", "evidence": "宽度30毫米", "turn_id": "turn-create"},
            "height": {"type": "user", "evidence": "高度50毫米", "turn_id": "turn-create"},
            "center": {"type": "derived", "evidence": "实体中心采用原点。"},
        }
        result = server._handle_tool("cad_create_model", {
            "name": "source-contract-create", "first_feature": feature})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertTrue(cadplan.validate_plan(saved)["valid"])
        self.assertTrue(all("turn_id" not in source for source in
                            saved["features"][0]["sources"].values()))

    def test_add_feature_consumes_transient_user_turn_id_before_cadplan_validation(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-add-hole",
            "raw_user_prompt": "增加一个直径6毫米的贯穿孔", "reference_ids": []}]}),
            encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = {
            "id": "hole-new", "kind": "hole", "operation": "cut",
            "parameters": {"diameter": 6, "through": True, "pos": "point",
                           "point": [0, 15, 0], "axis": "Y"},
            "sources": {
                "diameter": {"type": "user", "evidence": "直径6毫米", "turn_id": "turn-add-hole"},
                "through": {"type": "derived", "evidence": "按用户贯穿孔意图。"},
                "pos": {"type": "derived", "evidence": "以全局坐标定位。"},
                "point": {"type": "derived", "evidence": "在主实体正面中心。"},
                "axis": {"type": "derived", "evidence": "沿正面法线贯穿。"},
            },
        }
        result = server._handle_tool("cad_add_feature", {"feature": feature})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertTrue(cadplan.validate_plan(saved)["valid"])
        self.assertNotIn("turn_id", saved["features"][1]["sources"]["diameter"])

    def test_replace_model_consumes_transient_user_turn_id_before_cadplan_validation(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-replace-source",
            "raw_user_prompt": "请从头重建这个模型，并将长度从40毫米改为55毫米", "reference_ids": []}]}),
            encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = copy.deepcopy(_box_plan()["features"][0])
        feature["parameters"]["length"] = 55
        feature["sources"]["length"] = {
            "type": "user", "evidence": "长度从40毫米改为55毫米",
            "turn_id": "turn-replace-source"}
        result = server._handle_tool("cad_create_model", {
            "name": "source-contract-replacement", "first_feature": feature,
            "replace": True})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertTrue(cadplan.validate_plan(saved)["valid"])
        self.assertEqual(55, saved["features"][0]["parameters"]["length"])
        self.assertNotIn("turn_id", saved["features"][0]["sources"]["length"])

    def test_user_diameter_converted_to_radius_keeps_derived_provenance(self):
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-cylinder",
            "raw_user_prompt": "创建直径20毫米、高度30毫米的圆柱", "reference_ids": []}]}),
            encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = {
            "id": "cylinder", "kind": "cylinder", "operation": "add",
            "parameters": {"radius": 10, "height": 30, "center": [0, 0, 0],
                           "axis": "Z", "base": True},
            "sources": {
                "radius": {"type": "derived", "evidence": "由用户直径20毫米换算为半径10毫米。"},
                "height": {"type": "user", "evidence": "高度30毫米", "turn_id": "turn-cylinder"},
                "center": {"type": "derived", "evidence": "圆柱底面中心取原点。"},
                "axis": {"type": "derived", "evidence": "沿全局Z轴。"},
                "base": {"type": "derived", "evidence": "高度从底面沿正轴延伸。"},
            },
        }
        result = server._handle_tool("cad_create_model", {
            "name": "derived-radius", "first_feature": feature})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertEqual("derived", saved["features"][0]["sources"]["radius"]["type"])
        self.assertNotIn("turn_id", saved["features"][0]["sources"]["height"])

    def test_face_recess_user_dimensions_are_verified_and_turn_id_is_not_persisted(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        prompt = "在主体正面切出退台，退台宽度20毫米、退台高度10毫米、退台深度5毫米"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-recess",
            "raw_user_prompt": prompt, "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        result = server._handle_tool("cad_add_face_recess", {
            "feature_id": "recess", "face_normal_axis": "Y", "face_side": "positive",
            "face_center": [0, 0], "width": 20, "height": 10, "depth": 5,
            "source_type": "user", "turn_id": "turn-recess", "evidence": prompt})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertTrue(cadplan.validate_plan(saved)["valid"])
        recess = saved["features"][-1]
        self.assertNotIn("turn_id", json.dumps(recess["sources"], ensure_ascii=False))
        self.assertEqual("derived", recess["sources"]["width"]["type"])
        self.assertEqual("user", recess["sources"]["length"]["type"])
        self.assertEqual("user", recess["sources"]["height"]["type"])

    def test_hole_array_user_diameter_is_verified_and_turn_id_is_not_persisted(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        prompt = "在主体正面添加两个直径6毫米的贯穿孔"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-hole-array",
            "raw_user_prompt": prompt, "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        result = server._handle_tool("cad_add_hole_array", {
            "face_normal_axis": "Y", "face_side": "positive", "source_type": "user",
            "turn_id": "turn-hole-array", "evidence": prompt,
            "holes": [{"id": "hole-left", "face_center": [-8, 0], "diameter": 6},
                      {"id": "hole-right", "face_center": [8, 0], "diameter": 6}]})
        saved = server._load()["plan"]
        self.assertFalse(result["isError"], result)
        self.assertTrue(cadplan.validate_plan(saved)["valid"], cadplan.validate_plan(saved))
        for feature in saved["features"][1:]:
            self.assertEqual("user", feature["sources"]["diameter"]["type"])
            self.assertEqual("derived", feature["sources"]["point"]["type"])
            self.assertNotIn("turn_id", feature["sources"]["diameter"])

    def test_source_inventory_is_saved_as_unverified_and_keeps_model_context_out(self):
        state = self._write_state(_box_plan())
        state["reference_inventory"] = {}
        server._save(state)
        mock_response = {
            "content": json.dumps({"summary": "可见正面有成组结构", "visible_evidence": [
                {"evidence_id": "src-1", "description": "两个圆形轮廓", "image_region": "center", "confidence": 0.7}],
                "uncertain": ["遮挡区域未知"], "inferred": []}),
            "_usage": {"prompt_tokens": 800, "completion_tokens": 300},
            "_request_profile": {"task_kind": "vision_review", "thinking_effort": "low"},
        }
        with mock.patch.object(server.llm, "image_content_part", return_value={"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}), \
             mock.patch.object(server.llm, "chat_completion", return_value=mock_response) as call:
            server._handle_tool("cad_inspect_references", {"reference_image_index": 1})
        request = call.call_args.args[0]
        self.assertEqual(len(request), 2)
        self.assertEqual(len(request[1]["content"]), 2)
        self.assertNotIn("CADPlan", request[1]["content"][0]["text"])
        inventory = server._load()["reference_inventory"][self.reference_record["id"]]
        self.assertEqual(inventory["verification_status"], "model_observation_unverified")
        self.assertEqual(inventory["source_sha256"], self.reference_record["sha256"])
        self.assertEqual(inventory["usage"]["completion_tokens"], 300)
        self.assertTrue(inventory["observation_valid"])
        self.assertEqual(inventory["roi_cache_key"],
                         server._inventory_roi_cache_key(self.reference_record["sha256"], None))

    def test_successful_inspection_persists_gate_entry_and_allows_create(self):
        state = server._load()
        state.update({"plan": None, "reference_inventory": {},
                      "reference_inventory_roi": {}})
        server._save(state)
        mock_response = {
            "content": json.dumps({"summary": "可见正面有退入面", "visible_evidence": [
                {"evidence_id": "surface-a", "description": "正面有一个清晰退入面"}]}),
            "_usage": {"prompt_tokens": 500, "completion_tokens": 180},
            "_request_profile": {"task_kind": "vision_review", "thinking_effort": "low"},
        }
        with mock.patch.object(server.llm, "image_content_part",
                               return_value={"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}), \
             mock.patch.object(server.llm, "chat_completion", return_value=mock_response):
            server._handle_tool("cad_inspect_references", {"reference_image_index": 1})

        saved_inventory = server._load()["reference_inventory"][self.reference_record["id"]]
        self.assertTrue(saved_inventory["observation_valid"])
        self.assertEqual(saved_inventory["source_sha256"], self.reference_record["sha256"])
        # Global classification can remain unknown; visible facts are the gate evidence.
        saved_inventory["shape_classification"] = "unknown"
        saved_inventory["shape_match"] = "no_match"
        state = server._load()
        state["reference_inventory"][self.reference_record["id"]] = saved_inventory
        server._save(state)

        feature = copy.deepcopy(_box_plan()["features"][0])
        feature["sources"] = {key: {"type": "derived", "evidence": "test fixture"}
                               for key in feature["parameters"]}
        result = server._handle_tool("cad_create_model", {
            "name": "valid-observation-create", "first_feature": feature})
        self.assertFalse(result["isError"], result)
        self.assertEqual("valid-observation-create", server._load()["plan"]["name"])

    def test_empty_inventory_retries_twice_and_stays_blocked(self):
        state = server._load()
        state.update({"plan": None, "reference_inventory": {},
                      "reference_inventory_roi": {}})
        server._save(state)
        empty_response = {"content": json.dumps({"summary": "", "visible_evidence": [],
                                                 "uncertain": ["无法看清"]})}
        with mock.patch.object(server.llm, "image_content_part",
                               return_value={"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}), \
             mock.patch.object(server.llm, "chat_completion", return_value=empty_response) as call:
            result = server._handle_tool("cad_inspect_references", {"reference_image_index": 1})
        self.assertEqual(2, call.call_count)
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertFalse(payload["observation_valid"])
        persisted = server._load()["reference_inventory"][self.reference_record["id"]]
        self.assertFalse(persisted["observation_valid"])
        self.assertEqual("unknown", persisted["verification_status"])
        with self.assertRaisesRegex(ValueError, "非空可见观察清单"):
            server._handle_tool("cad_create_model", {
                "name": "must-be-blocked", "first_feature": _box_plan()["features"][0]})

    def test_inventory_cache_identity_includes_roi_and_diagnostics_drop_response_values(self):
        full = copy.deepcopy(self.valid_inventory)
        self.assertTrue(server._inventory_entry_matches(self.reference_record, full, None))
        self.assertFalse(server._inventory_entry_matches(
            self.reference_record, full, [0.1, 0.1, 0.5, 0.5]))
        response_text = json.dumps({"summary": "do not persist this raw phrase",
                                    "visible_evidence": [{"description": "secret-like raw field"}],
                                    "untrusted_key_with_data": "sensitive response value"})
        diagnostics = server._inventory_response_diagnostics(
            {}, response_text, json.loads(response_text), [], "observed")
        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("do not persist this raw phrase", serialized)
        self.assertNotIn("secret-like raw field", serialized)
        self.assertNotIn("sensitive response value", serialized)
        self.assertNotIn("untrusted_key_with_data", serialized)
        roi_key = server._inventory_roi_cache_key(self.reference_record["sha256"],
                                                 [0.1, 0.1, 0.5, 0.5])
        nested, error, path = server._inventory_observation(
            {"data": {"result": {"summary": "可见结构", "visible_evidence": [
                {"description": "侧面有独立槽面"}]}}}, self.reference_record, roi_key)
        self.assertIsNone(error)
        self.assertEqual(["data", "result"], path)
        self.assertEqual("侧面有独立槽面", nested["visible_evidence"][0]["description"])

    def test_source_only_correction_requires_exact_user_turn_quote(self):
        plan = _box_plan()
        base = plan["features"][0]
        base["sources"] = {key: {"type": "image_estimate", "evidence": "old image estimate"}
                            for key in ("length", "width", "height", "center")}
        state = self._write_state(plan)
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-2",
            "raw_user_prompt": "长度仍取40毫米，来源改用这个明确标注", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        server._handle_tool("cad_update_feature", {
            "feature_id": "base", "source_updates": {
                "length": {"type": "user", "turn_id": "turn-2", "evidence": "长度仍取40毫米"}},
            "provenance_corrections": [{"parameter": "length", "turn_id": "turn-2",
                                        "evidence": "长度仍取40毫米"}]})
        updated = server._load()["plan"]["features"][0]
        self.assertEqual(updated["sources"]["length"]["type"], "user")
        self.assertEqual(updated["parameters"]["length"], 40)

    def test_changed_value_rejects_unverified_user_provenance(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-3",
            "raw_user_prompt": "把长度改成110毫米", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        with self.assertRaisesRegex(ValueError, "authoritative_user_turns"):
            server._handle_tool("cad_update_feature", {
                "feature_id": "base", "parameter_updates": {"length": 110},
                "source_updates": {"length": {"type": "user", "turn_id": "turn-3",
                                                "evidence": "用户要求长度改为100"}}})
        unchanged = server._load()["plan"]["features"][0]
        self.assertEqual(unchanged["parameters"]["length"], 40)

    def test_changed_user_source_requires_verbatim_quote_from_trusted_turn(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-4",
            "raw_user_prompt": "把长度调整到120 mm。", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        with self.assertRaisesRegex(ValueError, "逐字放入 evidence"):
            server._handle_tool("cad_update_feature", {
                "feature_id": "base", "parameter_updates": {"length": 120},
                "source_updates": {"length": {"type": "user", "turn_id": "turn-4",
                                              "evidence": "用户明确要求将主体长度设为120毫米"}},
            })
        self.assertEqual(40, server._load()["plan"]["features"][0]["parameters"]["length"])
        server._handle_tool("cad_update_feature", {
            "feature_id": "base", "parameter_updates": {"length": 120},
            "source_updates": {"length": {"type": "user", "turn_id": "turn-4",
                                              "evidence": "长度调整到120 mm"}},
        })
        saved = server._load()["plan"]["features"][0]
        self.assertEqual(saved["parameters"]["length"], 120)
        self.assertEqual(saved["sources"]["length"]["type"], "user")

    def test_explicit_user_scalar_cannot_be_relabelled_as_derived(self):
        state = server._load()
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-explicit",
            "raw_user_prompt": "请创建底板：底板长100毫米、宽60毫米、厚20毫米；盲孔深6毫米；矩形截面绕Z轴360度旋转。",
            "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = copy.deepcopy(_box_plan()["features"][0])
        feature["parameters"].update({"length": 100, "width": 60, "height": 20})
        feature["sources"] = {
            "length": {"type": "derived", "evidence": "用户给定底板长100毫米"},
            "width": {"type": "derived", "evidence": "用户给定底板宽60毫米"},
            "height": {"type": "derived", "evidence": "用户给定底板厚20毫米"},
            "center": {"type": "derived", "evidence": "底板中心取原点"},
        }
        with self.assertRaisesRegex(ValueError, "authoritative_user_turns") as caught:
            server._handle_tool("cad_create_model", {
                "name": "explicit-source-contract", "first_feature": feature})
        self.assertIn("不要把明确用户数值改标为 derived/image_estimate",
                      str(caught.exception))
        self.assertIsNone(server._load().get("plan"))
        for feature in (
            {"id": "plate", "kind": "box", "operation": "add",
             "parameters": {"height": 20},
             "sources": {"height": {"type": "derived", "evidence": "用户给定厚度20"}}},
            {"id": "blind", "kind": "cylinder", "operation": "cut",
             "parameters": {"height": 6},
             "sources": {"height": {"type": "derived", "evidence": "用户给定深度6"}}},
            {"id": "revolve", "kind": "revolve", "operation": "add",
             "parameters": {"angle": 360},
             "sources": {"angle": {"type": "derived", "evidence": "用户给定360度"}}},
        ):
            with self.subTest(feature=feature["id"]):
                with self.assertRaisesRegex(ValueError, "authoritative_user_turns"):
                    server._sanitize_and_verify_user_sources({"features": [feature]})
        audit = server._sanitize_and_verify_user_sources({"features": [{
            "id": "blind", "kind": "cylinder", "operation": "cut",
            "parameters": {"height": 6},
            "sources": {"height": {"type": "user", "evidence": "盲孔深6毫米"}},
        }]}, source_turn_ids={("blind", "height"): "turn-explicit"})
        self.assertEqual("depth", audit[0]["user_parameter"])

    def test_changed_user_source_rejects_same_number_from_untrusted_turn_id(self):
        state = self._write_state(_box_plan())
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-5",
            "raw_user_prompt": "把长度改为110毫米", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        with self.assertRaisesRegex(ValueError, "authoritative_user_turns"):
            server._handle_tool("cad_update_feature", {
                "feature_id": "base", "parameter_updates": {"length": 120},
                "source_updates": {"length": {"type": "user", "turn_id": "missing-turn",
                                                  "evidence": "用户要求120mm"}},
            })

    def test_create_model_cannot_bypass_incremental_edit_and_preserves_history_on_explicit_rebuild(self):
        plan = _box_plan()
        plan["features"][0]["sources"] = {
            "length": {"type": "user", "evidence": "原turn的40毫米"},
            "width": {"type": "image_estimate", "evidence": "原图宽度估值"},
            "height": {"type": "user", "evidence": "原turn的50毫米"},
            "center": {"type": "derived", "evidence": "原点定义"},
        }
        state = self._write_state(plan)
        state["finding_ledger"] = {"f1": {"evidence_id": "f1", "status": "open"}}
        state["geometry_failure_history"] = [{"failure_id": "fail-1"}]
        server._save(state)
        manifest = self.root / "user-turns.json"
        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-edit",
            "raw_user_prompt": "把长度改成55毫米", "reference_ids": []}]}), encoding="utf-8")
        os.environ["AI_MODELER_USER_TURNS_MANIFEST"] = str(manifest)
        feature = copy.deepcopy(plan["features"][0])
        feature["parameters"]["length"] = 55
        feature["sources"]["length"] = {"type": "user", "turn_id": "turn-edit",
                                          "evidence": "长度按新的要求改为55毫米"}
        with self.assertRaisesRegex(ValueError, "不能替代现有模型的局部编辑"):
            server._handle_tool("cad_create_model", {
                "name": "replacement", "first_feature": feature, "replace": True,
            })

        manifest.write_text(json.dumps({"turns": [{"turn_id": "turn-rebuild",
            "raw_user_prompt": "请从头重建这个模型", "reference_ids": []}]}), encoding="utf-8")
        feature["parameters"]["length"] = 40
        feature["sources"]["length"] = {"type": "user", "turn_id": "turn-rebuild",
                                          "evidence": "模型原始要求40毫米"}
        server._handle_tool("cad_create_model", {
            "name": "replacement", "first_feature": feature, "replace": True,
        })
        saved = server._load()
        rebuilt = saved["plan"]["features"][0]
        self.assertEqual(rebuilt["sources"]["width"], plan["features"][0]["sources"]["width"])
        self.assertEqual(rebuilt["sources"]["center"], plan["features"][0]["sources"]["center"])
        self.assertEqual("原turn的40毫米", rebuilt["sources"]["length"]["evidence"])
        self.assertEqual(1, len(saved["replaced_plans"]))
        self.assertIn("f1", saved["finding_ledger"])
        self.assertEqual("fail-1", saved["geometry_failure_history"][0]["failure_id"])

    def test_hole_summary_accepts_active_segment_and_counterbore_statuses(self):
        plan = _box_plan(with_hole=True)
        geometry = {"hole_measurement_complete": True,
                    "active_through_hole_count": 1,
                    "hole_instances": [
                        {"feature_id": "hole-1", "through": False, "status": "verified_blind_segment",
                         "partially_superseded_by": "hole-2"},
                        {"feature_id": "hole-2", "through": False, "status": "verified_counterbore"},
                        {"feature_id": "hole-old", "through": True, "status": "superseded",
                         "superseded_by": "hole-2"},
                    ]}
        summary = server._hole_measurement_summary(plan, geometry)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["active_instance_count"], 2)
        self.assertEqual(summary["superseded_instance_count"], 1)

    def test_geometry_edits_can_continue_without_visual_review(self):
        plan = _box_plan()
        self._write_state(plan, geometry={"state": "needs_review"})
        hole = _box_plan(with_hole=True)["features"][1]
        hole["sources"] = {key: {"type": "derived", "evidence": "test fixture geometry"}
                           for key in hole["sources"]}
        result = server._handle_tool("cad_add_feature", {"feature": hole})
        self.assertFalse(result["isError"])
        state = server._load()
        self.assertEqual(["base", "hole-1"],
                         [row["id"] for row in state["plan"]["features"]])
        self.assertEqual(2, state["revision"])

    def test_agent_prompt_has_no_fixed_review_or_score_acceptance_gate(self):
        prompt_path = Path(__file__).parents[1] / "prompts" / "dsh_agent" / "system.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        self.assertEqual(dsh_agent._prompt(), prompt)
        self.assertIn("无需每添加一个特征都做视觉审查", prompt)
        self.assertIn("分数只作诊断", prompt)
        self.assertIn("finish 始终允许保存候选", prompt)
        self.assertIn("没有审查、视角不可比、证据不完整", prompt)
        for obsolete_gate in ("80分门槛", "至少80分", "每个特征都必须先复核",
                              "match 即可直接结束", "自评相似度达标即可验收"):
            self.assertNotIn(obsolete_gate, prompt)

    def test_production_agent_prompt_preserves_image_axis_and_member_ratios(self):
        prompt = dsh_agent._prompt()
        self.assertIn("先从同视向可见的主体边界和边长投影确定各轴之间的无量纲比例", prompt)
        self.assertIn("不能只因一组名义尺寸看起来顺眼", prompt)
        self.assertIn("每个推算参数分别写入所属 feature 的 `sources`", prompt)
        self.assertIn("inventory 给出的成员比值全部为 1.0", prompt)
        self.assertIn('传 refresh_reason="conflict" 重观察', prompt)
        self.assertIn("主体创建后以及关键特征完成时", prompt)
        self.assertIn("修订对应 CADPlan 参数", prompt)

    def test_production_prompt_maps_front_surface_holes_and_open_recess_edges(self):
        prompt = dsh_agent._prompt()
        self.assertIn("primary front（主正面，法向 +Y）", prompt)
        self.assertIn("每个孔组/凹槽所属的 `surface_id`", prompt)
        self.assertIn("`normal_relation_to_primary`", prompt)
        self.assertIn("组间 `surface_relations`", prompt)
        self.assertIn("依据各孔组支承面相对主正面的关系映射轴向", prompt)
        self.assertIn("属于主正面或与其平行退层面的一个或多个孔组都映射为 `axis=\"Y\"`", prompt)
        self.assertIn("只有原图证据明确显示孔组位于与主正面垂直的侧面", prompt)
        self.assertIn("`touches_part_edge` 描述工件自身的 top/bottom/left/right 边缘", prompt)
        self.assertIn("`touches_boundary` 描述图片/ROI 边界", prompt)
        self.assertIn("先按 `surface_id` 和 `surface_relations` 建立面间拓扑", prompt)
        self.assertIn("凹槽按 `touches_part_edge` 中实际接触的工件边缘确定开口范围及是否贯通", prompt)
        self.assertIn("保留它相对主正面的居中或偏置关系", prompt)
        self.assertIn("不得把位于正面中部的退台误建成角部 notch", prompt)

    def test_finish_without_current_visual_evidence_saves_unverified_candidate(self):
        self._write_state()
        with mock.patch.object(server, "_export_candidate", return_value={"aimodel": "candidate.aimodel"}):
            result = server._handle_tool("cad_finish_model", {"summary": "test candidate"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("candidate_not_verified", payload["state"])
        self.assertEqual("candidate.aimodel", payload["output_paths"]["aimodel"])
        self.assertFalse(payload["finish_summary"]["accepted"])
        self.assertIn(self.valid_inventory["visible_evidence"][0]["evidence_id"],
                      payload["visual_evidence"]["references"][self.reference_record["id"]][
                          "missing_required_evidence_ids"])

    def test_partial_iso_review_with_different_unobservable_id_does_not_cover_source_fact(self):
        required_base = "source-visible-base"
        required_inner = "source-visible-inner-holes"
        state = self._write_state()
        state["reference_inventory"][self.reference_record["id"]]["visible_evidence"] = [
            {"evidence_id": required_base, "description": "主体外形"},
            {"evidence_id": required_inner, "description": "内侧壁三孔"},
        ]
        state["reference_similarity_history"] = [self._review_with_rows([
            {"evidence_id": required_base, "item": "主体外形",
             "reference": "矩形主体", "model": "矩形主体",
             "status": "match", "mismatch_severity": "none",
             "affected_feature_ids": ["base"]},
            {"evidence_id": "ev-inner-wall-holes", "item": "内侧壁孔",
             "reference": "原图内侧壁可见三孔", "model": "预览中的内侧壁孔口不可辨",
             "status": "unobservable", "mismatch_severity": "none",
             "affected_feature_ids": ["inner-wall-hole-1", "inner-wall-hole-2",
                                      "inner-wall-hole-3"]},
        ])]
        state["reference_similarity_history"][0]["coverage_status"] = "partial"
        server._save(state)

        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertFalse(coverage["covered"])
        self.assertIn(required_inner, coverage["missing_required_evidence_ids"])
        self.assertIn("ev-inner-wall-holes", coverage["unmapped_evidence_ids"])

        with mock.patch.object(server, "_export_candidate",
                               return_value={"aimodel": "candidate.aimodel", "stl": "candidate.stl"}):
            result = server._handle_tool("cad_finish_model", {"summary": "partial visual evidence"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("candidate_not_verified", payload["state"])
        self.assertFalse(payload["finish_summary"]["accepted"])
        self.assertEqual("candidate.aimodel", payload["output_paths"]["aimodel"])

    def test_same_required_inventory_id_can_be_resolved_by_another_comparable_view(self):
        required_base = "source-visible-base"
        required_inner = "source-visible-inner-holes"
        state = self._write_state()
        state["reference_inventory"][self.reference_record["id"]]["visible_evidence"] = [
            {"evidence_id": required_base, "description": "主体外形"},
            {"evidence_id": required_inner, "description": "内侧壁三孔"},
        ]
        state["reference_similarity_history"] = [
            self._review_with_rows([
                {"evidence_id": required_base, "item": "主体外形",
                 "reference": "矩形主体", "model": "矩形主体",
                 "status": "match", "mismatch_severity": "none",
                 "affected_feature_ids": ["base"]},
                {"evidence_id": required_inner, "item": "内侧壁孔",
                 "reference": "内侧壁可见三孔", "model": "当前视角孔口不可辨",
                 "status": "unobservable", "mismatch_severity": "none",
                 "affected_feature_ids": ["inner-wall-hole-1", "inner-wall-hole-2",
                                          "inner-wall-hole-3"]},
            ], view="iso", recorded_at=1),
            self._review_with_rows([
                {"evidence_id": required_inner, "item": "内侧壁孔",
                 "reference": "内侧壁可见三孔", "model": "正面可见三孔",
                 "status": "match", "mismatch_severity": "none",
                 "affected_feature_ids": ["inner-wall-hole-1", "inner-wall-hole-2",
                                          "inner-wall-hole-3"]},
            ], view="front", recorded_at=2),
        ]
        server._save(state)

        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertTrue(coverage["covered"])
        self.assertIn(required_inner, coverage["resolved_required_evidence_ids"])
        with mock.patch.object(server, "_export_candidate", return_value={"aimodel": "candidate.aimodel"}):
            result = server._handle_tool("cad_finish_model", {"summary": "two-view coverage"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("accepted", payload["state"])

    def test_failed_front_review_keeps_unresolved_required_id_and_timeout_in_candidate_report(self):
        required_id = "source-visible-inner-holes"
        state = self._write_state()
        state["reference_inventory"][self.reference_record["id"]]["visible_evidence"] = [
            {"evidence_id": required_id, "description": "内侧壁三孔"},
        ]
        state["reference_similarity_history"] = [self._review_with_rows([
            {"evidence_id": required_id, "item": "内侧壁孔",
             "reference": "原图内侧壁可见三孔", "model": "当前预览无法辨认",
             "status": "unobservable", "mismatch_severity": "none",
             "affected_feature_ids": ["inner-wall-hole-1", "inner-wall-hole-2",
                                      "inner-wall-hole-3"]},
        ], view="iso")]
        error = "大模型非流式请求超过 150.0 秒。"
        state["reference_similarity_attempts"] = [{
            "revision": 1, "preview_generation": 2, "view": "front",
            "reference_id": self.reference_record["id"],
            "reference_image_index": 1, "failed": True,
            "failure_kind": "timeout", "error": error,
        }]
        state["reference_similarity_error"] = {
            "revision": 1, "preview_generation": 2, "view": "front",
            "reference_id": self.reference_record["id"], "error": error,
        }
        server._save(state)

        with mock.patch.object(server, "_export_candidate", return_value={"aimodel": "candidate.aimodel"}):
            result = server._handle_tool("cad_finish_model", {"summary": "front review timed out"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("candidate_not_verified", payload["state"])
        self.assertFalse(payload["finish_summary"]["accepted"])
        visual = payload["finish_summary"]["visual_evidence"]["references"][
            self.reference_record["id"]]
        self.assertIn(required_id, visual["unresolved_required_evidence_ids"])
        self.assertEqual(error, visual["failed_review_attempts"][0]["error"])
        self.assertEqual("candidate.aimodel", payload["output_paths"]["aimodel"])

    def test_optional_inventory_unknown_does_not_block_fully_mapped_required_evidence(self):
        required_id = self.valid_inventory["visible_evidence"][0]["evidence_id"]
        state = self._write_state(reviews=[self._review(evidence_id=required_id)])
        state["reference_inventory"][self.reference_record["id"]]["uncertain"] = [{
            "uncertain_id": "hidden-back", "description": "背面是否存在额外孔",
            "reason": "背面不在图像可见范围内",
        }]
        server._save(state)

        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertTrue(coverage["covered"])
        self.assertEqual("hidden-back", coverage["optional_unknowns"][0]["uncertain_id"])
        with mock.patch.object(server, "_export_candidate", return_value={"aimodel": "candidate.aimodel"}):
            result = server._handle_tool("cad_finish_model", {"summary": "optional hidden face unknown"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("accepted", payload["state"])

    def test_review_prompt_supplies_inventory_ids_and_unmapped_reply_cannot_claim_coverage(self):
        required_id = self.valid_inventory["visible_evidence"][0]["evidence_id"]
        state = self._write_state()
        preview_path = self.root / "mcp" / "previews" / "iso-1.png"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"test-preview-image")
        state["preview_generation"] = 1
        state["previews"] = [{
            "revision": 1, "preview_generation": 1, "view": "iso",
            "preview_image_path": str(preview_path),
        }]
        server._save(state)
        response = {
            "content": json.dumps({
                "view_comparable": True,
                "coverage_status": "verified",
                "covered_evidence_ids": ["agent-invented-id"],
                "evidence": [{
                    "evidence_id": "agent-invented-id", "item": "front face",
                    "reference": "visible recessed face", "model": "visible recessed face",
                    "status": "match", "mismatch_severity": "none",
                    "affected_feature_ids": ["base"],
                }],
            }),
            "_usage": {},
        }
        image_part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        with mock.patch.object(server.llm, "image_content_part", return_value=image_part), \
             mock.patch.object(server.llm, "chat_completion", return_value=response) as call, \
             mock.patch.object(server, "_capture_best_candidate"):
            review = server._reference_similarity_review(
                state, "iso", 1, reference_id=self.reference_record["id"])

        user_content = call.call_args.args[0][1]["content"]
        prompt_text = "\n".join(part.get("text", "") for part in user_content
                                 if isinstance(part, dict) and part.get("type") == "text")
        self.assertIn(required_id, prompt_text)
        self.assertIn(required_id, review["expected_evidence_ids"])
        self.assertEqual(["agent-invented-id"], review["unmapped_evidence_ids"])
        self.assertEqual(["agent-invented-id"], review["unmapped_coverage_ids"])
        self.assertEqual([], review["covered_evidence_ids"])
        self.assertFalse(review["coverage_integrity"])
        self.assertEqual("partial", review["coverage_status"])

    def test_low_score_with_grounded_matching_evidence_does_not_block_finish(self):
        self._write_state(reviews=[self._review(score=8)])
        summary = server._visual_evidence_summary(server._load())
        self.assertTrue(summary["all_references_covered"])
        with mock.patch.object(server, "_export_candidate", return_value={"aimodel": "candidate.aimodel"}):
            result = server._handle_tool("cad_finish_model", {"summary": "verified geometry"})
        payload = json.loads(result["content"][0]["text"].splitlines()[-1])
        self.assertEqual("accepted", payload["state"])
        self.assertTrue(payload["score_is_advisory"])

    def test_latest_partial_for_same_reference_view_overrides_older_match(self):
        self._write_state(reviews=[
            self._review(status="match", recorded_at=1, score=99),
            self._review(status="partial_mismatch", recorded_at=2, score=100),
        ])
        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertFalse(coverage["covered"])
        self.assertIn(self.valid_inventory["visible_evidence"][0]["evidence_id"],
                      coverage["mismatch_evidence_ids"])

    def test_target_fingerprint_ignores_queries_but_tracks_edits_and_corrections(self):
        initial = [{
            "turn_id": "build-1", "raw_user_prompt": "请根据参考图建模",
            "reference_ids": [self.reference_record["id"]],
        }]
        baseline = server._user_target_fingerprint(initial)
        self.assertEqual(
            baseline,
            server._user_target_fingerprint(initial + [{
                "turn_id": "query-1", "raw_user_prompt": "现在有几个孔？",
                "reference_ids": [],
            }]))
        self.assertFalse(server._turn_affects_model_target({
            "raw_user_prompt": "厚度是20毫米吗？"}))
        self.assertFalse(server._turn_affects_model_target({
            "raw_user_prompt": "现在模型宽100mm吗？"}))
        self.assertTrue(server._turn_affects_model_target({
            "raw_user_prompt": "厚度设为20毫米可以吗？"}))
        for prompt in ("能把中排孔增大10%吗？", "为什么没孔？", "是不是少了孔？"):
            with self.subTest(prompt=prompt):
                self.assertTrue(server._turn_affects_model_target({"raw_user_prompt": prompt}))
        self.assertNotEqual(baseline, server._user_target_fingerprint(initial + [{
            "turn_id": "edit-1", "raw_user_prompt": "中排孔径增加10%，其它不变",
            "reference_ids": [],
        }]))

    def test_valid_user_override_can_accept_target_and_records_source_difference(self):
        turn = {
            "turn_id": "user-edit-1",
            "raw_user_prompt": "中排孔径增加10%，其它不变",
            "reference_ids": [],
        }
        state = self._write_state()
        state["user_turns"] = [turn]
        state["reference_inventory"][self.reference_record["id"]]["visible_evidence"] = [{
            "evidence_id": "middle-hole-diameter",
            "description": "中排孔径比例",
        }]
        review = self._review(score=12)
        review["user_target_fingerprint"] = server._user_target_fingerprint([turn])
        review["covered_evidence_ids"] = ["middle-hole-diameter"]
        review["evidence"] = [{
            "evidence_id": "middle-hole-diameter",
            "item": "middle hole diameter",
            "reference": "source image shows smaller middle holes",
            "target": "user requests middle holes 10% larger than the source",
            "model": "middle holes are 10% larger than the source",
            "assessment_basis": "user_override",
            "target_turn_id": turn["turn_id"],
            "target_quote": "中排孔径增加10%，其它不变",
            "target_overrides_reference": True,
            "reference_status": "mismatch",
            "status": "match",
            "mismatch_severity": "none",
            "affected_feature_ids": ["base"],
        }]
        state["reference_similarity_history"] = [review]
        server._save(state)
        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertTrue(coverage["covered"])
        self.assertIn("middle-hole-diameter",
                      coverage["supported_evidence_ids"])
        self.assertIn("middle-hole-diameter",
                      coverage["overridden_reference_evidence_ids"])

    def test_fabricated_user_override_quote_stays_unverified(self):
        turn = {
            "turn_id": "user-edit-1",
            "raw_user_prompt": "中排孔径增加10%，其它不变",
            "reference_ids": [],
        }
        state = self._write_state()
        state["user_turns"] = [turn]
        state["reference_inventory"][self.reference_record["id"]]["visible_evidence"] = [{
            "evidence_id": "middle-hole-diameter",
            "description": "中排孔径比例",
        }]
        review = self._review(score=100)
        review["user_target_fingerprint"] = server._user_target_fingerprint([turn])
        review["covered_evidence_ids"] = ["middle-hole-diameter"]
        review["evidence"] = [{
            "evidence_id": "middle-hole-diameter",
            "item": "middle hole diameter",
            "reference": "source image shows smaller middle holes",
            "target": "user requests a fabricated target",
            "model": "middle holes are 10% larger than the source",
            "assessment_basis": "user_override",
            "target_turn_id": turn["turn_id"],
            "target_quote": "用户从未说过的孔径改为100mm",
            "target_overrides_reference": True,
            "reference_status": "mismatch",
            "status": "match",
            "mismatch_severity": "none",
            "affected_feature_ids": ["base"],
        }]
        state["reference_similarity_history"] = [review]
        server._save(state)
        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertFalse(coverage["covered"])
        self.assertEqual("partial", coverage["coverage_status"])
        self.assertNotIn("middle-hole-diameter", coverage["mismatch_evidence_ids"])

    def test_cross_view_mismatch_cannot_be_covered_by_another_high_score_view(self):
        self._write_state(reviews=[
            self._review(status="match", recorded_at=1, view="front", score=100),
            self._review(status="partial_mismatch", recorded_at=2,
                         view="iso", score=100),
        ])
        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertFalse(coverage["covered"])
        self.assertIn(self.valid_inventory["visible_evidence"][0]["evidence_id"],
                      coverage["mismatch_evidence_ids"])

    def test_noncomparable_pair_is_diagnostic_when_another_pair_supports_fact(self):
        self._write_state(reviews=[
            self._review(status="match", recorded_at=1, view="iso", score=3),
            self._review(status="mismatch", recorded_at=2, view="front",
                         comparable=False, score=100),
        ])
        summary = server._visual_evidence_summary(server._load())
        coverage = summary["references"][self.reference_record["id"]]
        self.assertTrue(coverage["covered"])
        self.assertEqual(1, coverage["noncomparable_pairs"])
        self.assertEqual(1, server._summary(server._load())["independent_reference_review_pairs"])

    def test_pattern_hole_instances_use_brep_statuses_not_feature_count_equality(self):
        plan = _box_plan(with_hole=True, through=True)
        geometry = {
            "state": "passed", "hole_measurement_complete": True,
            "hole_instances": [
                {"feature_id": "hole-1", "instance_index": 1,
                 "through": True, "status": "verified_open"},
                {"feature_id": "hole-1", "instance_index": 2,
                 "through": True, "status": "verified_open"},
            ],
        }
        measured = server._hole_measurement_summary(plan, geometry)
        self.assertTrue(measured["complete"])
        self.assertEqual(2, measured["instance_count"])

    def test_blind_hole_uses_verified_blind_status(self):
        plan = _box_plan(with_hole=True, through=False)
        geometry = {
            "state": "passed", "hole_measurement_complete": True,
            "hole_instances": [
                {"feature_id": "hole-1", "instance_index": 1,
                 "through": False, "status": "verified_blind"},
            ],
        }
        self.assertTrue(server._hole_measurement_summary(plan, geometry)["complete"])

    def test_same_finding_requires_changed_geometry_before_closure(self):
        plan = _box_plan()
        state = {"plan": plan, "revision": 1, "finding_ledger": {},
                 "reference_similarity_history": []}
        row = {
            "evidence_id": "front-hole-count", "item": "front hole count",
            "reference": "the source shows nine holes", "model": "the model shows eight holes",
            "status": "mismatch", "mismatch_severity": "major",
            "affected_feature_ids": ["base"],
        }
        server._record_review_evidence(
            state, [row], revision=1, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        server._save(state)
        finding_id = next(iter(state["finding_ledger"]))
        same_geometry_match = dict(row, model="the model now shows nine holes", status="match",
                                   mismatch_severity="none")
        server._record_review_evidence(
            state, [same_geometry_match], revision=1, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        self.assertEqual("awaiting_verification",
                         state["finding_ledger"][finding_id]["status"])
        self.assertTrue(state["finding_ledger"][finding_id]["history"][-1][
            "closure_rejected_same_geometry"])

        state["plan"] = copy.deepcopy(plan)
        state["plan"]["features"][0]["parameters"]["length"] = 41
        state["revision"] = 2
        server._record_review_evidence(
            state, [same_geometry_match], revision=2, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        finding = state["finding_ledger"][finding_id]
        self.assertEqual("closed", finding["status"])
        self.assertEqual(2, finding["closed_revision"])

    def test_reference_reinspection_can_correct_false_positive_without_geometry_edit(self):
        state = self._write_state()
        row = {
            "evidence_id": "front-hole-count", "item": "front hole count",
            "reference": "the source shows nine holes", "model": "the model shows eight holes",
            "status": "mismatch", "mismatch_severity": "major",
            "affected_feature_ids": ["base"],
        }
        server._record_review_evidence(
            state, [row], revision=1, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        finding_id = next(iter(state["finding_ledger"]))
        corrected = dict(row, model="the source and model both show nine holes",
                         status="match", mismatch_severity="none")
        server._record_review_evidence(
            state, [corrected], revision=1, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        server._save(state)
        self.assertEqual("awaiting_verification",
                         state["finding_ledger"][finding_id]["status"])

        result = server._handle_tool("cad_get_reference_image", {
            "reference_id": self.reference_record["id"],
            "evidence_id": "front-hole-count",
        })
        self.assertFalse(result["isError"])
        state = server._load()
        server._record_review_evidence(
            state, [corrected], revision=1, view="front",
            reference_image_index=1, reference_id=self.reference_record["id"])
        finding = state["finding_ledger"][finding_id]
        self.assertEqual("closed", finding["status"])
        self.assertEqual("reference_reinspection_correction",
                         finding["closure_basis"])

    def test_cycle_guard_requires_the_active_finding_for_parameter_return(self):
        before = _box_plan()
        after = copy.deepcopy(before)
        after["features"][0]["parameters"]["length"] = 41
        before_signature = server._signature_key(before)
        after_signature = server._signature_key(after)
        finding_id = "finding-front-holes"
        state = {
            "revision": 2,
            "geometry_signatures": [
                {"signature": before_signature, "revision": 1},
                {"signature": after_signature, "revision": 2},
            ],
            "finding_ledger": {finding_id: {
                "status": "open", "last_mismatch_revision": 2,
                "last_mismatch_geometry_signature": after_signature,
                "affected_feature_ids": ["base"],
            }},
            "geometry_mutations": [],
        }
        with self.assertRaisesRegex(ValueError, "回到历史 CADPlan 状态"):
            server._guard_geometry_transition(state, after, before, ["base"])
        server._guard_geometry_transition(state, after, before, ["base"], [finding_id])

        server._record_geometry_transition(
            state, before, after, ["base"], "cad_update_feature", [finding_id],
            {"length": {"before": 40, "after": 41, "direction": "increased"}})
        server._record_geometry_transition(
            state, after, before, ["base"], "cad_update_feature", [finding_id],
            {"length": {"before": 41, "after": 40, "direction": "decreased"}})
        mutation = state["geometry_mutations"][-1]
        self.assertEqual("increased", mutation["reversed_directions"][0]["previous_direction"])
        self.assertEqual("decreased", mutation["reversed_directions"][0]["current_direction"])

    def test_restore_best_candidate_uses_tool_and_restores_prior_plan(self):
        plan = _box_plan()
        self._write_state(plan)
        state = server._load()
        state.update({"preview_revision": 1, "preview_generation": 1,
                      "previews": [{"revision": 1, "preview_generation": 1,
                                    "preview_image_path": ""}]})
        server._capture_best_candidate(state)
        self.assertIsNotNone(state["last_valid_candidate"])
        server._save(state)

        degraded = copy.deepcopy(plan)
        degraded["features"][0]["parameters"]["length"] = 41
        state = server._load()
        state["plan"] = degraded
        state["revision"] = 2
        server._save(state)
        result = server._handle_tool("cad_restore_best_candidate", {"reason": "回退退化尝试"})
        self.assertFalse(result["isError"])
        restored = server._load()
        self.assertEqual(plan, restored["plan"])
        self.assertEqual(3, restored["revision"])

    def test_single_reference_review_selector_defaults_and_multi_reference_lists_choices(self):
        index, record = server._reference_record()
        self.assertEqual(1, index)
        self.assertEqual(self.reference_record["id"], record["id"])
        second = dict(self.reference_record, id="sha256:second", filename="second.png")
        with mock.patch.object(server, "_REFERENCE_IMAGE_RECORDS",
                               [self.reference_record, second]):
            with self.assertRaisesRegex(ValueError, "sha256:second"):
                server._reference_record()

    def test_native_tool_domain_cancellation_blocks_state_commit(self):
        state = server._load()
        state["mutation_count"] = 2
        server._save(state)
        cancel = threading.Event()
        cancel.set()
        updated = dict(state, mutation_count=99)
        with mock.patch.object(server._TOOL_EXECUTION_CONTEXT, "cancel_event", cancel, create=True):
            with self.assertRaisesRegex(RuntimeError, "cancelled before state commit"):
                server._save(updated)
        self.assertEqual(2, server._load()["mutation_count"])

    def test_main_self_report_does_not_count_as_independent_reference_review(self):
        state = self._write_state()
        state["visual_reviews"] = {"iso": {
            "revision": 1, "view": "iso", "review_type": "main_agent_self_report",
            "issues": [{"status": "match", "item": "everything"}],
        }}
        server._save(state)
        summary = server._summary(server._load())
        self.assertEqual(["iso"], summary["main_agent_self_report_views"])
        self.assertEqual(0, summary["independent_reference_review_pairs"])
        coverage = server._visual_evidence_summary(server._load())
        reference_coverage = coverage["references"][self.reference_record["id"]]
        self.assertFalse(reference_coverage["covered"])
        self.assertIn(self.valid_inventory["visible_evidence"][0]["evidence_id"],
                      reference_coverage["missing_required_evidence_ids"])

    def test_external_fact_requires_exact_opened_quote_and_independent_support(self):
        quote = "The density of this alloy is 2.70 g/cm3."
        server._SEARCH_RESULT_DETAILS["result-1"] = {
            "url": "https://example.test/material", "text": "Material data. " + quote,
            "text_sha256": "body-sha256",
        }
        server._handle_tool("cad_require_external_fact", {
            "fact_id": "density", "claim": "AA6061-T6 density is 2.70 g/cm3",
            "impact": "informational",
        })
        with self.assertRaisesRegex(ValueError, "逐字出现在已打开页面正文"):
            server._handle_tool("cad_attach_external_fact_evidence", {
                "fact_id": "density", "result_id": "result-1",
                "quote": "A different material density is 2.70 g/cm3.",
            })
        with mock.patch.object(server.llm, "chat_completion", return_value={
                "content": '{"verdict":"supported","rationale":"页面明确给出该合金密度。"}',
                "_usage": {"total_tokens": 11},
                "_request_profile": {"reasoning_effort": "low"}}):
            result = server._handle_tool("cad_attach_external_fact_evidence", {
                "fact_id": "density", "result_id": "result-1", "quote": quote,
            })
        self.assertFalse(result["isError"])
        fact = server._load()["required_external_facts"]["density"]
        self.assertEqual("verified", fact["status"])
        self.assertEqual("body-sha256", fact["evidence"]["page_text_sha256"])
        self.assertEqual("https://example.test/material", fact["evidence"]["url"])

    def test_external_fact_verifier_reads_rust_persisted_opened_page_in_new_worker(self):
        quote = "The material density is exactly 2.70 g/cm3."
        url, title = "https://example.test/data", "Material facts"
        result_id = hashlib.sha256((url + title).encode("utf-8")).hexdigest()[:16]
        text_sha = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        native_state = {"schemaVersion": 1,
                        "results": [{"result_id": result_id, "url": url, "title": title}],
                        "opened_pages": [{"result_id": result_id, "url": url, "text": quote,
                                          "text_sha256": text_sha, "opened_at_epoch_ms": 1}]}
        server._STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path = server._STATE_DIR / "native-web-state.json"
        state_path.write_text(json.dumps(native_state), encoding="utf-8")
        server._handle_tool("cad_require_external_fact", {
            "fact_id": "native-density", "claim": "Material density is 2.70 g/cm3",
            "impact": "informational"})
        with mock.patch.dict(server._SEARCH_RESULT_DETAILS, {}, clear=True), mock.patch.object(
                server.llm, "chat_completion", return_value={
                    "content": '{"verdict":"supported","rationale":"The exact density is present."}'}):
            server._handle_tool("cad_attach_external_fact_evidence", {
                "fact_id": "native-density", "result_id": result_id, "quote": quote})
        self.assertEqual(text_sha, server._load()["required_external_facts"]["native-density"]["evidence"]["page_text_sha256"])
        native_state["opened_pages"][0]["text"] = "Tampered page text"
        state_path.write_text(json.dumps(native_state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "正文 SHA"):
            server._native_web_evidence_page(result_id)

    def test_external_fact_cannot_be_verified_from_unrelated_quote(self):
        quote = "The alloy is available in several colors."
        server._SEARCH_RESULT_DETAILS["result-2"] = {
            "url": "https://example.test/unrelated", "text": quote,
            "text_sha256": "body-sha256",
        }
        server._handle_tool("cad_require_external_fact", {
            "fact_id": "density", "claim": "AA6061-T6 density is 2.70 g/cm3",
            "impact": "geometry",
        })
        with mock.patch.object(server.llm, "chat_completion", return_value={
                "content": '{"verdict":"inconclusive","rationale":"摘录没有密度信息。"}',
                "_usage": {}, "_request_profile": {}}):
            server._handle_tool("cad_attach_external_fact_evidence", {
                "fact_id": "density", "result_id": "result-2", "quote": quote,
            })
        fact = server._load()["required_external_facts"]["density"]
        self.assertEqual("unknown", fact["status"])

    def test_reference_tool_returns_text_metadata_without_image_bytes(self):
        manifest = self.root / "references.json"
        manifest.write_text(json.dumps({"schema_version": 1, "images": [self.reference_record]}),
                            encoding="utf-8")
        with mock.patch.dict(os.environ, {"AI_MODELER_REFERENCE_MANIFEST": str(manifest)}):
            server._reload_reference_manifest()
            result = server._handle_tool("cad_get_reference_image", {
                "reference_id": self.reference_record["id"],
            })
        self.assertTrue(result["content"])
        self.assertFalse(any(block.get("type") == "image" for block in result["content"]))
        self.assertIn(self.reference_record["id"], result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
