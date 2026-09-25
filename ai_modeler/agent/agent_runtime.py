# -*- coding: utf-8 -*-
"""Model-directed AgentLoop runtime for AI Modeler.

The runtime owns one model/tool loop. CAD operations remain structured and are
executed by the local CADPlan/mesh services; the model chooses which operation
to run next and when to ask the user or finish.
"""
from __future__ import annotations

import json
import os
import ipaddress
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ai_modeler.agent import agentloop as cad
from ai_modeler.agent import llm
from ai_modeler.agent import plan_state


MAX_AGENT_TURNS = 512
MAX_CONSECUTIVE_NO_PROGRESS = 8
MAX_STAGNATION_WARNINGS = 3
MAX_TRANSIENT_RETRIES = 6
MAX_OUTPUT_TOKENS = 65536
DEFAULT_CONTEXT_CHARS = 1000000
COMPACT_AFTER_TOOL_GROUPS = 8
RETAINED_TOOL_GROUPS = 4

_VIEWS = ("front", "right", "top", "iso", "back", "left", "bottom")
_VIEW_SETS = ("three", "extended", "iso", *_VIEWS)


def _explicit_image_question(prompt: str) -> bool:
    """Recognize clear image Q&A requests that should not start CAD modeling."""
    text = str(prompt or "").strip().lower()
    if not text:
        return False
    patterns = (
        r"(?:请问)?这是什么(?:东西|零件|结构|模型)?[？?。!！]*$",
        r"(?:这张|这幅|这个|该)?(?:图片|图像|照片|图)\s*.{0,8}(?:是什么|显示什么|画的什么|写了什么)",
        r"(?:描述|解释|识别|概括|总结)(?:一下)?(?:这张|这幅|这个|该)?(?:图片|图像|照片|图)",
        r"(?:what is this|describe (?:the )?(?:image|picture|photo)|identify (?:the )?(?:image|picture|photo)|read the text)",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _explicit_modeling_request(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if re.match(r"^(?:如何|怎么|为什么|能否|怎样|what\b|how\b|why\b)", text):
        return False
    return bool(re.search(
        r"(?:创建|建立|生成|建模|绘制|画出|制作|复刻|还原|添加|增加|删除|移除|"
        r"修改|调整|缩小|放大|打孔|切除|拉伸|旋转|create|build|model|draw|"
        r"add|remove|resize|modify)", text, re.IGNORECASE))


def _requires_image_bootstrap(prompt: str, image_paths: list,
                              current_plan: Optional[dict]) -> bool:
    """Require an initial CAD candidate for a new image modeling turn.

    An attached image is the modeling request by default. Clear image Q&A stays
    conversational, while a supplied plan means the user is continuing an
    existing model and the normal model-directed routing remains in control.
    """
    return bool(image_paths and not isinstance(current_plan, dict)
                and not _explicit_image_question(prompt))


def _required_tool_only(tools: list, name: str, required_fields=()) -> list:
    """Return one copied function schema with any call-specific required keys."""
    for tool in tools or []:
        if (not isinstance(tool, dict) or tool.get("type") != "function"
                or (tool.get("function") or {}).get("name") != name):
            continue
        function = dict(tool["function"])
        parameters = dict(function.get("parameters") or {})
        required = list(parameters.get("required") or [])
        for field in required_fields:
            if field not in required:
                required.append(field)
        parameters["required"] = required
        function["parameters"] = parameters
        return [dict(tool, function=function)]
    raise cad.AgentLoopError("AgentLoop 缺少必需工具：%s" % name)


def _tool(name: str, description: str, properties: dict, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(required or []),
            },
        },
    }


AGENT_TOOLS = [
    _tool(
        "web_search",
        "按需搜索外部标准、材料或事实。返回带 result_id、标题、摘要和链接的结果；用户图像和明确尺寸优先于网页内容。",
        {"query": {"type": "string", "minLength": 2, "maxLength": 240}},
        ["query"],
    ),
    _tool(
        "web_open_result",
        "按 web_search 返回的 result_id 读取该结果页正文，核实标准或参数；不能输入任意 URL。",
        {"result_id": {"type": "string"}}, ["result_id"],
    ),
    _tool(
        "cad_create_model",
        "建立本地可编辑模型，包含第一个主实体特征。程序统一使用 mm/deg、校验并立即预览；parameters 放纯数值，sources 在特征对象中单独放来源。"
        "有参考图时在本次调用附上简短 reference_reading.feature_groups：按可见区域列出主体和重复特征的 kind、quantity、region、arrangement、evidence。不要推断图外的连接关系；颜色不能证明多个面上的开口属于同一特征。将关键不确定性留给后续单视图复核。",
        {"name": {"type": "string"}, "first_feature": {"type": "object"},
         "reference_reading": {"type": "object"}},
        ["name", "first_feature"],
    ),
    _tool(
        "cad_add_feature",
        "只追加一个可编辑特征。程序合成并校验整份 CADPlan，成功后立即更新右侧预览。",
        {"feature": {"type": "object"}}, ["feature"],
    ),
    _tool(
        "cad_update_feature",
        "按稳定 id 修改一个特征；仅发送变化的参数和来源，其他特征与参数保持原样。",
        {"feature_id": {"type": "string"},
         "parameter_updates": {"type": "object"},
         "source_updates": {"type": "object"},
         "remove_parameters": {"type": "array", "items": {"type": "string"}},
         "fields": {"type": "object"}}, ["feature_id"],
    ),
    _tool(
        "cad_remove_feature",
        "按稳定 id 删除一个特征；若其他特征依赖它，校验会拒绝并保持原模型。",
        {"feature_id": {"type": "string"}}, ["feature_id"],
    ),
    _tool(
        "cad_get_state",
        "读取当前权威特征树、参数来源、尺寸、未解决偏差和版本号。",
        {"include_features": {"type": "boolean"}}, [],
    ),
    _tool(
        "cad_get_feature_spec",
        "按特征 kind 查询完整参数、必需字段及当前 CATIA 映射状态；新增不熟悉的特征前使用。",
        {"kind": {"type": "string"}}, ["kind"],
    ),
    _tool(
        "cad_validate_plan",
        "按需独立校验当前或提供的 CADPlan，不修改候选。增量编辑工具已自动校验，避免重复调用。",
        {"plan": {"type": "object"}}, [],
    ),
    _tool(
        "cad_preview_plan",
        "按需查看当前模型的指定视图；增量建模工具已经自动更新右侧预览。每次覆盖当前候选预览文件，不会调用 CATIA。",
        {"view_set": {"type": "string", "enum": list(_VIEW_SETS)}}, [],
    ),
    _tool(
        "cad_review_model",
        "对当前候选做真实多模态图像复核：每个请求单独包含用户原图和一张生成的模型单视图。geometry 检查形状/特征证据；similarity 检查参考图整体对应关系。复核图会在单次检查后清理。",
        {
            "review_type": {"type": "string", "enum": ["geometry", "similarity"]},
            "views": {"type": "array", "items": {"type": "string", "enum": list(_VIEWS)},
                      "uniqueItems": True, "maxItems": len(_VIEWS)},
        }, ["review_type"],
    ),
    _tool(
        "ask_user",
        "只有主要结构存在无法从图像或已有信息合理判断的歧义时才提问。图像没有尺寸标注不构成阻塞：应先按比例估值、标注 image_estimate、生成并复核候选，再问真正未解决的问题。",
        {"question": {"type": "string", "minLength": 2, "maxLength": 2000}},
        ["question"],
    ),
    _tool(
        "cad_finish",
        "请求结束本轮。程序会核验当前计划、预览和所需视觉检查是否对应同一版几何；条件不满足时会返回缺项并继续循环。不得用自评分数替代几何证据。",
        {"summary": {"type": "string", "maxLength": 2000}}, [],
    ),
]


MAX_IDENTICAL_FINISHES = 3


def _reference_reading_blockers(reading, plan=None, final=False) -> list:
    """Validate a concise, shape-independent inventory of visible feature groups."""
    if not isinstance(reading, dict):
        return ["缺少 reference_reading：先列可见特征组、数量和区域证据。"]
    blockers = []
    groups = reading.get("feature_groups")
    if not isinstance(groups, list) or not groups:
        blockers.append("reference_reading.feature_groups 必须列出可见主体和重复特征组。")
        groups = []
    group_ids = set()
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict):
            blockers.append("feature_groups[%d] 必须是对象。" % (index - 1))
            continue
        group_id = str(group.get("id") or "").strip()
        kind = str(group.get("kind") or "").strip().lower()
        region = str(group.get("region") or "").strip()
        evidence = str(group.get("evidence") or "").strip()
        quantity = group.get("quantity")
        if not group_id or group_id in group_ids:
            blockers.append("feature_groups[%d].id 必须非空且唯一。" % (index - 1))
        group_ids.add(group_id)
        if not kind:
            blockers.append("feature_groups[%d].kind 不能为空。" % (index - 1))
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            blockers.append("feature_groups[%d].quantity 必须是正整数。" % (index - 1))
        if not region:
            blockers.append("feature_groups[%d].region 不能为空。" % (index - 1))
        if not evidence:
            blockers.append("feature_groups[%d].evidence 不能为空。" % (index - 1))
    if final and isinstance(plan, dict):
        target_holes = _reference_hole_quantity(reading)
        if target_holes is not None:
            actual = _planned_hole_quantity(plan)
            if actual != target_holes:
                blockers.append("当前模型有 %d 个可识别的物理孔位，特征组要求 %d 个；"
                                "继续补齐或修正孔组后再复核。"
                                % (actual, target_holes))
    return blockers


def _reference_hole_quantity(reading) -> Optional[int]:
    """Derive optional hole coverage from generic feature-group quantities."""
    if not isinstance(reading, dict):
        return None
    groups = reading.get("feature_groups")
    if not isinstance(groups, list):
        return None
    hole_groups = [row for row in groups if isinstance(row, dict)
                   and ("hole" in str(row.get("kind") or "").lower()
                        or "孔" in str(row.get("kind") or ""))]
    if not hole_groups:
        return None
    return sum(row.get("quantity", 0) for row in hole_groups
               if isinstance(row.get("quantity"), int)
               and not isinstance(row.get("quantity"), bool))


