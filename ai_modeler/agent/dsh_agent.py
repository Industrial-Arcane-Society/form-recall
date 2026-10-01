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
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_modeler.paths import PROJECT_ROOT
from ai_modeler.sdk import cadplan
from ai_modeler.agent import plan_state
from ai_modeler.parts.library import retrieval as knowledge_retrieval


_RUNTIME_LOCK = threading.RLock()
_RUNTIMES: dict[str, dict[str, Any]] = {}
_REASONING_TYPES = {"reasoning-delta", "thinking-delta", "reasoning", "thinking"}


def _local_data_root() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "AI Modeler" / "dsh"
    return Path.home() / "AppData" / "Local" / "AI Modeler" / "dsh"


def _dsh_rc2_bin() -> Path:
    """Resolve the locally installed pinned npm DSH RC2 CLI shim."""
    package_root = Path(os.environ.get("AI_MODELER_DSH_NODE_MODULES") or
                        (_local_data_root().parent / "dsh-cli" / "node_modules")).resolve()
    cli = package_root / ".bin" / ("dsh.cmd" if os.name == "nt" else "dsh")
    if not cli.is_file():
        raise RuntimeError(
            "未安装 DSH 0.2.0-rc.2 runtime；请重新运行 scripts\\01_安装依赖.ps1。")
    return cli


def _yaml_string(value: str) -> str:
    # JSON double-quoted strings are valid YAML scalars and correctly escape
    # Windows paths, newlines, Unicode, and provider credentials.
    return json.dumps(str(value), ensure_ascii=False)


def _provider_patch(llm_cfg: dict[str, Any], session_dir: Path,
                    reference_manifest_path: Path | None = None) -> Path:
    """Create an invocation-only DSH patch; no global DSH profile is edited."""
    base_url = str(llm_cfg.get("base_url") or "").strip().rstrip("/")
    model = str(llm_cfg.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("请先在模型设置中填写 API 地址和模型名称。")
    reference_manifest_path = Path(reference_manifest_path or
                                   _reference_manifest_path(session_dir)).resolve()
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

    from ai_modeler.agent import llm
    request_profile = llm.resolve_request_profile(llm_cfg, "construction")
    def positive_int(value):
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    verified_context = positive_int(request_profile.get("context_window"))
    configured_context = positive_int(llm_cfg.get("dsh_context_window"))
    configured_output_cap = positive_int(
        llm_cfg.get("dsh_output_cap", llm_cfg.get("max_tokens")))
    # DSH requires positive capacities even when a provider does not publish
    # them. Label framework bounds as operational settings, never as provider
    # capabilities; user values on this provider profile override the fallback.
    runtime_context_capacity = verified_context or configured_context or 262144
    runtime_output_capacity = configured_output_cap or 32768
    capacity_policy = {
        "context_window": {
            "value": runtime_context_capacity,
            "source": "verified_provider_capability" if verified_context else
                      "user_configured_operational_capacity" if configured_context else
                      "dsh_operational_fallback",
            "provider_capability_known": verified_context is not None,
        },
        "output_cap": {
            "value": runtime_output_capacity,
            "source": "user_configured_operational_cap" if configured_output_cap else
                      "dsh_operational_fallback",
            "provider_maximum_known": False,
        },
    }
    _write_json(session_dir / "runtime-capacity-policy.json", capacity_policy)
    model_fields = [
        "          - id: " + _yaml_string(model),
        "            name: " + _yaml_string(model),
        "            contextWindow: %d" % runtime_context_capacity,
        "            maxTokens: %d" % runtime_output_capacity,
    ]
    provider_suffix = []
    if request_profile.get("vision_supported") is True:
        provider_suffix.extend([
            "        defaultInput: [text, image]",
            "        maxRequestImageBytes: 20971520",
            "        requestImagePixelBudget: 64000000",
            "        requestImageMaxBytes: 20971520",
        ])
        model_fields.append("            input: [text, image]")
    if request_profile.get("reasoning_supported") is True:
        effort = request_profile.get("reasoning_effort")
        effort_map = {"off": None}
        if request_profile.get("reasoning_control") == "toggle_and_budget":
            # SiliconFlow's Kimi Chat Completions route maps DSH's low level to
            # the documented binary enable_thinking switch. Do not claim that
            # high/max are distinct wire levels when the route exposes only a
            # toggle; an optional explicit thinking_budget is added separately.
            effort_map["low"] = "enabled"
        else:
            for supported_effort in request_profile.get("supported_efforts") or []:
                if supported_effort not in {"none", "off"}:
                    effort_map[supported_effort] = supported_effort
        model_fields.append("            reasoningEfforts:")
        model_fields.extend(
            "              %s: %s" % (key, "null" if value is None else _yaml_string(value))
            for key, value in effort_map.items())
        budget = request_profile.get("thinking_budget")
        if budget is not None and effort and effort != "none":
            model_fields.extend([
                "            thinkingBudgets:",
                "              %s: %d" % (effort, int(budget)),
            ])
    provider_fields = [
        "        displayName: AI Modeler",
        "        apiKeyEnv: AIMODELER_LLM_API_KEY",
        "        api: " + ("openai-responses" if is_responses else "openai-completions"),
        "        baseURL: " + _yaml_string(base_url),
        "        retryPolicy:",
        "          mode: normal",
        "          maxRetries: 3",
        "        models:",
    ] + model_fields + provider_suffix
    if compat:
        provider_fields.append("        compat:")
        provider_fields.extend("          %s: %s" % (
                                   key,
                                   ("true" if value else "false")
                                   if isinstance(value, bool)
                                   else _yaml_string(value))
                               for key, value in compat.items())
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
    - id: ai-modeler-native-tools
      name: {_yaml_string(str((Path(__file__).with_name('dsh_native_tools.mjs')).resolve().as_uri()))}

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
    if value in {"none", "disabled", "off"}:
        # Omitting the DSH effort turns Qwen-compatible thinking off. Passing
        # the literal string "off" is truthy and its adapter would enable it.
        return None
    if value in {"xhigh", "ultra", "max"}:
        return "max"
    if value not in {"off", "minimal", "low", "medium", "high"}:
        return None
    return value


def _session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", session_id)[:96] or uuid.uuid4().hex
    return _local_data_root() / "sessions" / safe


def _stream_path(session_dir: Path) -> Path:
    return session_dir / "agent-observability.jsonl"


def _user_turns_manifest_path(session_dir: Path) -> Path:
    return session_dir / "user-turns.json"


def _record_user_turn(session_dir: Path, session_id: str, turn_id: str,
                      prompt: str, reference_ids: list[str], *,
                      intent: str | None = None,
                      affects_model_target: bool | None = None,
                      intent_source: str | None = None) -> Path:
    """Persist the user's unmodified target before the DSH turn can call tools."""
    path = _user_turns_manifest_path(session_dir)
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("用户目标会话清单损坏；原文件已保留：%s" % path) from exc
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or
                manifest.get("session_id") != session_id or
                not isinstance(manifest.get("turns"), list)):
            raise RuntimeError("用户目标会话清单结构无效；原文件已保留：%s" % path)
    else:
        manifest = {"schema_version": 1, "session_id": session_id, "turns": []}
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")
    turn = {
        "turn_id": turn_id,
        "session_id": session_id,
        "raw_user_prompt": str(prompt or ""),
        "reference_ids": [str(value) for value in reference_ids if value],
        "created_at": created_at,
        "at_epoch_ms": int(time.time() * 1000),
    }
    if intent:
        turn["intent"] = str(intent)
    if isinstance(affects_model_target, bool):
        turn["affects_model_target"] = affects_model_target
    if intent_source:
        turn["intent_source"] = str(intent_source)
    manifest["turns"].append(turn)
    _write_json(path, manifest)
    return path


