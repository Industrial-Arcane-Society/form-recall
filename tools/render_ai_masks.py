# -*- coding: utf-8 -*-
r"""只出**轮廓掩码**（比对用）—— 比整套渲染快两个数量级。

为什么单独做这个：
    图像相似度只需要两边的**轮廓掩码**，不需要着色/描边。
    实测 B-Rep 复刻件的网格很大（型材 3 万三角面），`softrender` 是纯 Python
    扫描线光栅化，一件要 8~25 s，全量 447 件要 2~3 小时；
    而用 PIL 的 `ImageDraw.polygon` 把三角面直接画进掩码是 C 实现，
    同样一件只要 0.3~0.6 s（实测全量 ~4 分钟）。

做法（与 `softrender` 同一套视图数学，保证口径一致）：
    1. 取零件特征 → 建网格（走真管线）；
    2. 用 CATIA 等轴测视向 (1,−1,1)/√3、up=+Z 投影到屏幕；
    3. 按屏幕坐标把每个三角面填进 PIL 图像 → 掩码；
    4. 保存 `<型号>_mask.png`（黑底白形），供 `compare_ai_vs_photos.py` 使用。

用法:
    python tools\render_ai_masks.py --all
输出:
    零件AI建模图片\<族>\<型号>_mask.png
    kb/ai_render_index.json（复用同一份索引，带 mask 路径）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AI_MODELER_BREP_COARSE", "1")

from ai_modeler import partlib as PL, docmodel as DM, kernel as K   # noqa: E402
from ai_modeler import softrender as SR                              # noqa: E402

W, H = 352, 448
MARGIN = 0.06
AZ, EL = -45.0, 35.264


def render_mask(fs, w=W, h=H, az=None, el=None):
    """特征表 → 掩码(np.uint8 0/255)。az/el 可临时覆盖视向（视向重拟合要用）。"""
    az = AZ if az is None else az
    el = EL if el is None else el
    doc = DM.Document()
    for f in fs:
        doc.add(f)
    body = doc.build(max_faces=800000)
    if not body:
        return None
    tris = K.polygons_to_triangles(body)
    if not tris:
        return None
    right, up, _fwd = SR.view_matrix(az, el)
    P = np.asarray([t[0] for t in tris] + [t[1] for t in tris] + [t[2] for t in tris],
                   dtype=np.float64)
    xs = P @ np.asarray(right)
    ys = P @ np.asarray(up)
    n = len(tris)
    A = np.stack([xs[:n], ys[:n]], 1)
    B = np.stack([xs[n:2 * n], ys[n:2 * n]], 1)
    C = np.stack([xs[2 * n:], ys[2 * n:]], 1)
    mn = np.array([xs.min(), ys.min()])
    mx = np.array([xs.max(), ys.max()])
    span = max(float(mx[0] - mn[0]), float(mx[1] - mn[1]), 1e-6)
    scale = (1.0 - 2.0 * MARGIN) * min(w, h) / span
    cx = (mn[0] + mx[0]) / 2.0
    cy = (mn[1] + mx[1]) / 2.0
    ox, oy = w / 2.0, h / 2.0

    def to_px(Q):
        X = (Q[:, 0] - cx) * scale + ox
        Y = oy - (Q[:, 1] - cy) * scale
        return np.stack([X, Y], 1)

    A, B, C = to_px(A), to_px(B), to_px(C)
    img = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(img)
    # 三角形稍微外扩 0.5 px，避免相邻三角之间出现缝（掩码必须是实心的）
    for i in range(n):
        tri = [(A[i, 0], A[i, 1]), (B[i, 0], B[i, 1]), (C[i, 0], C[i, 1])]
        dr.polygon(tri, fill=255, outline=255)
    return np.asarray(img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    with io.open(os.path.join(ROOT, "kb", "ai_render_index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    items = [it for it in idx["items"]]
    if a.group:
        items = [it for it in items if a.group in it["group"]]
    if a.limit:
        items = items[:a.limit]
    print("出轮廓掩码 %d 个（PIL 光栅化，预计 ~4 分钟/447 件）…" % len(items))
    t0 = time.time()
    ok = fail = 0
    for i, it in enumerate(items):
        rec = {"prompt": it["prompt"], "model": it["model"], "group": it["group"]}
        try:
            r = PL.build_features_ex(it["prompt"])
            if not r:
                raise RuntimeError("建不出来")
            m = render_mask(r[1])
            if m is None:
                raise RuntimeError("网格为空")
            d = os.path.join(ROOT, "零件AI建模图片", it["group"])
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, it["model"] + "_mask.png")
            Image.fromarray(m).save(p)
            rec.update({"ok": True, "mask": os.path.relpath(p, ROOT),
                        "fg": round(float((m > 0).mean()), 4)})
            ok += 1
        except Exception as e:
            rec.update({"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:60])})
            fail += 1
        it.update(rec)
        if (i + 1) % 50 == 0:
            print("  %d/%d  %.0fs  ok=%d fail=%d" % (i + 1, len(items), time.time() - t0, ok, fail),
                  flush=True)
    with io.open(os.path.join(ROOT, "kb", "ai_render_index.json"), "w", encoding="utf-8") as f:
        json.dump({"n": idx["n"], "w": W, "h": H, "view": {"az": AZ, "el": EL},
                   "items": idx["items"]}, f, ensure_ascii=False, indent=1)
    print("完成：ok=%d fail=%d，用时 %.0fs" % (ok, fail, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
