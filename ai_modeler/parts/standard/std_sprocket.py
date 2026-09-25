# -*- coding: utf-8 -*-
r"""
U5：滚子链链轮标准数据（GB/T 1243—2006）。

数据来源：`kb/sprocket_std.json`（由 `tools/extract_sprocket_std.py` 从机械设计手册
「滚子链的基本参数与尺寸(GB/T 1243—2006)」表抽取，含来源 URL 与锚点自检）。

本模块只做"查表 + 按标准公式算几何"，不做任何猜测：
    分度圆直径      d  = p / sin(180°/z)
    齿根圆直径      df = d − d1
    齿顶圆直径(简化) da ≈ p·(0.54 + cot(180°/z))
    齿沟圆弧半径    ra ≈ 0.5025·d1 + 0.05
查不到的链号**明确报错**（返回 None + 说明），绝不静默取近似值。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import math
import os
from typing import Dict, Optional

_KB = os.path.join(str(PROJECT_ROOT),
                   "kb", "sprocket_std.json")
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


def chains() -> Dict[str, dict]:
    return (load_db().get("chains") or {})


def normalize_chain(code) -> Optional[str]:
    """把用户写法归一：'08a'→'08A'、'8A'→'08A'、'12A-1'→'12A'。"""
    if not code:
        return None
    t = str(code).strip().upper().replace(" ", "")
    t = t.split("-")[0]
    if "-" in t or "/" in t:
        t = t.split("/")[0]
    tbl = chains()
    if t in tbl:
        return t
    # '8A' -> '08A'
    if len(t) >= 2 and t[0].isdigit() and t[1].isalpha():
        t2 = "0" + t
        if t2 in tbl:
            return t2
    return None


def chain_param(code) -> Optional[dict]:
    key = normalize_chain(code)
    return chains().get(key) if key else None


def spec(chain_code, teeth: int):
    """算出链轮的关键尺寸。查不到链号返回 None（由调用方提示用户）。"""
    info = chain_param(chain_code)
    if not info:
        return None
    z = max(6, int(teeth))
    p = float(info["p"])
    d1 = float(info["d1"])
    half = math.pi / z
    d = p / math.sin(half)                       # 分度圆直径
    df = d - d1                                  # 齿根圆直径
    da = p * (0.54 + 1.0 / math.tan(half))       # 齿顶圆直径（手册简化式）
    ra = 0.5025 * d1 + 0.05                      # 齿沟圆弧半径
    return {
        "chain": normalize_chain(chain_code), "teeth": z,
        "pitch": p, "roller_d": d1,
        "pitch_dia": d, "root_dia": df, "tip_dia": da, "groove_r": ra,
        "b1": info.get("b1"), "d2": info.get("d2"), "h2": info.get("h2"),
        "standard": "GB/T 1243—2006",
        # 齿宽：手册 bf1 = 0.93·b1（单排）；这里给单排值，双排需另乘系数
        "width": round(0.93 * float(info["b1"]), 2) if info.get("b1") else None,
    }


def summary(s: dict) -> str:
    return ("%s 号滚子链 %d 齿（%s）：节距 p=%g、滚子直径 d1=%g；"
            "分度圆 ⌀%.2f、齿根圆 ⌀%.2f、齿顶圆 ⌀%.2f"
            % (s["chain"], s["teeth"], s["standard"], s["pitch"], s["roller_d"],
               s["pitch_dia"], s["root_dia"], s["tip_dia"]))


def explain(s: dict) -> list:
    """提示词 → 生成特征 的对照说明（与带轮一致的可解释口径）。"""
    return [
        ("“%s”链号" % s["chain"],
         "节距 p=%g、滚子直径 d1=%g" % (s["pitch"], s["roller_d"]),
         "%s 链条基本参数表" % s["standard"]),
        ("“%d 齿”" % s["teeth"],
         "分度圆 ⌀%.2f = p / sin(180°/z)" % s["pitch_dia"],
         "链轮分度圆定义"),
        ("齿形",
         "齿根圆 ⌀%.2f = d − d1；齿顶圆 ⌀%.2f = p·(0.54+cot(180°/z))"
         % (s["root_dia"], s["tip_dia"]),
         "%s 链轮基本参数和主要尺寸" % s["standard"]),
        ("齿宽", "单排 0.93·b1 = %s" % s["width"],
         "%s 轴面齿廓尺寸（双排/多排需另乘系数）" % s["standard"]),
    ]