def _append_observability(path: Path, row: dict[str, Any]) -> None:
    """Append a safe, structured activity row without exposing prompt content."""
    payload = {"at": time.time(), **row}
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def _read_observability(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _usage_from_observability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only actual provider/adapter usage events; absent stays absent."""
    wire_rows = [row for row in rows
                 if row.get("kind") == "provider_wire_response_complete"
                 and isinstance(row.get("usage"), dict)]
    wire_errors = any(row.get("kind") == "provider_wire_error" for row in rows)
    # DSH's adapter emits a synthetic all-zero usage event when its transport
    # failed. With no provider response body, those zeros are not measured use.
    selected_rows = wire_rows or ([] if wire_errors else [
        row for row in rows if row.get("kind") == "dsh_usage"])
    totals: dict[str, int] = {}
    cache_totals: dict[str, int] = {}
    for row in selected_rows:
        usage = row.get("usage")
        if not isinstance(usage, dict):
            continue
        for field in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"):
            value = _nonnegative_integer_count(usage.get(field))
            if value is not None:
                totals[field] = totals.get(field, 0) + value
        cache_details = usage.get("cache_token_details")
        if isinstance(cache_details, dict):
            for field, raw_value in cache_details.items():
                value = _nonnegative_integer_count(raw_value)
                if isinstance(field, str) and value is not None:
                    cache_totals[field] = cache_totals.get(field, 0) + value
    if cache_totals:
        totals["cache_token_details"] = cache_totals
    return totals


def _nonnegative_integer_count(value: Any) -> int | None:
    """Return a trustworthy whole-number counter, preserving unknown values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _sum_request_image_counts(rows: list[dict[str, Any]]) -> int | None:
    """Sum known per-request image counts; one unknown count makes the total unknown."""
    if not rows:
        return None
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            return None
        known_values = [
            count for key in ("image_count", "request_image_count")
            if (count := _nonnegative_integer_count(row.get(key))) is not None
        ]
        if not known_values or any(count != known_values[0] for count in known_values[1:]):
            return None
        total += known_values[0]
    return total


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

    max_pixels = 64_000_000
    max_dimension = 8192
    # Match the durable attachment-store limits. Smaller hard-coded DSH limits
    # forced otherwise valid user originals through unnecessary lossy resize.
    max_encoded_bytes = 20 * 1024 * 1024
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


_REFERENCE_MANIFEST_NAME = "reference-images.json"
_REFERENCE_EXTENSIONS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "JPG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}


def _reference_manifest_path(session_dir: Path) -> Path:
    return session_dir / _REFERENCE_MANIFEST_NAME


def _persist_reference_assets(session_dir: Path, image_paths, session_id: str):
    """Keep immutable, content-addressed originals and a cumulative session index.

    The per-turn model message may contain only the newly attached images, but
    Native CAD tools always read this manifest so text-only follow-ups retain
    access to every original reference from the conversation.
    """
    from PIL import Image

    manifest_path = _reference_manifest_path(session_dir)
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("参考图会话清单损坏；已保留原文件：%s" % manifest_path) from exc
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or not isinstance(manifest.get("images"), list)):
            raise RuntimeError("参考图会话清单版本或结构无效：%s" % manifest_path)
    else:
        manifest = {"schema_version": 1, "session_id": session_id, "images": []}

    asset_root = session_dir / "reference-assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    images = list(manifest.get("images") or [])
    by_id = {str(row.get("id")): row for row in images if isinstance(row, dict)}
    changed = False
    for value in image_paths or ():
        source = Path(value).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError("参考图片不存在：%s" % source)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        stable_id = "sha256:" + digest
        if stable_id in by_id:
            continue
        try:
            with Image.open(io.BytesIO(data)) as opened:
                image_format = str(opened.format or "").upper()
                media_type, extension = _REFERENCE_EXTENSIONS[image_format]
                width, height = opened.size
                opened.verify()
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError("参考图片格式无效或暂不支持：%s" % source.name) from exc
        if (width < 1 or height < 1 or width > 8192 or height > 8192
                or width * height > 64_000_000):
            raise ValueError("参考图片尺寸超过会话保存上限：%s" % source.name)
        target = asset_root / (digest + extension)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("同哈希参考图缓存校验失败：%s" % target.name)
        else:
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        row = {
            "id": stable_id,
            "sha256": digest,
            "filename": source.name,
            "width": int(width),
            "height": int(height),
            "media_type": media_type,
            "path": str(target.resolve()),
        }
        images.append(row)
        by_id[stable_id] = row
        changed = True

    if changed or not manifest_path.exists():
        manifest = {"schema_version": 1, "session_id": session_id, "images": images}
        temporary = manifest_path.with_name(manifest_path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            temporary.replace(manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def _plan_fingerprint(plan):
    if not isinstance(plan, dict):
        return None
    geometry_fingerprint = getattr(plan_state, "geometry_fingerprint", None)
    if callable(geometry_fingerprint):
        return geometry_fingerprint(plan)
    try:
        canonical = json.dumps(plan, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        "knowledge_search": None,
        "knowledge_visual_examples": [],
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
                     observed_preview, observability_path=None, run_started=None,
                     first_tool_observed=None, turn_id=None):
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
        tool_row = {"kind": "tool_start", "turn_id": turn_id, "tool": name}
        if first_tool_observed is not None and not first_tool_observed[0]:
            first_tool_observed[0] = True
            elapsed_ms = (time.monotonic() - run_started) * 1000 if run_started else None
            tool_row["first_tool_latency_ms"] = round(elapsed_ms, 1) if elapsed_ms is not None else None
            progress("已调用首个工具：%s · 首工具耗时 %.1f 秒" % (name, elapsed_ms / 1000)
                     if elapsed_ms is not None else "已调用首个工具：%s" % name)
        else:
            progress("Agent 正在调用工具：" + name)
        if observability_path:
            _append_observability(observability_path, tool_row)
    elif event_type == "tool/result":
        result = data.get("result") or data.get("content") or data
        content = "\n".join(_iter_text(result))
        match = re.search(r"AI_MODELER_PROGRESS:(.+)", content)
        if match:
            progress("Agent 进度：" + match.group(1).strip()[:240])
        tool_name = str(data.get("name") or data.get("toolName") or "工具")
        if observability_path:
            _append_observability(observability_path, {
                "kind": "tool_complete", "turn_id": turn_id, "tool": tool_name})
        _publish_saved_preview(session_dir, on_preview, observed_preview)


def _publish_saved_preview(session_dir, on_preview, observed_preview):
    if on_preview is None:
        return
    state_path = session_dir / "cad-state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        preview = data.get("last_preview")
        if not isinstance(preview, dict):
            preview = data.get("partial_preview")
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
    offset = path.stat().st_size if path.is_file() else 0
    last_activity = None
    last_activity_progress_at = 0.0
    turn_started = time.monotonic()
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
                        frame = row.get("frame") if isinstance(row.get("frame"), dict) else {}
                        chunk = frame.get("chunk") if isinstance(frame.get("chunk"), dict) else {}
                        kind = str(row.get("kind") or chunk.get("type") or "")
                        kind = kind or str(row.get("activity") or "")
                        if kind in {"dsh_request_start", "provider_wire_request"}:
                            model = row.get("model") or "当前模型"
                            images = row.get("image_count", row.get("request_image_count"))
                            effort = row.get("reasoning_effort")
                            detail = "请求 %s" % model
                            if images is not None:
                                detail += " · 本次图像 %s 张" % images
                            if effort:
                                detail += " · 推理 %s" % effort
                            progress("正在请求模型：" + detail)
                            last_activity = None
                            last_activity_progress_at = 0.0
                        elif kind == "provider_wire_response_headers":
                            elapsed = row.get("headers_elapsed_ms")
                            if isinstance(elapsed, (int, float)):
                                progress("模型服务已开始响应 · %.1f 秒" % (elapsed / 1000))
                        elif kind in {"dsh_first_stream_chunk", "provider_wire_response_complete"}:
                            elapsed = row.get("elapsed_ms", row.get("first_body_event_ms"))
                            if isinstance(elapsed, (int, float)):
                                progress("模型首个内容事件已收到 · %.1f 秒" % (elapsed / 1000))
                        elif kind == "stream_activity":
                            activity = row.get("activity")
                            now = time.monotonic()
                            if (activity != last_activity or
                                    now - last_activity_progress_at >= 15):
                                seconds = max(0, int(now - turn_started))
                                progress("模型正在分析参考图与建模约束 · %02d:%02d" %
                                         (seconds // 60, seconds % 60)
                                         if activity == "reasoning" else "模型正在生成回复")
                                last_activity = activity
                                last_activity_progress_at = now
                        elif kind == "provider_wire_error":
                            progress("模型请求失败，正在由 DSH 处理重试或返回错误。")
        except (OSError, ValueError):
            pass
        stop.wait(0.08)


def _runtime_for(session_id: str, llm_cfg: dict[str, Any], session_dir: Path,
                 image_paths=None, reference_manifest_path: Path | None = None):
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
    }, sort_keys=True).encode("utf-8")).hexdigest()
    with _RUNTIME_LOCK:
        current = _RUNTIMES.get(session_id)
        if current and current.get("fingerprint") == config_fingerprint:
            return current["harness"]
        if current:
            _close_runtime_entry(current)
        session_dir.mkdir(parents=True, exist_ok=True)
        stream_path = _stream_path(session_dir)
        stream_path.touch(exist_ok=True)
        reference_manifest_path = Path(reference_manifest_path or
                                       _reference_manifest_path(session_dir))
        patch = _provider_patch(llm_cfg, session_dir, reference_manifest_path)
        runtime_home = session_dir / "dsh-home"
        env = {
            "AIMODELER_LLM_API_KEY": str(llm_cfg.get("api_key") or "sk-local"),
            "DSH_HOME": str(runtime_home),
            "DSH_SYSTEM_PROMPT": _prompt(),
            "DSH_TELEMETRY_DISABLED": "1",
            "AI_MODELER_STREAM_FILE": str(stream_path),
            "AI_MODELER_REFERENCE_MANIFEST": str(reference_manifest_path.resolve()),
            "AI_MODELER_ACTIVE_TURN_FILE": str((session_dir / "active-turn.json").resolve()),
            "AI_MODELER_USER_TURNS_MANIFEST": str(_user_turns_manifest_path(session_dir).resolve()),
            "AI_MODELER_PROVIDER_BASE_URL": str(llm_cfg.get("base_url") or ""),
            "AI_MODELER_PROVIDER_MODEL": str(llm_cfg.get("model") or ""),
            "AI_MODELER_TRANSPORT_IMAGE_MAP": str((session_dir / "transport-image-map.json").resolve()),
            "AI_MODELER_STATE_DIR": str(session_dir.resolve()),
            "AI_MODELER_PROJECT_ROOT": str(PROJECT_ROOT),
            "AI_MODELER_PYTHON": sys.executable,
            "AI_MODELER_DSH_NODE_MODULES": str(
                Path(os.environ.get("AI_MODELER_DSH_NODE_MODULES") or
                     (_local_data_root().parent / "dsh-cli" / "node_modules")).resolve()),
            "AI_MODELER_NATIVE_TOOLS_FILE": str((session_dir / "native-tool-registration.json").resolve()),
            "AI_MODELER_ONLINE_ENABLED": str(llm_cfg.get("_online_enabled", True)).lower(),
            "AI_MODELER_HAS_REFERENCES": str(llm_cfg.get("_has_references", False)).lower(),
            "AI_MODELER_REFERENCE_IMAGES": json.dumps(
                [str(value) for value in (image_paths or [])], ensure_ascii=False),
            "AI_MODELER_REVIEW_BASE_URL": str(llm_cfg.get("base_url") or ""),
            "AI_MODELER_REVIEW_MODEL": str(llm_cfg.get("model") or ""),
            "AI_MODELER_REVIEW_REASONING_EFFORT": str(llm_cfg.get("reasoning_effort") or ""),
            "AI_MODELER_REVIEW_THINKING_BUDGET": str(
                llm_cfg.get("thinking_budget") if llm_cfg.get("thinking_budget") is not None else ""),
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
            dsh_bin=str(_dsh_rc2_bin()),
        )
        _RUNTIMES[session_id] = {
            "harness": harness, "fingerprint": config_fingerprint,
            "session_dir": session_dir, "close_lock": threading.Lock(),
            "closed": False,
        }
        return harness


