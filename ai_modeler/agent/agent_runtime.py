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
MAX_CONSECUTIVE_NO_TOOL_RESPONSES = 2
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
        r"(?:这张|这幅|这个|该|根据|按照)?(?:图片|图像|照片|图).{0,24}"
        r"(?:怎么|如何|怎样|能否|可不可以|可以).{0,12}"
        r"(?:建模|创建|生成|制作|复刻|还原)",
        r"(?:分析|讨论|说明).{0,16}(?:这张|这幅|这个|该)?(?:图片|图像|照片|图)"
        r".{0,20}(?:怎么|如何|怎样|能否).{0,12}"
        r"(?:建模|创建|生成|制作|复刻|还原)",
        r"(?:what is this|describe (?:the )?(?:image|picture|photo)|identify (?:the )?(?:image|picture|photo)|read the text)",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _explicit_modeling_request(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if re.match(r"^(?:如何|怎么|为什么|能否|怎样|what\b|how\b|why\b)", text):
        return False
    return bool(re.search(
        r"(?:创建|建立|生成|建模|绘制|画出|制作|复刻|还原|添加|增加|删除|移除|"
        r"修改|调整|缩小|放大|打孔|切除|拉伸|旋转|新建|create|build|model|draw|"
        r"add|remove|resize|modify)", text, re.IGNORECASE))


def _modeling_expected(prompt: str, image_paths: list) -> bool:
    if image_paths:
        return not _explicit_image_question(prompt)
    return _explicit_modeling_request(prompt)


def _requires_initial_model_creation(prompt: str, image_paths: list,
                                     current_plan: Optional[dict]) -> bool:
    """Require an initial CAD candidate for a new modeling turn.

    An attached image is the modeling request by default; explicit image Q&A
    stays conversational. Explicit text modeling requests also start directly
    with the first feature. A supplied plan means this is an existing-model
    continuation, so the model can choose any appropriate tool.
    """
    return bool(_modeling_expected(prompt, image_paths)
                and not isinstance(current_plan, dict))


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


def _initial_creation_tools(tools: list, has_reference_images: bool) -> list:
    return _required_tool_only(tools, "cad_create_model")


def _initial_reference_inspection_tool(tools: list) -> list:
    return _required_tool_only(tools, "cad_inspect_references", ("reference_reading",))


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


_SOURCE_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": [
            "user", "image_estimate", "derived", "existing_model"]},
        "evidence": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["type"],
}

_RELATION_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": ["depends_on", "pattern_source"]},
        "feature_id": {"type": "string", "minLength": 1},
    },
    "required": ["type", "feature_id"],
}

_FEATURE_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": list(cad.cadplan.FEATURE_SPECS)},
        "operation": {"type": "string", "enum": ["add", "cut"]},
        "parameters": {
            "type": "object",
            "description": ("CADPlan 参数对象。全局 X/Y/Z 分别对应长/宽/高方向，Z 向上；"
                            "hole 的 axis 是孔轴方向，point 是全局 XYZ 上的孔轴点。"
                            "模型 front 视图从 +Y 看向原点，front 面法向为 +Y；"
                            "right 视图从 +X 看向原点。正面孔通常用 axis=Y，侧面孔用 axis=X。"
                            "hole 必须写 pos='point' 并把三维坐标放在 point；不能把坐标数组放进 pos。"
                            "pocket 只支持 Z 轴；要沿正面法向 Y 切凹槽时使用 box 且 operation='cut'。"
                            "hole 常用 diameter/through/pos/point/axis；"
                            "pattern 常用 mode/count/step，operation 必须与 pattern_source 源特征相同；"
                            "复制 cut 孔特征时 pattern 也必须 cut，否则会把圆柱刀具加成实体。长度和坐标用 mm。"),
            "properties": {
                "length": {"type": "number"}, "width": {"type": "number"},
                "height": {"type": "number"}, "depth": {"type": "number"},
                "diameter": {"type": "number"}, "radius": {"type": "number"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "z": {"type": "number"}, "through": {"type": "boolean"},
                "pos": {"type": "string", "enum": ["center", "point", "pcd", "corners"]},
                "axis": {"type": "string", "enum": ["X", "Y", "Z", "x", "y", "z"]},
                "point": {"type": "array", "minItems": 3, "maxItems": 3,
                          "items": {"type": "number"}},
                "center": {"type": "array", "minItems": 3, "maxItems": 3,
                           "items": {"type": "number"}},
            },
            "additionalProperties": True,
        },
        "sources": {
            "type": "object",
            "description": ("每个数值参数都必须在此给出同名来源项；"
                            "尤其 pattern 的 count 和 step 必须标记为 image_estimate、"
                            "derived、user 或 existing_model。"),
            "additionalProperties": _SOURCE_TOOL_SCHEMA,
        },
        "relations": {
            "type": "array",
            "items": _RELATION_TOOL_SCHEMA,
        },
        "name": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["id", "kind", "operation", "parameters", "sources"],
}

