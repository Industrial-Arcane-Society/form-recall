# -*- coding: utf-8 -*-
r"""测 BSP-CSG 内核在"多圆柱面"下的耗时，用于决定 EGT 齿纹/孔的表达方式。"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM   # noqa: E402

ADD, CUT = DM.ADD, DM.CUT


def run(n_cyl, seg=16):
    t0 = time.time()
    doc = DM.Document()
    doc.add(DM.Feature("box", {"length": 79.8, "width": 79.8, "height": 98.21,
                              "corner": 0.0, "corner_mode": "fillet",
                              "center": (0, 0, 0), "base": False,
                              "axis": "Z"}, ADD))
    pitch = 79.8 / (n_cyl + 1)
    for i in range(n_cyl):
        x = -79.8 / 2 + pitch * (i + 1)
        doc.add(DM.Feature("hole", {"diameter": 11.0, "through": True,
                                    "x": x, "y": 0.0, "axis": "Z",
                                    "segments": seg}, CUT))
    try:
        mesh = doc.build()
    except Exception as e:
        return None, None, str(e)
    verts, faces = (mesh if isinstance(mesh, tuple) else (mesh, None))
    nv = len(verts) if hasattr(verts, "__len__") else -1
    return nv, len(faces) if faces is not None else -1, time.time() - t0


def main():
    for seg in (8, 16, 32):
        print("--- segments=%d ---" % seg)
        for n in (2, 8, 16, 32, 64):
            nv, nf, dt = run(n, seg)
            if nv is None:
                print("  cyl=%3d  失败: %s" % (n, dt))
                continue
            print("  cyl=%3d  顶点=%6d  面=%6d  耗时=%.3f s" % (n, nv, nf, dt))
            if dt > 20:
                print("  → 超阈值，停止加码")
                break


if __name__ == "__main__":
    main()