def _planned_hole_quantity(plan) -> int:
    """Count unique modeled hole positions, including common repeated layouts."""
    features = [row for row in (plan or {}).get("features", [])
                if isinstance(row, dict)]
    holes = {}
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
    # A pattern repeats all positions of its source feature. The source hole is
    # already present, so count the pattern's total instances once instead of
    # counting its source plus a second copy of the whole pattern.
    for feature in features:
        if feature.get("kind") != "pattern" or feature.get("operation") != "cut":
            continue
        relations = feature.get("relations") or []
        source = next((row.get("feature_id") for row in relations
                       if isinstance(row, dict) and row.get("type") == "pattern_source"), None)
        if source in holes:
            holes[source] *= max(1, int((feature.get("parameters") or {}).get("count", 1)))
    return sum(holes.values())


def _reference_feature_coverage(plan, reading) -> dict:
    """Return the target feature checklist and measurable hole coverage if present."""
    if not isinstance(reading, dict):
        return {}
    groups = [row for row in reading.get("feature_groups", [])
              if isinstance(row, dict)]
    target_holes = _reference_hole_quantity(reading)
    modeled_holes = _planned_hole_quantity(plan) if isinstance(plan, dict) else 0
    return {
        "required_feature_groups": [
            {key: row.get(key) for key in
             ("id", "kind", "quantity", "region", "arrangement") if key in row}
            for row in groups
        ],
        "hole_positions": ({
            "target": target_holes,
            "modeled": modeled_holes,
            "missing": max(0, int(target_holes) - modeled_holes),
            "excess": max(0, modeled_holes - int(target_holes)),
        } if isinstance(target_holes, int) and not isinstance(target_holes, bool) else None),
    }


def _agent_system(has_reference_images: bool = False) -> str:
    text = cad._prompt("runtime")
    if has_reference_images:
        text += "\n\n" + cad._prompt("reference_read")
    index = {
        kind: {"required": spec["required"], "params": spec["params"]}
        for kind, spec in cad.cadplan.FEATURE_SPECS.items()
    }
    return (text + "\n\n## CADPlan v%s 特征索引\n"
            "特征对象使用 id、kind、operation(add/cut)、parameters、sources、"
            "可选 relations/name/note。数值参数 sources 的 type 为 user、"
            "image_estimate、derived 或 existing_model；来源对象只允许 type、evidence、note，"
            "不要放 value、confidence 或其它字段；image_estimate 必须带具体 evidence。"
            "坐标为右手全局 XYZ，长度默认 mm，角度默认 deg。"
            "例如 box：{\"id\":\"base\",\"kind\":\"box\",\"operation\":\"add\","
            "\"parameters\":{\"length\":40,\"width\":20,\"height\":10},"
            "\"sources\":{\"length\":{\"type\":\"user\"},\"width\":{\"type\":\"user\"},"
            "\"height\":{\"type\":\"user\"}}。不要把数值包进对象或放在 sources.value。"
            "常用 box 参数为 length/width/height/center，单孔为 diameter/through/pos/point/axis；"
            "这些基础参数无需查询 cad_get_feature_spec，只有不熟悉的条件参数或映射时才查询。"
            "box 的 center 是三维位置；corner 是非负倒角标量，不能当位置向量。"
            "完整合法性由每次增量工具中的 SDK 校验，不要重传整个计划。"
            "不确定条件必需参数或 CATIA 支持状态时调用 cad_get_feature_spec。\n%s\n\n"
            "## 几何语义速查（猜错会得到错误的体积，从而误判自己这一步的对错）\n"
            "- box：center 是**几何中心**，长宽高以它为中心向两侧各一半。\n"
            "- cylinder / cone：base=true 时坐落在 z=0 平面上（center 的 z 不起作用）；"
            "base=false 时以 center 为中心，上下各一半。\n"
            "- prism：profile 是 XY 平面上的闭合多边形，**center 的 z 是拉伸的底面**，"
            "向上拉伸 height —— 不是居中。要切 3mm 深的槽且顶面在 z=36，就取 center z=33。\n"
            "- hole（pos=point）：point 是孔轴线上的定位点，through=true 时沿 axis 贯穿整个零件；"
            "diameter 必须大于它要削掉的部位，否则什么都不切。\n"
            "- pocket：沿 Z 向下切，depth 是深度，只能切 Z 向。\n"
            "- pattern：relations 必须用 pattern_source 指向**紧邻的前一个**特征；"
            "step 是三维数组如 [0,0,1]。\n"
            "- 每次提交后工具返回 feature_effect.volume_delta_mm3 —— **用它判断这一步切掉/增加了多少**，"
            "不要靠尺寸推算；返回 warning 时先按 warning 处理，不要重复提交同一个特征。\n%s" %
            (cad.cadplan.CADPLAN_SCHEMA_VERSION,
             json.dumps(index, ensure_ascii=False, separators=(",", ":")),
             ""))


# One attached image costs real vision tokens on the provider side even though
# its base64 body must not be charged as plain text. Charging a nominal 256
# chars made images invisible to the context budget, so a long image-driven
# loop never compacted while the real request kept growing.
IMAGE_CHAR_COST = 4000