_REFERENCE_READING_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "feature_groups": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "kind": {"type": "string", "minLength": 1},
                    "quantity": {
                        "type": "integer", "minimum": 1,
                        "description": ("跨整个工件估计的唯一物理特征数；孔组按唯一物理孔计，"
                                        "不等于各可见面孔口数之和。"),
                    },
                    "region": {"type": "string", "minLength": 1},
                    "arrangement": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string", "minLength": 1},
                },
                "required": ["id", "kind", "quantity", "region",
                             "arrangement", "evidence"],
            },
        },
        "visible_regions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "image": {"type": "string", "minLength": 1},
                    "id": {"type": "string", "minLength": 1},
                    "region": {"type": "string", "minLength": 1},
                    "surface_state": {"type": "string", "enum": [
                        "solid_face", "recessed_solid_face",
                        "background_opening", "unknown"]},
                    "image_bbox_norm": {
                        "type": "array", "minItems": 4, "maxItems": 4,
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "evidence": {"type": "string", "minLength": 1},
                    "features": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {"type": "string", "minLength": 1},
                                "quantity": {
                                    "type": "integer", "minimum": 1,
                                    "description": ("仅计本可见面上的孔口/特征实例；一个通孔可在入口面和出口面"
                                                    "各出现一次，因此跨面合计可高于唯一物理孔数。"),
                                },
                                "physical_feature_group_id": {
                                    "type": "string", "minLength": 1,
                                    "description": ("可选：仅在有图像证据支持对应关系时，填写 feature_groups 中"
                                                    "已有全局物理特征组的 id；不能确认时省略并记录不确定性。"),
                                },
                                "arrangement": {"type": "string", "minLength": 1},
                                "evidence": {"type": "string", "minLength": 1},
                                "normalized_centers": {
                                    "type": "array", "maxItems": 24,
                                    "items": {"type": "array", "minItems": 2,
                                              "maxItems": 2,
                                              "items": {"type": "number",
                                                        "minimum": 0,
                                                        "maximum": 1}},
                                },
                                "relative_size_order": {
                                    "type": "array", "maxItems": 24,
                                    "items": {"type": "string", "enum": [
                                        "large", "medium", "small", "same", "unknown"]},
                                },
                                "size_order_axis": {"type": "string", "enum": [
                                    "horizontal_left_to_right", "horizontal_right_to_left",
                                    "vertical_top_to_bottom", "vertical_bottom_to_top",
                                    "unknown", "not_applicable"]},
                                "size_order_confidence": {"type": "string", "enum": [
                                    "high", "medium", "low", "unknown"]},
                            },
                            "required": ["kind", "quantity", "arrangement", "evidence"],
                        },
                    },
                },
                "required": ["image", "id", "region", "surface_state",
                             "image_bbox_norm", "evidence", "features"],
            },
        },
        "dimensionless_constraints": {
            "type": "array", "maxItems": 24,
            "description": ("Return an empty array when no ratio has clearly visible endpoints in the same image "
                            "projection. Otherwise record only directly measurable target/baseline relations; "
                            "do not infer hidden depth or estimate dimensions here."),
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "image": {"type": "string", "minLength": 1},
                    "view": {"type": "string", "enum": list(_VIEWS) + ["unknown"]},
                    "target": {"type": "string", "minLength": 1, "maxLength": 100},
                    "baseline": {"type": "string", "minLength": 1, "maxLength": 100},
                    "axis": {"type": "string", "enum": [
                        "horizontal", "vertical", "depth", "overall", "unknown"]},
                    "ratio": {"type": "number", "minimum": 0, "maximum": 100},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                "required": ["image", "view", "target", "baseline", "axis",
                             "ratio", "confidence", "evidence"],
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["feature_groups", "visible_regions", "dimensionless_constraints"],
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
        "cad_inspect_references",
        "独立读取本轮原始参考图，不生成或修改 CAD。必须逐图列出可见区域边界、实面/后退实面/背景开口状态、特征数量和像素证据。feature_groups[].quantity 表示跨工件估计的唯一物理特征数（孔组按唯一物理孔计）；visible_regions[].features[].quantity 表示该可见面上的孔口/特征实例数，一个通孔可在入口和出口面各出现一次，跨面孔口合计可高于物理孔数。有证据支持时，可用逐面特征的 physical_feature_group_id 指向已有全局组；无法确认时省略并记录不确定性。重复特征尽可能记录归一化位置及相对尺寸次序，并把可辨认的局部尺寸/间距与同图基准写成无量纲比例。不要把不同平面合并、不要凭颜色重复计数，也不要猜测不可见的孔深。互斥区域状态会被程序拒绝。",
        {"reference_reading": _REFERENCE_READING_TOOL_SCHEMA}, ["reference_reading"],
    ),
    _tool(
        "cad_create_model",
        "建立本地可编辑模型，包含第一个主实体特征。程序统一使用 mm/deg、校验并立即预览；parameters 放纯数值，sources 在特征对象中单独放来源。"
        "本轮有图时必须先成功调用 cad_inspect_references；按逐面清单和图像比例建模。若外轮廓显示连续顶面/侧壁，应优先建立完整主体，再用切除表达前面凹槽；只有背景确实贯通时才拆成分离薄板。不得把后退实面当成空槽，也不能漏掉后退面上的孔。"
        "坐标约定：X 为 length，Y 为 width，Z 向上；front 相机从 +Y 侧观察，right 相机从 +X 侧观察。",
        {"name": {"type": "string"}, "first_feature": _FEATURE_TOOL_SCHEMA},
        ["name", "first_feature"],
    ),
    _tool(
        "cad_add_feature",
        "只追加一个可编辑特征。sources 必须逐项标记所有数值参数的来源；pattern 的 count 和 step 也要提供来源。"
        "程序合成并校验整份 CADPlan，成功后立即更新右侧预览。"
        "坐标约定：X 为 length，Y 为 width，Z 向上；front 从 +Y 侧观察，right 从 +X 侧观察。"
        "孔的 axis 必须垂直于要打孔的可见面；front 面孔沿 Y，right 面孔沿 X。",
        {"feature": _FEATURE_TOOL_SCHEMA}, ["feature"],
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
        "对当前候选做真实多模态图像复核：每个请求单独包含同视角的用户原图和一张生成的模型单视图。"
        "只选择与原图视角匹配的视图；若视角不匹配，程序会跳过并改用匹配视图，避免用等轴测原图和正交视图硬比。"
        "geometry 检查形状/特征证据；similarity 检查参考图整体对应关系。复核图会在单次检查后清理。",
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
# Initial explicit false plus one same-version confirmation.
NONCOMPARABLE_REFERENCE_REVIEW_CONFIRMATIONS = 2


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
    constraints = reading.get("dimensionless_constraints")
    if not isinstance(constraints, list):
        blockers.append("reference_reading.dimensionless_constraints 必须是数组；无法量化时可为空。")
    else:
        for index, item in enumerate(constraints):
            label = "dimensionless_constraints[%d]" % index
            if not isinstance(item, dict):
                blockers.append(label + " 必须是对象。")
                continue
            ratio = item.get("ratio")
            if (not isinstance(ratio, (int, float)) or isinstance(ratio, bool)
                    or not 0 <= float(ratio) <= 100):
                blockers.append(label + ".ratio 必须是非负且不超过 100 的无量纲图像比例。")
            if not all(str(item.get(key) or "").strip()
                       for key in ("image", "target", "baseline", "evidence")):
                blockers.append(label + " 必须给出图像、目标、基准和像素证据。")
    if final and isinstance(plan, dict):
        target_holes = _reference_hole_quantity(reading)
        if target_holes is not None:
            actual = _planned_hole_quantity(plan)
            if actual != target_holes:
                blockers.append("当前模型有 %d 个可识别的物理孔位，特征组要求 %d 个；"
                                "继续补齐或修正孔组后再复核。"
                                % (actual, target_holes))
    return blockers


def _reference_region_blockers(reading, active_images=None) -> list:
    """Validate a pixel-localized per-plane inventory before any CAD feature is built."""
    if not isinstance(reading, dict):
        return ["缺少参考图可见区域清单。"]
    regions = reading.get("visible_regions")
    if not isinstance(regions, list) or not regions:
        return ["reference_reading.visible_regions 必须逐个列出原图中的可见平面和开口。"]
    known_images = {os.path.basename(str(name)) for name in active_images or []}
    seen = set()
    images_with_regions = set()
    bounded_regions = []
    blockers = []
    feature_groups = reading.get("feature_groups")
    feature_group_rows = feature_groups if isinstance(feature_groups, list) else []
    feature_group_ids = {
        group["id"].strip()
        for group in feature_group_rows
        if isinstance(group, dict)
        and isinstance(group.get("id"), str)
        and group["id"].strip()
    }
    valid_surface_states = {"solid_face", "recessed_solid_face",
                            "background_opening", "unknown"}
    for constraint_index, constraint in enumerate(
            reading.get("dimensionless_constraints") or []):
        if not isinstance(constraint, dict):
            blockers.append("dimensionless_constraints[%d] 必须是对象。" % constraint_index)
            continue
        constraint_image = os.path.basename(str(constraint.get("image") or "").strip())
        if (not constraint_image
                or (known_images and constraint_image not in known_images)):
            blockers.append(
                "dimensionless_constraints[%d].image 必须指向本轮参考图文件名。"
                % constraint_index)
    for index, region in enumerate(regions):
        label = "visible_regions[%d]" % index
        if not isinstance(region, dict):
            blockers.append(label + " 必须是对象。")
            continue
        image = os.path.basename(str(region.get("image") or "").strip())
        region_id = str(region.get("id") or "").strip()
        surface_state = str(region.get("surface_state") or "").strip().lower()
        if not image or (known_images and image not in known_images):
            blockers.append(label + ".image 必须指向本轮参考图文件名。")
        images_with_regions.add(image)
        key = (image, region_id)
        if not region_id or key in seen:
            blockers.append(label + ".id 必须非空且在同一张图内唯一。")
        seen.add(key)
        if surface_state not in valid_surface_states:
            blockers.append(label + ".surface_state 必须区分实面、后退实面、背景开口或未知。")
        evidence = str(region.get("evidence") or "").strip()
        if not evidence:
            blockers.append(label + ".evidence 必须指出支持该区域判断的像素边界。")
        bounds = region.get("image_bbox_norm")
        valid_bounds = (
            isinstance(bounds, (list, tuple)) and len(bounds) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and 0 <= float(value) <= 1 for value in bounds)
            and float(bounds[0]) < float(bounds[2])
            and float(bounds[1]) < float(bounds[3]))
        if not valid_bounds:
            blockers.append(label + ".image_bbox_norm 必须是 [left,top,right,bottom] 的 0-1 区域框。")
        else:
            bounded_regions.append((image, region_id, surface_state,
                                    tuple(float(value) for value in bounds)))
        features = region.get("features")
        if not isinstance(features, list):
            blockers.append(label + ".features 必须是数组，可用空数组表示没有可见特征。")
            continue
        for feature_index, feature in enumerate(features):
            feature_label = "%s.features[%d]" % (label, feature_index)
            if not isinstance(feature, dict):
                blockers.append(feature_label + " 必须是对象。")
                continue
            kind = str(feature.get("kind") or "").strip().lower()
            quantity = feature.get("quantity")
            if not kind:
                blockers.append(feature_label + ".kind 不能为空。")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
                blockers.append(feature_label + ".quantity 必须是正整数。")
                continue
            if "physical_feature_group_id" in feature:
                group_id = feature.get("physical_feature_group_id")
                if (not isinstance(group_id, str) or not group_id.strip()
                        or group_id.strip() not in feature_group_ids):
                    blockers.append(
                        feature_label + ".physical_feature_group_id 必须引用现有的 feature_groups[].id。")
            if ("hole" in kind or "孔" in kind) and surface_state == "background_opening":
                blockers.append(feature_label + " 把孔列在背景开口上；请分开记录后退实体孔面和空隙。")
            if not str(feature.get("arrangement") or "").strip():
                blockers.append(feature_label + ".arrangement 必须说明可见排列。")
            if not str(feature.get("evidence") or "").strip():
                blockers.append(feature_label + ".evidence 必须指出数量/类别的像素证据。")
            centers = feature.get("normalized_centers")
            if centers is not None:
                valid_centers = (
                    isinstance(centers, list)
                    and len(centers) == quantity
                    and all(isinstance(point, (list, tuple)) and len(point) == 2
                            and all(isinstance(value, (int, float))
                                    and not isinstance(value, bool)
                                    and 0 <= float(value) <= 1 for value in point)
                            for point in centers))
                if not valid_centers:
                    blockers.append(
                        feature_label + ".normalized_centers 必须按特征数量给出 0-1 图像坐标。")
            order = feature.get("relative_size_order")
            if order is not None:
                if (not isinstance(order, list) or len(order) != quantity
                        or any(value not in {"large", "medium", "small", "same", "unknown"}
                               for value in order)):
                    blockers.append(
                        feature_label + ".relative_size_order 必须按图像排列为每个特征给一个相对大小标签。")
                elif (len(order) > 1
                      and any(value not in {"unknown", "same"} for value in order)
                      and str(feature.get("size_order_axis") or "unknown")
                      in {"unknown", "not_applicable"}):
                    blockers.append(
                        feature_label + " 给出了相对大小顺序，但没有标明图像排列轴。")
                if not str(feature.get("size_order_confidence") or "").strip():
                    blockers.append(
                        feature_label + " 给出了相对大小顺序，但没有记录判断置信度。")
    # A recessed solid face and a background opening are mutually exclusive
    # descriptions when they cover the same projected image area. Partial
    # overlap is normal in perspective views, so only near-identical boxes are
    # rejected; the reader must split their visible pixel regions or mark the
    # ambiguous area unknown.
    solid_states = {"solid_face", "recessed_solid_face"}
    for index, first in enumerate(bounded_regions):
        image_a, region_a, state_a, bounds_a = first
        for image_b, region_b, state_b, bounds_b in bounded_regions[index + 1:]:
            if image_a != image_b or region_a == region_b:
                continue
            if not ((state_a in solid_states and state_b == "background_opening")
                    or (state_b in solid_states and state_a == "background_opening")):
                continue
            left = max(bounds_a[0], bounds_b[0])
            top = max(bounds_a[1], bounds_b[1])
            right = min(bounds_a[2], bounds_b[2])
            bottom = min(bounds_a[3], bounds_b[3])
            intersection = max(0.0, right - left) * max(0.0, bottom - top)
            area_a = (bounds_a[2] - bounds_a[0]) * (bounds_a[3] - bounds_a[1])
            area_b = (bounds_b[2] - bounds_b[0]) * (bounds_b[3] - bounds_b[1])
            union = area_a + area_b - intersection
            iou = intersection / union if union > 0 else 0.0
            if iou >= 0.90:
                blockers.append(
                    "参考图 %s 的区域 %s（%s）与 %s（%s）区域框几乎重合，"
                    "但一个标为实体面、另一个标为背景开口；请按可见边界拆分两块区域，"
                    "或将无法判定的区域标为 unknown 后再建模。"
                    % (image_a, region_a, state_a, region_b, state_b))

    missing_images = known_images - images_with_regions
    for image in sorted(missing_images):
        blockers.append("参考图 %s 没有任何对应的 visible_regions。" % image)
    return list(dict.fromkeys(blockers))


