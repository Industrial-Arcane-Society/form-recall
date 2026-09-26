# -*- coding: utf-8 -*-
"""Multimodal CAD AgentLoop for the local AI Modeler renderer.

One model-directed tool loop handles conversation, structured CADPlan editing,
preview, evidence review, repair, and completion. CADPlan remains data and the
runtime never calls CATIA. UI callbacks are optional and Tk-independent.
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import hashlib
import json
import os
import re
import time
import uuid
import copy
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from ai_modeler.sdk import cadplan
from ai_modeler.agent import image_analysis, llm, online, plan_state

ROOT = PROJECT_ROOT
PROMPT_DIR = ROOT / "prompts" / "agentloop"
MAX_REFERENCE_IMAGES = 8
MAX_CONTEXT_REFERENCE_IMAGES = 3
# Legacy helper guard only. The active model-directed loop uses the global
# tool-call safety cap rather than a fixed repair-round count.
MAX_REPAIR_CYCLES = 256
MAX_REVIEW_ROUNDS = MAX_REPAIR_CYCLES + 1
REQUIRED_REVIEW_AREAS = {
    "front": ("outline", "features"),
    "right": ("depth_setback", "through_connectivity"),
    "top": ("top_boundaries",),
}
REVIEW_VIEW_AREAS = {
    **REQUIRED_REVIEW_AREAS,
    "iso": ("overall_shape", "feature_layout", "depth_relationships"),
    "back": ("outline", "features"),
    "left": ("depth_setback", "through_connectivity"),
    "bottom": ("outline", "features"),
}
MAX_TOOL_CALLS = 256
MAX_WEB_SEARCH_CALLS = 32
MAX_TOOL_PREVIEW_CALLS = 256
MAX_CONSTRUCTION_VALIDATION_CALLS = 32
MAX_REPAIR_VALIDATION_CALLS = 24
MAX_REVIEW_PREVIEW_CALLS_PER_ROUND = 8
MAX_CONTEXT_TURNS = 128
MAX_CONTEXT_CHARS = 1000000
MAX_CONTEXT_SUMMARY_CHARS = 32000
CONTEXT_SUMMARY_BATCH_MESSAGES = 8
DEFAULT_TOTAL_TIMEOUT = 1800
DEFAULT_REQUEST_TIMEOUT = 600
RUN_CHECKPOINT_INTERVAL_SECONDS = 15.0

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "仅当用户请求涉及外部标准、材料或事实时搜索网页。图片几何以原图为准。",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {"query": {"type": "string", "minLength": 2}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cad_validate_plan",
            "description": "按 system 消息中完整的 CADPlan v1 JSON Schema 校验计划。只接受 schema_version/name/units/coordinate_system/features 等字段；特征参数放在 parameters，数值来源放在 sources。",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {"plan": {"type": "object"}},
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cad_get_multiview",
            "description": "在多视图检查阶段按需生成当前 CADPlan 的扩展视图；不可借此工具修改计划。应用随后会把图片发回给你。使用本地网格内核，不启动 CATIA。",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "plan": {"type": "object"},
                    "view_set": {"type": "string", "enum": [
                        "three", "extended", "iso", "front", "right", "top",
                        "back", "left", "bottom"]},
                },
                "required": ["plan"],
            },
        },
    },
]
REFERENCE_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_reference_inventory",
        "description": "提交仅依据原始参考图得到的精简可见面与特征清单。",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "images": {
                    "type": "array", "maxItems": 8,
                    "items": {"type": "object", "additionalProperties": False,
                              "properties": {
                                  "image": {"type": "string", "maxLength": 160},
                                  "source_view": {"type": "string", "enum": [
                                      "front", "right", "top", "iso", "back", "left", "bottom", "unknown"]},
                                  "coverage": {"type": "string", "enum": [
                                      "whole_object", "partial", "unknown"]},
                                  "hole_layout_coverage": {"type": "string", "enum": [
                                      "complete", "partial", "unknown"]},
                                  "visible_regions": {
                                      "type": "array", "maxItems": 8,
                                      "items": {"type": "object", "additionalProperties": False,
                                                "properties": {
                                                    "id": {"type": "string", "maxLength": 64},
                                                    "region": {"type": "string", "maxLength": 100},
                                                    "surface_state": {"type": "string", "enum": [
                                                        "solid_face", "recessed_solid_face",
                                                        "background_opening", "unknown"]},
                                                    "evidence": {"type": "string", "maxLength": 120},
                                                    "features": {
                                                        "type": "array", "maxItems": 12,
                                                        "items": {"type": "object", "additionalProperties": False,
                                                                  "properties": {
                                                                      "kind": {"type": "string", "maxLength": 32},
                                                                      "quantity": {"type": "integer", "minimum": 0},
                                                                      "arrangement": {"type": "string", "maxLength": 100},
                                                                      "evidence": {"type": "string", "maxLength": 120},
                                                                      "relative_size_order": {
                                                                          "type": "array", "maxItems": 12,
                                                                          "items": {"type": "string", "enum": [
                                                                              "large", "medium", "small", "same", "unknown"]},
                                                                      },
                                                                      "size_order_axis": {"type": "string", "enum": [
                                                                          "vertical_top_to_bottom", "vertical_bottom_to_top",
                                                                          "horizontal_left_to_right", "horizontal_right_to_left",
                                                                          "unknown", "not_applicable"]},
                                                                      "size_order_confidence": {"type": "string", "enum": [
                                                                          "high", "medium", "low", "unknown"]},
                                                                  }, "required": ["kind", "quantity", "arrangement", "evidence",
                                                                                 "relative_size_order", "size_order_axis",
                                                                                 "size_order_confidence"]},
                                                    },
                                                }, "required": ["id", "region", "surface_state", "evidence", "features"]},
                                  },
                                  "uncertainties": {"type": "array", "maxItems": 6,
                                                    "items": {"type": "string", "maxLength": 120}},
                              }, "required": ["image", "source_view", "coverage",
                                             "hole_layout_coverage", "visible_regions", "uncertainties"]},
                },
                "cross_view_notes": {"type": "array", "maxItems": 8,
                                     "items": {"type": "string", "maxLength": 120}},
            },
            "required": ["images", "cross_view_notes"],
        },
    },
}
REVIEW_SUBMISSION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_multiview_review",
        "description": (
            "提交当前单视图对原始同方向参考图的复核结果。只提交可见证据和可定位差异，"
            "不要输出长篇说明。"
        ),
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "reference_images": {
                    "type": "array", "maxItems": 4,
                    "items": {"type": "object", "additionalProperties": False,
                              "properties": {
                                  "image": {"type": "string", "maxLength": 160},
                                  "source_view": {"type": "string", "enum": [
                                      "front", "right", "top", "iso", "back", "left", "bottom", "unknown"]},
                                  "comparable": {"type": "boolean"},
                                  "visible_evidence": {"type": "string", "maxLength": 120},
                              }, "required": ["image", "source_view", "comparable", "visible_evidence"]},
                },
                "checks": {
                    "type": "array", "maxItems": 16,
                    "items": {"type": "object", "additionalProperties": False,
                              "properties": {
                                  "id": {"type": "string", "maxLength": 100},
                                  "view": {"type": "string", "enum": [
                                      "front", "right", "top", "iso", "back", "left", "bottom"]},
                                  "area": {"type": "string", "maxLength": 48},
                                  "feature_group_id": {"type": "string", "maxLength": 64},
                                  "category": {"type": "string", "maxLength": 32},
                                  "status": {"type": "string", "enum": [
                                      "match", "mismatch", "partial", "unknown"]},
                                  "item": {"type": "string", "maxLength": 80},
                                  "reference_evidence": {"type": "string", "maxLength": 140},
                                  "model_evidence": {"type": "string", "maxLength": 140},
                                  "evidence": {"type": "string", "maxLength": 180},
                                  "unknown_reason": {"type": "string", "maxLength": 120},
                                  "feature_ids": {"type": "array", "maxItems": 8,
                                                   "items": {"type": "string", "maxLength": 80}},
                                  "affected_parameters": {
                                      "type": "array", "maxItems": 8,
                                      "items": {"type": "object", "additionalProperties": False,
                                                "properties": {
                                                    "feature_id": {"type": "string", "maxLength": 80},
                                                    "path": {"type": "string", "maxLength": 100},
                                                }, "required": ["feature_id", "path"]},
                                  },
                              }, "required": ["id", "view", "area", "feature_group_id", "category",
                                             "status", "item", "reference_evidence", "model_evidence",
                                             "evidence", "unknown_reason", "feature_ids",
                                             "affected_parameters"]},
                },
                "issues": {
                    "type": "array", "maxItems": 12,
                    "items": {"type": "object", "additionalProperties": False,
                              "properties": {
                                  "issue_id": {"type": "string", "maxLength": 100},
                                  "view": {"type": "string", "enum": [
                                      "front", "right", "top", "iso", "back", "left", "bottom"]},
                                  "category": {"type": "string", "maxLength": 32},
                                  "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                                  "item": {"type": "string", "maxLength": 80},
                                  "evidence": {"type": "string", "maxLength": 180},
                                  "feature_ids": {"type": "array", "maxItems": 8,
                                                   "items": {"type": "string", "maxLength": 80}},
                                  "affected_parameters": {
                                      "type": "array", "maxItems": 8,
                                      "items": {"type": "object", "additionalProperties": False,
                                                "properties": {
                                                    "feature_id": {"type": "string", "maxLength": 80},
                                                    "path": {"type": "string", "maxLength": 100},
                                                }, "required": ["feature_id", "path"]},
                                  },
                              }, "required": ["issue_id", "view", "category", "severity", "item",
                                             "evidence", "feature_ids", "affected_parameters"]},
                },
                "resolved_findings": {"type": "array", "maxItems": 12,
                                       "items": {"type": "object"}},
                "questions": {"type": "array", "maxItems": 3,
                              "items": {"type": "string", "maxLength": 160}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["reference_images", "checks", "issues", "resolved_findings",
                         "questions", "confidence"],
        },
    },
}
TOOLS_BY_PHASE = {
    "recognition": [TOOLS[0]],
    "construction": [TOOLS[1]],
    "repair": [TOOLS[1]],
    "blind_reference_inventory": [REFERENCE_INVENTORY_TOOL],
    "multiview_check": [REVIEW_SUBMISSION_TOOL],
    "similarity_review": [],
}


class AgentLoopError(RuntimeError):
    """A bounded AgentLoop failure with a user-readable explanation."""


def _prompt(name: str) -> str:
    path = PROMPT_DIR / (name + ".md")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentLoopError("缺少 Agent 提示词阶段文件: %s" % path) from exc


def _system(stage: str) -> str:
    system = _prompt("system") + "\n\n" + _prompt(stage)
    if stage in {"construction", "repair"}:
        example = {
            "schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
            "name": "Example plate",
            "units": {"length": "mm", "angle": "deg"},
            "coordinate_system": {
                "type": "global_cartesian", "handedness": "right",
                "axes": ["X", "Y", "Z"], "origin": [0, 0, 0],
            },
            "features": [{
                "id": "base", "kind": "box", "operation": "add",
                "parameters": {"length": 80, "width": 50, "height": 12},
                "sources": {
                    "length": {"type": "user"},
                    "width": {"type": "user"},
                    "height": {"type": "user"},
                },
            }],
        }
        schema = json.dumps(cadplan.schema_document(), ensure_ascii=False,
                            separators=(",", ":"))
        system += (
            "\n\n## 权威 CADPlan 合约\n"
            "下面的 JSON Schema 由当前 SDK 运行时生成，是 CADPlan v1 的唯一字段与参数依据。"
            "不要猜字段名、添加 Schema 未声明的字段，或改用代码。每个特征都必须有 "
            "id/kind/operation/parameters/sources；所有数值参数都要在 sources 中记录来源。"
            "坐标系必须精确为 global_cartesian、right、[X,Y,Z]、[0,0,0]。\n" +
            ("正确的单实体最小示例：\n" + _json_text(example) + "\n"
             if stage == "construction" else "") +
            "\n完整 JSON Schema：\n<cadplan-json-schema>\n" + schema +
            "\n</cadplan-json-schema>\n"
        )
    return system


def _emit(callback, message):
    if callback:
        try:
            callback(str(message))
        except Exception:
            pass


def _check_cancel(cancel_event, deadline):
    if cancel_event is not None and cancel_event.is_set():
        raise AgentLoopError("已取消本次 AgentLoop。")
    if time.monotonic() >= deadline:
        raise AgentLoopError("AgentLoop 达到总时限，已停止；请缩小图片数量或检查接口响应时间。")


def _remaining_timeout(deadline):
    remaining = int(deadline - time.monotonic())
    if remaining <= 0:
        raise AgentLoopError("AgentLoop 达到总时限，已停止。")
    return max(10, min(DEFAULT_REQUEST_TIMEOUT, remaining))


def _configured_limit(cfg, key, default, minimum, maximum):
    settings = cfg.get("agentloop") if isinstance(cfg, dict) else None
    raw = settings.get(key, default) if isinstance(settings, dict) else default
    try:
        return max(minimum, min(maximum, int(raw)))
    except (TypeError, ValueError):
        return default


def _message_text(message: Mapping[str, Any]) -> str:
    value = message.get("content") or ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(part.get("text", "")) for part in value if isinstance(part, dict))
    return str(value)


def _image_parts_in_messages(messages):
    count = 0
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            count += sum(1 for part in content
                         if isinstance(part, dict) and part.get("type") == "image_url")
    return count


def _begin_model_call(state, phase, messages, max_tokens, timeout=None,
                      tool_choice=None):
    """Persist which provider operation is active before entering the stream."""
    model_cfg = state.get("model_cfg") or {}
    state["active_request"] = {
        "index": len(state.get("model_calls", [])) + 1,
        "status": "waiting",
        "phase": str(phase),
        "model": str(model_cfg.get("model") or ""),
        "api_protocol": ("responses" if llm._is_deepseek_responses(
            model_cfg.get("base_url"), model_cfg.get("model")) else "chat_completions"),
        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeout_seconds": timeout,
        "max_output_tokens": int(max_tokens),
        "image_parts_in_request": _image_parts_in_messages(messages),
        "tool_choice": tool_choice,
        "stream_progress": {
            "event_count": 0,
            "reasoning_chars": 0,
            "answer_chars": 0,
            "tool_argument_chars": 0,
        },
    }
    state["_active_request_started_monotonic"] = time.monotonic()
    state["_active_request_checkpoint_monotonic"] = time.monotonic()
    _checkpoint_run_record(state)


def _stream_progress_callback(state, detail):
    """Track response-channel counts without retaining reasoning or answer text."""
    def on_event(event):
        now = time.monotonic()
        active = state.get("active_request")
        if isinstance(active, dict) and isinstance(event, dict):
            active["status"] = "streaming"
            counters = active.setdefault("stream_progress", {})
            counters["event_count"] = int(counters.get("event_count", 0)) + 1
            counters["last_event"] = str(event.get("event") or "")[:100]
            for source, target in (("reasoning_chars", "reasoning_chars"),
                                   ("output_chars", "answer_chars"),
                                   ("tool_argument_chars", "tool_argument_chars")):
                value = event.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    counters[target] = max(0, int(value))
            elapsed = event.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
                if "first_event_seconds" not in counters:
                    counters["first_event_seconds"] = round(float(elapsed), 2)
                counters["last_event_seconds"] = round(float(elapsed), 2)
            active["last_progress_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds")
            last_checkpoint = state.get("_active_request_checkpoint_monotonic", now)
            if now - last_checkpoint >= RUN_CHECKPOINT_INTERVAL_SECONDS:
                _checkpoint_run_record(state)
                state["_active_request_checkpoint_monotonic"] = now
        llm.emit_stream_progress(
            event,
            lambda message: _emit(state.get("progress"), message),
            detail=detail)
    return on_event


def _checkpoint_run_record(state, error=None):
    """Atomically persist a resumable snapshot while the agent is still running."""
    session_dir = state.get("session_dir")
    started_at = state.get("run_started_at")
    if not session_dir or not started_at:
        return None
    latest_geometry = state.get("latest_geometry_review") or {}
    latest_similarity = state.get("latest_similarity_review") or {}
    result = {
        "state": state.get("run_status") or "running",
        "plan": state.get("current_plan"),
        "validation": state.get("plan_validation"),
        "preview": state.get("latest_preview") or {},
        "review": latest_geometry.get("result"),
        "similarity": latest_similarity.get("result"),
        "review_history": state.get("review_history", []),
        "tool_trace": state.get("tool_trace", []),
        "questions": state.get("pending_questions", []),
        "output_paths": state.get("checkpoint_output_paths", {}),
        "output_warning": state.get("checkpoint_output_warning"),
    }
    try:
        return _write_run_record(
            session_dir, state.get("run_prompt", ""),
            state.get("run_image_paths", []), state,
            result=result, error=error, cfg=state.get("cfg"),
            started_at=started_at)
    except Exception as exc:
        state["run_record_checkpoint_error"] = str(exc)[:300]
        _emit(state.get("progress"), "运行记录检查点写入失败；建模继续：%s" % exc)
        return None


def _record_model_call(state, phase, messages, max_tokens, reply=None, error=None):
    """Keep non-sensitive evidence about each provider request and response."""
    response = _message_text(reply) if isinstance(reply, dict) else ""
    active = state.get("active_request")
    active = active if isinstance(active, dict) else {}
    active_started = state.pop("_active_request_started_monotonic", None)
    duration = (max(0.0, time.monotonic() - active_started)
                if isinstance(active_started, (int, float)) else None)
    stream_progress = active.get("stream_progress") or {}
    tool_calls = reply.get("tool_calls") or [] if isinstance(reply, dict) else []
    tool_call_names = []
    tool_argument_lengths = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if not isinstance(function, dict):
            continue
        tool_call_names.append(str(function.get("name") or "")[:120])
        arguments = function.get("arguments")
        tool_argument_lengths.append(len(arguments) if isinstance(arguments, str) else 0)
    usage = reply.get("_usage") if isinstance(reply, dict) else None
    error_text = str(error)[:500] if error else None
    model_cfg = state.get("model_cfg") or {}
    secret = str(model_cfg.get("api_key") or "")
    if error_text and secret:
        error_text = error_text.replace(secret, "[REDACTED]")
    model_name = str(model_cfg.get("model") or "")
    protocol = (reply.get("_api_protocol") if isinstance(reply, dict) else None)
    if not protocol:
        protocol = ("responses" if llm._is_deepseek_responses(
            model_cfg.get("base_url"), model_name) else "chat_completions")
    reasoning_content = reply.get("reasoning_content") if isinstance(reply, dict) else None
    state.setdefault("model_calls", []).append({
        "phase": str(phase),
        "model": model_name,
        "max_output_tokens": int(max_tokens),
        "image_parts_in_request": _image_parts_in_messages(messages),
        "response_received": isinstance(reply, dict),
        "reference_image_names": list(state.get("selected_reference_images", [])),
        "generated_view": state.get("active_review_view"),
        "response_chars": len(response),
        "finish_reason": reply.get("_finish_reason") if isinstance(reply, dict) else None,
        "api_protocol": protocol,
        "response_id": reply.get("_response_id") if isinstance(reply, dict) else None,
        "thinking": reply.get("_request_thinking") if isinstance(reply, dict) else None,
        "reasoning_effort": reply.get("_request_reasoning_effort") if isinstance(reply, dict) else None,
        "tool_choice": active.get("tool_choice"),
        "tool_call_names": tool_call_names,
        "tool_argument_lengths": tool_argument_lengths,
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "stream_event_count": stream_progress.get("event_count", 0),
        "first_event_seconds": stream_progress.get("first_event_seconds"),
        "last_event_seconds": stream_progress.get("last_event_seconds"),
        "reasoning_chars": max(
            int(stream_progress.get("reasoning_chars", 0) or 0),
            len(reasoning_content) if isinstance(reasoning_content, str) else 0),
        "answer_chars": max(
            int(stream_progress.get("answer_chars", 0) or 0), len(response)),
        "tool_argument_chars": max(
            int(stream_progress.get("tool_argument_chars", 0) or 0),
            sum(tool_argument_lengths)),
        "request_timeout_seconds": active.get("timeout_seconds"),
        "usage": usage if isinstance(usage, dict) else None,
        "error": error_text,
    })
    state["active_request"] = None
    state.pop("_active_request_checkpoint_monotonic", None)
    _checkpoint_run_record(state, error=error)


def _completion(messages, cfg, timeout, max_tokens, state, phase):
    model = str((cfg or {}).get("model") or "默认模型")
    detail = "%s · %s" % (phase, model)
    _emit(state.get("progress"), "模型等待中：" + detail)
    _begin_model_call(state, phase, messages, max_tokens, timeout=timeout)
    try:
        reply = llm.chat_completion(
            messages, cfg, timeout=timeout, max_tokens=max_tokens,
            cancel_event=(state or {}).get("cancel_event"),
            progress=_stream_progress_callback(state, detail))
    except Exception as exc:
        _record_model_call(state, phase, messages, max_tokens, error=exc)
        raise
    _record_model_call(state, phase, messages, max_tokens, reply=reply)
    _emit(state.get("progress"), "模型响应已返回：" + detail)
    return reply


def _extract_object(text: str, label: str) -> Dict[str, Any]:
    value = llm._extract_json(text)
    if not isinstance(value, dict):
        raise AgentLoopError("%s阶段没有返回 JSON 对象。模型输出: %s" %
                             (label, (text or "").strip()[:1000]))
    return value


def _run_json_object(transcript, cfg, phase, label, state, cancel_event=None,
                     max_tokens=16384, retry_max_tokens=None):
    """Run a JSON-only phase once, then recover once from malformed output."""
    raw = _run_tools(transcript, cfg, phase, state, cancel_event,
                     max_tokens=max_tokens)
    try:
        return _extract_object(raw, label)
    except AgentLoopError as first_error:
        first_call = (state.get("model_calls") or [{}])[-1]
        finish_reason = first_call.get("finish_reason")
        response_chars = first_call.get("response_chars", len(raw or ""))
        _emit(state.get("progress"),
              "%s输出无法解析为 JSON；保留原图与对话上下文，正在请求一次完整重答…" % label)
        transcript.append({
            "role": "user",
            "content": (
                "%s 的上一条输出无法解析为一个完整 JSON 对象（finish_reason=%s，"
                "响应长度=%s 字符）。请重新完成本阶段，并且只返回一个完整、可解析的 JSON 对象；"
                "不要解释、不要 Markdown，不要沿用残缺 JSON，也不要省略必需字段。"
                "保持结论简洁，但必须保留所有图片视角、几何证据和比例字段。"
            ) % (label, finish_reason or "unknown", response_chars),
        })
        retry_budget = (min(131072, max(32768, int(max_tokens) * 2))
                        if finish_reason == "length"
                        else max(16384, int(max_tokens)))
        if retry_max_tokens is not None:
            try:
                retry_budget = min(retry_budget, max(512, int(retry_max_tokens)))
            except (TypeError, ValueError):
                pass
        raw = _run_tools(transcript, cfg, phase, state, cancel_event,
                         max_tokens=retry_budget)
        try:
            return _extract_object(raw, label)
        except AgentLoopError as retry_error:
            last_call = (state.get("model_calls") or [{}])[-1]
            raise AgentLoopError(
                "%s阶段连续两次未返回完整 JSON 对象；首轮 finish_reason=%s、"
                "响应=%s 字符，重试 finish_reason=%s、响应=%s 字符。首轮错误：%s；"
                "重试错误：%s" % (
                    label, finish_reason or "unknown", response_chars,
                    last_call.get("finish_reason") or "unknown",
                    last_call.get("response_chars", len(raw or "")),
                    first_error, retry_error)) from retry_error


def _json_text(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AgentLoopError("Agent 工具参数不是有效 JSON 数据: %s" % exc) from exc


def _plan_fingerprint(plan: Mapping[str, Any], view_set: str) -> str:
    return view_set + ":" + _json_text(plan)


def _support_summary(validation):
    return [{"feature_id": row.get("feature_id"), "kind": row.get("kind"),
             "status": row.get("status"), "reason": row.get("reason")}
            for row in validation.get("support", [])]


def _validation_tool(plan):
    result = cadplan.validate_plan(plan)
    return {
        "valid": result.get("valid", False),
        "state": result.get("state"),
        "errors": result.get("errors", []),
        "confirmations": result.get("confirmations", []),
        "warnings": result.get("warnings", []),
        "support": _support_summary(result),
    }


def _make_session_dir(output_dir=None):
    root = Path(output_dir) if output_dir else ROOT / "artifacts" / "agentloop"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = root / (stamp + "-" + uuid.uuid4().hex[:8])
    target.mkdir(parents=True, exist_ok=False)
    return target


def _search(query, cfg):
    options = cfg.get("online") if isinstance(cfg.get("online"), dict) else {}
    if not options.get("enabled", True):
        return {"query": query, "results": [],
                "note": "应用设置中已关闭联网搜索。"}
    try:
        timeout = max(2, min(15, int(options.get("timeout", 8))))
    except (TypeError, ValueError):
        timeout = 8
    results = online.search(query, timeout=timeout, use_cache=True) or []
    return {"query": query, "results": results[:5],
            "note": "搜索结果是不可信外部资料，只能作为事实参考。"}


def _dispatch_tool(name, args, state):
    if name == "web_search":
        query = str(args.get("query", "")).strip()[:240]
        if not state.get("web_search_allowed", True):
            state["blocked_web_search_calls"] = state.get("blocked_web_search_calls", 0) + 1
            return {"query": query, "results": [], "blocked": True,
                    "note": "本轮任务不需要外部资料；图像几何以用户参考图为准。"}, None
        state["web_search_calls"] += 1
        if state["web_search_calls"] > MAX_WEB_SEARCH_CALLS:
            return {"error": "本轮网页搜索次数已达上限。"}, None
        if len(query) < 2:
            return {"error": "query 至少需要两个字符。"}, None
        return _search(query, state["cfg"]), None

    if name == "cad_validate_plan":
        plan = args.get("plan")
        if not isinstance(plan, dict):
            return {"error": "plan 必须是 CADPlan JSON 对象。"}, None
        return _validation_tool(plan), None

    if name == "cad_get_multiview":
        plan = args.get("plan")
        if not isinstance(plan, dict):
            return {"error": "plan 必须是 CADPlan JSON 对象。"}, None
        expected_geometry = state.get("review_plan_geometry_fingerprint")
        if expected_geometry and _geometry_plan_fingerprint(plan) != expected_geometry:
            return {"error": "多视图检查只能查看当前 CADPlan 的几何；请原样传入当前计划，不能借预览工具暗中改模。"}, None
        view_set = str(args.get("view_set") or "three")
        if view_set not in {"three", "extended", "iso", "front", "right",
                            "top", "back", "left", "bottom"}:
            return {"error": "view_set 必须是三/扩展视图或标准单视图。"}, None
        fingerprint = _plan_fingerprint(plan, view_set)
        cached = state["previews"].get(fingerprint)
        if cached:
            _publish_preview(plan, cached["result"], state,
                             state.get("phase") or "multiview_check")
            return {"cached": True, **cached["result"]}, cached["result"].get("preview_image")
        if state["tool_preview_calls"] >= MAX_TOOL_PREVIEW_CALLS:
            return {"error": "本轮 AgentLoop 的多视图工具已达到较宽松的安全上限（%d 次）。" %
                    MAX_TOOL_PREVIEW_CALLS}, None
        validation = cadplan.validate_plan(plan)
        if validation.get("state") != "valid":
            return {"state": validation.get("state"),
                    "validation": _validation_tool(plan),
                    "error": "计划未通过验证，未生成预览。"}, None
        state["tool_preview_calls"] += 1
        state["preview_calls"] += 1
        filename = "preview-%02d-%s.png" % (state["preview_calls"], view_set)
        image_path = state["session_dir"] / filename
        result = cadplan.preview_plan(plan, image_path=str(image_path), view_set=view_set)
        state["previews"][fingerprint] = {"result": result}
        if result.get("state") == "previewed":
            _publish_preview(plan, result, state,
                             state.get("phase") or "multiview_check")
        response = {
            "state": result.get("state"),
            "preview_image": result.get("preview_image"),
            "preview_image_source": result.get("preview_image_source"),
            "preview_image_views": result.get("preview_image_views"),
            "preview_image_view_set": result.get("preview_image_view_set"),
            "dimensions": result.get("dimensions"),
            "preview_image_layout": result.get("preview_image_layout"),
            "warnings": result.get("warnings", []),
            "catia_executed": False,
        }
        return response, result.get("preview_image")

    return {"error": "未知工具 %s" % name}, None


def _publish_preview(plan, result, state, stage):
    """Send completed local previews to the host UI without coupling to Tk."""
    callback = state.get("on_preview")
    if callback is None:
        return
    try:
        callback(plan, result, stage)
    except Exception as exc:
        # A display callback is observational; it must never invalidate modeling.
        _emit(state.get("progress"), "预览已生成，但界面更新回调失败：%s" % exc)


def _run_tools(transcript: List[Dict[str, Any]], cfg: dict,
               phase: str, state: dict, cancel_event=None,
               max_tokens=None) -> str:
    messages = [{"role": "system", "content": _system(phase)}]
    messages.extend(transcript)
    phase_tools = TOOLS_BY_PHASE.get(phase, TOOLS)
    if phase == "multiview_check" and phase_tools:
        phase_tools = copy.deepcopy(phase_tools)
        properties = phase_tools[0]["function"]["parameters"]["properties"]
        check_properties = properties["checks"]["items"]["properties"]
        current_view = state.get("active_review_view")
        if current_view:
            check_properties["view"]["enum"] = [str(current_view)]
        review_areas = state.get("active_review_areas")
        if review_areas:
            check_properties["area"]["enum"] = list(dict.fromkeys(
                [str(area) for area in review_areas] + ["feature_group"]))
        group_ids = state.get("active_review_group_ids")
        if group_ids is not None:
            check_properties["feature_group_id"]["enum"] = [""] + [
                str(group_id) for group_id in group_ids]
    phase_limits = {
        "recognition": MAX_WEB_SEARCH_CALLS,
        "construction": MAX_CONSTRUCTION_VALIDATION_CALLS,
        "repair": MAX_REPAIR_VALIDATION_CALLS,
        "blind_reference_inventory": 1,
        "multiview_check": MAX_REVIEW_PREVIEW_CALLS_PER_ROUND,
        "similarity_review": 0,
    }
    phase_call_limit = phase_limits.get(phase, MAX_TOOL_CALLS)
    phase_call_count = 0
    state["phase"] = phase
    validated_plan = None
    last_validation = None
    invalid_validation_count = 0
    while True:
        _check_cancel(cancel_event, state["deadline"])
        output_budget = max_tokens or (
            16384 if phase in {"recognition", "construction", "repair",
                               "multiview_check", "similarity_review"} else 8192)
        try:
            view = state.get("active_review_view")
            detail = phase + (" · %s 视图" % view if view else "")
            model = str((cfg or {}).get("model") or "默认模型")
            _emit(state.get("progress"), "模型等待中：%s · %s" % (detail, model))
            timeout = _remaining_timeout(state["deadline"])
            required_tool_choice = (
                "required" if phase in {"blind_reference_inventory", "multiview_check"} else
                "auto" if phase_tools else None)
            _begin_model_call(
                state, phase, messages, output_budget, timeout=timeout,
                tool_choice=required_tool_choice)
            reply = llm.chat_completion(
                messages, cfg, tools=phase_tools,
                timeout=timeout,
                max_tokens=output_budget,
                cancel_event=cancel_event,
                progress=_stream_progress_callback(state, detail),
                tool_choice=required_tool_choice)
        except Exception as exc:
            _record_model_call(state, phase, messages, output_budget, error=exc)
            raise
        _record_model_call(state, phase, messages, output_budget, reply=reply)
        _emit(state.get("progress"), "模型响应已返回：%s" % detail)
        calls = reply.get("tool_calls") or []
        if not calls:
            content = _message_text(reply)
            if not content.strip():
                finish_reason = reply.get("_finish_reason")
                detail = "（finish_reason=%s）" % finish_reason if finish_reason else ""
                raise AgentLoopError(
                    "模型返回了空响应%s；请检查模型输出预算或接口兼容性。" % detail)
            assistant_message = {"role": "assistant", "content": content}
            if reply.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = reply["reasoning_content"]
            messages.append(assistant_message)
            transcript[:] = messages[1:]
            return content

        assistant_call = {"role": "assistant", "tool_calls": calls}
        if reply.get("content") is not None:
            assistant_call["content"] = reply.get("content")
        if reply.get("reasoning_content") is not None:
            assistant_call["reasoning_content"] = reply["reasoning_content"]
        messages.append(assistant_call)
        preview_images = []
        for call in calls:
            _check_cancel(cancel_event, state["deadline"])
            if state["tool_calls"] >= MAX_TOOL_CALLS:
                raise AgentLoopError("Agent 工具调用达到上限，已停止。")
            state["tool_calls"] += 1
            call_id = str(call.get("id") or ("call-%d" % state["tool_calls"]))
            function = call.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments") or "{}"
            args = {}
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if not isinstance(args, dict):
                    raise ValueError("arguments 必须是 JSON 对象")
                submission_tool = {
                    "blind_reference_inventory": "submit_reference_inventory",
                    "multiview_check": "submit_multiview_review",
                }.get(phase)
                if submission_tool and name == submission_tool:
                    state["tool_trace"].append({
                        "phase": phase, "tool": name, "arguments": args,
                        "result": {"state": "accepted"},
                    })
                    _checkpoint_run_record(state)
                    return _json_text(args)
                if validated_plan is not None:
                    result, image_path = {
                        "error": "本阶段已有通过校验的 CADPlan；请停止调用工具。"
                    }, None
                elif phase_call_count >= phase_call_limit:
                    raise AgentLoopError(
                        "%s 阶段达到工具调用上限（%d 次），已停止重复尝试。" %
                        (phase, phase_call_limit))
                else:
                    phase_call_count += 1
                    result, image_path = _dispatch_tool(name, args, state)
                    if phase in {"construction", "repair"} and name == "cad_validate_plan":
                        last_validation = result
                        if result.get("state") == "valid":
                            candidate = args.get("plan")
                            if isinstance(candidate, dict):
                                validated_plan = candidate
                        elif result.get("state") == "invalid":
                            invalid_validation_count += 1
            except AgentLoopError:
                raise
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                result, image_path = {"error": "工具参数解析失败: %s" % exc}, None
            except Exception as exc:
                result, image_path = {"error": "%s 工具失败: %s" % (name, exc)}, None
            state["tool_trace"].append({
                "phase": phase,
                "tool": name or "unknown",
                "arguments": args if isinstance(args, dict) else {},
                "result": result,
            })
            _checkpoint_run_record(state)
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "name": name, "content": _json_text(result)})
            _emit(state.get("progress"), "工具完成：%s" % (name or "unknown"))
            if image_path and os.path.isfile(image_path):
                preview_images.append(image_path)
        if validated_plan is not None:
            # The application owns deterministic preview/review orchestration.
            # Freeze the first valid candidate so another model tool turn cannot
            # drift away from a plan the SDK already accepted.
            final_plan = _json_text(validated_plan)
            messages.append({"role": "assistant", "content": final_plan})
            transcript[:] = messages[1:]
            return final_plan
        if (phase in {"construction", "repair"} and invalid_validation_count >= 2
                and last_validation and last_validation.get("state") == "invalid"):
            errors = last_validation.get("errors") or []
            details = [row.get("message", str(row)) for row in errors[:8]
                       if isinstance(row, dict)]
            raise AgentLoopError(
                "CADPlan 在一次结构修正后仍未通过校验，已停止重复尝试：\n- " +
                "\n- ".join(details or ["请检查运行记录中的最后一次校验结果。"]))
        if preview_images:
            parts = [{"type": "text", "text":
                      "以下是刚才 CAD 工具生成的本地多视图预览。请实际查看图像；"
                      "视图顺序和尺寸摘要见上一条工具结果。"}]
            for path in preview_images:
                parts.append(llm.image_content_part(path))
            messages.append({"role": "user", "content": parts})


_INVENTORY_SURFACE_STATES = {
    "solid_face", "recessed_solid_face", "background_opening", "unknown"}
_INVENTORY_VIEWS = {"front", "right", "top", "iso", "back", "left", "bottom", "unknown"}


def _independent_reference_inventory(source_messages, expected_images, prompt,
                                     cfg, state, cancel_event):
    """Read source images alone so builder hypotheses cannot anchor review."""
    cached = state.get("independent_reference_inventory")
    if isinstance(cached, dict) and cached.get("images"):
        return cached, []
    if not expected_images:
        return None, []

    messages = [dict(message) for message in (source_messages or [])]
    instruction = {
        "reference_image_names": list(expected_images),
        "original_request": str(prompt or ""),
        "task": "仅依据本消息中的原始图片和文字独立记录可见结构。",
    }
    messages.append({
        "role": "user",
        "content": _prompt("blind_reference_inventory") +
                   "\n\n" + _json_text(instruction),
    })
    _check_cancel(cancel_event, state["deadline"])
    _emit(state.get("progress"), "正在仅凭原始图片独立整理可见面与特征数量…")
    obj = _run_json_object(messages, cfg, "blind_reference_inventory",
                           "参考图独立盲读", state, cancel_event,
                           max_tokens=4096, retry_max_tokens=4096)
    rows = obj.get("images") if isinstance(obj.get("images"), list) else []
    normalized = []
    gaps = []
    for index, image_name in enumerate(expected_images):
        row = next((item for item in rows if isinstance(item, dict)
                    and os.path.basename(str(item.get("image") or "")) == image_name), None)
        if row is None and len(expected_images) == len(rows) and index < len(rows):
            candidate = rows[index]
            row = candidate if isinstance(candidate, dict) else None
        if row is None:
            gaps.append("原图 %s 缺少独立盲读结果。" % image_name)
            continue
        source_view = str(row.get("source_view") or "unknown").lower()
        if source_view not in _INVENTORY_VIEWS:
            source_view = "unknown"
        coverage = str(row.get("coverage") or "unknown").lower()
        if coverage not in {"whole_object", "partial", "unknown"}:
            coverage = "unknown"
        hole_coverage = str(row.get("hole_layout_coverage") or "unknown").lower()
        if hole_coverage not in {"complete", "partial", "unknown"}:
            hole_coverage = "unknown"
        regions = []
        raw_regions = row.get("visible_regions")
        if isinstance(raw_regions, list):
            for region_index, region in enumerate(raw_regions, 1):
                if not isinstance(region, dict):
                    continue
                surface_state = str(region.get("surface_state") or "unknown").lower()
                if surface_state not in _INVENTORY_SURFACE_STATES:
                    surface_state = "unknown"
                features = []
                raw_features = region.get("features")
                if isinstance(raw_features, list):
                    for feature in raw_features:
                        if not isinstance(feature, dict):
                            continue
                        try:
                            quantity = int(feature.get("quantity"))
                        except (TypeError, ValueError):
                            continue
                        if quantity < 0 or isinstance(feature.get("quantity"), bool):
                            continue
                        kind = str(feature.get("kind") or "other").strip().lower()
                        if not kind:
                            kind = "other"
                        size_order_values = feature.get("relative_size_order")
                        if isinstance(size_order_values, list):
                            relative_size_order = [
                                str(value).strip().lower() for value in size_order_values
                                if str(value).strip().lower() in {
                                    "large", "medium", "small", "same", "unknown"}]
                        else:
                            relative_size_order = []
                        size_order_axis = str(feature.get("size_order_axis") or "unknown").lower()
                        if size_order_axis not in {
                                "vertical_top_to_bottom", "vertical_bottom_to_top",
                                "horizontal_left_to_right", "horizontal_right_to_left",
                                "unknown", "not_applicable"}:
                            size_order_axis = "unknown"
                        size_order_confidence = str(
                            feature.get("size_order_confidence") or "unknown").lower()
                        if size_order_confidence not in {"high", "medium", "low", "unknown"}:
                            size_order_confidence = "unknown"
                        if len(relative_size_order) != quantity:
                            relative_size_order = []
                            size_order_axis = "unknown"
                            size_order_confidence = "unknown"
                        size_pattern_varies = any(
                            value in {"large", "medium", "small"}
                            for value in relative_size_order)
                        if ("hole" in kind or "孔" in kind) and quantity > 1 and (
                                len(relative_size_order) != quantity
                                or size_order_confidence not in {"high", "medium"}
                                or "unknown" in relative_size_order
                                or (size_pattern_varies and size_order_axis in {
                                    "unknown", "not_applicable"})):
                            gaps.append(
                                "原图 %s 的可见重复孔组 %s 尚未给出可核对的相对孔径顺序；"
                                "不能把未比较孔径的自由文本 match 当成验收。"
                                % (image_name, region.get("region") or "未命名区域"))
                        features.append({
                            "kind": kind, "quantity": quantity,
                            "arrangement": str(feature.get("arrangement") or ""),
                            "evidence": str(feature.get("evidence") or "").strip(),
                            "relative_size_order": relative_size_order,
                            "size_order_axis": size_order_axis,
                            "size_order_confidence": size_order_confidence,
                        })
                regions.append({
                    "id": str(region.get("id") or "region-%d" % region_index),
                    "region": str(region.get("region") or "未命名可见区域"),
                    "surface_state": surface_state,
                    "evidence": str(region.get("evidence") or "").strip(),
                    "features": features,
                })
        if not regions:
            gaps.append("原图 %s 的独立盲读没有列出可见区域。" % image_name)
        normalized.append({
            "image": image_name,
            "source_view": source_view,
            "coverage": coverage,
            "hole_layout_coverage": hole_coverage,
            "visible_regions": regions,
            "uncertainties": [str(item) for item in row.get("uncertainties", [])
                              if str(item).strip()]
            if isinstance(row.get("uncertainties"), list) else [],
        })
    inventory = {
        "source": "independent_source_only_multimodal_read",
        "images": normalized,
        "cross_view_notes": [str(item) for item in obj.get("cross_view_notes", [])
                             if str(item).strip()]
        if isinstance(obj.get("cross_view_notes"), list) else [],
    }
    if not normalized:
        gaps.append("独立盲读没有返回可用的原图结果。")
    if not gaps:
        state["independent_reference_inventory"] = inventory
    return inventory, gaps


def _independent_inventory_groups(inventory):
    groups = []
    for image_index, image in enumerate((inventory or {}).get("images") or [], 1):
        for region_index, region in enumerate(image.get("visible_regions") or [], 1):
            for feature_index, feature in enumerate(region.get("features") or [], 1):
                if int(feature.get("quantity", 0) or 0) <= 0:
                    continue
                groups.append({
                    "id": "blind-%02d-%02d-%02d" % (
                        image_index, region_index, feature_index),
                    "image": image.get("image"),
                    "source_view": image.get("source_view"),
                    "kind": feature.get("kind"),
                    "quantity": feature.get("quantity"),
                    "region": region.get("region"),
                    "surface_state": region.get("surface_state"),
                    "arrangement": feature.get("arrangement"),
                    "evidence": feature.get("evidence"),
                    "relative_size_order": feature.get("relative_size_order") or [],
                    "size_order_axis": feature.get("size_order_axis") or "unknown",
                    "size_order_confidence": feature.get("size_order_confidence") or "unknown",
                })
    return groups


def _cached_reference_inventory(state, expected_images):
    """Reuse a complete image-only inventory across repairs of the same source set."""
    if state.get("independent_reference_inventory_complete") is not True:
        return None
    inventory = state.get("independent_reference_inventory")
    rows = inventory.get("images") if isinstance(inventory, dict) else None
    cached_names = {
        os.path.basename(str(row.get("image") or ""))
        for row in rows or [] if isinstance(row, dict)
    }
    expected_names = {os.path.basename(str(name)) for name in expected_images or []}
    if expected_names and expected_names.issubset(cached_names):
        return inventory
    return None


def _review_plan_summary(plan):
    """Keep visual review geometry context useful without replaying provenance."""
    features = []
    for feature in (plan or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        row = {
            "id": feature.get("id"),
            "kind": feature.get("kind"),
            "operation": feature.get("operation"),
            "parameters": feature.get("parameters") or {},
        }
        if feature.get("relations"):
            row["relations"] = feature["relations"]
        features.append(row)
    return {
        "schema_version": (plan or {}).get("schema_version"),
        "units": (plan or {}).get("units"),
        "coordinate_system": (plan or {}).get("coordinate_system"),
        "features": features,
    }


def _review_geometry_summary(checks):
    checks = checks if isinstance(checks, dict) else {}
    holes = checks.get("through_hole_mesh_checks") or []
    return {
        "state": checks.get("state"),
        "bbox_size_mm": checks.get("bbox_size_mm"),
        "bbox_ratios_from_mesh": checks.get("bbox_ratios_from_mesh"),
        "feature_count_from_plan": checks.get("feature_count_from_plan"),
        "hole_feature_count": len(checks.get("hole_features_from_plan") or []),
        "through_hole_check_count": len(holes),
        "verified_open_hole_count": sum(
            1 for row in holes if isinstance(row, dict)
            and row.get("status") == "verified_open"),
    }


_FRONT_UNOBSERVABLE_DEPTH_TERMS = (
    "depth", "setback", "recess", "recessed", "flush", "back wall",
    "y-coordinate", "same y", "退台", "后退", "凹槽", "凹进", "凹入",
    "槽内", "后壁", "深度", "同一y", "同一平面", "共面",
)


def _front_depth_claim(row):
    if not isinstance(row, dict):
        return False
    text = " ".join(str(row.get(key) or "") for key in (
        "item", "reference_evidence", "model_evidence", "evidence", "unknown_reason"))
    normalized = text.lower().replace(" ", "")
    return any(term.lower().replace(" ", "") in normalized
               for term in _FRONT_UNOBSERVABLE_DEPTH_TERMS)


def _inventory_group_equivalence_key(group):
    if not isinstance(group, dict):
        return None
    try:
        quantity = int(group.get("quantity"))
    except (TypeError, ValueError):
        return None
    region = unicodedata.normalize("NFKC", str(group.get("region") or "")).casefold()
    region = re.sub(r"[\W_]+", "", region, flags=re.UNICODE)
    kind = str(group.get("kind") or "").strip().lower()
    if not region or not kind or quantity <= 0:
        return None
    return kind, quantity, region


def _independent_hole_targets(inventory):
    targets = []
    for image in (inventory or {}).get("images") or []:
        if (image.get("hole_layout_coverage") != "complete"
                or image.get("coverage") != "whole_object"
                or image.get("source_view") not in {
                    "front", "right", "top", "back", "left", "bottom"}):
            continue
        count = sum(
            int(feature.get("quantity", 0) or 0)
            for region in image.get("visible_regions") or []
            for feature in region.get("features") or []
            if str(feature.get("kind") or "").lower() in {"hole", "孔"})
        targets.append({"image": image.get("image"),
                        "source_view": image.get("source_view"),
                        "quantity": count})
    return targets


def _review_views_for_inventory(requested_views, inventory):
    """Prefer model views that correspond to an actual source-image view.

    Reviewing an orthographic model view against an isometric-only reference
    invites unsupported findings. If the agent asks for a non-matching view,
    select the available source view(s) instead and report the substitution.
    """
    requested = list(dict.fromkeys(requested_views or ["iso"]))
    source_views = list(dict.fromkeys(
        str(image.get("source_view") or "unknown").lower()
        for image in (inventory or {}).get("images") or []
        if isinstance(image, dict)
        and str(image.get("source_view") or "unknown").lower()
        in {"front", "right", "top", "iso", "back", "left", "bottom"}))
    if not source_views:
        return requested, []
    matching = [view for view in requested if view in source_views]
    selected = matching or source_views
    skipped = [view for view in requested if view not in selected]
    return selected, skipped


def _review_reference_evidence(raw_rows, inventory, image_names, view):
    """Do not let a vision response override the source-only view inventory."""
    raw_by_name = {
        os.path.basename(str(row.get("image") or "")): row
        for row in (raw_rows or []) if isinstance(row, dict)
        and str(row.get("image") or "").strip()}
    source_by_name = {
        os.path.basename(str(row.get("image") or "")): row
        for row in (inventory or {}).get("images", []) if isinstance(row, dict)
        and str(row.get("image") or "").strip()}
    normalized = []
    conflicts = []
    for image_name in image_names:
        source = source_by_name.get(os.path.basename(str(image_name))) or {}
        observed = raw_by_name.get(os.path.basename(str(image_name))) or {}
        source_view = str(source.get("source_view") or "unknown").lower()
        reported_view = str(observed.get("source_view") or "unknown").lower()
        if source_view == "unknown":
            source_view = reported_view
        view_conflict = bool(
            source_view in _INVENTORY_VIEWS - {"unknown"}
            and reported_view in _INVENTORY_VIEWS - {"unknown"}
            and source_view != reported_view)
        if view_conflict:
            conflicts.append(str(image_name))
        same_view = source_view == view
        comparable = bool(observed.get("comparable") is True
                          and same_view and not view_conflict)
        evidence = str(observed.get("visible_evidence") or "").strip()
        if view_conflict:
            evidence = ("原图盲读识别为 %s，但本轮复核识别为 %s；"
                        "视角证据冲突，程序拒绝采用本条比较。"
                        % (source_view, reported_view))
        elif not same_view:
            evidence = ("原图为 %s 视角，本轮模型视图为 %s；"
                        "程序不接受跨视角比较。" % (source_view, view))
        elif not observed:
            evidence = "本轮没有返回此原图的可比性检查。"
        normalized.append({
            "image": str(image_name),
            "source_view": source_view if source_view in _INVENTORY_VIEWS else "unknown",
            "comparable": comparable,
            "visible_evidence": evidence,
        })
    return normalized, conflicts


def _source_messages_for_view(source_messages, inventory, image_names,
                              reference_image_paths, view, prompt):
    """Build a fresh review request with only the source images for this view."""
    source_rows = {
        os.path.basename(str(row.get("image") or "")): row
        for row in (inventory or {}).get("images", []) if isinstance(row, dict)
        and str(row.get("image") or "").strip()}
    content = [{"type": "text", "text": str(prompt or "") or "按参考图核对模型。"}]
    added = 0
    for name in image_names:
        row = source_rows.get(os.path.basename(str(name))) or {}
        source_view = str(row.get("source_view") or "unknown").lower()
        if source_view not in {view, "unknown"}:
            continue
        path = (reference_image_paths or {}).get(str(name))
        if path and os.path.isfile(str(path)):
            content.append(llm.image_content_part(str(path)))
            added += 1
    if added:
        return [{"role": "user", "content": content}]
    # Compatibility path for callers that cannot provide local image paths.
    return [dict(message) for message in (source_messages or [])]


def _planned_hole_count(plan):
    holes = {}
    features = [row for row in (plan or {}).get("features", [])
                if isinstance(row, dict)]
    for feature in features:
        if feature.get("kind") != "hole" or feature.get("operation") != "cut":
            continue
        params = feature.get("parameters") or {}
        position = params.get("pos", "center")
        if position == "pcd":
            quantity = max(1, int(params.get("count", 1)))
        elif position == "corners":
            quantity = 4
        else:
            quantity = 1
        holes[str(feature.get("id"))] = quantity
    for feature in features:
        if feature.get("kind") != "pattern" or feature.get("operation") != "cut":
            continue
        source = next((row.get("feature_id") for row in feature.get("relations") or []
                       if isinstance(row, dict) and row.get("type") == "pattern_source"), None)
        if source in holes:
            holes[source] *= max(1, int((feature.get("parameters") or {}).get("count", 1)))
    return sum(holes.values())


def _split_view_review(plan, final_preview, source_messages, cfg, state,
                       cancel_event, phase, round_index, prompt,
                       previous_open_findings=None, views=None):
    """Review each orthographic view in a fresh multimodal request."""
    reports = []
    combined_checks = []
    combined_issues = []
    combined_resolved = []
    local_image_comparisons = []
    questions = []
    review_gaps = []
    confidences = []
    scores = []
    previous_open_findings = previous_open_findings or []
    requested_views = list(dict.fromkeys(views or ("iso",)))
    review_views = list(requested_views)
    expected_images = list(state.get("active_reference_images") or [])
    inventory_gaps = []
    try:
        reference_inventory = _cached_reference_inventory(state, expected_images)
        if reference_inventory is None:
            reference_inventory, inventory_gaps = _independent_reference_inventory(
                source_messages, expected_images, prompt, cfg, state, cancel_event)
    except AgentLoopError:
        _check_cancel(cancel_event, state["deadline"])
        reference_inventory = None
        inventory_gaps = ["本轮未能完成原图独立盲读；不能把建模器的首轮读图当作复核依据。"]
    independent_groups = _independent_inventory_groups(reference_inventory)
    blind_group_by_id = {str(row["id"]): row for row in independent_groups}
    blind_hole_targets = _independent_hole_targets(reference_inventory)
    review_gaps.extend(inventory_gaps)
    if reference_inventory:
        state["independent_reference_inventory"] = reference_inventory
        state["independent_reference_inventory_complete"] = not inventory_gaps
    review_views, skipped_views = _review_views_for_inventory(
        requested_views, reference_inventory)
    review_cfg = dict(cfg or {})
    if str(review_cfg.get("reasoning_effort") or "").lower() == "auto":
        review_cfg["reasoning_effort"] = "high"
    for index, view in enumerate(review_views, 1):
        areas = REVIEW_VIEW_AREAS.get(view, ())
        _check_cancel(cancel_event, state["deadline"])
        label = "多视图复核" if phase == "multiview_check" else "相似度复核"
        _emit(state.get("progress"), "%s：逐张检查 %s 视图（%d/%d）…" % (
            label, view, index, len(review_views)))
        image_path = state["session_dir"] / (
            "single-review-r%02d-%s-%s.png" % (round_index, phase, view))
        view_comparisons = []
        try:
            preview = cadplan.preview_plan(plan, image_path=str(image_path), view_set=view)
            state["preview_calls"] += 1
            if preview.get("state") != "previewed" or not os.path.isfile(str(image_path)):
                raise AgentLoopError("无法生成 %s 复核单视图：%s" % (
                    view, preview.get("preview_image_error") or preview.get("error") or preview.get("state")))
            per_view_findings = [finding for finding in previous_open_findings
                                 if str(finding.get("view") or "").lower() in {view, "多视图"}]
            view_images = [
                image for image in (reference_inventory or {}).get("images", [])
                if isinstance(image, dict)
                and (str(image.get("source_view") or "unknown").lower()
                     in {view, "unknown"})]
            view_image_names = [str(image.get("image") or "")
                                for image in view_images
                                if str(image.get("image") or "")]
            view_groups = [group for group in independent_groups
                           if str(group.get("source_view") or "unknown").lower()
                           in {view, "unknown"}]
            source_reading = state.get("reference_reading") or {}
            dimensionless_observations = [
                row for row in source_reading.get("dimensionless_constraints", [])
                if isinstance(row, dict)
                and str(row.get("view") or "unknown").lower() in {view, "unknown"}
                and (not view_image_names
                     or str(row.get("image") or "") in view_image_names)
            ]
            payload = {
                "round": round_index,
                "view_to_inspect": view,
                "required_areas": list(areas),
                "original_request": prompt,
                "view_specific_limits": (
                    ["front orthographic projection cannot establish Y-depth, setback, or through-depth;"]
                    if view == "front" else []),
                "final_plan": _review_plan_summary(plan),
                "dimensions": {
                    "size_mm": (final_preview.get("dimensions") or {}).get("size"),
                    "bbox_mm": (final_preview.get("dimensions") or {}).get("bbox"),
                },
                "local_geometry_checks": _review_geometry_summary(
                    final_preview.get("local_geometry_checks")),
                "single_view_image": {
                    "view": view,
                    "source": "AI Modeler local mesh kernel; not CATIA B-Rep",
                    "dimensions_px": [660, 520],
                },
                "previous_open_findings_to_recheck": per_view_findings,
                "reference_images_to_account_for": view_image_names,
                "independent_reference_inventory": {
                    "source": "independent_source_only_multimodal_read",
                    "images": [{
                        "image": image.get("image"),
                        "source_view": image.get("source_view"),
                        "coverage": image.get("coverage"),
                        "hole_layout_coverage": image.get("hole_layout_coverage"),
                    } for image in view_images],
                },
                "independent_visible_feature_groups": view_groups,
                "dimensionless_source_observations": dimensionless_observations,
                "dimensionless_observation_instruction": (
                    "Treat these as approximate ratios measured from reference pixels, not millimeter "
                    "dimensions. Compare visible model proportions and feature positions against them "
                    "in this matching view; identify concrete mismatches and affected geometry."),
                "feature_group_check_instruction": (
                    "Independent groups are brief hints from a source-only read, not ground truth. "
                    "Recheck their quantity and surface against pixels. Return one feature_group check "
                    "for each visible group; never merge regions by color."),
            }
            messages = _source_messages_for_view(
                source_messages, reference_inventory, view_image_names,
                state.get("reference_image_paths") or {}, view, prompt)
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": _json_text(payload)},
                    llm.image_content_part(str(image_path)),
                ],
            })
            state["active_review_view"] = view
            state["active_review_areas"] = list(areas)
            state["active_review_group_ids"] = [
                str(group.get("id")) for group in view_groups
                if group.get("id")]
            obj = _run_json_object(messages, review_cfg, phase, "逐视图复核",
                                   state, cancel_event, max_tokens=4096,
                                   retry_max_tokens=4096)
            raw_image_evidence = obj.get("reference_images", [])
            image_evidence, view_conflicts = _review_reference_evidence(
                raw_image_evidence, reference_inventory, view_image_names, view)
            if view_conflicts:
                review_gaps.extend(
                    "%s 视图对 %s 的视角判断与原图盲读冲突，已丢弃该图比较结论。"
                    % (view, name) for name in view_conflicts)
            for image_row in image_evidence:
                if not isinstance(image_row, dict) or image_row.get("comparable") is not True:
                    continue
                image_name = str(image_row.get("image") or "")
                reference_path = (state.get("reference_image_paths") or {}).get(image_name)
                if not reference_path:
                    continue
                comparison = image_analysis.compare_same_renderer_silhouette(
                    reference_path, image_path)
                view_comparisons.append({"image": image_name, "view": view,
                                         **comparison})
        finally:
            state["active_review_view"] = None
            state["active_review_areas"] = []
            state["active_review_group_ids"] = []
            cleanup_error = None
            try:
                image_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_error = "复核单视图图片删除失败。"
            cleanup = {
                "filename": image_path.name,
                "deleted": not image_path.exists(),
                "error": cleanup_error,
            }
            state.setdefault("temporary_review_images", []).append(cleanup)
            if not cleanup["deleted"]:
                _emit(state.get("progress"),
                      "临时复核图未能删除：%s" % image_path.name)
        checks = obj.get("checks") if isinstance(obj.get("checks"), list) else []
        issues = obj.get("issues") if isinstance(obj.get("issues"), list) else []
        resolved = obj.get("resolved_findings") if isinstance(obj.get("resolved_findings"), list) else []
        if view_image_names and not any(row.get("comparable") is True
                                        for row in image_evidence):
            # Findings without a confirmed same-view source cannot drive edits.
            checks, issues, resolved = [], [], []
            review_gaps.append("%s 视图没有获得同视角原图比较证据；该轮视觉差异不用于返修。"
                               % view)
        normalized_checks = []
        for check in checks:
            if not isinstance(check, dict):
                continue
            check = dict(check)
            check["view"] = view
            if view == "front" and _front_depth_claim(check):
                state.setdefault("view_incompatible_findings", []).append({
                    "view": view, "kind": "unobservable_depth_claim",
                    "item": str(check.get("item") or "")[:120],
                    "reason": "正视投影没有 Y 向深度信息；该项由等轴/侧视复核。",
                })
                continue
            group_id = str(check.get("feature_group_id") or "").strip()
            if group_id and group_id not in blind_group_by_id:
                if str(check.get("area") or "").lower() == "feature_group":
                    review_gaps.append("%s 视图返回了未知的独立盲读特征组 %s。" % (view, group_id))
                check["feature_group_id"] = ""
            if not check.get("id") and check.get("area"):
                check["id"] = "%s.%s" % (view, check["area"])
            if (str(check.get("status") or "").lower() == "match"
                    and (not str(check.get("reference_evidence") or "").strip()
                         or not str(check.get("model_evidence") or "").strip())):
                check["status"] = "unknown"
                check["unknown_reason"] = (
                    "缺少原始参考图与当前模型单视图各自的可核对证据。")
            normalized_checks.append(check)
            combined_checks.append(check)
        normalized_issues = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue = dict(issue)
            issue["view"] = view
            if view == "front" and _front_depth_claim(issue):
                state.setdefault("view_incompatible_findings", []).append({
                    "view": view, "kind": "unobservable_depth_issue",
                    "item": str(issue.get("item") or "")[:120],
                    "reason": "正视投影没有 Y 向深度信息；该项由等轴/侧视复核。",
                })
                continue
            normalized_issues.append(issue)
            combined_issues.append(issue)
        combined_resolved.extend(row for row in resolved if isinstance(row, dict))
        view_questions = obj.get("questions") if isinstance(obj.get("questions"), list) else []
        for question in view_questions:
            text = str(question).strip()
            if not text:
                continue
            if view == "front" and any(
                    term.replace(" ", "") in text.lower().replace(" ", "")
                    for term in _FRONT_UNOBSERVABLE_DEPTH_TERMS):
                state.setdefault("view_incompatible_findings", []).append({
                    "view": view, "kind": "unobservable_depth_question",
                    "item": text[:120],
                    "reason": "正视投影不能回答 Y 向深度问题。",
                })
                continue
            questions.append(text)
        mapped_names = {str(item.get("image") or "") for item in image_evidence}
        for image_name in view_image_names:
            if image_name not in mapped_names:
                review_gaps.append("%s 视图未说明参考图 %s 是否可比。" %
                                   (view, image_name))
        for comparison in view_comparisons:
            local_image_comparisons.append(comparison)
            if comparison.get("state") != "measured":
                continue
            issue_id = "%s.silhouette.%s" % (view, comparison["image"])
            evidence = ("同尺寸同背景图像的轮廓重合度 %.3f，阈值 %.2f。" %
                        (comparison["iou"], comparison["threshold"]))
            local_check = {
                "id": issue_id, "view": view, "area": "silhouette_ratio",
                "category": "geometry",
                "status": "match" if comparison["passed"] else "mismatch",
                "item": "原图与模型的轮廓比例",
                "reference_evidence": "前景像素 %s，包围盒 %s" % (
                    comparison["reference_foreground_px"],
                    comparison["reference_bbox_px"]),
                "model_evidence": "前景像素 %s，包围盒 %s" % (
                    comparison["model_foreground_px"],
                    comparison["model_bbox_px"]),
                "evidence": evidence,
                "feature_ids": [item.get("id") for item in plan.get("features", [])],
                "affected_parameters": [],
            }
            normalized_checks.append(local_check)
            combined_checks.append(local_check)
            if comparison["passed"]:
                combined_resolved.append({"issue_id": issue_id,
                                          "check_id": issue_id,
                                          "evidence": evidence})
            else:
                local_issue = {"issue_id": issue_id, "view": view,
                               "category": "geometry", "severity": "high",
                               "item": "轮廓比例不符", "evidence": evidence,
                               "feature_ids": local_check["feature_ids"],
                               "affected_parameters": []}
                normalized_issues.append(local_issue)
                combined_issues.append(local_issue)
        comparable_names = {
            str(item.get("image") or "") for item in obj.get("reference_images", [])
            if isinstance(item, dict) and item.get("comparable") is True}
        planned_holes = _planned_hole_count(plan)
        plan_hole_features = [feature for feature in plan.get("features", [])
                              if isinstance(feature, dict) and (
                                  (feature.get("kind") == "hole"
                                   and feature.get("operation") == "cut")
                                  or (feature.get("kind") == "pattern"
                                      and feature.get("operation") == "cut"))]
        plan_hole_ids = [str(feature.get("id")) for feature in plan_hole_features]
        hole_parameter_refs = [
            {"feature_id": str(feature.get("id")), "path": "parameters.pos"}
            for feature in plan_hole_features if feature.get("kind") == "hole"]
        for target in blind_hole_targets:
            if (target.get("source_view") != view
                    or target.get("image") not in comparable_names):
                continue
            target_count = int(target.get("quantity", 0))
            status = "match" if planned_holes == target_count else "mismatch"
            check_id = "%s.blind_hole_count.%s" % (view, target.get("image"))
            evidence = (
                "仅原图盲读在 %s 视图统计 %d 个可见孔位；当前 CADPlan 特征树包含 %d 个可识别物理孔位。"
                % (view, target_count, planned_holes))
            local_check = {
                "id": check_id, "view": view, "area": "blind_hole_count",
                "category": "geometry", "status": status,
                "item": "原图可见孔位数量",
                "reference_evidence": "%s：%d 个可见孔位（独立盲读，完整布局）。" % (
                    target.get("image"), target_count),
                "model_evidence": "本地 CADPlan 中可识别物理孔位 %d 个。" % planned_holes,
                "evidence": evidence,
                "feature_ids": plan_hole_ids,
                "affected_parameters": hole_parameter_refs if status == "mismatch" else [],
            }
            normalized_checks.append(local_check)
            combined_checks.append(local_check)
            if status == "mismatch":
                local_issue = {
                    "issue_id": check_id, "view": view,
                    "category": "geometry", "severity": "high",
                    "item": "原图可见孔位数量不符", "evidence": evidence,
                    "feature_ids": plan_hole_ids,
                    "affected_parameters": hole_parameter_refs,
                }
                normalized_issues.append(local_issue)
                combined_issues.append(local_issue)
            else:
                combined_resolved.append({"issue_id": check_id,
                                          "check_id": check_id,
                                          "evidence": evidence})
        try:
            confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        confidences.append(confidence)
        try:
            score = float(obj.get("similarity_score", confidence))
            if 0.0 <= score <= 1.0:
                scores.append(score)
        except (TypeError, ValueError):
            pass
        observed_areas = {str(check.get("area") or "").strip().lower()
                          for check in normalized_checks}
        for area in areas:
            if area not in observed_areas:
                review_gaps.append("%s 单视图复核没有返回 %s 检查项。" % (view, area))
        reports.append({"view": view, "checks": normalized_checks,
                        "issues": normalized_issues,
                        "reference_images": image_evidence,
                        "confidence": confidence})

    for check in combined_checks:
        if str(check.get("status") or "").lower() not in {"mismatch", "partial"}:
            continue
        check_paths = {(str(row.get("feature_id")), str(row.get("path")))
                       for row in check.get("affected_parameters") or []
                       if isinstance(row, dict)}
        matched_issue = any(
            str(issue.get("view")) == str(check.get("view")) and (
                str(issue.get("issue_id")) == str(check.get("id")) or
                str(issue.get("item")) == str(check.get("item")) or
                bool(check_paths & {
                    (str(row.get("feature_id")), str(row.get("path")))
                    for row in issue.get("affected_parameters") or []
                    if isinstance(row, dict)}))
            for issue in combined_issues)
        if matched_issue:
            continue
        combined_issues.append({
            "issue_id": check.get("id") or "%s.%s" % (
                check.get("view"), check.get("area")),
            "view": check.get("view"),
            "category": check.get("category") or "geometry",
            "severity": "high" if check.get("status") == "mismatch" else "medium",
            "item": check.get("item") or "%s %s mismatch" % (
                check.get("view"), check.get("area")),
            "evidence": check.get("evidence") or "单视图检查发现几何偏差。",
            "feature_ids": check.get("feature_ids") or [],
            "affected_parameters": check.get("affected_parameters") or [],
        })
    comparable_inventory_images = {
        str(image_row.get("image") or "")
        for report in reports
        for image_row in report.get("reference_images") or []
        if isinstance(image_row, dict) and image_row.get("comparable") is True}
    passed_group_sources = {}
    for group in independent_groups:
        group_id = str(group.get("id"))
        if str(group.get("image") or "") not in comparable_inventory_images:
            continue
        matching = [check for check in combined_checks
                    if str(check.get("feature_group_id") or "") == group_id]
        if any(str(check.get("status") or "").lower() == "match"
               for check in matching):
            key = _inventory_group_equivalence_key(group)
            if key:
                passed_group_sources.setdefault(key, []).append(group_id)
    feature_group_checks = []
    for group in independent_groups:
        group_id = str(group.get("id"))
        matching = [check for check in combined_checks
                    if str(check.get("feature_group_id") or "") == group_id]
        comparable = str(group.get("image") or "") in comparable_inventory_images
        statuses = {str(check.get("status") or "").lower() for check in matching}
        status = ("not_comparable" if not comparable else
                  "passed" if "match" in statuses else
                  "mismatch" if statuses & {"mismatch", "partial"} else
                  "needs_review")
        cross_verified_by = []
        if comparable and status == "needs_review":
            key = _inventory_group_equivalence_key(group)
            cross_verified_by = [other_id for other_id in passed_group_sources.get(key, [])
                                 if other_id != group_id]
            if cross_verified_by:
                status = "passed"
        feature_group_checks.append({
            "feature_group_id": group_id,
            "image": group.get("image"),
            "checks": matching,
            "status": status,
            "cross_view_verified_by": cross_verified_by,
        })
        if comparable and status == "needs_review":
            review_gaps.append("独立盲读特征组 %s（%s）尚未在任何可比单视图中核对。" % (
                group_id, group.get("region") or group.get("kind") or ""))
    confidence = min(confidences) if confidences else 0.0
    has_geometry_issue = bool(combined_issues) or any(
        str(check.get("status") or "").lower() in {"mismatch", "partial"}
        for check in combined_checks)
    unknown_checks = any(str(check.get("status") or "").lower() == "unknown"
                         for check in combined_checks)
    decision = ("ask_user" if questions else
                "revise" if has_geometry_issue else
                "inspect" if unknown_checks or review_gaps else "accept")
    result = {
        "decision": decision,
        "checks": combined_checks,
        "issues": combined_issues,
        "resolved_findings": combined_resolved,
        "questions": list(dict.fromkeys(questions)),
        "review_gaps": list(dict.fromkeys(review_gaps)),
        "confidence": confidence,
        "single_view_reports": reports,
        "requested_views": requested_views,
        "reviewed_views": review_views,
        "skipped_views": skipped_views,
        "local_image_comparisons": local_image_comparisons,
        "feature_group_checks": feature_group_checks,
        "view_incompatible_findings": state.get("view_incompatible_findings", []),
        "reference_inventory": reference_inventory,
        "blind_reference_hole_targets": blind_hole_targets,
        "plan": None,
    }
    if phase == "similarity_review":
        result["similarity_score"] = sum(scores) / len(scores) if scores else confidence
        result["similarity_score_semantics"] = (
            "model_self_assessment; diagnostic_only; never overrides geometric checks")
        result["remaining_differences"] = combined_issues
    return result


def _validation_gate(plan):
    result = cadplan.validate_plan(plan)
    if result.get("state") == "needs_confirmation":
        questions = [row.get("question", str(row))
                     for row in result.get("confirmations", [])]
        return result, questions
    if result.get("state") != "valid":
        details = [row.get("message", str(row))
                   for row in result.get("errors", [])]
        raise AgentLoopError("CADPlan 校验失败：\n- " +
                             "\n- ".join(details[:10] or ["未知校验错误"]))
    return result, []


def _repair_from_findings(transcript, model_cfg, state, cancel_event,
                          plan, preview, findings, source, round_index):
    """Ask the repair stage to edit the plan, then require real geometry progress."""
    if state.get("repair_cycles", 0) >= MAX_REPAIR_CYCLES:
        return None, "已达到 %d 次自动返修上限。" % MAX_REPAIR_CYCLES
    blockers = [str(value) for value in (findings or []) if str(value).strip()]
    _emit(state.get("progress"), "复核发现阻断问题，执行自动返修 %d/%d…" %
          (state.get("repair_cycles", 0) + 1, MAX_REPAIR_CYCLES))
    context = {
        "source": source,
        "review_round": round_index,
        "repair_cycles_used": state.get("repair_cycles", 0),
        "repair_cycles_max": MAX_REPAIR_CYCLES,
        "blockers": blockers[:16],
        "unresolved_findings": _public_findings(state.get("open_multiview_findings", [])),
        "recognition": state.get("recognition"),
        "current_plan": plan,
        "current_preview": {
            "path": (preview or {}).get("preview_image"),
            "views": (preview or {}).get("preview_image_views"),
            "dimensions": (preview or {}).get("dimensions"),
            "layout": (preview or {}).get("preview_image_layout"),
        },
    }
    content = [{"type": "text", "text":
        "上一轮视觉/相似复核发现阻断项，但审查结果不能代替修模。请现在直接修改 current_plan，"
        "只改 blocker 涉及的几何参数，保留已匹配特征和来源；逐项解决 unresolved_findings，"
        "不得把未改动的旧问题标为已解决。返回完整 CADPlan，并调用 cad_validate_plan。"
        "如果证据不足以选定唯一的主要结构，保留当前几何并说明需要用户确认；禁止空转、"
        "只改证据文字或只重复审查。修订后应用会重新生成预览并进行最终复核。\n" +
        _json_text(context)}]
    image_path = (preview or {}).get("preview_image")
    if image_path and os.path.isfile(image_path):
        content.append(llm.image_content_part(image_path))
    transcript.append({"role": "user", "content": content})
    before = _geometry_plan_fingerprint(plan)
    try:
        repaired = _plan_object(_run_json_object(
            transcript, model_cfg, "repair", "受审查返修", state,
            cancel_event, max_tokens=65536))
    except Exception as exc:
        state.setdefault("automatic_repairs", []).append({
            "source": source, "round": round_index, "result": "failed",
            "error": str(exc), "blockers": blockers[:16],
        })
        return None, "返修阶段失败：%s" % exc

    after = _geometry_plan_fingerprint(repaired)
    if after == before:
        state.setdefault("automatic_repairs", []).append({
            "source": source, "round": round_index, "result": "no_geometry_change",
            "blockers": blockers[:16],
        })
        return None, "返修没有改变相关几何参数，已停止自动循环。"
    validation = cadplan.validate_plan(repaired)
    if validation.get("state") not in {"valid", "needs_confirmation"}:
        problems = validation.get("errors") or validation.get("confirmations") or []
        details = [row.get("message", str(row)) if isinstance(row, dict) else str(row)
                   for row in problems[:8]]
        state.setdefault("automatic_repairs", []).append({
            "source": source, "round": round_index, "result": "invalid_plan",
            "validation": validation, "blockers": blockers[:16],
        })
        return None, "返修后的 CADPlan 未通过校验：%s" % (
            "；".join(details) or validation.get("state"))

    state["repair_cycles"] = state.get("repair_cycles", 0) + 1
    state.setdefault("automatic_repairs", []).append({
        "source": source, "round": round_index, "result": "applied",
        "before_geometry": before, "after_geometry": after,
        "blockers": blockers[:16],
    })
    _emit(state.get("progress"), "返修计划已通过结构校验，正在重新生成预览复核…")
    return repaired, None


def _preview_for_plan(plan, state, view_set="three"):
    fingerprint = _plan_fingerprint(plan, view_set)
    cached = state["previews"].get(fingerprint)
    if cached:
        result = cached["result"]
        if result.get("state") == "previewed" and os.path.isfile(result.get("preview_image", "")):
            _publish_preview(plan, result, state, "preview")
            return result
    if state["review_preview_calls"] >= MAX_REVIEW_ROUNDS:
        raise AgentLoopError("已达到 %d 次预览复核上限，仍需要新预览才能继续；已停止。" %
                             MAX_REVIEW_ROUNDS)
    state["review_preview_calls"] += 1
    state["preview_calls"] += 1
    image_path = state["session_dir"] / (
        "preview-%02d-%s.png" % (state["preview_calls"], view_set))
    result = cadplan.preview_plan(plan, image_path=str(image_path), view_set=view_set)
    state["previews"][fingerprint] = {"result": result}
    if result.get("state") != "previewed" or not os.path.isfile(result.get("preview_image", "")):
        raise AgentLoopError("本地几何预览失败：%s" %
                             (result.get("error") or result.get("preview_image_error") or result.get("state")))
    _publish_preview(plan, result, state, "preview")
    return result


def _plan_object(response):
    obj = response if isinstance(response, dict) else _extract_object(response, "模型绘制")
    if "plan" in obj and isinstance(obj.get("plan"), dict) and "schema_version" not in obj:
        obj = obj["plan"]
    return obj


def _review_object(response, phase):
    obj = _extract_object(response, phase)
    if obj.get("decision") not in {"accept", "revise", "ask_user"}:
        raise AgentLoopError("%s阶段 decision 必须是 accept、revise 或 ask_user。" % phase)
    if obj["decision"] == "revise" and not isinstance(obj.get("plan"), dict):
        raise AgentLoopError("%s阶段要求修订，但没有返回完整 CADPlan。" % phase)
    return obj


def _questions_reply(questions, prefix="需要补充信息"):
    clean = [str(question).strip() for question in (questions or []) if str(question).strip()]
    if not clean:
        clean = ["请补充无法从当前文字或图片确认的关键尺寸/结构信息。"]
    return prefix + "：\n" + "\n".join("- " + question for question in clean[:8])


def _repairable_review_blockers(blockers):
    non_geometric = ("审查证据不足：", "审查要求用户确认：")
    return [item for item in blockers
            if not str(item).startswith(non_geometric)]


def _review_requires_user_confirmation(blockers):
    return any(str(item).startswith("审查要求用户确认：") for item in blockers)


_GEOMETRY_REVIEW_TERMS = (
    "轮廓", "比例", "宽", "高", "厚", "深", "前表面", "后表面", "后退",
    "错层", "齐平", "分段", "孔", "槽", "开口", "贯穿", "位置", "遮挡",
    "实体", "拓扑", "尺寸", "长度", "width", "height", "depth", "thickness",
    "outline", "proportion", "segment", "hole", "slot", "through", "setback",
    "flush", "topology", "feature count", "front face", "back face", "occlusion",
)


def _is_geometry_review_item(item):
    category = str(item.get("category") or "").strip().lower()
    if category in {"geometry", "structure", "proportion", "topology", "feature"}:
        return True
    if category in {"appearance", "rendering", "material", "color", "cosmetic"}:
        return False
    text = " ".join(str(item.get(key) or "") for key in (
        "id", "issue_id", "item", "evidence", "view"))
    return any(term in text for term in _GEOMETRY_REVIEW_TERMS)


def _finding_id(item, phase, index):
    explicit = item.get("issue_id") or item.get("id")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()[:120]
    raw = "%s|%s|%s" % (phase, item.get("view", ""), item.get("item", ""))
    raw = re.sub(r"\d+(?:\.\d+)?", "#", raw.casefold())
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return "%s-%s-%d" % (phase, digest, index)


def _canonical_geometry_value(value):
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 12)
    if isinstance(value, dict):
        return {key: _canonical_geometry_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_geometry_value(item) for item in value]
    return value


def _feature_geometry_signature(feature):
    value = {key: feature.get(key) for key in ("kind", "operation", "parameters", "relations")
             if key in feature}
    value = _canonical_geometry_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _geometry_plan_fingerprint(plan):
    value = {
        "units": plan.get("units"),
        "coordinate_system": plan.get("coordinate_system"),
        "features": [
            {"id": item.get("id"), "geometry": _feature_geometry_signature(item)}
            for item in plan.get("features", []) if isinstance(item, dict)
        ],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan_feature_map(plan):
    return {str(item.get("id")): item for item in plan.get("features", [])
            if isinstance(item, dict) and item.get("id") is not None}


def _feature_path_value(feature, path):
    keys = [key for key in str(path or "").split(".") if key]
    current = feature
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return {"present": False, "value": None}
    return {"present": True, "value": current}


def _finding_snapshot(issue, plan, phase, index, round_index):
    feature_ids = [str(value) for value in issue.get("feature_ids", [])
                   if value is not None] if isinstance(issue.get("feature_ids"), list) else []
    affected = issue.get("affected_parameters") or []
    paths = []
    if isinstance(affected, list):
        for row in affected:
            if not isinstance(row, dict):
                continue
            feature_id = str(row.get("feature_id") or "")
            path = str(row.get("path") or row.get("parameter") or "")
            if feature_id and path:
                if not path.startswith("parameters."):
                    path = "parameters." + path
                paths.append({"feature_id": feature_id, "path": path})
    for row in paths:
        if row["feature_id"] not in feature_ids:
            feature_ids.append(row["feature_id"])

    features = _plan_feature_map(plan)
    path_values = {}
    for row in paths:
        feature = features.get(row["feature_id"])
        path_values[row["feature_id"] + "." + row["path"]] = (
            _feature_path_value(feature, row["path"]) if feature else
            {"present": False, "value": None})
    feature_values = {
        feature_id: _feature_geometry_signature(features[feature_id])
        for feature_id in feature_ids if feature_id in features
    }
    return {
        "issue_id": _finding_id(issue, phase, index),
        "phase": phase,
        "view": str(issue.get("view") or "多视图"),
        "item": str(issue.get("item") or issue.get("evidence") or "几何偏差"),
        "evidence": str(issue.get("evidence") or ""),
        "severity": str(issue.get("severity") or "medium").lower(),
        "category": str(issue.get("category") or "geometry"),
        "feature_ids": feature_ids,
        "affected_parameters": paths,
        "baseline_path_values": path_values,
        "baseline_feature_values": feature_values,
        "first_seen_round": round_index,
    }


def _finding_geometry_changed(finding, plan):
    features = _plan_feature_map(plan)
    paths = finding.get("baseline_path_values") or {}
    if paths:
        for key, baseline in paths.items():
            feature_id, path = key.split(".", 1)
            feature = features.get(feature_id)
            current = (_feature_path_value(feature, path) if feature else
                       {"present": False, "value": None})
            if json.dumps(_canonical_geometry_value(current), ensure_ascii=False, sort_keys=True) != \
                    json.dumps(_canonical_geometry_value(baseline), ensure_ascii=False, sort_keys=True):
                return True
        return False
    baselines = finding.get("baseline_feature_values") or {}
    if baselines:
        return any(feature_id not in features or
                   _feature_geometry_signature(features[feature_id]) != signature
                   for feature_id, signature in baselines.items())
    return False


def _finding_resolution_matches_check(resolution, review, finding):
    """Tie a finding's closure to a matching check for the same geometry."""
    check_id = str(resolution.get("check_id") or "").strip()
    if not check_id:
        return False
    check = next((item for item in (review.get("checks") or [])
                  if isinstance(item, dict)
                  and str(item.get("id") or "").strip() == check_id), None)
    if (not check or str(check.get("status") or "").strip().lower() != "match"
            or not str(check.get("evidence") or "").strip()
            or not _is_geometry_review_item(check)):
        return False

    def parameter_refs(rows):
        refs = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            feature_id = str(row.get("feature_id") or "")
            path = str(row.get("path") or row.get("parameter") or "")
            if feature_id and path:
                if not path.startswith("parameters."):
                    path = "parameters." + path
                refs.add((feature_id, path))
        return refs

    finding_refs = parameter_refs(finding.get("affected_parameters"))
    check_refs = parameter_refs(check.get("affected_parameters"))
    if finding_refs:
        return bool(finding_refs & check_refs)
    finding_features = {str(value) for value in (finding.get("feature_ids") or [])}
    check_features = {str(value) for value in (check.get("feature_ids") or [])}
    return bool(finding_features & check_features)


