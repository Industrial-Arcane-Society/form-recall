# -*- coding: utf-8 -*-
r"""B 计划开工前的关键验证：**型材的孔互相交叉吗？**

为什么这一步必须先做：
    "B 直写网格"的思路是 —— 不用布尔，直接拼三角形：端面开孔 + 侧面开孔 +
    每个孔一段圆柱壁。这个做法**成立的前提是"孔与孔不相交"**：
    一旦两孔相交，两段圆柱壁就会互相穿插，拼出来的网格不是合法实体。

所以先把 289 个型材的真实孔两两判一遍相交：
    两个圆柱（半径 r1/r2、轴线方向 a1/a2、轴线位置 p1/p2）相交 ⟺
    两轴线的最短距离 d < r1 + r2（且轴向范围有重叠）。

用法: python tools\check_profile_holes.py
输出: reports\型材孔相交分析.md
"""
from __future__ import annotations

import io
import json
import math
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")


def axis_vec(axis):
    return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0)}[axis]


def seg_dist(p1, d1, p2, d2):
    """两轴线（无限长）的最短距离。d1/d2 为单位轴向量。"""
    w = [p2[i] - p1[i] for i in range(3)]
    a = sum(d1[i] * d1[i] for i in range(3))
    b = sum(d1[i] * d2[i] for i in range(3))
    c = sum(d2[i] * d2[i] for i in range(3))
    d = sum(d1[i] * w[i] for i in range(3))
    e = sum(d2[i] * w[i] for i in range(3))
    den = a * c - b * b
    if abs(den) < 1e-12:                     # 平行
        # 点到直线的距离
        proj = [p1[i] + d1[i] * d for i in range(3)]
        return math.sqrt(sum((p2[i] - proj[i]) ** 2 for i in range(3)))
    s = (b * e - c * d) / den
    t = (a * e - b * d) / den
    q1 = [p1[i] + d1[i] * s for i in range(3)]
    q2 = [p2[i] + d2[i] * t for i in range(3)]
    return math.sqrt(sum((q1[i] - q2[i]) ** 2 for i in range(3)))


def point_of(h):
    """把 (axis,u,v) 还原成三维轴线上的一个点。"""
    ai = "XYZ".index(h["axis"])
    p = [0.0, 0.0, 0.0]
    others = [k for k in range(3) if k != ai]
    p[others[0]] = float(h["u"])
    p[others[1]] = float(h["v"])
    p[ai] = float(h.get("z0") or 0.0)
    return p, axis_vec(h["axis"])


def main():
    feats = json.load(io.open(os.path.join(KB, "real_features.json"),
                              encoding="utf-8"))
    prof = {k: v for k, v in feats.items() if k.upper().startswith("EGT00")
            and "x" in k and "SW" in k.upper() or
            (k.upper().startswith("EGT006") and "D30" in k.upper())}
    print("型材文件 %d 个" % len(prof))

    rows = []
    total_pairs = 0
    cross_parts = 0
    for name, v in sorted(prof.items()):
        hs = [h for h in v["cylinders"] if float(h["d"]) >= 2.0]
        n = len(hs)
        bad = 0
        for i in range(n):
            for j in range(i + 1, n):
                p1, d1 = point_of(hs[i])
                p2, d2 = point_of(hs[j])
                r1 = float(hs[i]["d"]) / 2.0
                r2 = float(hs[j]["d"]) / 2.0
                d = seg_dist(p1, d1, p2, d2)
                total_pairs += 1
                if d < r1 + r2 - 0.05:
                    bad += 1
        if bad:
            cross_parts += 1
        rows.append((name, n, bad))

    print()
    print("孔对总数 %d，其中**轴线距离 < 半径和**（即相交）的孔对属于 %d 个零件"
          % (total_pairs, cross_parts))
    worst = sorted(rows, key=lambda r: -r[2])[:12]
    print("\n相交孔对最多的零件：")
    for name, n, bad in worst:
        print("   %-26s 孔 %-4d 相交孔对 %-5d" % (name, n, bad))

    L = ["# 型材孔相交分析（B 计划可行性的前提）", "",
         "**为什么要查**：B 计划「直写网格」的做法是「端面开孔 + 侧面开孔 + 每孔一段"
         "圆柱壁」，**前提是孔与孔不相交**；一旦相交，两段圆柱壁会互相穿插，"
         "拼出来的网格就不是合法实体。", "",
         "判据：两根圆柱相交 ⟺ 轴线最短距离 < r1 + r2。", "",
         "## 结果", "",
         "| 指标 | 值 |", "|---|---|",
         "| 检查的型材 | %d |" % len(prof),
         "| 孔对总数 | %d |" % total_pairs,
         "| **存在相交孔对的零件** | **%d** |" % cross_parts, "",
         "## 相交最多的零件", "",
         "| 文件 | 孔数 | 相交孔对 |", "|---|---|---|"]
    for name, n, bad in worst:
        L.append("| `%s` | %d | %d |" % (name, n, bad))
    with io.open(os.path.join(ROOT, "reports", "型材孔相交分析.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/型材孔相交分析.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
