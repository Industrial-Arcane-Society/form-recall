# -*- coding: utf-8 -*-
"""DeepSeek Harness SDK adapter for the AI Modeler CADPlan tools.

DSH owns the conversational agent/tool loop. CADPlan and the local geometry
kernel remain the authoritative model representation and renderer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import io
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ai_modeler.paths import PROJECT_ROOT
from ai_modeler.sdk import cadplan
from ai_modeler.agent import plan_state


_RUNTIME_LOCK = threading.RLock()
_RUNTIMES: dict[str, dict[str, Any]] = {}
_REASONING_TYPES = {"reasoning-delta", "thinking-delta", "reasoning", "thinking"}


def _local_data_root() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "AI Modeler" / "dsh"
    return Path.home() / "AppData" / "Local" / "AI Modeler" / "dsh"


def _yaml_string(value: str) -> str:
    # JSON double-quoted strings are valid YAML scalars and correctly escape
    # Windows paths, newlines, Unicode, and provider credentials.
    return json.dumps(str(value), ensure_ascii=False)


def _provider_patch(llm_cfg: dict[str, Any], session_dir: Path,
                    image_paths=None) -> Path:
    """Create an invocation-only DSH patch; no global DSH profile is edited."""
    base_url = str(llm_cfg.get("base_url") or "").strip().rstrip("/")
    model = str(llm_cfg.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("请先在模型设置中填写 API 地址和模型名称。")

    low = base_url.lower()
    model_low = model.lower()
    is_responses = _is_responses_endpoint(base_url) or (
        "api.deepseek.com" in low and
        model_low in {"deepseek-flash", "deepseek-v4-pro"}
    )
    compat: dict[str, Any] = {}
    if not is_responses:
        # The configured endpoint is OpenAI-compatible, not necessarily an
        # OpenAI endpoint. SiliconFlow rejects role="developer" even when the
        # model is marked as reasoning-capable; keep the system prompt in the
        # widely supported system role.
        compat["supportsDeveloperRole"] = False
    if "siliconflow" in low and "kimi" in model_low:
        compat["thinkingTokenBudgetField"] = "thinking_budget"
        # SiliconFlow's Kimi route uses the top-level `enable_thinking` toggle.
        # An empty `reasoningEfforts.off` only omits `reasoning_effort`; it does
        # not override a provider default that enables thinking. DSH's qwen
        # compatibility format maps the selected off level to
        # `enable_thinking: false`, while its budget field handles enabled levels.
        compat["thinkingFormat"] = "qwen"
    elif "deepseek" in low and not is_responses:
        compat["thinkingFormat"] = "deepseek"
    if is_responses:
        compat["supportsMaxOutputTokens"] = False
        if base_url.lower().endswith("/responses"):
            base_url = base_url[:-len("/responses")].rstrip("/")

    budget = llm_cfg.get("thinking_budget")
    provider_fields = [
        "        displayName: AI Modeler",
        "        apiKeyEnv: AIMODELER_LLM_API_KEY",
        "        api: " + ("openai-responses" if is_responses else "openai-completions"),
        "        baseURL: " + _yaml_string(base_url),
        "        retryPolicy:",
        "          mode: normal",
        "          maxRetries: 3",
        "        defaultContextWindow: 1048576",
        "        defaultMaxTokens: 65536",
        "        defaultInput: [text, image]",
        "        maxRequestImageBytes: 52428800",
        "        requestImagePixelBudget: 16777216",
        "        requestImageMaxBytes: 4194304",
        "        models:",
        "          - id: " + _yaml_string(model),
        "            name: " + _yaml_string(model),
        "            contextWindow: 1048576",
        "            maxTokens: 65536",
        "            input: [text, image]",
        "            reasoningEfforts:",
        "              off: null",
        "              minimal: minimal",
        "              low: low",
        "              medium: medium",
        "              high: high",
        "              max: high",
    ]
    if compat:
        provider_fields.append("        compat:")
        provider_fields.extend("          %s: %s" % (
                                   key,
                                   ("true" if value else "false")
                                   if isinstance(value, bool)
                                   else _yaml_string(value))
                               for key, value in compat.items())
    if "thinkingTokenBudgetField" in compat:
        try:
            selected_budget = max(128, min(32768, int(budget))) if budget is not None else None
        except (TypeError, ValueError):
            selected_budget = None
        # Keep Kimi reasoning bounded when the user selected an effort level
        # but did not set an explicit token budget. DSH repeats this budget
        # on each model call; an unbounded high-effort call can dominate a
        # small, tool-driven CAD task.
        budgets = selected_budget or 2048
        provider_fields.extend([
            "        thinkingBudgets:",
            "          minimal: %d" % max(128, min(32768, budgets // 4)),
            "          low: %d" % max(128, min(32768, budgets // 2)),
            "          medium: %d" % budgets,
            "          high: %d" % budgets,
        ])
    escaped_provider = "\n".join(provider_fields)
    patch = f"""# Generated for this AI Modeler conversation. Do not edit the global DSH profile.