def _inspect_references(args: dict, state: dict) -> dict:
    if not state.get("has_reference_images"):
        return {"state": "invalid", "error": "当前请求没有可读取的参考图。"}
    reading = args.get("reference_reading")
    blockers = _reference_reading_blockers(reading)
    blockers.extend(_reference_region_blockers(
        reading, state.get("active_reference_images") or []))
    if blockers:
        return {
            "state": "invalid_reference_reading",
            "reference_reading_blockers": list(dict.fromkeys(blockers)),
            "instruction": (
                "请重新查看原始像素，只修正读图清单后再次调用 cad_inspect_references；"
                "逐个可见平面填写 0-1 图像区域框，并逐面统计孔槽。暂时不要生成 CAD 特征。"),
        }
    if reading != state.get("reference_reading"):
        state["reference_reading"] = reading
        state["reference_reading_revision"] = state.get(
            "reference_reading_revision", 0) + 1
    state["reference_reading_source"] = "cad_inspect_references"
    visible_hole_aperture_total = sum(
        int(feature.get("quantity", 0))
        for region in reading.get("visible_regions", [])
        if isinstance(region, dict)
        for feature in region.get("features", [])
        if isinstance(feature, dict)
        and ("hole" in str(feature.get("kind") or "").lower()
             or "孔" in str(feature.get("kind") or "")))
    return {
        "state": "reference_reading_ready",
        "visible_region_count": len(reading.get("visible_regions", [])),
        "visible_hole_aperture_count": visible_hole_aperture_total,
        "reference_reading": reading,
        "next_step_hint": (
            "读图已通过逐区域校验。现在按全局唯一物理特征摘要规划 CAD，并分别核对每个可见面的孔口；"
            "同一通孔可在不同面各有一个孔口，不要把跨面孔口总数当作物理孔数。先建主体，之后逐个添加特征并观察预览；"
            "不要把未知拓扑直接当成空槽。"),
    }


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
            "坐标为右手全局 XYZ：X=box.length，Y=box.width，Z=box.height 且向上；长度默认 mm，角度默认 deg。"
            "本地预览相机约定：front 从 +Y 朝原点看，right 从 +X 朝原点看，iso 从 +X/+Y/+Z 侧看。"
            "因此 front 面孔的 axis 应为 Y，right 面孔的 axis 应为 X。axis 是孔轴方向，"
            "point 是全局 XYZ 坐标，必须落在所选表面或孔轴线上；不要把孔沿着面内方向钻。"
            "例如 box：{\"id\":\"base\",\"kind\":\"box\",\"operation\":\"add\","
            "\"parameters\":{\"length\":40,\"width\":20,\"height\":10},"
            "\"sources\":{\"length\":{\"type\":\"user\"},\"width\":{\"type\":\"user\"},"
            "\"height\":{\"type\":\"user\"}}。不要把数值包进对象或放在 sources.value。"
            "常用 box 参数为 length/width/height/center，单孔为 diameter/through/pos/point/axis；"
            "这些基础参数无需查询 cad_get_feature_spec，只有不熟悉的条件参数或映射时才查询。"
            "box 的 center 是三维位置；corner 是非负倒角标量，不能当位置向量。"
            "完整合法性由每次增量工具中的 SDK 校验，不要重传整个计划。"
            "hole 必须明确写 pos='point'、point=[x,y,z]；将坐标写入 pos 是类型错误。"
            "凹槽若沿 Y 轴从正面切入，不要用仅支持 Z 向的 pocket，应使用 box cut。"
            "不确定条件必需参数或 CATIA 支持状态时调用 cad_get_feature_spec。\n%s\n\n"
            "## 几何语义速查（猜错会得到错误的体积，从而误判自己这一步的对错）\n"
            "- box：center 是**几何中心**，长宽高以它为中心向两侧各一半。\n"
            "- cylinder / cone：base=true 时坐落在 z=0 平面上（center 的 z 不起作用）；"
            "base=false 时以 center 为中心，上下各一半。\n"
            "- prism：profile 是 XY 平面上的闭合多边形，**center 的 z 是拉伸的底面**，"
            "向上拉伸 height —— 不是居中。要切 3mm 深的槽且顶面在 z=36，就取 center z=33。\n"
             "- hole（pos=point）：point 是孔轴线上的全局定位点，axis 必须是目标面的法向；"
             "front 面法向为 Y、right 面法向为 X。through=true 时沿 axis 贯穿整个零件；"
            "diameter 必须大于它要削掉的部位，否则什么都不切。\n"
            "- pocket：沿 Z 向下切，depth 是深度，只能切 Z 向。\n"
            "- pattern：relations 必须用 pattern_source 指向**紧邻的前一个**特征；"
            "operation 必须与源特征一致（源孔 cut → 阵列 cut；不能用 add，否则圆柱刀具会被加成实体）；"
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
        tuple((row.get("image"), row.get("view"), row.get("evidence"),
               row.get("consecutive_explicit_false_reviews"))
              for row in _reference_image_gap_records(state)),
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
        "reference_image_evidence_gaps": _reference_image_gap_records(state),
        "latest_reviews": latest_reviews[-6:],
        "geometry_review_completed": state.get("geometry_review_completed", False),
        "geometry_review_debt": state.get("geometry_review_debt", 0),
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


def _call_model(messages, state, tools, output_tokens, tool_choice=None,
                reasoning_task="modeling"):
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
            model_cfg = llm.agent_reasoning_config(
                model_cfg,
                "high" if reasoning_task in {
                    "reference_inventory", "geometry_review"}
                else "low")
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
        if model_cfg.get("thinking_budget") is not None:
            detail += " · 思考预算 %s" % model_cfg["thinking_budget"]
        cad._emit(state.get("progress"), "模型等待中：" + detail)
        try:
            request_timeout = cad._remaining_timeout(deadline)
            cad._begin_model_call(
                state, request_phase, messages, output_tokens,
                timeout=request_timeout, tool_choice=tool_choice,
                function_tool_names=cad._function_tool_names(tools))
            reply = llm.chat_completion(
                messages, model_cfg, tools=tools, tool_choice=tool_choice,
                timeout=request_timeout, max_tokens=output_tokens,
                cancel_event=state.get("cancel_event"),
                deadline=deadline,
                progress=cad._stream_progress_callback(state, detail))
        except llm.ModelRequestCancelled:
            raise                     # 用户主动停止：绝不重试/降级
        except Exception as exc:
            cad._record_model_call(state, "agent_loop", messages, output_tokens,
                                   error=exc)
            if (tool_choice is not None and not tool_choice_fallback_used
                    and _is_tool_choice_unsupported(exc)):
                tool_choice_fallback_used = True
                if tool_choice == "required":
                    state["tool_choice_required_unsupported"] = True
                    warning = (
                        "模型接口拒绝 tool_choice=required；本轮已回退到自动工具选择，"
                        "并继续保留工具执行与几何验收。")
                else:
                    warning = (
                        "模型接口不支持指定工具名；本次改为仅开放目标工具的自动选择。")
                state["tool_choice_compatibility_warning"] = warning
                tool_choice = None
                cad._emit(
                    state.get("progress"), warning)
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
        state["geometry_review_debt"] = state.get("geometry_review_debt", 0) + 1
        prior = state.get("reviewed_view_status", {})
        if prior:
            state["repair_cycles"] = state.get("repair_cycles", 0) + 1
        state["reviewed_view_status"] = {}
        state["reference_images_reviewed"] = []
        # Keep unresolved findings until a later independent view review
        # confirms each one was repaired against changed geometry.
        state["latest_geometry_review"] = None
        state["latest_similarity_review"] = None
        state["reference_image_evidence_gaps"] = {}
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
    if name == "cad_inspect_references":
        return "独立读取参考图的可见平面、区域位置和特征数量。"
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
    notes = []
    # Snapshot before _set_plan runs: a geometry change clears latest_preview.
    previous_preview = state.get("latest_preview") or {}
    if name == "cad_create_model" and state.get("has_reference_images"):
        blockers = _reference_reading_blockers(state.get("reference_reading"))
        blockers.extend(_reference_region_blockers(
            state.get("reference_reading"), state.get("active_reference_images") or []))
        if state.get("reference_reading_source") != "cad_inspect_references":
            blockers.append("必须先通过 cad_inspect_references 完成独立读图，再创建首个几何特征。")
        if blockers:
            return {"state": "needs_reference_reading",
                    "reference_reading_blockers": list(dict.fromkeys(blockers)),
                    "instruction": "暂不创建几何。先调用 cad_inspect_references，逐图记录视角、连续外壳、后退实体面/背景开口、可见特征数量和比例证据。"}
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


def _reference_image_key(value) -> str:
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _explicit_noncomparable_image_evidence(image_row: dict, view: str):
    """Accept only reviewer evidence for the same source/model view pair."""
    if not isinstance(image_row, dict) or image_row.get("comparable") is not False:
        return None
    source_view = str(image_row.get("source_view") or "unknown").strip().lower()
    view = str(view or "").strip().lower()
    if not view or source_view != view:
        return None
    evidence = str(image_row.get("visible_evidence") or "").strip()
    if not evidence:
        return None
    # These strings are synthesized when the review omitted a comparison or
    # the normalized source/reviewer views conflicted; they are not a reviewer
    # statement that the image itself cannot be compared.
    if (evidence == "本轮没有返回此原图的可比性检查。"
            or ("但本轮复核识别为 " in evidence
                and "视角证据冲突，程序拒绝采用本条比较。" in evidence)
            or (evidence.startswith("原图为 ")
                and "本轮模型视图为 " in evidence
                and "程序不接受跨视角比较。" in evidence)):
        return None
    return evidence


def _reference_image_gap_records(state: dict, terminal_only=False) -> list:
    """Return current-fingerprint non-comparable image gaps, never as passes."""
    active = {_reference_image_key(name)
              for name in state.get("active_reference_images") or []
              if _reference_image_key(name)}
    fingerprint = str(state.get("plan_geometry_fingerprint") or "")
    if not active or not fingerprint:
        return []
    records = state.get("reference_image_evidence_gaps") or {}
    if not isinstance(records, dict):
        return []
    result = []
    for key, record in records.items():
        if not isinstance(record, dict) or key not in active:
            continue
        if str(record.get("geometry_fingerprint") or "") != fingerprint:
            continue
        evidence = str(record.get("evidence") or "").strip()
        try:
            confirmations = int(record.get("consecutive_explicit_false_reviews", 0))
        except (TypeError, ValueError):
            confirmations = 0
        terminal = confirmations >= NONCOMPARABLE_REFERENCE_REVIEW_CONFIRMATIONS
        if not evidence or confirmations < 1 or (terminal_only and not terminal):
            continue
        result.append({
            "image": str(record.get("image") or key),
            "view": str(record.get("view") or "unknown"),
            "evidence": evidence,
            "consecutive_explicit_false_reviews": confirmations,
            "terminal": terminal,
        })
    return sorted(result, key=lambda row: row["image"].casefold())


