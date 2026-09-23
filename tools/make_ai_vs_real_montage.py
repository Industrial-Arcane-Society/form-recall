# -*- coding: utf-8 -*-
r"""给每个零件出"并排拼图"：左 = AI 提示词建模的渲染图，右 = 零件真实照片。

这是用户要的"拍照对比"交付物：**图上直接看得出像不像**，不用看数字。

用法:
    python tools\make_ai_vs_real_montage.py --all
输出:
    零件比对图\<族>\<型号>_对比.png
    reports\AI建模图_并排对比清单.md
"""
from __future__ import annotations

import io
import json
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
REAL = os.path.join(ROOT, "零件真实图片")
OUT = os.path.join(ROOT, "零件比对图")
REPORTS = os.path.join(ROOT, "reports")
H = 520


def find_real(model):
    stem = model.strip().lower()
    for dp, _dn, fns in os.walk(REAL):
        for fn in fns:
            if fn.lower().endswith(".png") and os.path.splitext(fn)[0].strip().lower() == stem:
                return os.path.join(dp, fn)
    return None


def main():
    with io.open(os.path.join(KB, "ai_render_index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    items = [it for it in idx["items"] if it.get("ok")]
    print("出并排拼图 %d 个…" % len(items))
    done = 0
    miss = []
    for it in items:
        real = find_real(it["model"])
        ai = os.path.join(ROOT, it["img"])
        if not real or not os.path.exists(ai):
            miss.append(it["model"])
            continue
        a = Image.open(ai).convert("RGB")
        b = Image.open(real).convert("RGB")
        a = a.resize((int(a.width * H / a.height), H), Image.LANCZOS)
        b = b.resize((int(b.width * H / b.height), H), Image.LANCZOS)
        cv = Image.new("RGB", (a.width + b.width + 12, H + 26), (24, 28, 34))
        cv.paste(a, (0, 26))
        cv.paste(b, (a.width + 12, 26))
        ImageDraw.Draw(cv).text(
            (6, 7), "AI 提示词建模：%s        |        真实照片（CATIA）" % it["model"],
            fill=(232, 238, 244))
        d = os.path.join(OUT, it["group"])
        os.makedirs(d, exist_ok=True)
        cv.save(os.path.join(d, "%s_对比.png" % it["model"]))
        done += 1
    print("完成 %d 个，缺实拍图 %d 个" % (done, len(miss)))
    with io.open(os.path.join(REPORTS, "AI建模图_并排对比清单.md"), "w",
                 encoding="utf-8") as f:
        f.write("# AI 建模图 vs 真实照片 并排对比\n\n")
        f.write("左：AI 提示词自动建模的渲染图（视角与实拍一致：CATIA 等轴测 (1,−1,1)）；\n"
                "右：零件真实照片（CATIA 截图）。\n\n")
        f.write("共 **%d** 对，目录 `零件比对图\\<族>\\<型号>_对比.png`。\n" % done)
        if miss:
            f.write("\n缺实拍图的型号：%s\n" % "、".join(miss[:40]))
    print("→ 零件比对图\\、reports/AI建模图_并排对比清单.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
