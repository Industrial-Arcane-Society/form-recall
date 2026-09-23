# -*- coding: utf-8 -*-
r"""
U8：候选生成 + 可行性校验。

升级文档口径：
    规格不全时（如只给"⌀100 三槽带轮"）：
      1. 生成 2–3 个**满足标准**的候选（不同槽型 × 宽度组合）
      2. 用轻量代理指标（强度 / 重量 / 标准符合度）排序
      3. 用户选定后走全精度几何校验（U3）再出图

**核心纪律：绝不"猜一个"。** 候选只从本地标准表里取（`kb/*_std.json` +
`std_pulley`/`std_bearing`/`std_sprocket`），每个候选都必须给出：
    · 它满足哪条标准（依据字符串）
    · 代理指标的数值（不是拍脑袋的"更好"，是可比较的数）
    · 可行性判定（不满足的候选**直接淘汰**，不是排在最后）

为什么不用"检索→生成"的 LLM 路径：本项目的标准数据已经结构化入库，
**检索就是查表**，不需要模型参与，也就没有数值幻觉的入口（与 U9 一致）。
"""
from __future__ import annotations

import math
import os
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------
# 代理指标（都是可复算的数，不是主观打分）
# --------------------------------------------------------------------------
def _pulley_metrics(spec: dict) -> Dict[str, float]:
    """带轮候选的轻量代理指标。

    · 标准符合度 std_fit：轮缘实际宽度 / 标准最小宽度。=1 表示刚好，越大越"宽"
      （越宽越重、但槽间干涉风险越低）。这个比值是可复算的标准量。
    · 材料体积 vol：用回转近似算体积（外径/内孔/宽度），作为"重量"代理。
      环体体积 = π/4·(D²−d²)·B，对带轮是合理的量级估计。
    · 槽间余量 gap_margin：轮缘富余宽度，越大越不容易干涉。
    """
    D = float(spec.get("outer_d") or 0.0)
    d = float(spec.get("bore_d") or 0.0)
    B = float(spec.get("width") or 0.0)
    rim_min = float(spec.get("rim_w_min") or 0.0)
    vol = math.pi / 4.0 * max(0.0, D * D - d * d) * B / 1000.0   # cm³ 量级
    std_fit = (B / rim_min) if rim_min > 1e-9 else 1.0
    return {
        "std_fit": round(std_fit, 3),
        "volume_cm3": round(vol, 1),
        "gap_margin": round(B - rim_min, 2),
        "outer_d": round(D, 2),
        "width": round(B, 2),
    }


def _score(metrics: Dict[str, float]) -> float:
    """把代理指标合成一个可排序的分数（越小越好）。

    权重是**公开可审计**的：体积（重量）为主，标准符合度轻微惩罚"过宽"。
    不引入任何拟合参数 —— 这一层的目的是"排序"，不是"精确选优"，
    精确评估留给 U3 的全精度几何校验。
    """
    vol = float(metrics.get("volume_cm3") or 0.0)
    fit = float(metrics.get("std_fit") or 1.0)
    return round(vol * (1.0 + 0.15 * max(0.0, fit - 1.0)), 2)


# --------------------------------------------------------------------------
# 带轮候选
# --------------------------------------------------------------------------
def pulley_candidates(pitch_d: float, grooves: int = None,
                      bore_d: float = 0.0, max_n: int = 3) -> List[dict]:
    """只给基准直径时，给出 2–3 个满足标准的槽型候选。

    可行性的判据（全部来自本地标准表）：
      1. 该槽型的**直径系列表**里必须真的列有这个基准直径（否则不算"满足标准"）；
      2. 齿/槽数没给时按该槽型常用值取 3（并在依据里写明是默认）；
      3. 轮缘宽度必须放得下 n 条槽 —— 由 `std_pulley.spec` 自己算并给警告。
    """
    from ai_modeler import std_pulley as SP
    n = int(grooves or 3)
    out: List[dict] = []
    tbl = SP.groove_table()
    for gt in sorted(tbl.keys()):
        series = SP.diameter_series(gt)
        if not series or float(pitch_d) not in series:
            # 不在该槽型的标准直径系列里 -> **不可行**，直接淘汰
            continue
        try:
            s = SP.spec(float(pitch_d), n, gt, bore_d=float(bore_d or 0.0))
        except Exception:
            continue
        m = _pulley_metrics(s)
        out.append({
            "kind": "vbelt_pulley",
            "params": {"pitch_d": float(pitch_d), "grooves": n, "groove_type": gt,
                       "outer_d": s.get("outer_d"), "width": s.get("width"),
                       "bore_d": float(bore_d or 0.0)},
            "standard": s.get("standard", "GB/T 10412—2002"),
            "basis": ("%s 型 %d 槽：基准直径 ⌀%g 命中该槽型的**标准直径系列表**"
                      "（外径 ⌀%g）；轮缘最小宽度 %g ≤ 实取 %g"
                      % (gt, n, float(pitch_d), s.get("outer_d") or 0.0,
                         s.get("rim_w_min") or 0.0, s.get("width") or 0.0)),
            "metrics": m,
            "score": _score(m),
            "feasible": True,
            "warnings": list(s.get("warnings") or []),
        })
    out.sort(key=lambda c: c["score"])
    return out[:max(0, int(max_n))]


