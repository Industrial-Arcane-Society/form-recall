# -*- coding: utf-8 -*-
r"""真实几何知识库2：把 STEP 里量到的**孔位/孔深**按型号存好，供建模器直接取用。

与 `reallib.py` 的分工：
    · `reallib.py`    管子型号 → 类别 / 外形 / 出处（第一轮迭代的成果）
    · 本模块          管子型号 → **逐孔的真实位置、直径、轴向范围**
      （第二轮迭代：用户反馈"其他零件只是大体形状，细节都没有"，
        根因就是孔位没被用起来）

数据来源：`kb/real_features.json`（由 `tools/extract_step_features.py` 从
CATIA 导出的 STEP 里解析）。每个圆柱面记录：
    d（直径）、axis（X/Y/Z）、u/v（轴线在垂直平面上的坐标）、z0~z1（轴向范围）、
    faces（由几个圆柱面合并而来）
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import io
import json
import os
import re
from typing import Dict, List, Optional

ROOT = str(PROJECT_ROOT)
KB = os.path.join(ROOT, "kb")
_CACHE = {}


def _load():
    if "f" not in _CACHE:
        p = os.path.join(KB, "real_features.json")
        try:
            with io.open(p, encoding="utf-8") as f:
                _CACHE["f"] = json.load(f)
        except Exception:
            _CACHE["f"] = {}
        #: 归一化索引：去掉扩展名、空格、大小写
        idx = {}
        for k, v in _CACHE["f"].items():
            stem = os.path.splitext(k)[0]
            for key in (stem, stem.replace(" ", ""), stem.replace(" ", "_"),
                        stem.upper(), stem.lower()):
                idx[key] = v
        _CACHE["idx"] = idx
    return _CACHE["f"], _CACHE["idx"]


def feature_of(model: str) -> Optional[dict]:
    """按型号取真实特征；取不到返回 None（**不猜**）。"""
    if not model:
        return None
    _f, idx = _load()
    m = str(model).strip()
    stem = os.path.splitext(m)[0]
    for key in (stem, stem.replace(" ", ""), stem.replace(" ", "_"),
                stem.upper(), stem.lower()):
        v = idx.get(key)
        if v:
            return v
    # 型材写法带前导零（SW80x088）与文件名一致，这里再兜一次宽松匹配
    m2 = re.sub(r"[^0-9A-Za-z]", "", stem).lower()
    for k, v in idx.items():
        if re.sub(r"[^0-9A-Za-z]", "", k).lower() == m2:
            return v
    # 型号是文件名前缀的情况：连接件的文件名带尺寸后缀
    # （型号 `EGT013`，文件 `EGT013 300x450 SW80.stp`）—— 不兜这一层的话
    # 73 个连接件全都取不到真实特征，只能退回"按直径平均摆"。
    pre = stem.lower() + " "
    for k, v in idx.items():
        if k.lower().startswith(pre):
            return v
    return None


def hole_plan(model: str, budget: int = 64, min_d: float = 0.0) -> dict:
    """把真实圆柱特征整理成**可建模的孔清单**。

    规则（都能从数据本身验证）：
      · 同一条轴线上有大小两个圆柱（沉孔/台阶孔）→ 大的按"近端盲孔"建，
        小的按通孔建，两者都保留；
      · 孔深：真实 z0~z1 的长度；若与零件在该轴上的总长一致 → 通孔；
      · 只保留落在包络内的（提取阶段已过滤构造几何）；
      · 超出 `budget` 的孔**不静默丢弃**，在 `dropped` 里如实上报。

    返回 {holes:[{d,axis,u,v,depth,through,from}], dropped, total, envelope}
    """
    v = feature_of(model)
    if not v:
        return {"holes": [], "dropped": 0, "total": 0, "envelope": None,
                "source": None}
    env = v.get("envelope") or [0, 0, 0]
    #: 各轴的零件总长（用于判通孔）
    ext = {"X": float(env[0]), "Y": float(env[1]), "Z": float(env[2])}
    #: 各轴的包围盒范围（判"从哪一端钻"）
    lo = v.get("bbox_min") or [0, 0, 0]
    hi = v.get("bbox_max") or [0, 0, 0]
    rng = {"X": (lo[0], hi[0]), "Y": (lo[1], hi[1]), "Z": (lo[2], hi[2])}

    # 同一轴线上的圆柱按直径分组 → 识别沉孔/台阶孔
    groups = {}
    for c in v.get("cylinders") or []:
        if float(c["d"]) < min_d:
            continue
        groups.setdefault((c["axis"], round(c["u"], 2), round(c["v"], 2)),
                          []).append(c)

    holes = []
    for (axis, u, vv), cs in groups.items():
        cs.sort(key=lambda c: -float(c["d"]))
        span_total = ext.get(axis, 0.0)
        for i, c in enumerate(cs):
            d = float(c["d"])
            z0, z1 = c.get("z0"), c.get("z1")
            if z0 is None or z1 is None:
                depth, through = None, True
            else:
                depth = abs(z1 - z0)
                # ⚠ 深度太小的一律按通孔处理：实测有些圆柱配不到端面圆
                #   （z0==z1 → 深度 0），按"深 0"发给 CATIA 会变成零深特征，
                #   而零深特征会让整棵 Part 报错（见 §0.29 那次故障）。
                through = (depth < 0.2) or (depth >= span_total - 1.0)
            # 大孔在下（沉孔）还是在上：按真实 z 的中点判断，取靠近端面的那一侧
            holes.append({
                "d": d, "axis": axis, "u": u, "v": vv,
                "depth": None if through else round(depth, 3),
                "through": bool(through),
                "z0": z0, "z1": z1, "faces": c.get("faces", 1),
                "theoretical_span": span_total,
            })
    # 大的在前（沉孔优先切），保证小的通孔能在后面补上
    holes.sort(key=lambda h: (-h["d"], h["axis"], h["u"], h["v"]))
    total = len(holes)
    dropped = max(0, total - budget)
    return {"holes": holes[:budget], "dropped": dropped, "total": total,
            "envelope": env, "bbox_min": lo, "bbox_max": hi,
            "source": "kb/real_features.json（CATIA 导出 STEP 实测孔位）"}


def stats() -> dict:
    f, _idx = _load()
    n_cyl = sum(v.get("n_kept", 0) for v in f.values())
    return {"parts": len(f), "cylinders": n_cyl}


def holes_intersect(cyls, tol: float = 0.05) -> bool:
    """孔与孔是否相交（决定能不能走"直写网格"这条路径）。

    B 计划的做法是"每孔一段圆柱壁、直接拼三角形"，**前提是孔不相交**：
    一旦相交，两段圆柱壁互相穿插，拼出来的网格不是合法实体。

    判据：两圆柱轴线的最短距离 < r1 + r2（且轴向范围有重叠时才算）。
    实测：289 个型材里 **267 个完全不相交**（EGT001/EGT002），
    只有 22 个 EGT006（⌀30 圆型材）存在相交孔对 —— 那批退回布尔路径。
    """
    def vec(ax):
        return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0),
                "Z": (0.0, 0.0, 1.0)}[ax]

    items = []
    for h in cyls:
        if float(h["d"]) < 2.0:
            continue
        ax = h["axis"]
        ai = "XYZ".index(ax)
        others = [k for k in range(3) if k != ai]
        p = [0.0, 0.0, 0.0]
        p[others[0]] = float(h["u"])
        p[others[1]] = float(h["v"])
        z0, z1 = h.get("z0"), h.get("z1")
        p[ai] = float(z0) if z0 is not None else 0.0
        rng = (float(z0), float(z1)) if z0 is not None and z1 is not None else None
        items.append((p, vec(ax), float(h["d"]) / 2.0, ai, rng))

    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            p1, d1, r1, a1, rg1 = items[i]
            p2, d2, r2, a2, rg2 = items[j]
            if rg1 and rg2 and a1 == a2:
                if rg1[1] < rg2[0] - tol or rg2[1] < rg1[0] - tol:
                    continue                     # 轴向不重叠，不可能相交
            w = [p2[k] - p1[k] for k in range(3)]
            b = sum(d1[k] * d2[k] for k in range(3))
            d = sum(d1[k] * w[k] for k in range(3))
            e = sum(d2[k] * w[k] for k in range(3))
            den = 1.0 - b * b
            if abs(den) < 1e-12:                 # 平行
                proj = [p1[k] + d1[k] * d for k in range(3)]
                dist = sum((p2[k] - proj[k]) ** 2 for k in range(3)) ** 0.5
            else:
                s = (b * e - d) / den
                t = (e - b * d) / den
                q1 = [p1[k] + d1[k] * s for k in range(3)]
                q2 = [p2[k] + d2[k] * t for k in range(3)]
                dist = sum((q1[k] - q2[k]) ** 2 for k in range(3)) ** 0.5
            if dist < r1 + r2 - tol:
                return True
    return False