def _text_size(value) -> int:
    """Estimate context size without counting base64 image bodies as text."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_size(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image"}:
            return IMAGE_CHAR_COST
        return sum(_text_size(key) + _text_size(item)
                   for key, item in value.items())
    return 0


def _messages_size(messages) -> int:
    return sum(_text_size(message) for message in messages or [])


def _rows(value):
    return value if isinstance(value, list) else []


def _score(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _state_progress_key(state: dict):
    validation = state.get("plan_validation") or {}
    pending = state.get("open_multiview_findings") or []
    def review_key(record):
        if not isinstance(record, dict):
            return None
        result = record.get("result") or {}
        reports = []
        for report in _rows(result.get("single_view_reports")):
            if not isinstance(report, dict):
                continue
            checks = tuple(sorted((str(row.get("area")), str(row.get("status")))
                                  for row in _rows(report.get("checks"))
                                  if isinstance(row, dict)))
            reports.append((report.get("view"), checks,
                            tuple(sorted(str(row.get("issue_id") or row.get("item"))
                                         for row in _rows(report.get("issues"))
                                         if isinstance(row, dict))),
                            _score(report.get("confidence"))))
        return (result.get("decision"), tuple(reports),
                tuple(sorted(str(row.get("issue_id") or row.get("item"))
                             for row in _rows(result.get("issues"))
                             if isinstance(row, dict))),
                tuple(sorted(str(row.get("issue_id") or row.get("id"))
                             for row in _rows(result.get("resolved_findings"))
                             if isinstance(row, dict))),
                _score(result.get("similarity_score")))

    finish_gate = state.get("finish_gate") or {}
    return (
        state.get("plan_geometry_fingerprint"),
        state.get("preview_geometry_fingerprint"),
        validation.get("state"),
        tuple((row.get("code"), row.get("path"), row.get("message"))
              for row in _rows(validation.get("errors")) if isinstance(row, dict)),
        tuple((str(row.get("issue_id")), str(row.get("evidence")))
              for row in pending if isinstance(row, dict)),
        review_key(state.get("latest_geometry_review")),
        review_key(state.get("latest_similarity_review")),
        (finish_gate.get("state"), tuple(finish_gate.get("blockers") or [])),
        state.get("web_search_calls", 0),
    )


def _runtime_snapshot(state: dict) -> str:
    current_plan = state.get("current_plan")
    validation = state.get("plan_validation")
    preview = state.get("latest_preview") or {}
    latest_reviews = [row for row in state.get("review_history", [])
                      if row.get("geometry_fingerprint") == state.get("plan_geometry_fingerprint")]
    payload = {
        "task": state.get("user_prompt", ""),
        "conversation_summary": state.get("conversation_summary", ""),
        "reference_reading": state.get("reference_reading"),
        "current_plan": current_plan,
        "validation": validation,
        "preview": {
            "state": preview.get("state"),
            "dimensions": preview.get("dimensions"),
            "views": preview.get("preview_image_views"),
            "geometry_fingerprint": state.get("preview_geometry_fingerprint"),
        },
        "reviewed_view_status": state.get("reviewed_view_status", {}),
        "latest_reviews": latest_reviews[-6:],
        "unresolved_findings": cad._public_findings(
            state.get("open_multiview_findings", [])),
        "repair_cycles": state.get("repair_cycles", 0),
        "stagnant_tool_calls": state.get("stagnant_tool_calls", 0),
        "stagnation_warnings": state.get("stagnation_warnings", 0),
        "tool_history": [
            {"tool": row.get("tool"), "result": row.get("result_summary")}
            for row in state.get("tool_trace", [])[-12:]
        ],
    }
    return "AgentLoop 上下文压缩后的权威状态摘要（计划与当前几何指纹优先）：\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"))


def _group_tool_messages(messages: List[dict]) -> List[List[dict]]:
    groups = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if (isinstance(message, dict) and message.get("role") == "assistant"
                and message.get("tool_calls")):
            group = [message]
            expected = {str(call.get("id")) for call in message.get("tool_calls", [])
                        if isinstance(call, dict) and call.get("id")}
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool":
                group.append(messages[index])
                expected.discard(str(messages[index].get("tool_call_id")))
                index += 1
                if not expected:
                    break
            groups.append(group)
            continue
        groups.append([message])
        index += 1
    return groups


def _compact_messages(messages: List[dict], state: dict, force=False) -> bool:
    """Drop repeated old tool transcripts while preserving the full user context."""
    prefix = state.get("runtime_prefix_messages")
    if not isinstance(prefix, list):
        prefix = list(messages[:int(state.get("runtime_base_count", 0))])
    prefix_count = len(prefix)
    already_compacted = bool(state.get("runtime_compacted"))
    tail_start = prefix_count + (1 if already_compacted else 0)
    budget = int(state.get("context_char_limit", DEFAULT_CONTEXT_CHARS))
    if not prefix or prefix_count < 1:
        return False
    groups = _group_tool_messages(messages[tail_start:])
    if (not force and _messages_size(messages) <= budget
            and len(groups) <= COMPACT_AFTER_TOOL_GROUPS):
        return False
    if not groups:
        return False
    # Input history and the current user request remain intact. The runtime
    # checkpoint carries the authoritative plan/review state; the recent paired
    # tool calls retain conversational continuity without replaying every old
    # full CADPlan and reasoning item on each stateless Responses request.
    tail = [item for group in groups[-RETAINED_TOOL_GROUPS:] for item in group]
    compacted = list(prefix)
    compacted.append({"role": "user", "content": _runtime_snapshot(state)})
    compacted.extend(tail)
    messages[:] = compacted
    state["runtime_compacted"] = True
    state["runtime_base_count"] = prefix_count + 1
    state["context_compactions"] = state.get("context_compactions", 0) + 1
    cad._emit(state.get("progress"),
              "已压缩较早的工具往返，保留原始请求、当前 CADPlan、复核证据和最近操作…")
    return True


def _is_context_error(error: Exception) -> bool:
    text = str(error).lower()
    markers = ("context length", "context_length", "prompt too long",
               "prompt_too_long", "maximum context", "too many tokens",
               "input is too long", "413")
    return any(marker in text for marker in markers)


def _is_transient_error(error: Exception) -> bool:
    text = str(error).lower()
    return bool(re.search(r"\b429\b|\b5\d\d\b|rate.?limit|overloaded|temporar",
                          text) or any(marker in text for marker in (
                              "connection reset", "connection failed", "timed out",
                              "temporarily unavailable", "network is unreachable")))


def _is_tool_choice_unsupported(error: Exception) -> bool:
    text = str(error).lower()
    mentions_choice = "tool_choice" in text or "tool choice" in text
    return mentions_choice and any(marker in text for marker in (
        "not support", "unsupported", "invalid", "unknown", "unrecognized",
        "doesn't support", "does not support", "不支持", "无效"))


def _call_model(messages, state, tools, output_tokens, tool_choice=None):
    deadline = state["deadline"]
    compact_retry_used = False
    transient_attempt = 0
    tool_choice_fallback_used = False
    while True:
        cad._check_cancel(state.get("cancel_event"), deadline)
        _compact_messages(messages, state)
        request_phase = str(state.get("request_phase") or "AgentLoop")
        model = str((state.get("model_cfg") or {}).get("model") or "默认模型")
        model_cfg = dict(state.get("model_cfg") or {})
        if str(model_cfg.get("reasoning_effort") or "").lower() == "auto":
            model_cfg["reasoning_effort"] = (
                "high" if state.get("has_reference_images")
                and not state.get("current_plan") else "low")
        effort = model_cfg.get("reasoning_effort")
        thinking = model_cfg.get("thinking")
        if not effort and isinstance(thinking, dict):
            effort = thinking.get("type")
        effort = {"auto": "high", "medium": "high", "xhigh": "high",
                  "ultra": "max"}.get(str(effort or "").lower(), effort)
        if effort == "none" or effort == "disabled":
            effort = "关闭"
        if (not effort and model in {"deepseek-flash", "deepseek-v4-pro"}
                and "api.deepseek.com" in str(model_cfg.get("base_url") or "").lower()):
            effort = "high（DeepSeek Responses 默认）"
        detail = "%s · %s" % (request_phase, model)
        if effort:
            detail += " · 推理强度 %s" % effort
        cad._emit(state.get("progress"), "模型等待中：" + detail)
        try:
            reply = llm.chat_completion(
                messages, model_cfg, tools=tools, tool_choice=tool_choice,
                timeout=cad._remaining_timeout(deadline), max_tokens=output_tokens,
                cancel_event=state.get("cancel_event"),
                progress=lambda event: llm.emit_stream_progress(
                    event,
                    lambda message: cad._emit(state.get("progress"), message),
                    detail=detail))
        except llm.ModelRequestCancelled:
            raise                     # 用户主动停止：绝不重试/降级
        except Exception as exc:
            cad._record_model_call(state, "agent_loop", messages, output_tokens,
                                   error=exc)
            if (tool_choice is not None and not tool_choice_fallback_used
                    and _is_tool_choice_unsupported(exc)):
                tool_choice_fallback_used = True
                tool_choice = None
                cad._emit(
                    state.get("progress"),
                    "模型接口不支持指定工具名；改用仅开放 cad_create_model 的自动选择重试。")
                continue
            if _is_context_error(exc) and not compact_retry_used:
                compact_retry_used = _compact_messages(messages, state, force=True)
                if compact_retry_used:
                    continue
            if _is_transient_error(exc) and transient_attempt < MAX_TRANSIENT_RETRIES:
                delay = min(30.0, 0.75 * (2 ** transient_attempt))
                delay *= 0.8 + random.random() * 0.4
                remaining = deadline - time.monotonic()
                if remaining <= delay + 1:
                    raise
                transient_attempt += 1
                cad._emit(state.get("progress"),
                          "模型接口临时错误；%d/%d 次重试，等待 %.1f 秒…" % (
                              transient_attempt, MAX_TRANSIENT_RETRIES, delay))
                event = state.get("cancel_event")
                if event is not None:
                    if event.wait(delay):
                        raise cad.AgentLoopError("已取消本次 AgentLoop。") from exc
                else:
                    time.sleep(delay)
                continue
            raise
        cad._record_model_call(state, "agent_loop", messages, output_tokens,
                               reply=reply)
        cad._emit(state.get("progress"), "模型响应已返回：" + detail)
        return reply


def _continue_truncated_response(messages: List[dict], reply: dict) -> None:
    """Resume an incomplete Responses turn instead of replaying it from zero.

    DeepSeek's Responses API is stateless, so the assistant reasoning/output
    item must be sent back in the next request. Reissuing the same transcript
    with a larger output limit makes a reasoning model repeat its work and can
    burn the whole AgentLoop timeout without advancing the task.
    """
    assistant = {"role": "assistant", "content": reply.get("content") or ""}
    reasoning = reply.get("reasoning_content")
    if reasoning:
        assistant["reasoning_content"] = reasoning
    if assistant.get("content") or reasoning:
        messages.append(assistant)
    messages.append({
        "role": "user",
        "content": (
            "上一条模型响应达到单次输出边界，已保留它返回的推理和文本上下文。"
            "请从当前上下文继续，不要从头重复分析；优先完成尚未执行的 CAD 工具调用，"
            "或直接给出简洁、完整的当前答复。"),
    })


def _validation_result(plan) -> dict:
    try:
        return cad.cadplan.validate_plan(plan)
    except Exception as exc:
        return {"state": "invalid", "errors": [{"message": str(exc)}]}


_PIXEL_EVIDENCE_RE = re.compile(r"scanline|scan-line|扫描线", re.IGNORECASE)


def _unsupported_pixel_evidence(plan: dict, state: dict) -> dict:
    """Refuse plan sizes that cite pixel ratios this turn never measured.

    The scanline tool publishes ratios only for a flat orthographic view; when it
    refuses (see image_analysis.MIN_SCANLINE_SPAN_RATIO) a plan that still cites
    "scanline" ratios is claiming a measurement that does not exist. That is how
    an isometric screenshot produced a 14.5/71/14.5 mm part.
    """
    images = [row for row in ((state.get("raster_analysis") or {}).get("images") or [])
              if isinstance(row, dict)]
    if not images:
        return {}
    if any((row.get("three_region_scanline_candidate") or {}).get("usable_for_dimensions")
           for row in images):
        return {}
    offending = []
    for feature in plan.get("features") or []:
        if not isinstance(feature, dict):
            continue
        for key, source in (feature.get("sources") or {}).items():
            if not isinstance(source, dict) or source.get("type") != "image_estimate":
                continue
            evidence = str(source.get("evidence") or "")
            if _PIXEL_EVIDENCE_RE.search(evidence):
                offending.append({"feature_id": feature.get("id"), "parameter": key,
                                  "evidence": evidence[:200]})
    if not offending:
        return {}
    reasons = [str(row.get("scanline_rejected", {}).get("reason") or "")
               for row in images]
    reasons = "；".join(reason for reason in reasons if reason) or "本轮没有可用的扫描线测量。"
    return {
        "offending": offending,
        "error": "这些参数的 evidence 引用了像素扫描线比例，但本轮参考图没有可用测量：" + reasons,
        "instruction": ("该图不是平色正视图，不能作为比例尺寸证据。请按整体轮廓重新做目视比例估算"
                        "并改写 evidence，或调用 ask_user 确认关键尺寸；"
                        "不要把扫描线比例留在 sources 里。"),
    }


def _set_plan(args: dict, state: dict) -> dict:
    plan = args.get("plan")
    if not isinstance(plan, dict):
        return {"state": "invalid", "errors": ["plan 必须是 CADPlan JSON 对象。"]}
    validation = _validation_result(plan)
    if validation.get("state") not in {"valid", "needs_confirmation"}:
        return {"state": "invalid", "validation": validation,
                "instruction": "请根据校验错误修正 CADPlan，再提交。"}
    unfounded = _unsupported_pixel_evidence(plan, state)
    if unfounded:
        return {"state": "invalid", "validation": validation,
                "error": unfounded["error"], "instruction": unfounded["instruction"],
                "offending_sources": unfounded["offending"]}
    if state.get("has_reference_images"):
        drift = _reference_reading_blockers(state.get("reference_reading"), plan)
        if drift:
            return {"state": "invalid", "validation": validation,
                    "error": "当前几何与已提交的读图结果矛盾。",
                    "reference_reading_blockers": drift,
                    "instruction": ("不要为了迁就几何擅自减少或合并 reference_reading.feature_groups。"
                                    "按清单补齐可见特征，并用可见面和孔口方向修正孔轴；"
                                    "若读图清单确实错了，再更正清单并给出新的图像证据。")}

    before_fp = state.get("plan_geometry_fingerprint")
    after_fp = cad._geometry_plan_fingerprint(plan)
    geometry_changed = bool(before_fp and before_fp != after_fp)
    if geometry_changed:
        prior = state.get("reviewed_view_status", {})
        if prior:
            state["repair_cycles"] = state.get("repair_cycles", 0) + 1
        state["reviewed_view_status"] = {}
        state["reference_images_reviewed"] = []
        # Keep unresolved findings until a later independent view review
        # confirms each one was repaired against changed geometry.
        state["latest_geometry_review"] = None
        state["latest_similarity_review"] = None
        state["latest_preview"] = None
        state["preview_geometry_fingerprint"] = None

    state["current_plan"] = plan
    state["plan_validation"] = validation
    state["plan_geometry_fingerprint"] = after_fp
    state["agent_started"] = True
    state["candidate_attempted"] = True
    state["plan_submissions"] = state.get("plan_submissions", 0) + 1
    return {
        "state": validation.get("state"),
        "geometry_fingerprint": after_fp,
        "feature_count": len(plan.get("features", [])),
        "change_summary": str(args.get("change_summary") or "")[:1600],
        "validation": validation,
        "geometry_changed": geometry_changed,
        "next_step_hint": (
            "计划有效。请立即调用 cad_preview_plan 显示当前建模进度；若还有特征未完成，依据预览每次只追加一个特征后重新提交完整计划。全部特征完成后再进行最终多视图复核。"
            if validation.get("state") == "valid" else
            "计划含待确认项。对图片缺少的绝对尺寸先用 image_estimate 建候选；只有拓扑歧义无法合理假设时才询问。"
        ),
    }


def _tool_progress_summary(name: str, args: dict) -> str:
    """Build a short, auditable action summary from the selected tool call."""
    args = args if isinstance(args, dict) else {}
    if name == "cad_create_model":
        return "建立模型 %s 与首个主实体。" % str(args.get("name") or "候选")[:100]
    if name == "cad_add_feature":
        feature = args.get("feature") or {}
        return "添加特征 %s。" % str(feature.get("id") or feature.get("name") or "未命名")[:100]
    if name == "cad_update_feature":
        return "修改特征 %s。" % str(args.get("feature_id") or "")[:100]
    if name == "cad_remove_feature":
        return "删除特征 %s。" % str(args.get("feature_id") or "")[:100]
    if name == "cad_get_state":
        return "读取当前特征树和尺寸。"
    if name == "cad_get_feature_spec":
        return "查询 %s 特征的参数规格。" % str(args.get("kind") or "")[:100]
    if name == "web_open_result":
        return "核实搜索结果 %s 的网页正文。" % str(args.get("result_id") or "")[:100]
    if name == "cad_set_plan":
        summary = str(args.get("change_summary") or "提交或修订 CADPlan 候选").strip()
        plan = args.get("plan")
        count = len(plan.get("features", [])) if isinstance(plan, dict) else 0
        if count:
            summary += "（%d 个特征）" % count
        return summary[:500]
    if name == "cad_validate_plan":
        return "校验 CADPlan 结构、参数和数值来源。"
    if name == "cad_preview_plan":
        return "生成 %s 视图预览，检查候选外形。" % (args.get("view_set") or "three")
    if name == "cad_review_model":
        review_type = "几何" if args.get("review_type") == "geometry" else "相似度"
        views = "、".join(str(view) for view in args.get("views", []) if view)
        return "进行%s复核%s。" % (review_type, "（%s 视图）" % views if views else "")
    if name == "ask_user":
        return "遇到图像证据无法区分的结构歧义，准备向用户确认：%s" % str(
            args.get("question") or "")[:360]
    if name == "cad_finish":
        return "检查计划、预览和必需复核是否满足结束条件。"
    if name == "web_search":
        return "查询用户要求核实的外部资料：%s" % str(args.get("query") or "")[:300]
    return "执行工具：%s" % (name or "unknown")


def _preview_plan(args: dict, state: dict) -> dict:
    plan = state.get("current_plan")
    if not isinstance(plan, dict):
        return {"state": "blocked", "error": "尚无已提交 CADPlan；请先调用 cad_set_plan。"}
    validation = _validation_result(plan)
    if validation.get("state") != "valid":
        return {"state": validation.get("state"), "validation": validation,
                "error": "计划尚未达到可预览状态。"}
    if state.get("tool_preview_calls", 0) >= cad.MAX_TOOL_PREVIEW_CALLS:
        return {"state": "blocked", "error": "达到预览工具的安全熔断上限。"}
    view_set = str(args.get("view_set") or "three")
    if view_set not in _VIEW_SETS:
        return {"state": "invalid", "error": "不支持该 view_set。"}
    state["tool_preview_calls"] = state.get("tool_preview_calls", 0) + 1
    state["preview_calls"] = state.get("preview_calls", 0) + 1
    image_path = state["session_dir"] / "candidate-preview.png"
    try:
        preview = cad.cadplan.preview_plan(plan, image_path=str(image_path),
                                           view_set=view_set)
    except Exception as exc:
        return {"state": "preview_failed", "error": str(exc)}
    if preview.get("state") != "previewed" or not os.path.isfile(str(image_path)):
        return {"state": "preview_failed",
                "error": preview.get("error") or preview.get("preview_image_error") or preview.get("state")}
    state["latest_preview"] = preview
    state["preview_geometry_fingerprint"] = state["plan_geometry_fingerprint"]
    state["previews"] = {
        cad._plan_fingerprint(plan, view_set): {"result": preview},
    }
    cad._publish_preview(plan, preview, state, "candidate")
    return {
        "state": "previewed",
        "geometry_fingerprint": state["plan_geometry_fingerprint"],
        "preview_image": str(image_path),
        "view_set": view_set,
        "views": preview.get("preview_image_views"),
        "dimensions": preview.get("dimensions"),
        "local_geometry_checks": preview.get("local_geometry_checks"),
        "warnings": preview.get("warnings", []),
        "catia_executed": False,
        "next_step_hint": "当前预览已展示，作为逐特征建模进度。若还有几何特征未完成，只追加下一个特征并刷新预览；全部特征完成后再做最终多视图复核。",
    }


COSMETIC_EPSILON = 0.01          # mm / deg: below this an edit is churn, not progress


def _plan_metadata(plan) -> str:
    """Plan identity with parameters removed (ids, kinds, sources, order)."""
    clone = json.loads(json.dumps(plan or {}))
    for feature in clone.get("features") or []:
        if isinstance(feature, dict):
            feature.pop("parameters", None)
    return json.dumps(clone, sort_keys=True, ensure_ascii=False)


def _max_parameter_delta(before, after) -> float:
    """Largest numeric parameter change; infinity when the change is structural."""
    def index(plan):
        return {str(feature.get("id")): (feature.get("parameters") or {})
                for feature in (plan or {}).get("features") or []
                if isinstance(feature, dict)}

    left, right = index(before), index(after)
    if set(left) != set(right):
        return float("inf")
    worst = 0.0
    for feature_id, parameters in left.items():
        peer = right[feature_id]
        if set(parameters) != set(peer):
            return float("inf")
        for key, value in parameters.items():
            other = peer[key]
            if isinstance(value, bool) or isinstance(other, bool):
                if value != other:
                    return float("inf")
            elif isinstance(value, (int, float)) and isinstance(other, (int, float)):
                worst = max(worst, abs(float(value) - float(other)))
            elif (isinstance(value, (list, tuple)) and isinstance(other, (list, tuple))
                  and len(value) == len(other)):
                for item, mate in zip(value, other):
                    if (isinstance(item, (int, float)) and isinstance(mate, (int, float))
                            and not isinstance(item, bool) and not isinstance(mate, bool)):
                        worst = max(worst, abs(float(item) - float(mate)))
                    elif item != mate:
                        return float("inf")
            elif value != other:
                return float("inf")
    return worst


def _apply_incremental(name: str, args: dict, state: dict) -> dict:
    """Validate the entire candidate before committing one feature change."""
    current = state.get("current_plan")
    # Snapshot before _set_plan runs: a geometry change clears latest_preview.
    previous_preview = state.get("latest_preview") or {}
    # Accept the manifest from either the create call or a JSON reading already
    # written by the model. It is checked once before the first body, avoiding a
    # build on an untracked visual guess while not requiring a separate tool turn.
    notes = []
    candidate_readings = [args.get("reference_reading")]
    if name == "cad_create_model" and state.get("has_reference_images"):
        candidate_readings.append(state.get("reference_reading"))
    for candidate_reading in candidate_readings:
        if not isinstance(candidate_reading, dict):
            continue
        blockers = _reference_reading_blockers(candidate_reading)
        if blockers:
            notes.extend(blockers)
            continue
        if candidate_reading != state.get("reference_reading"):
            state["reference_reading"] = candidate_reading
            state["reference_reading_revision"] = state.get(
                "reference_reading_revision", 1) + 1
    if name == "cad_create_model" and state.get("has_reference_images"):
        blockers = _reference_reading_blockers(state.get("reference_reading"))
        if blockers:
            return {"state": "needs_reference_reading",
                    "reference_reading_blockers": blockers,
                    "instruction": "先把一次读图清单放进 cad_create_model.reference_reading；"
                                   "按可见区域列 feature_groups，说明每组数量与证据。"
                    }
    try:
        if name == "cad_create_model":
            if isinstance(current, dict):
                return {"state": "blocked", "error": "已有模型；请使用增量编辑工具。"}
            candidate = plan_state.create(
                args.get("name"), args.get("first_feature"),
                source_prompt=state.get("user_prompt", ""))
        else:
            if not isinstance(current, dict):
                return {"state": "blocked", "error": "尚无模型；先调用 cad_create_model。"}
            if name == "cad_add_feature":
                candidate = plan_state.add(current, args.get("feature"))
            elif name == "cad_update_feature":
                candidate = plan_state.update(
                    current, args.get("feature_id"),
                    parameter_updates=args.get("parameter_updates"),
                    source_updates=args.get("source_updates"),
                    remove_parameters=args.get("remove_parameters"),
                    fields=args.get("fields"))
            else:
                candidate = plan_state.remove(current, args.get("feature_id"))
    except (TypeError, ValueError, KeyError) as exc:
        return {"state": "invalid", "error": str(exc)}
    validation = _validation_result(candidate)
    if validation.get("state") == "invalid":
        return {"state": "invalid", "validation": validation,
                "error": "本次特征修改未提交；原模型保持不变。"}
    before_fp = state.get("plan_geometry_fingerprint")
    after_fp = cad._geometry_plan_fingerprint(candidate)
    geometry_same = before_fp == after_fp
    if geometry_same and name != "cad_create_model":
        if _plan_metadata(candidate) == _plan_metadata(current):
            return {"state": "unchanged", "validation": validation,
                    "revision": state.get("plan_revision", 0),
                    "error": "参数与来源都未改变；无需重新预览。"}
        # A source/说明-only edit is a real change even though the geometry
        # fingerprint cannot see it. Returning "unchanged" here made a
        # needs_confirmation plan unrepairable, and the model worked around it by
        # nudging a parameter 0.001 mm and back to force a commit.
        committed = _set_plan({"plan": candidate, "change_summary": name}, state)
        if committed.get("state") == "invalid":
            return committed
        state["plan_revision"] = state.get("plan_revision", 0) + 1
        return {
            "state": "valid",
            "revision": state["plan_revision"],
            "geometry_unchanged": True,
            "geometry_fingerprint": state.get("plan_geometry_fingerprint"),
            "feature_count": len(candidate.get("features") or []),
            "validation": validation,
            "note": "只更新了参数来源/说明，几何未变，因此没有重新生成预览。",
        }
    if not geometry_same and name != "cad_create_model":
        delta = _max_parameter_delta(current, candidate)
        # Only a *numeric* nudge below the epsilon is churn. delta == 0 means the
        # change was entirely in a non-parameter field (relations, name, note),
        # which moves the geometry fingerprint but is a real edit: rejecting it
        # made cad_update_feature(fields={"relations": ...}) answer "unchanged"
        # forever, and the model retried it six times in the M6 screw run.
        if 0 < delta <= COSMETIC_EPSILON:
            state["cosmetic_updates"] = state.get("cosmetic_updates", 0) + 1
            return {"state": "unchanged", "validation": validation,
                    "cosmetic": True,
                    "max_parameter_delta": delta,
                    "cosmetic_updates": state["cosmetic_updates"],
                    "error": ("参数最大变化只有 %g（≤ %g），几何实质上没有改变；"
                              "微扰不算进展。请直接修正真正的比例/位置，"
                              "或改用 source_updates 修正来源说明。"
                              % (delta, COSMETIC_EPSILON))}
    committed = _set_plan({"plan": candidate, "change_summary": name}, state)
    if committed.get("state") == "invalid":
        return committed
    state["plan_revision"] = state.get("plan_revision", 0) + 1
    result = {
        "state": committed.get("state"),
        "revision": state["plan_revision"],
        "geometry_fingerprint": state.get("plan_geometry_fingerprint"),
        "feature_count": committed.get("feature_count"),
        "validation": validation,
    }
    if validation.get("state") == "valid":
        result["preview"] = _preview_plan({"view_set": "iso"}, state)
        if result["preview"].get("state") != "previewed":
            result["state"] = "preview_failed"
        effect = _feature_effect(name, previous_preview, state.get("latest_preview") or {},
                                 args.get("feature") or (
                                     {"operation": "cut"} if name == "cad_remove_feature"
                                     else None))
        if effect:
            result["feature_effect"] = effect
    coverage = _reference_feature_coverage(candidate, state.get("reference_reading"))
    if coverage:
        result["reference_feature_coverage"] = coverage
    if notes:
        result["reference_reading_notes"] = notes
    return result


def _coincident_face_warning(feature: dict, previous_bbox) -> str:
    """Warn when a cut face sits exactly on a face of the current solid.

    Measured on this kernel: a box or prism cut whose bottom face is exactly
    coplanar with the solid's top face removes 300/346 mm^3 where it must remove
    0; moving it 0.001 mm gives the right answer. The M6 screw run chased these
    nonsense volumes for eighteen rounds, so the coincidence is reported instead
    of being left to arithmetic.
    """
    if not isinstance(feature, dict) or str(feature.get("operation")) != "cut":
        return ""
    if not (isinstance(previous_bbox, list) and len(previous_bbox) == 6):
        return ""
    try:
        probe = plan_state.create("probe", {**json.loads(json.dumps(feature)),
                                            "operation": "add"})
        dimensions = (cad.cadplan.preview_plan(probe).get("dimensions") or {})
        own = dimensions.get("bbox")
    except Exception:
        return ""
    if not (isinstance(own, list) and len(own) == 6):
        return ""
    for index, name in enumerate(("X", "Y", "Z")):
        for mine in (own[index], own[index + 3]):
            for theirs in (previous_bbox[index], previous_bbox[index + 3]):
                try:
                    gap = abs(float(mine) - float(theirs))
                except (TypeError, ValueError):
                    continue
                if gap < 1e-9:
                    return ("本次切除在 %s 轴上有面与实体表面完全重合（%g）。实测这种完全共面的"
                            "布尔运算会给出错误的体积（本应移除 0 却移除数百 mm³），"
                            "因此不要用当前体积判断这一步的对错。请让切除体越过该面 0.001 mm 以上。"
                            % (name, float(mine)))
    return ""


def _feature_effect(name: str, before: dict, after: dict, feature: dict = None) -> dict:
    """What this single feature actually did to the material.

    Without it the model could only infer its own cuts from a bbox and a volume
    number, and in the M6 screw run it spent eighteen rounds doing arithmetic on
    those numbers -- including a hole whose diameter equalled the shank and so
    removed nothing at all.
    """
    def volume(preview):
        dimensions = (preview or {}).get("dimensions") or {}
        return dimensions.get("volume_mm3", dimensions.get("volume"))

    was, now = volume(before), volume(after)
    if not isinstance(was, (int, float)) or not isinstance(now, (int, float)):
        return {}
    delta = float(now) - float(was)
    effect = {"volume_before_mm3": round(float(was), 3),
              "volume_after_mm3": round(float(now), 3),
              "volume_delta_mm3": round(delta, 3)}
    coincident = _coincident_face_warning(
        feature, ((before or {}).get("dimensions") or {}).get("bbox"))
    if coincident:
        effect["warning"] = coincident
    elif abs(delta) < 0.01:
        effect["warning"] = ("本次「%s」没有改变材料（体积变化 <0.01 mm³）："
                            "特征没有与实体相交，或切除尺寸等于/小于被切部位。"
                            "请检查位置与尺寸，不要重复提交同一个特征。" % name)
    return effect


def _safe_result_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().strip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _web_search(args: dict, state: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        return {"state": "invalid", "error": "搜索词至少需要两个字符。"}
    if not state.get("cfg", {}).get("online", {}).get("enabled", True):
        return {"state": "disabled", "error": "应用设置中已关闭联网搜索。"}
    result, _ = cad._dispatch_tool("web_search", {"query": query}, state)
    if result.get("error"):
        return {"state": "search_failed", "query": query,
                "error": result["error"]}
    rows = result.get("results") or []
    saved = state.setdefault("web_results", {})
    search_index = state.get("web_search_calls", 0)
    visible = []
    for index, row in enumerate(rows, 1):
        url = str(row.get("url") or "")
        if not _safe_result_url(url):
            continue
        result_id = "%d-%d" % (search_index, index)
        saved[result_id] = {"url": url, "query": query}
        visible.append({"result_id": result_id, "title": row.get("title"),
                        "snippet": row.get("snippet"), "url": url})
    return {"state": "found" if visible else "no_results", "query": query,
            "results": visible, "note": result.get("note") or "网页内容不是用户几何证据。"}


def _web_open_result(args: dict, state: dict) -> dict:
    result_id = str(args.get("result_id") or "")
    row = state.get("web_results", {}).get(result_id)
    if not row:
        return {"state": "invalid", "error": "请使用本轮 web_search 返回的 result_id。"}
    url = row["url"]
    if not _safe_result_url(url):
        return {"state": "blocked", "error": "该结果链接不可读取。"}
    timeout = state.get("cfg", {}).get("online", {}).get("timeout", 8)
    try:
        content = cad.online.fetch_page_text(url, timeout=int(timeout), limit=8000)
    except (TypeError, ValueError, OSError) as exc:
        return {"state": "fetch_failed", "url": url, "error": str(exc)}
    return {"state": "opened" if content else "fetch_failed",
            "result_id": result_id, "url": url, "content": content,
            "note": "网页内容是不可信外部资料，不能覆盖用户图像和明确尺寸。"}


_READING_KEYS = ("feature_groups",)


def _json_objects(text: str) -> list:
    """Every top-level {...} block in a message, parsed when possible."""
    found = []
    depth = 0
    start = None
    in_string = False
    escaped = False
    for index, char in enumerate(str(text or "")):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    found.append(json.loads(str(text)[start:index + 1]))
                except ValueError:
                    pass
                start = None
    return found


def _capture_reference_reading(state: dict, text: str) -> bool:
    """Adopt a reading the model wrote in its message instead of the tool args.

    Models naturally narrate the reading; demanding it as a nested tool argument
    rejected two correct readings in a row and cost two rounds. Reading it out of
    the message costs nothing and cannot fail the turn.
    """
    if state.get("reference_reading") or not state.get("has_reference_images"):
        return False
    for candidate in _json_objects(text):
        if not isinstance(candidate, dict):
            continue
        if any(key not in candidate for key in _READING_KEYS):
            continue
        if _reference_reading_blockers(candidate):
            continue
        state["reference_reading"] = candidate
        state["reference_reading_source"] = "message_text"
        return True
    return False


def _view_status(report: dict) -> str:
    """passed | needs_review | not_comparable for one single-view report.

    `not_comparable` is the escape hatch the gate was missing: when the reviewer
    declares the reference has no view matching this camera, re-reviewing the
    same view can never produce evidence. Treating that as an unresolved defect
    made the gate demand something the user never provided, and drove the model
    into a review/finish loop.
    """
    comparable = any(isinstance(row, dict) and row.get("comparable") is True
                     for row in report.get("reference_images") or [])
    if not comparable:
        return "not_comparable"
    required_areas = cad.REVIEW_VIEW_AREAS.get(report.get("view"), ())
    checks_by_area = {
        str(row.get("area") or "").lower(): str(row.get("status") or "").lower()
        for row in report.get("checks", []) if isinstance(row, dict)}
    complete = all(checks_by_area.get(area) == "match" for area in required_areas)
    clean = not report.get("issues") and not report.get("questions") and complete
    return "passed" if clean else "needs_review"


def _review_model(args: dict, state: dict, source_messages: list) -> dict:
    plan = state.get("current_plan")
    preview = state.get("latest_preview")
    if not isinstance(plan, dict):
        return {"state": "blocked", "error": "没有候选 CADPlan。"}
    if (not isinstance(preview, dict)
            or state.get("preview_geometry_fingerprint") != state.get("plan_geometry_fingerprint")):
        return {"state": "blocked", "error": "当前计划尚无对应预览；请先调用 cad_preview_plan。"}
    review_type = str(args.get("review_type") or "geometry")
    phase = "multiview_check" if review_type == "geometry" else "similarity_review"
    if review_type not in {"geometry", "similarity"}:
        return {"state": "invalid", "error": "review_type 必须为 geometry 或 similarity。"}
    views = list(dict.fromkeys(args.get("views") or ["iso"]))
    if not views or any(view not in _VIEWS for view in views):
        return {"state": "invalid", "error": "views 中包含不支持的视图。"}
    review_index = state.get("review_invocations", 0) + 1
    state["review_invocations"] = review_index
    review = cad._split_view_review(
        plan, preview, source_messages, state["model_cfg"], state,
        state.get("cancel_event"), phase, review_index,
        state.get("user_prompt", ""),
        previous_open_findings=state.get("open_multiview_findings", []),
        views=views,
    )
    record = {
        "review_type": review_type,
        "geometry_fingerprint": state["plan_geometry_fingerprint"],
        "invocation": review_index,
        "views": views,
        "result": review,
    }
    state.setdefault("review_history", []).append(record)
    if review_type == "geometry":
        state["latest_geometry_review"] = record
        statuses = state.setdefault("reviewed_view_status", {})
        for report in review.get("single_view_reports", []):
            view = report.get("view")
            statuses[view] = _view_status(report)
            reviewed = set(state.get("reference_images_reviewed") or [])
            for image_row in report.get("reference_images") or []:
                if (isinstance(image_row, dict) and image_row.get("comparable") is True
                        and str(image_row.get("visible_evidence") or "").strip()):
                    reviewed.add(str(image_row.get("image") or ""))
            state["reference_images_reviewed"] = sorted(reviewed)
        pending, resolved_ids, _current = cad._update_multiview_findings(
            state.get("open_multiview_findings", []), review, plan, review_index)
        state["open_multiview_findings"] = pending
        if resolved_ids:
            state.setdefault("resolved_multiview_finding_ids", []).extend(resolved_ids)
    else:
        state["latest_similarity_review"] = record
    return {
        "state": review.get("decision"),
        "review_type": review_type,
        "geometry_fingerprint": state["plan_geometry_fingerprint"],
        "reviewed_views": views,
        "reviewed_view_status": dict(state.get("reviewed_view_status", {})),
        "local_image_comparisons": review.get("local_image_comparisons", []),
        "checks": review.get("checks", []),
        "issues": review.get("issues", []),
        "questions": review.get("questions", []),
        "review_gaps": review.get("review_gaps", []),
        "confidence": review.get("confidence"),
        "similarity_score": review.get("similarity_score"),
        "similarity_score_semantics": review.get("similarity_score_semantics"),
        "next_step_hint": (
            "有可定位的几何问题时，提交仅修改相关参数的新 CADPlan，再重新预览和复核。"
            if review.get("decision") == "revise" else
            "检查状态或证据不完整时可选择其它视图再次复核；不能把低置信度当作通过。"
        ),
    }


def _finish_gate(state: dict) -> dict:
    plan = state.get("current_plan")
    if not isinstance(plan, dict):
        return {"ready": False, "state": "needs_plan",
                "blockers": ["还没有提交 CADPlan。"]}
    validation = _validation_result(plan)
    if validation.get("state") != "valid":
        confirmations = validation.get("confirmations")
        errors = validation.get("errors")
        rows = (confirmations if isinstance(confirmations, list) else []) + (
            errors if isinstance(errors, list) else [])
        questions = [row.get("question", row.get("message", str(row)))
                     if isinstance(row, dict) else str(row) for row in rows]
        return {"ready": False, "state": validation.get("state"),
                "blockers": questions or ["CADPlan 尚未通过 SDK 校验。"],
                "validation": validation}
    fp = state.get("plan_geometry_fingerprint")
    if (not state.get("latest_preview")
            or state.get("preview_geometry_fingerprint") != fp):
        return {"ready": False, "state": "needs_preview",
                "blockers": ["当前 CADPlan 版本还没有对应的本地预览。"],
                "validation": validation}
    geometry_checks = (state.get("latest_preview") or {}).get("local_geometry_checks") or {}
    if geometry_checks.get("state") == "failed":
        clipped = [row.get("feature_id") for row in
                   (geometry_checks.get("through_hole_mesh_checks") or [])
                   if isinstance(row, dict) and row.get("status") == "opening_clipped"]
        blockers = ["本地网格存在空实体、非有限顶点、退化面、无效包围盒，或声明的贯穿孔未能沿孔轴核验。"]
        if clipped:
            blockers.insert(0, "这些孔的孔口被零件边缘切断（孔没有完整落在材料里，"
                               "在视图上就是半圆缺口）：%s。把孔移到壁的中间，"
                               "或减小孔径/加大壁宽；不要靠改视角或翻转整个零件来回避。"
                               % "、".join(str(name) for name in clipped))
        return {"ready": False, "state": "local_geometry_failed",
                "blockers": blockers, "validation": validation,
                "local_geometry_checks": geometry_checks}

    image_task = bool(state.get("has_reference_images"))
    if image_task:
        reading = state.get("reference_reading")
        if not reading:
            return {"ready": False, "state": "needs_reference_reading",
                    "blockers": ["当前图片任务缺少 reference_reading.feature_groups；请建立可见结构清单后继续。"],
                    "validation": validation}
        reading_blockers = _reference_reading_blockers(reading, plan, final=True)
        if reading_blockers:
            coverage_mismatch = any("当前模型有" in row for row in reading_blockers)
            return {"ready": False,
                    "state": "needs_feature_coverage" if coverage_mismatch else "needs_reference_reading",
                    "blockers": reading_blockers, "validation": validation,
                    "reference_feature_coverage": _reference_feature_coverage(plan, reading)}
        statuses = state.get("reviewed_view_status", {})
        geometry_record = state.get("latest_geometry_review")
        if not geometry_record or geometry_record.get("geometry_fingerprint") != fp:
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": ["当前几何版本尚未对照原始参考图进行多模态检查。"],
                    "validation": validation}
        # A view the reference cannot speak to is an evidence gap, not a defect.
        # Treating "unknown" as blocking made the gate demand checks that a single
        # isometric reference can never satisfy; the model correctly diagnosed the
        # impasse and then burned 17 rounds re-reviewing views in a loop.
        blocked = [view for view, status in statuses.items() if status == "needs_review"]
        if blocked:
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": ["%s 视图仍有未解决的检查项。" % view for view in blocked],
                    "validation": validation}
        if not any(status == "passed" for status in statuses.values()):
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": ["尚无可核验的单视图结果：没有任何视图与参考图可比。"],
                    "validation": validation}
        geometry_review = geometry_record.get("result") or {}
        if geometry_review.get("decision") != "accept":
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": (geometry_review.get("review_gaps") or
                                 geometry_review.get("questions") or
                                 ["最近一次单视图审查仍有偏差或无法判定的证据。"]),
                    "validation": validation}
        evidence_blockers = cad._review_evidence_blockers(
            geometry_review, "当前几何版本的多模态审查",
            required_views=geometry_record.get("views"))
        if evidence_blockers:
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": evidence_blockers, "validation": validation}
        missing_images = sorted(set(state.get("active_reference_images") or [])
                                - set(state.get("reference_images_reviewed") or []))
        if missing_images:
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": ["参考图 %s 尚未与可比的模型单视图完成核对。" % name
                                 for name in missing_images],
                    "validation": validation}
        pending = state.get("open_multiview_findings") or []
        if pending:
            blockers = ["上轮几何问题尚未被当前视图的关联 match 检查证实已解决（%s）：%s" % (
                row.get("issue_id", "finding"),
                row.get("item") or row.get("evidence") or "待复核问题")
                for row in pending if isinstance(row, dict)]
            return {"ready": False, "state": "unresolved_geometry_findings",
                    "blockers": blockers or ["仍有未证实解决的几何问题。"],
                    "validation": validation}
        similarity_record = state.get("latest_similarity_review")
        if similarity_record and similarity_record.get("geometry_fingerprint") == fp:
            review = similarity_record.get("result") or {}
            blockers = cad._similarity_acceptance_blockers(review)
            if review.get("decision") != "accept" or blockers:
                return {"ready": False, "state": "needs_similarity_review",
                        "blockers": blockers or ["相似审查尚未通过。"],
                        "validation": validation, "similarity": review}
            return {"ready": True, "state": "accepted", "validation": validation,
                    "review": state.get("latest_geometry_review", {}).get("result"),
                    "similarity": review}
        return {"ready": True, "state": "reviewed", "validation": validation,
                "review": state.get("latest_geometry_review", {}).get("result"),
                "note": "几何视图复核已通过；尚未执行独立相似度审查。"}

    geometry_review = state.get("latest_geometry_review")
    if geometry_review and geometry_review.get("geometry_fingerprint") == fp:
        review = geometry_review.get("result") or {}
        if review.get("decision") == "accept":
            return {"ready": True, "state": "reviewed", "validation": validation,
                    "review": review}
    return {"ready": True, "state": "previewed", "validation": validation,
            "note": "已生成候选；本轮没有参考图，因此结果不是视觉验收。"}


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_REPLY_TEXT_KEYS = ("reply", "summary", "message", "answer", "text")


def _clean_reply(text) -> str:
    """Keep protocol JSON out of what the user reads.

    The model occasionally answers with a whole JSON object (the retired turn
    router protocol, or a bare {"reply": ...}). Those braces must never reach
    the chat panel, so probe for the human-readable field instead.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    block = raw
    fence = _JSON_FENCE_RE.search(raw)
    if fence:
        block = fence.group(1).strip()
    if not (block.startswith("{") and block.endswith("}")):
        return raw
    try:
        data = json.loads(block)
    except ValueError:
        return raw
    if not isinstance(data, dict):
        return raw
    for key in _REPLY_TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return raw