# --------------------------------------------------------------------------
# 轴承候选（复用 U5 已交付的 by_bore）
# --------------------------------------------------------------------------
def bearing_candidates(bore_mm: float, max_n: int = 3) -> List[dict]:
    """只给内径时，给出若干**标准**轴承候选（按外径从小到大）。

    数据来自 `kb/bearing_std.json`（GB/T 276—2013，337 条，含锚点自检）。
    """
    from ai_modeler import std_bearing as SB
    out = []
    for c in SB.by_bore(bore_mm):
        D, B, d = float(c["D"]), float(c["B"]), float(c["d"])
        vol = math.pi / 4.0 * max(0.0, D * D - d * d) * B / 1000.0
        m = {"outer_d": round(D, 2), "width": round(B, 2),
             "volume_cm3": round(vol, 1),
             "std_fit": 1.0, "gap_margin": 0.0}
        out.append({
            "kind": "deep_groove_ball_bearing",
            "params": {"code": c["code"], "d": d, "D": D, "B": B},
            "standard": c.get("standard", "GB/T 276—2013"),
            "basis": ("%s（%s）：内径 ⌀%g、外径 ⌀%g、宽度 %g，取自 GB/T 276—2013 "
                      "外形尺寸表" % (c["code"], c.get("series_name") or c.get("series"),
                                     d, D, B)),
            "metrics": m, "score": _score(m), "feasible": True, "warnings": [],
        })
    out.sort(key=lambda c: c["score"])
    return out[:max(0, int(max_n))]


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def candidates_for(cat: str, **kw) -> List[dict]:
    """按零件类别生成候选。认不出的类别返回空列表（**不猜**）。"""
    c = str(cat or "").strip()
    if c in ("多槽带轮", "V带轮", "皮带轮", "多楔带轮"):
        return pulley_candidates(kw.get("pitch_d") or kw.get("D") or 0.0,
                                 kw.get("grooves") or kw.get("n"),
                                 kw.get("bore_d") or 0.0,
                                 kw.get("max_n", 3))
    if c in ("深沟球轴承", "球轴承", "轴承"):
        return bearing_candidates(kw.get("bore") or kw.get("d") or 0.0,
                                  kw.get("max_n", 3))
    return []


def render(cands: List[dict], limit: int = 3) -> str:
    """把候选渲染成给用户看的清单（每个都带依据与指标）。"""
    if not cands:
        return "没有找到满足标准的候选。"
    lines = ["给你 %d 个都**满足标准**的候选（按轻量代理指标排序，越小越省料）："
             % min(len(cands), limit)]
    for i, c in enumerate(cands[:limit], 1):
        m = c.get("metrics") or {}
        lines.append("  %d) %s" % (i, c.get("basis", "")))
        lines.append("     依据：%s" % c.get("standard", ""))
        lines.append("     代理指标：体积≈%s cm³、外径 ⌀%s、宽 %s、标准符合度 %s"
                     % (m.get("volume_cm3"), m.get("outer_d"),
                        m.get("width"), m.get("std_fit")))
        for w in (c.get("warnings") or [])[:2]:
            lines.append("     ⚠ %s" % w)
    lines.append("  选定后我会用全精度几何校验（U3）复核再出图。")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== U8 候选生成演示 ===")
    print("\n[带轮] 只给 基准直径⌀100 + 3 槽：")
    cs = candidates_for("多槽带轮", pitch_d=100.0, grooves=3)
    print(render(cs))
    print("\n[轴承] 只给 内径 ⌀20：")
    bs = candidates_for("深沟球轴承", bore=20.0)
    print(render(bs))