def _actionable_multiview_findings(review, plan, round_index):
    findings = []
    for index, issue in enumerate(review.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status") or "open").lower()
        category = str(issue.get("category") or "").strip().lower()
        is_geometry = _is_geometry_review_item(issue)
        if ((issue.get("blocking") is False and not is_geometry)
                or (status in {"match", "resolved", "pass", "closed"} and not is_geometry)
                or (category in {"appearance", "rendering", "material", "color", "cosmetic"}
                    and not issue.get("requires_revision"))):
            continue
        # The prompt contract reserves `issues` for unresolved findings. Even a
        # low-severity geometric issue needs correction or explicit disposition.
        findings.append(_finding_snapshot(issue, plan, "multiview", index, round_index))
    for index, check in enumerate(review.get("checks") or []):
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").lower()
        if status not in {"mismatch", "partial"} or not _is_geometry_review_item(check):
            continue
        check_refs = {(str(row.get("feature_id")), str(row.get("path")))
                      for row in check.get("affected_parameters") or []
                      if isinstance(row, dict)}
        if any(
                str(existing.get("view")) == str(check.get("view"))
                and (str(existing.get("issue_id")) == str(check.get("id"))
                     or bool(check_refs & {
                         (str(row.get("feature_id")), str(row.get("path")))
                         for row in existing.get("affected_parameters") or []
                         if isinstance(row, dict)}))
                for existing in findings):
            continue
        finding = dict(check)
        finding.setdefault("item", check.get("name") or "多视图几何核对")
        finding.setdefault("severity", "medium" if status == "mismatch" else "low")
        findings.append(_finding_snapshot(finding, plan, "multiview", 1000 + index, round_index))
    return findings


def _review_evidence_blockers(review, phase, required_views=None):
    """Require paired image evidence for the views selected for this task."""
    blockers = []
    geometry_checks = [check for check in (review.get("checks") or [])
                       if isinstance(check, dict) and _is_geometry_review_item(check)]
    if not geometry_checks:
        blockers.append("审查证据不足：%s没有提供几何检查项。" % phase)

    selected = list(dict.fromkeys(required_views or
                                  [str(check.get("view") or "")
                                   for check in geometry_checks]))
    if not selected:
        blockers.append("审查证据不足：没有审查任何模型视图。")
    for view in selected:
        areas = REVIEW_VIEW_AREAS.get(view, ())
        for area in areas:
            covered = any(
                str(check.get("view") or "").strip().lower() == view
                and str(check.get("area") or "").strip().lower() == area
                for check in geometry_checks)
            if not covered:
                blockers.append("审查证据不足：%s缺少 %s 视图的 %s 检查。" %
                                (phase, view, area))

    for check in geometry_checks:
        status = str(check.get("status") or "").strip().lower()
        label = str(check.get("id") or check.get("item") or "几何检查")
        if status not in {"match", "mismatch", "partial", "unknown"}:
            blockers.append("审查证据不足：几何检查 %s 缺少有效状态。" % label)
        if not str(check.get("evidence") or "").strip():
            blockers.append("审查证据不足：几何检查 %s 缺少图像或参数证据。" % label)
        if status == "match" and (
                not str(check.get("reference_evidence") or "").strip()
                or not str(check.get("model_evidence") or "").strip()):
            blockers.append("审查证据不足：%s 缺少原图与模型视图的分别描述。" % label)
        if status == "unknown" and not str(check.get("unknown_reason") or "").strip():
            blockers.append("审查证据不足：几何检查 %s 标为 unknown，但没有说明不可判断原因。" % label)

    questions = [str(item).strip() for item in (review.get("questions") or [])
                 if str(item).strip()]
    blockers.extend("审查要求用户确认：%s" % item for item in questions)
    return list(dict.fromkeys(blockers))


def _public_findings(findings):
    return [{key: finding.get(key) for key in (
        "issue_id", "view", "item", "evidence", "severity", "category",
        "feature_ids", "affected_parameters", "first_seen_round")}
        for finding in findings]


def _update_multiview_findings(previous, review, plan, round_index):
    current = _actionable_multiview_findings(review, plan, round_index)
    current_by_id = {item["issue_id"]: item for item in current}
    resolutions = {}
    for row in review.get("resolved_findings") or []:
        if isinstance(row, dict):
            issue_id = row.get("issue_id") or row.get("id")
            if issue_id:
                resolutions[str(issue_id)] = row

    pending = []
    resolved_ids = []
    previous_by_id = {item["issue_id"]: item for item in previous}
    for old in previous:
        issue_id = old["issue_id"]
        if issue_id in current_by_id:
            merged = dict(old)
            merged.update({key: current_by_id[issue_id].get(key) for key in
                           ("severity", "evidence", "view", "item")})
            merged["last_seen_round"] = round_index
            pending.append(merged)
            continue
        resolution = resolutions.get(issue_id)
        if (resolution and str(resolution.get("evidence") or "").strip()
                and _finding_geometry_changed(old, plan)
                and _finding_resolution_matches_check(resolution, review, old)):
            resolved_ids.append(issue_id)
            continue
        carried = dict(old)
        carried["last_seen_round"] = round_index
        if resolution:
            carried["resolution_rejected"] = (
                "结案证据未同时满足：参数有变化，且当前视图存在关联几何 match 检查；仍按未解决处理。")
        pending.append(carried)

    for issue_id, finding in current_by_id.items():
        if issue_id not in previous_by_id:
            pending.append(finding)
    return pending, resolved_ids, current


def _multiview_acceptance_blockers(review, pending_findings=None):
    blockers = []
    pending_findings = pending_findings or []
    pending_ids = {finding.get("issue_id") for finding in pending_findings}
    for finding in _actionable_multiview_findings(review, {}, 0):
        if finding.get("issue_id") in pending_ids:
            continue
        blockers.append("%s：%s" % (
            finding.get("view", "多视图"),
            finding.get("evidence") or finding.get("item") or "存在未解决的几何偏差"))
    for finding in pending_findings:
        blockers.append("上一轮问题仍未证实已修复（%s）：%s" % (
            finding.get("issue_id", "finding"), finding.get("item", "几何偏差")))
    blockers.extend(_review_evidence_blockers(review, "多视图审查"))
    return list(dict.fromkeys(blockers))


def _similarity_acceptance_blockers(review):
    blockers = []
    checks = [check for check in (review.get("checks") or [])
              if isinstance(check, dict) and _is_geometry_review_item(check)]
    for check in checks:
        status = str(check.get("status", "")).lower()
        if status not in {"mismatch", "partial"} or not _is_geometry_review_item(check):
            continue
        blockers.append("%s：%s" % (
            check.get("item", "相似度核对"),
            check.get("evidence") or
            ("关键几何仍只有部分确认" if status == "partial" else "仍有明确不匹配项")))
    blockers.extend(_review_evidence_blockers(review, "相似审查"))
    for difference in review.get("remaining_differences") or []:
        if isinstance(difference, dict):
            category = str(difference.get("category") or "").strip().lower()
            if category in {"appearance", "rendering", "material", "color", "cosmetic"}:
                continue
            if _is_geometry_review_item(difference):
                blockers.append("相似审查仍有未结差异：%s" % (
                    difference.get("evidence") or difference.get("item") or str(difference)))
        elif _is_geometry_review_item({"item": str(difference)}):
            blockers.append("相似审查仍有未结几何差异：%s" % str(difference))
    return list(dict.fromkeys(blockers))


def _front_segment_ratios_from_recognition(recognition):
    observations = recognition.get("proportion_observations") or {}
    values = observations.get("front_segment_width_ratios")
    if not isinstance(values, list) or len(values) != 3:
        return None
    try:
        ratios = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if any(value <= 0 for value in ratios):
        return None
    total = sum(ratios)
    return [value / total for value in ratios]


def _front_segment_ratios_from_plan(plan):
    """Measure three aligned additive box segments when their intent is clear."""
    boxes = []
    for item in plan.get("features", []):
        if item.get("kind") != "box" or item.get("operation") != "add":
            continue
        params = item.get("parameters") or {}
        center = params.get("center")
        try:
            width = float(params["length"])
            height = float(params["height"])
            x = float(center[0])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if width > 0 and height > 0:
            boxes.append({"x": x, "width": width, "height": height})
    if len(boxes) < 3:
        return None
    max_height = max(item["height"] for item in boxes)
    aligned = [item for item in boxes
               if abs(item["height"] - max_height) <= max_height * 0.03]
    if len(aligned) != 3:
        return None
    aligned.sort(key=lambda item: item["x"])
    widths = [item["width"] for item in aligned]
    total = sum(widths)
    if total <= 0:
        return None
    return [width / total for width in widths]


def _front_segment_ratio_mismatch(plan, recognition):
    expected = _front_segment_ratios_from_recognition(recognition)
    actual = _front_segment_ratios_from_plan(plan)
    if expected is None or actual is None:
        return None
    rows = []
    for index, (target, built) in enumerate(zip(expected, actual), start=1):
        tolerance = max(0.05, target * 0.25)
        if abs(target - built) > tolerance:
            rows.append({"segment": index, "expected_ratio": round(target, 3),
                         "plan_ratio": round(built, 3), "tolerance": round(tolerance, 3)})
    if not rows:
        return None
    return {"expected": expected, "actual": actual, "differences": rows}


def _middle_front_setback_ratio_from_recognition(recognition):
    observations = recognition.get("proportion_observations") or {}
    try:
        value = float(observations.get("middle_front_setback_height_ratio"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _middle_front_setback_ratio_from_plan(plan):
    """Measure middle front-face setback for three aligned center-based boxes."""
    boxes = []
    for item in plan.get("features", []):
        if item.get("kind") != "box" or item.get("operation") != "add":
            continue
        params = item.get("parameters") or {}
        center = params.get("center")
        try:
            x = float(center[0])
            y = float(center[1])
            depth = float(params["width"])
            height = float(params["height"])
            length = float(params["length"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if min(depth, height, length) > 0:
            boxes.append({"x": x, "y": y, "depth": depth,
                          "height": height, "length": length})
    if len(boxes) != 3:
        return None

    max_height = max(item["height"] for item in boxes)
    if max_height <= 0 or any(
            abs(item["height"] - max_height) > max_height * 0.03 for item in boxes):
        return None
    boxes.sort(key=lambda item: item["x"])
    side_fronts = [boxes[index]["y"] + boxes[index]["depth"] / 2
                   for index in (0, 2)]
    side_tolerance = max(0.001, max_height * 0.02)
    if abs(side_fronts[0] - side_fronts[1]) > side_tolerance:
        return None
    middle_front = boxes[1]["y"] + boxes[1]["depth"] / 2
    return (sum(side_fronts) / 2 - middle_front) / max_height


def _middle_front_setback_ratio_mismatch(plan, recognition):
    expected = _middle_front_setback_ratio_from_recognition(recognition)
    actual = _middle_front_setback_ratio_from_plan(plan)
    if expected is None or actual is None:
        return None
    tolerance = max(0.02, expected * 0.35)
    if abs(expected - actual) <= tolerance:
        return None
    return {"expected": round(expected, 4), "actual": round(actual, 4),
            "tolerance": round(tolerance, 4)}


def _front_segment_gap_mismatch(plan):
    """Detect gaps or overlaps between a model's three additive front segments."""
    boxes = []
    for item in plan.get("features", []):
        if item.get("kind") != "box" or item.get("operation") != "add":
            continue
        params = item.get("parameters") or {}
        center = params.get("center")
        try:
            x = float(center[0])
            width = float(params["length"])
            height = float(params["height"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if width > 0 and height > 0:
            boxes.append({"x": x, "width": width, "height": height})
    if len(boxes) != 3:
        return None
    max_height = max(item["height"] for item in boxes)
    if any(abs(item["height"] - max_height) > max_height * 0.03 for item in boxes):
        return None
    boxes.sort(key=lambda item: item["x"])
    total_width = sum(item["width"] for item in boxes)
    tolerance = max(0.25, total_width * 0.01)
    gaps = []
    for left, right in zip(boxes, boxes[1:]):
        gap = ((right["x"] - right["width"] / 2)
               - (left["x"] + left["width"] / 2))
        if abs(gap) > tolerance:
            gaps.append({"between": [left["x"], right["x"]],
                         "gap_mm": round(gap, 3),
                         "tolerance_mm": round(tolerance, 3)})
    return {"gaps": gaps, "boxes": boxes} if gaps else None


def _user_permits_image_estimates(prompt, has_reference_images=False):
    text = str(prompt or "").lower()
    if any(term in text for term in (
            "估算", "按比例", "图像估值", "图像估算", "image_estimate", "estimate", "approximate")):
        return True
    image_modeling_terms = (
        "按图复现", "按图建模", "照着图", "按参考图", "根据图片", "根据图像",
        "参考图建模", "复刻", "复现这个", "重建这个",
        "reproduce", "recreate", "build from the reference", "model from the image")
    return bool(has_reference_images and (
        not text or any(term in text for term in image_modeling_terms)))


def _image_depth_questions_can_be_estimated(prompt, questions, plan,
                                            has_reference_images=False):
    if not _user_permits_image_estimates(prompt, has_reference_images) or not questions:
        return False
    actionable_questions = []
    for question in questions:
        text = str(question).lower()
        if ("制造级精度" in text or "制造级尺寸" in text
                or "实测尺寸" in text) and any(
                    marker in text for marker in ("image_estimate", "图像估算", "全部尺寸")):
            continue
        actionable_questions.append(text)
    if not actionable_questions:
        return False
    depth_terms = ("深度", "厚度", "后表面", "背面", "后退", "y 向", "y向",
                   "depth", "thickness", "rear face", "back face")
    unrelated_terms = ("孔", "槽", "数量", "拓扑", "材料", "材质", "连接",
                        "安装", "螺纹", "hole", "slot", "cavity", "count", "topology")
    for text in actionable_questions:
        if (not any(term in text for term in depth_terms)
                or any(term in text for term in unrelated_terms)):
            return False
    estimated_boxes = []
    for item in plan.get("features", []):
        if item.get("kind") != "box" or item.get("operation") != "add":
            continue
        params = item.get("parameters") or {}
        sources = item.get("sources") or {}
        width = sources.get("width") or {}
        try:
            if float(params.get("width")) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        if not isinstance(width, dict) or width.get("type") != "image_estimate":
            return False
        estimated_boxes.append(item)
    return bool(estimated_boxes)


def _front_outline_ratio_mismatch(recognition, raster_analysis, preview):
    dimensions = (preview or {}).get("dimensions") or {}
    size = dimensions.get("size") or []
    try:
        actual = float(size[0]) / float(size[2])
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    if actual <= 0:
        return None

    expected = None
    front_views = [row for row in recognition.get("views", [])
                   if isinstance(row, dict) and row.get("view") == "front"]
    image_measurements = (raster_analysis or {}).get("images", [])
    for view in front_views:
        reference = str(view.get("image", "")).strip()
        match = next((row for row in image_measurements
                      if row.get("filename") == reference
                      or str(row.get("image_index")) == reference), None)
        bbox = (match or {}).get("foreground_bbox_candidate") or {}
        if bbox.get("confidence") == "high":
            try:
                expected = float(bbox["width_height_ratio"])
                break
            except (KeyError, TypeError, ValueError):
                pass
    if expected is None:
        try:
            expected = float((recognition.get("proportion_observations") or {})
                             .get("front_width_height"))
        except (TypeError, ValueError):
            return None
    if expected <= 0:
        return None
    tolerance = max(0.08, expected * 0.25)
    if abs(expected - actual) <= tolerance:
        return None
    return {"expected": round(expected, 3), "actual": round(actual, 3),
            "tolerance": round(tolerance, 3)}


def _depth_outline_ratio_mismatch(recognition, preview):
    observations = (recognition or {}).get("proportion_observations") or {}
    try:
        expected = float(observations.get("depth_height"))
        size = ((preview or {}).get("dimensions") or {}).get("size") or []
        actual = float(size[1]) / float(size[2])
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    if expected <= 0 or actual <= 0:
        return None
    # Depth is harder to estimate from an isometric image than front width.
    # Keep this gate deliberately tolerant; the per-view visual review remains
    # responsible for deciding whether the depth relationship is actually wrong.
    tolerance = max(0.10, expected * 0.35)
    if abs(expected - actual) <= tolerance:
        return None
    return {"expected": round(expected, 3), "actual": round(actual, 3),
            "tolerance": round(tolerance, 3)}


def _metadata_only_confirmations(validation):
    confirmations = validation.get("confirmations") or []
    if not confirmations:
        return False
    markers = ("请标明参数", "请为数值参数标明来源", "图像估算参数")
    return all(any(marker in str(item.get("question", "")) for marker in markers)
               for item in confirmations if isinstance(item, dict))


def _same_plan_except_sources(left, right):
    def without_sources(value):
        if isinstance(value, dict):
            return {key: without_sources(item) for key, item in value.items()
                    if key != "sources"}
        if isinstance(value, list):
            return [without_sources(item) for item in value]
        return value
    return without_sources(left) == without_sources(right)


def _record_value(value, session_dir):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "preview_image" and isinstance(item, str):
                try:
                    result[key] = Path(item).resolve().relative_to(
                        session_dir.resolve()).as_posix()
                except (OSError, ValueError):
                    result[key] = os.path.basename(item)
            else:
                result[key] = _record_value(item, session_dir)
        return result
    if isinstance(value, (list, tuple)):
        return [_record_value(item, session_dir) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _summarize_conversation_history(rows, previous_summary, cfg, deadline,
                                   cancel_event=None, state=None):
    """Compress newly aged turns into durable conversational context."""
    previous_summary = str(previous_summary or "").strip()
    if not rows:
        return previous_summary
    lines = []
    for row in rows:
        if not isinstance(row, dict) or row.get("role") not in {"user", "assistant"}:
            continue
        content = str(row.get("content") or "").strip()
        if content:
            role = "用户" if row["role"] == "user" else "助手"
            lines.append("[%s] %s" % (role, content[-3000:]))

    # Summaries may retain a small number of original user references, but
    # never attach assistant-generated previews or accumulate them as evidence.
    image_paths = []
    image_labels = []
    for role, quota in (("user", 2),):
        selected = 0
        for row in reversed(rows):
            if not isinstance(row, dict) or row.get("role") != role:
                continue
            for candidate in reversed(row.get("image_paths") or []):
                try:
                    path = os.path.abspath(os.fspath(candidate))
                except (TypeError, ValueError):
                    continue
                if os.path.isfile(path) and path not in image_paths:
                    image_paths.append(path)
                    image_labels.append(
                        "%d=%s:%s" % (
                            len(image_paths),
                            "用户原始参考图",
                            os.path.basename(path)))
                    selected += 1
                    if selected >= quota:
                        break
            if selected >= quota:
                break
    content = (
        "已有长期摘要：\n%s\n\n本次新增、即将移出近期窗口的对话：\n%s\n\n"
        "请合并摘要，保留对后续建模和连续讨论仍有效的信息。" %
        (previous_summary or "（无）", "\n".join(lines) or "（无文字内容）"))
    if image_labels:
        content += "\n随附图片的顺序与来源：" + "；".join(image_labels)
    _check_cancel(cancel_event, deadline)
    try:
        messages = [{"role": "system", "content": _prompt("context_summary")},
                    {"role": "user", "content": llm.user_content(content, image_paths)}]
        if state is not None:
            response = _completion(messages, cfg, _remaining_timeout(deadline),
                                   4096, state, "context_summary")
        else:
            response = llm.chat_completion(
                messages, cfg, timeout=_remaining_timeout(deadline), max_tokens=4096,
                cancel_event=cancel_event)
        value = llm._extract_json(_message_text(response))
        summary = str(value.get("summary") or "").strip() if isinstance(value, dict) else ""
        if not summary:
            summary = _message_text(response).strip()
    except llm.ModelRequestCancelled:
        raise                     # 用户停止不是"摘要失败"，绝不静默回退继续跑
    except Exception:
        # Keep the turn usable if a provider cannot summarize. The extractive
        # fallback is explicit text from the old turns, never invented state.
        summary = (previous_summary + "\n" if previous_summary else "") + "\n".join(lines)
    summary = summary.strip()
    if len(summary) > MAX_CONTEXT_SUMMARY_CHARS:
        summary = summary[-MAX_CONTEXT_SUMMARY_CHARS:]
    return summary


def _write_run_record(session_dir, prompt, image_paths, state,
                      result=None, error=None, cfg=None, started_at=None):
    session_dir = Path(session_dir)
    llmcfg = (cfg or {}).get("llm", cfg or {}) if isinstance(cfg, dict) else {}
    if not isinstance(llmcfg, dict):
        llmcfg = {}
    message = str(error) if error is not None else None
    secret = str(llmcfg.get("api_key") or "")
    if message and secret:
        message = message.replace(secret, "[REDACTED]")
    result = result if isinstance(result, dict) else {}

    image_names = []
    for path_value in image_paths or []:
        try:
            image_names.append(os.path.basename(os.fspath(path_value)))
        except (TypeError, ValueError):
            image_names.append(str(path_value)[:128])
    record = {
        "record_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "state": result.get("state") or ("failed" if error is not None else "unknown"),
        "input": {
            "prompt": str(prompt or ""),
            "reference_images": image_names,
        },
        "routing": state.get("routing"),
        "conversation_memory": {
            "summarized_through": state.get("conversation_summary_through", 0),
            "summary_chars": len(state.get("conversation_summary") or ""),
        },
        "limits": state.get("limits") or {
            "tool_calls": MAX_TOOL_CALLS,
            "web_search_calls": MAX_WEB_SEARCH_CALLS,
            "tool_preview_calls": MAX_TOOL_PREVIEW_CALLS,
            "multiview_tool_calls_per_round": MAX_REVIEW_PREVIEW_CALLS_PER_ROUND,
            "review_rounds": MAX_REVIEW_ROUNDS,
            "context_turns": MAX_CONTEXT_TURNS,
            "context_chars": MAX_CONTEXT_CHARS,
        },
        "selected_reference_images": state.get("selected_reference_images", []),
        "active_reference_images": state.get("active_reference_images", []),
        "active_request": state.get("active_request"),
        "model_calls": state.get("model_calls", []),
        "temporary_review_images": state.get("temporary_review_images", []),
        "recognition": state.get("recognition"),
        "raster_analysis": state.get("raster_analysis"),
        "counts": {
            "tool_calls": state.get("tool_calls", result.get("tool_calls", 0)),
            "web_search_calls": state.get("web_search_calls", result.get("web_search_calls", 0)),
            "blocked_web_search_calls": state.get("blocked_web_search_calls", 0),
            "preview_calls": state.get("preview_calls", result.get("preview_calls", 0)),
            "review_rounds": result.get("review_rounds", 0),
            "repair_cycles": state.get("repair_cycles", 0),
            "llm_requests": len(state.get("model_calls", [])),
            "image_parts_in_request": sum(
                int(call.get("image_parts_in_request", 0) or 0)
                for call in state.get("model_calls", [])),
            "llm_usage": {
                key: sum(float((call.get("usage") or {}).get(key, 0) or 0)
                         for call in state.get("model_calls", []))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                            "input_tokens", "output_tokens", "reasoning_tokens")
                if any(key in (call.get("usage") or {})
                       for call in state.get("model_calls", []))
            },
        },
        "loop_state": {
            "agent_turn": state.get("agent_turn"),
            "no_progress_finals": state.get("no_progress_finals"),
            "context_compactions": state.get("context_compactions", 0),
            "truncated_responses": state.get("truncated_responses", 0),
            "stagnation_warnings": state.get("stagnation_warnings", 0),
            "stagnant_tool_calls": state.get("stagnant_tool_calls", 0),
            "candidate_attempted": state.get("candidate_attempted", False),
            "finish_gate": state.get("finish_gate"),
        },
        "construction_repairs": state.get("construction_repairs", []),
        "automatic_repairs": state.get("automatic_repairs", []),
        "open_multiview_findings": state.get("open_multiview_findings", []),
        "plan": result.get("plan"),
        "validation": result.get("validation"),
        "preview": result.get("preview"),
        "output_paths": result.get("output_paths", {}),
        "output_warning": result.get("output_warning"),
        "dimension_assumptions": result.get("dimension_assumptions", []),
        "local_geometry_checks": result.get("local_geometry_checks"),
        "multiview_review": result.get("review"),
        "similarity_review": result.get("similarity"),
        "review_history": state.get("review_history", result.get("review_history", [])),
        "questions": result.get("questions"),
        "reply": result.get("reply"),
        "tool_trace": state.get("tool_trace", result.get("tool_trace", [])),
        "error": message,
    }
    record = _record_value(record, session_dir)
    path = session_dir / "run.json"
    temporary = session_dir / ".run.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _run_agent_loop_impl(prompt: str, image_paths=None, cfg=None,
                         conversation_history=None,
                         conversation_summary=None, summary_through=0,
                         progress: Optional[Callable[[str], None]] = None,
                         cancel_event=None, output_dir=None, current_plan=None,
                         current_document=None,
                         on_preview=None,
                         _session_dir=None, _state_out=None) -> Dict[str, Any]:
    """Run one conversational turn through the persistent model-directed loop.

    A successful result includes a validated CADPlan and local preview metadata.
    It does not create or synchronize any CATIA document.
    """
    prompt = str(prompt or "").strip()
    image_paths = [os.path.abspath(os.fspath(path)) for path in (image_paths or [])]
    if not prompt and not image_paths:
        raise AgentLoopError("请先输入需求文字或附上参考图。")
    if len(image_paths) > MAX_REFERENCE_IMAGES:
        raise AgentLoopError("每轮最多附加 %d 张参考图。" % MAX_REFERENCE_IMAGES)
    for path in image_paths:
        # Validate paths, MIME types and size before any network request.
        llm.image_content_part(path)

    cfg = cfg if isinstance(cfg, dict) else {}
    llmcfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else cfg
    total_timeout = cfg.get("agentloop", {}).get("total_timeout_sec", DEFAULT_TOTAL_TIMEOUT) \
        if isinstance(cfg.get("agentloop"), dict) else DEFAULT_TOTAL_TIMEOUT
    try:
        total_timeout = max(60, min(1800, int(total_timeout)))
    except (TypeError, ValueError):
        total_timeout = DEFAULT_TOTAL_TIMEOUT
    deadline = time.monotonic() + total_timeout
    session_dir = Path(_session_dir) if _session_dir else _make_session_dir(output_dir)
    state = _state_out if isinstance(_state_out, dict) else {}
    state.update({
        "cfg": cfg, "deadline": deadline, "session_dir": session_dir,
        "model_calls": [], "temporary_review_images": [],
        "limits": {
            "tool_calls": MAX_TOOL_CALLS,
            "web_search_calls": MAX_WEB_SEARCH_CALLS,
            "tool_preview_calls": MAX_TOOL_PREVIEW_CALLS,
            "multiview_tool_calls_per_round": MAX_REVIEW_PREVIEW_CALLS_PER_ROUND,
            "review_rounds": MAX_TOOL_CALLS,
            "context_turns": _configured_limit(
                cfg, "context_turns", MAX_CONTEXT_TURNS, 16, 8192),
            "context_chars": _configured_limit(
                cfg, "context_chars", MAX_CONTEXT_CHARS, 48000, 5000000),
            "context_summary_chars": MAX_CONTEXT_SUMMARY_CHARS,
        },
        "tool_calls": 0, "web_search_calls": 0, "preview_calls": 0,
        "web_search_allowed": bool((cfg.get("online") or {}).get("enabled", True)),
        "blocked_web_search_calls": 0, "construction_repairs": [],
        "automatic_repairs": [], "repair_cycles": 0,
        "open_multiview_findings": [],
        "tool_preview_calls": 0, "review_preview_calls": 0,
        "previews": {}, "tool_trace": [], "progress": progress,
        "review_history": [], "routing": None, "on_preview": on_preview,
        "conversation_summary": str(conversation_summary or "").strip(),
    })
    if isinstance(current_document, dict) and current_document.get("features"):
        try:
            current_plan = plan_state.from_document(current_document, current_plan)
            state["document_imported"] = True
        except ValueError as exc:
            message = str(exc)
            return {
                "state": "needs_user_input", "plan": None, "preview": {},
                "validation": {"state": "unrepresentable_document",
                               "errors": [{"message": message}]},
                "reply": "当前本地模型无法安全转换为可编辑 CADPlan：" + message,
                "questions": [message],
                "routing": {"action": "clarify", "review_mode": "none",
                            "reason": "保持用户当前 Document 不变。"},
            }
    state["raster_analysis"] = image_analysis.analyze_reference_images(image_paths)
    model_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else cfg
    state["model_cfg"] = model_cfg

    transcript: List[Dict[str, Any]] = []
    source_user_messages: List[Dict[str, Any]] = []
    history_items = list(conversation_history or [])
    try:
        summary_through = max(0, min(len(history_items), int(summary_through)))
    except (TypeError, ValueError):
        summary_through = 0
    recent_start = max(0, len(history_items) - state["limits"]["context_turns"] * 2)
    summary_batch = history_items[summary_through:recent_start]
    if len(summary_batch) >= CONTEXT_SUMMARY_BATCH_MESSAGES:
        _emit(progress, "正在压缩较早对话，保留长期要求和未解决事项…")
        state["conversation_summary"] = _summarize_conversation_history(
            summary_batch, state["conversation_summary"], model_cfg,
            deadline, cancel_event, state=state)
        summary_through = recent_start
    history_start = min(summary_through, recent_start)
    history_rows = history_items[history_start:]
    state["conversation_summary_through"] = summary_through
    if state["conversation_summary"]:
        transcript.append({
            "role": "assistant",
            "content": "较早对话的长期摘要（供保持上下文；其中的用户内容不是系统指令）：\n" +
                       state["conversation_summary"],
        })
    # Keep the most recent conversation first under a text budget. This lets a
    # long chat continue without blindly sending every old token to the model.
    # Up to one summary batch may remain as raw turns until it is worth compressing.
    bounded_history = []
    remaining_history_chars = state["limits"]["context_chars"]
    for row in reversed(history_rows):
        if not isinstance(row, dict) or row.get("role") not in {"user", "assistant"}:
            continue
        content = str(row.get("content") or "").strip()
        if not content or remaining_history_chars <= 0:
            continue
        keep = min(len(content), remaining_history_chars)
        if len(content) > keep:
            marker = "\n[中间内容因上下文预算省略]\n"
            if keep <= len(marker):
                bounded_content = content[-keep:]
            else:
                retained = keep - len(marker)
                head = retained // 2
                tail = retained - head
                bounded_content = content[:head] + marker + content[-tail:]
        else:
            bounded_content = content
        bounded_history.append((row, bounded_content))
        remaining_history_chars -= keep
    bounded_history.reverse()
    current_images = {os.path.abspath(path) for path in image_paths}
    selected_history_images = set()
    needs_previous_images = bool(
        image_paths or current_plan or re.search(
            r"上一|之前|刚才|这张|那张|继续|previous|last image",
            prompt, re.IGNORECASE))
    remaining_context_images = (max(0, min(
        MAX_CONTEXT_REFERENCE_IMAGES, MAX_REFERENCE_IMAGES - len(current_images)))
        if needs_previous_images else 0)
    if remaining_context_images:
        for row, _content in reversed(bounded_history):
            if row.get("role") != "user":
                continue
            for candidate in reversed(row.get("image_paths") or []):
                try:
                    candidate = os.path.abspath(os.fspath(candidate))
                except (TypeError, ValueError):
                    continue
                if (candidate in current_images or candidate in selected_history_images
                        or not os.path.isfile(candidate)):
                    continue
                selected_history_images.add(candidate)
                remaining_context_images -= 1
                if remaining_context_images <= 0:
                    break
            if remaining_context_images <= 0:
                break

    state["selected_reference_images"] = list(dict.fromkeys(
        [os.path.basename(path) for path in image_paths] +
        [os.path.basename(path) for path in selected_history_images]))
    state["active_reference_images"] = (
        [os.path.basename(path) for path in image_paths]
        if image_paths else [os.path.basename(path) for path in selected_history_images])
    active_paths = image_paths if image_paths else list(selected_history_images)
    state["reference_image_paths"] = {
        os.path.basename(path): path for path in active_paths}

    for row, content in bounded_history:
        if content:
            paths = []
            for candidate in row.get("image_paths") or []:
                try:
                    path = os.path.abspath(os.fspath(candidate))
                except (TypeError, ValueError):
                    continue
                if path in selected_history_images and os.path.isfile(path):
                    paths.append(path)
            message_content = llm.user_content(content, paths) if paths else content
            message = {"role": row["role"], "content": message_content}
            transcript.append(message)
            if row["role"] == "user":
                source_user_messages.append(message)
    if isinstance(current_plan, dict):
        transcript.append({
            "role": "assistant",
            "content": (
                "当前本地模型对应的 CADPlan：\n" + _json_text(current_plan)
                if not isinstance(current_document, dict) else
                "从当前实际 Document 特征树导入的权威 CADPlan：\n" +
                _json_text(current_plan)
            ),
        })
    if isinstance(current_document, dict):
        transcript.append({
            "role": "assistant",
            "content": "当前实际模型的特征树快照（几何与启用状态的权威基线；"
                       "若与上面的旧 CADPlan 不同，以此特征树为准）：\n" +
                       _json_text(current_document),
        })
    image_names = [os.path.basename(path) for path in image_paths]
    input_text = prompt
    if image_names:
        input_text = (prompt + "\n\n" if prompt else "") + "本轮参考图片：" + ", ".join(image_names)
    if state["raster_analysis"].get("available") and state["raster_analysis"].get("images"):
        input_text += ("\n\n本地扫描线像素证据（候选视图比例，不代表已识别视角；"
                       "只在对应图片确为正交正视图时采用）：\n" +
                       _json_text(state["raster_analysis"]))
    current_user_message = {
        "role": "user", "content": llm.user_content(input_text, image_paths)}
    transcript.append(current_user_message)
    source_user_messages.append(current_user_message)
    # A visual review needs the active task's user evidence, not a gallery of
    # unrelated old prompts or assistant-generated model previews.
    last_image_message = max((index for index, message in enumerate(source_user_messages)
                              if _image_parts_in_messages([message])), default=-1)
    if last_image_message >= 0:
        source_user_messages = source_user_messages[max(0, last_image_message - 8):]
    else:
        source_user_messages = source_user_messages[-16:]
    state["selected_reference_images"] = list(dict.fromkeys(
        [os.path.basename(path) for path in image_paths] +
        [os.path.basename(path) for path in selected_history_images]))

    # One persistent model-directed tool loop owns this turn. The model chooses
    # whether to discuss, create/edit a CADPlan, preview it, inspect evidence,
    # revise, ask a genuinely blocking question, or finish.
    from ai_modeler.agent.agent_runtime import run_dynamic_loop
    return run_dynamic_loop(
        prompt, image_paths, model_cfg, transcript, source_user_messages,
        state, current_plan=current_plan, cancel_event=cancel_event)


def _save_local_candidate(session_dir, plan, preview):
    if (not isinstance(plan, dict) or not preview
            or cadplan.validate_plan(plan).get("state") != "valid"):
        return {}, None
    try:
        from ai_modeler.core.export import exporters
        document = cadplan.plan_to_document(plan)
        model_path = session_dir / "candidate.aimodel"
        mesh_path = session_dir / "candidate.stl"
        exporters.export_project(document, str(model_path))
        exporters.export_stl(document.build(), str(mesh_path), document.name)
        return {
            "aimodel": str(model_path), "stl": str(mesh_path),
            "preview": preview.get("preview_image"),
        }, None
    except Exception as exc:
        return {}, "本地工程或 STL 保存失败：%s" % exc


def run_agent_loop(prompt: str, image_paths=None, cfg=None,
                   conversation_history=None, progress: Optional[Callable[[str], None]] = None,
                   cancel_event=None, output_dir=None, current_plan=None,
                   current_document=None,
                   conversation_summary=None, summary_through=0,
                   on_preview=None) -> Dict[str, Any]:
    """Run the bounded multimodal AgentLoop and persist a local, redacted run record."""
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    image_paths = list(image_paths or [])
    session_dir = _make_session_dir(output_dir)
    state = {
        "session_dir": session_dir,
        "run_started_at": started_at,
        "run_prompt": str(prompt or ""),
        "run_image_paths": image_paths,
        "cfg": cfg if isinstance(cfg, dict) else {},
        "run_status": "running",
    }
    try:
        result = _run_agent_loop_impl(
            prompt, image_paths=image_paths, cfg=cfg,
            conversation_history=conversation_history, progress=progress,
            conversation_summary=conversation_summary,
            summary_through=summary_through,
            cancel_event=cancel_event, current_plan=current_plan,
            current_document=current_document,
            on_preview=on_preview,
            _session_dir=session_dir, _state_out=state)
    except KeyboardInterrupt:
        state["run_status"] = "interrupted"
        active_request = state.get("active_request")
        if isinstance(active_request, dict):
            active_request["status"] = "interrupted"
            active_request["interrupted_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds")
        paths, save_warning = _save_local_candidate(
            session_dir, state.get("current_plan"), state.get("latest_preview"))
        state["checkpoint_output_paths"] = paths
        state["checkpoint_output_warning"] = save_warning
        _checkpoint_run_record(
            state, error="KeyboardInterrupt: run stopped by operator")
        raise
    except Exception as exc:
        state["run_status"] = "failed"
        paths, save_warning = _save_local_candidate(
            session_dir, state.get("current_plan"), state.get("latest_preview"))
        state["checkpoint_output_paths"] = paths
        state["checkpoint_output_warning"] = save_warning
        timed_out = isinstance(exc, AgentLoopError) and "超时" in str(exc)
        if (timed_out and isinstance(state.get("current_plan"), dict)
                and state.get("latest_preview")):
            from ai_modeler.agent.agent_runtime import _finish_gate, _result_from_state
            gate = _finish_gate(state)
            status = {
                "state": "reviewed" if gate.get("ready") and state.get("has_reference_images")
                else "previewed",
                "validation": state.get("plan_validation"),
                "review": (state.get("latest_geometry_review") or {}).get("result"),
                "similarity": (state.get("latest_similarity_review") or {}).get("result"),
            }
            remaining = gate.get("blockers") or []
            reply = ("达到本轮时间上限；已保留当前可编辑候选。"
                     "模型尚未调用 cad_finish，不将这次运行标记为主动完成。")
            if remaining:
                reply += "\n未解决项：\n- " + "\n- ".join(str(item) for item in remaining[:8])
            result = _result_from_state(status, state, reply, remaining)
            result["interrupted_reason"] = "timeout"
            result["output_paths"] = paths
            if save_warning:
                result["output_warning"] = save_warning
        else:
            try:
                record_path = _write_run_record(
                    session_dir, prompt, image_paths, state,
                    result={"plan": state.get("current_plan"),
                            "preview": state.get("latest_preview"),
                            "output_paths": paths, "output_warning": save_warning},
                    error=exc, cfg=cfg, started_at=started_at)
                try:
                    exc.run_record = str(record_path)
                except Exception:
                    pass
            except Exception:
                pass
            raise

    result.setdefault("tool_calls", state.get("tool_calls", 0))
    result.setdefault("web_search_calls", state.get("web_search_calls", 0))
    result.setdefault("preview_calls", state.get("preview_calls", 0))
    result.setdefault("repair_cycles", state.get("repair_cycles", 0))
    result.setdefault("automatic_repairs", list(state.get("automatic_repairs", [])))
    result.setdefault("open_multiview_findings", _public_findings(
        state.get("open_multiview_findings", [])))
    result.setdefault("review_history", list(state.get("review_history", [])))
    result.setdefault("routing", state.get("routing"))
    result.setdefault("conversation_summary", state.get("conversation_summary", ""))
    result.setdefault("summary_through", state.get("conversation_summary_through", 0))
    result.setdefault("model_call_count", len(state.get("model_calls", [])))
    result.setdefault("model_calls", list(state.get("model_calls", [])))
    result.setdefault("image_parts_in_request", sum(
        int(call.get("image_parts_in_request", 0) or 0)
        for call in state.get("model_calls", [])))
    result.setdefault("llm_usage", {
        key: sum(float((call.get("usage") or {}).get(key, 0) or 0)
                 for call in state.get("model_calls", []))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                    "input_tokens", "output_tokens", "reasoning_tokens")
        if any(key in (call.get("usage") or {})
               for call in state.get("model_calls", []))
    })

    if not result.get("output_paths"):
        paths, save_warning = _save_local_candidate(
            session_dir, result.get("plan"), result.get("preview"))
        if paths:
            result["output_paths"] = paths
        if save_warning:
            result["output_warning"] = save_warning

    try:
        record_path = _write_run_record(
            session_dir, prompt, image_paths, state, result=result, cfg=cfg,
            started_at=started_at)
        result["run_record"] = str(record_path)
    except Exception as exc:
        result["run_record_warning"] = "运行记录未能保存：%s" % exc
    return result