def _restore_current_geometry_review(state: dict) -> bool:
    """Restore exact-fingerprint review evidence cleared at a text-turn start."""
    fingerprint = str(state.get("plan_geometry_fingerprint") or "")
    if not fingerprint:
        return False
    records = [
        row for row in state.get("review_history", [])
        if isinstance(row, dict)
        and row.get("review_type") == "geometry"
        and str(row.get("geometry_fingerprint") or "") == fingerprint
        and isinstance(row.get("result"), dict)
    ]
    if not records:
        return False

    statuses = {}
    reviewed_images = {}
    saw_report = False
    active = {_reference_image_key(name)
              for name in state.get("active_reference_images") or []
              if _reference_image_key(name)}
    for record in records:
        for report in (record.get("result") or {}).get("single_view_reports") or []:
            if not isinstance(report, dict):
                continue
            saw_report = True
            view = str(report.get("view") or "")
            if view:
                statuses[view] = _view_status(report)
            for image_row in report.get("reference_images") or []:
                if (not isinstance(image_row, dict)
                        or image_row.get("comparable") is not True
                        or not str(image_row.get("visible_evidence") or "").strip()):
                    continue
                image = str(image_row.get("image") or "").strip()
                image_key = _reference_image_key(image)
                if image and (not active or image_key in active):
                    reviewed_images[image_key] = image
    if not saw_report:
        return False

    state["latest_geometry_review"] = records[-1]
    state["reviewed_view_status"] = statuses
    state["reference_images_reviewed"] = sorted(reviewed_images.values())
    covered = set(reviewed_images)
    terminal_gaps = {
        _reference_image_key(row.get("image"))
        for row in _reference_image_gap_records(state, terminal_only=True)
    }
    state["geometry_review_completed"] = not active or active.issubset(covered)
    # Incomplete source coverage remains due unless each gap has independently
    # met the explicit non-comparability handoff threshold.
    # This counter tracks edits since a review, not an automatic review quota.
    # Incomplete image coverage is tracked separately and checked at finish.
    state["geometry_review_debt"] = 0
    return bool(not active or active.issubset(covered | terminal_gaps))


def _reference_image_gap_blockers(state: dict, terminal_only=True) -> list:
    blockers = []
    for record in _reference_image_gap_records(state, terminal_only=terminal_only):
        blockers.append(
            "参考图 %s 在当前几何版本的复核中明确不可与 %s 视图比较（%s）；"
            "此图仍是未解决的证据缺口，不代表视觉通过。请补充可比视角或向用户说明当前候选与缺口。"
            % (record["image"], record["view"], record["evidence"]))
    return blockers


def _feature_box_bounds(feature: dict):
    """Return axis-aligned XYZ bounds for a centered box feature, if available."""
    parameters = feature.get("parameters") or {}
    center = parameters.get("center")
    dimensions = (parameters.get("length"), parameters.get("width"),
                  parameters.get("height"))
    if (not isinstance(center, (list, tuple)) or len(center) != 3
            or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   for value in center)
            or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or float(value) <= 0 for value in dimensions)):
        return None
    return tuple((float(center[index]) - float(dimensions[index]) / 2.0,
                  float(center[index]) + float(dimensions[index]) / 2.0)
                 for index in range(3))


def _full_depth_cut_causes(plan: dict, geometry_checks: dict, hole_checks: list) -> list:
    """Find box cuts that erase the full-depth material needed by failed holes."""
    features = [row for row in (plan or {}).get("features") or []
                if isinstance(row, dict)]
    positions = {str(feature.get("id")): index
                 for index, feature in enumerate(features)}
    hole_specs = {str(row.get("feature_id")): row
                  for row in geometry_checks.get("hole_features_from_plan") or []
                  if isinstance(row, dict)}
    axis_indices = {"X": 0, "Y": 1, "Z": 2}
    causes = {}
    for check in hole_checks:
        hole_id = str(check.get("feature_id") or "")
        hole = hole_specs.get(hole_id)
        if not hole or hole_id not in positions:
            continue
        parameters = hole.get("parameters") or {}
        axis_index = axis_indices.get(str(parameters.get("axis") or "Z").upper())
        point = parameters.get("point")
        if (axis_index is None or not isinstance(point, (list, tuple)) or len(point) != 3
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       for value in point)):
            continue
        point = tuple(float(value) for value in point)
        radius = float(parameters.get("diameter") or 0) / 2.0
        for cut in features[:positions[hole_id]]:
            if cut.get("kind") != "box" or cut.get("operation") != "cut":
                continue
            bounds = _feature_box_bounds(cut)
            if not bounds:
                continue
            cut_position = positions.get(str(cut.get("id")))
            prior_additions = [
                candidate_bounds
                for feature in features[:cut_position]
                if feature.get("operation") == "add" and feature.get("kind") == "box"
                for candidate_bounds in [_feature_box_bounds(feature)]
                if candidate_bounds
            ]
            if not prior_additions:
                continue
            body_bounds = tuple((min(row[axis][0] for row in prior_additions),
                                 max(row[axis][1] for row in prior_additions))
                                for axis in range(3))
            tolerance = 1e-6
            # This cut removes the complete body span along the hole's own axis,
            # and its cross-section covers the failed hole location.
            spans_body = (
                bounds[axis_index][0] <= body_bounds[axis_index][0] + tolerance
                and bounds[axis_index][1] >= body_bounds[axis_index][1] - tolerance)
            covers_hole = all(
                bounds[axis][0] - radius <= point[axis] <= bounds[axis][1] + radius
                for axis in range(3) if axis != axis_index)
            if not (spans_body and covers_hole):
                continue
            cut_id = str(cut.get("id") or "(unnamed cut)")
            axis_name = "XYZ"[axis_index]
            dimension_name = ("length", "width", "height")[axis_index]
            body_depth = body_bounds[axis_index][1] - body_bounds[axis_index][0]
            cut_depth = bounds[axis_index][1] - bounds[axis_index][0]
            side = ("+" if point[axis_index] >=
                    (body_bounds[axis_index][0] + body_bounds[axis_index][1]) / 2.0
                    else "-")
            key = (cut_id, axis_index, side)
            causes.setdefault(key, {
                "holes": [], "axis_name": axis_name, "dimension_name": dimension_name,
                "cut_depth": cut_depth, "body_depth": body_depth,
            })["holes"].append(hole_id)
    diagnoses = []
    for (cut_id, axis_index, side), cause in causes.items():
        axis_name = cause["axis_name"]
        dimension_name = cause["dimension_name"]
        diagnoses.append(
            "贯穿孔 %s 的孔轴位置被切除特征 %s 覆盖：该 box cut 沿 %s 轴贯穿整个主体 "
            "（%s=%.3g mm，主体厚度 %.3g mm），并切掉孔周围承托材料；这不是孔中心靠边。"
            "请把 %s 改为从 %s%s 外表面切入的盲凹槽，缩短该轴切除深度并把中心移到外表面内侧，"
            "保留连续后壁；将这些孔留在凹槽底面，再重新预览。"
            % ("、".join(dict.fromkeys(cause["holes"])), cut_id, axis_name,
               dimension_name, cause["cut_depth"], cause["body_depth"],
               cut_id, axis_name, side))
    return diagnoses


def _review_model(args: dict, state: dict, source_messages: list) -> dict:
    plan = state.get("current_plan")
    preview = state.get("latest_preview")
    if not isinstance(plan, dict):
        return {"state": "blocked", "error": "没有候选 CADPlan。"}
    if (not isinstance(preview, dict)
            or state.get("preview_geometry_fingerprint") != state.get("plan_geometry_fingerprint")):
        return {"state": "blocked", "error": "当前计划尚无对应预览；请先调用 cad_preview_plan。"}
    geometry_checks = preview.get("local_geometry_checks") or {}
    if geometry_checks.get("state") == "failed":
        gate = _finish_gate(state)
        return {
            "state": gate.get("state") or "local_geometry_failed",
            "review_skipped": True,
            "geometry_fingerprint": state.get("plan_geometry_fingerprint"),
            "local_geometry_checks": geometry_checks,
            "blockers": gate.get("blockers") or ["本地几何检查未通过，暂不提交视觉复核。"],
            "next_step_hint": "按本地几何检查定位致错特征并修复，生成新的预览；失败几何不能由视觉复核放行。",
        }
    review_type = str(args.get("review_type") or "geometry")
    phase = "multiview_check" if review_type == "geometry" else "similarity_review"
    if review_type not in {"geometry", "similarity"}:
        return {"state": "invalid", "error": "review_type 必须为 geometry 或 similarity。"}
    requested_views = list(dict.fromkeys(args.get("views") or ["iso"]))
    if not requested_views or any(view not in _VIEWS for view in requested_views):
        return {"state": "invalid", "error": "views 中包含不支持的视图。"}
    review_index = state.get("review_invocations", 0) + 1
    state["review_invocations"] = review_index
    review = cad._split_view_review(
        plan, preview, source_messages, state["model_cfg"], state,
        state.get("cancel_event"), phase, review_index,
        state.get("user_prompt", ""),
        previous_open_findings=state.get("open_multiview_findings", []),
        views=requested_views,
    )
    reviewed_views = list(review.get("reviewed_views") or requested_views)
    record = {
        "review_type": review_type,
        "geometry_fingerprint": state["plan_geometry_fingerprint"],
        "invocation": review_index,
        "views": reviewed_views,
        "requested_views": requested_views,
        "skipped_views": list(review.get("skipped_views") or []),
        "result": review,
    }
    state.setdefault("review_history", []).append(record)
    if review_type == "geometry":
        state["latest_geometry_review"] = record
        statuses = state.setdefault("reviewed_view_status", {})
        image_outcomes = {}
        active_image_keys = {
            _reference_image_key(name)
            for name in state.get("active_reference_images", [])
            if _reference_image_key(name)
        }
        for report in review.get("single_view_reports", []):
            view = report.get("view")
            statuses[view] = _view_status(report)
            reviewed = set(state.get("reference_images_reviewed") or [])
            for image_row in report.get("reference_images") or []:
                if not isinstance(image_row, dict):
                    continue
                image_name = str(image_row.get("image") or "").strip()
                image_key = _reference_image_key(image_name)
                if not image_key or (active_image_keys and image_key not in active_image_keys):
                    continue
                visible_evidence = str(image_row.get("visible_evidence") or "").strip()
                if image_row.get("comparable") is True and visible_evidence:
                    reviewed.add(image_name)
                    image_outcomes[image_key] = (
                        "comparable", image_name, str(view or "unknown"),
                        visible_evidence)
                    continue
                false_evidence = _explicit_noncomparable_image_evidence(
                    image_row, str(view or ""))
                if false_evidence:
                    previous = image_outcomes.get(image_key)
                    if not previous or previous[0] != "comparable":
                        image_outcomes[image_key] = (
                            "noncomparable", image_name, str(view or "unknown"),
                            false_evidence)
                elif image_key not in image_outcomes:
                    image_outcomes[image_key] = (
                        "unresolved", image_name, str(view or "unknown"), "")
            state["reference_images_reviewed"] = sorted(reviewed)
        gap_states = state.setdefault("reference_image_evidence_gaps", {})
        if not isinstance(gap_states, dict):
            gap_states = {}
            state["reference_image_evidence_gaps"] = gap_states
        geometry_fingerprint = str(state.get("plan_geometry_fingerprint") or "")
        for image_key, (outcome, image_name, view, evidence) in image_outcomes.items():
            if outcome == "comparable":
                gap_states.pop(image_key, None)
                continue
            if outcome == "unresolved":
                gap_states.pop(image_key, None)
                continue
            previous = gap_states.get(image_key)
            if (not isinstance(previous, dict)
                    or str(previous.get("geometry_fingerprint") or "")
                    != geometry_fingerprint):
                confirmations = 1
            else:
                try:
                    confirmations = int(previous.get(
                        "consecutive_explicit_false_reviews", 0))
                except (TypeError, ValueError):
                    confirmations = 0
                confirmations = max(1, confirmations + 1)
            gap_states[image_key] = {
                "image": image_name,
                "view": view,
                "evidence": evidence,
                "geometry_fingerprint": geometry_fingerprint,
                "consecutive_explicit_false_reviews": confirmations,
                "last_review_invocation": review_index,
            }
        active_images = {
            _reference_image_key(name)
            for name in state.get("active_reference_images", []) if name
        }
        covered_images = {
            _reference_image_key(name)
            for name in state.get("reference_images_reviewed", []) if name
        }
        reference_coverage_complete = (
            not active_images or active_images.issubset(covered_images))
        state["geometry_review_completed"] = reference_coverage_complete
        state["geometry_review_debt"] = 0
        pending, resolved_ids, _current = cad._update_multiview_findings(
            state.get("open_multiview_findings", []), review, plan, review_index)
        state["open_multiview_findings"] = pending
        if resolved_ids:
            state.setdefault("resolved_multiview_finding_ids", []).extend(resolved_ids)
    else:
        state["latest_similarity_review"] = record
    image_gaps = _reference_image_gap_records(state)
    terminal_image_gaps = [
        row for row in image_gaps if row.get("terminal")]
    actionable_findings = state.get("open_multiview_findings") or []
    if (review_type == "geometry" and terminal_image_gaps
            and not actionable_findings):
        next_step_hint = (
            "当前候选已保留；以下原图在同一几何版本的连续复核中均有具体证据表明无法与所选模型视图比较：%s。"
            "这些图片仍是未解决的证据缺口，不是视觉通过。不要重复同一视角复核；"
            "可调用 ask_user 将当前候选和缺口交给用户，或先请求可比视角。"
            % "；".join("%s：%s" % (row["image"], row["evidence"])
                         for row in terminal_image_gaps))
    elif review.get("decision") == "revise":
        next_step_hint = "有可定位的几何问题时，提交仅修改相关参数的新 CADPlan，再重新预览和复核。"
    else:
        next_step_hint = "检查状态或证据不完整时可选择其它视图再次复核；不能把低置信度当作通过。"
    return {
        "state": review.get("decision"),
        "review_type": review_type,
        "geometry_fingerprint": state["plan_geometry_fingerprint"],
        "reviewed_views": reviewed_views,
        "requested_views": requested_views,
        "skipped_views": list(review.get("skipped_views") or []),
        "reviewed_view_status": dict(state.get("reviewed_view_status", {})),
        "reference_image_evidence_gaps": image_gaps,
        "forced_geometry_review_due": (
            _reference_review_due(state) if review_type == "geometry" else False),
        "local_image_comparisons": review.get("local_image_comparisons", []),
        "checks": review.get("checks", []),
        "issues": review.get("issues", []),
        "questions": review.get("questions", []),
        "review_gaps": review.get("review_gaps", []),
        "confidence": review.get("confidence"),
        "similarity_score": review.get("similarity_score"),
        "similarity_score_semantics": review.get("similarity_score_semantics"),
        "next_step_hint": next_step_hint,
    }


