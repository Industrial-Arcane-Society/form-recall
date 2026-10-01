"""SiliconFlow thinking parameter and provider-config compatibility tests."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_modeler.agent import llm


KIMI_MODEL = "moonshotai/Kimi-K2.7-Code"


class SiliconFlowThinkingTests(unittest.TestCase):
    def _params(self, effort, budget=None, model=KIMI_MODEL):
        cfg = {"model": model, "reasoning_effort": effort}
        if budget is not None:
            cfg["thinking_budget"] = budget
        return llm._siliconflow_thinking_params(cfg)

    def test_none_disables_thinking_even_with_explicit_budget(self):
        self.assertEqual(
            {"enable_thinking": False}, self._params("none", budget=2048)
        )

    def test_low_medium_high_enable_without_reasoning_effort_or_budget(self):
        for effort in ("low", "medium", "high"):
            with self.subTest(effort=effort):
                self.assertEqual(
                    {"enable_thinking": True}, self._params(effort)
                )

    def test_explicit_kimi_budget_is_forwarded_and_overrides_max_default(self):
        self.assertEqual(
            {"enable_thinking": True, "thinking_budget": 4096},
            self._params("high", budget=4096),
        )
        self.assertEqual(
            {"enable_thinking": True, "thinking_budget": 4096},
            self._params("max", budget=4096),
        )
        self.assertEqual(
            {"enable_thinking": True, "thinking_budget": 32768},
            self._params("max"),
        )

    def test_explicit_kimi_budget_is_clamped_to_api_range(self):
        self.assertEqual(
            {"enable_thinking": True, "thinking_budget": 128},
            self._params("high", budget=1),
        )
        self.assertEqual(
            {"enable_thinking": True, "thinking_budget": 32768},
            self._params("high", budget=50000),
        )

    def test_invalid_explicit_kimi_budget_is_rejected(self):
        for value in (True, 512.5, "not-a-number", []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "thinking_budget"):
                    self._params("high", budget=value)

    def test_explicit_budget_is_limited_to_kimi_model_family(self):
        self.assertEqual(
            {"enable_thinking": True},
            self._params(
                "high", budget=2048, model="deepseek-ai/DeepSeek-V4-Flash"
            ),
        )

    def test_chat_completion_places_explicit_budget_in_request_json(self):
        payloads = []

        def open_request(request, timeout=None):
            payloads.append(json.loads(request.data.decode("utf-8")))
            chunk = {"id": "chat-test", "choices": [
                {"delta": {}, "finish_reason": "stop"}]}
            stream = "data: %s\n\ndata: [DONE]\n\n" % json.dumps(chunk)
            return io.BytesIO(stream.encode("utf-8"))

        cfg = {
            "base_url": "https://api.siliconflow.cn/v1",
            "model": KIMI_MODEL,
            "api_key": "test-only",
            "reasoning_effort": "high",
            "thinking_budget": 2048,
        }
        with mock.patch.object(llm.urllib.request, "urlopen",
                               side_effect=open_request):
            llm.chat_completion([{"role": "user", "content": "Test."}], cfg)

        self.assertEqual(1, len(payloads))
        self.assertTrue(payloads[0]["enable_thinking"])
        self.assertEqual(2048, payloads[0]["thinking_budget"])
        self.assertNotIn("reasoning_effort", payloads[0])

    def test_request_profile_sets_task_soft_budgets_and_preserves_user_choice(self):
        cfg = {"base_url": "https://api.siliconflow.cn/v1",
               "model": KIMI_MODEL, "reasoning_effort": "low"}
        inventory = llm.resolve_request_profile(
            cfg, "vision_review", "source_inventory")
        self.assertEqual(1024, inventory["thinking_budget"])
        self.assertEqual("task_profile_soft_default", inventory["thinking_budget_source"])
        self.assertFalse(inventory["thinking_budget_hard_limit_known"])
        self.assertEqual({"enable_thinking": True, "thinking_budget": 1024},
                         inventory["payload_reasoning"])

        conflict = llm.resolve_request_profile(
            cfg, "vision_review", "evidence_conflict")
        self.assertEqual("high", conflict["reasoning_effort"])
        self.assertEqual(4096, conflict["thinking_budget"])

        cfg["thinking_budget"] = 3072
        explicit = llm.resolve_request_profile(
            cfg, "vision_review", "source_inventory")
        self.assertEqual(3072, explicit["thinking_budget"])
        self.assertEqual("user_config", explicit["thinking_budget_source"])

    def test_construction_low_profile_is_explicit_and_none_stays_disabled(self):
        cfg = {"base_url": "https://api.siliconflow.cn/v1",
               "model": KIMI_MODEL, "reasoning_effort": "low"}
        construction = llm.resolve_request_profile(cfg, "construction")
        self.assertEqual(2048, construction["thinking_budget"])
        self.assertEqual({"enable_thinking": True, "thinking_budget": 2048},
                         construction["payload_reasoning"])
        cfg["reasoning_effort"] = "none"
        disabled = llm.resolve_request_profile(cfg, "vision_review", "source_inventory")
        self.assertIsNone(disabled["thinking_budget"])
        self.assertEqual({"enable_thinking": False}, disabled["payload_reasoning"])

    def test_nonstream_review_wire_observability_keeps_profile_cache_and_finish(self):
        class Response(io.BytesIO):
            status = 200

        response_json = {
            "id": "review-test",
            "choices": [{"message": {"content": "{\"summary\":\"ok\"}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "total_tokens": 120,
                      "prompt_tokens_details": {"cached_tokens": 40}},
        }
        with tempfile.TemporaryDirectory() as temp:
            telemetry = Path(temp) / "observability.jsonl"
            cfg = {"base_url": "https://api.siliconflow.cn/v1",
                   "model": KIMI_MODEL, "api_key": "test-only",
                   "reasoning_effort": "low"}
            with mock.patch.dict(os.environ, {"AI_MODELER_STREAM_FILE": str(telemetry)}), \
                    mock.patch.object(llm, "ensure_local_server", return_value=(True, "")), \
                    mock.patch.object(llm.urllib.request, "urlopen",
                                      return_value=Response(json.dumps(response_json).encode())):
                message = llm.chat_completion(
                    [{"role": "user", "content": "review"}], cfg,
                    max_tokens=1800, stream=False, task_kind="vision_review",
                    review_complexity="source_inventory")
            rows = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
        request = next(row for row in rows if row["kind"] == "provider_wire_request")
        completed = next(row for row in rows
                         if row["kind"] == "provider_wire_response_complete")
        self.assertEqual(1800, request["request_fields"]["output_token_fields"]["max_tokens"])
        self.assertEqual(1024, request["request_fields"]["resolved_thinking_budget"])
        self.assertEqual("task_profile_soft_default",
                         request["request_fields"]["thinking_budget_source"])
        self.assertFalse(request["request_fields"]["thinking_budget_hard_limit_known"])
        self.assertEqual("stop", completed["finish_reason"])
        self.assertEqual(40, completed["usage"]["cache_token_details"][
            "prompt_tokens_details.cached_tokens"])
        self.assertEqual(40, message["_usage"]["cache_token_details"][
            "prompt_tokens_details.cached_tokens"])

    def test_provider_usage_omits_null_boolean_and_non_finite_counts(self):
        usage = llm._safe_provider_usage({
            "prompt_tokens": None,
            "completion_tokens": True,
            "total_tokens": float("inf"),
            "reasoning_tokens": 3.5,
            "prompt_tokens_details": {
                "cached_tokens": None,
                "cache_read_tokens": float("inf"),
                "cache_write_tokens": 7,
            },
        })

        self.assertEqual({
            "reasoning_tokens": 3.5,
            "cache_token_details": {"prompt_tokens_details.cache_write_tokens": 7},
        }, usage)


if __name__ == "__main__":
    unittest.main()
