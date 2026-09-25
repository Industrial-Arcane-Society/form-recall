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

    def test_deepseek_flash_disables_default_reasoning_and_keeps_finish_reason(self):
        response = io.BytesIO(json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "{}"},
                "finish_reason": "stop",
            }],
        }).encode("utf-8"))
        with mock.patch.object(llm.urllib.request, "urlopen", return_value=response) as opener:
            result = llm.chat_completion(
                [{"role": "user", "content": "Return JSON."}],
                {"base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-flash", "api_key": "test-only"},
                tools=agentloop.TOOLS)
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertEqual("stop", result["_finish_reason"])

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

    def test_full_staged_loop_views_input_and_preview_without_catia(self):
        plan = _box_plan()
        tool_call = {
            "id": "validate-call",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": plan}),
            },
        }
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "one rectangular solid"})),
            _provider_reply(tool_calls=[tool_call]),
            _provider_reply(json.dumps({
                "decision": "accept", "issues": [], "plan": None,
                "checks": _required_geometry_checks(),
                "questions": [], "confidence": 0.95,
            })),
            _provider_reply(json.dumps({
                "decision": "accept", "similarity_score": 0.94,
                "checks": _required_geometry_checks([{
                    "item": "overall shape", "category": "geometry",
                    "status": "match", "evidence": "single rectangular prism"}]),
                "remaining_differences": [], "plan": None,
                "questions": [], "confidence": 0.95,
            })),
        ]
        captured = []

        def fake_chat(messages, cfg, **kwargs):
            captured.append((messages, kwargs))
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            old_image = Path(temp) / "old.png"
            new_image = Path(temp) / "new.png"
            old_image.write_bytes(b"\x89PNG\r\n\x1a\nold")
            new_image.write_bytes(b"\x89PNG\r\n\x1a\nnew")
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat), \
                    mock.patch.object(agentloop.cadplan, "build_plan",
                                      side_effect=AssertionError("CATIA build must not run")) as build:
                result = agentloop.run_agent_loop(
                    "按图建立模型", image_paths=[str(new_image)],
                    conversation_history=[{
                        "role": "user", "content": "上一轮参考图", "image_paths": [str(old_image)],
                    }],
                    cfg={"llm": {"base_url": "https://example.invalid/v1", "model": "vision-test",
                                 "api_key": "test"},
                         "agentloop": {"total_timeout_sec": 60}},
                    output_dir=Path(temp) / "sessions")
                self.assertTrue(Path(result["preview"]["preview_image"]).is_file())
                record = json.loads(Path(result["run_record"]).read_text(encoding="utf-8"))
                self.assertEqual("accepted", record["state"])
                self.assertEqual(1, len(record["tool_trace"]))
                self.assertTrue(record["preview"]["preview_image"].startswith("preview-"))

        self.assertEqual("accepted", result["state"])
        self.assertEqual(1, result["tool_calls"])
        self.assertEqual(1, result["review_rounds"])
        self.assertFalse(build.called)
        self.assertEqual(5, len(captured))

        # The first request includes both the prior turn's image and this turn's image.
        initial_user_messages = [row for row in captured[0][0] if row.get("role") == "user"]
        image_parts = [part for row in initial_user_messages
                       for part in (row.get("content") if isinstance(row.get("content"), list) else [])
                       if part.get("type") == "image_url"]
        self.assertEqual(2, len(image_parts))

        # Tools are stage scoped: construction validates once, and the visual
        # review may request one extra view when the automatic three-view is unclear.
        names = lambda call: [row["function"]["name"] for row in call[1].get("tools", [])]
        self.assertFalse(captured[0][1].get("tools"))
        self.assertEqual(["web_search"], names(captured[1]))
        self.assertEqual(["cad_validate_plan"], names(captured[2]))
        self.assertEqual(["cad_get_multiview"], names(captured[3]))
        self.assertFalse(captured[4][1].get("tools"))
        construction_system = captured[2][0][0]["content"]
        self.assertIn("<cadplan-json-schema>", construction_system)
        self.assertIn('"schema_version":{"const":"1.0"}', construction_system)
        self.assertIn('"x-feature-support-matrix"', construction_system)
        self.assertIn("中段前表面必须小于两侧段前表面的 Y", construction_system)
        self.assertIn("不要仅因数值是估算值就要求用户确认", construction_system)

        # The app generates the three-view and gives that image to visual review.
        review_messages = captured[3][0]
        preview_user = [row for row in review_messages if row.get("role") == "user"
                        and isinstance(row.get("content"), list)]
        self.assertTrue(any(any(part.get("type") == "image_url" for part in row["content"])
                            for row in preview_user))
        self.assertIn("对话回合路由器", captured[0][0][0]["content"])
        self.assertIn("# 阶段一", captured[1][0][0]["content"])
        self.assertIn("# 阶段三", captured[3][0][0]["content"])
        self.assertIn("# 阶段四", captured[4][0][0]["content"])

    def test_conversation_route_answers_without_starting_model_stages(self):
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
        self.assertEqual("当前模型还是原来的长方体。", result["reply"])
        self.assertEqual(0, result["tool_calls"])
        self.assertEqual(1, len(captured))
        self.assertFalse(captured[0][1].get("tools"))

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
            if "对话记忆压缩器" in messages[0]["content"]:
                return _provider_reply(json.dumps({
                    "summary": "长期约束：中段前表面必须后退。"}, ensure_ascii=False))
            return _provider_reply(json.dumps(route, ensure_ascii=False))

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "你还记得中段要求吗？", conversation_history=history,
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"}},
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
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("conversation", result["state"])
        routed_context = "\n".join(str(row.get("content")) for row in captured[0])
        self.assertIn("BEGIN_REQUIREMENT", routed_context)
        self.assertIn("FINAL_CORRECTION", routed_context)
        self.assertIn("中间内容因上下文预算省略", routed_context)

    def test_manual_document_edit_is_authoritative_and_published_as_live_preview(self):
        original_plan = _box_plan()
        edited_plan = json.loads(json.dumps(original_plan))
        edited_plan["features"][0]["parameters"]["length"] = 25
        validate_call = {
            "id": "validate-edited-plan", "type": "function",
            "function": {"name": "cad_validate_plan",
                         "arguments": json.dumps({"plan": edited_plan})},
        }
        replies = [
            _route_reply(action="edit", need_recognition=False,
                         review_mode="none", review_rounds=0),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(json.dumps(edited_plan)),
        ]
        captured = []
        published = []

        def fake_chat(messages, _cfg, **kwargs):
            captured.append((messages, kwargs))
            return replies.pop(0)

        local_document = agentloop.cadplan.plan_to_document(original_plan)
        local_document.features[0].params["length"] = 25
        snapshot = {"name": local_document.name,
                    "features": [feature.to_dict() for feature in local_document.features]}
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "把当前模型再加长一点", current_plan=original_plan,
                    current_document=snapshot,
                    cfg={"llm": {"base_url": "https://example.invalid/v1",
                                 "model": "vision-test", "api_key": "test"}},
                    on_preview=lambda plan, preview, stage:
                        published.append((plan, preview, stage)),
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("previewed", result["state"])
        self.assertEqual(25, result["plan"]["features"][0]["parameters"]["length"])
        self.assertEqual(2, len(captured))
        construction_messages = captured[1][0]
        self.assertTrue(any(
            "当前实际模型的特征树快照" in str(row.get("content"))
            for row in construction_messages))
        self.assertTrue(any(
            "可能与当前特征树不同步" in str(row.get("content"))
            for row in construction_messages))
        self.assertEqual(1, len(published))
        self.assertEqual("preview", published[0][2])
        self.assertEqual("previewed", published[0][1]["state"])

    def test_live_preview_is_rendered_in_the_right_agent_panel(self):
        photo = mock.Mock()
        photo.width.return_value = 320
        panel = type("PreviewPanel", (), {})()
        panel._last_agent_preview_path = None
        panel._agent_live_photo = None
        panel.tab_agent = mock.Mock()
        panel.tab_agent.winfo_width.return_value = 500
        panel.lbl_agent_preview = mock.Mock()
        panel.lbl_agent_stage = mock.Mock()
        panel.lbl_agent_detail = mock.Mock()
        panel._append_agent_event = mock.Mock()

        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "preview.png"
            image.write_bytes(b"local preview placeholder")
            with mock.patch.object(ui_app.tk, "PhotoImage", return_value=photo):
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
        self.assertIs(photo, panel._agent_live_photo)
        panel.lbl_agent_preview.configure.assert_called_once_with(image=photo, text="")
        self.assertEqual("实时预览 · multiview",
                         panel.lbl_agent_stage.configure.call_args.kwargs["text"])
        self.assertIn("20.00 × 16.00 × 10.00 mm",
                      panel.lbl_agent_detail.configure.call_args.kwargs["text"])
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
        panel._cancel = None
        panel._t0 = time.time()
        panel._budget_s = 600
        panel._poll_agent = mock.Mock()
        panel.after = mock.Mock()

        ui_app.App._poll_agent(panel)

        panel._show_agent_live_preview.assert_called_once_with(payload)
        panel.status.assert_called_once_with("建模过程预览已更新", ui_app.TH.WARN)
        panel.after.assert_called_once_with(120, panel._poll_agent)

    def test_unrepresentable_tree_stops_before_construction(self):
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
        self.assertEqual(1, len(captured))
        self.assertEqual(0, result["tool_calls"])

    def test_high_severity_multiview_issue_cannot_be_accepted(self):
        plan = _box_plan()
        validate_call = {
            "id": "validate-plan",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": plan}),
            },
        }
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "a rectangular plate"})),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(json.dumps({
                "decision": "accept",
                "issues": [{"view": "front", "item": "孔排缺失",
                            "evidence": "只看见两排孔", "severity": "high"}],
                "checks": _required_geometry_checks(),
                "plan": None, "questions": [], "confidence": 0.3,
            })),
        ]

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion",
                                   side_effect=lambda *_args, **_kwargs: replies.pop(0)):
                result = agentloop.run_agent_loop(
                    "reproduce the reference", cfg={"llm": {
                        "base_url": "https://example.invalid/v1",
                        "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")
            self.assertTrue(Path(result["preview"]["preview_image"]).is_file())

        self.assertEqual("needs_user_input", result["state"])
        self.assertTrue(any("高严重度" in question or "置信度" in question
                            for question in result["questions"]))
        self.assertNotIn("similarity", result)

    def test_missing_source_metadata_is_repaired_without_changing_geometry(self):
        initial_plan = _box_plan()
        del initial_plan["features"][0]["sources"]["height"]
        corrected_plan = json.loads(json.dumps(initial_plan))
        corrected_plan["features"][0]["sources"]["height"] = {
            "type": "image_estimate", "evidence": "front view overall height"}
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "a rectangular solid"})),
            _provider_reply(json.dumps(initial_plan)),
            _provider_reply(json.dumps(corrected_plan)),
            _provider_reply(json.dumps({
                "decision": "accept", "issues": [], "plan": None,
                "checks": _required_geometry_checks(),
                "questions": [], "confidence": 0.9,
            })),
            _provider_reply(json.dumps({
                "decision": "accept", "similarity_score": 0.9,
                "checks": _required_geometry_checks([{
                    "item": "shape", "category": "geometry",
                    "status": "match", "evidence": "rectangular prism"}]),
                "remaining_differences": [], "plan": None,
                "questions": [], "confidence": 0.9,
            })),
        ]

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion",
                                   side_effect=lambda *_args, **_kwargs: replies.pop(0)):
                result = agentloop.run_agent_loop(
                    "build a measured rectangular solid", cfg={"llm": {
                        "base_url": "https://example.invalid/v1",
                        "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("accepted", result["state"])
        self.assertEqual([], replies)
        self.assertEqual("image_estimate",
                         result["plan"]["features"][0]["sources"]["height"]["type"])

    def test_similarity_mismatch_cannot_be_accepted(self):
        plan = _box_plan()
        validate_call = {
            "id": "validate-plan",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": plan}),
            },
        }
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "a rectangular plate"})),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(json.dumps({
                "decision": "accept", "issues": [], "plan": None,
                "checks": _required_geometry_checks(),
                "questions": [], "confidence": 0.9,
            })),
            _provider_reply(json.dumps({
                "decision": "accept", "similarity_score": 0.94,
                "checks": _required_geometry_checks([{
                    "item": "hole rows", "category": "geometry", "status": "mismatch",
                    "evidence": "the reference has three rows"}]),
                "remaining_differences": [], "plan": None,
                "questions": [], "confidence": 0.9,
            })),
        ]

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion",
                                   side_effect=lambda *_args, **_kwargs: replies.pop(0)):
                result = agentloop.run_agent_loop(
                    "reproduce the reference", cfg={"llm": {
                        "base_url": "https://example.invalid/v1",
                        "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("needs_user_input", result["state"])
        self.assertEqual("mismatch", result["similarity"]["checks"][0]["status"])
        self.assertTrue(any("three rows" in question for question in result["questions"]))

    def test_segment_ratio_conflict_triggers_one_repair_and_full_repreview(self):
        initial_plan = _segmented_plan([40, 36, 40])
        corrected_plan = _segmented_plan([46, 23, 46])
        validate_call = {
            "id": "validate-plan",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": initial_plan}),
            },
        }
        accept_multiview = json.dumps({
            "decision": "accept", "issues": [], "plan": None,
            "checks": _required_geometry_checks(),
            "questions": [], "confidence": 0.9,
        })
        accept_similarity = json.dumps({
            "decision": "accept", "similarity_score": 0.9,
            "checks": _required_geometry_checks([{
                "item": "segments", "category": "geometry", "status": "match",
                "evidence": "three segments"}]),
            "remaining_differences": [], "plan": None,
            "questions": [], "confidence": 0.9,
        })
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "three segments",
                                        "proportion_observations": {
                                            "front_segment_width_ratios": [0.4, 0.2, 0.4]}})),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(accept_multiview),
            _provider_reply(accept_similarity),
            _provider_reply(json.dumps({
                "decision": "revise", "similarity_score": 0.9,
                "checks": [{"item": "segments", "status": "mismatch",
                            "evidence": "middle is too wide"}],
                "remaining_differences": [], "plan": corrected_plan,
                "questions": [], "confidence": 0.9,
            })),
            _provider_reply(accept_multiview),
            _provider_reply(accept_similarity),
        ]

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion",
                                   side_effect=lambda *_args, **_kwargs: replies.pop(0)):
                result = agentloop.run_agent_loop(
                    "reproduce three visible sections", cfg={"llm": {
                        "base_url": "https://example.invalid/v1",
                        "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("accepted", result["state"])
        self.assertEqual(2, result["review_rounds"])
        self.assertEqual("ratio_repaired_repreview", result["review_history"][0]["action"])
        self.assertEqual("deterministic_ratio_gate", result["automatic_repairs"][0]["source"])
        self.assertEqual("applied", result["automatic_repairs"][0]["result"])
        self.assertAlmostEqual(0.2,
                               result["plan"]["features"][1]["parameters"]["length"] /
                               sum(feature["parameters"]["length"]
                                   for feature in result["plan"]["features"]))

    def test_front_outline_ratio_conflict_triggers_revision_and_repreview(self):
        initial_plan = _box_plan()
        corrected_plan = json.loads(json.dumps(initial_plan))
        corrected_plan["features"][0]["parameters"]["length"] = 9.5
        validate_call = {
            "id": "validate-plan",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": initial_plan}),
            },
        }
        accept_multiview = json.dumps({
            "decision": "accept", "issues": [], "plan": None,
            "checks": _required_geometry_checks(),
            "questions": [], "confidence": 0.9,
        })
        accept_similarity = json.dumps({
            "decision": "accept", "similarity_score": 0.9,
            "checks": _required_geometry_checks([{
                "item": "outline ratio", "category": "geometry", "status": "match",
                "evidence": "overall silhouette"}]),
            "remaining_differences": [], "plan": None,
            "questions": [], "confidence": 0.9,
        })
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({
                "recognized": "a rectangular solid",
                "proportion_observations": {"front_width_height": 0.95},
            })),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(accept_multiview),
            _provider_reply(accept_similarity),
            _provider_reply(json.dumps({
                "decision": "revise", "similarity_score": 0.9,
                "checks": [{"item": "outline ratio", "status": "mismatch",
                            "evidence": "plan is too wide"}],
                "remaining_differences": [], "plan": corrected_plan,
                "questions": [], "confidence": 0.9,
            })),
            _provider_reply(accept_multiview),
            _provider_reply(accept_similarity),
        ]

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion",
                                   side_effect=lambda *_args, **_kwargs: replies.pop(0)):
                result = agentloop.run_agent_loop(
                    "make a rectangular solid with a 0.95 front width-height ratio", cfg={"llm": {
                        "base_url": "https://example.invalid/v1",
                        "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("accepted", result["state"])
        self.assertEqual(2, result["review_rounds"])
        self.assertAlmostEqual(0.95,
                               result["preview"]["dimensions"]["size"][0] /
                               result["preview"]["dimensions"]["size"][2])
        self.assertEqual([], replies)

    def test_construction_stops_after_one_failed_plan_correction(self):
        invalid_plan = _box_plan()
        invalid_plan["unexpected"] = "wrong field"
        validate_call = {
            "id": "invalid-plan",
            "type": "function",
            "function": {
                "name": "cad_validate_plan",
                "arguments": json.dumps({"plan": invalid_plan}),
            },
        }
        replies = [
            _route_reply(),
            _provider_reply(json.dumps({"recognized": "a box"})),
            _provider_reply(tool_calls=[validate_call]),
            _provider_reply(tool_calls=[validate_call]),
        ]
        captured = []

        def fake_chat(messages, cfg, **kwargs):
            captured.append((messages, kwargs))
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                with self.assertRaisesRegex(
                        agentloop.AgentLoopError, "一次结构修正后仍未通过") as caught:
                    agentloop.run_agent_loop(
                        "build a test box",
                        cfg={"llm": {"base_url": "https://example.invalid/v1",
                                     "model": "vision-test", "api_key": "test"}},
                        output_dir=Path(temp) / "sessions")
            record = json.loads(Path(caught.exception.run_record).read_text(encoding="utf-8"))

        self.assertEqual(4, len(captured))
        self.assertEqual(2, len(record["tool_trace"]))
        self.assertEqual("failed", record["state"])

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

    def test_tool_preview_does_not_consume_three_review_rounds(self):
        plan = _box_plan()
        preview_call = {
            "id": "extended-preview",
            "type": "function",
            "function": {
                "name": "cad_get_multiview",
                "arguments": json.dumps({"plan": plan, "view_set": "extended"}),
            },
        }
        replies = [
            _route_reply(review_rounds=1),
            _provider_reply(json.dumps({"recognized": "a rectangular solid"})),
            _provider_reply(json.dumps(plan)),
            _provider_reply(tool_calls=[preview_call]),
            _provider_reply(json.dumps({
                "decision": "accept", "issues": [], "plan": None,
                "checks": _required_geometry_checks(),
                "questions": [], "confidence": 0.9,
            })),
            _provider_reply(json.dumps({
                "decision": "accept", "similarity_score": 0.9,
                "checks": _required_geometry_checks(),
                "remaining_differences": [], "plan": None,
                "questions": [], "confidence": 0.9,
            })),
        ]

        def fake_chat(_messages, _cfg, **_kwargs):
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(agentloop.llm, "chat_completion", side_effect=fake_chat):
                result = agentloop.run_agent_loop(
                    "build a rectangular solid", cfg={
                        "llm": {"base_url": "https://example.invalid/v1",
                                "model": "vision-test", "api_key": "test"}},
                    output_dir=Path(temp) / "sessions")

        self.assertEqual("accepted", result["state"], result)
        self.assertEqual(1, result["review_rounds"])
        self.assertEqual(2, result["preview_calls"])


if __name__ == "__main__":
    unittest.main()