def _reference_review_due(state: dict, final=False) -> bool:
    """Only enforce full source-image coverage at a terminal handoff.

    Mid-build review is model-directed; the program does not interrupt feature
    construction after an arbitrary number of edits. Finish and ask-user gates
    can still require a current preview and account for every source image.
    """
    if not final or not state.get("has_reference_images"):
        return False
    plan = state.get("current_plan")
    preview = state.get("latest_preview")
    fingerprint = state.get("plan_geometry_fingerprint")
    if (not isinstance(plan, dict) or not isinstance(preview, dict)
            or preview.get("state") != "previewed"
            or not fingerprint
            or state.get("preview_geometry_fingerprint") != fingerprint):
        return False
    checks = preview.get("local_geometry_checks") or {}
    if checks.get("state") == "failed":
        return False
    active_images = {
        _reference_image_key(name)
        for name in state.get("active_reference_images", []) if name
    }
    covered_images = {
        _reference_image_key(name)
        for name in state.get("reference_images_reviewed", []) if name
    }
    terminal_gaps = {
        _reference_image_key(row.get("image"))
        for row in _reference_image_gap_records(state, terminal_only=True)
    }
    return bool(active_images - covered_images - terminal_gaps)


def _geometry_review_only_tools(tools: list) -> list:
    """Reserve an overdue checkpoint for one model-chosen geometry review."""
    selected = []
    for tool in tools:
        function = tool.get("function") or {}
        if function.get("name") != "cad_review_model":
            continue
        cloned_function = dict(function)
        parameters = dict(function.get("parameters") or {})
        properties = dict(parameters.get("properties") or {})
        review_type = dict(properties.get("review_type") or {})
        review_type["enum"] = ["geometry"]
        properties["review_type"] = review_type
        views = dict(properties.get("views") or {})
        views["minItems"] = 1
        properties["views"] = views
        parameters["properties"] = properties
        required = list(parameters.get("required") or [])
        if "views" not in required:
            required.append("views")
        parameters["required"] = required
        cloned_function["parameters"] = parameters
        selected.append(dict(tool, function=cloned_function))
    if not selected:
        raise cad.AgentLoopError("AgentLoop 缺少几何复核工具。")
    return selected


