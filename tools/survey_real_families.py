# -*- coding: utf-8 -*-
r"""勘察：真实几何里每个族的数据质量与可用信号。"""
from __future__ import annotations

import io
import json
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEAD_H = {"M5": 5.0, "M6": 6.0, "M8": 8.0, "M10": 10.0, "M12": 12.0}


def main():
    d = json.load(io.open(os.path.join(ROOT, "kb", "model_geometry.json"),
                          encoding="utf-8"))
    rg = json.load(io.open(os.path.join(ROOT, "kb", "real_geometry.json"),
                           encoding="utf-8"))

    print("=" * 78)
    print("一、螺钉：实测总长 = L + 头高 的合格率")
    for sp in ("M5", "M6", "M8", "M10", "M12"):
        ok = bad = 0
        zs = []
        for k, e in d["bolts"].items():
            if e["spec"] != sp:
                continue
            v = list(e["variants"].values())[0]
            g = v["real"]
            if not g:
                continue
            want = e["length"] + HEAD_H[sp]
            got = g["axis_len"]
            if abs(got - want) < 0.02:
                ok += 1
                zs.append(round(got - want, 2))
            else:
                bad += 1
        print("  %-4s 合格 %2d / 不合格 %2d   (残差集合 %s)"
              % (sp, ok, bad, sorted(set(zs))))
    print("  → 不合格记录是 CATIA 装配导出的坐标系污染，属数据缺陷，非规律缺失")

    print()
    print("=" * 78)
    print("二、非 EGT 族的实测特征")
    fams = defaultdict(list)
    for rec in rg["parts"]:
        rel = rec["rel"]
        if rel.startswith("外购件/EGT") or rel == "螺钉":
            continue
        fams[rel].append(rec)
    for rel in sorted(fams, key=lambda r: -len(fams[r])):
        rs = fams[rel]
        print("\n--- %s（%d 件）" % (rel, len(rs)))
        for rec in rs:
            b = rec["bbox"]["size"]
            holes = rec.get("hole_diameters", {})
            hk = ",".join("%s×%s" % kv for kv in
                          sorted(holes.items(), key=lambda kv: -float(kv[0]))[:5])
            print("    %-34s 实体%d 面%4d 柱%4d | bbox %8.2f×%8.2f×%8.2f | 柱径 %s"
                  % (rec["file"][:34], rec["n_solids"], rec["n_faces"],
                     rec["n_cylinders"], b[0], b[1], b[2], hk))


if __name__ == "__main__":
    main()
