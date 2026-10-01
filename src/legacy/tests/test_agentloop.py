"""Desktop AgentLoop tests that are independent of the retired stage machine.

Nine tests that drove the old turn-router JSON protocol and stage-scoped tool
sets were archived here: `temp/legacy-documents/docs/architecture/AgentLoop.md` records that the fixed
recognition/construction/multiview staging was replaced by one persistent tool
loop, so those tests asserted a product that no longer exists. The dynamic
loop's own acceptance suite is `tests/test_agent_runtime_incremental.py`.
"""
import json
import io
import queue
import struct
import tempfile
import time
import unittest
import urllib.error
import zlib
from pathlib import Path
from unittest import mock

from ai_modeler import agentloop, llm
from ai_modeler.agent import image_analysis
from ai_modeler.cadplan import CADPLAN_SCHEMA_VERSION, preview_plan
from ai_modeler.llm import image_content_part
from ai_modeler.clipboard_image import PNG_SIGNATURE, dib_to_png
from ai_modeler.ui import app as ui_app


def _box_plan():
    return {
        "schema_version": CADPLAN_SCHEMA_VERSION,
        "name": "AgentLoop test box",
        "units": {"length": "mm", "angle": "deg"},
        "coordinate_system": {
            "type": "global_cartesian", "handedness": "right",
            "axes": ["X", "Y", "Z"], "origin": [0, 0, 0],
        },
        "features": [{
            "id": "base", "kind": "box", "operation": "add",
            "parameters": {"length": 20, "width": 16, "height": 10},
            "sources": {
                "length": {"type": "user"},
                "width": {"type": "user"},
                "height": {"type": "user"},
            },
        }],
    }


def _segmented_plan(widths):
    total = sum(widths)
    cursor = -total / 2
    features = []
    for index, width in enumerate(widths):
        center_x = cursor + width / 2
        features.append({
            "id": ["left", "middle", "right"][index],
            "kind": "box", "operation": "add",
            "parameters": {"length": width, "width": 30, "height": 120,
                           "center": [center_x, 0, 0]},
            "sources": {"length": {"type": "image_estimate",
                                     "evidence": "front orthographic segment width"},
                        "width": {"type": "user"},
                        "height": {"type": "user"},
                        "center": {"type": "derived", "note": "centered segments"}},
        })
        cursor += width
    plan = _box_plan()
    plan["features"] = features
    return plan


def _write_rgb_png(path, width, height, pixels):
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixels(x, y))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    content = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
               chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    Path(path).write_bytes(content)


def _provider_reply(content=None, tool_calls=None):
    return {"content": content, "tool_calls": tool_calls or []}


def _route_reply(action="create", need_recognition=True,
                 review_mode="full", review_rounds=3):
    return _provider_reply(json.dumps({
        "action": action,
        "reply": "",
        "questions": [],
        "need_recognition": need_recognition,
        "review_mode": review_mode,
        "review_rounds": review_rounds,
        "reason": "test route",
    }))


def _required_geometry_checks(extra=()):
    checks = [
        {"id": "front.outline", "view": "front", "area": "outline",
         "category": "geometry", "status": "match", "evidence": "轮廓与计划一致。"},
        {"id": "front.features", "view": "front", "area": "features",
         "category": "geometry", "status": "match", "evidence": "特征布局与计划一致。"},
        {"id": "right.depth_setback", "view": "right", "area": "depth_setback",
         "category": "geometry", "status": "match", "evidence": "厚度与后退关系一致。"},
        {"id": "right.through_connectivity", "view": "right",
         "area": "through_connectivity", "category": "geometry",
         "status": "match", "evidence": "贯穿方向与连接关系一致。"},
        {"id": "top.top_boundaries", "view": "top", "area": "top_boundaries",
         "category": "geometry", "status": "match", "evidence": "顶视边界与计划一致。"},
    ]
    return list(extra) + checks


