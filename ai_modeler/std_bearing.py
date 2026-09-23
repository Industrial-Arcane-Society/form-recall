# -*- coding: utf-8 -*-
r"""
U5：深沟球轴承标准尺寸（GB/T 276—2013）。

数据来源：`kb/bearing_std.json`（由 `tools/extract_bearing_std.py` 从机械设计手册
「常用滚动轴承尺寸及性能参数 → 深沟球轴承(GB/T 276—2013)」表抽取，
含来源 URL、337 条代号与 15 项公开标准值锚点自检）。

本模块只做"查表 + 明确的缺档处理"，不做任何猜测：
    · 查到代号  → 直接给标准 d/D/B；
    · 只给内径  → 在标准表里找该内径下的**代号与尺寸**（可能有多个，全部列出，
                  由调用方/用户决定，绝不替用户挑一个）；
    · 查不到    → 返回 None + 说明，由上层提示，绝不静默取近似值。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

_KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "kb", "bearing_std.json")
_DB: Optional[dict] = None


def load_db() -> dict:
    global _DB
    if _DB is None:
        try:
            with open(_KB, encoding="utf-8") as f:
                _DB = json.load(f)
        except Exception:
            _DB = {}
    return _DB


def bearings() -> Dict[str, dict]:
    return (load_db().get("bearings") or {})


def series_note() -> Dict[str, str]:
    return (load_db().get("series_note") or {})


def standard() -> str:
    return load_db().get("standard") or "GB/T 276—2013"


def normalize_code(code) -> Optional[str]:
    """把用户写法归一：'6204'、' 6204 '、'6204/P5'→'6204'、'617/0.6'。

    容忍：小写、空格、公差/游隙后缀（/P5、/C3、-2RS、ZZ）、
    "GB/T276 6204"这类前缀文字。
    """
    if code is None:
        return None
    t = str(code).strip().upper().replace(" ", "")
    if not t:
        return None
    # 去掉常见后缀：/P0 /P5 /C3 -2RS -ZZ 等
    t = t.split("/P")[0].split("/C")[0]
    for suf in ("-2RS", "-2RZ", "-ZZ", "-Z", "-RS", "-N", "-NR", "ZZ", "2RS"):
        if t.endswith(suf) and not t.endswith("/" + suf):
            t = t[: -len(suf)]
            break
    tbl = bearings()
    if t in tbl:
        return t
    # "GB/T2766204" / "轴承6204" 这类带前缀的写法 -> 取尾部**恰好 4 或 5 位**的数字段
    # （⚠️ 不能写 `\d{4,5}$`：它会先把 "GB/T2766305" 吃成 "66305"）
    import re
    m = re.search(r"(\d{5}|\d{4})$", t)
    if m and m.group(1) in tbl:
        return m.group(1)
    # 分数内径写法可能被上面的正则截断，再兜一次
    m = re.search(r"((?:617|618|619)/\d+(?:\.\d+)?)$", t)
    if m and m.group(1) in tbl:
        return m.group(1)
    return None


def by_code(code) -> Optional[dict]:
    """按代号查标准尺寸。查不到返回 None。"""
    key = normalize_code(code)
    if not key:
        return None
    v = bearings().get(key)
    if not v:
        return None
    out = dict(v)
    out["code"] = key
    out["standard"] = standard()
    out["series_name"] = series_note().get(v.get("series") or "", v.get("series"))
    return out


def by_bore(bore_mm) -> List[dict]:
    """按内径查**所有**标准代号（同一内径有多个系列，全部返回，不替用户挑）。"""
    try:
        d = float(bore_mm)
    except (TypeError, ValueError):
        return []
    out = []
    for code, v in bearings().items():
        if abs(float(v["d"]) - d) < 1e-6:
            item = dict(v)
            item["code"] = code
            item["standard"] = standard()
            item["series_name"] = series_note().get(v.get("series") or "", v.get("series"))
            out.append(item)
    # 按外径从小到大排：通常外径越小越紧凑，排前面便于用户比较
    out.sort(key=lambda x: (float(x["D"]), x["code"]))
    return out


def spec(code=None, bore=None, prefer_series=None):
    """拿到一套标准尺寸。

    · 给 code       → 直接返回该代号的 d/D/B；
    · 只给 bore     → 返回该内径下的**候选清单**（candidates），
                      若 prefer_series 命中某个系列则直接选定它；
    · 都查不到      → {'error': ...}，由上层原样展示给用户。
    """
    if code:
        got = by_code(code)
        if got:
            return dict(got, source="code")
        if not bore:
            return {"error": "标准表里没有代号 %r（GB/T 276—2013 深沟球轴承共 %d 条）"
                             % (code, len(bearings()))}
    if bore:
        cands = by_bore(bore)
        if not cands:
            return {"error": "标准表里没有内径 %s mm 的深沟球轴承"
                             % bore}
        if prefer_series:
            for c in cands:
                if c["series"] == str(prefer_series) or c["code"].startswith(str(prefer_series)):
                    return dict(c, source="bore+series")
        if len(cands) == 1:
            return dict(cands[0], source="bore")
        return {"candidates": cands, "source": "bore",
                "note": "内径 %s mm 有 %d 个标准系列，请择一（未替你选）"
                        % (bore, len(cands))}
    return {"error": "既没有代号也没有内径，无法查标准表"}


def summary(s: dict) -> str:
    if s.get("error"):
        return s["error"]
    return ("%s（%s，%s）：内径 d=%g、外径 D=%g、宽度 B=%s mm"
            % (s["code"], s.get("series_name") or s.get("series"), s["standard"],
               s["d"], s["D"], ("%g" % s["B"]) if s.get("B") is not None else "—"))


def explain(s: dict) -> list:
    """提示词 → 生成特征 的对照说明（与带轮/链轮一致的可解释口径）。"""
    if s.get("error"):
        return [("轴承代号", s["error"], "GB/T 276—2013 标准尺寸表")]
    out = [("“%s”轴承代号" % s["code"],
            "内径 d=%g、外径 D=%g、宽度 B=%s"
            % (s["d"], s["D"], ("%g" % s["B"]) if s.get("B") is not None else "—"),
            "%s 外形尺寸表（%s）" % (s["standard"], s.get("series_name") or s.get("series")))]
    if s.get("source") == "bore":
        out.append(("只写了内径 d=%g" % s["d"],
                    "按标准表定为 %s（同内径仅此一个系列）" % s["code"],
                    s["standard"]))
    return out
