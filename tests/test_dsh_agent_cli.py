from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_modeler.agent import dsh_agent
from ai_modeler.cli import agent as agent_cli
from ai_modeler.cli.main import main as dispatch_main
from ai_modeler.runtime import config as runtime_config


class DshAgentCliTests(unittest.TestCase):
    def test_reference_observation_uses_targeted_roi_without_repeating_cached_keys(self):
        prompt = dsh_agent._prompt()

        self.assertIn("每张原图首次观察使用一次全图 inventory", prompt)
        self.assertIn("有效返回后直接使用其中的事实", prompt)
        self.assertIn("`reference_id`、原图 `source_sha256` 和 `roi_norm`", prompt)
        self.assertIn("已复用同一原图SHA的持久化source inventory", prompt)
        self.assertIn("某项影响建模或验收的具体事实仍为 `unknown`", prompt)
        self.assertIn("请求针对该事实的 ROI 复看，并先说明要核对的事实", prompt)
        self.assertIn("不要重复同一 key", prompt)
        self.assertIn("ROI 仍不能判定时保留 `unknown` 和候选状态", prompt)
        self.assertIn("若 inventory 整体读取失败或无有效事实，遵守读取门禁", prompt)

    def test_geometry_noop_requires_state_driven_incremental_progress(self):
        prompt = dsh_agent._prompt()

        self.assertIn("每次几何写工具返回后，依据该次返回的 state/result", prompt)
        self.assertIn("若结果为 `unchanged`/`no_op`", prompt)
        self.assertIn("geometry hash 与上一次尝试相同", prompt)
        self.assertIn("不得再次提交等价几何参数", prompt)
        self.assertIn("不得删除后重建相同主体或特征", prompt)
        self.assertIn("对照最新 `cad_get_state`、原图观察和 construction milestone", prompt)
        self.assertIn("找出有证据支持且尚未建成的下一项特征", prompt)
        self.assertIn("只做一个最小增量写入，再等它的实际结果决定后续动作", prompt)
        self.assertIn("没有证据支持的未建特征", prompt)
        self.assertIn("保留当前可用候选（如有）", prompt)

    def test_native_tool_timeout_is_configured_outside_provider_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp)
            patch_path = dsh_agent._provider_patch({
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "moonshotai/Kimi-K2.7-Code",
                "reasoning_effort": "low",
                "thinking_budget": 128,
                "api_key": "unit-test-key",
            }, session)
            text = patch_path.read_text(encoding="utf-8")
            native_tools = Path(dsh_agent.__file__).with_name(
                "dsh_native_tools.mjs").read_text(encoding="utf-8")
            self.assertIn("- id: ai-modeler-native-tools", text)
            self.assertNotIn("toolCallTimeoutMs", text)
            self.assertIn("const TIMEOUT_MS = 210_000", native_tools)
            self.assertIn("timeoutMs: TIMEOUT_MS", native_tools)
            self.assertNotIn("unit-test-key", text)

    def test_cli_intent_marks_plain_qa_as_non_target_and_direct_edit_as_target(self):
        self.assertEqual(agent_cli._infer_user_intent(
            "不要修改模型。请简要复述当前主体的尺寸。", False),
            ("query", False))
        self.assertEqual(agent_cli._infer_user_intent(
            "把长方体长度改为110 mm，其它保持不变。", False),
            ("geometry_edit", True))
        self.assertEqual(agent_cli._infer_user_intent("按图复刻。", True),
                         ("model_target", True))

    def test_failed_provider_transport_does_not_report_synthetic_zero_usage(self):
        rows = [
            {"kind": "provider_wire_error", "error_name": "TypeError"},
            {"kind": "dsh_usage", "usage": {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
        ]
        self.assertEqual(dsh_agent._usage_from_observability(rows), {})

    def test_successful_provider_usage_remains_authoritative(self):
        rows = [
            {"kind": "provider_wire_response_complete", "usage": {
                "input_tokens": 12, "output_tokens": 4, "total_tokens": 16}},
            {"kind": "dsh_usage", "usage": {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
        ]
        self.assertEqual(dsh_agent._usage_from_observability(rows), {
            "input_tokens": 12, "output_tokens": 4, "total_tokens": 16})

    def test_non_finite_or_fractional_usage_counters_are_omitted(self):
        rows = [{"kind": "provider_wire_response_complete", "usage": {
            "input_tokens": 12,
            "output_tokens": float("nan"),
            "total_tokens": float("inf"),
            "reasoning_tokens": 1.5,
            "cache_token_details": {
                "cached_tokens": 8,
                "uncached_tokens": float("inf"),
                "fractional_tokens": 0.5,
            },
        }}]
        self.assertEqual(dsh_agent._usage_from_observability(rows), {
            "input_tokens": 12,
            "cache_token_details": {"cached_tokens": 8},
        })

    def test_request_image_count_aggregation_keeps_unknown_separate_from_zero(self):
        self.assertIsNone(dsh_agent._sum_request_image_counts([]))
        self.assertEqual(dsh_agent._sum_request_image_counts([
            {"image_count": 0}, {"image_count": 2},
        ]), 2)
        self.assertEqual(dsh_agent._sum_request_image_counts([
            {"image_count": None, "request_image_count": 3},
        ]), 3)
        for value in (None, True, -1, float("nan"), float("inf"), 1.5):
            with self.subTest(value=value):
                self.assertIsNone(dsh_agent._sum_request_image_counts([
                    {"image_count": value},
                ]))

    def test_stream_watcher_emits_sparse_activity_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observability.jsonl"
            path.touch()
            messages = []

            class FakeStop:
                def __init__(self):
                    self.waits = 0

                def is_set(self):
                    return self.waits >= 3

                def wait(self, _timeout):
                    self.waits += 1
                    if self.waits <= 2:
                        with path.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps({
                                "kind": "stream_activity", "activity": "reasoning",
                                "observed_chunk_events": self.waits * 1000,
                            }) + "\n")
                    return self.is_set()

            with mock.patch.object(dsh_agent.time, "monotonic",
                                   side_effect=[0.0, 1.0, 20.0]):
                dsh_agent._stream_watcher(path, messages.append, FakeStop())

            self.assertEqual(len(messages), 2)
            self.assertTrue(messages[0].endswith("00:01"))
            self.assertTrue(messages[1].endswith("00:20"))

    def test_user_turn_manifest_retains_raw_turns_and_reference_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            session_dir = Path(temp)
            first = dsh_agent._record_user_turn(
                session_dir, "test-session", "turn-1", "按图复刻", ["sha256:abc"])
            second = dsh_agent._record_user_turn(
                session_dir, "test-session", "turn-2", "中排孔径增加10%，其它不变", [])

            self.assertEqual(first, second)
            manifest = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["session_id"], "test-session")
            self.assertEqual(manifest["turns"][0]["raw_user_prompt"], "按图复刻")
            self.assertEqual(manifest["turns"][0]["reference_ids"], ["sha256:abc"])
            self.assertEqual(manifest["turns"][1]["raw_user_prompt"],
                             "中排孔径增加10%，其它不变")
            self.assertEqual(manifest["turns"][1]["reference_ids"], [])
            self.assertEqual(manifest["turns"][1]["session_id"], "test-session")
            self.assertTrue(manifest["turns"][1]["created_at"].endswith("Z"))

    def test_reference_asset_manifest_keeps_original_for_text_only_followup(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / "session"
            source = root / "reference.png"
            Image.new("RGB", (3, 2), (120, 80, 40)).save(source, format="PNG")
            source_bytes = source.read_bytes()

            first = dsh_agent._persist_reference_assets(
                session_dir, [source], "persistent-image-session")
            stored_path = Path(first["images"][0]["path"])
            source.unlink()
            followup = dsh_agent._persist_reference_assets(
                session_dir, [], "persistent-image-session")

            self.assertEqual(first["images"][0]["id"], followup["images"][0]["id"])
            self.assertEqual(first["images"][0]["sha256"], followup["images"][0]["sha256"])
            self.assertEqual(source_bytes, stored_path.read_bytes())

    def test_separate_cli_turns_restore_visible_history_without_reuploading_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / "persistent-session"
            output_dir = root / "outputs"
            image = root / "original.png"
            image.write_bytes(b"test reference image")
            config_source = {
                "llm": {"enabled": True, "base_url": "https://example.invalid/v1",
                        "model": "test/model", "api_key": ""},
            }
            calls = []

            def fake_run(prompt, image_paths=None, conversation_history=None,
                         session_id=None, **_kwargs):
                index = len(calls) + 1
                turn_id = "turn-%d" % index
                calls.append({
                    "prompt": prompt,
                    "image_paths": list(image_paths or []),
                    "conversation_history": list(conversation_history or []),
                })
                session_dir.mkdir(parents=True, exist_ok=True)
                (session_dir / "active-turn.json").write_text(
                    json.dumps({"turn_id": turn_id}), encoding="utf-8")
                with (session_dir / "agent-observability.jsonl").open(
                        "a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"kind": "turn_start", "turn_id": turn_id}) + "\n")
                return {
                    "state": "conversation",
                    "reply": "已记录第 %d 轮的可见回复。" % index,
                    "plan": {"features": []},
                    "output_paths": {},
                    "observability": {"path": str(session_dir / "agent-observability.jsonl")},
                    "run_id": session_id,
                }

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(runtime_config, "load", return_value=config_source), \
                    mock.patch.object(dsh_agent, "_session_path", return_value=session_dir), \
                    mock.patch.object(dsh_agent, "run_dsh_agent", side_effect=fake_run), \
                    mock.patch.object(dsh_agent, "close_session"), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                first_code = agent_cli.main([
                    "--text", "按原图复刻", "--image", str(image),
                    "--session-id", "persistent-cli-session", "--output-dir", str(output_dir),
                ])
                second_code = agent_cli.main([
                    "--text", "保持原图目标，继续修正中排孔径",
                    "--session-id", "persistent-cli-session", "--output-dir", str(output_dir),
                ])

            self.assertEqual(0, first_code)
            self.assertEqual(0, second_code)
            self.assertEqual([str(image.resolve())], calls[0]["image_paths"])
            self.assertEqual([], calls[1]["image_paths"])
            self.assertEqual([], calls[0]["conversation_history"])
            self.assertEqual(1, len(calls[1]["conversation_history"]))
            self.assertEqual("按原图复刻", calls[1]["conversation_history"][0]["user_prompt"])
            self.assertEqual("已记录第 1 轮的可见回复。",
                             calls[1]["conversation_history"][0]["assistant_reply"])
            second_result = json.loads((output_dir / "turns" / "turn-2" / "result.json")
                                       .read_text(encoding="utf-8"))
            archived_second = [json.loads(line) for line in
                               Path(second_result["observability"]["copied_path"])
                               .read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["turn-2"], [row["turn_id"] for row in archived_second])

    def test_text_file_accepts_utf16_and_gb18030(self):
        with tempfile.TemporaryDirectory() as temp:
            utf16_path = Path(temp) / "utf16.txt"
            utf16_path.write_bytes("创建一个长方体".encode("utf-16"))
            gb_path = Path(temp) / "gb.txt"
            gb_path.write_bytes("创建一个螺钉".encode("gb18030"))
            self.assertEqual(agent_cli._decode_text(utf16_path), "创建一个长方体")
            self.assertEqual(agent_cli._decode_text(gb_path), "创建一个螺钉")

    def test_session_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            agent_cli._session_id("../outside")
        with self.assertRaises(ValueError):
            agent_cli._session_id(r"C:\\temp")
        self.assertEqual(agent_cli._session_id("test_session-1"), "test_session-1")

    def test_offline_and_multiple_images_call_same_dsh_agent_without_mutating_config(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            session_dir = temp_path / "persistent-session"
            output_dir = temp_path / "cli-output"
            images = [temp_path / "front.png", temp_path / "side.png"]
            for image in images:
                image.write_bytes(b"test image bytes")
            config_source = {
                "llm": {"enabled": True, "base_url": "https://example.invalid/v1",
                        "model": "test/model", "api_key": "never-print-this"},
                "online": {"enabled": True},
            }
            captured = {}

            def fake_run(prompt, image_paths=None, cfg=None, session_id=None, progress=None,
                         **_kwargs):
                captured.update(prompt=prompt, image_paths=image_paths, cfg=cfg,
                                 session_id=session_id)
                session_dir.mkdir(parents=True, exist_ok=True)
                (session_dir / "active-turn.json").write_text(
                    json.dumps({"turn_id": "turn-one"}), encoding="utf-8")
                (session_dir / "user-turns.json").write_text(
                    json.dumps({"schema_version": 1, "session_id": session_id,
                                "turns": [{"raw_user_prompt": prompt}]}), encoding="utf-8")
                (session_dir / "agent-observability.jsonl").write_text(
                    '{"kind":"turn_start","turn_id":"older-turn"}\n'
                    '{"kind":"turn_start","turn_id":"turn-one"}\n', encoding="utf-8")
                files = session_dir / "source-output"
                files.mkdir(exist_ok=True)
                paths = {}
                for key, name in agent_cli._OUTPUT_KEYS.items():
                    if key.startswith("preview_"):
                        continue
                    path = files / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"generated by mocked DSH")
                    paths[key] = str(path)
                if progress:
                    progress("已调用首个工具：cad_create_model · 首工具耗时 0.2 秒")
                return {
                    "state": "candidate_not_verified",
                    "plan": {"features": []},
                    "validation": {"valid": True},
                    "local_geometry_checks": {"geometry_state": "passed",
                                              "brep_valid": True},
                    "output_paths": paths,
                    "observability": {"path": str(session_dir / "agent-observability.jsonl")},
                    "run_id": session_id,
                    "error": None,
                }

            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.object(runtime_config, "load", return_value=config_source), \
                    mock.patch.object(dsh_agent, "_session_path", return_value=session_dir), \
                    mock.patch.object(dsh_agent, "run_dsh_agent", side_effect=fake_run), \
                    mock.patch.object(dsh_agent, "close_session") as close_session, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = agent_cli.main([
                    "--text", "创建一个100×60×20毫米长方体",
                    "--image", str(images[0]), "--image", str(images[1]),
                    "--session-id", "text-image-session",
                    "--output-dir", str(output_dir), "--offline",
                ])

            payload = json.loads(out.getvalue())
            self.assertEqual(code, 4)
            self.assertEqual(payload["state"], "candidate_not_verified")
            self.assertEqual(captured["session_id"], "text-image-session")
            self.assertEqual(captured["prompt"], "创建一个100×60×20毫米长方体")
            self.assertEqual(len(captured["image_paths"]), 2)
            self.assertFalse(captured["cfg"]["online"]["enabled"])
            self.assertTrue(config_source["online"]["enabled"])
            self.assertNotIn("never-print-this", out.getvalue())
            self.assertNotIn("never-print-this", err.getvalue())
            close_session.assert_called_once_with("text-image-session", delete_data=False)
            turn_dir = Path(payload["cli_output_dir"])
            self.assertTrue((turn_dir / "candidate.aimodel").is_file())
            self.assertTrue((turn_dir / "preview.png").is_file())
            self.assertTrue((turn_dir / "agent-observability.jsonl").is_file())
            self.assertTrue((turn_dir / "user-turns.json").is_file())
            archived_events = [json.loads(line) for line in
                               (turn_dir / "agent-observability.jsonl").read_text(
                                   encoding="utf-8").splitlines()]
            self.assertEqual(["turn-one"], [row["turn_id"] for row in archived_events])
            self.assertTrue(Path(payload["cli_result_path"]).is_file())

    def test_exit_code_zero_requires_verified_accepted_result(self):
        verified = {
            "state": "accepted",
            "validation": {"valid": True},
            "local_geometry_checks": {"geometry_state": "passed", "brep_valid": True},
            "output_paths": {"aimodel": "a", "stl": "b", "report": "c", "preview": "d"},
        }
        self.assertEqual(agent_cli._exit_code(verified), 0)
        self.assertEqual(agent_cli._exit_code({"state": "accepted"}), 4)
        self.assertEqual(agent_cli._exit_code({"state": "candidate_not_verified"}), 4)
        self.assertEqual(agent_cli._exit_code({"state": "error", "error": "tool failed"}), 3)
        self.assertEqual(agent_cli._exit_code({"state": "accepted", "delivery_warnings": ["missing"]}), 5)
        self.assertEqual(agent_cli._exit_code({"state": "conversation", "reply": "有9个孔"}), 0)

    def test_copy_failure_is_visible_and_blocks_accepted_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / "session"
            result = {
                "state": "accepted",
                "plan": {"features": [{"id": "box"}]},
                "output_paths": {"aimodel": str(root / "missing.aimodel")},
                "observability": {},
            }
            agent_cli._copy_outputs(result, root / "output", session_dir)
            self.assertTrue(result["delivery_warnings"])
            self.assertNotEqual(agent_cli._exit_code(result), 0)

    def test_observability_copy_failure_does_not_copy_all_session_turns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / "session"
            session_dir.mkdir()
            (session_dir / "active-turn.json").write_text(
                json.dumps({"turn_id": "current-turn"}), encoding="utf-8")
            source = session_dir / "agent-observability.jsonl"
            source.write_text(
                '{"kind":"provider_wire_request","turn_id":"prior-turn"}\n'
                '{"kind":"provider_wire_request","turn_id":"current-turn"}\n',
                encoding="utf-8")
            target = root / "output" / "agent-observability.jsonl"
            original_read_text = Path.read_text

            def fail_source_read(path, *args, **kwargs):
                if path == source:
                    raise PermissionError("simulated log read failure")
                return original_read_text(path, *args, **kwargs)

            result = {"output_paths": {}, "observability": {"path": str(source)}}
            with mock.patch.object(Path, "read_text", new=fail_source_read):
                agent_cli._copy_outputs(result, target.parent, session_dir)

            self.assertFalse(target.exists())
            self.assertIsNone(result["observability"]["copied_path"])
            self.assertIsNone(result["observability"]["copied_event_count"])
            self.assertEqual("PermissionError", result["observability"]["copy_error"])

    def test_close_session_without_delete_keeps_persistent_session_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temp}):
                session_dir = dsh_agent._session_path("persistent-session")
                output = session_dir / "output" / "candidate.aimodel"
                output.parent.mkdir(parents=True)
                output.write_text("keep", encoding="utf-8")
                state = session_dir / "cad-state.json"
                state.write_text("{}", encoding="utf-8")
                dsh_agent.close_session("persistent-session", delete_data=False)
                self.assertEqual(output.read_text(encoding="utf-8"), "keep")
                self.assertTrue(state.is_file())

    def test_cli_dispatch_does_not_open_gui_for_unknown_args(self):
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch("ai_modeler.ui.app.main") as gui_main, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dispatch_main(["--not-a-command"])
        self.assertEqual(code, 2)
        self.assertIn("用法:", err.getvalue())
        gui_main.assert_not_called()

    def test_no_command_remains_gui_default(self):
        with mock.patch("ai_modeler.ui.app.main") as gui_main:
            self.assertEqual(dispatch_main([]), 0)
            gui_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
