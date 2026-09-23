# -*- coding: utf-8 -*-
r"""实测 B-Rep 实体：把"真实零件的网格"直接当模型几何用（外轮廓 = 真实）。

为什么要有它（数据说话）：
    本项目的图像相似度第一次算准之后暴露了一个大问题 ——
    平均只有 **62.1%**，0/313 达到 95%。根因很清楚：
    多数非标件是用 `_box_features(实测包络)` = **一个包围盒 + 孔** 建的，
    **型腔、加强筋、台阶、圆角**全都丢了；而几何判据只量孔位/包络，量不到外轮廓。
    典型例子 `KMS-M05-010-100-D`：AI 侧是方块、真实侧是 L 形支架。

    理论上的出路是"特征识别"（把 B-Rep 反推成型腔/筋/台阶的参数化特征），
    那是研究工作；**工程上更诚实、更快的做法**是：
    用 OpenCASCADE 读实测 STEP 拿到真实 B-Rep → 三角化 → 直接当模型几何。

它与现有做法的关系（**不是取代**）：
    · 标准件（螺栓/齿轮…）继续走参数化构建器 —— 那样才叫"用提示词建模"；
    · 用实测数据的族（销座/支座/阀岛/连接件…）**优先**用实测 B-Rep，
      因为对"复刻真实零件"这个目标来说它严格更接近；
    · 拿不到实测网格时，退回原来的"包络长方体 + 实测孔位"，并如实标注。

网格库由 `tools/build_real_brep.py` 生成（`kb/real_brep/<型号>.npz`）。
"""
from __future__ import annotations

import functools
import io
import json
import os
from typing import List, Optional

from . import kernel as K

KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kb")
BREP_DIR = os.path.join(KB, "real_brep")
#: 渲染用粗网格（三角面少 5~10 倍，轮廓看不出来；度量/导出仍用精细网格）
BREP_COARSE = os.path.join(KB, "real_brep_coarse")
INDEX = os.path.join(KB, "real_brep_index.json")

_cache = {}


def _index() -> dict:
    if "idx" not in _cache:
        try:
            with io.open(INDEX, encoding="utf-8") as f:
                d = json.load(f)
            _cache["idx"] = {it["model"].strip().lower(): it
                             for it in d.get("items", []) if it.get("ok")}
        except Exception:
            _cache["idx"] = {}
    return _cache["idx"]


def stats() -> dict:
    idx = _index()
    return {"n": len(idx), "dir": BREP_DIR,
            "size_mb": round(sum(os.path.getsize(os.path.join(BREP_DIR, f))
                                 for f in os.listdir(BREP_DIR)) / 1e6, 1)
            if os.path.isdir(BREP_DIR) else 0.0}


def available(model: str) -> bool:
    m = (model or "").strip().lower()
    if not m:
        return False
    if m in _index():
        return True
    for k in _index():
        if k.startswith(m + " ") or m.startswith(k):
            return True
    return False


def _resolve(model: str) -> Optional[str]:
    m = (model or "").strip().lower()
    if not m:
        return None
    if m in _index():
        return _index()[m]["model"]
    for k, v in _index().items():
        if k.startswith(m + " ") or m.startswith(k):
            return v["model"]
    return None


def cylinders(model: str):
    """该实测零件里的真圆柱面（供判据使用：网格里确实有这些孔）。"""
    try:
        with io.open(os.path.join(KB, "real_features.json"), encoding="utf-8") as f:
            feats = {k.strip().lower(): v for k, v in json.load(f).items()}
    except Exception:
        return []
    name = _resolve(model)
    if not name:
        return []
    v = feats.get(name.strip().lower() + ".stp") or feats.get(name.strip().lower())
    return (v or {}).get("cylinders") or []


def polys(model: str, tag: int = 0) -> List[K.Polygon]:
    """把实测网格读成内核多边形（带正确外法向）。"""
    name = _resolve(model)
    if not name:
        return []
    path = os.path.join(BREP_DIR, name + ".npz")
    if os.environ.get("AI_MODELER_BREP_COARSE", "").lower() in ("1", "on", "true"):
        pc = os.path.join(BREP_COARSE, name + ".npz")
        if os.path.exists(pc):
            path = pc
    if not os.path.exists(path):
        return []
    import numpy as np
    d = np.load(path)
    V = d["V"].astype(float)
    T = d["T"]
    out: List[K.Polygon] = []
    for (a, b, c) in T:
        p0, p1, p2 = V[a], V[b], V[c]
        pg = K.make_polygon([tuple(p0), tuple(p1), tuple(p2)], tag)
        if pg is not None:
            out.append(pg)
    return out


def volume(model: str) -> float:
    return K.volume_of(polys(model))
