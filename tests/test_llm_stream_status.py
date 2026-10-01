"""Checks that stream progress labels distinguish reasoning, answers, and tools."""
import unittest

from ai_modeler.agent.llm import describe_stream_progress, emit_stream_progress


class DescribeStreamProgressTests(unittest.TestCase):
    def test_reasoning_chars_are_reported_as_reasoning(self):
        status = describe_stream_progress({"reasoning_chars": 137})

        self.assertIn("推理", status)
        self.assertIn("137", status)

    def test_visible_response_text_is_not_reported_as_thinking(self):
        status = describe_stream_progress({
            "output_chars": 82,
            "visible_output": "正在组织可读的答复内容。",
        })

        self.assertIn("答复文本", status)
        self.assertIn("82", status)
        self.assertNotIn("思考", status)
        self.assertNotIn("推理", status)
        self.assertNotIn("正在组织可读的答复内容", status)

    def test_visible_output_alone_is_answer_text_not_reasoning(self):
        status = describe_stream_progress({"visible_output": "answer text"})

        self.assertIn("答复文本", status)
        self.assertNotIn("思考", status)

    def test_reasoning_and_answer_counters_are_both_reported(self):
        status = describe_stream_progress({
            "reasoning_chars": 31,
            "output_chars": 48,
        })

        self.assertIn("推理流", status)
        self.assertIn("答复文本", status)
        self.assertIn("31", status)
        self.assertIn("48", status)

    def test_tool_argument_stream_is_reported_as_tool_call(self):
        status = describe_stream_progress({"tool_argument_chars": 24})

        self.assertIn("工具调用", status)
        self.assertIn("24", status)
        self.assertNotIn("思考", status)

    def test_created_event_and_empty_event_have_clear_fallbacks(self):
        self.assertEqual("服务端已接收请求",
                         describe_stream_progress({"event": "response.created"}))
        self.assertEqual("正在接收模型流式响应", describe_stream_progress({}))
        self.assertEqual("正在接收模型流式响应", describe_stream_progress(None))

    def test_progress_callback_reports_counts_without_model_text(self):
        messages = []
        emit_stream_progress({
            "reasoning_chars": 137,
            "reasoning_delta": "PRIVATE THOUGHT",
            "output_chars": 42,
            "visible_output": "PRIVATE ANSWER",
        }, messages.append, detail="主循环")

        self.assertEqual(1, len(messages))
        self.assertIn("主循环", messages[0])
        self.assertIn("137", messages[0])
        self.assertNotIn("PRIVATE THOUGHT", messages[0])
        self.assertNotIn("PRIVATE ANSWER", messages[0])


if __name__ == "__main__":
    unittest.main()
