# -*- coding: utf-8 -*-
"""Versioned, provider-neutral CADPlan SDK for the CATIA V5 bridge.

Plans contain data only. This module never evaluates Python or CATScript supplied
by a plan or by an LLM.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ai_modeler.core.model.docmodel import ADD, CUT, Document, Feature, KIND_LABELS
from ai_modeler.paths import temp_path

CADPLAN_SCHEMA_VERSION = "1.0"
LENGTH_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
ANGLE_UNITS = {"deg": 1.0, "rad": 180.0 / math.pi}
THREE_VIEW_TILE_PX = (330, 260)
THREE_VIEW_MARGIN = 0.09


def _orthographic_layout(size: Any) -> Dict[str, Any]:
    """Describe the axis dimensions and shared display scale of the three views."""
    if not isinstance(size, (list, tuple)) or len(size) != 3:
        return {}
    length, width, height = (float(value) for value in size)
    max_extent = max(length, width, height)
    scale = ((1.0 - 2.0 * THREE_VIEW_MARGIN) *
             min(THREE_VIEW_TILE_PX) / max_extent) if max_extent > 0 else None
    return {
        "unit": "mm",
        "axes": {"length": "X", "width": "Y", "height": "Z"},
        "uniform_scale_across_views": True,
        "scale_pixels_per_mm": scale,
        "tile_size_pixels": list(THREE_VIEW_TILE_PX),
        "view_extents": {
            "front": {"horizontal_axis": "X", "horizontal": length,
                      "vertical_axis": "Z", "vertical": height},
            "right": {"horizontal_axis": "Y", "horizontal": width,
                      "vertical_axis": "Z", "vertical": height},
            "top": {"horizontal_axis": "X", "horizontal": length,
                    "vertical_axis": "Y", "vertical": width},
        },
        "dimension_ratios": {
            "length_to_height": length / height if height else None,
            "width_to_height": width / height if height else None,
            "length_to_width": length / width if width else None,
        },
    }


def _spec(required, status, reason, params=None):
    return {
        "required": tuple(required),
        "params": tuple(params or required),
        "status": status,
        "reason": reason,
    }


# This registry is the maintained contract between CADPlan and Document.
# Required parameters are the geometry-defining values; tessellation controls
# remain optional because they do not change the nominal shape.
FEATURE_SPECS = {
    "box": _spec(("length", "width", "height"), "exact",
                 "矩形草图加凸台；当前 CATIA 映射仅支持 Z 轴拉伸。",
                 ("length", "width", "height", "center", "axis", "base", "corner", "corner_mode")),
    "cylinder": _spec(("radius", "height"), "exact",
                      "center 默认表示轴向底面中心(base=true)，height 沿 axis 正向拉伸；"
                      "base=false 时 center 表示几何中点。等半径时为圆草图加凸台；"
                      "圆锥台会近似，非 Z 轴圆锥台暂不支持。",
                      ("radius", "radius2", "height", "center", "axis", "base", "segments")),
    "socket_head_bolt": _spec(
        ("shank_d", "thread_pitch", "length", "head_d", "head_h", "hex_s", "hex_t"), "approximate",
        "本地网格按 thread_pitch 生成可见的近似 ISO 公制螺旋牙型；整段杆部带牙，"
        "未建牙端倒角/退刀槽。当前 CATIA 导出器仍以光杆凸台和六角凹槽表达，"
        "不会创建原生或装饰螺纹。",
        ("shank_d", "thread_pitch", "length", "head_d", "head_h", "hex_s", "hex_t",
         "center", "segments")),
    "sphere": _spec(("radius",), "approximate",
                    "CATIA 导出器以多层圆盘近似球体。",
                    ("radius", "center", "segments", "rings")),
    "prism": _spec(("profile", "height"), "exact",
                   "闭合多边形草图加凸台；当前仅支持 Z 轴。",
                   ("profile", "height", "center", "axis")),
    "revolve": _spec(("profile", "angle"), "approximate",
                     "本地 OCCT BREP 按闭合[半径,轴向坐标]轮廓精确回转，并将新增回转体与已有实体做布尔合并；"
                     "CATIA 导出器仍用多层圆盘近似，非 CATIA 原生回转特征。",
                     ("profile", "angle", "center", "axis", "segments")),
    "gear": _spec(("teeth", "module", "height"), "exact",
                  "齿廓转换为多边形草图并拉伸；当前仅支持 Z 轴。",
                  ("teeth", "module", "height", "center", "axis", "base")),
    "loft": _spec(("root_chord", "tip_chord", "span", "thickness"), "approximate",
                  "CATIA 中以根部截面拉伸近似多截面放样。",
                  ("root_chord", "tip_chord", "span", "thickness", "camber", "camber_pos",
                   "twist", "sweep", "sections", "points", "rotate", "translate")),
    "hole": _spec(("diameter", "through", "pos"), "exact",
                  "圆形切除；through=true 时按当前零件包围盒生成贯穿深度。盲孔可用 entry_point 与 direction 显式指定入口点和沿全局轴的切入方向；未指定时沿用正轴端面默认语义。",
                  ("diameter", "through", "pos", "point", "x", "y", "count", "pcd",
                   "phase", "depth", "margin", "axis", "segments", "entry_point",
                   "direction")),
    "pocket": _spec(("length", "width", "depth"), "exact",
                    "矩形/圆角矩形凹槽；当前仅支持 Z 轴。",
                    ("length", "width", "depth", "x", "y", "axis", "side", "corner", "corner_mode")),
    "pattern": _spec(("mode", "count"), "exact",
                     "转换为显式复制的 CATIA 操作；relations 示例："
                     "[{\"type\":\"pattern_source\",\"feature_id\":\"紧邻前一特征的 id\"}]。"
                     "operation 必须与源特征相同：阵列切孔用 cut，阵列加料用 add。",
                     ("mode", "count", "step", "axis", "angle", "full", "center")),
    "helix": _spec(("radius", "wire_r", "height", "turns"), "unsupported",
                   "弹簧/螺旋不能由当前草图、凸台和凹槽通道表达。",
                   ("radius", "wire_r", "height", "turns", "center", "axis", "taper",
                    "seg_per_turn", "wire_seg", "taper_start")),
    "spoked_wheel": _spec(("outer_r", "rim_w", "hub_r", "height", "spokes", "spoke_w"), "approximate",
                          "CATIA 仅生成轮缘与轮毂，辐条需要补建。",
                          ("outer_r", "rim_w", "hub_r", "height", "spokes", "spoke_w",
                           "center", "segments")),
    "vbelt_pulley": _spec(("pitch_d", "grooves", "height", "groove_type"), "approximate",
                          "CATIA 生成光轮盘体；V 形回转槽需要在 CATIA 中补建。",
                          ("pitch_d", "grooves", "height", "groove_type", "outer_d",
                           "groove_depth", "bore_d", "center", "segments")),
    "timing_pulley": _spec(("pitch_d", "teeth", "height", "tooth_type"), "approximate",
                           "CATIA 生成盘体，齿形需要在 CATIA 中补建。",
                           ("pitch_d", "teeth", "height", "tooth_type", "bore_d",
                            "center", "segments")),
    "worm": _spec(("radius", "height", "thread_r", "wire_r", "turns"), "approximate",
                  "CATIA 仅生成光轴，螺旋齿需要补建。",
                  ("radius", "height", "center", "center2", "thread_r", "wire_r", "axis",
                   "height2", "turns", "segments", "seg_per_turn", "wire_seg")),
    "helical_gear": _spec(("teeth", "module", "height"), "approximate",
                          "CATIA 以直齿轮近似，省略螺旋角。",
                          ("teeth", "module", "height", "helix_deg", "bore_d",
                           "center", "segments")),
    "bevel_gear": _spec(("teeth", "module", "height", "cone_deg"), "approximate",
                        "CATIA 以直齿圆柱轮廓近似，省略锥面。",
                        ("teeth", "module", "height", "cone_deg", "bore_d",
                         "center", "segments")),
    "crankshaft": _spec(("length", "main_d", "pin_d", "stroke", "throws"), "approximate",
                        "CATIA 按 X 轴布置主轴颈、交替偏置连杆颈和哑铃形曲柄臂"
                        "(主轴圆 + 连杆颈圆外公切凸包)，曲柄臂外形被简化。",
                        ("length", "main_d", "pin_d", "stroke", "throws", "center", "segments")),
    "camshaft": _spec(("length", "journal_d", "base_d", "lift", "lobes", "lobe_deg"), "approximate",
                      "CATIA 凸轮沿轴向等分布置，相位需核对。",
                      ("length", "journal_d", "base_d", "lift", "lobes",
                       "lobe_deg", "center", "segments")),
    "impeller": _spec(("hub_d", "tip_d", "height", "blades", "blade_t", "twist_deg"),
                      "approximate", "CATIA 仅生成轮毂和轮盘，扭转叶片需要补建。",
                      ("hub_d", "tip_d", "height", "blades", "blade_t",
                       "twist_deg", "center", "segments")),
    "piston": _spec(("diameter", "height", "pin_d", "grooves"), "approximate",
                    "CATIA 以圆柱体近似，销孔和活塞环槽需要补建。",
                    ("diameter", "height", "pin_d", "grooves", "center", "segments")),
    "conrod": _spec(("length", "big_d", "small_d", "big_bore", "small_bore",
                     "shank_w", "thick"), "approximate",
                    "CATIA 以大头、小头和直杆身近似，杆身过渡被简化。",
                    ("length", "big_d", "small_d", "big_bore", "small_bore",
                     "shank_w", "thick", "center", "segments")),
    "cross_joint": _spec(("span", "journal_d"), "approximate",
                         "CATIA 仅生成单轴，另一根横轴需要补建。",
                         ("span", "journal_d", "center", "segments")),
    "scroll_disc": _spec(("outer_d", "height", "wall_t", "turns"), "approximate",
                         "CATIA 仅生成底盘，螺旋壁需要补建。",
                         ("outer_d", "height", "wall_t", "turns", "center", "segments")),
    "rotor": _spec(("radius", "n", "height", "bore_d"), "exact",
                   "转子轮廓、厚度与中心孔映射为可编辑草图和凹槽。",
                   ("radius", "n", "height", "bore_d", "center")),
    # Document recognizes this label, but Feature.build currently emits no shell
    # geometry and the CATIA exporter has no shell branch.
    "shell": _spec(("thickness",), "unsupported",
                   "当前 Document/CATIA 转换器没有抽壳实现。",
                   ("thickness",)),
}

EXACT_KINDS = {k for k, s in FEATURE_SPECS.items() if s["status"] == "exact"}
APPROXIMATE_KINDS = {k for k, s in FEATURE_SPECS.items() if s["status"] == "approximate"}
UNSUPPORTED_KINDS = {k for k, s in FEATURE_SPECS.items() if s["status"] == "unsupported"}

_LENGTH_KEYS = {
    "length", "width", "height", "radius", "radius2", "corner", "wire_r", "sec_a",
    "sec_b", "base", "margin", "diameter", "depth", "pcd", "outer_r", "rim_w",
    "hub_r", "pitch_d", "bore_d", "thread_r", "height2", "pin_d", "stroke",
    "main_d", "journal_d", "base_d", "lift", "hub_d", "tip_d", "big_d",
    "small_d", "big_bore", "small_bore", "shank_w", "thick", "span",
    "shank_d", "thread_pitch", "head_d", "head_h", "hex_s", "hex_t",
    "root_chord", "tip_chord", "sweep", "groove_depth", "outer_d", "wall_t", "thickness",
    "module", "spoke_w", "blade_t", "x", "y", "z",
}
_LENGTH_ARRAY_KEYS = {"center", "center2", "translate", "step", "point", "entry_point", "profile"}
_ANGLE_KEYS = {"angle", "phase", "twist", "twist_deg", "helix_deg", "cone_deg", "lobe_deg"}
_DIMENSIONLESS_KEYS = {"turns", "taper", "taper_start", "camber", "camber_pos"}
_NONNEGATIVE_KEYS = {"corner", "bore_d", "groove_depth", "sweep", "lift"}
_POSITIVE_KEYS = set(_LENGTH_KEYS) - {"x", "y", "z", "base"} - _NONNEGATIVE_KEYS
_INTEGER_KEYS = {"teeth", "count", "grooves", "spokes", "blades", "lobes", "n", "throws",
                 "segments", "rings", "sections", "points", "seg_per_turn", "wire_seg"}
_BOOLEAN_KEYS = {"base", "through", "full"}
_NUMERIC_SCALAR_KEYS = ((_LENGTH_KEYS | _ANGLE_KEYS | _DIMENSIONLESS_KEYS | _INTEGER_KEYS)
                        - _BOOLEAN_KEYS)
_STRING_KEYS = {"axis", "corner_mode", "mode", "pos", "groove_type", "tooth_type",
                "direction", "offset_mode", "side"}
_ENUMS = {
    "axis": {"X", "Y", "Z"},
    "corner_mode": {"fillet", "chamfer"},
    "mode": {"linear", "circular"},
    "pos": {"center", "corners", "pcd", "point"},
    "groove_type": {"Y", "Z", "A", "B", "C", "D", "E", "SPZ", "SPA", "SPB", "SPC"},
    "tooth_type": {"MXL", "XL", "L", "H", "XH", "XXH", "T2.5", "T5", "T10",
                   "T20", "3M", "5M", "8M", "14M"},
    "side": {"positive", "negative"},
    "direction": {"positive", "negative"},
}


def support_matrix() -> Dict[str, Dict[str, str]]:
    """Return the public feature coverage table used by docs and agents."""
    return {
        kind: {
            "status": spec["status"],
            "reason": spec["reason"],
            "required_parameters": list(spec["required"]),
        }
        for kind, spec in FEATURE_SPECS.items()
    }


def geometry_backend_support_matrix() -> Dict[str, Dict[str, str]]:
    """Describe local geometry execution separately from CATIA translation."""
    from ai_modeler.core.geometry import occ_kernel
    return {
        kind: {"status": status, "reason": reason,
               "backend": "OCCT BREP" if occ_kernel.available() else "unavailable",
               "runtime_available": str(bool(occ_kernel.available())).lower()}
        for kind, (status, reason) in occ_kernel.GEOMETRY_SUPPORT.items()
        if kind in FEATURE_SPECS
    }


def _geometry_backend_status(feature: Mapping[str, Any]) -> Dict[str, str]:
    from ai_modeler.core.geometry import occ_kernel
    kind = str(feature.get("kind") or "")
    params = feature.get("parameters") or {}
    status, reason = occ_kernel.GEOMETRY_SUPPORT.get(
        kind, ("unavailable", "此特征没有本地 BREP 执行映射。"))
    if kind == "box" and float(params.get("corner", 0.0) or 0.0) > 0:
        status, reason = "approximate", "圆角/倒角来自离散轮廓，主体由 OCCT 棱柱生成。"
    if kind == "pocket" and float(params.get("corner", 0.0) or 0.0) > 0:
        status, reason = "approximate", "圆角/倒角轮廓已离散后执行 OCCT BREP 切除。"
    if kind == "pattern" and not any(
            row.get("type") == "pattern_source" for row in feature.get("relations", [])
            if isinstance(row, dict)):
        status, reason = "unavailable", "CADPlan pattern 必须声明唯一 pattern_source 关系。"
    if not occ_kernel.available():
        status, reason = "unavailable", "运行桌面程序的 Python 未安装 OpenCASCADE BREP 依赖。"
    return {"feature_id": str(feature.get("id", "")), "kind": kind,
            "status": status, "reason": reason, "backend": "occt_brep"}


def _feature_status(feature: Mapping[str, Any]) -> str:
    kind = feature.get("kind")
    params = feature.get("parameters") or {}
    spec = FEATURE_SPECS.get(kind)
    if not spec:
        return "unsupported"
    if spec["status"] != "exact":
        axis = str(params.get("axis", "Z")).upper()
        if kind in {"revolve", "loft"} and axis != "Z":
            return "unsupported"
        return spec["status"]
    axis = str(params.get("axis", "Z")).upper()
    if kind in {"box", "prism", "gear", "pocket", "pattern"} and axis != "Z":
        return "unsupported"
    if kind == "pocket" and str(params.get("side", "positive")).lower() == "negative":
        return "unsupported"
    if kind == "pattern" and params.get("mode") == "circular" and axis != "Z":
        return "unsupported"
    if kind == "cylinder":
        r2 = params.get("radius2", params.get("radius"))
        if r2 is not None and params.get("radius") is not None:
            try:
                if abs(float(r2) - float(params["radius"])) > 1e-9:
                    return "approximate" if axis == "Z" else "unsupported"
            except (TypeError, ValueError):
                pass
    return spec["status"]


def _feature_reason(feature: Mapping[str, Any], status: str) -> str:
    kind = feature.get("kind")
    params = feature.get("parameters") or {}
    axis = str(params.get("axis", "Z")).upper()
    base_status = FEATURE_SPECS[kind]["status"]
    if status == "unsupported" and kind not in {"helix", "shell"} and axis != "Z":
        return "%s 轴上的该特征当前不能由 CATIA 转换器可靠表达。" % axis
    if kind == "pocket" and str(params.get("side", "positive")).lower() == "negative":
        return "本地 BREP 支持从负轴面切槽；当前 CATIA 转换器只表达正轴面凹槽。"
    if kind == "cylinder" and status == "approximate":
        return "圆锥台以多层圆盘近似。"
    if kind == "cylinder" and status == "unsupported":
        return "%s 轴上的圆锥台当前不支持。" % axis
    if kind == "pattern" and status == "unsupported":
        return "当前 CATIA 显式阵列只支持 Z 轴阵列。"
    if status == base_status:
        return FEATURE_SPECS[kind]["reason"]
    return FEATURE_SPECS[kind]["reason"]


def _contains_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, (list, tuple)):
        return any(_contains_number(v) for v in value)
    if isinstance(value, dict):
        return any(_contains_number(v) for v in value.values())
    return False


def _walk_numbers(value: Any, path: str, errors: List[Dict[str, str]]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            errors.append({"code": "non_finite_number", "path": path,
                           "message": "数值必须是有限数。"})
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _walk_numbers(item, "%s[%d]" % (path, i), errors)
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_numbers(item, "%s.%s" % (path, key), errors)
    elif value is None or isinstance(value, (str, bool)):
        return
    else:
        errors.append({"code": "invalid_value_type", "path": path,
                       "message": "参数只能包含 JSON 标量、数组或对象。"})


def _valid_vec(value: Any, length: int) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == length
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(float(v)) for v in value))


def _conditional_required(kind: str, params: Mapping[str, Any]) -> List[str]:
    required = list(FEATURE_SPECS[kind]["required"])
    if kind == "hole":
        pos = params.get("pos")
        if pos == "point":
            required.append("point")
        elif pos == "pcd":
            required.extend(("pcd", "count"))
        elif pos == "corners":
            pass
        elif pos == "center":
            pass
        else:
            required.append("pos")
        if params.get("through") is False:
            required.append("depth")
    elif kind == "pattern":
        if params.get("mode") == "linear":
            required.append("step")
        elif params.get("mode") == "circular":
            required.extend(("axis", "angle", "center"))
        else:
            required.append("mode")
    return list(dict.fromkeys(required))


def _read_plan(plan: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if isinstance(plan, Mapping):
        return copy.deepcopy(dict(plan)), None
    try:
        with open(os.fspath(plan), "r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "CADPlan 顶层必须是 JSON 对象。"
    return value, None


def validate_plan(plan: Any) -> Dict[str, Any]:
    """Validate a CADPlan mapping or JSON path without invoking CATIA."""
    data, load_error = _read_plan(plan)
    if load_error:
        return {"valid": False, "state": "invalid",
                "errors": [{"code": "plan_load_error", "path": "$", "message": load_error}],
                "confirmations": [], "support": [], "warnings": []}

    errors: List[Dict[str, str]] = []
    confirmations: List[Dict[str, str]] = []
    warnings: List[Dict[str, Any]] = []
    geometry_support: List[Dict[str, str]] = []
    allowed_top = {"schema_version", "name", "source_prompt", "units",
                   "coordinate_system", "features", "pending_questions"}
    for key in data:
        if key not in allowed_top:
            errors.append({"code": "unknown_field", "path": "$." + str(key),
                           "message": "CADPlan 不支持此字段。"})
    if data.get("schema_version") != CADPLAN_SCHEMA_VERSION:
        errors.append({"code": "unsupported_schema_version", "path": "$.schema_version",
                       "message": "只支持 schema_version=1.0。"})
    if not isinstance(data.get("name"), str) or not data.get("name", "").strip():
        errors.append({"code": "missing_name", "path": "$.name",
                       "message": "必须提供非空模型名称。"})
    if "source_prompt" in data and not isinstance(data["source_prompt"], str):
        errors.append({"code": "invalid_source_prompt", "path": "$.source_prompt",
                       "message": "source_prompt 必须为字符串。"})

    units = data.get("units")
    if not isinstance(units, dict):
        errors.append({"code": "missing_units", "path": "$.units",
                       "message": "必须声明长度和角度单位。"})
        units = {}
    else:
        for key in units:
            if key not in {"length", "angle"}:
                errors.append({"code": "unknown_field", "path": "$.units." + str(key),
                               "message": "单位对象只接受 length 和 angle。"})
    length_unit, angle_unit = units.get("length"), units.get("angle")
    if length_unit not in LENGTH_UNITS:
        errors.append({"code": "unsupported_length_unit", "path": "$.units.length",
                       "message": "长度单位只支持 mm、cm、m、in。"})
    if angle_unit not in ANGLE_UNITS:
        errors.append({"code": "unsupported_angle_unit", "path": "$.units.angle",
                       "message": "角度单位只支持 deg、rad。"})

    coord = data.get("coordinate_system")
    if not isinstance(coord, dict):
        errors.append({"code": "missing_coordinate_system", "path": "$.coordinate_system",
                       "message": "必须声明右手全局直角坐标系。"})
    else:
        for key in coord:
            if key not in {"type", "handedness", "axes", "origin"}:
                errors.append({"code": "unknown_field", "path": "$.coordinate_system." + str(key),
                               "message": "坐标系对象不支持此字段。"})
        if coord.get("type") != "global_cartesian" or coord.get("handedness") != "right":
            errors.append({"code": "unsupported_coordinate_system", "path": "$.coordinate_system",
                           "message": "当前只支持右手 global_cartesian 坐标系。"})
        if coord.get("axes") != ["X", "Y", "Z"]:
            errors.append({"code": "unsupported_axis_order", "path": "$.coordinate_system.axes",
                           "message": "坐标轴顺序必须为 [X,Y,Z]。"})
        if coord.get("origin") != [0, 0, 0]:
            errors.append({"code": "unsupported_origin", "path": "$.coordinate_system.origin",
                           "message": "当前 Document 使用原点 [0,0,0]；计划坐标请先换算到全局原点。"})

    features = data.get("features")
    if not isinstance(features, list) or not features:
        errors.append({"code": "missing_features", "path": "$.features",
                       "message": "至少需要一个特征。"})
        features = []
    support = []
    ids: Dict[str, int] = {}
    has_add = False
    prior_feature_ids = []
    for index, feature in enumerate(features):
        path = "$.features[%d]" % index
        if not isinstance(feature, dict):
            errors.append({"code": "invalid_feature", "path": path,
                           "message": "特征必须是 JSON 对象。"})
            continue
        for key in feature:
            if key not in {"id", "kind", "operation", "name", "parameters", "sources",
                           "relations", "note", "enabled"}:
                errors.append({"code": "unknown_field", "path": path + "." + str(key),
                               "message": "特征不支持此字段。"})
        if "enabled" in feature and not isinstance(feature["enabled"], bool):
            errors.append({"code": "invalid_enabled", "path": path + ".enabled",
                           "message": "enabled 必须是布尔值。"})
        fid = feature.get("id")
        if not isinstance(fid, str) or not fid.strip():
            errors.append({"code": "missing_feature_id", "path": path + ".id",
                           "message": "每个特征必须有稳定 id。"})
            fid = "index-%d" % index
        elif fid in ids:
            errors.append({"code": "duplicate_feature_id", "path": path + ".id",
                           "message": "特征 id 重复：%s。" % fid})
        else:
            ids[fid] = index

        kind = feature.get("kind")
        if kind not in FEATURE_SPECS:
            errors.append({"code": "unsupported_feature_kind", "path": path + ".kind",
                           "message": "未知或非公开特征类型；支持类型见 CADPlan Schema。"})
            continue
        op = feature.get("operation")
        if op not in {ADD, CUT}:
            errors.append({"code": "invalid_operation", "path": path + ".operation",
                           "message": "operation 必须是 add 或 cut。"})
        for text_key in ("name", "note"):
            if text_key in feature and not isinstance(feature[text_key], str):
                errors.append({"code": "invalid_feature_text", "path": path + "." + text_key,
                               "message": text_key + " 必须为字符串。"})
        if op == CUT and not has_add:
            confirmations.append({"feature_id": fid, "parameter": "operation",
                                  "question": "该切除特征之前没有可切除的实体；请确认前置实体。"})
        if op == ADD and feature.get("enabled", True) is not False:
            has_add = True

        params = feature.get("parameters")
        if not isinstance(params, dict):
            confirmations.append({"feature_id": fid, "parameter": "parameters",
                                  "question": "请提供该特征的参数对象。"})
            params = {}
        for key in params:
            if key not in FEATURE_SPECS[kind]["params"]:
                errors.append({"code": "unknown_parameter", "path": path + ".parameters." + str(key),
                               "message": "该特征不支持此参数；未知参数不会传给 Document。"})

        missing = [key for key in _conditional_required(kind, params) if key not in params or params[key] is None]
        for key in missing:
            confirmations.append({"feature_id": fid, "parameter": key,
                                  "question": "请补充必需参数 %s。" % key})
        for key, value in params.items():
            ppath = path + ".parameters." + str(key)
            _walk_numbers(value, ppath, errors)
            if key in _BOOLEAN_KEYS and not isinstance(value, bool):
                errors.append({"code": "invalid_parameter_type", "path": ppath,
                               "message": "此参数必须为布尔值。"})
            elif key in _INTEGER_KEYS and (not isinstance(value, int) or isinstance(value, bool)):
                errors.append({"code": "invalid_parameter_type", "path": ppath,
                               "message": "此参数必须为整数。"})
            elif key in _INTEGER_KEYS and isinstance(value, int) and value < 1:
                errors.append({"code": "invalid_parameter_value", "path": ppath,
                               "message": "此参数必须至少为 1。"})
            elif key in _STRING_KEYS and not isinstance(value, str):
                errors.append({"code": "invalid_parameter_type", "path": ppath,
                               "message": "此参数必须为字符串。"})
            elif key in _NUMERIC_SCALAR_KEYS and (
                    not isinstance(value, (int, float)) or isinstance(value, bool)):
                errors.append({"code": "invalid_parameter_type", "path": ppath,
                               "message": "此参数必须为有限数值。"})
            elif key in _ENUMS and isinstance(value, str) and (
                    value.upper() if key == "axis" else value) not in _ENUMS[key]:
                errors.append({"code": "invalid_parameter_value", "path": ppath,
                               "message": "可选值为：%s。" % "、".join(sorted(_ENUMS[key]))})
            if key in {"teeth", "segments", "n", "points"} and isinstance(value, int) and value < (
                    6 if key == "teeth" else 3):
                errors.append({"code": "invalid_parameter_value", "path": ppath,
                               "message": "%s 的值不足以生成有效轮廓。" % key})
            if key in {"rings", "sections"} and isinstance(value, int) and value < 2:
                errors.append({"code": "invalid_parameter_value", "path": ppath,
                               "message": "%s 至少为 2。" % key})
            if key in _POSITIVE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
                errors.append({"code": "non_positive_dimension", "path": ppath,
                               "message": "该尺寸/计数必须大于 0。"})
            if key in _NONNEGATIVE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                errors.append({"code": "negative_dimension", "path": ppath,
                               "message": "该尺寸不能小于 0。"})
        if "center" in params and not _valid_vec(params["center"], 3):
            errors.append({"code": "invalid_vector", "path": path + ".parameters.center",
                           "message": "center 必须是三个有限数值 [x,y,z]。"})
        for key in ("point", "entry_point", "step", "translate", "center2"):
            if key in params and not _valid_vec(params[key], 3):
                errors.append({"code": "invalid_vector", "path": path + ".parameters." + key,
                               "message": key + " 必须是三个有限数值 [x,y,z]。"})
        if "profile" in params:
            prof = params["profile"]
            if (not isinstance(prof, list) or len(prof) < 3 or
                    any(not _valid_vec(point, 2) for point in prof)):
                errors.append({"code": "invalid_profile", "path": path + ".parameters.profile",
                               "message": "profile 必须至少包含 3 个二维有限数值点。"})
        if "rotate" in params:
            rotate = params["rotate"]
            if (not isinstance(rotate, (list, tuple)) or len(rotate) != 2 or
                    rotate[0] not in {"X", "Y", "Z"} or
                    not isinstance(rotate[1], (int, float)) or isinstance(rotate[1], bool)):
                errors.append({"code": "invalid_rotation", "path": path + ".parameters.rotate",
                               "message": "rotate 必须是 [X|Y|Z, 角度]。"})
        if kind == "hole":
            pos = params.get("pos")
            if pos == "pcd" and isinstance(params.get("count"), int) and params.get("count") < 2:
                errors.append({"code": "invalid_hole_pattern", "path": path + ".parameters.count",
                               "message": "PCD 孔数至少为 2。"})
            has_entry = "entry_point" in params
            has_direction = "direction" in params
            if has_entry != has_direction:
                errors.append({"code": "incomplete_hole_entry", "path": path + ".parameters",
                               "message": "显式盲孔入口必须同时提供 entry_point 与 direction。"})
            if (has_entry or has_direction) and params.get("through") is not False:
                errors.append({"code": "hole_entry_requires_blind", "path": path + ".parameters",
                               "message": "entry_point/direction 只适用于 through=false 的盲孔。"})
            if (has_entry and has_direction and pos != "point"):
                errors.append({"code": "hole_entry_requires_point", "path": path + ".parameters.pos",
                               "message": "显式盲孔入口要求 pos=point，以便核对入口与孔轴位置。"})
            if (has_entry and _valid_vec(params.get("entry_point"), 3)
                    and _valid_vec(params.get("point"), 3)):
                axis = str(params.get("axis", "Z")).upper()
                plane = {"X": (1, 2), "Y": (0, 2), "Z": (0, 1)}.get(axis)
                if plane and any(abs(float(params["entry_point"][i]) -
                                     float(params["point"][i])) > 1e-6 for i in plane):
                    errors.append({"code": "hole_entry_point_mismatch",
                                   "path": path + ".parameters.entry_point",
                                   "message": "entry_point 必须与 point 位于同一孔轴线上；两者的轴法平面坐标必须一致。"})
        if (kind == "vbelt_pulley" and (params.get("groove_type") or params.get("outer_d"))
                and all(isinstance(params.get(key), (int, float))
                        and not isinstance(params.get(key), bool)
                        for key in ("pitch_d", "grooves", "height"))):
            try:
                from ai_modeler.parts import std_pulley
                pulley_spec = std_pulley.spec(
                    float(params["pitch_d"]), int(params["grooves"]),
                    params.get("groove_type"), float(params["height"]),
                    float(params.get("bore_d", 0.0) or 0.0), keep_width=True)
                min_width = ((pulley_spec["grooves"] - 1) *
                             max(pulley_spec["pitch_e"], pulley_spec["root_w"] + 0.5)
                             + pulley_spec["root_w"] + 0.5)
                if float(params["height"]) + 1e-8 < min_width:
                    errors.append({
                        "code": "vbelt_width_below_profile_minimum",
                        "path": path + ".parameters.height",
                        "message": ("输入轮宽 %.6g mm 无法容纳 %d 个标准 V 槽；无自交剖面的最小轮宽为 %.6g mm。"
                                    % (float(params["height"]), pulley_spec["grooves"], min_width)),
                    })
            except (TypeError, ValueError, KeyError):
                pass
        if kind == "pattern":
            mode = params.get("mode")
            if mode not in {"linear", "circular"}:
                errors.append({"code": "invalid_pattern_mode", "path": path + ".parameters.mode",
                               "message": "阵列模式必须是 linear 或 circular。"})
            if isinstance(params.get("count"), int) and params.get("count") < 1:
                errors.append({"code": "invalid_pattern_count", "path": path + ".parameters.count",
                               "message": "阵列数量必须大于 0。"})

        sources = feature.get("sources")
        if not isinstance(sources, dict):
            sources = {}
            if any(_contains_number(v) for v in params.values()):
                confirmations.append({"feature_id": fid, "parameter": "sources",
                                      "question": "请为数值参数标明来源：user、image_estimate、derived 或 existing_model。"})
        for key, value in params.items():
            if not _contains_number(value):
                continue
            source = sources.get(key)
            if source is None:
                confirmations.append({"feature_id": fid, "parameter": key,
                                      "question": "请标明参数 %s 的数值来源。" % key})
            elif (isinstance(source, dict) and source.get("type") == "image_estimate"
                  and not str(source.get("evidence", "")).strip()):
                confirmations.append({"feature_id": fid, "parameter": key,
                                      "question": "图像估算参数 %s 请补充视图/位置证据。" % key})
        for key in sources:
            if key not in params:
                errors.append({"code": "orphan_parameter_source", "path": path + ".sources." + str(key),
                               "message": "来源字段必须对应一个参数。"})
            elif not isinstance(sources[key], dict):
                errors.append({"code": "invalid_parameter_source", "path": path + ".sources." + str(key),
                               "message": "参数来源必须是对象。"})
            else:
                if sources[key].get("type") not in {
                        "user", "image_estimate", "derived", "existing_model"}:
                    errors.append({"code": "invalid_parameter_source_type",
                                   "path": path + ".sources." + str(key) + ".type",
                                   "message": "来源 type 必须是 user、image_estimate、derived 或 existing_model。"})
                for source_key in sources[key]:
                    if source_key not in {"type", "evidence", "note"}:
                        errors.append({"code": "unknown_field", "path": path + ".sources." + str(key) + "." + str(source_key),
                                       "message": "来源对象只接受 type、evidence 和 note。"})
                if sources[key].get("evidence") is not None and not isinstance(sources[key].get("evidence"), str):
                    errors.append({"code": "invalid_source_evidence",
                                   "path": path + ".sources." + str(key) + ".evidence",
                                   "message": "evidence 必须是字符串。"})
                if sources[key].get("note") is not None and not isinstance(sources[key].get("note"), str):
                    errors.append({"code": "invalid_source_note",
                                   "path": path + ".sources." + str(key) + ".note",
                                   "message": "note 必须是字符串。"})
                if (isinstance(sources[key], dict)
                        and sources[key].get("type") == "existing_model"
                        and not str(sources[key].get("note", "")).strip()):
                    confirmations.append({"feature_id": fid, "parameter": key,
                                          "question": "继承参数 %s 请说明来自当前模型特征树；原始来源未知时请明确注明。" % key})

        relations = feature.get("relations", [])
        if not isinstance(relations, list):
            errors.append({"code": "invalid_relations", "path": path + ".relations",
                           "message": ("relations 必须是数组，例如："
                                       "[{\"type\":\"pattern_source\","
                                       "\"feature_id\":\"上一个特征ID\"}]。")})
            relations = []
        pattern_sources = []
        for ri, relation in enumerate(relations):
            rpath = "%s.relations[%d]" % (path, ri)
            if not isinstance(relation, dict) or relation.get("type") not in {"depends_on", "pattern_source"}:
                errors.append({"code": "invalid_relation", "path": rpath,
                               "message": ("关系项必须使用 type 字段（depends_on 或 pattern_source）"
                                           "和 feature_id 字段，例如："
                                           "{\"type\":\"pattern_source\","
                                           "\"feature_id\":\"上一个特征ID\"}。")})
                continue
            if set(relation) - {"type", "feature_id"}:
                errors.append({"code": "unknown_field", "path": rpath,
                               "message": "关系对象只接受 type 和 feature_id。"})
            target = relation.get("feature_id")
            if target not in ids or ids.get(target, index) >= index:
                errors.append({"code": "invalid_relation_target", "path": rpath + ".feature_id",
                               "message": ("feature_id 必须填写此前定义特征的 id；"
                                           "pattern_source 还必须指向紧邻的前一个特征。")})
            if relation.get("type") == "pattern_source":
                pattern_sources.append(target)
        if kind == "pattern":
            if len(pattern_sources) != 1:
                confirmations.append({"feature_id": fid, "parameter": "relations",
                                      "question": "阵列必须用唯一的 pattern_source 关系指定源特征。"})
            elif (index == 0 or not isinstance(features[index - 1], dict)
                  or pattern_sources[0] != features[index - 1].get("id")):
                errors.append({"code": "pattern_source_order", "path": path + ".relations",
                               "message": "当前 Document 阵列只复制紧邻的前一特征；请调整顺序或 source 关系。"})
            elif str(feature.get("operation") or "").lower() != str(
                    features[index - 1].get("operation") or "").lower():
                source = features[index - 1]
                source_operation = str(source.get("operation") or "").lower()
                if source_operation in {"add", "cut"}:
                    errors.append({
                        "code": "pattern_operation_mismatch",
                        "path": path + ".operation",
                        "message": ("阵列 operation 必须与 pattern_source 源特征「%s」一致："
                                    "源操作是 %s，阵列也必须用 %s。切孔阵列必须为 cut；"
                                    "若写 add，程序会把孔的圆柱刀具加成实体。"
                                    % (source.get("id"), source_operation, source_operation)),
                    })

        status = _feature_status(feature)
        reason = _feature_reason(feature, status)
        support.append({"feature_id": fid, "kind": kind, "status": status,
                        "reason": reason})
        geometry_row = _geometry_backend_status(feature)
        geometry_support.append(geometry_row)
        if geometry_row["status"] == "approximate":
            warnings.append({"code": "LOCAL_BREP_APPROXIMATION", "feature_id": fid,
                             "kind": kind, "message": geometry_row["reason"]})
        elif geometry_row["status"] == "unavailable":
            warnings.append({"code": "LOCAL_BREP_UNAVAILABLE", "feature_id": fid,
                             "kind": kind, "message": geometry_row["reason"]})
        if status == "approximate":
            warnings.append({"code": "CATIA_APPROXIMATION", "feature_id": fid,
                             "kind": kind, "message": reason})
        elif status == "unsupported":
            warnings.append({"code": "CATIA_UNSUPPORTED", "feature_id": fid,
                             "kind": kind, "message": reason})
        prior_feature_ids.append(fid)

    pending = data.get("pending_questions", [])
    if pending:
        if not isinstance(pending, list):
            errors.append({"code": "invalid_pending_questions", "path": "$.pending_questions",
                           "message": "pending_questions 必须是数组。"})
        else:
            for i, question in enumerate(pending):
                confirmations.append({"feature_id": "", "parameter": "pending_questions[%d]" % i,
                                      "question": str(question)})
    state = "invalid" if errors else ("needs_confirmation" if confirmations else "valid")
    return {
        "valid": state == "valid",
        "state": state,
        "errors": errors,
        "confirmations": confirmations,
        "support": support,
        "geometry_support": geometry_support,
        "warnings": warnings,
        "schema_version": CADPLAN_SCHEMA_VERSION,
    }


def _scale_nested(value: Any, factor: float) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) * factor
    if isinstance(value, list):
        return [_scale_nested(v, factor) for v in value]
    if isinstance(value, tuple):
        return tuple(_scale_nested(v, factor) for v in value)
    return copy.deepcopy(value)


def _to_document(plan: Mapping[str, Any]) -> Tuple[Document, Dict[str, Dict[str, Any]]]:
    length_factor = LENGTH_UNITS[plan["units"]["length"]]
    angle_factor = ANGLE_UNITS[plan["units"]["angle"]]
    doc = Document(plan["name"])
    doc.source_prompt = str(plan.get("source_prompt", ""))
    manifest = {}
    for item in plan["features"]:
        params = {}
        for key, value in item["parameters"].items():
            if key == "thickness" and item["kind"] == "loft":
                params[key] = copy.deepcopy(value)
            elif key == "thickness" and item["kind"] == "shell":
                params[key] = _scale_nested(value, length_factor)
            elif key in _LENGTH_ARRAY_KEYS or key in _LENGTH_KEYS:
                params[key] = _scale_nested(value, length_factor)
            elif key in _ANGLE_KEYS:
                params[key] = _scale_nested(value, angle_factor)
            elif key == "rotate" and isinstance(value, (list, tuple)) and len(value) == 2:
                params[key] = [value[0], float(value[1]) * angle_factor]
            else:
                params[key] = copy.deepcopy(value)
        # `point` keeps its legacy global XYZ centerline-point meaning and is
        # projected to the axis plane. `entry_point` is separate: it fixes the
        # opening location for an explicitly directed blind hole.
        if item["kind"] == "hole" and params.get("pos") == "point":
            x, y, z = params["point"]
            axis = str(params.get("axis", "Z")).upper()
            params["x"], params["y"] = (x, y) if axis == "Z" else ((y, z) if axis == "X" else (x, z))
        # CADPlan's box center is a geometric center. Document's legacy box
        # primitive defaults to a base point at the start of a one-sided
        # extrusion, so opt into a symmetric extrusion unless the plan
        # explicitly requests the legacy base behavior.
        if (item["kind"] == "box" and "center" in params
                and "base" not in item["parameters"]):
            params["base"] = False
        feature = Feature(item["kind"], params,
                          CUT if item["operation"] == CUT else ADD,
                          item.get("name", ""), item.get("note", ""),
                          item.get("id"), item.get("relations", []))
        feature.enabled = item.get("enabled", True)
        source_map = item.get("sources") or {}
        provenance = {key: source.get("type", "unknown")
                      for key, source in source_map.items() if isinstance(source, dict)}
        if item["kind"] == "hole" and "point" in source_map:
            source_type = source_map["point"].get("type", "unknown")
            provenance.update({"x": source_type, "y": source_type})
        if item["kind"] == "hole" and "entry_point" in source_map:
            provenance["entry_point"] = source_map["entry_point"].get("type", "unknown")
        feature.set_provenance(provenance)
        doc.add(feature)
        manifest[item["id"]] = {
            "kind": item["kind"],
            "operation": item["operation"],
            "name": item.get("name", feature.name),
            "parameters": copy.deepcopy(item["parameters"]),
            "sources": copy.deepcopy(source_map),
            "relations": copy.deepcopy(item.get("relations", [])),
        }
    return doc, manifest


def _doc_or_error(plan: Any):
    data, load_error = _read_plan(plan)
    validation = validate_plan(data if data is not None else plan)
    if data is None:
        return None, None, validation
    if not validation["valid"]:
        return data, None, validation
    doc, manifest = _to_document(data)
    return data, (doc, manifest), validation


def _catia_operations(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    operations = []
    for index, entry in enumerate(features):
        operations.append({
            "index": index + 1,
            "kind": entry.get("kind"),
            "plane": entry.get("plane", "XY"),
            "center": [entry.get("cx", 0.0), entry.get("cy", 0.0), entry.get("z", 0.0)],
            "profile": copy.deepcopy(entry.get("profile") or []),
            "dimensions": {
                key: entry[key] for key in ("l", "w", "r", "h", "depth")
                if key in entry
            },
            "name": entry.get("name", ""),
            "note": entry.get("note", ""),
        })
    return operations


def _ray_surface_crossings(triangles, point, axis):
    """Count distinct surface crossings of an axis-parallel ray at a point."""
    axis_index = {"X": 0, "Y": 1, "Z": 2}.get(str(axis).upper(), 2)
    u, v = [index for index in (0, 1, 2) if index != axis_index]
    hits = []
    for a, b, c, _tag in triangles:
        den = ((b[v] - c[v]) * (a[u] - c[u]) +
               (c[u] - b[u]) * (a[v] - c[v]))
        if abs(den) < 1e-10:
            continue
        w1 = ((b[v] - c[v]) * (point[u] - c[u]) +
              (c[u] - b[u]) * (point[v] - c[v])) / den
        w2 = ((c[v] - a[v]) * (point[u] - c[u]) +
              (a[u] - c[u]) * (point[v] - c[v])) / den
        w3 = 1.0 - w1 - w2
        if min(w1, w2, w3) < -1e-7:
            continue
        axial = w1 * a[axis_index] + w2 * b[axis_index] + w3 * c[axis_index]
        if all(abs(axial - old) > 1e-5 for old in hits):
            hits.append(axial)
    return len(hits)


def _local_geometry_checks(body, size, feature_manifest, length_factor=1.0,
                           brep_report=None):
    """Report deterministic mesh facts without claiming CATIA verification."""
    if brep_report is not None:
        intent_feature_ids = {
            str(intent.get("feature_id"))
            for item in (brep_report.get("feature_results") or [])
            for intent in (item.get("cylindrical_cut_intents") or [])
            if intent.get("feature_id") is not None
        }
        intent_feature_ids.update(
            str(row.get("feature_id")) for row in (brep_report.get("hole_instances") or [])
            if row.get("feature_id") is not None)
        inventory = [
            {"feature_id": feature_id, "kind": feature.get("kind"),
             "operation": feature.get("operation"),
             "parameters": feature.get("parameters"),
             "hole_intent_source": ("declared_hole" if feature.get("kind") == "hole"
                                    else "inferred_from_cylindrical_cut_geometry")}
            for feature_id, feature in feature_manifest.items()
            if feature.get("kind") == "hole" or str(feature_id) in intent_feature_ids
        ]
        hole_instances = copy.deepcopy(brep_report.get("hole_instances") or [])
        unverified_cut_intents = copy.deepcopy(
            brep_report.get("unverified_cut_intents") or [])
        size_values = list(size) if size else None
        invalid_bbox = (not size_values or len(size_values) != 3
                        or any(not math.isfinite(float(v)) or float(v) <= 0
                               for v in size_values))
        self_check = brep_report.get("self_intersection") or {}
        self_state = self_check.get("status")
        feature_results = list(brep_report.get("feature_results") or [])
        skipped_or_noop = any(row.get("state") in {"skipped", "no_op"}
                              for row in feature_results)
        valid = bool(brep_report.get("brep_valid"))
        closed = bool(brep_report.get("closed"))
        free_edges = brep_report.get("free_edge_count")
        solid_count = int(brep_report.get("solid_count") or 0)
        expected_hole_instances = int(
            brep_report.get("active_hole_instance_count",
                            brep_report.get("planned_hole_instance_count", len(hole_instances))) or 0)
        active_hole_instances = [row for row in hole_instances
                                 if row.get("status") != "superseded"]
        holes_complete = (
            len(active_hole_instances) == expected_hole_instances
            and not unverified_cut_intents
            and all(row.get("status") in {"verified_open", "verified_blind",
                                           "verified_counterbore", "verified_blind_segment"}
                    for row in active_hole_instances))
        failed_hole_instances = [
            row for row in active_hole_instances
            if row.get("status") in {"blocked", "opening_clipped", "diameter_mismatch",
                                      "no_cylindrical_wall", "depth_mismatch"}]
        volume = brep_report.get("volume_mm3")
        if (not valid or not closed or free_edges not in (0, None)
                or brep_report.get("failed_feature_id")
                or invalid_bbox or solid_count != 1
                or failed_hole_instances
                or volume is None or not math.isfinite(float(volume))
                or float(volume) <= 0):
            state = "failed"
        elif self_state in {"self_intersection", "failed"}:
            state = "failed"
        elif (free_edges is None or not holes_complete or skipped_or_noop
              or self_state not in {"checked_no_intersection", "not_applicable"}):
            state = "needs_review"
        else:
            state = "passed"
        image_estimates = []
        for feature_id, feature in feature_manifest.items():
            for parameter, source in (feature.get("sources") or {}).items():
                if isinstance(source, dict) and source.get("type") == "image_estimate":
                    image_estimates.append({
                        "feature_id": feature_id, "parameter": parameter,
                        "value": (feature.get("parameters") or {}).get(parameter),
                        "evidence": source.get("evidence"),
                        "visual_measurement": "not_performed_by_geometry_backend",
                    })
        ratios = ({"front_width_height": size_values[0] / size_values[2],
                   "depth_height": size_values[1] / size_values[2],
                   "top_width_depth": size_values[0] / size_values[1]}
                  if not invalid_bbox else None)
        return {
            **copy.deepcopy(brep_report),
            "state": state,
            "geometry_state": state,
            "source": "OpenCASCADE native BREP; preview mesh is display-only",
            "bbox_size_mm": size_values,
            "bbox_ratios_from_brep": ratios,
            "bbox_ratios_from_mesh": ratios,
            "feature_count_from_plan": len(feature_manifest),
            "feature_ids_from_plan": list(feature_manifest),
            "hole_features_from_plan": inventory,
            "hole_feature_count_from_plan": len(inventory),
            "expected_hole_instance_count": expected_hole_instances,
            "hole_instance_count": len(hole_instances),
            "active_hole_instance_count": len(active_hole_instances),
            "superseded_hole_instance_count": len(hole_instances) - len(active_hole_instances),
            "hole_instances": hole_instances,
            "through_hole_checks": hole_instances,
            # Retained as a compatibility alias for existing MCP consumers.
            "through_hole_mesh_checks": hole_instances,
            "hole_measurement_complete": holes_complete,
            "hole_measurement_state": (
                "not_applicable_no_holes_or_cylindrical_cuts"
                if not inventory and not hole_instances and not unverified_cut_intents
                else "complete" if holes_complete else "incomplete_or_unverified"),
            "unverified_cut_intents": unverified_cut_intents,
            "through_hole_count_expected": int(
                brep_report.get("active_through_hole_count",
                                brep_report.get("through_hole_count", 0)) or 0),
            "through_hole_count_verified": sum(
                1 for row in active_hole_instances
                if row.get("through") and row.get("status") == "verified_open"),
            "through_hole_result": (
                "measured_by_native_brep" if any(
                    row.get("through") for row in active_hole_instances) and all(
                    row.get("status") == "verified_open" for row in active_hole_instances
                    if row.get("through"))
                else "no_holes_declared" if not inventory
                else "not_measured_or_failed"),
            "image_estimate_parameters": image_estimates,
            "visual_validation": "not_performed",
            "catia_geometry_verification": "not_performed",
        }
    nonfinite_vertices = 0
    degenerate_faces = 0
    for polygon in body:
        points = polygon.points()
        if any(not all(math.isfinite(float(axis)) for axis in point)
               for point in points):
            nonfinite_vertices += 1
            continue
        if len(points) < 3 or len({tuple(point) for point in points}) < 3:
            degenerate_faces += 1
    size_values = list(size) if size else None
    invalid_bbox = (not size_values or len(size_values) != 3
                    or any(not math.isfinite(float(v)) or v <= 0
                           for v in size_values))
    inventory = [
        {"feature_id": feature_id, "kind": feature.get("kind"),
         "operation": feature.get("operation"),
         "parameters": feature.get("parameters")}
        for feature_id, feature in feature_manifest.items()
        if feature.get("kind") == "hole"
    ]
    hole_mesh_checks = []
    point_holes = [row for row in inventory
                   if row["parameters"].get("pos") == "point"
                   and row["parameters"].get("through") is True
                   and isinstance(row["parameters"].get("point"), list)
                   and len(row["parameters"]["point"]) == 3]
    if point_holes and body:
        from ai_modeler.core import kernel as K
        triangles = K.polygons_to_triangles(body)
        for hole in point_holes:
            params = hole["parameters"]
            center = [float(value) * length_factor for value in params["point"]]
            axis = str(params.get("axis") or "Z").upper()
            axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 2)
            side = [index for index in (0, 1, 2) if index != axis_index]
            radius = float(params.get("diameter", 0)) * length_factor / 2.0
            center_hits = _ray_surface_crossings(triangles, center, axis)
            ring_hits = []
            for side_index in side:
                for direction in (-1, 1):
                    sample = list(center)
                    sample[side_index] += direction * radius * 1.25
                    ring_hits.append(_ray_surface_crossings(triangles, sample, axis))
            # `any(ring_hits)` only asked whether *some* material surrounds the
            # hole, so a hole whose bore spills past the part's edge still
            # reported verified_open -- while the reviewer kept (correctly) seeing
            # a half-circle notch, and the model kept rebuilding the whole part to
            # explain the difference. Requiring the whole ring to sit in material
            # turns that argument into a mesh fact.
            supported = [hits >= 1 for hits in ring_hits]
            status = ("verified_open" if center_hits == 0 and all(supported)
                      else "opening_clipped" if center_hits == 0 and any(supported)
                      else "blocked" if center_hits > 0 and any(ring_hits)
                      else "inconclusive")
            hole_mesh_checks.append({
                "feature_id": hole["feature_id"], "axis": axis,
                "center_mm": center, "status": status,
                "axis_surface_crossings": center_hits,
                "surrounding_surface_crossings": ring_hits,
                "ring_supported": supported,
                "method": "axis ray against local triangle mesh",
            })
    hole_measurement_complete = len(hole_mesh_checks) == len(inventory)
    geometry_state = (
        "failed" if (not body or invalid_bbox or nonfinite_vertices
                     or degenerate_faces
                     or any(row["status"] != "verified_open"
                            for row in hole_mesh_checks))
        else "needs_review" if not hole_measurement_complete
        else "passed"
    )
    return {
        "state": geometry_state,
        "source": "AI Modeler local mesh kernel",
        "face_count": len(body), "bbox_size_mm": size_values,
        "bbox_ratios_from_mesh": ({
            "front_width_height": size_values[0] / size_values[2],
            "depth_height": size_values[1] / size_values[2],
            "top_width_depth": size_values[0] / size_values[1],
        } if not invalid_bbox else None),
        "feature_count_from_plan": len(feature_manifest),
        "feature_ids_from_plan": list(feature_manifest),
        "nonfinite_face_count": nonfinite_vertices,
        "degenerate_face_count": degenerate_faces,
        "hole_features_from_plan": inventory,
        "through_hole_mesh_checks": hole_mesh_checks,
        "hole_measurement_complete": hole_measurement_complete,
        "hole_visibility": "requires_multimodal_view_review",
        "through_hole_result": (
            "measured_for_all_declared_holes" if hole_measurement_complete and inventory
            else "no_holes_declared" if not inventory
            else "not_measured_for_all_declared_holes"),
    }


def _preview_internal(plan: Any):
    """Return the public preview plus its private build state for build_plan."""
    data, pair, validation = _doc_or_error(plan)
    if pair is None:
        return ({"state": validation["state"], "validation": validation,
                 "catia_executed": False, "dimensions": None, "catia_operations": []},
                None, None, data, None)
    doc, manifest = pair
    try:
        from ai_modeler.core.geometry import occ_kernel
        brep = doc.build_brep()
        brep_report = doc.brep_report or {}
        body = occ_kernel.shape_to_polys(brep)
        bbox = brep_report.get("bbox_mm")
        size = ([bbox[i + 3] - bbox[i] for i in range(3)]
                if bbox else None)
        stats = {
            "bbox": bbox,
            "size": size,
            "volume": brep_report.get("volume_mm3"),
            "area": occ_kernel.surface_area(brep),
            "triangles": len(body),
            "faces": brep_report.get("face_count"),
        }
        from ai_modeler.catia_backend import catia_export
        catia_features, exporter_warnings = catia_export.build_catia_features(doc)
    except Exception as exc:
        failure_report = getattr(exc, "report", None)
        previous = getattr(exc, "last_valid_shape", None)
        partial_body = None
        partial_checks = None
        last_valid_dimensions = None
        if previous is not None:
            try:
                previous_info = occ_kernel.inspect_shape(previous)
                partial_body = occ_kernel.shape_to_polys(previous)
                partial_checks = {
                    **previous_info,
                    "state": "needs_review", "geometry_state": "needs_review",
                    "partial": True, "accepted": False,
                    "applied_feature_results": list((failure_report or {}).get("feature_results", [])),
                    "failed_feature_effect": (failure_report or {}).get("failed_feature_effect"),
                }
                applied_results = [row for row in (failure_report or {}).get("feature_results", [])
                                   if row.get("state") == "applied"]
                applied_ids = [str(row.get("feature_id")) for row in applied_results
                               if row.get("feature_id") is not None]
                previous_bbox = previous_info.get("bbox_mm")
                previous_size = ([previous_bbox[i + 3] - previous_bbox[i]
                                  for i in range(3)] if previous_bbox else None)
                last_valid_dimensions = {
                    "unit": "mm", "bbox": previous_bbox,
                    "size": previous_size,
                    "volume_mm3": previous_info.get("volume_mm3"),
                    "face_count": previous_info.get("face_count"),
                    "feature_count": len(applied_results),
                    "applied_feature_ids": applied_ids,
                    "failed_feature_id": (failure_report or {}).get("failed_feature_id"),
                }
            except Exception:
                last_valid_dimensions = None
        return ({"state": "preview_failed", "validation": validation,
                 "error": str(exc), "catia_executed": False,
                 "dimensions": None,
                 "last_valid_dimensions": last_valid_dimensions,
                 "feature_manifest": manifest,
                 "catia_operations": [],
                 "local_geometry_checks": failure_report,
                 "last_valid_intermediate_preserved": previous is not None,
                 "geometry_backend": "occt_brep",
                 "partial_preview": bool(partial_body),
                 "partial_state": "candidate_partial_not_accepted" if partial_body else None,
                 "partial_geometry_checks": partial_checks,
                 "failed_feature_id": (failure_report or {}).get("failed_feature_id"),
                 "failure_code": ((failure_report or {}).get("failed_feature_effect") or {}).get("failure_code"),
                 "related_feature_ids": ((failure_report or {}).get("failed_feature_effect") or {}).get("related_feature_ids", []),
                 "partial_image": None},
                None, None, data, partial_body)
    warnings = list(validation["warnings"])
    for index, message in enumerate(exporter_warnings):
        warnings.append({"code": "CATIA_TRANSLATOR_NOTE", "feature_id": None,
                         "kind": None, "message": str(message), "index": index})
    if not body:
        warnings.append({"code": "EMPTY_GEOMETRY", "feature_id": None,
                         "kind": None, "message": "Document 没有生成实体几何。"})
    result = {
        "state": "previewed" if body else "empty_geometry",
        "validation": validation,
        "geometry_backend": "occt_brep",
        "native_brep": {key: value for key, value in brep_report.items()
                        if key not in {"hole_instances", "feature_results"}},
        "catia_executed": False,
        "dimensions": {
            "unit": "mm",
            "bbox": list(stats["bbox"]) if stats.get("bbox") else None,
            "size": list(stats["size"]) if stats.get("size") else None,
            "volume_mm3": stats.get("volume"),
            "surface_area_mm2": stats.get("area"),
            "triangles": stats.get("triangles"),
            "feature_count": len(doc.features),
        },
        "catia_operations": _catia_operations(catia_features),
        "warnings": warnings,
        "feature_manifest": manifest,
        "build_log": list(doc.last_log),
        "local_geometry_checks": _local_geometry_checks(
            body, stats.get("size"), manifest,
            LENGTH_UNITS[data["units"]["length"]], brep_report=brep_report),
    }
    result["orthographic_layout"] = _orthographic_layout(stats.get("size"))
    return result, doc, manifest, data, body


def plan_to_document(plan: Any) -> Document:
    """Convert a valid CADPlan to a local Document without invoking CATIA."""
    _data, pair, validation = _doc_or_error(plan)
    if pair is None:
        issues = validation.get("errors") or validation.get("confirmations") or []
        detail = "; ".join(
            item.get("message", str(item)) if isinstance(item, dict) else str(item)
            for item in issues[:8])
        raise ValueError("CADPlan 不能转换为 Document (%s): %s" %
                         (validation.get("state"), detail or "无效计划"))
    return pair[0]


def preview_plan(plan: Any, image_path: Optional[str] = None,
                 view_set: str = "three") -> Dict[str, Any]:
    """Build local geometry and write an inspectable multi-view PNG.

    When image_path is omitted, the PNG is written to a unique temporary path so
    a multimodal caller can open it after reading this result. This is a local
    kernel preview, not a screenshot or measurement of a CATIA B-Rep. ``three``
    writes front/right/top; ``extended`` writes iso/front/right/top/back/left/
    bottom at one shared scale. A standard view name writes only that view.
    """
    single_views = {"iso", "front", "right", "top", "back", "left", "bottom"}
    if view_set not in {"three", "extended"} | single_views:
        return {"state": "invalid_view_set", "catia_executed": False,
                "error": "view_set 必须是 three、extended 或一个标准单视图名称。"}
    result, _doc, _manifest, _data, body = _preview_internal(plan)
    is_partial = bool(result.get("partial_preview"))
    if (result.get("state") != "previewed" and not is_partial) or not body:
        return result
    if image_path:
        target = Path(image_path).expanduser().absolute()
        if target.suffix.lower() != ".png":
            return {**result, "state": "invalid_preview_path",
                    "preview_image_error": "image_path 必须以 .png 结尾。"}
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        suffix = ".%s-view.png" % view_set
        fd, generated = tempfile.mkstemp(prefix="cadplan-preview-", suffix=suffix)
        os.close(fd)
        target = Path(generated)
    try:
        from ai_modeler.ui import softrender
        if view_set == "three":
            views = ["front", "right", "top"]
            softrender.save_three_view(body, str(target), tile=THREE_VIEW_TILE_PX)
        elif view_set == "extended":
            views = ["iso", "front", "right", "top", "back", "left", "bottom"]
            softrender.save_multiview(body, str(target), views=views,
                                      tile=THREE_VIEW_TILE_PX, columns=3)
        else:
            views = [view_set]
            softrender.save_multiview(body, str(target), views=views,
                                      tile=(660, 520), columns=1)
    except Exception as exc:
        if is_partial:
            return {**result, "partial_image_error": str(exc)}
        return {**result, "state": "preview_image_failed",
                "preview_image_error": str(exc)}
    result["preview_image"] = str(target)
    if is_partial:
        result["partial_image"] = str(target)
    result["preview_image_views"] = views
    result["preview_image_view_set"] = view_set
    result["preview_image_grid_columns"] = 1 if view_set in single_views else 3
    result["preview_image_source"] = "OCCT BREP tessellated for display; not a CATIA screenshot"
    result["preview_render_style"] = "view-adaptive depth-cued matte shading with visible feature edges"
    result["preview_render_note"] = (
        "诊断渲染：凹入或远离相机的表面会略暗，清晰线条表示可见的几何折边；"
        "亮暗不是零件材质颜色，需结合其它视图判断。")
    result["preview_image_layout"] = result.get("orthographic_layout")
    return result


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (text[:64] or "cadplan")


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def build_plan(plan: Any, catpart_path: Optional[str] = None,
               strict: bool = False) -> Dict[str, Any]:
    """Export AIMODEL/STL/report, then build and save an editable CATPart.

    strict=True returns before any CATIA call if a feature is approximate or
    unsupported. Output files are never overwritten.
    """
    if strict:
        preflight = validate_plan(plan)
        if preflight.get("state") != "valid":
            return {"state": preflight.get("state", "invalid"),
                    "catia_executed": False, "validation": preflight,
                    "errors": preflight.get("errors", []),
                    "confirmations": preflight.get("confirmations", []),
                    "dimensions": None, "catia_operations": [],
                    "output_paths": {}}
        strict_blockers = [item for item in preflight.get("support", [])
                           if item.get("status") != "exact"]
        if strict_blockers:
            return {"state": "strict_blocked", "catia_executed": False,
                    "validation": preflight, "dimensions": None,
                    "catia_operations": [], "warnings": preflight.get("warnings", []),
                    "blocked_features": strict_blockers, "output_paths": {}}
    preview, doc, manifest, data, body = _preview_internal(plan)
    if preview.get("state") != "previewed":
        return {"state": preview.get("state", "invalid"), "catia_executed": False,
                "validation": preview.get("validation"), "warnings": preview.get("warnings", []),
                "errors": (preview.get("validation") or {}).get("errors", []),
                "confirmations": (preview.get("validation") or {}).get("confirmations", []),
                "dimensions": preview.get("dimensions"), "catia_operations": []}
    local_checks = preview.get("local_geometry_checks") or {}
    quality_state = local_checks.get("geometry_state", local_checks.get("state"))
    check_status = (local_checks.get("self_intersection") or {}).get("status")
    feature_results = local_checks.get("feature_results") or []
    explicit_skips_only = (any(row.get("state") == "skipped" for row in feature_results)
                           and not any(row.get("state") == "no_op" for row in feature_results)
                           and check_status in {"checked_no_intersection", "not_applicable"})
    if quality_state == "failed":
        blocked_state = ("geometry_failed" if quality_state == "failed"
                         else "geometry_needs_review")
        support = ((preview.get("validation") or {}).get("support") or [])
        return {"state": blocked_state, "catia_invoked": False,
                "catia_executed": False, "catia_saved": False,
                "catia_operations": [], "dimensions": preview.get("dimensions"),
                "validation": preview.get("validation"),
                "local_geometry_checks": local_checks,
                "warnings": preview.get("warnings", []),
                "blocked_features": [item for item in support if item.get("status") != "exact"],
                "output_paths": {},
                "reason": "OpenCASCADE geometry checks did not pass; export and CATIA execution were blocked."}
    candidate_only = (quality_state != "passed" and not explicit_skips_only)
    warnings = list(preview.get("warnings", []))
    validation = preview["validation"]
    support = preview["validation"]["support"]
    blockers = [item for item in support if item["status"] != "exact"]
    if strict and blockers:
        return {
            "state": "strict_blocked",
            "catia_executed": False,
            "validation": preview["validation"],
            "dimensions": preview["dimensions"],
            "catia_operations": preview["catia_operations"],
            "warnings": warnings,
            "blocked_features": blockers,
            "output_paths": {},
        }

    if catpart_path:
        catpart = Path(catpart_path).expanduser().absolute()
        if catpart.suffix.lower() != ".catpart":
            return {"state": "invalid_output_path", "catia_executed": False,
                    "error": "catpart_path 必须以 .CATPart 结尾。"}
        out_dir, stem = catpart.parent, catpart.stem
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = temp_path("outputs", "cadplan", stamp + "-" + _slug(data["name"]))
        stem = _slug(data["name"])
        catpart = out_dir / (stem + ".CATPart")
    aimodel = out_dir / (stem + ".aimodel")
    stl = out_dir / (stem + ".stl")
    three_view = out_dir / (stem + ".three-view.png")
    four_view = out_dir / (stem + ".four-view.png")
    report_path = out_dir / (stem + ".geometry-report.json")
    targets = (catpart, aimodel, stl, three_view, four_view, report_path)
    if any(target.exists() for target in targets):
        return {"state": "output_exists", "catia_executed": False,
                "error": "输出路径已存在；请指定新的 CATPart 路径。",
                "output_paths": {"catpart": str(catpart), "aimodel": str(aimodel),
                                 "stl": str(stl), "three_view": str(three_view),
                                 "four_view": str(four_view), "report": str(report_path)}}

    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"size": preview["dimensions"].get("size")}
    try:
        from ai_modeler.ui import softrender
        softrender.save_three_view(body, str(three_view), tile=THREE_VIEW_TILE_PX)
        softrender.save_four_view(body, str(four_view))
    except Exception as exc:
        return {"state": "preview_image_failed", "catia_executed": False,
                "error": str(exc), "dimensions": preview["dimensions"],
                "output_paths": {"three_view": str(three_view), "four_view": str(four_view)}}
    from ai_modeler.core import exporters
    exporters.export_project(doc, str(aimodel), extra={
        "cadplan_schema_version": CADPLAN_SCHEMA_VERSION,
        "cadplan": data,
        "feature_manifest": manifest,
    })
    exporters.export_stl(body, str(stl), doc.name)
    output_paths = {"aimodel": str(aimodel), "stl": str(stl),
                    "three_view": str(three_view), "four_view": str(four_view),
                    "report": str(report_path)}
    if not candidate_only:
        output_paths["catpart"] = str(catpart)
    report = {
        "format": "ai-modeler-geometry-report",
        "version": 1,
        "plan_schema_version": CADPLAN_SCHEMA_VERSION,
        "model": data["name"],
        "geometry_source": "OpenCASCADE native BREP; preview/STL use its tessellation.",
        "geometry_backend": preview.get("geometry_backend", "occt_brep"),
        "local_geometry_checks": preview.get("local_geometry_checks"),
        "dimensions": preview["dimensions"],
        "features": manifest,
        "support": support,
        "catia_operations": preview["catia_operations"],
        "warnings": warnings,
        "catia": {"invoked": False, "executed": False, "saved": False, "message": ""},
        "catia_geometry_verification": {
            "state": "not_performed",
            "reason": "当前 cscript/COM 通道返回 CATIA 操作与保存状态，不回读 CATPart 的 B-Rep 测量值。",
        },
        "preview_images": {
            "state": "awaiting_agent_inspection",
            "source": "OCCT BREP tessellated for display; not CATIA BREP",
            "three_view": {"path": str(three_view), "views": ["front", "right", "top"],
                           "layout": _orthographic_layout(stats.get("size"))},
            "four_view": {"path": str(four_view), "views": ["iso", "front", "right", "top"]},
        },
        "output_paths": output_paths,
        "candidate_saved": bool(candidate_only),
        "acceptance": "not_accepted" if candidate_only else "awaiting_visual_review",
    }
    _write_report(report_path, report)
    notice_rows = [
        "近似/跳过特征说明：",
        *["[%s] %s (%s): %s" % (item["status"], item["feature_id"], item["kind"], item["reason"])
          for item in blockers],
    ]
    notice = "\n".join(notice_rows) if blockers else ""
    if candidate_only:
        report["catia"] = {"invoked": False, "executed": False, "saved": False,
                           "message": "几何有效性/孔实例仍需核验；仅保存CADPlan与BREP派生候选，不生成CATPart。"}
        _write_report(report_path, report)
        return {
            "state": "candidate_unverified",
            "candidate_saved": True,
            "acceptance": "not_accepted",
            "geometry_state": quality_state,
            "catia_invoked": False,
            "catia_executed": False,
            "catia_saved": False,
            "dimensions": preview["dimensions"],
            "local_geometry_checks": preview.get("local_geometry_checks"),
            "warnings": warnings,
            "blocked_features": [],
            "output_paths": output_paths,
            "visual_review": report["preview_images"],
            "validation": validation,
        }
    from ai_modeler.catia_backend import catia
    if preview["catia_operations"]:
        ok, message, script = catia.push_to_catia(
            doc, save_catpart=str(catpart), post_build_notice=notice)
        invoked = True
    else:
        ok, message, script, invoked = False, "没有可执行的 CATIA 特征操作。", None, False
    saved = bool(ok and catpart.is_file())
    executed = bool(script)
    report["catia"] = {"invoked": invoked, "executed": executed, "saved": saved,
                       "message": message, "script": script}
    _write_report(report_path, report)
    has_unsupported = any(item["status"] == "unsupported" for item in support)
    state = "built" if saved and not has_unsupported else (
        "partial" if has_unsupported or not preview["catia_operations"] else "catia_failed")
    return {
        "state": state,
        "catia_invoked": invoked,
        "catia_executed": executed,
        "catia_saved": saved,
        "catia_message": message,
        "catia_script": script,
        "dimensions": preview["dimensions"],
        "catia_operations": preview["catia_operations"],
        "warnings": warnings,
        "blocked_features": [],
        "output_paths": report["output_paths"],
        "visual_review": report["preview_images"],
        "validation": validation,
    }


def schema_document() -> Dict[str, Any]:
    """Generate the complete JSON Schema document for CADPlan v1."""
    numeric = {"type": "number"}
    integer = {"type": "integer"}
    boolean = {"type": "boolean"}
    strings = {"type": "string"}
    vec3 = {"type": "array", "prefixItems": [numeric, numeric, numeric],
            "minItems": 3, "maxItems": 3, "items": False}
    profile = {"type": "array", "minItems": 3, "items": {
        "type": "array", "prefixItems": [numeric, numeric], "minItems": 2,
        "maxItems": 2, "items": False}}
    int_keys = _INTEGER_KEYS
    bool_keys = _BOOLEAN_KEYS
    string_keys = _STRING_KEYS
    prop_schemas: Dict[str, Any] = {}
    for key in _LENGTH_KEYS | _ANGLE_KEYS | _DIMENSIONLESS_KEYS:
        prop_schemas[key] = copy.deepcopy(numeric)
        if key in _POSITIVE_KEYS:
            prop_schemas[key]["exclusiveMinimum"] = 0
        elif key in _NONNEGATIVE_KEYS:
            prop_schemas[key]["minimum"] = 0
    for key in int_keys:
        prop_schemas[key] = copy.deepcopy(integer)
        prop_schemas[key]["minimum"] = 1
    for key in {"teeth", "segments", "n", "points"}:
        prop_schemas[key]["minimum"] = 6 if key == "teeth" else 3
    for key in {"rings", "sections"}:
        prop_schemas[key]["minimum"] = 2
    for key in bool_keys:
        prop_schemas[key] = boolean
    for key in string_keys:
        prop_schemas[key] = strings
    prop_schemas.update({
        "center": vec3, "center2": vec3, "translate": vec3, "step": vec3,
        "point": vec3, "entry_point": vec3, "profile": profile,
        "rotate": {"type": "array", "prefixItems": [
            {"type": "string", "enum": ["X", "Y", "Z"]}, numeric],
            "minItems": 2, "maxItems": 2, "items": False},
    })
    for key in ("axis", "corner_mode", "mode", "pos", "side", "direction",
                "groove_type", "tooth_type"):
        prop_schemas[key] = {"type": "string", "enum": sorted(_ENUMS[key])}
    feature_alternatives = []
    for kind, spec in FEATURE_SPECS.items():
        parameter_props = {key: copy.deepcopy(prop_schemas[key])
                           for key in spec["params"] if key in prop_schemas}
        parameter_schema = {"type": "object", "properties": parameter_props,
                            "additionalProperties": False}
        conditional_required = list(spec["required"])
        if kind == "hole":
            conditional_required = ["diameter", "through", "pos"]
        if kind == "pattern":
            conditional_required = ["mode", "count"]
        conditionals = []
        if kind == "hole":
            conditionals.extend([
                {
                    "if": {"properties": {"parameters": {"properties": {"pos": {"const": "point"}},
                                                           "required": ["pos"]}}},
                    "then": {"properties": {"parameters": {"required": ["point"]}}},
                },
                {
                    "if": {"properties": {"parameters": {"properties": {"pos": {"const": "pcd"}},
                                                           "required": ["pos"]}}},
                    "then": {"properties": {"parameters": {"required": ["pcd", "count"]}}},
                },
                {
                    "if": {"properties": {"parameters": {"properties": {"through": {"const": False}},
                                                           "required": ["through"]}}},
                    "then": {"properties": {"parameters": {"required": ["depth"]}}},
                },
                {
                    "if": {"properties": {"parameters": {"required": ["entry_point"]}}},
                    "then": {"properties": {"parameters": {
                        "required": ["direction", "point", "pos", "through"],
                        "properties": {"pos": {"const": "point"},
                                       "through": {"const": False}},
                    }}},
                },
                {
                    "if": {"properties": {"parameters": {"required": ["direction"]}}},
                    "then": {"properties": {"parameters": {
                        "required": ["entry_point", "point", "pos", "through"],
                        "properties": {"pos": {"const": "point"},
                                       "through": {"const": False}},
                    }}},
                },
            ])
        if kind == "pattern":
            conditionals.extend([
                {
                    "if": {"properties": {"parameters": {"properties": {"mode": {"const": "linear"}},
                                                           "required": ["mode"]}}},
                    "then": {"properties": {"parameters": {"required": ["step"]}}},
                },
                {
                    "if": {"properties": {"parameters": {"properties": {"mode": {"const": "circular"}},
                                                           "required": ["mode"]}}},
                    "then": {"properties": {"parameters": {"required": ["axis", "angle", "center"]}}},
                },
            ])
        numeric_param_keys = (set(spec["params"]) &
                              (_NUMERIC_SCALAR_KEYS | _LENGTH_ARRAY_KEYS | {"rotate"}))
        for param_key in sorted(numeric_param_keys):
            conditionals.append({
                "if": {
                    "required": ["parameters"],
                    "properties": {"parameters": {"required": [param_key]}},
                },
                "then": {
                    "required": ["sources"],
                    "properties": {"sources": {"required": [param_key]}},
                },
            })
        parameter_schema["required"] = conditional_required
        feature_alternatives.append({
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"const": kind, "x-catia-support-status": spec["status"],
                         "x-catia-support-note": spec["reason"]},
                "operation": {"type": "string", "enum": ["add", "cut"]},
                "name": strings,
                "note": strings,
                "enabled": {"type": "boolean", "default": True},
                "parameters": parameter_schema,
                "sources": {"type": "object",
                            "propertyNames": {"enum": list(spec["params"])},
                            "additionalProperties": {"$ref": "#/$defs/source"}},
                "relations": {"type": "array", "items": {"$ref": "#/$defs/relation"}},
            },
            "required": ["id", "kind", "operation", "parameters", "sources"],
            "additionalProperties": False,
            "allOf": conditionals,
        })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ai-modeler.local/schemas/cadplan-1.0.schema.json",
        "title": "AI Modeler CADPlan",
        "description": "Provider-neutral, data-only plan for validated CATIA V5 construction.",
        "type": "object",
        "properties": {
            "schema_version": {"const": CADPLAN_SCHEMA_VERSION},
            "name": {"type": "string", "minLength": 1},
            "source_prompt": strings,
            "units": {
                "type": "object",
                "properties": {
                    "length": {"type": "string", "enum": list(LENGTH_UNITS)},
                    "angle": {"type": "string", "enum": list(ANGLE_UNITS)},
                },
                "required": ["length", "angle"], "additionalProperties": False,
            },
            "coordinate_system": {
                "type": "object",
                "properties": {
                    "type": {"const": "global_cartesian"},
                    "handedness": {"const": "right"},
                    "axes": {"const": ["X", "Y", "Z"]},
                    "origin": {"const": [0, 0, 0]},
                },
                "required": ["type", "handedness", "axes", "origin"],
                "additionalProperties": False,
            },
            "features": {"type": "array", "minItems": 1,
                         "items": {"oneOf": feature_alternatives}},
            "pending_questions": {"type": "array", "items": strings},
        },
        "required": ["schema_version", "name", "units", "coordinate_system", "features"],
        "additionalProperties": False,
        "$defs": {
            "source": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["user", "image_estimate", "derived", "existing_model"]},
                    "evidence": strings,
                    "note": strings,
                },
                "required": ["type"], "additionalProperties": False,
                "allOf": [{
                    "if": {"properties": {"type": {"const": "image_estimate"}},
                           "required": ["type"]},
                    "then": {"required": ["evidence"]},
                }, {
                    "if": {"properties": {"type": {"const": "existing_model"}},
                           "required": ["type"]},
                    "then": {"required": ["note"]},
                }],
            },
            "relation": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["depends_on", "pattern_source"]},
                    "feature_id": {"type": "string", "minLength": 1},
                },
                "required": ["type", "feature_id"], "additionalProperties": False,
            },
        },
        "x-feature-support-matrix": support_matrix(),
    }


def write_schema(path: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema_document(), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return str(target)