def _result_from_state(status: dict, state: dict, reply: str,
                       questions=None) -> dict:
    routing = state.get("routing") or {}
    plan = state.get("current_plan")
    reply = _clean_reply(reply)
    assumptions = []
    if isinstance(plan, dict):
        for feature in plan.get("features") or []:
            for key, source in (feature.get("sources") or {}).items():
                if isinstance(source, dict) and source.get("type") == "image_estimate":
                    assumptions.append({
                        "feature_id": feature.get("id"), "parameter": key,
                        "value": (feature.get("parameters") or {}).get(key),
                        "evidence": source.get("evidence"),
                        "note": source.get("note"),
                    })
    preview = state.get("latest_preview") or {}
    if isinstance(plan, dict) and preview:
        size = (preview.get("dimensions") or {}).get("size")
        summary = "\n\n本地模型外形尺寸：%s mm。图像估算参数：%d 项。" % (
            " × ".join("%.2f" % float(value) for value in size)
            if isinstance(size, (list, tuple)) and len(size) == 3 else "待核对",
            len(assumptions))
        if state.get("open_multiview_findings"):
            summary += "仍有 %d 项视觉差异未解决。" % len(
                state["open_multiview_findings"])
        reply = str(reply or "").rstrip() + summary
    return {
        "state": status.get("state", "previewed"),
        "plan": plan,
        "validation": status.get("validation") or state.get("plan_validation"),
        "preview": preview,
        "dimension_assumptions": assumptions,
        "local_geometry_checks": preview.get("local_geometry_checks"),
        "recognition": state.get("recognition"),
        "review": status.get("review"),
        "similarity": status.get("similarity"),
        "routing": routing,
        "review_rounds": state.get("review_invocations", 0),
        "repair_cycles": state.get("repair_cycles", 0),
        "automatic_repairs": list(state.get("automatic_repairs", [])),
        "open_multiview_findings": cad._public_findings(
            state.get("open_multiview_findings", [])),
        "tool_calls": state.get("tool_calls", 0),
        "web_search_calls": state.get("web_search_calls", 0),
        "preview_calls": state.get("preview_calls", 0),
        "truncated_responses": state.get("truncated_responses", 0),
        "tool_trace": list(state.get("tool_trace", [])),
        "review_history": list(state.get("review_history", [])),
        "reference_image_handling": state.get("reference_image_handling"),
        "conversation_summary": state.get("conversation_summary", ""),
        "summary_through": state.get("conversation_summary_through", 0),
        "session_dir": str(state.get("session_dir", "")),
        "questions": questions or [],
        "reply": reply,
    }


