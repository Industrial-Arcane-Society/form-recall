# -*- coding: utf-8 -*-
r"""
U9：LLM 只出**结构**，不出数。

升级文档的口径：
    LLM 只做两件事 —— ① 意图理解（口语 → 哪个零件、哪些特征）；
                        ② 结果解释（把校验报告翻成人话）。
    **绝不做算术、绝不生成数值**（通用 LLM 在参数精度上不可靠，数值幻觉会直接
    变成错误几何，而且不可复现）。

因此这里定义的 plan 格式里，**每个数值都必须是一个"引用"**：
    {"ref": "text", "identifier": "直径"}   引用原文里"哪个参数"的值（**不写数字**）
    {"ref": "std", "table": "bearing_std", "key": "6204", "field": "D"}
                                           到本地标准表里查
    {"ref": "formula", "name": "da = dd + 2*ha"}
                                           由本地公式求值
    {"ref": "default"}                     用零件库典型值
    {"ref": "ask", "question": "..."}      信息不足 -> 提问，不猜

⚠️ **为什么是"参数名"而不是"原文位置 span"**（这是实测踩出来的）：
   让模型给字符区间不可行 —— 它数不准。实测让它引用 `"圆柱 直径60 高120"`
   里的直径，它给出 span [4,6]（其实指向 "6"），于是半径成了 6.0 而不是 30。
   况且"直径60 → 半径30"本身就是**算术**，正是不该让模型碰的东西。
   所以模型只负责说"这个参数取自原文的『直径』"，**取数与换算全部留给本地**。

`validate_plan()` 会把任何**裸数字**（plan 里直接写 100 而不是引用）判为违规。
`resolve_plan()` 才去本地表/公式里把值取出来 —— 数值的最终来源永远是本地。

这样做的收益是可复现性：**同一个 plan 在任何时候解析出的数值都一样**，
LLM 换版本、温度变化都不会让几何跟着变。
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: 允许出现的引用类型
REF_KINDS = ("text", "std", "formula", "default", "ask")

#: 特征参数里允许"是结构、不是数值"的键（布尔开关、枚举、坐标中心由本地算）
NON_NUMERIC_KEYS = {
    "op", "kind", "name", "note", "axis", "pos", "mode", "through", "base",
    "corner_mode", "enabled", "ref", "question", "table", "key", "field",
    "formula", "identifier", "span", "compound", "full", "symmetric", "count_mode",
}

PLAN_SYSTEM_PROMPT = '''你是一个 CAD **结构**解析器。你的唯一任务是：把用户的中文建模需求
映射成"要建哪些特征、按什么顺序、用什么方式连接"，**绝对不要做任何算术，也不要自己编造数值**。

严格输出 JSON，格式: {"plan": [ ... ]}
每个元素形如:
{"kind": "box", "op": "add",
 "params": {"length": {"ref":"text","identifier":"长"},
            "width":  {"ref":"text","identifier":"宽"},
            "height": {"ref":"text","identifier":"高"}}}
{"kind": "cylinder", "op": "add",
 "params": {"radius": {"ref":"text","identifier":"直径"},
            "height": {"ref":"text","identifier":"高"}}}
{"kind": "hole", "op": "cut",
 "params": {"diameter": {"ref":"text","identifier":"直径"}, "pos": "center", "through": true}}
{"kind": "vbelt_pulley", "op": "add",
 "params": {"D": {"ref":"text","identifier":"外径"},
            "n": {"ref":"text","identifier":"槽数"},
            "t": {"ref":"text","identifier":"槽型"}}}
{"kind": "deep_groove_ball_bearing", "op": "add",
 "params": {"code": {"ref":"std","table":"bearing_std","key":"6204"}}}

**每个数值参数必须是引用对象，绝不能直接写数字。** 可用引用:
  {"ref":"text", "identifier":"参数名"}   该参数的值取自原文里这个名字对应的数
                                          （参数名用中文：长/宽/高/直径/内径/外径/
                                            槽数/齿数/宽度/长度/厚度/模数/圈数…）
  {"ref":"std",  "table":表名, "key":键, "field":字段}   到本地标准表查
  {"ref":"formula", "name":"公式名"}                     由本地公式求值
  {"ref":"default"}                                      用零件库典型值
  {"ref":"ask", "question":"要问用户的话"}                 信息不足时提问，**不要猜**

规则:
- **不要输出任何数字**，也不要自己算 —— "直径60 要写成半径30" 这类算术一律不许做。
- **不要给字符位置**（你数不准），只要说明这个值对应哪个参数名。
- 只输出结构（kind / op / pos / axis / through 这类枚举与开关），数值一律用上面的引用。
- 不要做单位换算 —— 原文写 mm 就是 mm，本地会处理。
- 不要输出任何多余文字或 markdown 代码块。
- 无法识别时输出 {"plan": []}。
'''


# --------------------------------------------------------------------------
# 校验：plan 里不许出现裸数字
# --------------------------------------------------------------------------
def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float))


def _is_ref(v) -> bool:
    return isinstance(v, dict) and v.get("ref") in REF_KINDS


def validate_plan(plan) -> Tuple[bool, List[str]]:
    """检查 plan 是否符合"只出结构不出数"。

    返回 (是否合规, 问题列表)。任何**裸数字**都会被判定违规。
    """
    errs: List[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            if "ref" in node:
                kind = node.get("ref")
                if kind not in REF_KINDS:
                    errs.append("%s: 未知引用类型 %r（允许 %s）"
                                % (path, kind, "/".join(REF_KINDS)))
                elif kind == "text":
                    if not node.get("identifier"):
                        errs.append("%s: text 引用缺少 identifier（参数名）" % path)
                elif kind == "std":
                    for k in ("table", "key"):
                        if not node.get(k):
                            errs.append("%s: std 引用缺少 %s" % (path, k))
                elif kind == "ask":
                    if not node.get("question"):
                        errs.append("%s: ask 引用缺少 question" % path)
                return
            for k, v in node.items():
                walk(v, "%s.%s" % (path, k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))
        elif _is_number(node):
            errs.append("%s: **裸数字 %r** —— plan 里必须用引用，数值由本地求值"
                        % (path, node))
        elif isinstance(node, str) or node is None or isinstance(node, bool):
            # 字符串/None/布尔在"数值参数"位置上都是违规（枚举位置已在调用处排除）
            errs.append("%s: 数值参数必须是引用对象（实得 %r）" % (path, node))

    if not isinstance(plan, dict):
        return False, ["plan 必须是对象"]
    feats = plan.get("plan", plan.get("features"))
    if feats is None:
        return False, ["plan 里没有 plan/features 字段"]
    if not isinstance(feats, list):
        return False, ["plan 必须是数组"]
    for i, f in enumerate(feats):
        if not isinstance(f, dict) or not f.get("kind"):
            errs.append("[%d]: 缺少 kind" % i)
            continue
        for k, v in (f.get("params") or {}).items():
            if k in NON_NUMERIC_KEYS:
                # 枚举/开关：允许字符串/布尔，但不允许数字
                if _is_number(v):
                    errs.append("[%d].%s: 结构字段不该是数字 %r" % (i, k, v))
                continue
            # ⚠️ 每个参数值都要交给 walk 检查 —— 包括"看着不像引用"的那些。
            #    早先写成 `if not _is_ref(v): 报错 else: walk(v)`，于是
            #    {"ref":"telepathy"} 这类**引用类型写错**的值走的是 else 分支以外，
            #    反而不会被 walk 检查到（实测 4 类非法引用一个都没抓出来）。
            before = len(errs)
            walk(v, "[%d].%s" % (i, k))
            if len(errs) == before:
                # walk 没报错时再补一条"必须是引用"的总判据
                if not _is_ref(v):
                    errs.append("[%d].%s: 数值参数必须是引用对象（实得 %r）"
                                % (i, k, v))
    return (not errs), errs


# --------------------------------------------------------------------------
# 求值：把引用落到本地的数
# --------------------------------------------------------------------------
def _slice_text(text: str, span) -> Optional[str]:
    try:
        a, b = int(span[0]), int(span[1])
    except Exception:
        return None
    if a < 0 or b <= a or b > len(text or ""):
        return None
    return text[a:b]


def _resolve_text_ref(ref: dict, text: str):
    """把 {"ref":"text","identifier":X} 落到具体数值 —— **取数在本地做**。

    用本地的 `nlparser.extract_dims()` 解析原文，这是唯一的取值实现；
    模型只提供"参数名"这个线索。这样：
      · 模型不需要（也做不到）数准字符位置；
      · "直径60 → 半径30" 这类换算发生在本地几何层，模型碰不到算术。

    （保留 span 分支只为兼容旧格式：若给了 span 就按 span 取，并按 U1 的
      出处表核对；但**推荐用 identifier**。）
    """
    ident = str((ref or {}).get("identifier") or "").strip()
    try:
        from ai_modeler.nlparser import extract_dims
        d = extract_dims(text or "")
    except Exception as e:
        return None, "本地取数失败: %s" % e
    if not ident:
        seg = _slice_text(text, (ref or {}).get("span"))
        if seg is None:
            return None, "text 引用缺少 identifier（参数名）"
        m = re.search(r"-?\d+(?:\.\d+)?", seg)
        if not m:
            return None, "text 引用的片段里没有数字: %r" % seg
        return float(m.group(0)), "explicit"
    field = _IDENT_TO_FIELD.get(ident)
    if field is None and ident in _ALLOW_FIELDS:
        field = ident
    if field is None:
        return None, "不认识的参数名 %r" % ident
    v = getattr(d, field, None)
    if v is None:
        return None, "原文里没找到参数「%s」" % ident
    return v, (d.provenance_of(field) or "explicit")


#: 参数名（identifier）-> 通用形状 Dims 的字段名。
_IDENT_TO_FIELD = {
    "长": "length", "长度": "length", "长边": "length", "总长": "length", "全长": "length",
    "宽": "width", "宽度": "width", "宽边": "width",
    "高": "height", "高度": "height", "厚": "height", "厚度": "height",
    "直径": "diameter", "外径": "diameter", "大径": "diameter",
    "内径": "inner_diameter", "孔径": "inner_diameter", "小径": "inner_diameter",
    "内孔": "inner_diameter", "中心孔": "inner_diameter",
    "半径": "radius",
    "齿数": "teeth", "槽数": "count", "个数": "count", "数量": "count",
    "模数": "module", "圆角": "corner", "倒角": "corner",
    "内缩": "margin", "边距": "margin", "深度": "depth", "深": "depth",
    "壁厚": "wall", "间距": "spacing", "分度圆": "pcd", "节圆": "pcd",
    "叶片数": "blades", "叶展": "span",
}
_ALLOW_FIELDS = set(_IDENT_TO_FIELD.values()) | {"teeth", "blades", "xy"}


def resolve_ref(ref: dict, text: str = "", cat: str = ""):
    """把一个引用求成具体的值。返回 (值, 出处)。求不出来返回 (None, 原因)。"""
    kind = (ref or {}).get("ref")
    if kind == "text":
        return _resolve_text_ref(ref, text)
    if kind == "std":
        tbl = str(ref.get("table") or "")
        key = str(ref.get("key") or "")
        field = str(ref.get("field") or "")
        try:
            if tbl in ("bearing_std", "std_bearing"):
                from ai_modeler import std_bearing as SB
                got = SB.by_code(key)
                if not got:
                    return None, "标准表里没有轴承代号 %r" % key
                return (got.get(field) if field else got), "standard"
            if tbl in ("sprocket_std", "std_sprocket"):
                from ai_modeler import std_sprocket as SS
                got = SS.chain_param(key)
                if not got:
                    return None, "标准表里没有链号 %r" % key
                return (got.get(field) if field else got), "standard"
        except Exception as e:
            return None, "查标准表失败: %s" % e
        return None, "未知标准表 %r（可用 bearing_std / sprocket_std）" % tbl
    if kind == "default":
        try:
            from ai_modeler import partlib as PL
            tv = (PL.TYPICAL or {}).get(cat) or {}
            return tv, "default"
        except Exception as e:
            return None, "取典型值失败: %s" % e
    if kind == "formula":
        return None, "公式 %r 需要本地公式求值（尚未登记）" % ref.get("name")
    if kind == "ask":
        return None, ref.get("question") or "信息不足，需要用户补充"
    return None, "未知引用类型 %r" % kind


def resolve_plan(plan: dict, text: str = "", cat: str = "") -> Tuple[list, List[str], List[str]]:
    """把 plan 落到具体数值。返回 (特征列表, 问题列表, 错误列表)。

    ⚠️ 这里**只做搬运**：数值要么来自原文原样、要么来自本地标准表。
    不在这里做任何算术（换算/推导留给 partlib 与 std_* 模块）。
    """
    ok, errs = validate_plan(plan)
    if not ok:
        return [], [], errs
    feats, asks, bad = [], [], []
    for i, f in enumerate(plan.get("plan") or plan.get("features") or []):
        params = {}
        for k, v in (f.get("params") or {}).items():
            if k in NON_NUMERIC_KEYS:
                params[k] = v
                continue
            val, why = resolve_ref(v, text, cat)
            if val is None:
                if isinstance(v, dict) and v.get("ref") == "ask":
                    asks.append(why)
                else:
                    bad.append("[%d].%s: %s" % (i, k, why))
                continue
            params[k] = val
        feats.append({"kind": f.get("kind"), "op": f.get("op", "add"),
                      "params": params})
    return feats, asks, bad


# --------------------------------------------------------------------------
# 结果解释（U9 的第二件事）
# --------------------------------------------------------------------------
def explain_report(report, max_items: int = 12) -> str:
    """把校验报告翻成人话（LLM 可做这件事；数值仍来自本地报告）。"""
    lines = []
    try:
        items = getattr(report, "results", None) or []
    except Exception:
        items = []
    for r in items[:max_items]:
        name = getattr(r, "name", "?")
        ok = getattr(r, "ok", True)
        want = getattr(r, "want", None)
        got = getattr(r, "got", None)
        tol = getattr(r, "tol", None)
        if ok:
            lines.append("「%s」合格（要求 %s，实测 %s）" % (name, want, got))
        else:
            lines.append("「%s」**超差**：要求 %s，实测 %s，公差 %s" % (name, want, got, tol))
    if not lines:
        return "没有可报告的校验项。"
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== U9 plan 校验演示 ===")
    good = {"plan": [{"kind": "cylinder", "op": "add",
                      "params": {"radius": {"ref": "text", "identifier": "\u76f4\u5f84"},
                                 "height": {"ref": "text", "identifier": "\u9ad8"}}}]}
    bad = {"plan": [{"kind": "cylinder", "op": "add",
                     "params": {"radius": 30, "height": 120}}]}
    for tag, p in (("\u5408\u89c4 plan", good), ("\u8fdd\u89c4 plan\uff08\u88f8\u6570\u5b57\uff09", bad)):
        ok, errs = validate_plan(p)
        print("%s -> %s" % (tag, "\u5408\u89c4 \u2705" if ok else "\u8fdd\u89c4 \u274c"))
        for e in errs:
            print("    ", e)
    for t in ("\u5706\u67f1 \u76f4\u5f8460 \u9ad8120",
              "\u957f\u65b9\u4f53\u7684\u957f120 \u5bbd80 \u9ad815"):
        print("\n\u539f\u6587:", t)
        print("  \u6c42\u503c:", resolve_plan(good, t))
