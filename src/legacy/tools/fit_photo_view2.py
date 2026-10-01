# -*- coding: utf-8 -*-
r"""定出"真实照片用的是哪个视图" —— 用真实几何渲染去拟合照片掩码。

为什么必须做这一步（实测证据）：
    重拍的照片是 CATIA **当前视图**（`PutSightDirection` 在这台机器上报 err=450、
    `StartCommand` 卡死，所以设不了等轴测）。而我们的掩码渲染用的是等轴测。
    实测后果：
      · 型材（斜长条）两边角度**碰巧**对得上 → 相似度 85%；
      · 支座/气缸这类有明确朝向的零件 → AI 是斜带、照片是横带/竖块 → 只有 42%。
    → 说明"视图"是当前最大的系统性误差来源，不是模型。

做法：拿几个形状有辨识度的零件，把**真实网格**在候选视图里渲染掩码，
与照片掩码算相似度（快版比对，一件 ~1.4 s），取平均分最高的视图。

用法: python tools\fit_photo_view2.py
输出: kb/photo_view.json（最佳视图 + 各候选得分）
"""
from __future__ import annotations

import io
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("AI_MODELER_BREP_COARSE", "1")

from ai_modeler import partlib as PL, docmodel as DM, kernel as K   # noqa: E402
from ai_modeler import softrender as SR                              # noqa: E402
import compare_ai_vs_photos as CMP                                   # noqa: E402
import render_ai_masks as RAM                                        # noqa: E402

#: CATIA 的 6 个标准视图 + 4 个等轴测 + 几个常见斜视
CANDIDATES = {
    "front(-90,0)": (-90, 0), "back(90,0)": (90, 0),
    "left(180,0)": (180, 0), "right(0,0)": (0, 0),
    "top(-90,89)": (-90, 89), "bottom(-90,-89)": (-90, -89),
    "iso(-45,35)": (-45, 35.264), "iso2(-135,35)": (-135, 35.264),
    "iso3(45,35)": (45, 35.264), "iso4(135,35)": (135, 35.264),
    "front_up(-90,25)": (-90, 25), "front_dn(-90,-25)": (-90, -25),
    "right_up(0,25)": (0, 25), "right_dn(0,-25)": (0, -25),
}


def render_mask(prompt, az, el, w=RAM.W, h=RAM.H):
    RAM.AZ, RAM.EL = az, el
    r = PL.build_features_ex(prompt)
    if not r:
        return None
    return RAM.render_mask(r[1], w=w, h=h)


def main():
    ai = json.load(io.open(os.path.join(ROOT, "kb", "ai_render_index.json"),
                           encoding="utf-8"))
    ph = {x["model"].strip().lower(): x
          for x in json.load(io.open(os.path.join(ROOT, "kb", "reshoot_index.json"),
                                     encoding="utf-8"))["items"] if x.get("ok")}
    # 挑"照片掩码可用 + 形状有辨识度"的零件做拟合样本
    # ⚠ 样本必须**跨族**：第一版只取了前 8 个可用件，结果全是 EGT 型材（长条），
    #   长条在很多视图下都长得差不多 → 拟合被它带偏，得不出结论。
    probes = []
    per_group = {}
    for it in ai["items"]:
        g = it["group"]
        if per_group.get(g, 0) >= 3:                # 每族最多取 3 个
            continue
        q = ph.get(it["model"].strip().lower())
        if not q:
            continue
        mb = CMP.photo_mask(os.path.join(ROOT, q["img"]), os.path.join(ROOT, q["bg"]))
        if mb is None:
            continue
        cov = float(mb.mean())
        if 0.02 <= cov <= 0.30:                     # 太小的掩码没信息量
            probes.append((it, mb, cov))
            per_group[g] = per_group.get(g, 0) + 1
        if len(probes) >= 12:
            break
    if not probes:
        print("没有可用的照片掩码样本")
        return 1
    print("用 %d 个零件做视图拟合：%s" % (len(probes), [p[0]["model"] for p in probes]))
    scores = {}
    for name, (az, el) in CANDIDATES.items():
        vals = []
        for it, mb, _c in probes:
            am = render_mask(it["prompt"], az, el)
            if am is None:
                continue
            r = CMP.similarity(am, mb)
            if r:
                vals.append(r[0])
        if vals:
            scores[name] = round(sum(vals) / len(vals), 1)
        print("   %-18s 平均相似度 %s" % (name, scores.get(name, "—")), flush=True)
    best = max(scores, key=scores.get) if scores else None
    print("\n最佳视图：%s = %s（相似度 %.1f%%）"
          % (best, CANDIDATES.get(best), scores.get(best, 0)))
    if best:
        with io.open(os.path.join(ROOT, "kb", "photo_view.json"), "w",
                     encoding="utf-8") as f:
            json.dump({"view": best, "az": CANDIDATES[best][0],
                       "el": CANDIDATES[best][1], "score": scores[best],
                       "all": dict(sorted(scores.items(), key=lambda kv: -kv[1]))},
                      f, ensure_ascii=False, indent=1)
        print("→ kb/photo_view.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