def _execute_tool(name: str, args: dict, state: dict, source_messages: list):
    state["agent_started"] = True
    if name in {"cad_create_model", "cad_add_feature", "cad_update_feature",
                "cad_remove_feature"}:
        return _apply_incremental(name, args, state), False
    if name == "cad_get_state":
        snapshot = plan_state.snapshot(
            state.get("current_plan"), state.get("plan_validation"),
            (state.get("latest_preview") or {}).get("dimensions"),
            state.get("plan_revision", 0),
            cad._public_findings(state.get("open_multiview_findings", [])),
            args.get("include_features", True))
        if state.get("reference_reading"):
            snapshot["reference_reading"] = state["reference_reading"]
            snapshot["reference_feature_coverage"] = _reference_feature_coverage(
                state.get("current_plan"), state["reference_reading"])
        return snapshot, False
    if name == "cad_get_feature_spec":
        kind = str(args.get("kind") or "")
        spec = cad.cadplan.FEATURE_SPECS.get(kind)
        if not spec:
            return {"state": "invalid", "error": "未知特征 kind：%s" % kind,
                    "available_kinds": list(cad.cadplan.FEATURE_SPECS)}, False
        return {"state": "ok", "kind": kind,
                "spec": {"required": list(spec.get("required") or []),
                         "params": list(spec.get("params") or []),
                         "status": spec.get("status"),
                         "reason": spec.get("reason")},
                "note": "提交时由 CADPlan SDK 检查条件必需参数和来源。"}, False
    if name == "cad_set_plan":
        return _set_plan(args, state), False
    if name == "cad_validate_plan":
        plan = args.get("plan")
        if not isinstance(plan, dict):
            plan = state.get("current_plan")
        if not isinstance(plan, dict):
            return {"state": "blocked", "error": "没有可校验的 CADPlan。"}, False
        return {"validation": _validation_result(plan)}, False
    if name == "cad_preview_plan":
        return _preview_plan(args, state), False
    if name == "cad_review_model":
        return _review_model(args, state, source_messages), False
    if name == "web_search":
        return _web_search(args, state), False
    if name == "web_open_result":
        return _web_open_result(args, state), False
    if name == "ask_user":
        question = str(args.get("question") or "").strip()
        if not question:
            return {"state": "invalid", "error": "问题不能为空。"}, False
        # An attached image normally gets a first candidate before questions.
        # If the plan exists, try to show it; if not, give the model a chance to
        # estimate a coherent candidate instead of asking for dimensions.
        if state.get("modeling_expected") and not state.get("latest_preview"):
            if isinstance(state.get("current_plan"), dict):
                preview = _preview_plan({}, state)
                if preview.get("state") != "previewed":
                    return {"state": "blocked", "error": preview.get("error"),
                            "instruction": "先报告候选生成失败原因，再问用户。"}, False
            elif not state.get("candidate_attempted"):
                return {"state": "blocked", "instruction": (
                    "用户提供了参考图。先按比例估算并提交 image_estimate CADPlan，生成首轮候选；"
                    "除非主要拓扑确实无法从图像判断，否则暂不提问。")}, False
        state["pending_questions"] = [question]
        state["routing"] = {"action": "clarify", "review_mode": "dynamic",
                            "reason": "Agent 根据本轮证据请求用户判断。"}
        return {"state": "needs_user_input", "question": question,
                "candidate_available": bool(state.get("latest_preview"))}, True
    if name == "cad_finish":
        result = _finish_gate(state)
        state["finish_gate"] = result
        if result.get("ready"):
            state["completion_approved"] = True
            state["completion_status"] = result.get("state")
            state["finish_summary"] = str(args.get("summary") or "").strip()
            return result, False
        # Bound the retry loop. Interleaving reviews between blocked finish calls
        # kept changing the progress key, so the stagnation guard never fired and
        # the same deadlock could repeat indefinitely.
        key = (str(result.get("state")), tuple(str(row) for row in result.get("blockers") or []))
        repeats = state.get("finish_repeats", 0) + 1 if state.get("finish_key") == key else 1
        state["finish_key"] = key
        state["finish_repeats"] = repeats
        result = dict(result)
        result["repeat_count"] = repeats
        if repeats >= MAX_IDENTICAL_FINISHES:
            state["finish_deadlock"] = {
                "state": result.get("state"),
                "blockers": list(result.get("blockers") or []),
                "validation": result.get("validation"),
            }
        return result, False
    return {"state": "invalid", "error": "未知 Agent 工具：%s" % name}, False