- id: llm-pi-ai
  config:
    providers:
      ai-modeler:
{escaped_provider}

- id: system-prompt
  config:
    includeHarnessIdentity: false
    includeRuntimeContext: false
    personaPrefix: !!js process.env.DSH_SYSTEM_PROMPT ?? ''
    personaSuffix: ''
- id: session-log-deepseek
  disabled: true
- id: session-telemetry-otel
  disabled: true
- id: attachment-local
  disabled: true
- id: agent-instructions
  disabled: true
- id: tool-bash
  disabled: true
- id: tool-pwsh
  disabled: true
- id: tool-fs
  disabled: true
- id: tool-fs-search
  disabled: true
- id: tool-skill
  disabled: true
- id: tool-jobs
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-workflow
  disabled: true
- id: tool-todo
  disabled: true
- id: tool-goal
  disabled: true
- id: tool-ralph
  disabled: true
- id: tool-web
  disabled: true

- insert:
    - id: ai-modeler-attachment-store
      name: {_yaml_string(str((Path(__file__).with_name('dsh_attachment_store.mjs')).resolve().as_uri()))}

- insert:
    - id: ai-modeler-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: ai-modeler
        transport: stdio
        command: {_yaml_string(sys.executable)}
        args: ['-u', '-m', 'ai_modeler.agent.dsh_mcp_server']
        cwd: {_yaml_string(str(PROJECT_ROOT))}
        env:
          AI_MODELER_STATE_DIR: {_yaml_string(str(session_dir))}
          AI_MODELER_PROJECT_ROOT: {_yaml_string(str(PROJECT_ROOT))}
          AI_MODELER_ONLINE_ENABLED: {_yaml_string(str(llm_cfg.get('_online_enabled', True)).lower())}
          AI_MODELER_HAS_REFERENCES: {_yaml_string(str(llm_cfg.get('_has_references', False)).lower())}
          AI_MODELER_REFERENCE_IMAGES: {_yaml_string(json.dumps([str(Path(p).resolve()) for p in (image_paths or [])], ensure_ascii=False))}
          AI_MODELER_REVIEW_BASE_URL: {_yaml_string(base_url)}
          AI_MODELER_REVIEW_MODEL: {_yaml_string(model)}
          AIMODELER_LLM_API_KEY: !!js process.env.AIMODELER_LLM_API_KEY ?? 'sk-local'
        failOnStartupError: true

- insert:
    - id: ai-modeler-live-stream
      name: {_yaml_string(str((Path(__file__).with_name('dsh_stream_bridge.mjs')).resolve().as_uri()))}
