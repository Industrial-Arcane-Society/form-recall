# -*- coding: utf-8 -*-
r"""数据可信度分级：哪些零件的实测包围盒可以当"真实外形"用。

分级规则（写死，便于复核）：
  A 单实体 且 顶点包围盒 ≈ 圆弧修正包围盒（各轴差 ≤2% 或 ≤0.5mm）
     → 包围盒可信，可直接当真实外形
  B 单实体 但两者不一致 → 圆弧修正值可信（顶点漏掉光顺圆柱极值），标注
  C 多实体（装配导出）→ 包围盒是"装配体范围"，**不当零件外形用**；
     只采用圆柱面直径指纹与拓扑计数
"""
from __future__ import annotations

import io
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with io.open(os.path.join(ROOT, "kb", "real_geometry.json"),
                 encoding="utf-8") as f:
        parts = json.load(f)["parts"]
    grade = Counter()
    fam = defaultdict(Counter)
    rows = []
    for p in parts:
        bb = p.get("bbox")
        bv = p.get("bbox_vertex")
        if bb is None:
            g = "D"      # 无几何（空 SHAPE_REPRESENTATION）
        elif p["n_solids"] <= 1 and bv:
            dev = max(abs(bb["size"][i] - bv["size"][i]) for i in range(3))
            g = "A" if dev <= max(0.5, 0.02 * max(bb["size"])) else "B"
        elif p["n_solids"] > 1:
            g = "C"
        else:
            g = "B"
        grade[g] += 1
        key = "/".join(p["rel"].split("/")[:-1]) or p["rel"]
        fam[key][g] += 1
        rows.append((g, p["file"], p["n_solids"], bb["size"] if bb else None,
                     bv["size"] if bv else None))
    print("总 %d 件，可信度分布：%s" % (len(parts), dict(grade)))
    print()
    print("%-46s %s" % ("族", "A/B/C/D"))
    for k in sorted(fam, key=lambda x: -sum(fam[x].values())):
        c = fam[k]
        print("  %-44s A=%3d B=%3d C=%3d D=%3d"
              % (k[:44], c["A"], c["B"], c["C"], c["D"]))
    print()
    print("=== B 级样例（顶点漏极值，已由圆弧修正）===")
    for g, f, ns, bs, bvs in rows:
        if g == "B":
            print("  %-34s 实体%d 修正=%s 顶点=%s" % (f[:34], ns, bs, bvs))
    print()
    print("=== D 级（无几何，需向用户说明）===")
    for g, f, ns, bs, bvs in rows:
        if g == "D":
            print("  %s" % f)
    with io.open(os.path.join(ROOT, "kb", "real_geometry_trust.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"grades": {f: g for g, f, _, _, _ in rows},
                   "summary": dict(grade)}, f, ensure_ascii=False, indent=1)
    print("\n→ kb/real_geometry_trust.json")


if __name__ == "__main__":
    main()