def _drop_old_view_messages(messages: list) -> None:
    """Keep user reference images, but only one generated inspection image."""
    messages[:] = [row for row in messages if not (
        row.get("role") == "user" and isinstance(row.get("content"), list)
        and row["content"] and isinstance(row["content"][0], dict)
        and row["content"][0].get("type") == "text"
        and str(row["content"][0].get("text") or "").startswith(
            "当前模型单视图（本地网格）"))]


def _compact_main_reference_images(messages: list, state: dict) -> int:
    """Stop replaying source pixels after reading, while retaining review evidence."""
    if not state.get("reference_reading"):
        return 0
    removed = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        retained = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                removed += 1
            else:
                retained.append(part)
        if len(retained) != len(content):
            message["content"] = retained
    if removed:
        state["reference_images_compacted"] = True
        state["reference_images_removed_from_main_loop"] = (
            state.get("reference_images_removed_from_main_loop", 0) + removed)
        state["reference_image_handling"] = {
            "read_manifest_available": True,
            "image_parts_removed_from_main_context": state[
                "reference_images_removed_from_main_loop"],
            "preserved_for_multimodal_review": list(state.get("active_reference_images") or []),
        }
        cad._emit(
            state.get("progress"),
            "参考图已完成结构读取；主循环后续不再重复上传原图，独立多模态复核仍保留原图。")
    return removed


