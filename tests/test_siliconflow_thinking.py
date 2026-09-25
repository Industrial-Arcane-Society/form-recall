"""SiliconFlow thinking parameter and provider-config compatibility tests."""
from __future__ import annotations

import io
import json
import unittest
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


if __name__ == "__main__":
    unittest.main()
