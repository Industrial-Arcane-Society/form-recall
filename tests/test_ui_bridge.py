"""Contract and document tests for the Tauri JSONL bridge."""
from __future__ import annotations

import io
import json

from ai_modeler.core import docmodel as DM
from ai_modeler.ui import bridge as ui_bridge


class InlinePool:
    """Keep JSONL protocol tests deterministic while exercising serve_stdio."""

    def __init__(self, *args, **kwargs):
        pass

    def submit(self, function, *args):
        function(*args)

    def shutdown(self, *args, **kwargs):
        pass


def test_jsonl_handshake_pairs_ids_and_keeps_stdout_clean(monkeypatch):
    protocol = io.StringIO()
    diagnostics = io.StringIO()
    monkeypatch.setattr(ui_bridge.sys, "stdin", io.StringIO(
        '{"id":"hello-一","method":"system.handshake","params":{}}\n'
        '{"id":"quit","method":"app.shutdown","params":{}}\n'
    ))
    monkeypatch.setattr(ui_bridge.sys, "stdout", protocol)
    monkeypatch.setattr(ui_bridge.sys, "stderr", diagnostics)
    monkeypatch.setattr(ui_bridge.concurrent.futures, "ThreadPoolExecutor", InlinePool)
    monkeypatch.setattr(ui_bridge.Bridge, "reset_chat", lambda self: {"session_id": "closed"})
    original_dispatch = ui_bridge.Bridge.dispatch

    def noisy_dispatch(self, method, params, request_id):
        print("diagnostic must go to stderr")
        return original_dispatch(self, method, params, request_id)

    monkeypatch.setattr(ui_bridge.Bridge, "dispatch", noisy_dispatch)
    assert ui_bridge.serve_stdio() == 0

    messages = [json.loads(line) for line in protocol.getvalue().splitlines()]
    assert [message["id"] for message in messages] == ["hello-一", "quit"]
    assert messages[0]["type"] == "result"
    assert messages[0]["result"]["protocol"] == 1
    assert messages[1]["result"] == {"stopped": True}
    assert "diagnostic must go to stderr" in diagnostics.getvalue()


def test_jsonl_returns_a_structured_error_with_the_original_id(monkeypatch):
    protocol = io.StringIO()
    monkeypatch.setattr(ui_bridge.sys, "stdin", io.StringIO(
        '{"id":"bad-request-9","method":"no.such.method","params":{}}\n'
    ))
    monkeypatch.setattr(ui_bridge.sys, "stdout", protocol)
    monkeypatch.setattr(ui_bridge.concurrent.futures, "ThreadPoolExecutor", InlinePool)

    assert ui_bridge.serve_stdio() == 0

    frame = json.loads(protocol.getvalue())
    assert frame["id"] == "bad-request-9"
    assert frame["type"] == "error"
    assert frame["error"]["code"] == "ValueError"
    assert "未知方法" in frame["error"]["message"]


def test_build_events_keep_the_request_id_and_write_valid_glb(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_bridge, "preview_root", lambda: tmp_path / "本地预览 with spaces")
    output = io.StringIO()
    bridge = ui_bridge.Bridge(output_stream=output)
    bridge.doc.add(DM.Feature("box", {"length": 30, "width": 20, "height": 10}))

    result = bridge.dispatch("document.build", {}, "request-模型-7")

    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    progress = [frame for frame in frames if frame.get("event") == "job.progress"]
    changed = [frame for frame in frames if frame.get("event") == "model.changed"]
    assert progress and all(frame["request_id"] == "request-模型-7" for frame in progress)
    assert len(changed) == 1 and changed[0]["request_id"] == "request-模型-7"
    mesh_path = result["mesh_path"]
    assert "本地预览 with spaces" in mesh_path
    payload = open(mesh_path, "rb").read()
    assert payload[:4] == b"glTF"
    assert int.from_bytes(payload[4:8], "little") == 2
    assert int.from_bytes(payload[8:12], "little") == len(payload)
    json_length = int.from_bytes(payload[12:16], "little")
    assert payload[16:20] == b"JSON"
    gltf = json.loads(payload[20:20 + json_length].decode("utf-8").rstrip())
    bin_header = 20 + json_length
    assert payload[bin_header + 4:bin_header + 8] == b"BIN\x00"
    assert gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][-1] == 1.0
    assert gltf["accessors"]
    assert result["stats"]["triangles"] > 0


def test_project_round_trip_handles_chinese_paths_with_spaces(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_bridge, "preview_root", lambda: tmp_path / "预览")
    bridge = ui_bridge.Bridge(output_stream=io.StringIO())
    bridge.doc.add(DM.Feature("box", {"length": 25, "width": 15, "height": 8}))
    path = tmp_path / "中文项目 with spaces" / "模型.aimodel"

    saved = bridge.dispatch("project.save", {"path": str(path)}, "save-id")
    opened = bridge.dispatch("project.open", {"path": str(path)}, "open-id")

    assert saved["path"] == str(path)
    assert path.is_file()
    assert opened["current_path"] == str(path)
    assert len(opened["features"]) == 1
    assert opened["mesh_path"]


def test_cancel_targets_only_the_active_request():
    bridge = ui_bridge.Bridge(output_stream=io.StringIO())
    active = bridge._start_job("agent-123")

    result = bridge.dispatch("agent.cancel", {"request_id": "agent-123"}, "cancel-456")

    assert result == {"cancelled": True, "request_id": "agent-123"}
    assert active.is_set()
    assert bridge.dispatch("agent.cancel", {"request_id": "unknown"}, "cancel-789")["cancelled"] is False


def test_quick_build_cleans_up_request_after_model_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_bridge, "preview_root", lambda: tmp_path / "preview")
    bridge = ui_bridge.Bridge(output_stream=io.StringIO())
    bridge.cfg.setdefault("online", {})["enabled"] = False

    result = bridge.dispatch(
        "model.quick_build",
        {"text": "画一个 100×60×20 的长方体, 中心打直径 10 的通孔", "new_model": True},
        "quick-build-1",
    )

    assert result["state"] == "complete"
    assert result["features"]
    assert result["mesh_path"]
    assert bridge._jobs == {}