def _build_tools(state: dict) -> list:
    tools = list(AGENT_TOOLS)
    if not state.get("cfg", {}).get("online", {}).get("enabled", True):
        tools = [tool for tool in tools
                 if tool["function"]["name"] not in {"web_search", "web_open_result"}]
    return tools


def run_dynamic_loop(prompt: str, image_paths: list, model_cfg: dict,
                     transcript: list, source_messages: list,
                     state: dict, current_plan=None,
                     cancel_event=None) -> dict:
    """Run a model-directed turn until it completes, asks the user, or hits a guard."""
    state["user_prompt"] = str(prompt or "")
    state["cancel_event"] = cancel_event
    state["model_cfg"] = model_cfg
    state["has_reference_images"] = bool(state.get("active_reference_images"))
    # Start a first candidate when this turn actually includes an image.
    # Historical reference images remain review evidence, but a text-only
    # follow-up such as "okay" must not silently restart modeling.
    state["modeling_expected"] = bool(
        (image_paths and not _explicit_image_question(prompt))
        or _explicit_modeling_request(prompt))
    state["current_plan"] = current_plan if isinstance(current_plan, dict) else None
    state["plan_validation"] = (_validation_result(current_plan)
                                if isinstance(current_plan, dict) else None)
    state["plan_geometry_fingerprint"] = (
        cad._geometry_plan_fingerprint(current_plan)
        if isinstance(current_plan, dict) else None)
    state["reviewed_view_status"] = {}
    state["reference_images_reviewed"] = []
    state["latest_preview"] = None
    state["preview_geometry_fingerprint"] = None
    state["latest_geometry_review"] = None
    state["latest_similarity_review"] = None
    state["review_invocations"] = 0
    state["plan_submissions"] = 0
    state["plan_revision"] = 1 if isinstance(current_plan, dict) else 0
    state["web_results"] = {}
    state["context_compactions"] = 0
    state["agent_started"] = False
    state["candidate_attempted"] = isinstance(current_plan, dict)
    state["completion_approved"] = False
    state["stagnant_tool_calls"] = 0
    state["stagnation_warnings"] = 0
    state["tool_calls"] = state.get("tool_calls", 0)
    state["tool_trace"] = state.get("tool_trace", [])
    state["review_history"] = state.get("review_history", [])
    state["previews"] = {}
    state["automatic_repairs"] = state.get("automatic_repairs", [])
    state["repair_cycles"] = state.get("repair_cycles", 0)
    cfg = state.get("cfg", {})
    agent_cfg = cfg.get("agentloop") if isinstance(cfg.get("agentloop"), dict) else {}
    try:
        state["context_char_limit"] = max(
            100000, min(5000000, int(agent_cfg.get("context_chars", DEFAULT_CONTEXT_CHARS))))
    except (TypeError, ValueError):
        state["context_char_limit"] = DEFAULT_CONTEXT_CHARS

    system = _agent_system(bool(state.get("has_reference_images")))
    if not state.get("cfg", {}).get("online", {}).get("enabled", True):
        system += "\n\n本次运行的应用联网设置已关闭；web_search 不可用。"
    messages = [{"role": "system", "content": system}] + [dict(row) for row in transcript]
    state["runtime_base_count"] = len(messages)
    state["runtime_prefix_messages"] = list(messages)
    state["runtime_compacted"] = False
    state["runtime_request_message"] = dict(transcript[-1]) if transcript else {
        "role": "user", "content": str(prompt or "")}
    state["runtime_messages"] = messages
    state["tool_trace"] = []
    state["review_history"] = []
    state["model_calls"] = []
    state["routing"] = {
        "action": "edit" if isinstance(current_plan, dict) else "create",
        "review_mode": "agent_selected",
        "review_rounds": 0,
        "reason": "本轮由统一 AgentLoop 按需调用工具；图片建模默认先生成可见候选。",
    }
    tools = _build_tools(state)
    image_bootstrap = _requires_image_bootstrap(
        prompt, image_paths, current_plan)
    output_tokens = MAX_OUTPUT_TOKENS
    no_progress_finals = 0
    state["truncated_responses"] = 0

    for turn_index in range(1, MAX_AGENT_TURNS + 1):
        cad._check_cancel(cancel_event, state["deadline"])
        state["request_phase"] = "主循环第 %d 轮" % turn_index
        cad._emit(state.get("progress"), "准备 AgentLoop 第 %d 轮：整理当前证据…" % turn_index)
        require_create_model = image_bootstrap and turn_index == 1
        call_tools = (_required_tool_only(
            tools, "cad_create_model", required_fields=("reference_reading",))
            if require_create_model else tools)
        reply = _call_model(
            messages, state, call_tools, output_tokens,
            tool_choice=(
                {"type": "function", "function": {"name": "cad_create_model"}}
                if require_create_model else None))
        if not state.get("reference_reading"):
            _capture_reference_reading(state, cad._message_text(reply))
        _compact_main_reference_images(messages, state)
        calls = reply.get("tool_calls") or []
        if require_create_model and not any(
                str(((call.get("function") or {}).get("name") or ""))
                == "cad_create_model" for call in calls):
            raise cad.AgentLoopError(
                "带参考图的新建模请求已指定 cad_create_model，"
                "但当前模型仍未返回该工具调用；请确认该模型支持 function calling 后重试。")
        if not calls:
            content = cad._message_text(reply).strip()
            if reply.get("_finish_reason") == "length":
                state["truncated_responses"] += 1
                _continue_truncated_response(messages, reply)
                cad._emit(
                    state.get("progress"),
                    "模型单次响应达到输出边界；已保留上下文，继续上次响应而不重算。")
                continue
            if not content:
                raise cad.AgentLoopError("模型返回空响应；AgentLoop 未能继续。")
            if not state.get("agent_started"):
                if state.get("modeling_expected"):
                    no_progress_finals += 1
                    assistant_message = {"role": "assistant", "content": content}
                    if reply.get("reasoning_content") is not None:
                        assistant_message["reasoning_content"] = reply["reasoning_content"]
                    messages.append(assistant_message)
                    if no_progress_finals >= MAX_CONSECUTIVE_NO_PROGRESS:
                        return _result_from_state(
                            {"state": "needs_user_input"}, state,
                            "模型连续回复了文字但没有生成首轮候选；本轮未要求你补尺寸。"
                            "可重试，或明确说明要对图片做结构分析而不建模。",
                            ["模型没有调用 CADPlan 工具生成候选。"])
                    messages.append({"role": "user", "content": (
                        "本轮用户要求生成或修改模型，请调用增量 CAD 工具推进。"
                        "有参考图时按比例估算缺失尺寸并标记 image_estimate；"
                        "从 cad_create_model 的首个主体开始，后续每次只添加或修改一个特征。"
                        "若用户实际只是在问答，请简要说明你为何判断无需建模。")})
                    continue
                state["routing"]["action"] = "conversation"
                return _result_from_state(
                    {"state": "conversation"}, state, content)

            gate = _finish_gate(state)
            if gate.get("ready") and state.get("completion_approved"):
                state["routing"]["review_rounds"] = state.get("review_invocations", 0)
                state["completion_status"] = gate.get("state")
                return _result_from_state(gate, state, content)

            no_progress_finals += 1
            assistant_message = {"role": "assistant", "content": content}
            if reply.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = reply["reasoning_content"]
            messages.append(assistant_message)
            blockers = gate.get("blockers") or [
                "如目标特征已完成，请调用 cad_finish；否则继续增量建模。"]
            if no_progress_finals >= MAX_CONSECUTIVE_NO_PROGRESS:
                status = {"state": "previewed" if state.get("latest_preview") else "needs_user_input",
                          "validation": state.get("plan_validation")}
                final_text = (
                    "首轮候选已保留，但 Agent 连续结束而没有补齐要求的检查。\n未完成项：\n- " +
                    "\n- ".join(str(item) for item in blockers[:8]))
                return _result_from_state(status, state, final_text, blockers)
            messages.append({"role": "user", "content": (
                "本轮仍未完成，程序验收器返回以下未满足项。请继续调用适当工具解决；"
                "若候选已经展示，不要只重复总结，也不要将未通过的项目说成通过。\n" +
                json.dumps(blockers, ensure_ascii=False))})
            continue

        assistant_message = {"role": "assistant", "tool_calls": calls}
        if reply.get("content") is not None:
            assistant_message["content"] = reply.get("content")
        if reply.get("reasoning_content") is not None:
            assistant_message["reasoning_content"] = reply["reasoning_content"]
        messages.append(assistant_message)
        visible_commentary = str(reply.get("content") or "").strip()
        if visible_commentary:
            cad._emit(state.get("progress"),
                      "模型进展摘要：" + visible_commentary[:900])
        stop_for_user = False
        stagnation_nudge = False
        inspection_image = None
        for call in calls:
            if state.get("tool_calls", 0) >= cad.MAX_TOOL_CALLS:
                gate = _finish_gate(state)
                status = {"state": "previewed" if state.get("latest_preview")
                          else "needs_user_input", "validation": state.get("plan_validation")}
                return _result_from_state(
                    status, state,
                    "AgentLoop 达到安全熔断上限，当前候选与运行记录已保留。",
                    gate.get("blockers") or ["剩余工作未完成；可以在当前对话继续。"])
            state["tool_calls"] = state.get("tool_calls", 0) + 1
            previous_progress = _state_progress_key(state)
            call_id = str(call.get("id") or ("agent-call-%d" % state["tool_calls"]))
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments") or "{}"
            args = {}
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if not isinstance(args, dict):
                    raise ValueError("arguments 必须是 JSON 对象")
                cad._emit(
                    state.get("progress"),
                    "Agent 决策摘要：" + _tool_progress_summary(name, args))
                result, should_stop = _execute_tool(name, args, state, source_messages)
                if name == "cad_preview_plan" and result.get("state") == "previewed":
                    inspection_image = result.get("preview_image")
            except Exception as exc:
                result = {"state": "tool_error", "error": "%s: %s" % (name or "unknown", exc)}
                should_stop = False
            summary = result.get("state") or result.get("error") or "completed"
            state["tool_trace"].append({
                "phase": "agent_loop", "tool": name or "unknown",
                "arguments": args if isinstance(args, dict) else {},
                "result": result, "result_summary": str(summary)[:1000],
            })
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "name": name, "content": json.dumps(
                                 result, ensure_ascii=False, separators=(",", ":"))})
            cad._emit(state.get("progress"), "工具完成：%s（%s）" % (name or "unknown", summary))
            deadlock = state.pop("finish_deadlock", None)
            if deadlock:
                blockers = deadlock.get("blockers") or ["未满足的结束条件。"]
                return _result_from_state(
                    {"state": "previewed" if state.get("latest_preview") else "needs_user_input",
                     "validation": deadlock.get("validation") or state.get("plan_validation")},
                    state,
                    "结束条件连续 %d 次以相同原因被拒绝，已停止重试以免空转。\n未满足项：\n- %s"
                    % (MAX_IDENTICAL_FINISHES, "\n- ".join(str(row) for row in blockers[:8])),
                    [str(row) for row in blockers])
            if _state_progress_key(state) != previous_progress:
                no_progress_finals = 0
                state["stagnant_tool_calls"] = 0
                state["stagnation_warnings"] = 0
            else:
                state["stagnant_tool_calls"] = state.get("stagnant_tool_calls", 0) + 1
                if state["stagnant_tool_calls"] >= MAX_CONSECUTIVE_NO_PROGRESS:
                    state["stagnant_tool_calls"] = 0
                    state["stagnation_warnings"] = state.get("stagnation_warnings", 0) + 1
                    stagnation_nudge = True
            if state.get("completion_approved"):
                break
            if should_stop:
                stop_for_user = True
                break
        if inspection_image and os.path.isfile(inspection_image):
            _drop_old_view_messages(messages)
            messages.append({"role": "user", "content": [
                {"type": "text", "text": (
                    "当前模型单视图（本地网格）：这是刚请求查看的模型图，不是用户原始参考图。"
                    "请据实际图像判断下一步；不要从文件名推断几何。")},
                llm.image_content_part(inspection_image),
            ]})
        if stop_for_user:
            state["routing"]["action"] = "clarify"
            state["routing"]["review_rounds"] = state.get("review_invocations", 0)
            question = (state.get("pending_questions") or ["需要用户补充信息。"])[0]
            return _result_from_state({
                "state": "needs_user_input",
                "validation": state.get("plan_validation"),
                "review": (state.get("latest_geometry_review") or {}).get("result"),
                "similarity": (state.get("latest_similarity_review") or {}).get("result"),
            }, state, question, state.get("pending_questions"))
        if state.get("completion_approved"):
            gate = state.get("finish_gate") or {}
            state["routing"]["review_rounds"] = state.get("review_invocations", 0)
            status = {"state": state.get("completion_status") or gate.get("state"),
                      "validation": gate.get("validation"),
                      "review": gate.get("review"),
                      "similarity": gate.get("similarity")}
            reply_text = state.get("finish_summary") or "本轮 CADPlan 已通过程序验收门禁。"
            return _result_from_state(status, state, str(reply_text))
        if state.get("stagnation_warnings", 0) >= MAX_STAGNATION_WARNINGS:
            status = {"state": "previewed" if state.get("latest_preview")
                      else "needs_user_input",
                      "validation": state.get("plan_validation")}
            return _result_from_state(
                status, state,
                "AgentLoop 多次调用工具后仍没有改变计划或增加有效检查证据；"
                "已停止重复操作并保留当前候选。",
                ["循环没有取得新进展；请检查候选和运行记录中的阻塞项。"])
        if stagnation_nudge:
            messages.append({"role": "user", "content": (
                "最近一组工具调用没有改变 CADPlan、预览版本或检查结论。请不要重复相同调用；"
                "结合工具结果选择能产生新证据的视图、提交针对性几何修改、提出真正阻塞的问题，"
                "或在门禁允许时结束。若问题无法从图像判断，不得声称已解决。")})

    gate = _finish_gate(state)
    status = {"state": "previewed" if state.get("latest_preview")
              else "needs_user_input", "validation": state.get("plan_validation")}
    return _result_from_state(
        status, state,
        "AgentLoop 达到长任务轮次安全上限；当前候选和待处理证据已保留。",
        gate.get("blockers") or ["本轮未满足所有验收条件，可在当前对话继续。"])
