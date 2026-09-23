# -*- coding: utf-8 -*-
r"""
U8 验收自检：候选生成 + 可行性校验。

升级文档口径：
    规格不全时给出 2–3 个**满足标准**的候选，用轻量代理指标排序，
    用户选定后再走全精度几何校验（U3）。
    关键纪律：**绝不"猜一个"**。

本脚本把它变成可复跑的断言：
  A) 规格不全（只给基准直径）-> 真的给出 2–3 个候选；
  B) 每个候选的数值都能追到**本地标准表**（不是生成出来的数字）；
  C) **不可行的候选被直接淘汰**（不在标准直径系列里的槽型不得出现）；
  D) 排序可复现（同一输入两次调用顺序完全一致）；
  E) 每个候选都带"依据 + 可复算的代理指标"，不是主观打分；
  F) 认不出的类别返回空（**不猜**）；
  G) 候选选定后可交给 U3 全精度校验（这里验证参数能建出几何）。

用法：python tools\check_candidates.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import candidates as CAND      # noqa: E402
from ai_modeler import std_pulley as SP        # noqa: E402
from ai_modeler import std_bearing as SB       # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    print("U8 候选生成 + 可行性校验 —— 验收自检")

    # ---------------------------------------------------------------- A
    print("\n[A] 规格不全 -> 给出 2–3 个候选")
    cs = CAND.candidates_for("多槽带轮", pitch_d=100.0, grooves=3)
    chk(2 <= len(cs) <= 3, "带轮候选数 = %d（要求 2–3）" % len(cs))
    bs = CAND.candidates_for("深沟球轴承", bore=20.0)
    chk(2 <= len(bs) <= 3, "轴承候选数 = %d（要求 2–3）" % len(bs))

    # ---------------------------------------------------------------- B
    print("\n[B] 候选数值必须能追到本地标准表")
    series_cache = {gt: SP.diameter_series(gt) for gt in SP.groove_table()}
    ok_b = True
    for c in cs:
        gt = c["params"]["groove_type"]
        ser = series_cache.get(gt) or {}
        d = c["params"]["pitch_d"]
        if d not in ser:
            ok_b = False
            print("      %s 型的系列表里没有 ⌀%g" % (gt, d))
        # 外径必须等于标准表值
        if abs(float(ser.get(d, -1)) - float(c["params"]["outer_d"])) > 1e-6:
            ok_b = False
            print("      %s 型 ⌀%g 的外径与系列表不符" % (gt, d))
    chk(ok_b, "带轮候选的基准直径/外径全部来自标准直径系列表")

    ok_b2 = all(SB.by_code(c["params"]["code"]) is not None for c in bs)
    chk(ok_b2, "轴承候选的代号全部能在 kb/bearing_std.json 里查到")

    # ---------------------------------------------------------------- C
    print("\n[C] 不可行的组合必须被直接淘汰（不是排在最后）")
    # ⌀20 太小：A/B/C 型都没有这个档，只应有 Z（以及可能的 Y）
    small = CAND.candidates_for("多槽带轮", pitch_d=20.0, grooves=3, max_n=99)
    gts = [c["params"]["groove_type"] for c in small]
    chk(all(d in (series_cache.get(g) or {}) 
            for g in gts for d in [20.0]),
        "返回的候选全部落在各自的标准直径系列里（实得 %s）" % gts)
    big = CAND.candidates_for("多槽带轮", pitch_d=500.0, grooves=3, max_n=99)
    chk(all(500.0 in (series_cache.get(c["params"]["groove_type"]) or {})
            for c in big),
        "大直径下返回的候选同样都在系列表里（实得 %d 个）" % len(big))
    # 一个绝对不存在的直径 -> 一个候选都不该有（宁可不给，也不猜）
    none = CAND.candidates_for("多槽带轮", pitch_d=123.456, grooves=3, max_n=99)
    chk(len(none) == 0, "⌀123.456 不在任何标准系列 -> 候选数 0（实得 %d）" % len(none))

    # ---------------------------------------------------------------- D
    print("\n[D] 排序必须可复现")
    a1 = CAND.candidates_for("多槽带轮", pitch_d=100.0, grooves=3)
    a2 = CAND.candidates_for("多槽带轮", pitch_d=100.0, grooves=3)
    chk(a1 == a2, "两次调用结果完全一致")
    chk([c["score"] for c in a1] == sorted(c["score"] for c in a1),
        "候选按代理指标升序排列（%s）" % [c["score"] for c in a1])

    # ---------------------------------------------------------------- E
    print("\n[E] 每个候选都要有依据 + 可复算的代理指标")
    need = {"std_fit", "volume_cm3", "gap_margin", "outer_d", "width"}
    ok_e = True
    for c in a1 + bs:
        m = c.get("metrics") or {}
        if not need.issubset(set(m)):
            ok_e = False
            print("      缺指标:", need - set(m))
        if not c.get("basis") or not c.get("standard"):
            ok_e = False
            print("      缺依据/标准号:", c.get("params"))
    chk(ok_e, "全部候选都带依据、标准号与完整代理指标")
    # 代理指标必须是可复算的（体积 = π/4·(D²−d²)·B）。
    # ⚠️ 注意 d 要用**有效内孔**：用户没写内孔时候选里 bore_d=0，
    #    但标准层会按 dd 的 0.22 倍补一个 —— 验算必须走同一条口径，
    #    否则会拿 0 去算体积而得出偏大的数（我第一版就是这么错的）。
    import math
    c0 = a1[0]
    p0 = c0["params"]
    eff = SP.spec(float(p0["pitch_d"]), int(p0["grooves"]), p0["groove_type"],
                  float(p0["width"]), bore_d=float(p0["bore_d"]))
    D, d, B = float(eff["outer_d"]), float(eff["bore_d"]), float(eff["width"])
    want = round(math.pi / 4.0 * (D * D - d * d) * B / 1000.0, 1)
    chk(abs(want - c0["metrics"]["volume_cm3"]) < 0.2,
        "体积指标可复算（期望 %s，实得 %s）" % (want, c0["metrics"]["volume_cm3"]))

    # ---------------------------------------------------------------- F
    print("\n[F] 认不出的类别 -> 返回空（不猜）")
    chk(CAND.candidates_for("没这个零件", pitch_d=100.0) == [],
        "未知类别返回空列表")

    # ---------------------------------------------------------------- G
    print("\n[G] 候选选定后能交给 U3 全精度校验（能建出几何）")
    try:
        from ai_modeler import kernel as K
        c0 = a1[0]
        p = c0["params"]
        # 签名是 (pitch_d, grooves, height, center, tag, groove_depth, seg,
        #         bore_d, groove_type, standard) —— 用关键字传，别按印象排位置
        polys = K.make_vbelt_pulley(pitch_d=float(p["pitch_d"]),
                                    grooves=int(p["grooves"]),
                                    height=float(p["width"]),
                                    groove_type=p["groove_type"],
                                    bore_d=float(p["bore_d"]), seg=32)
        chk(bool(polys), "候选能建出网格（%d 个面）" % len(polys))
        bb = K.bbox(polys)
        # ⚠️ bbox() 返回的是 (xmin,ymin,zmin,xmax,ymax,zmax)，不是三个尺寸 ——
        #    第一版我按"三个尺寸"取，读到的是负的最小值。
        if bb is not None:
            sx, sy, sz = bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]
            chk(abs(sz - float(p["width"])) < 0.5,
                "建出的宽度与候选一致（%.2f vs %.2f）" % (sz, float(p["width"])))
            chk(abs(sx - float(p["outer_d"])) < 0.6,
                "建出的外径与候选一致（%.2f vs %.2f）" % (sx, float(p["outer_d"])))
        else:
            chk(False, "bbox 为空")
    except Exception as e:
        chk(False, "候选建几何失败: %s" % e)

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