def run_dsh_agent(prompt: str, image_paths=None, cfg=None, session_id=None,
                  progress: Callable[[str], None] | None = None, cancel_event=None,
                  current_plan=None, current_document=None, on_preview=None,
                  dsh_session_id: str | None = None,
                  user_intent: str | None = None,
                  affects_model_target: bool | None = None,
                  intent_source: str | None = None,
                  conversation_history: list[dict[str, Any]] | None = None):
    """Run one durable DSH turn with native Cordis CADPlan tools."""
    image_paths = list(image_paths or [])
    session_id = str(session_id or ("ai-modeler-" + uuid.uuid4().hex))
    dsh_session_id = str(dsh_session_id or session_id)
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
    reference_manifest_path = _reference_manifest_path(session_dir)
    if reference_manifest_path.is_file():
        try:
            previous_manifest = json.loads(reference_manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("参考图会话清单损坏；已保留原文件：%s" % reference_manifest_path) from exc
    else:
        previous_manifest = {"images": []}
    reference_manifest = _persist_reference_assets(session_dir, image_paths, session_id)
    requested_source_hashes = {
        hashlib.sha256(Path(path).expanduser().resolve().read_bytes()).hexdigest()
        for path in image_paths
    }
    current_reference_rows = [
        row for row in reference_manifest.get("images", [])
        if isinstance(row, dict) and row.get("sha256") in requested_source_hashes
    ]
    turn_id = uuid.uuid4().hex
    user_turns_manifest_path = _record_user_turn(
        session_dir, session_id, turn_id, prompt,
        [str(row.get("id")) for row in current_reference_rows if row.get("id")],
        intent=user_intent, affects_model_target=affects_model_target,
        intent_source=intent_source)
    llm_cfg["_has_references"] = bool(reference_manifest.get("images"))
    # A user retry starts a fresh finite retry budget, while repeated failures
    # inside one DSH turn cannot spin forever on the same review request.
    state["reference_similarity_attempts"] = []
    state["reference_similarity_error"] = None
    if isinstance(current_document, dict) and current_document.get("features"):
        imported_plan = plan_state.from_document(
            current_document, previous_plan=state.get("plan") or current_plan)
        if _plan_fingerprint(imported_plan) != _plan_fingerprint(state.get("plan")):
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
    state["has_references"] = bool(reference_manifest.get("images"))
    state["reference_images"] = [
        {key: row.get(key) for key in ("id", "sha256", "filename", "width", "height", "media_type")}
        for row in reference_manifest.get("images", []) if isinstance(row, dict)
    ]
    if progress:
        progress("正在查询本地工件知识库。")
    try:
        knowledge_result = knowledge_retrieval.search_knowledge(
            prompt, image_paths=None)
    except Exception as exc:
        # A local index problem must not prevent free-form or image modeling.
        knowledge_result = {
            "status": "provider_error", "query": str(prompt or ""),
            "matches": [], "negative_examples": [],
            "message": "本地知识检索暂时不可用；继续根据用户输入建模。",
            "provider_warnings": [str(exc)[:240]],
        }
    if progress:
        status = knowledge_result.get("status")
        hits = knowledge_result.get("matches") or []
        executable_recipes = [row for row in knowledge_result.get("modeling_recipes") or []
                              if row.get("status") == "executable" and row.get("builder")]
        if executable_recipes:
            recipe = executable_recipes[0]
            labels = ", ".join(str(row.get("model") or row.get("category") or "")
                                for row in hits[:2])
            progress("本地知识库命中可执行配方：%s%s；Agent 将直接调用配方建模。" % (
                recipe.get("name") or recipe.get("id"),
                ("（型号：%s）" % labels) if labels else ""))
        elif status in {"exact_match", "exact_prompt"}:
            labels = ", ".join(str(row.get("model") or row.get("category") or "")
                                for row in hits[:3])
            progress("本地知识库精确命中：%s。" % (labels or status))
        elif status == "similar_matches":
            progress("本地知识库找到 %d 个相似描述，仅作参考。" % len(hits))
        elif status == "image_only":
            progress("知识库没有文字命中；识图只使用本会话原图。")
        else:
            progress("本地知识库未找到可靠文字命中，继续按当前输入建模。")
    state["knowledge_search"] = knowledge_result
    state["knowledge_visual_examples"] = []
    _write_json(state_path, state)

    harness = _runtime_for(session_id, llm_cfg, session_dir, image_paths,
                           reference_manifest_path)
    blocks = [{"type": "text", "text": str(prompt or "请根据参考图建立模型。")}]
    history = [row for row in (conversation_history or [])
               if isinstance(row, dict)
               and isinstance(row.get("user_prompt"), str)
               and isinstance(row.get("assistant_reply"), str)]
    if history:
        blocks.append({"type": "text", "text": (
            "以下是同一应用会话中之前真实显示给用户的可见对话记录；只用于延续自然对话，"
            "助手回答不是新的用户尺寸约束。CAD状态以 cad_get_state 工具为准，用户目标以程序注入的"
            "authoritative user-turn manifest 为准。\n"
            + json.dumps(history, ensure_ascii=False, separators=(",", ":")))})
    blocks.append({"type": "text",
                   "text": knowledge_retrieval.format_knowledge_context(knowledge_result)})
    knowledge_image_parts = 0
    if image_paths:
        knowledge_sheet_path = session_dir / "knowledge-visual-examples.png"
        try:
            examples = knowledge_retrieval.visual_examples_for_result(knowledge_result)
            if examples:
                selected = knowledge_retrieval.build_visual_examples_contact_sheet(
                    knowledge_sheet_path, examples)
                if selected:
                    state["knowledge_visual_examples"] = [
                        {"model": row.get("model"), "category": row.get("category")}
                        for row in selected]
                    _write_json(state_path, state)
                    labels = ", ".join(str(row.get("model") or "") for row in selected)
                    blocks.append({
                        "type": "text",
                        "text": (
                            "接下来这张图是本地知识库中已有零件的真实照片/程序渲染配对样例，"
                            "不是用户目标图，也不是已排序的视觉命中。请与用户原图比较后，"
                            "只把明确相似的类别或形态作为参考，不直接照搬型号、尺寸或结构。"
                            "样例型号：%s。\n%s") % (
                                labels,
                                knowledge_retrieval.format_visual_examples_context(selected)),
                    })
                    # The raw user images stay in the reference store and are
                    # fetched on demand. Only the compact knowledge sheet is
                    # attached to this initial request, then its temporary file
                    # is removed so repeated turns do not accumulate previews.
                    blocks.append(_read_image(str(knowledge_sheet_path)))
                    knowledge_image_parts = 1
        except Exception as exc:
            knowledge_result.setdefault("provider_warnings", []).append(
                "视觉样例图无法生成：%s" % str(exc)[:200])
            state["knowledge_search"] = knowledge_result
            _write_json(state_path, state)
        finally:
            try:
                knowledge_sheet_path.unlink(missing_ok=True)
            except OSError:
                pass
    available_references = [
        "- %s (%s)" % (row.get("id"), row.get("filename"))
        for row in reference_manifest.get("images", []) if isinstance(row, dict)
    ]
    if available_references:
        blocks.append({"type": "text", "text": (
            "会话内可通过 cad_get_reference_image(reference_id, roi_norm) 重新查看用户原图或局部裁切。"
            "稳定引用列表：\n%s") % "\n".join(available_references)})
    # Original reference bytes live only in the session asset store. The DSH
    # transcript receives stable IDs; vision tools retrieve bytes on demand.

    if progress:
        progress("正在启动 DSH SDK 多模态 Agent 会话。")
        effort = _effort(llm_cfg)
        progress("模型等待中：DSH SDK · 推理强度 %s" % (effort or "服务端默认"))

    observer = [None]
    stream_path = _stream_path(session_dir)
    stream_path.touch(exist_ok=True)
    run_started = time.monotonic()
    turn_profile = None
    try:
        from ai_modeler.agent.llm import resolve_request_profile
        turn_profile = resolve_request_profile(llm_cfg, "construction")
    except Exception:
        turn_profile = None
    active_turn = {
        "turn_id": turn_id,
        "started_at_epoch_ms": int(time.time() * 1000),
        "session_id": session_id,
        "dsh_session_id": dsh_session_id,
        "provider": (turn_profile or {}).get("provider_family"),
        "model": llm_cfg.get("model"),
        "reasoning_effort": (turn_profile or {}).get("reasoning_effort"),
        "runtime_capacity_policy": (
            json.loads((session_dir / "runtime-capacity-policy.json").read_text(encoding="utf-8"))
            if (session_dir / "runtime-capacity-policy.json").is_file() else None),
        "reference_images": [
            {key: row.get(key) for key in ("id", "sha256", "filename", "width", "height")}
            for row in reference_manifest.get("images", []) if isinstance(row, dict)
        ],
        "user_turns_manifest": str(user_turns_manifest_path),
    }
    _write_json(session_dir / "active-turn.json", active_turn)
    _append_observability(stream_path, {
        "kind": "turn_start", "turn_id": turn_id,
        "model": llm_cfg.get("model"),
        "reasoning_profile": turn_profile,
        "new_reference_count": len(current_reference_rows),
        "available_reference_count": len(reference_manifest.get("images", [])),
        "source_images": [
            {"reference_id": row.get("id"), "source_sha256": row.get("sha256"),
             "width": row.get("width"), "height": row.get("height")}
            for row in current_reference_rows
        ],
    })
    stop_stream = threading.Event()
    stream_thread = threading.Thread(
        target=_stream_watcher,
        args=(stream_path, progress or (lambda _message: None), stop_stream),
        daemon=True,
    )
    stream_thread.start()
    start_actions = len(state.get("actions") or [])
    cancellation = threading.Event()

    first_tool_observed = [False]

    def on_notification(notification):
        _notify_progress(notification, progress or (lambda _message: None),
                         session_id, session_dir, on_preview, observer,
                         stream_path, run_started, first_tool_observed, turn_id)

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
        result = harness.run(blocks, session_id=dsh_session_id,
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
            active_turn_path = session_dir / "active-turn.json"
            active = json.loads(active_turn_path.read_text(encoding="utf-8"))
            active["completed_at_epoch_ms"] = int(time.time() * 1000)
            active["first_tool_observed"] = first_tool_observed[0]
            _write_json(active_turn_path, active)
        except (OSError, ValueError, TypeError):
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

    agent_self_report = response
    finish_summary = final_state.get("finish_summary") or {}
    if finished and finish_this_turn:
        independent_count = int(finish_summary.get("independent_review_pair_count") or 0)
        visual_summary = finish_summary.get("visual_evidence") or {}
        external = finish_summary.get("required_external_facts") or {}
        required_external = int(external.get("required_count") or 0)
        verified_external = int(external.get("verified_count") or 0)
        local_state = str(finish_summary.get("local_geometry_state") or "unknown")
        if finish_state == "accepted":
            response = (
                "已通过程序验收并保存模型。" if independent_count or not image_paths else
                "已通过程序验收并保存模型；本轮没有原图输入。")
        else:
            response = (
                "已保存候选，程序状态为 %s，尚未验收。" % (finish_state or "candidate_not_verified")
                + " 本地几何状态：%s；独立原图-模型复核pair：%d；参考证据覆盖：%s；"
                "外部事实来源：%d/%d项已核实；未解决finding：%d；CATIA未运行。"
                % (local_state, independent_count,
                   "已覆盖" if visual_summary.get("all_references_covered") else "未覆盖或未知",
                   verified_external, required_external,
                   int(finish_summary.get("open_finding_count") or 0)))

    turn_actions = actions[start_actions:]
    tool_calls = sum(1 for item in turn_actions if item.get("kind") == "tool")
    web_calls = sum(1 for item in turn_actions if item.get("kind") == "web_search")
    preview_calls = sum(1 for item in turn_actions if item.get("tool") == "cad_preview_model")
    review_rounds = sum(1 for item in turn_actions
                        if item.get("tool") == "cad_review_reference_similarity")
    main_self_report_count = sum(1 for item in turn_actions
                                 if item.get("tool") == "cad_record_visual_review")
    output_paths = final_state.get("output_paths") or {}
    validation = cadplan.validate_plan(plan) if isinstance(plan, dict) else None
    events = getattr(result, "events", []) if result is not None else []
    # A short grace interval lets the detached response-body observer persist
    # the provider's terminal usage event after the DSH stream has completed.
    time.sleep(0.12)
    observability_rows = [row for row in _read_observability(stream_path)
                          if row.get("turn_id") in {None, turn_id}]
    dsh_requests = [row for row in observability_rows
                    if row.get("kind") == "dsh_request_start"]
    wire_requests = [row for row in observability_rows
                     if row.get("kind") == "provider_wire_request"]
    wire_completions = [row for row in observability_rows
                        if row.get("kind") == "provider_wire_response_complete"]
    provider_finish_reasons = [
        {"request_id": row.get("request_id"), "finish_reason": row.get("finish_reason")}
        for row in wire_completions if row.get("finish_reason") is not None]
    dsh_finish_reasons = [
        {"finish_reason": row.get("finish_reason"), "elapsed_ms": row.get("elapsed_ms")}
        for row in observability_rows if row.get("kind") == "dsh_request_complete"
        and row.get("finish_reason") is not None]
    dsh_usage_rows = [row for row in observability_rows
                      if row.get("kind") == "dsh_usage"]
    observed_usage = _usage_from_observability(observability_rows)
    first_tool_rows = [row for row in observability_rows
                       if row.get("kind") == "tool_start"
                       and isinstance(row.get("first_tool_latency_ms"), (int, float))]
    first_tool_latency_ms = (first_tool_rows[0]["first_tool_latency_ms"]
                             if first_tool_rows else None)
    provider_wire_observed = bool(wire_requests)
    model_call_count = len(dsh_requests)
    call_rows = [{
        "provider": row.get("provider"),
        "model": row.get("model"),
        "reasoning_effort": row.get("reasoning_effort"),
        "dsh_request_image_count": row.get("request_image_count"),
        "capture_layer": row.get("request_capture_layer"),
        "provider_wire_observed": provider_wire_observed,
    } for row in dsh_requests]
    transport_image_rows = [image for row in wire_requests
                            for image in row.get("images", [])
                            if isinstance(image, dict)]
    return {
        "state": state_name,
        "plan": plan,
        "validation": validation,
        "preview": preview,
        "review": review if review_current else None,
        "open_multiview_findings": unresolved,
        "reply": response,
        "agent_self_report": agent_self_report if finished and finish_this_turn else None,
        "finish_reason": finish_reason,
        "error": failure_message,
        "output_paths": output_paths,
        "output_warning": final_state.get("output_warning"),
        "tool_calls": tool_calls,
        "web_search_calls": web_calls,
        "preview_calls": preview_calls,
        "review_rounds": review_rounds,
        "main_self_report_count": main_self_report_count,
        "finish_summary": finish_summary or None,
        "repair_cycles": sum(1 for item in turn_actions
                             if item.get("tool") in {"cad_update_feature", "cad_remove_feature"}),
        "model_call_count": model_call_count,
        "model_call_count_source": "dsh_llm_stream_events" if dsh_requests else "unknown",
        "model_calls": call_rows,
        "provider_attempt_count": len(wire_requests) if provider_wire_observed else None,
        "provider_finish_reasons": provider_finish_reasons,
        "dsh_finish_reasons": dsh_finish_reasons,
        "provider_wire_observed": provider_wire_observed,
        "provider_wire_capture_layer": "node_global_fetch" if provider_wire_observed else "unknown",
        "request_image_hashes": transport_image_rows,
        "image_parts_in_request": (_sum_request_image_counts(wire_requests)
                                   if provider_wire_observed else None),
        "reference_image_count": len(current_reference_rows),
        "knowledge_search": final_state.get("knowledge_search"),
        "knowledge_visual_examples": final_state.get("knowledge_visual_examples") or [],
        "knowledge_image_parts": knowledge_image_parts,
        "image_input_mode": "individual",
        "llm_usage": observed_usage or None,
        "usage_source": ("provider_response_body" if wire_completions and observed_usage
                         else "dsh_adapter_stream_event" if dsh_usage_rows and observed_usage
                         else "unknown"),
        "first_tool_latency_ms": first_tool_latency_ms,
        "observability": {
            "path": str(stream_path),
            "logical_requests": len(dsh_requests),
            "provider_attempts_observed": len(wire_requests) if provider_wire_observed else None,
            "provider_wire_status": "observed" if provider_wire_observed else
                                    "unknown (DSH stream events only)" if dsh_requests else "unknown",
        },
        "routing": {"action": "dsh_agent", "review_mode":
                    "multimodal" if image_paths else "local_geometry"},
        "run_record": str(session_dir / "cad-state.json"),
        "run_id": session_id,
        "dsh_session_id": dsh_session_id,
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