class AgentLoopTests(unittest.TestCase):
    def test_local_image_scan_measures_three_flat_front_regions(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "front.png"

            def pixel(x, _y):
                if 20 <= x < 80 or 120 <= x < 180:
                    return (181, 197, 197)
                if 80 <= x < 120:
                    return (201, 138, 138)
                return (119, 119, 153)

            _write_rgb_png(image, 200, 100, pixel)
            result = image_analysis.analyze_reference_images([image])

        candidate = result["images"][0]["three_region_scanline_candidate"]
        self.assertIsNotNone(candidate)
        self.assertEqual("high", candidate["confidence"])
        for actual, expected in zip(candidate["width_ratios"], [0.375, 0.25, 0.375]):
            self.assertAlmostEqual(expected, actual)
        self.assertEqual("high", result["images"][0]["foreground_bbox_candidate"]["confidence"])

    def test_deepseek_flash_uses_responses_high_default_and_keeps_finish_reason(self):
        events = [
            {"type": "response.created", "response": {"id": "resp-test"}},
            {"type": "response.completed", "response": {
                "id": "resp-test", "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "{}"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 1,
                          "total_tokens": 4,
                          "output_tokens_details": {"reasoning_tokens": 0}},
            }},
        ]
        stream = "".join(
            "event: %s\ndata: %s\n\n" % (event["type"], json.dumps(event))
            for event in events)
        response = io.BytesIO(stream.encode("utf-8"))
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=response) as opener:
            result = llm.chat_completion(
                [{"role": "user", "content": "Return JSON."}],
                {"base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-flash", "api_key": "test-only"},
                tools=agentloop.TOOLS, tool_choice={
                    "type": "function", "function": {"name": "cad_create_model"}})
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("https://api.deepseek.com/responses", request.full_url)
        self.assertEqual("high", payload["reasoning"]["effort"])
        self.assertEqual({"type": "function", "name": "cad_create_model"},
                         payload["tool_choice"])
        self.assertEqual("stop", result["_finish_reason"])
        self.assertEqual("high", result["_request_reasoning_effort"])

    def test_siliconflow_k27_forwards_named_choice_and_keeps_auto_default(self):
        tool = {"type": "function", "function": {
            "name": "cad_create_model",
            "parameters": {"type": "object", "properties": {}}}}
        payloads = []

        def open_request(request, timeout=None):
            payloads.append(json.loads(request.data.decode("utf-8")))
            chunk = {"id": "chat-test", "choices": [
                {"delta": {}, "finish_reason": "stop"}]}
            stream = "data: %s\n\ndata: [DONE]\n\n" % json.dumps(chunk)
            return io.BytesIO(stream.encode("utf-8"))

        cfg = {"base_url": "https://api.siliconflow.cn/v1",
               "model": "moonshotai/Kimi-K2.7-Code", "api_key": "test-only",
               "reasoning_effort": "none"}
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=open_request):
            llm.chat_completion([{"role": "user", "content": "Build."}], cfg,
                                tools=[tool], tool_choice={
                                    "type": "function",
                                    "function": {"name": "cad_create_model"}})
            llm.chat_completion([{"role": "user", "content": "Continue."}], cfg,
                                tools=[tool])

        self.assertEqual({"type": "function", "function": {
            "name": "cad_create_model"}}, payloads[0]["tool_choice"])
        self.assertFalse(payloads[0]["enable_thinking"])
        self.assertEqual("auto", payloads[1]["tool_choice"])

    def test_chat_completion_stream_stops_reading_after_done_marker(self):
        chunk = {"id": "chat-test", "choices": [
            {"delta": {"content": "{}"}, "finish_reason": "stop"}]}

        class GuardedStream:
            def __init__(self):
                self.read_after_done = False

            def __iter__(self):
                yield ("data: %s\n" % json.dumps(chunk)).encode("utf-8")
                yield b"\n"
                yield b"data: [DONE]\n"
                self.read_after_done = True
                raise AssertionError("stream reader continued after [DONE]")

        stream = GuardedStream()
        result = llm._read_chat_completions_stream(stream)
        self.assertEqual("{}", result["content"])
        self.assertEqual("stop", result["finish_reason"])
        self.assertFalse(stream.read_after_done)

    def test_stream_timeout_caps_idle_wait_not_output_tokens(self):
        self.assertEqual(60, llm._stream_request_timeout(600))
        self.assertEqual(12.5, llm._stream_request_timeout(12.5))
        self.assertEqual(60, llm._stream_request_timeout(None))

    def test_empty_provider_response_reports_finish_reason(self):
        state = {"deadline": time.monotonic() + 10}
        with mock.patch.object(llm, "chat_completion",
                               return_value={"content": "", "_finish_reason": "length"}):
            with self.assertRaisesRegex(agentloop.AgentLoopError, "finish_reason=length"):
                agentloop._run_tools(
                    [{"role": "user", "content": "Generate a CAD plan."}],
                    {}, "recognition", state)

    def test_image_capability_error_is_not_mislabeled_as_tools_error(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":"image_url is not supported by this model"}'))
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "不接受图像输入"):
                llm.chat_completion(
                    [{"role": "user", "content": "test"}],
                    {"base_url": "https://example.invalid/v1", "model": "test"},
                    tools=agentloop.TOOLS)

    def test_tool_capability_error_identifies_function_calling(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":"tools are not supported"}'))
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "function calling"):
                llm.chat_completion(
                    [{"role": "user", "content": "test"}],
                    {"base_url": "https://example.invalid/v1", "model": "test"},
                    tools=agentloop.TOOLS)

    def test_clipboard_dib_is_converted_to_png(self):
        # A one-pixel, 24-bit Windows DIB containing a red pixel (BGR + row padding).
        header = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
        png = dib_to_png(header + bytes((0, 0, 255, 0)))
        self.assertTrue(png.startswith(PNG_SIGNATURE))
        offset = len(PNG_SIGNATURE)
        chunks = {}
        while offset < len(png):
            size = struct.unpack_from(">I", png, offset)[0]
            kind = png[offset + 4:offset + 8]
            chunks[kind] = png[offset + 8:offset + 8 + size]
            offset += size + 12
        self.assertEqual((1, 1), struct.unpack(">II", chunks[b"IHDR"][:8]))
        self.assertEqual(b"\x00\xff\x00\x00", zlib.decompress(chunks[b"IDAT"]))

    def test_image_part_encodes_local_image_as_data_uri(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ref.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
            part = image_content_part(path)
        self.assertEqual("image_url", part["type"])
        self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_extended_preview_has_three_orthographic_and_four_extra_views(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "extended.png"
            result = preview_plan(_box_plan(), str(path), view_set="extended")
            self.assertEqual("previewed", result["state"])
            self.assertEqual(["iso", "front", "right", "top", "back", "left", "bottom"],
                             result["preview_image_views"])
            self.assertTrue(path.is_file())

    def test_conversation_route_answers_without_starting_modeling(self):
        route = {
            "action": "conversation", "reply": "当前模型还是原来的长方体。",
            "questions": [], "need_recognition": False,
            "review_mode": "none", "review_rounds": 0,
            "reason": "用户在询问当前模型，没有要求改几何。",
        }
        captured = []

        def fake_chat(messages, _cfg, **kwargs):
            captured.append((messages, kwargs))
            return _provider_reply(json.dumps(route, ensure_ascii=False))

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "当前模型还是刚才那个吗？", current_plan=_box_plan(),
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("conversation", result["state"])
        # The retired turn router answered with JSON; the reply the user reads
        # must still be the human sentence inside it, never the braces.
        self.assertEqual("当前模型还是原来的长方体。", result["reply"])
        self.assertEqual(0, result["tool_calls"])
        self.assertEqual(1, len(captured))
        # The dynamic loop always offers the full tool set: whether this turn is
        # a plain answer is the model's call, not the program's stage machine.
        self.assertTrue(captured[0][1].get("tools"))

    def test_aged_context_is_summarized_and_recent_turns_remain_available(self):
        route = {
            "action": "conversation", "reply": "我记得中段需要后退。",
            "questions": [], "need_recognition": False,
            "review_mode": "none", "review_rounds": 0,
            "reason": "延续先前模型约束。",
        }
        history = [
            {"role": "user" if index % 2 == 0 else "assistant",
             "content": "历史轮次 %02d：保留这个约束。" % index}
            for index in range(40)
        ]
        captured = []

        def fake_chat(messages, _cfg, **kwargs):
            captured.append((messages, kwargs))
            # Only the conversation summarizer sends this batch of aged turns.
            if "即将移出近期窗口的对话" in json.dumps(messages, ensure_ascii=False):
                return _provider_reply(json.dumps({
                    "summary": "长期约束：中段前表面必须后退。"}, ensure_ascii=False))
            return _provider_reply(json.dumps(route, ensure_ascii=False))

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "你还记得中段要求吗？", conversation_history=history,
                    # The aged-context window is 128 turns by default; shrink it
                    # so 40 rows actually overflow into one summary batch.
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"},
                         "agentloop": {"context_turns": 16}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("conversation", result["state"])
        self.assertEqual("长期约束：中段前表面必须后退。", result["conversation_summary"])
        self.assertEqual(8, result["summary_through"])
        self.assertEqual(2, len(captured))
        summary_input = captured[0][0][1]["content"]
        if isinstance(summary_input, list):
            summary_input = "\n".join(part.get("text", "") for part in summary_input
                                       if isinstance(part, dict))
        self.assertIn("历史轮次 00", summary_input)
        routed_context = "\n".join(str(row.get("content")) for row in captured[1][0])
        self.assertIn("长期约束：中段前表面必须后退。", routed_context)
        self.assertIn("历史轮次 39", routed_context)

    def test_context_budget_preserves_both_ends_of_a_long_recent_message(self):
        route = {
            "action": "conversation", "reply": "收到，会保留开头目标和最后的修正。",
            "questions": [], "need_recognition": False,
            "review_mode": "none", "review_rounds": 0,
            "reason": "测试长消息裁切。",
        }
        content = "BEGIN_REQUIREMENT " + ("x" * 60000) + " FINAL_CORRECTION"
        captured = []

        def fake_chat(messages, _cfg, **_kwargs):
            captured.append(messages)
            return _provider_reply(json.dumps(route, ensure_ascii=False))

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "继续刚才的讨论", conversation_history=[{
                        "role": "user", "content": content}],
                    # The default char budget is 1,000,000; use the configured
                    # minimum so one long message has to be trimmed.
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"},
                         "agentloop": {"context_chars": 48000}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("conversation", result["state"])
        routed_context = "\n".join(str(row.get("content")) for row in captured[0])
        self.assertIn("BEGIN_REQUIREMENT", routed_context)
        self.assertIn("FINAL_CORRECTION", routed_context)
        self.assertIn("中间内容因上下文预算省略", routed_context)

    def test_live_preview_is_appended_to_the_conversation(self):
        panel = type("PreviewPanel", (), {})()
        panel._last_agent_preview_path = None
        panel._append_agent_event = mock.Mock()
        panel._append_chat = mock.Mock()
        panel._set_chat_activity = mock.Mock()

        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "preview.png"
            image.write_bytes(b"local preview placeholder")
            ui_app.App._show_agent_live_preview(panel, {
                "stage": "multiview",
                "preview": {
                    "state": "previewed", "preview_image": str(image),
                    "preview_image_views": ["front", "right", "top"],
                    "dimensions": {"size": [20, 16, 10]},
                    "preview_image_source": "local mesh preview",
                },
            })

        self.assertEqual(str(image), panel._last_agent_preview_path)
        panel._append_chat.assert_called_once_with(
            "assistant", "实时模型预览 · multiview\n外形尺寸：20.00 × 16.00 × 10.00 mm\n视图：front、right、top",
            [str(image)])
        panel._set_chat_activity.assert_called_once_with(
            "模型预览已更新", "实时模型预览 · multiview\n外形尺寸：20.00 × 16.00 × 10.00 mm\n视图：front、right、top",
            active=True)
        panel._append_agent_event.assert_called_once()

    def test_preview_queue_is_polled_while_agent_worker_is_running(self):
        payload = {"request_id": 17, "preview": {"state": "previewed"},
                   "stage": "preview"}
        panel = type("AgentPanel", (), {})()
        panel._q = queue.Queue()
        panel._q.put(("agent_preview", payload, None))
        panel._active_request = {"id": 17}
        panel._show_agent_live_preview = mock.Mock()
        panel.status = mock.Mock()
        # Members App._poll_agent touches on the way to the preview branch.
        panel._finish_agent_reasoning_block = mock.Mock()
        panel._set_agent_thinking_panel = mock.Mock()
        panel._refresh_agent_wait_clock = mock.Mock()
        panel._agent_wait_started = None
        panel._agent_stream_detail = ''
        panel._cancel = None
        panel._t0 = time.time()
        panel._budget_s = 600
        panel._poll_agent = mock.Mock()
        panel.after = mock.Mock()

        ui_app.App._poll_agent(panel)

        panel._show_agent_live_preview.assert_called_once_with(payload)
        panel.status.assert_called_once_with("建模过程预览已更新", ui_app.TH.WARN)
        panel.after.assert_called_once_with(120, panel._poll_agent)

    def test_unrepresentable_tree_stops_before_any_model_call(self):
        route = {
            "action": "edit", "reply": "", "questions": [],
            "need_recognition": False, "review_mode": "none",
            "review_rounds": 0, "reason": "用户要求编辑当前模型。",
        }
        captured = []

        def fake_chat(messages, _cfg, **kwargs):
            captured.append((messages, kwargs))
            return _provider_reply(json.dumps(route, ensure_ascii=False))

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "改一下当前模型", current_plan=_box_plan(),
                    current_document={"name": "legacy", "features": [{
                        "kind": "hex_bolt", "name": "旧螺栓", "params": {},
                        "op": "add", "enabled": True,
                    }]},
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("needs_user_input", result["state"])
        self.assertIn("hex_bolt", result["reply"])
        # The snapshot cannot become a CADPlan at all, so the turn is refused
        # before any model request is paid for.
        self.assertEqual(0, len(captured))
        self.assertEqual(0, result["tool_calls"])

    def test_failed_run_record_redacts_api_key(self):
        secret = "super-secret-test-key"
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(
                    agentloop.llm, "chat_completion",
                    side_effect=RuntimeError("upstream rejected " + secret)):
                with self.assertRaisesRegex(RuntimeError, "upstream rejected") as caught:
                    agentloop.run_agent_loop(
                        "make a test part", cfg={"llm": {
                            "base_url": "https://example.invalid/v1",
                            "model": "vision-test", "api_key": secret}},
                        output_dir=Path(temp) / "sessions")
            record_path = Path(caught.exception.run_record)
            contents = record_path.read_text(encoding="utf-8")
            record = json.loads(contents)
        self.assertEqual("failed", record["state"])
        self.assertNotIn(secret, contents)
        self.assertIn("[REDACTED]", record["error"])

    def test_model_stream_checkpoint_keeps_channel_metrics_without_text(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {
                "model_cfg": {"base_url": "https://api.siliconflow.cn/v1",
                              "model": "moonshotai/Kimi-K2.7-Code"},
                "cfg": {"llm": {"model": "moonshotai/Kimi-K2.7-Code"}},
                "session_dir": Path(temp), "run_started_at": "2026-09-26T00:00:00+08:00",
                "run_prompt": "复刻", "run_image_paths": [], "run_status": "running",
                "model_calls": [], "tool_trace": [],
            }
            agentloop._begin_model_call(
                state, "agent_loop", [{"role": "user", "content": "复刻"}],
                65536, timeout=600, tool_choice="required")
            state["_active_request_checkpoint_monotonic"] = time.monotonic() - 20
            progress = agentloop._stream_progress_callback(state, "主循环")
            progress({
                "event": "chat.completion.delta", "reasoning_delta": "PRIVATE THOUGHT",
                "reasoning_chars": 37, "output_chars": 21,
                "visible_output": "VISIBLE ANSWER", "tool_argument_chars": 59,
                "elapsed_seconds": 4.2,
            })
            interim = json.loads((Path(temp) / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(1, interim["active_request"]["stream_progress"]["event_count"])
            self.assertEqual(37, interim["active_request"]["stream_progress"]["reasoning_chars"])
            agentloop._record_model_call(
                state, "agent_loop", [{"role": "user", "content": "复刻"}], 65536,
                reply={"content": "VISIBLE ANSWER", "reasoning_content": "PRIVATE THOUGHT",
                       "_finish_reason": "tool_calls", "tool_calls": [{
                           "function": {"name": "cad_create_model",
                                        "arguments": '{"name":"body"}'}}]})
            record = json.loads((Path(temp) / "run.json").read_text(encoding="utf-8"))

        call = record["model_calls"][0]
        self.assertEqual("running", record["state"])
        self.assertEqual(["cad_create_model"], call["tool_call_names"])
        self.assertEqual(37, call["reasoning_chars"])
        self.assertEqual(21, call["answer_chars"])
        self.assertEqual(59, call["tool_argument_chars"])
        self.assertEqual(600, call["request_timeout_seconds"])
        self.assertIsNone(record["active_request"])
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("PRIVATE THOUGHT", serialized)
        self.assertNotIn("VISIBLE ANSWER", serialized)

    def test_keyboard_interrupt_keeps_interrupted_run_record(self):
        with tempfile.TemporaryDirectory() as temp:
            def interrupt(*_args, **kwargs):
                state = kwargs["_state_out"]
                state["active_request"] = {
                    "phase": "similarity_review", "status": "streaming",
                    "stream_progress": {"event_count": 3, "reasoning_chars": 0,
                                        "answer_chars": 280, "tool_argument_chars": 0},
                }
                raise KeyboardInterrupt()

            with mock.patch.object(agentloop, "_run_agent_loop_impl", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    agentloop.run_agent_loop(
                        "复刻", cfg={"llm": {"base_url": "https://example.test/v1",
                                            "model": "vision-test"}},
                        output_dir=Path(temp) / "sessions")

            record_path = next((Path(temp) / "sessions").glob("*/run.json"))
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("interrupted", record["state"])
        self.assertEqual("复刻", record["input"]["prompt"])
        self.assertEqual("interrupted", record["active_request"]["status"])
        self.assertEqual(280, record["active_request"]["stream_progress"]["answer_chars"])
        self.assertIn("KeyboardInterrupt", record["error"])

    def test_web_search_tool_uses_configured_online_search(self):
        state = {"cfg": {"online": {"enabled": True, "timeout": 5}},
                 "web_search_calls": 0}
        rows = [{"title": "Standard", "url": "https://example.test/std",
                 "snippet": "A public standard reference."}]
        with mock.patch.object(agentloop.online, "search", return_value=rows) as search:
            result, image = agentloop._dispatch_tool(
                "web_search", {"query": "bearing standard"}, state)
        self.assertIsNone(image)
        self.assertEqual(rows, result["results"])
        self.assertEqual(1, state["web_search_calls"])
        search.assert_called_once_with("bearing standard", timeout=5, use_cache=True)

if __name__ == "__main__":
    unittest.main()