def _reference_size_order_blockers(state: dict, plan: dict, review: dict) -> list:
    """Compare a clear source-image hole-size pattern with its planned features."""
    inventory = state.get("independent_reference_inventory") or review.get("reference_inventory")
    groups = cad._independent_inventory_groups(inventory)
    plan_features = {
        str(feature.get("id")): feature
        for feature in plan.get("features", [])
        if isinstance(feature, dict) and feature.get("id")
    }
    checks = [row for row in review.get("checks", []) if isinstance(row, dict)]
    blockers = []
    axis_indexes = {
        "vertical_top_to_bottom": (2, -1),
        "vertical_bottom_to_top": (2, 1),
        "horizontal_left_to_right": (0, 1),
        "horizontal_right_to_left": (0, -1),
    }

    for group in groups:
        expected = group.get("relative_size_order") or []
        confidence = str(group.get("size_order_confidence") or "unknown").lower()
        axis = str(group.get("size_order_axis") or "unknown").lower()
        kind = str(group.get("kind") or "").lower()
        same_size_only = bool(expected) and all(value == "same" for value in expected)
        if (confidence not in {"high", "medium"}
                or len(expected) != int(group.get("quantity", 0) or 0)
                or not any(value in {"large", "medium", "small", "same"}
                           for value in expected)
                or (axis not in axis_indexes and not same_size_only)
                or ("hole" not in kind and "孔" not in kind)):
            continue

        group_id = str(group.get("id") or "")
        check = next((row for row in checks
                      if str(row.get("feature_group_id") or "") == group_id), None)
        feature_ids = (check or {}).get("feature_ids") or []
        if isinstance(feature_ids, str):
            feature_ids = feature_ids.split()
        feature_ids = [str(value) for value in feature_ids]
        holes = [plan_features[value] for value in feature_ids
                 if value in plan_features
                 and str(plan_features[value].get("kind") or "").lower() == "hole"]
        if len(holes) != len(expected):
            blockers.append(
                "原图特征组 %s 清楚显示相对孔径顺序 %s，但当前复核没有映射到同数量的 CADPlan 孔特征；"
                "不能按孔数或几何自检结果直接验收。" % (
                    group_id, "—".join(expected)))
            continue

        coordinate_index, direction = axis_indexes.get(axis, (2, -1))
        try:
            ordered = sorted(
                holes,
                key=lambda feature: direction * float(
                    (feature.get("parameters") or {}).get("point")[coordinate_index]),
            )
            diameters = [float((feature.get("parameters") or {}).get("diameter"))
                         for feature in ordered]
        except (TypeError, ValueError, IndexError, KeyError):
            blockers.append(
                "原图特征组 %s 有清楚的相对孔径顺序，但 CADPlan 缺少可排序的孔坐标或直径参数。"
                % group_id)
            continue

        sorted_indices = sorted(range(len(diameters)), key=lambda index: diameters[index],
                                reverse=True)
        clusters = []
        cluster_for_index = {}
        for index in sorted_indices:
            diameter = diameters[index]
            if (not clusters or abs(diameter - clusters[-1][0])
                    / max(diameter, clusters[-1][0]) > 0.12):
                clusters.append([diameter])
            else:
                clusters[-1].append(diameter)
            cluster_for_index[index] = len(clusters) - 1
        cluster_labels = []
        for index in range(len(clusters)):
            if len(clusters) == 1:
                cluster_labels.append("same")
            elif index == 0:
                cluster_labels.append("large")
            elif index == len(clusters) - 1:
                cluster_labels.append("small")
            else:
                cluster_labels.append("medium")
        actual = [cluster_labels[cluster_for_index[index]]
                  for index in range(len(diameters))]

        mismatches = [
            index for index, target in enumerate(expected)
            if target not in {"unknown", "same"} and actual[index] != target
        ]
        if "same" in expected and not all(value == "same" for value in actual):
            mismatches.extend(index for index, target in enumerate(expected)
                              if target == "same")
        if mismatches:
            ids = [str(feature.get("id")) for feature in ordered]
            blockers.append(
                "原图特征组 %s 的孔径顺序是 %s，CADPlan 按图像排列轴得到 %s（直径 %s mm）；"
                "请只修正这组孔的 parameters.diameter 后再预览复核。受影响特征：%s。" % (
                    group_id, "—".join(expected), "—".join(actual),
                    "、".join("%g" % value for value in diameters),
                    "、".join(ids)))
    return list(dict.fromkeys(blockers))


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
        hole_checks = [row for row in geometry_checks.get("through_hole_mesh_checks") or []
                       if isinstance(row, dict) and row.get("status") != "verified_open"]
        diagnoses = _full_depth_cut_causes(plan, geometry_checks, hole_checks)
        blockers = list(diagnoses)
        clipped = [row.get("feature_id") for row in hole_checks
                   if row.get("status") == "opening_clipped"]
        if not diagnoses and clipped:
            blockers.append("这些孔的孔口被零件边缘切断（孔没有完整落在材料里，"
                            "在视图上就是半圆缺口）：%s。应修正产生边缘相交的孔或承载壁尺寸；"
                            "不要只改视角来回避。" % "、".join(str(name) for name in clipped))
        elif not diagnoses and hole_checks:
            failed = ["%s (%s)" % (row.get("feature_id"), row.get("status"))
                      for row in hole_checks]
            blockers.append("以下贯穿孔没有获得完整材料支撑，不能完成局部几何检查：%s。"
                            "检查孔之前的切除特征是否移除了孔所在面或孔轴上的材料。"
                            % "、".join(failed))
        if not blockers:
            blockers.append("本地网格存在空实体、非有限顶点、退化面或无效包围盒。")
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
        # The builder's first-pass visual reading is a hypothesis, not a source
        # of truth. Exact visible-hole coverage is checked against the separate
        # source-only inventory created during multimodal review.
        reading_blockers = _reference_reading_blockers(reading, plan, final=False)
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
        size_order_blockers = _reference_size_order_blockers(
            state, plan, geometry_review)
        if size_order_blockers:
            return {"ready": False, "state": "relative_feature_size_mismatch",
                    "blockers": size_order_blockers,
                    "validation": validation,
                    "review": geometry_review}
        if geometry_review.get("decision") != "accept":
            review_blockers = list(
                geometry_review.get("review_gaps")
                or geometry_review.get("questions")
                or ["最近一次单视图审查仍有偏差或无法判定的证据。"])
            review_blockers.extend(_reference_image_gap_blockers(state))
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": list(dict.fromkeys(review_blockers)),
                    "validation": validation}
        evidence_blockers = cad._review_evidence_blockers(
            geometry_review, "当前几何版本的多模态审查",
            required_views=geometry_record.get("views"))
        if evidence_blockers:
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": evidence_blockers, "validation": validation}
        reviewed_image_keys = {
            _reference_image_key(name)
            for name in state.get("reference_images_reviewed") or [] if name
        }
        missing_images = sorted(
            name for name in set(state.get("active_reference_images") or [])
            if _reference_image_key(name) not in reviewed_image_keys)
        if missing_images:
            terminal_gaps = {
                _reference_image_key(row.get("image")): row
                for row in _reference_image_gap_records(state, terminal_only=True)
            }
            missing_blockers = []
            for name in missing_images:
                gap = terminal_gaps.get(_reference_image_key(name))
                if gap:
                    missing_blockers.append(
                        "参考图 %s 在当前几何版本的复核中明确不可与 %s 视图比较（%s）；"
                        "此图仍是未解决的证据缺口，不代表视觉通过。请补充可比视角或向用户说明当前候选与缺口。"
                        % (gap["image"], gap["view"], gap["evidence"]))
                else:
                    missing_blockers.append(
                        "参考图 %s 尚未与可比的模型单视图完成核对。" % name)
            return {"ready": False, "state": "needs_visual_review",
                    "blockers": missing_blockers,
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


def _review_current_geometry_before_finish(state: dict, source_messages: list):
    """Close a gap if an agent asks to finish before reviewing the final version."""
    if not state.get("has_reference_images"):
        return None
    plan = state.get("current_plan")
    preview = state.get("latest_preview")
    fingerprint = state.get("plan_geometry_fingerprint")
    review = state.get("latest_geometry_review") or {}
    if (not isinstance(plan, dict) or not isinstance(preview, dict)
            or preview.get("state") != "previewed"
            or not fingerprint
            or state.get("preview_geometry_fingerprint") != fingerprint
            or review.get("geometry_fingerprint") == fingerprint):
        return None
    checks = preview.get("local_geometry_checks") or {}
    if checks.get("state") == "failed":
        return None
    cad._emit(
        state.get("progress"),
        "结束前补做当前几何版本的原图对照；按参考图自动挑选可比的单视图。")
    # The reviewer filters this set against its source-only view inventory and
    # compares only matching images. Unknown source views stay explicit evidence
    # gaps instead of being silently treated as front/right/top views.
    return _review_model(
        {"review_type": "geometry", "views": list(_VIEWS)},
        state, source_messages)


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


def _result_uncertainties(state: dict, assumptions: list, questions=None) -> list:
    """Surface image estimates and unresolved visual evidence in the result."""
    values = []
    reading = state.get("reference_reading") or {}
    for item in reading.get("uncertainties") or []:
        text = str(item or "").strip()
        if text:
            values.append(text)
    for row in (reading.get("dimensionless_constraints") or []):
        if (isinstance(row, dict)
                and str(row.get("confidence") or "").lower() == "low"):
            values.append("图像比例估算置信度较低：%s / %s ≈ %s（%s）" % (
                row.get("target") or "目标", row.get("baseline") or "基准",
                row.get("ratio"), row.get("evidence") or "无进一步像素说明"))
    for row in assumptions:
        values.append("%s.%s = %s mm 为图像估算（%s）" % (
            row.get("feature_id") or "feature", row.get("parameter") or "parameter",
            row.get("value"), row.get("evidence") or row.get("note") or "比例估值"))
    record = state.get("latest_geometry_review") or {}
    review = record.get("result") or {}
    for gap in _reference_image_gap_records(state, terminal_only=True):
        values.append(
            "原图证据缺口（未视为视觉通过）：%s 当前无法与 %s 视图比较（%s）" %
            (gap["image"], gap["view"], gap["evidence"]))
    for item in review.get("review_gaps") or []:
        values.append("视觉复核证据不足：" + str(item))
    for item in review.get("questions") or []:
        values.append("视觉复核未能判定：" + str(item))
    decision = str(review.get("decision") or "").strip().lower()
    if decision and decision != "accept":
        values.append("当前几何复核结论尚未接受：" + decision)
    for check in review.get("checks") or []:
        if isinstance(check, dict) and str(check.get("status") or "").lower() == "unknown":
            values.append("%s %s：%s" % (
                check.get("view") or "视图", check.get("item") or "检查项",
                check.get("unknown_reason") or check.get("evidence") or "图像证据不足"))
    for item in state.get("open_multiview_findings") or []:
        if isinstance(item, dict):
            values.append("未解决视觉差异：%s（%s）" % (
                item.get("item") or item.get("issue_id") or "未命名项",
                item.get("evidence") or "尚无新几何证据确认修复"))
    for item in questions or []:
        text = str(item or "").strip()
        if text:
            values.append("待确认：" + text)
    return list(dict.fromkeys(value[:500] for value in values if value))


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
    geometry_review = state.get("latest_geometry_review") or {}
    current_fingerprint = state.get("plan_geometry_fingerprint")
    preview_fingerprint = state.get("preview_geometry_fingerprint")
    review_fingerprint = geometry_review.get("geometry_fingerprint")
    review_result = geometry_review.get("result") or {}
    validation = state.get("plan_validation") or {}
    local_checks = preview.get("local_geometry_checks") or {}
    geometry_checked = bool(
        isinstance(plan, dict) and validation.get("state") == "valid"
        and isinstance(preview, dict) and preview
        and current_fingerprint and preview_fingerprint == current_fingerprint
        and local_checks.get("state") != "failed"
        and (not state.get("has_reference_images")
             or review_fingerprint == current_fingerprint))
    active_image_keys = {
        _reference_image_key(name)
        for name in state.get("active_reference_images") or [] if name
    }
    reviewed_image_keys = {
        _reference_image_key(name)
        for name in state.get("reference_images_reviewed") or [] if name
    }
    review_accepted = bool(
        geometry_checked and review_result.get("decision") == "accept"
        and not state.get("open_multiview_findings")
        and not _reference_image_gap_records(state)
        and active_image_keys.issubset(reviewed_image_keys)
        and (not active_image_keys or any(
            status == "passed" for status in
            (state.get("reviewed_view_status") or {}).values()))
        and not any(status == "needs_review" for status in
                    (state.get("reviewed_view_status") or {}).values()))
    candidate_requires_verification = bool(
        isinstance(plan, dict)
        and (not preview or preview_fingerprint != current_fingerprint
             or local_checks.get("state") == "failed"
             or state.get("has_reference_images") and not review_accepted))
    uncertainties = _result_uncertainties(state, assumptions, questions)
    if isinstance(plan, dict) and preview:
        size = (preview.get("dimensions") or {}).get("size")
        summary = "\n\n本地模型外形尺寸：%s mm。图像估算参数：%d 项。" % (
            " × ".join("%.2f" % float(value) for value in size)
            if isinstance(size, (list, tuple)) and len(size) == 3 else "待核对",
            len(assumptions))
        if state.get("open_multiview_findings"):
            summary += "仍有 %d 项视觉差异未解决。" % len(
                state["open_multiview_findings"])
        if geometry_checked:
            summary += "当前候选预览%s已对应几何版本 %s。" % (
                "和原图复核" if state.get("has_reference_images") else "",
                str(current_fingerprint)[:12])
        else:
            summary += "当前候选尚未完成最新几何版本的全部检查。"
        if uncertainties:
            shown = uncertainties[:6]
            summary += "\n\n未确定项：\n- " + "\n- ".join(shown)
            if len(uncertainties) > len(shown):
                summary += "\n- 另有 %d 项，详见复核记录。" % (
                    len(uncertainties) - len(shown))
        reply = str(reply or "").rstrip() + summary
    elif isinstance(plan, dict):
        reply = (str(reply or "").rstrip() +
                 "\n\nCADPlan 候选已保留且仍可用；本轮没有可展示的渲染预览，"
                 "因此不能将候选描述为已完成几何或原图核验。")
    warnings = list(preview.get("warnings") or [])
    compatibility_warning = state.get("tool_choice_compatibility_warning")
    if compatibility_warning and compatibility_warning not in warnings:
        warnings.append(compatibility_warning)
        reply = str(reply or "").rstrip() + "\n\n兼容性提示：" + compatibility_warning
    return {
        "state": status.get("state", "previewed"),
        "plan": plan,
        "candidate_available": isinstance(plan, dict),
        "candidate_preview_available": bool(preview),
        "candidate_requires_verification": candidate_requires_verification,
        "validation": status.get("validation") or state.get("plan_validation"),
        "preview": preview,
        "warnings": warnings,
        "dimension_assumptions": assumptions,
        "uncertainties": uncertainties,
        "geometry_evidence": {
            "plan_geometry_fingerprint": current_fingerprint,
            "preview_geometry_fingerprint": preview_fingerprint,
            "review_geometry_fingerprint": review_fingerprint,
            "review_decision": review_result.get("decision"),
            "review_accepted": bool(
                review_accepted),
            "final_geometry_checked": geometry_checked,
        },
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
    if name == "cad_inspect_references":
        return _inspect_references(args, state), False
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
        # Keep a real candidate preview available even when a text-only
        # follow-up starts with the persisted CADPlan but an empty turn-local
        # preview cache.
        candidate_plan = state.get("current_plan")
        preview = state.get("latest_preview")
        current_fp = str(state.get("plan_geometry_fingerprint") or "")
        preview_is_current = (
            isinstance(preview, dict) and preview.get("state") == "previewed"
            and current_fp
            and state.get("preview_geometry_fingerprint") == current_fp)
        preview_error = None
        if isinstance(candidate_plan, dict) and not preview_is_current:
            preview = _preview_plan({}, state)
            if preview.get("state") != "previewed":
                preview_error = str(preview.get("error") or "当前候选预览失败。")
                if state.get("modeling_expected"):
                    return {"state": "blocked", "error": preview_error,
                            "candidate_available": True,
                            "candidate_preview_available": False,
                            "instruction": "先报告候选预览失败原因，再问用户。"}, False
        elif not isinstance(candidate_plan, dict) and state.get("modeling_expected"):
            if not state.get("candidate_attempted"):
                return {"state": "blocked", "instruction": (
                    "用户提供了参考图。先按比例估算并提交 image_estimate CADPlan，生成首轮候选；"
                    "除非主要拓扑确实无法从图像判断，否则暂不提问。")}, False

        # A mutation may auto-preview inside this same tool batch. The turn's
        # forced-review tool list is selected only before the batch, so close
        # that gap here before allowing ask_user to terminate the run.
        if (state.get("has_reference_images")
                and isinstance(candidate_plan, dict)
                and not preview_error
                and ((state.get("latest_preview") or {}).get(
                    "local_geometry_checks") or {}).get("state") != "failed"):
            exact_review = state.get("latest_geometry_review") or {}
            if str(exact_review.get("geometry_fingerprint") or "") != current_fp:
                _restore_current_geometry_review(state)
                exact_review = state.get("latest_geometry_review") or {}
            if str(exact_review.get("geometry_fingerprint") or "") != current_fp:
                _review_current_geometry_before_finish(state, source_messages)
                exact_review = state.get("latest_geometry_review") or {}
            if str(exact_review.get("geometry_fingerprint") or "") != current_fp:
                return {
                    "state": "repair_required",
                    "candidate_available": True,
                    "candidate_preview_available": bool(state.get("latest_preview")),
                    "candidate_requires_verification": True,
                    "instruction": (
                        "当前 CADPlan 候选已保留，但本轮尚未完成同一几何版本的原图复核。"
                        "请先调用 cad_review_model；复核不能完成时，明确把候选标为待核验。"),
                }, False
            if _reference_review_due(state):
                return {
                    "state": "repair_required",
                    "candidate_available": True,
                    "candidate_preview_available": bool(state.get("latest_preview")),
                    "candidate_requires_verification": True,
                    "reference_image_evidence_gaps": _reference_image_gap_records(state),
                    "instruction": (
                        "当前原图覆盖仍有非终态证据缺口；请按复核检查点补齐比较。"
                        "未复核候选不得以 ask_user 结束。"),
                }, False
        actionable_findings = cad._public_findings(
            state.get("open_multiview_findings", []))
        if actionable_findings:
            repair_findings = []
            for finding in actionable_findings:
                affected_parameters = [
                    {"feature_id": str(row.get("feature_id") or ""),
                     "path": str(row.get("path") or "")}
                    for row in finding.get("affected_parameters") or []
                    if isinstance(row, dict) and row.get("feature_id") and row.get("path")
                ]
                repair_findings.append({
                    "finding_id": str(finding.get("issue_id") or ""),
                    "item": str(finding.get("item") or ""),
                    "evidence": str(finding.get("evidence") or ""),
                    "feature_ids": [str(value) for value in
                                    finding.get("feature_ids") or []],
                    "affected_parameters": affected_parameters,
                })

            geometry_version = str(
                state.get("plan_geometry_fingerprint")
                or state.get("plan_revision") or "unknown")
            recovery = state.get("ask_user_recovery")
            if (isinstance(recovery, dict)
                    and recovery.get("geometry_version") == geometry_version):
                finding_labels = []
                for finding in repair_findings:
                    parameters = [
                        "%s.%s" % (row["feature_id"], row["path"])
                        for row in finding["affected_parameters"]]
                    finding_labels.append(
                        "%s %s%s" % (
                            finding["finding_id"], finding["item"],
                            ("（" + "、".join(parameters) + "）") if parameters else ""))
                stop_question = (
                    "同一几何版本在已明确 cad_add_feature 和 cad_update_feature 可用后，"
                    "再次调用 ask_user；已停止重复询问并保留当前候选。"
                    "仍未解决的 finding：" + "；".join(finding_labels) +
                    "。请指出确实无法从当前证据判定的具体内容，或在后续继续修复。")
                state["pending_questions"] = [stop_question]
                state["routing"] = {
                    "action": "clarify", "review_mode": "dynamic",
                    "reason": "同一几何版本重复 ask_user，已停止自动重试。",
                }
                return {
                    "state": "needs_user_input",
                    "question": stop_question,
                    "candidate_available": bool(state.get("current_plan")),
                    "candidate_preview_available": bool(state.get("latest_preview")),
                    "available_edit_tools": ["cad_add_feature", "cad_update_feature"],
                    "unresolved_findings": repair_findings,
                }, True

            state["ask_user_recovery"] = {
                "geometry_version": geometry_version,
            }
            return {
                "state": "repair_required",
                "available_edit_tools": ["cad_add_feature", "cad_update_feature"],
                "unresolved_findings": repair_findings,
                "instruction": (
                    "本请求工具列表包含 cad_add_feature 和 cad_update_feature。"
                    "多模态复核已定位仍可行动的 mismatch；请结合 finding IDs、特征和参数路径继续修复。"
                    "你自行判断需修改已有特征还是补加缺失特征并选择参数；"
                    "不要按固定建模顺序重建模型。必要时先读取当前特征树或参数规范，"
                    "完成修改后再预览并复核。"),
            }, False

        state.pop("ask_user_recovery", None)
        terminal_image_gaps = _reference_image_gap_records(
            state, terminal_only=True)
        if terminal_image_gaps:
            gap_lines = [
                "%s：%s 视图不可比证据：%s" %
                (row["image"], row["view"], row["evidence"])
                for row in terminal_image_gaps
            ]
            question = (
                question.rstrip() +
                "\n\n当前候选已保留，但以下原图仍是未解决的证据缺口，不能视为视觉通过：\n- " +
                "\n- ".join(gap_lines))
        if preview_error:
            question = (
                question.rstrip() +
                "\n\nCADPlan 候选仍可用，但本轮未能生成候选预览，且该候选尚未完成最新几何核验：" +
                preview_error)
        state["pending_questions"] = [question]
        state["routing"] = {"action": "clarify", "review_mode": "dynamic",
                            "reason": "Agent 根据本轮证据请求用户判断。"}
        return {"state": "needs_user_input", "question": question,
                "candidate_available": bool(state.get("current_plan")),
                "candidate_preview_available": bool(state.get("latest_preview")),
                "candidate_requires_verification": bool(
                    isinstance(state.get("current_plan"), dict)
                    and (not state.get("latest_preview")
                         or state.get("preview_geometry_fingerprint")
                         != state.get("plan_geometry_fingerprint")
                         or state.get("has_reference_images")
                         and str((state.get("latest_geometry_review") or {}).get(
                             "geometry_fingerprint") or "")
                         != str(state.get("plan_geometry_fingerprint") or ""))),
                "reference_image_evidence_gaps": terminal_image_gaps}, True
    if name == "cad_finish":
        result = _finish_gate(state)
        checkpoint_review = None
        if result.get("state") == "needs_visual_review":
            checkpoint_review = _review_current_geometry_before_finish(
                state, source_messages)
            if checkpoint_review is not None:
                result = _finish_gate(state)
                result = dict(result)
                result["finish_checkpoint_review"] = checkpoint_review
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


_CAD_MUTATION_TOOLS = {
    "cad_create_model", "cad_add_feature", "cad_update_feature",
    "cad_remove_feature",
}


def _stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _mutation_intent(name: str, args: dict, plan_revision) -> Optional[dict]:
    """Identify the logical edit independent of its changing parameter values."""
    if name not in _CAD_MUTATION_TOOLS:
        return None
    target = {}
    if name in {"cad_create_model", "cad_add_feature"}:
        feature = args.get("first_feature" if name == "cad_create_model" else "feature")
        if isinstance(feature, dict):
            target = {key: feature.get(key) for key in ("id", "kind", "operation")}
        if name == "cad_create_model":
            target["name"] = args.get("name")
    else:
        target["feature_id"] = args.get("feature_id")
    return {"plan_revision": plan_revision or 0, "tool": name, "target": target}


def _deterministic_validation_errors(result: dict) -> Optional[list]:
    """Return stable SDK validation errors; exclude transient/tool exceptions."""
    if not isinstance(result, dict) or result.get("state") != "invalid":
        return None
    validation = result.get("validation")
    if not isinstance(validation, dict) or validation.get("state") != "invalid":
        return None
    errors = validation.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    normalized = []
    for error in errors:
        if not isinstance(error, dict) or not error.get("code") or not error.get("path"):
            return None
        normalized.append({
            "code": str(error["code"]),
            "path": str(error["path"]),
            "message": str(error.get("message") or ""),
        })
    return sorted(normalized, key=lambda row: (row["code"], row["path"], row["message"]))


def _repeated_mutation_failure(previous: dict, repeat_count: int,
                               plan_revision) -> dict:
    return {
        "state": "repeated_failed_action",
        "validation": previous.get("validation"),
        "error": previous.get("error") or "CADPlan 校验未通过。",
        "mutation": previous.get("mutation"),
        "plan_revision": plan_revision or 0,
        "repeat_count": repeat_count,
        "instruction": (
            "此 mutation 在当前 CADPlan 版本中已经因相同校验错误失败。"
            "不要再次提交；本轮已停止重复尝试并保留原候选。"),
    }


def _execute_tool_with_mutation_guard(name: str, args: dict, state: dict,
                                      source_messages: list):
    """Run a mutation once per deterministic failure; stop on the same failure."""
    intent = _mutation_intent(name, args, state.get("plan_revision"))
    if intent is None:
        result, should_stop = _execute_tool(name, args, state, source_messages)
        return result, should_stop, None

    intent_key = _stable_json(intent)
    arguments_key = _stable_json(args)
    history = state.setdefault("_failed_mutations_by_plan", {})
    prior_failures = history.get(intent_key) or []

    # Exact retries are deterministic at an unchanged plan revision. Return the
    # preserved SDK error without invoking plan construction/validation again.
    exact_prior = next((row for row in reversed(prior_failures)
                        if row.get("arguments_key") == arguments_key), None)
    if exact_prior:
        repeated = _repeated_mutation_failure(
            exact_prior, exact_prior.get("repeat_count", 1) + 1,
            state.get("plan_revision"))
        stop = {"mutation": intent, "validation": repeated.get("validation"),
                "repeat_count": repeated["repeat_count"]}
        return repeated, True, stop

    result, should_stop = _execute_tool(name, args, state, source_messages)
    errors = _deterministic_validation_errors(result)
    if errors is None:
        return result, should_stop, None

    error_key = _stable_json(errors)
    previous = next((row for row in reversed(prior_failures)
                     if row.get("error_key") == error_key), None)
    if previous:
        repeat_count = previous.get("repeat_count", 1) + 1
        previous["repeat_count"] = repeat_count
        previous["arguments_key"] = arguments_key
        repeated = _repeated_mutation_failure(
            {"validation": result.get("validation"), "error": result.get("error"),
             "mutation": intent}, repeat_count, state.get("plan_revision"))
        stop = {"mutation": intent, "validation": repeated.get("validation"),
                "repeat_count": repeat_count}
        return repeated, True, stop

    retryable = dict(result)
    retryable["retry_guidance"] = (
        "这是该 mutation 在当前 CADPlan 版本中首次出现的确定性校验错误。"
        "请依据 validation.errors 修正对应字段；不要原样重试。"
        "如果修正后仍是相同校验错误，AgentLoop 会停止重复提交并保留原候选。")
    history.setdefault(intent_key, []).append({
        "arguments_key": arguments_key,
        "error_key": error_key,
        "validation": retryable.get("validation"),
        "error": retryable.get("error"),
        "mutation": intent,
        "repeat_count": 1,
    })
    return retryable, should_stop, None


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
    state["modeling_expected"] = _modeling_expected(prompt, image_paths)
    state["current_plan"] = current_plan if isinstance(current_plan, dict) else None
    state["plan_validation"] = (_validation_result(current_plan)
                                if isinstance(current_plan, dict) else None)
    state["plan_geometry_fingerprint"] = (
        cad._geometry_plan_fingerprint(current_plan)
        if isinstance(current_plan, dict) else None)
    active_image_keys = {
        _reference_image_key(name)
        for name in state.get("active_reference_images", []) if name
    }
    prior_image_gaps = state.get("reference_image_evidence_gaps")
    state["reference_image_evidence_gaps"] = {
        key: row for key, row in (prior_image_gaps.items()
                                  if isinstance(prior_image_gaps, dict) else [])
        if isinstance(row, dict)
        and _reference_image_key(key) in active_image_keys
        and str(row.get("geometry_fingerprint") or "")
        == str(state.get("plan_geometry_fingerprint") or "")
    }
    state["reviewed_view_status"] = {}
    state["reference_images_reviewed"] = []
    state["latest_preview"] = None
    state["preview_geometry_fingerprint"] = None
    state["latest_geometry_review"] = None
    state["latest_similarity_review"] = None
    state["review_invocations"] = 0
    state["geometry_review_completed"] = False
    state["geometry_review_debt"] = 0
    state["plan_submissions"] = 0
    state["plan_revision"] = 1 if isinstance(current_plan, dict) else 0
    state["web_results"] = {}
    state["context_compactions"] = 0
    state["agent_started"] = False
    state["candidate_attempted"] = isinstance(current_plan, dict)
    state["completion_approved"] = False
    state["stagnant_tool_calls"] = 0
    state["stagnation_warnings"] = 0
    state["_failed_mutations_by_plan"] = {}
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
    initial_model_bootstrap = _requires_initial_model_creation(
        prompt, image_paths, current_plan)
    output_tokens = MAX_OUTPUT_TOKENS
    no_progress_finals = 0
    consecutive_no_tool_responses = 0
    state["no_progress_finals"] = 0
    state["consecutive_no_tool_responses"] = 0
    state["truncated_responses"] = 0

    for turn_index in range(1, MAX_AGENT_TURNS + 1):
        cad._check_cancel(cancel_event, state["deadline"])
        state["agent_turn"] = turn_index
        state["request_phase"] = "主循环第 %d 轮" % turn_index
        cad._emit(state.get("progress"), "准备 AgentLoop 第 %d 轮：整理当前证据…" % turn_index)
        require_reference_inspection = bool(
            initial_model_bootstrap
            and state.get("has_reference_images")
            and not state.get("reference_reading"))
        require_create_model = bool(
            initial_model_bootstrap
            and not state.get("candidate_attempted")
            and not require_reference_inspection)
        require_geometry_review = bool(
            not require_reference_inspection
            and not require_create_model
            and _reference_review_due(state))
        if require_reference_inspection:
            call_tools = _initial_reference_inspection_tool(tools)
        elif require_create_model:
            call_tools = _initial_creation_tools(
                tools, bool(state.get("has_reference_images")))
        elif require_geometry_review:
            call_tools = _geometry_review_only_tools(tools)
            cad._emit(
                state.get("progress"),
                "原图复核检查点已到；本轮只安排几何复核，视图由模型按参考图选择。")
        else:
            call_tools = tools
        action_tool_expected = bool(
            state.get("modeling_expected")
            and (isinstance(state.get("current_plan"), dict)
                 or state.get("agent_started")))
        require_action_tool = bool(
            action_tool_expected
            and not state.get("tool_choice_required_unsupported"))
        reply = _call_model(
            messages, state, call_tools, output_tokens,
            reasoning_task=(
                "reference_inventory" if require_reference_inspection else
                "geometry_review" if require_geometry_review else "modeling"),
            tool_choice=(
                {"type": "function", "function": {"name": "cad_inspect_references"}}
                if require_reference_inspection else
                {"type": "function", "function": {"name": "cad_create_model"}}
                if require_create_model else
                {"type": "function", "function": {"name": "cad_review_model"}}
                if require_geometry_review else
                "required" if require_action_tool else None))
        if not state.get("reference_reading") and not require_reference_inspection:
            _capture_reference_reading(state, cad._message_text(reply))
        _compact_main_reference_images(messages, state)
        calls = reply.get("tool_calls") or []
        if require_reference_inspection and not any(
                str(((call.get("function") or {}).get("name") or ""))
                == "cad_inspect_references" for call in calls):
            raise cad.AgentLoopError(
                "带图建模首先需要独立读图，但当前模型没有调用 cad_inspect_references。")
        if require_create_model and not any(
                str(((call.get("function") or {}).get("name") or ""))
                == "cad_create_model" for call in calls):
            raise cad.AgentLoopError(
                "新建模请求已指定 cad_create_model，"
                "但当前模型仍未返回该工具调用；请确认该模型支持 function calling 后重试。")
        if require_geometry_review and not any(
                str(((call.get("function") or {}).get("name") or ""))
                == "cad_review_model" for call in calls):
            raise cad.AgentLoopError(
                "参考图几何复核债务已到期，但模型没有提交 cad_review_model；"
                "当前候选已保留，尚未完成原图对照。")
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
            if action_tool_expected:
                consecutive_no_tool_responses += 1
                state["consecutive_no_tool_responses"] = (
                    consecutive_no_tool_responses)
                assistant_message = {"role": "assistant", "content": content}
                if reply.get("reasoning_content") is not None:
                    assistant_message["reasoning_content"] = reply["reasoning_content"]
                messages.append(assistant_message)
                gate = _finish_gate(state)
                blockers = gate.get("blockers") or [
                    "当前建模步骤尚未由 CAD 工具完成。"]
                if consecutive_no_tool_responses >= MAX_CONSECUTIVE_NO_TOOL_RESPONSES:
                    status = {
                        "state": "previewed" if state.get("latest_preview")
                        else "needs_user_input",
                        "validation": state.get("plan_validation"),
                    }
                    final_text = (
                        "模型连续两次没有调用 CAD 工具，已停止重试并保留当前候选。\n"
                        "未完成项：\n- " +
                        "\n- ".join(str(item) for item in blockers[:8]))
                    return _result_from_state(
                        status, state, final_text, blockers)
                messages.append({"role": "user", "content": (
                    "上一条回复没有调用 CAD 工具。请现在调用一个最合适的工具"
                    "推进当前步骤；只给必要参数，不要重复总结。")})
                cad._emit(
                    state.get("progress"),
                    "模型本轮没有调用工具；已要求它用一个 CAD 工具继续。")
                continue
            if not state.get("agent_started"):
                if state.get("modeling_expected"):
                    no_progress_finals += 1
                    state["no_progress_finals"] = no_progress_finals
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
            state["no_progress_finals"] = no_progress_finals
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
        repeated_mutation_stop = None
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
                consecutive_no_tool_responses = 0
                state["consecutive_no_tool_responses"] = 0
                cad._emit(
                    state.get("progress"),
                    "Agent 决策摘要：" + _tool_progress_summary(name, args))
                result, should_stop, repeated_mutation_stop = (
                    _execute_tool_with_mutation_guard(
                        name, args, state, source_messages))
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
            cad._checkpoint_run_record(state)
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "name": name, "content": json.dumps(
                                 result, ensure_ascii=False, separators=(",", ":"))})
            if name == "cad_inspect_references" and result.get("state") == "reference_reading_ready":
                _compact_main_reference_images(messages, state)
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
                state["no_progress_finals"] = 0
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
            if repeated_mutation_stop:
                break
        if repeated_mutation_stop:
            errors = _deterministic_validation_errors({
                "state": "invalid",
                "validation": repeated_mutation_stop.get("validation"),
            }) or []
            blockers = ["%s %s: %s" % (
                row.get("code"), row.get("path"), row.get("message"))
                for row in errors]
            mutation = repeated_mutation_stop.get("mutation") or {}
            target = (mutation.get("target") or {}).get("id") or (
                (mutation.get("target") or {}).get("feature_id")) or "模型"
            reply_text = (
                "同一 CADPlan 版本中，对特征 %s 的 %s 操作再次触发相同的确定性校验错误。"
                "AgentLoop 已停止重复提交；当前候选保持不变并已保留。\n校验错误：\n- %s"
                % (target, mutation.get("tool") or "CAD", "\n- ".join(blockers)))
            status = {"state": "previewed" if state.get("latest_preview")
                      else "needs_user_input", "validation": state.get("plan_validation")}
            return _result_from_state(
                status, state, reply_text, blockers or [
                    "重复 mutation 在当前 CADPlan 版本中持续失败；原候选已保留。"])
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
