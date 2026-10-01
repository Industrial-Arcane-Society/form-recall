# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ai_modeler.agent import dsh_agent, dsh_tool_domain
from ai_modeler.parts.library import retrieval


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_known_model_lookup_normalizes_multiplication_sign_and_enriches_geometry(self):
        result = retrieval.search_knowledge("请帮我建一个 M5×8 内六角螺栓")

        self.assertEqual(result["status"], "exact_match")
        self.assertEqual(result["matches"][0]["model"], "M5x8")
        self.assertTrue(result["matches"][0]["exact_identifier_match"])
        self.assertFalse(result["matches"][0]["has_full_feature_tree"])
        geometry = result.get("measured_geometry")
        self.assertIsNotNone(geometry)
        self.assertEqual(geometry["measured_envelope_mm"], [13.0, 10.0, 10.0])
        self.assertEqual(geometry["trust"], "exact")
        legacy = result["legacy_builder_output"]
        self.assertEqual(legacy["feature_count"], 7)
        self.assertFalse(legacy["features_preview"][-1][
            "cadplan_kind_and_parameters_supported"])
        self.assertIn("不是 CADPlan JSON", legacy["applicability"])

    def test_known_profile_identifier_is_exact(self):
        result = retrieval.search_knowledge("EGT001 SW80×088 型材")

        self.assertEqual(result["status"], "exact_match")
        self.assertEqual(result["matches"][0]["model"], "EGT001 SW80x088")
        self.assertEqual(result["measured_geometry"]["measured_envelope_mm"],
                         [88.0, 80.0, 80.0])

    def test_category_description_returns_context_only_examples(self):
        result = retrieval.search_knowledge("做一个定位销")

        self.assertEqual(result["status"], "similar_matches")
        self.assertTrue(result["matches"])
        self.assertTrue(all(row["match_type"] == "similar_reference"
                            for row in result["matches"]))
        self.assertTrue(all(not row["exact_identifier_match"]
                            for row in result["matches"]))
        self.assertIn("仅作上下文参考", result["message"])

    def test_unknown_or_negative_part_numbers_never_fall_back_to_nearby_model(self):
        for query in ("M5x999 内六角螺钉", "内六角螺栓 M14x88",
                      "KMS-M99-XYZ-123", "BES_M99MI_XXX"):
            with self.subTest(query=query):
                result = retrieval.search_knowledge(query)
                self.assertEqual(result["status"], "no_match")
                self.assertEqual(result["matches"], [])

    def test_unrelated_shape_description_is_a_safe_no_match(self):
        for query in ("设计一个100x60x20长方体并在中心打通孔", "给我做一个咖啡杯"):
            with self.subTest(query=query):
                self.assertEqual(retrieval.search_knowledge(query)["status"], "no_match")

    def test_jsonl_extension_source_is_discovered_without_code_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "base.jsonl"
            corpus.write_text("", encoding="utf-8")
            extension_dir = root / "extensions"
            extension_dir.mkdir()
            extension = extension_dir / "custom.jsonl"
            extension.write_text(json.dumps({
                "id": "custom:1", "prompt": "精密导向套 KX-2040",
                "category": "导向套", "model": "KX-2040",
                "envelope": [20, 40, 18], "envelope_str": "40×20×18",
                "trust": "measured", "source": "custom/catalog.json",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            provider = retrieval.JsonlPromptProvider(corpus, extension_dir)

            result = provider.search("请建精密导向套 KX-2040", limit=3)

        self.assertEqual(result["matches"][0]["match_type"], "exact_model")
        self.assertEqual(result["matches"][0]["model"], "KX-2040")
        self.assertIn("custom.jsonl", result["matches"][0]["source_file"])

    def test_non_jsonl_provider_can_be_registered_and_removed(self):
        class StaticVectorProvider:
            def search(self, query, limit, *, image_paths=None):
                return {"matches": [{
                    "id": "vector:1", "prompt": query,
                    "category": "图像索引零件", "model": "IMG-01",
                    "relevance": 0.91, "match_type": "similar_reference",
                    "exact_identifier_match": False,
                    "has_full_feature_tree": False,
                    "reusable_as_exact_geometry": False,
                    "source_file": "vector-index",
                }][:limit], "negative_examples": []}

        retrieval.register_provider("test_vector_provider", StaticVectorProvider())
        try:
            result = retrieval.search_knowledge("某个描述")
        finally:
            retrieval.unregister_provider("test_vector_provider")

        hit = next(row for row in result["matches"]
                   if row.get("provider") == "test_vector_provider")
        self.assertEqual(hit["model"], "IMG-01")
        self.assertEqual(result["status"], "similar_matches")

    def test_image_provider_can_return_visual_candidates(self):
        class StaticImageProvider:
            supports_images = True

            def search(self, query, limit, *, image_paths=None):
                self.received_images = list(image_paths or [])
                return {"matches": [{
                    "id": "image:1", "prompt": "paired image result",
                    "category": "图像样例", "model": "PHOTO-01",
                    "relevance": 0.88, "match_type": "similar_reference",
                    "exact_identifier_match": False,
                    "has_full_feature_tree": False,
                    "source_file": "image-index",
                }][:limit], "negative_examples": []}

        provider = StaticImageProvider()
        retrieval.register_provider("test_image_provider", provider)
        try:
            result = retrieval.search_knowledge("", image_paths=["reference.png"])
        finally:
            retrieval.unregister_provider("test_image_provider")

        self.assertTrue(result["image_query_supported"])
        self.assertEqual(provider.received_images, ["reference.png"])
        self.assertEqual(result["matches"][0]["model"], "PHOTO-01")
        self.assertEqual(result["status"], "similar_matches")

    def test_image_only_request_explains_current_visual_index_limit(self):
        result = retrieval.search_knowledge("", image_paths=["part.png"])

        self.assertEqual(result["status"], "image_only")
        self.assertFalse(result["image_query_supported"])
        self.assertTrue(result["visual_fallback_available"])
        self.assertIn("视觉向量索引", result["message"])

    def test_project_visual_examples_can_be_sent_as_one_contact_sheet(self):
        examples = retrieval.list_visual_examples(limit=24)
        self.assertGreaterEqual(len(examples), 1)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "examples.png"
            selected = retrieval.build_visual_examples_contact_sheet(output, examples[:3])
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(len(selected), 3)

    def test_llm_context_keeps_similar_hits_separate_from_exact_geometry(self):
        result = retrieval.search_knowledge("做一个定位销")
        context = retrieval.format_knowledge_context(result)

        self.assertIn("similar_reference", context)
        self.assertIn("不得将示例尺寸", context)
        self.assertNotIn("精确型号的实测几何摘要", context)


class DSHKnowledgeIntegrationTests(unittest.TestCase):
    def test_initial_dsh_turn_receives_text_hits_and_visual_example_sheet(self):
        captured = {}

        class FakeHarness:
            def run(self, blocks, **_kwargs):
                captured["blocks"] = blocks
                return SimpleNamespace(final_response="收到", finish_reason="stop", events=[])

        example_image = Path(retrieval.PROJECT_ROOT) / "examples" / "真实照片样本" / \
            "10_Tuenkers EGT Gripper-EGT001" / "EGT001 SW80x088.png"
        self.assertTrue(example_image.is_file())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            progress_messages = []
            with mock.patch.object(dsh_agent, "_session_path",
                                   side_effect=lambda session_id: root / session_id), \
                    mock.patch.object(dsh_agent, "_runtime_for", return_value=FakeHarness()):
                result = dsh_agent.run_dsh_agent(
                    "EGT001 SW80×088 型材", image_paths=[str(example_image)],
                    session_id="knowledge-test", progress=progress_messages.append)

            text_blocks = [block["text"] for block in captured["blocks"]
                           if block.get("type") == "text"]
            image_blocks = [block for block in captured["blocks"]
                            if block.get("type") == "image"]
            self.assertTrue(any("exact_match" in text for text in text_blocks))
            self.assertTrue(any("本地视觉样例板条目" in text for text in text_blocks))
            self.assertEqual(len(image_blocks), 1)  # compact sample board; user image is tool-fetched on demand
            self.assertEqual(result["knowledge_search"]["status"], "exact_match")
            self.assertEqual(result["knowledge_image_parts"], 1)
            self.assertTrue(any("本地知识库精确命中" in message
                                for message in progress_messages))
            self.assertFalse((root / "knowledge-test" /
                              "knowledge-visual-examples.png").exists())

    def test_tool_followup_knowledge_returns_and_persists_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "cad-state.json"
            with mock.patch.object(dsh_tool_domain, "_STATE_DIR", root), \
                    mock.patch.object(dsh_tool_domain, "_STATE_PATH", state_path), \
                    mock.patch.object(dsh_tool_domain, "_REFERENCE_IMAGES", []):
                response = dsh_tool_domain._handle_tool(
                    "cad_search_knowledge", {"query": "M5×8 内六角螺栓", "limit": 3})
                state = dsh_tool_domain._load()

        self.assertFalse(response["isError"])
        self.assertTrue(any("exact_match" in block.get("text", "")
                            for block in response["content"] if block.get("type") == "text"))
        self.assertEqual(state["knowledge_search"]["status"], "exact_match")
        self.assertEqual(state["actions"][-1]["tool"], "cad_search_knowledge")


if __name__ == "__main__":
    unittest.main()
