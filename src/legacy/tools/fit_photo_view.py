# -*- coding: utf-8 -*-
r"""找出"真实照片"用的是哪个视图 —— 用精确掩码去拟合，而不是靠猜。

背景：CATIA 的自动化接口在这台机器上**设不了视向**
（`PutSightDirection` 报 err=450、`StartCommand` 卡死），所以重拍出来的 518 张
是**当前视图**（多半是 CATIA 默认的前视图），而我们的渲染图是等轴测 ——
实测两者轮廓角度差 30° 以上，直接比必然很低分。

做法：拿几个形状有辨识度的零件，把模型在**候选视图**里渲染出掩码，
与真实照片的差分掩码比对（IoU + 对称 chamfer），取最好的那一个视图。

用法: python tools\fit_photo_view.py
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

from ai_modeler import partlib as PL, docmodel as DM, softrender as SR   # noqa: E402
import compare_ai_vs_photos as CMP                                       # noqa: E402

MAGENTA = (255, 0, 255)
W, H = 480, 600

#: 候选视图：(方位角, 俯仰角) —— 覆盖 CATIA 的 6 个标准视图 + 等轴测
CANDIDATES = {
    "front(-90,0)": (-90, 0), "back(90,0)": (90, 0),
    "right(0,0)": (0, 0), "left(180,0)": (180, 0),
    "top(-90,90)": (-90, 90), "bottom(-90,-90)": (-90, -90),
    "iso(-45,35)": (-45, 35.264), "iso2(-135,35)": (-135, 35.264),
    "iso3(45,35)": (45, 35.264), "iso4(135,35)": (135, 35.264),
    # CATIA 默认"前视图"通常沿 −X 看（YZ 平面），再带一点上仰
    "front_tilt(-90,15)": (-90, 15), "front_tilt2(-90,-15)": (-90, -15),
}


def render_mask(prompt, az, el):
    r = PL.build_features_ex(prompt)
    if not r:
        return None
    doc = DM.Document()
    for f in r[1]:
        doc.add(f)
    body = doc.build(max_faces=400000)
    if not body:
        return None
    SR.VIEWS["probe"] = (az, el)
    SR.BG_TOP = MAGENTA
    SR.BG_BOT = MAGENTA
    rd = SR.Renderer(W, H)
    if not rd.render(body, view="probe", edges=True, margin=0.06):
        return None
    rgb = np.frombuffer(bytes(rd.fb), np.uint8).reshape(H, W, 3).astype(np.int16)
    mg = np.array([255, 0, 255], np.int16)
    return (np.abs(rgb - mg).sum(2) > 24)


def main():
    ai = json.load(io.open(os.path.join(ROOT, "kb", "ai_render_index.json"),
                           encoding="utf-8"))
    ph = {x["model"].strip().lower(): x
          for x in json.load(io.open(os.path.join(ROOT, "kb", "reshoot_index.json"),
                                     encoding="utf-8"))["items"] if x.get("ok")}
    probes = [x for x in ai["items"]
              if x["model"].strip().lower() in ph
              and any(k in x["model"].lower() for k in
                      ("sw80x1018", "m5x16", "h03-20-20", "m05-010-100"))][:4]
    if not probes:
        probes = [x for x in ai["items"] if x["model"].strip().lower() in ph][:4]
    print("用 %d 个零件做视图拟合：%s" % (len(probes), [p["model"] for p in probes]))
    photo_masks = []
    for p in probes:
        q = ph[p["model"].strip().lower()]
        photo_masks.append(CMP.photo_mask(os.path.join(ROOT, q["img"]),
                                          os.path.join(ROOT, q["bg"])))
    scores = {}
    for name, (az, el) in CANDIDATES.items():
        vals = []
        for p, pm in zip(probes, photo_masks):
            if pm is None:
                continue
            am = render_mask(p["prompt"], az, el)
            if am is None:
                continue
            r = CMP.similarity(am, pm)
            if r:
                vals.append(r[0])
        if vals:
            scores[name] = sum(vals) / len(vals)
    for name, v in sorted(scores.items(), key=lambda kv: -kv[1]):
        print("   %-20s 平均相似度 %5.1f%%" % (name, v))
    best = max(scores, key=scores.get) if scores else None
    print("最佳视图：%s = %s（相似度 %.1f%%）"
          % (best, CANDIDATES.get(best), scores.get(best, 0)))
    if best:
        with io.open(os.path.join(ROOT, "kb", "photo_view.json"), "w",
                     encoding="utf-8") as f:
            json.dump({"view": best, "az": CANDIDATES[best][0],
                       "el": CANDIDATES[best][1], "score": scores[best],
                       "all": scores}, f, ensure_ascii=False, indent=1)
        print("→ kb/photo_view.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
