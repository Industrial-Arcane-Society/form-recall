# -*- coding: utf-8 -*-
r"""
U1 核心保证：把"两段式"变成**可自动检查**的不变量。

升级文档 U1 的两条硬性要求：
    阶段 A（规划）：**禁止**出现任何数字字面量，所有数值必须是 `@引用`；
    阶段 B（构建）：**只认 ParamTable**，引用缺失 -> 不猜（走 U8 候选或提问）。

现状（如实）：取值与出处已经统一（`Dims.table`、`Feature.provenance`），
但解析仍是**单遍**执行 —— 直接大改成两遍风险极高（十几处调用、失败表现为几何静默错位）。
所以本轮做的是**可验证的部分**：

1. `check_feature_numbers()`：检查"构建出来的特征里，每个数值参数是否都能追到出处"。
   追不到 = 这个数字是构建期凭空产生的 = **违反 A/B 分离**。
2. `check_no_bare_numbers()`：检查"结构表达里有没有裸数字"。
3. `plan_from_features()`：把已建好的特征**反构**成一个只含引用的 plan
   （数值不与结构混在一起）。这是两段式的**数据模型**，为后续真正的两遍执行铺路。

这三条合起来的效果：**U1 的核心主张变成了每次回归都会检查的断言**，
而不是一句"我们设计成两段式"。
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: 不是"零件尺寸"的键：几何开关、内核参数、说明字段。
#: 它们**本来就没有出处**，检查时排除（否则会把"布尔开关"误报成"凭空产生的数"）。
NON_DIM_KEYS = {
    "center", "explain", "std_warnings", "axis", "name", "note", "tag",
    "kind", "op", "mode", "segments", "base", "pos", "through",
    "corner_mode", "enabled", "count_mode", "radius2", "symmetric", "full",
    "compound", "seg_per_turn", "wire_seg", "taper", "taper_start",
    "helix_deg", "twist", "lobes",
}


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float))


# --------------------------------------------------------------------------
# 1) 构建期检查：特征里的数值必须都能追到出处
# --------------------------------------------------------------------------
def check_feature_numbers(doc) -> Tuple[bool, List[str]]:
    """检查一个 Document（或特征列表）里，**每个数值参数是否都有出处**。

    返回 (是否全部可追溯, 追不到处的清单)。
    """
    feats = getattr(doc, "features", doc)
    if feats is None:
        return True, []
    bad: List[str] = []
    for f in feats:
        p = dict(getattr(f, "params", {}) or {})
        prov = dict(getattr(f, "provenance", {}) or {})
        for k, v in p.items():
            if k.startswith("__") or k in NON_DIM_KEYS:
                continue
            if not _is_number(v):
                continue
            if not prov.get(k):
                bad.append("%s.%s" % (getattr(f, "kind", "?"), k))
    return (not bad), bad


# --------------------------------------------------------------------------
# 2) 规划期检查：结构表达里不许出现裸数字
# --------------------------------------------------------------------------
def check_no_bare_numbers(plan) -> Tuple[bool, List[str]]:
    """检查一个 plan（结构表达）里有没有裸数字。

    plan 的形状与 `llm_plan` 一致：{"plan":[{"kind":..., "params":{k: 引用}}]}
    但这里**不限于** LLM 来的 plan —— 任何"结构表达"都适用。

    ⚠️ 与 `check_feature_numbers` 必须用**同一套口径**（`NON_DIM_KEYS`）：
    几何开关/内核参数（`center`、`segments`、`through`…）是**结构**，不是数值，
    它们本就该以字面量出现。早先这里不跳这些键，于是 `center:[0,0,0]` 被当成
    "规划期出现了裸数字"，每一条用例都误报 —— 口径不一致会让检查失去意义。
    """
    bad: List[str] = []

    def walk(node, path, key=None):
        # 结构/内核字段整体跳过（含其内部的数字）
        if key is not None and key in NON_DIM_KEYS:
            return
        if isinstance(node, dict):
            if "ref" in node:
                return
            for k, v in node.items():
                walk(v, "%s.%s" % (path, k), k)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i), key)
        elif _is_number(node):
            bad.append("%s = %r" % (path, node))
        return

    walk(plan, "plan")
    return (not bad), bad


# --------------------------------------------------------------------------
# 3) 从特征反构"只含引用"的 plan（两段式的数据模型）
# --------------------------------------------------------------------------
def plan_from_features(doc, text: str = "") -> dict:
    """把已建好的特征**反构**成只含引用的 plan。

    每个数值参数变成一个引用对象：
      · 有出处（explicit/standard/derived/default/asked）-> `{"ref": 出处}`
      · 没有出处 -> `{"ref": "unresolved"}`（**显式暴露**，不假装有）
    非尺寸的开关/枚举原样保留（它们是结构，不是数值）。

    这个 plan 的价值：
      · 数值与结构被分离，符合文档"阶段 A 不出现数字字面量"的形状；
      · 可以直接喂给 `check_no_bare_numbers()`，于是"规划期干净"成了可回归的断言。
    """
    feats = getattr(doc, "features", doc) or []
    out = []
    for f in feats:
        prov = dict(getattr(f, "provenance", {}) or {})
        params = {}
        for k, v in (getattr(f, "params", {}) or {}).items():
            if k.startswith("__"):
                continue
            if k in NON_DIM_KEYS or not _is_number(v):
                params[k] = v            # 结构/开关原样
            else:
                params[k] = {"ref": prov.get(k) or "unresolved"}
        out.append({"kind": getattr(f, "kind", "?"),
                    "op": getattr(f, "op", "add"),
                    "params": params})
    return {"plan": out, "source_text": text or ""}


def unresolved_of(plan: dict) -> List[str]:
    """列出 plan 里 ref 为 unresolved 的参数（= 没有出处的数值）。"""
    out = []
    for i, f in enumerate((plan or {}).get("plan") or []):
        for k, v in (f.get("params") or {}).items():
            if isinstance(v, dict) and v.get("ref") == "unresolved":
                out.append("[%d].%s.%s" % (i, f.get("kind"), k))
    return out


# --------------------------------------------------------------------------
# 4) 端到端检查：解析一段描述，检查 U1 的两条要求
# --------------------------------------------------------------------------
def check_parse(text: str) -> dict:
    """解析一句话并检查 U1 的两条要求。返回一份结构化报告。"""
    from ai_modeler import nlparser as NLP
    r = NLP.parse(text)
    ok_nums, bad_nums = check_feature_numbers(r.doc)
    plan = plan_from_features(r.doc, text)
    ok_bare, bad_bare = check_no_bare_numbers(plan)
    unres = unresolved_of(plan)
    return {
        "text": text,
        "n_features": len(r.doc.features),
        "construct_traceable": ok_nums,      # 构建期：数值都能追到出处
        "untraceable": bad_nums,
        "plan_clean": ok_bare,               # 规划期：结构表达里没有裸数字
        "bare_numbers": bad_bare,
        "unresolved": unres,
        "plan": plan,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== U1 两段式不变量检查 ===")
    for t in ("\u957f\u65b9\u4f53\u7684\u957f120 \u5bbd80 \u9ad815",
              "\u6cd5\u5170 \u5916\u5f84160 \u5185\u5f8460 \u539a20",
              "\u6df1\u6c9f\u7403\u8f74\u627f 6204",
              "\u591a\u69fd\u5e26\u8f6e \u5185\u5f8425 \u5916\u5f84100 \u5bbd\u5ea630 \u69fd\u65703 A\u578b"):
        rep = check_parse(t)
        print("\n%-42s 特征 %d" % (t[:42], rep["n_features"]))
        print("  构建期可追溯: %s  %s" % (rep["construct_traceable"],
                                          rep["untraceable"] or ""))
        print("  规划期无裸数字: %s  %s" % (rep["plan_clean"],
                                            rep["bare_numbers"] or ""))
        if rep["unresolved"]:
            print("  未解析的引用: %s" % rep["unresolved"])
