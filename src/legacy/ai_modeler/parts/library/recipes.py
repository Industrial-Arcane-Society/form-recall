# -*- coding: utf-8 -*-
"""Local modeling recipes and deterministic builders for common CAD parts."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from ai_modeler.paths import PROJECT_ROOT


_RECIPE_PATH = PROJECT_ROOT / "kb" / "modeling_recipes.json"
_NORMALIZE_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("×", "x").replace("✕", "x").replace("＊", "x").replace("*", "x")
    return _NORMALIZE_RE.sub("", text)


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_RECIPE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取建模配方库 %s: %s" % (_RECIPE_PATH, exc)) from exc
    if data.get("schema_version") != "1.0" or not isinstance(data.get("recipes"), list):
        raise RuntimeError("建模配方库格式无效：%s" % _RECIPE_PATH)
    return [row for row in data["recipes"] if isinstance(row, dict)]


def list_recipes(query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Search concise, editable recipe cards by name/alias/intent."""
    rows = _load()
    needle = _normalize(query)
    if not needle:
        return rows[:max(1, min(int(limit), 20))]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        aliases = [row.get("name", ""), row.get("intent", ""), *row.get("aliases", [])]
        normalized = [_normalize(alias) for alias in aliases if alias]
        score = 0
        for alias in normalized:
            if alias and alias in needle:
                score = max(score, 100 + len(alias))
            elif needle and needle in alias:
                score = max(score, 60 + len(needle))
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in ranked[:max(1, min(int(limit), 20))]]


def build_recipe_plan(recipe_id: str, size: str, *, name: str = "",
                      source_prompt: str = "") -> dict[str, Any]:
    """Build an exact CADPlan for a standard socket-head cap screw.

    The screw is a single native Document feature backed by the local mesh
    kernel. ISO/GB dimensions and coarse pitch come from the maintained table;
    the local mesh builds a visible helical approximation across the shank.
    """
    if recipe_id != "fastener.socket_head_cap_screw":
        raise ValueError("配方 %s 当前只有检索说明，暂无可执行的直接建模器。" % recipe_id)
    from ai_modeler.parts import std_bolt

    spec = std_bolt.spec_from_text(size)
    if not spec or not spec.get("length"):
        available = ", ".join(sorted(std_bolt.bolts()))
        raise ValueError("无法从 %r 识别标准螺钉规格和长度；请使用 M5x8 格式，且规格须在标准表内（%s）。"
                         % (size, available))
    if spec.get("length", 0) <= 0:
        raise ValueError("螺钉长度必须大于 0。")

    model = "%s×%g" % (spec["spec"], spec["length"])
    standard = spec["standard"]
    part_name = name.strip() or ("内六角圆柱头螺钉 %s" % model)
    thread_note = ("公制右旋螺纹 %s，螺距 %g mm；本地网格已生成可见的近似螺旋牙型。"
                   "CATIA 导出器当前仍输出光杆。" % (spec["spec"], spec["p"]))
    length_note = ("长度不在标准长度系列内，仍按用户给定的 %g mm 建模；最近标准长度为 %g mm。" %
                   (spec["length"], spec["length_not_standard"])
                   if spec.get("length_not_standard") else "")
    feature_id = "socket-head-cap-screw"
    parameters = {
        "shank_d": spec["thread_d"],
        "thread_pitch": spec["p"],
        "length": spec["length"],
        "head_d": spec["head_d"],
        "head_h": spec["head_h"],
        "hex_s": spec["hex_s"],
        "hex_t": spec["hex_t"],
        "center": [0.0, 0.0, 0.0],
        "segments": 48,
    }
    sources = {
        "shank_d": {"type": "user", "evidence": "用户规格 %s 中的公称直径。" % model},
        "thread_pitch": {"type": "derived", "evidence": "%s 标准表 kb/bolt_std.json 中 %s 的粗牙螺距。"
                         % (standard, spec["spec"])},
        "length": {"type": "user", "evidence": "用户规格 %s 中的螺杆长度。" % model},
        "head_d": {"type": "derived", "evidence": "%s 标准表 kb/bolt_std.json。" % standard},
        "head_h": {"type": "derived", "evidence": "%s 标准表 kb/bolt_std.json。" % standard},
        "hex_s": {"type": "derived", "evidence": "%s 标准表 kb/bolt_std.json。" % standard},
        "hex_t": {"type": "derived", "evidence": "%s 标准表 kb/bolt_std.json。" % standard},
        "center": {"type": "derived", "note": "默认将零件轴线放在全局原点。"},
        "segments": {"type": "derived", "note": "圆柱网格离散精度，不影响标称尺寸。"},
    }
    return {
        "schema_version": "1.0",
        "name": part_name,
        "source_prompt": source_prompt or ("标准件配方：内六角圆柱头螺钉 %s" % model),
        "units": {"length": "mm", "angle": "deg"},
        "coordinate_system": {
            "type": "global_cartesian", "handedness": "right",
            "axes": ["X", "Y", "Z"], "origin": [0, 0, 0],
        },
        "features": [{
            "id": feature_id,
            "kind": "socket_head_bolt",
            "operation": "add",
            "name": "内六角圆柱头螺钉 %s" % model,
            "note": "%s；%s%s" % (standard, thread_note,
                                     (" " + length_note) if length_note else ""),
            "parameters": parameters,
            "sources": sources,
        }],
        "pending_questions": [],
    }
