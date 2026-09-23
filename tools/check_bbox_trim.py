# -*- coding: utf-8 -*-
r"""检查分位数裁剪包围盒：对单实体零件应几乎不变，对装配件应显著收敛。"""
from __future__ import annotations

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    "ULA63040AK0.stp", "UCBH50OCSK0105.stp", "UCBH63OESK0105.stp",
    "UCBM63OESK0105.stp", "MGPA50-50Z-P3DWASE.stp",
    "GB825_88_M24.stp", "BES_M18MI_PSC80A_S04G_W33.stp",
    "KMS-M05-010-100-D.stp", "KMS-M13-M05-P02-V04.stp",
    "KMS-M15-G14-H06.stp", "M10x30.stp", "EGT001 SW80x088.stp",
    "KMS-M01-P097-A20.stp", "KMS-M09-S03-20-05.stp",
]


def main():
    with io.open(os.path.join(ROOT, "kb", "real_geometry.json"),
                 encoding="utf-8") as f:
        parts = {p["file"]: p for p in json.load(f)["parts"]}
    print("%-32s %-24s %-24s %s" % ("文件", "圆弧修正包围盒", "分位裁剪包围盒", "是否相同"))
    for t in TARGETS:
        p = parts.get(t)
        if not p:
            print("  缺 %s" % t)
            continue
        a = p.get("bbox") and p["bbox"]["size"]
        b = p.get("bbox_trim") and p["bbox_trim"]["size"]
        same = a and b and max(abs(a[i] - b[i]) for i in range(3)) < 0.01
        print("%-32s %-24s %-24s %s"
              % (t[:32],
                 "×".join("%.2f" % v for v in a) if a else "-",
                 "×".join("%.2f" % v for v in b) if b else "-",
                 "相同 ✓" if same else "不同"))


if __name__ == "__main__":
    main()
