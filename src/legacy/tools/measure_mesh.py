# -*- coding: utf-8 -*-
r"""量**建出来的网格**的真实包围盒（不是零件声明值）。

为什么需要单独一个工具：`PartDef.bbox(p)` 返回的是"这个零件**应该**多大"，
是声明，不是测量。基准如果只比声明值，就可能出现"声明对了、建出来是错的"
这种假通过（本轮实测就发现这个漏洞：基准原先只比声明值）。
这里把特征真的跑一遍 BSP-CSG，再从网格顶点量尺寸。

用法（库）:
    from measure_mesh import measure
    size = measure(features)        # -> (X, Y, Z) 或 None
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM      # noqa: E402


def measure(features, timeout=None, max_faces=400000):
    """把特征建成网格并量外形；失败返回 None。

    `timeout` 是**秒数**（不是 `Document.build(deadline=)` 那种绝对时间戳）。
    ⚠ 踩过的坑：`Document.build` 的 `deadline` 参数语义是
      `time.time() > deadline`（绝对时刻），但它的 docstring 写的是"秒"。
      直接传 `60.0` 会被当成"1970 年就过期了"，立刻抛
      `BuildAborted: 计算超时 (0 秒)` —— 实测就是这么被骗到的。
      这里统一收秒数，内部换算成绝对时刻。

    `max_faces` 必须放宽：默认上限在多孔件上会被触发 —— 实测 64 个孔的箱体
    BSP 碎片化到 6.1 万面，直接抛 `BuildAborted: 模型过于复杂`，
    于是"网格量不出"被误判成建模失败（真实几何基准因此从 100% 掉到 94.6%）。
    验收的目的就是把模型建出来量，所以这里给足上限。

    返回 (x, y, z) 尺寸，**按从大到小排**（与基准的判据口径一致）。
    """
    if not features:
        return None
    doc = DM.Document()
    for f in features:
        doc.add(f)
    deadline = (time.time() + float(timeout)) if timeout else None
    try:
        body = doc.build(deadline=deadline, max_faces=max_faces)
    except Exception:
        return None
    if not body:
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    n = 0
    for poly in body:
        for v in getattr(poly, "vertices", None) or ():
            # kernel.Polygon.vertices 的元素是 `(坐标三元组, 法向三元组)`
            p = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 \
                and isinstance(v[0], (tuple, list)) else v
            if hasattr(p, "x"):
                p = (p.x, p.y, p.z)
            n += 1
            for i in range(3):
                if p[i] < lo[i]:
                    lo[i] = p[i]
                if p[i] > hi[i]:
                    hi[i] = p[i]
    if not n:
        return None
    return tuple(round(hi[i] - lo[i], 3) for i in range(3))


def measure_sorted(features, timeout=60.0, max_faces=400000):
    s = measure(features, timeout=timeout, max_faces=max_faces)
    return tuple(sorted(s, reverse=True)) if s else None


def measure_bbox(features, timeout=None, max_faces=400000):
    """量网格的**逐轴** lo/hi（不排序）。

    用途：核对"模型的坐标系"与"实测零件的坐标系"是不是同一个。
    只比排序后的外形（`measure_sorted`）看不出坐标系错位 —— 实测 EGT006
    ⌀30 圆棒的模型被放在 Z 轴上、而实测零件是沿 X 的，外形排序后完全一样，
    但孔位整体偏了几百毫米，模型上根本没有孔。
    """
    if not features:
        return None
    doc = DM.Document()
    for f in features:
        doc.add(f)
    deadline = (time.time() + float(timeout)) if timeout else None
    try:
        body = doc.build(deadline=deadline, max_faces=max_faces)
    except Exception:
        return None
    if not body:
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    n = 0
    for poly in body:
        for v in getattr(poly, "vertices", None) or ():
            p = v[0] if isinstance(v, (tuple, list)) and len(v) == 2 \
                and isinstance(v[0], (tuple, list)) else v
            if hasattr(p, "x"):
                p = (p.x, p.y, p.z)
            n += 1
            for i in range(3):
                if p[i] < lo[i]:
                    lo[i] = p[i]
                if p[i] > hi[i]:
                    hi[i] = p[i]
    if not n:
        return None
    return (tuple(round(v, 3) for v in lo), tuple(round(v, 3) for v in hi))


if __name__ == "__main__":
    from ai_modeler import partlib as PL
    for t in ("M10x30", "EGT001 SW80x118", "KMS-M15-G14-H06",
              "KMS-M09-S03-20-05", "定位销 KMS-M01-P097-A20"):
        r = PL.build_features_ex(t)
        if not r:
            print("%-30s 建不出来" % t)
            continue
        print("%-30s 声明=%s  实测网格=%s"
              % (t, tuple(round(float(v), 3) for v in r[2]),
                 measure_sorted(r[1])))
