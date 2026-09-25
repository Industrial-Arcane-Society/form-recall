# -*- coding: utf-8 -*-
r"""
U4：意图纠错层 —— 纠正一次，同类表述**不再需要纠正**。

设计（对应升级文档 U4 的验收口径）：
    · 用户在「意图核对」面板上改掉某个值 / 切换某个代号后，落盘到
      `kb/intent_corrections.json`；
    · 下次**同类表述**直接命中同一条纠正；
    · 不训练、不联网 —— 纯本地的"微调"。

两条关键设计取舍（都是为了避免"纠正污染"）：
    1) **匹配键取"可泛化的骨架"**，不是原文。原文里带着具体数字
       （「多槽带轮 外径100 槽数3 A型」），照原文存键的话，换成外径120 就不命中了。
       所以键 = 零件类别 + 被纠正的参数名 + **该参数在原文里出现时的上下文词**
       （如"外径"/"槽型"），并把数字归一成占位符。
    2) **只覆盖被纠正的那一个参数**，其它参数一仍其旧 —— 纠正一个数不该把整条
       描述都钉死。

用法（库）：
    from ai_modeler.core import corrections as C
    C.record(cat, key, value, note=..., source_text=...)
    C.lookup(cat, text) -> {'槽型': 'B', ...}
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import io
import json
import os
import re
from typing import Dict, List, Optional

ROOT = str(PROJECT_ROOT)
KB = os.path.join(ROOT, "kb")
PATH = os.path.join(KB, "intent_corrections.json")

_DB: Optional[dict] = None


def _load() -> dict:
    global _DB
    if _DB is None:
        try:
            with io.open(PATH, encoding="utf-8") as f:
                _DB = json.load(f)
        except Exception:
            _DB = {"version": 1, "rules": []}
        _DB.setdefault("version", 1)
        _DB.setdefault("rules", [])
    return _DB


def reload():
    """测试/工具用：丢掉内存缓存，下次读取重新读盘。"""
    global _DB
    _DB = None


def rules() -> List[dict]:
    return _load()["rules"]


def save() -> None:
    os.makedirs(KB, exist_ok=True)
    with io.open(PATH, "w", encoding="utf-8") as f:
        json.dump(_load(), f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ 骨架
#: 匹配键里保留下来的"上下文词"：这些词能说明"用户是在给哪个参数赋值"。
#: 不保留具体数字 —— 保留数字就没法泛化到同类表述。
_CONTEXT_WORDS = (
    "外径", "内径", "节圆直径", "基准直径", "分度圆直径", "直径", "半径",
    "宽度", "轮宽", "厚度", "高度", "长度", "总长", "齿宽", "线径", "壁厚",
    "槽数", "齿数", "圈数", "辐条数", "模数", "槽型", "齿型", "型号", "带型",
    "自由高度", "螺纹规格", "对边", "头高", "深度", "跨距", "升程",
)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def skeleton(text: str) -> str:
    """把一段描述归一成"可泛化的骨架"：数字 -> #，只留下类别/上下文词。

    '多槽带轮 外径100 槽数3 A型' -> '多槽带轮|外径#|槽数#|型'
    """
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return ""
    # 先把上下文词抽出来（顺序保留），再去掉所有数字
    parts = []
    rest = t
    for w in sorted(_CONTEXT_WORDS, key=len, reverse=True):
        if w in rest:
            parts.append(w)
            rest = rest.replace(w, "\u0001")
    # 零件类别（去掉数字与上下文词之后剩下的汉字）
    cats = re.findall(r"[\u4e00-\u9fff]+", re.sub(r"[\u0001\d.]+", "|", rest))
    cats = [c for c in cats if len(c) >= 2]
    key = "|".join(cats[:2] + [p + "#" for p in parts])
    return key or _NUM_RE.sub("#", t)


def _norm_cat(cat) -> str:
    return re.sub(r"\s+", "", str(cat or ""))


# ------------------------------------------------------------------ 读写
def record(cat: str, param: str, value, source_text: str = "",
           note: str = "", span=None) -> dict:
    """记一条纠正。同一个 (类别, 骨架, 参数) 覆盖旧记录。"""
    sk = skeleton(source_text)
    rule = {
        "cat": _norm_cat(cat),
        "skeleton": sk,
        "param": str(param),
        "value": value,
        "note": note or "",
        "source_text": source_text or "",
        "span": list(span) if span else None,
    }
    db = _load()
    for i, r in enumerate(db["rules"]):
        if (r.get("cat") == rule["cat"] and r.get("skeleton") == sk
                and r.get("param") == rule["param"]):
            rule["hits"] = r.get("hits", 0)
            db["rules"][i] = rule
            save()
            return rule
    rule["hits"] = 0
    db["rules"].append(rule)
    save()
    return rule


def lookup(cat: str, text: str) -> Dict[str, object]:
    """查这类表述该套哪些纠正。返回 {参数名: 值}（可能为空）。"""
    sk = skeleton(text)
    if not sk:
        return {}
    c = _norm_cat(cat)
    out = {}
    for r in _load()["rules"]:
        if r.get("skeleton") != sk:
            continue
        # 类别相同才套；类别为空表示"通用于任何零件"
        if r.get("cat") and c and r["cat"] != c:
            continue
        out[r["param"]] = r.get("value")
    return out


def bump(text: str, cat: str = "") -> int:
    """记录一次命中（用于"纠正一次之后不再需要纠正"的统计）。"""
    sk = skeleton(text)
    c = _norm_cat(cat)
    n = 0
    for r in _load()["rules"]:
        if r.get("skeleton") != sk:
            continue
        if r.get("cat") and c and r["cat"] != c:
            continue
        r["hits"] = r.get("hits", 0) + 1
        n += r["hits"]
    if n:
        save()
    return n


def clear() -> None:
    db = _load()
    db["rules"] = []
    save()


def summary() -> str:
    rs = rules()
    if not rs:
        return "暂无纠正记录"
    lines = ["已记录 %d 条意图纠正：" % len(rs)]
    for r in rs:
        lines.append("  %s · %s -> %r（命中 %d 次）%s"
                     % (r.get("cat") or "通用", r.get("param"), r.get("value"),
                        r.get("hits", 0), ("｜" + r["note"]) if r.get("note") else ""))
    return "\n".join(lines)
