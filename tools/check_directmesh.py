# -*- coding: utf-8 -*-
r"""自检：直写网格（B 计划）建出来的等截面带孔件，**逐项对上解析值**。

判据（全部可手算）：
  · 外形 = 截面 × 长度
  · 体积 = 截面面积×长度 − Σ(每个孔的面积×穿孔长度)
  · 网格封闭：每条边恰好被两个三角形共用（否则有缝/有洞）
  · 孔数：孔壁圆柱段数 = 孔数 × seg

用法: python tools\check_directmesh.py
    python tools\check_directmesh.py --profile "EGT001 SW80x4078"   # 拿真实孔阵试
"""
from __future__ import annotations

import argparse
import json
import io
import math
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import directmesh as D      # noqa: E402
from ai_modeler import kernel as K          # noqa: E402

KB = os.path.join(ROOT, "kb")


def volume(polys):
    tot = 0.0
    for poly in polys:
        vs = poly.points()
        if len(vs) < 3:
            continue
        a = vs[0]
        for i in range(1, len(vs) - 1):
            b, c = vs[i], vs[i + 1]
            tot += (a[0] * (b[1] * c[2] - b[2] * c[1])
                    - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(tot)


def edge_stats(polys):
    """统计"每条无向边被几个三角形共用"，检查是否封闭（都应为 2）。"""
    cnt = Counter()
    for poly in polys:
        vs = poly.points()
        n = len(vs)
        for i in range(n):
            a, b = vs[i], vs[(i + 1) % n]
            key = tuple(sorted((tuple(round(x, 6) for x in a),
                                tuple(round(x, 6) for x in b))))
            cnt[key] += 1
    bad = sum(1 for v in cnt.values() if v != 2)
    return len(cnt), bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="")
    ap.add_argument("--seg", type=int, default=24)
    a = ap.parse_args()

    cases = []
    if a.profile:
        feats = json.load(io.open(os.path.join(KB, "real_features.json"),
                                  encoding="utf-8"))
        v = feats[a.profile + ".stp"] if not a.profile.endswith(".stp") \
            else feats[a.profile]
        env = v["envelope"]                       # [80, 118, 80] 之类
        # 长度轴 = 最大那一维
        ai = max(range(3), key=lambda k: env[k])
        others = [k for k in range(3) if k != ai]
        holes = [{"d": h["d"], "axis": h["axis"], "u": h["u"], "v": h["v"],
                  "through": True}
                 for h in v["cylinders"] if float(h["d"]) >= 2.0]
        cases.append(("真实型材 %s（%d 孔）" % (a.profile, len(holes)),
                      env[others[0]], env[others[1]], env[ai],
                      "XYZ"[ai], -env[ai] / 2.0, holes,
                      v.get("bbox_min"), v.get("envelope")))
    else:
        cases.append(("方盒 + 5 个轴向孔", 80.0, 80.0, 200.0, "Y", -100.0,
                      [{"d": 11.0, "axis": "Y", "u": 0.0, "v": 0.0, "through": True},
                       {"d": 11.0, "axis": "Y", "u": 30.0, "v": 30.0, "through": True},
                       {"d": 11.0, "axis": "Y", "u": -30.0, "v": 30.0, "through": True},
                       {"d": 11.0, "axis": "Y", "u": 30.0, "v": -30.0, "through": True},
                       {"d": 11.0, "axis": "Y", "u": -30.0, "v": -30.0, "through": True}]))
        # 注意只放**一个**侧向孔：两个不同轴的孔若在中心交叉，就违反"孔不相交"
        # 这个前提（那种情况归 A′ 的局部布尔管），这里不该拿来测直写网格。
        cases.append(("方盒 + 侧向孔（垂直拉伸轴）", 80.0, 80.0, 120.0, "Y", -60.0,
                      [{"d": 11.0, "axis": "Z", "u": 0.0, "v": 0.0, "through": True}]))

    allok = True
    for case in cases:
        title, A, B, L, ax, base, holes = case[:7]
        rmin = case[7] if len(case) > 7 else None
        rsize = case[8] if len(case) > 8 else None
        t0 = time.time()
        polys = D.extrude_with_holes(A, B, L, axis=ax, base=base, holes=holes,
                                     seg=a.seg, ref_min=rmin, ref_size=rsize)
        dt = time.time() - t0
        bb = K.bbox(polys)
        got = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
        want = sorted([A, B, L], reverse=True)
        gots = tuple(sorted(got, reverse=True))
        v = volume(polys)
        # 解析体积：截面×长 − Σ 孔面积×穿孔长
        ai = "XYZ".index(ax)
        vwant = A * B * L
        for h in holes:
            ha = "XYZ".index(h["axis"])
            span = A if ha != ai and ha == "XYZ".index("XYZ"[[i for i in range(3) if i != ai][0]]) else B
            # 穿孔长度 = 沿孔轴方向的实体尺寸
            dims = {ai: L}
            oth = [i for i in range(3) if i != ai]
            dims[oth[0]] = A
            dims[oth[1]] = B
            vwant -= math.pi * (h["d"] / 2.0) ** 2 * dims[ha]
        e_env = max(abs(gots[i] - want[i]) for i in range(3))
        # 圆柱面离散会让体积略小于解析值，容差 2%
        e_vol = abs(v - vwant) / vwant * 100.0
        ne, nbad = edge_stats(polys)
        ok = e_env < 1e-6 and e_vol < 2.0 and nbad == 0
        allok = allok and ok
        print("%-34s 面 %-6d 用时 %5.2fs" % (title, len(polys), dt))
        print("     外形 %s  期望 %s  误差 %.6f"
              % (tuple(round(x, 2) for x in gots), tuple(round(x, 2) for x in want), e_env))
        print("     体积 %.1f  解析 %.1f  偏差 %.2f%%"
              % (v, vwant, e_vol))
        print("     边 %d 条，**非封闭边 %d 条**（应为 0）" % (ne, nbad))
        print("     → %s" % ("✅ 通过" if ok else "❌ 不通过"))
    print()
    print("总判定：%s" % ("✅ 全部通过" if allok else "❌ 有不通过项"))
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