"""
    target = session_dir / "runtime.patch.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patch, encoding="utf-8")
    return target


def _is_responses_endpoint(base_url: str) -> bool:
    path = base_url.lower().split("?", 1)[0].rstrip("/")
    return path.endswith("/responses")


def _effort(llm_cfg: dict[str, Any]) -> str | None:
    value = str(llm_cfg.get("reasoning_effort") or "").strip().lower()
    if value in {"", "auto", "default"}:
        return None
    if value in {"none", "disabled"}:
        return "off"
    if value in {"xhigh", "ultra", "max"}:
        return "max"
    if value not in {"off", "minimal", "low", "medium", "high"}:
        return None
    return value


def _session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", session_id)[:96] or uuid.uuid4().hex
    return _local_data_root() / "sessions" / safe


def _stream_path(session_dir: Path) -> Path:
    return session_dir / "assistant-stream.jsonl"


def _prompt() -> str:
    prompt_file = PROJECT_ROOT / "prompts" / "dsh_agent" / "system.md"
    return prompt_file.read_text(encoding="utf-8")


def _read_image(path_value: str) -> dict[str, str]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("参考图片不存在：%s" % path)
    data = path.read_bytes()
    if len(data) > 25 * 1024 * 1024:
        raise ValueError("参考图片超过 25 MiB：%s" % path.name)
    try:
        from PIL import Image, ImageOps
    except Exception as exc:
        raise RuntimeError("图片建模需要 Pillow；请运行 scripts\\01_安装依赖.ps1。") from exc

    max_pixels = 16_777_216
    max_dimension = 8192
    max_encoded_bytes = 4 * 1024 * 1024
    with Image.open(path) as opened:
        image_format = str(opened.format or "").upper()
        if image_format not in {"PNG", "JPEG", "JPG", "WEBP", "GIF"}:
            raise ValueError("不支持的参考图片格式：%s" % (image_format or path.name))
        if opened.width < 1 or opened.height < 1 or opened.width > max_dimension \
                or opened.height > max_dimension or opened.width * opened.height > 64_000_000:
            raise ValueError("参考图片尺寸超过支持范围：%s" % path.name)
        orientation = opened.getexif().get(274, 1)
        had_profile = bool(opened.info.get("icc_profile"))
        transparency = "A" in opened.getbands() or "transparency" in opened.info
        image = ImageOps.exif_transpose(opened)
        image.load()
        has_alpha = transparency or "A" in image.getbands()

        # Preserve already-small, well-formed PNG/JPEG bytes exactly. Other
        # inputs and oversized images are normalized before DSH attachment
        # admission so the provider's declared byte/pixel budgets are real.
        can_passthrough = (
            image_format in {"PNG", "JPEG", "JPG"}
            and orientation == 1
            and not had_profile
            and len(data) <= max_encoded_bytes
            and image.width * image.height <= max_pixels
            and image.mode in {"RGB", "RGBA"}
        )
        if can_passthrough:
            output = data
            mime = "image/jpeg" if image_format in {"JPEG", "JPG"} else "image/png"
        else:
            image = image.convert("RGBA" if has_alpha else "RGB")
            scale = min(1.0, (max_pixels / float(image.width * image.height)) ** 0.5,
                        max_dimension / float(image.width),
                        max_dimension / float(image.height))
            if scale < 1:
                size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                image = image.resize(size, Image.Resampling.LANCZOS)

            output = b""
            for _ in range(14):
                candidates = []
                if has_alpha:
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG", optimize=True, compress_level=9)
                    candidates.append(buffer.getvalue())
                else:
                    for quality in (92, 86, 80, 74, 68):
                        buffer = io.BytesIO()
                        image.save(buffer, format="JPEG", quality=quality,
                                   optimize=True, progressive=True, subsampling=0)
                        candidates.append(buffer.getvalue())
                        if len(candidates[-1]) <= max_encoded_bytes:
                            break
                output = min(candidates, key=len)
                if len(output) <= max_encoded_bytes:
                    break
                next_size = (max(1, int(image.width * 0.84)),
                             max(1, int(image.height * 0.84)))
                if next_size == image.size or next_size == (1, 1):
                    break
                image = image.resize(next_size, Image.Resampling.LANCZOS)
            if not output or len(output) > max_encoded_bytes:
                raise ValueError("参考图片无法压缩到模型接口允许的大小：%s" % path.name)
            mime = "image/png" if has_alpha else "image/jpeg"
    return {"type": "image", "data": base64.b64encode(output).decode("ascii"),
            "mimeType": mime}


def _seed_state(session_dir: Path, prompt: str, current_plan, current_document) -> None:
    state_path = session_dir / "cad-state.json"
    if state_path.exists():
        return
    plan = current_plan if isinstance(current_plan, dict) else None
    if isinstance(current_document, dict) and current_document.get("features"):
        plan = plan_state.from_document(current_document, previous_plan=plan)
    state = {
        "plan": plan,
        "revision": 0,
        "last_preview": None,
        "preview_revision": None,
        "preview_generation": 0,
        "previews": [],
        "visual_review": None,
        "visual_reviews": {},
        "unresolved_findings": [],
        "has_references": False,
        "actions": [],
        "output_paths": {},
        "finished": False,
        "finish_state": None,
        "source_prompt": str(prompt or ""),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(state_path, state)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _event_payload(notification: Any) -> tuple[str, dict[str, Any]] | None:
    method = getattr(notification, "method", None)
    payload = getattr(notification, "payload", None)
    if isinstance(notification, dict):
        method = notification.get("method")
        payload = notification.get("payload") or notification.get("params") or {}
    if not isinstance(payload, dict):
        return None
    return (str(method or ""), payload)


def _iter_text(value: Any):
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            yield value["text"]
        for child in value.values():
            yield from _iter_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_text(child)


def _notify_progress(notification, progress, session_id, session_dir, on_preview,
                     observed_preview):
    parsed = _event_payload(notification)
    if not parsed:
        return
    method, payload = parsed
    if method != "session.event":
        if method in {"agent/status", "session.status"}:
            status = str(payload.get("status") or "")
            if status:
                progress("DSH 状态：" + status)
        return
    event = payload.get("event")
    if not isinstance(event, dict):
        return
    event_type = str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event_type == "tool/call":
        name = str(data.get("name") or data.get("toolName") or "工具")
        progress("Agent 工具调用：" + name)
    elif event_type == "tool/result":
        result = data.get("result") or data.get("content") or data
        content = "\n".join(_iter_text(result))
        match = re.search(r"AI_MODELER_PROGRESS:(.+)", content)
        if match:
            progress("Agent 决策摘要：" + match.group(1).strip()[:400])
        _publish_saved_preview(session_dir, on_preview, observed_preview)


def _publish_saved_preview(session_dir, on_preview, observed_preview):
    if on_preview is None:
        return
    state_path = session_dir / "cad-state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        preview = data.get("last_preview")
        plan = data.get("plan")
        key = (data.get("revision"), (preview or {}).get("preview_image"), (preview or {}).get("preview_generation"))
        if not isinstance(preview, dict) or not isinstance(plan, dict):
            return
        if key == observed_preview[0]:
            return
        observed_preview[0] = key
        on_preview(plan, preview, "DSH 实时预览 · rev %s" % data.get("revision"))
    except Exception:
        return


def _stream_watcher(path: Path, progress, stop: threading.Event) -> None:
    offset = 0
    text_chars = 0
    last_emit = 0.0
    pending_reasoning_chars = 0
    reasoning_chars = 0
    while not stop.is_set():
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as stream:
                    stream.seek(offset)
                    while True:
                        line = stream.readline()
                        if not line:
                            break
                        offset = stream.tell()
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(row, dict):
                            continue
                        # The bridge writes counts only. Read the legacy frame
                        # shape defensively for a runtime that was already open.
                        frame = row.get("frame") if isinstance(row.get("frame"), dict) else {}
                        chunk = frame.get("chunk") if isinstance(frame.get("chunk"), dict) else {}
                        kind = str(row.get("kind") or chunk.get("type") or "")
                        try:
                            chars = max(0, int(row.get("characters", 0)))
                        except (TypeError, ValueError):
                            chars = 0
                        if not chars:
                            chars = len(str(chunk.get("text") or chunk.get("delta") or ""))
                        if kind in _REASONING_TYPES and chars:
                            pending_reasoning_chars += chars
                            reasoning_chars += chars
                        elif kind == "text-delta" and chars:
                            text_chars += chars
                            if text_chars >= 96:
                                progress("模型流式状态：生成答复")
                                text_chars = 0
                        now = time.monotonic()
                        if pending_reasoning_chars and (pending_reasoning_chars >= 180
                                                       or now - last_emit >= 0.25):
                            progress("模型思考流：已接收增量 · 累计约 %s 字符" %
                                     format(reasoning_chars, ","))
                            pending_reasoning_chars = 0
                            last_emit = now
        except (OSError, ValueError):
            pass
        if pending_reasoning_chars and time.monotonic() - last_emit >= 0.25:
            progress("模型思考流：已接收增量 · 累计约 %s 字符" %
                     format(reasoning_chars, ","))
            pending_reasoning_chars = 0
            last_emit = time.monotonic()
        stop.wait(0.08)


def _runtime_for(session_id: str, llm_cfg: dict[str, Any], session_dir: Path,
                 image_paths=None):
    try:
        from deepseek_harness import DeepSeekHarness
    except Exception as exc:
        raise RuntimeError(
            "未安装 DeepSeek Harness Python SDK。请运行 scripts\\01_安装依赖.ps1；"
            "SDK 运行时不依赖全局 dsh 安装。") from exc

    config_fingerprint = hashlib.sha256(json.dumps({
        "base_url": llm_cfg.get("base_url"),
        "model": llm_cfg.get("model"),
        "reasoning_effort": llm_cfg.get("reasoning_effort"),
        "thinking_budget": llm_cfg.get("thinking_budget"),
        "api_key_hash": hashlib.sha256(
            str(llm_cfg.get("api_key") or "").encode("utf-8")).hexdigest(),
        "online": llm_cfg.get("_online_enabled", True),
        "reference_images": [str(Path(path).resolve())
                             for path in (image_paths or [])],
    }, sort_keys=True).encode("utf-8")).hexdigest()
    with _RUNTIME_LOCK:
        current = _RUNTIMES.get(session_id)
        if current and current.get("fingerprint") == config_fingerprint:
            return current["harness"]
        if current:
            _close_runtime_entry(current)
        session_dir.mkdir(parents=True, exist_ok=True)
        stream_path = _stream_path(session_dir)
        stream_path.write_text("", encoding="utf-8")
        patch = _provider_patch(llm_cfg, session_dir, image_paths)
        runtime_home = session_dir / "dsh-home"
        env = {
            "AIMODELER_LLM_API_KEY": str(llm_cfg.get("api_key") or "sk-local"),
            "DSH_HOME": str(runtime_home),
            "DSH_SYSTEM_PROMPT": _prompt(),
            "DSH_CONTEXT_WINDOW": "1048576",
            "DSH_TELEMETRY_DISABLED": "1",
            "AI_MODELER_STREAM_FILE": str(stream_path),
        }
        harness = DeepSeekHarness(
            profile="sdk",
            provider="ai-modeler",
            model=str(llm_cfg.get("model") or ""),
            reasoning_effort=_effort(llm_cfg),
            dsh_home=str(runtime_home),
            cwd=str(PROJECT_ROOT),
            runtime_cwd=str(PROJECT_ROOT),
            patches=(str(patch),),
            env=env,
            request_timeout_seconds=None,
            shutdown_timeout_seconds=1.0,
        )
        _RUNTIMES[session_id] = {
            "harness": harness, "fingerprint": config_fingerprint,
            "session_dir": session_dir, "close_lock": threading.Lock(),
            "closed": False,
        }
        return harness


def run_dsh_agent(prompt: str, image_paths=None, cfg=None, session_id=None,
                  progress: Callable[[str], None] | None = None, cancel_event=None,
                  current_plan=None, current_document=None, on_preview=None):
    """Run one durable DSH turn with CADPlan-backed MCP tools."""
    image_paths = list(image_paths or [])
    session_id = str(session_id or ("ai-modeler-" + uuid.uuid4().hex))
    cfg = cfg if isinstance(cfg, dict) else {}
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    llm_cfg = dict(llm_cfg)
    llm_cfg["_online_enabled"] = (cfg.get("online", {}).get("enabled", True)
                                  if isinstance(cfg.get("online"), dict) else True)
    llm_cfg["_has_references"] = bool(image_paths)
    session_dir = _session_path(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _seed_state(session_dir, prompt, current_plan, current_document)
    state_path = session_dir / "cad-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("CAD 会话状态文件损坏；原文件已保留：%s" % state_path) from exc
    if not isinstance(state, dict):
        raise RuntimeError("CAD 会话状态文件根节点无效；原文件已保留：%s" % state_path)
    # A user retry starts a fresh finite retry budget, while repeated failures
    # inside one DSH turn cannot spin forever on the same review request.
    state["reference_similarity_attempts"] = []
    state["reference_similarity_error"] = None
    if isinstance(current_document, dict) and current_document.get("features"):
        imported_plan = plan_state.from_document(
            current_document, previous_plan=state.get("plan") or current_plan)
        state["plan"] = imported_plan
        state["revision"] = int(state.get("revision") or 0) + 1
        state["last_preview"] = None
        state["preview_revision"] = None
        state["previews"] = []
        state["visual_review"] = None
        state["visual_reviews"] = {}
        state["unresolved_findings"] = []
        state["output_paths"] = {}
        state["finished"] = False
        state["finish_state"] = None
    state["has_references"] = bool(image_paths or state.get("has_references"))
    _write_json(state_path, state)

    harness = _runtime_for(session_id, llm_cfg, session_dir, image_paths)
    blocks = [{"type": "text", "text": str(prompt or "请根据参考图建立模型。")}]
    for index, image_path in enumerate(image_paths, 1):
        blocks.append({"type": "text", "text": "参考图 %d：%s" %
                       (index, Path(image_path).name)})
        blocks.append(_read_image(image_path))

    if progress:
        progress("正在启动 DSH SDK 多模态 Agent 会话。")
        effort = _effort(llm_cfg)
        progress("模型等待中：DSH SDK · 推理强度 %s" % (effort or "服务端默认"))

    observer = [None]
    stream_path = _stream_path(session_dir)
    stream_path.write_text("", encoding="utf-8")
    stop_stream = threading.Event()
    stream_thread = threading.Thread(
        target=_stream_watcher,
        args=(stream_path, progress or (lambda _message: None), stop_stream),
        daemon=True,
    )
    stream_thread.start()
    start_actions = len(state.get("actions") or [])
    cancellation = threading.Event()

    def on_notification(notification):
        _notify_progress(notification, progress or (lambda _message: None),
                         session_id, session_dir, on_preview, observer)

    def cancellation_watch():
        while not cancellation.wait(0.1):
            if cancel_event is not None and cancel_event.is_set():
                with _RUNTIME_LOCK:
                    entry = _RUNTIMES.get(session_id)
                if entry:
                    _close_runtime_entry(entry)
                return

    cancel_thread = threading.Thread(target=cancellation_watch, daemon=True)
    cancel_thread.start()
    try:
        result = harness.run(blocks, session_id=session_id,
                             on_notification=on_notification)
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            result = None
        else:
            with _RUNTIME_LOCK:
                failed_entry = _RUNTIMES.get(session_id)
                if failed_entry and failed_entry.get("harness") is harness:
                    _RUNTIMES.pop(session_id, None)
            if failed_entry:
                _close_runtime_entry(failed_entry)
            raise
    finally:
        cancellation.set()
        cancel_thread.join(timeout=0.5)
        stop_stream.set()
        stream_thread.join(timeout=0.4)
        try:
            stream_path.unlink(missing_ok=True)
        except OSError:
            pass
    if cancel_event is not None and cancel_event.is_set():
        close_session(session_id, delete_data=False)

    if progress:
        progress("模型响应已返回：正在保存并整理 CADPlan 与本轮工具结果。")

    try:
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        final_state = {}
    plan = final_state.get("plan")
    preview = final_state.get("last_preview") or {}
    review = final_state.get("visual_review") or {}
    revision = final_state.get("revision")
    finished = bool(final_state.get("finished"))
    finish_state = final_state.get("finish_state")
    review_current = (review.get("revision") == revision) if isinstance(review, dict) else False
    issues = review.get("issues") or [] if isinstance(review, dict) else []
    unresolved = list(final_state.get("unresolved_findings") or [])
    actions = final_state.get("actions") or []
    turn_actions = actions[start_actions:]
    geometry_action = any(item.get("tool") in {
        "cad_create_model", "cad_add_feature", "cad_update_feature",
        "cad_remove_feature", "cad_preview_model", "cad_record_visual_review",
        "cad_finish_model",
    } for item in turn_actions)
    finish_this_turn = any(item.get("tool") == "cad_finish_model"
                           for item in turn_actions)
    state_name = (finish_state or "accepted") if finished and finish_this_turn else (
        "conversation" if not geometry_action else
        ("reviewed" if review_current and not unresolved else
         ("previewed" if preview else "conversation")))
    if not isinstance(plan, dict):
        plan = None
        state_name = "conversation"
    finish_reason = getattr(result, "finish_reason", None) if result else None
    failure_message = None
    if finish_reason == "error":
        for event in reversed(getattr(result, "events", []) or []):
            if not isinstance(event, dict) or event.get("type") != "turn/end":
                continue
            data = event.get("data")
            reason = data.get("reason") if isinstance(data, dict) else None
            error = reason.get("error") if isinstance(reason, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or "模型请求失败")
                code = str(error.get("code") or "")
                failure_message = ("DSH 模型请求失败：%s%s" %
                                   (message, "（%s）" % code if code else ""))
            else:
                failure_message = "DSH 模型请求失败；请查看运行记录中的 turn/end 错误。"
            break
        state_name = "error"
    response = str(getattr(result, "final_response", "") or "").strip() if result else ""
    if not response:
        if failure_message:
            response = failure_message
        elif cancel_event is not None and cancel_event.is_set():
            response = "已停止 DSH 本轮；保留当前已生成的 CADPlan 与预览。"
        elif state_name == "accepted":
            response = "已完成当前候选并保存本地工程与网格。"
        elif state_name == "candidate_with_open_findings":
            response = "已保存本地候选；视觉复核仍有明确未解决项，结果标记为待确认。"
        else:
            response = "本轮 DSH 会话已结束。"

    turn_actions = actions[start_actions:]
    tool_calls = sum(1 for item in turn_actions if item.get("kind") == "tool")
    web_calls = sum(1 for item in turn_actions if item.get("kind") == "web_search")
    preview_calls = sum(1 for item in turn_actions if item.get("tool") == "cad_preview_model")
    review_rounds = sum(1 for item in turn_actions if item.get("tool") == "cad_record_visual_review")
    output_paths = final_state.get("output_paths") or {}
    validation = cadplan.validate_plan(plan) if isinstance(plan, dict) else None
    events = getattr(result, "events", []) if result is not None else []
    model_call_count = sum(1 for event in events
                           if isinstance(event, dict) and
                           event.get("type") == "assistant/message")
    return {
        "state": state_name,
        "plan": plan,
        "validation": validation,
        "preview": preview,
        "review": review if review_current else None,
        "open_multiview_findings": unresolved,
        "reply": response,
        "finish_reason": finish_reason,
        "error": failure_message,
        "output_paths": output_paths,
        "output_warning": final_state.get("output_warning"),
        "tool_calls": tool_calls,
        "web_search_calls": web_calls,
        "preview_calls": preview_calls,
        "review_rounds": review_rounds,
        "repair_cycles": sum(1 for item in turn_actions
                             if item.get("tool") in {"cad_update_feature", "cad_remove_feature"}),
        "model_call_count": model_call_count,
        "model_calls": [{
            "reasoning_effort": _effort(llm_cfg),
            "provider": "ai-modeler",
            "model": llm_cfg.get("model"),
            "image_parts_in_request": len(image_paths),
        } for _ in range(model_call_count)] if model_call_count else [],
        "image_parts_in_request": len(image_paths),
        "llm_usage": {},
        "routing": {"action": "dsh_agent", "review_mode":
                    "multimodal" if image_paths else "local_geometry"},
        "run_record": str(session_dir / "cad-state.json"),
        "run_id": session_id,
        "dimension_assumptions": [
            {"feature_id": feature.get("id"), "parameter": key, **source}
            for feature in (plan.get("features", []) if isinstance(plan, dict) else [])
            for key, source in (feature.get("sources", {}) or {}).items()
            if isinstance(source, dict) and source.get("type") == "image_estimate"
        ],
        "local_geometry_checks": (preview.get("local_geometry_checks")
                                  if isinstance(preview, dict) else None),
    }


def _close_runtime_entry(entry: dict[str, Any]) -> None:
    lock = entry.setdefault("close_lock", threading.Lock())
    with lock:
        if entry.get("closed"):
            return
        entry["closed"] = True
        try:
            entry["harness"].close()
        except Exception:
            pass


def _clear_session_working_data(session_dir: Path) -> None:
    """Remove conversation state and DSH attachments, retaining saved outputs."""
    for child in session_dir.iterdir() if session_dir.exists() else ():
        if child.name == "output":
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass


def close_session(session_id: str | None, delete_data: bool = True) -> None:
    if not session_id:
        return
    with _RUNTIME_LOCK:
        entry = _RUNTIMES.pop(str(session_id), None)
    if entry:
        _close_runtime_entry(entry)
    if delete_data:
        _clear_session_working_data(
            entry["session_dir"] if entry else _session_path(str(session_id)))


def close_all() -> None:
    with _RUNTIME_LOCK:
        entries = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    for entry in entries:
        _close_runtime_entry(entry)
        _clear_session_working_data(entry["session_dir"])
