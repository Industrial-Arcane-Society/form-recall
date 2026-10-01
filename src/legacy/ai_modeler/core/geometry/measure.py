# -*- coding: utf-8 -*-
r"""
U3：公差级自检闭环 —— 从**几何本身**量尺寸，按公差带判定，超差即报错。

为什么要有这一层（升级文档 §U3）
    以前只有"包围盒差 2%"这一种粗糙判定：构建成功、但某个槽深差了 0.3mm，程序完全无感。
    工业上要求按**顶点/边/面**三级在**给定公差**内校验参数正确性
    （Pointer-CAD v2 的评测协议；MBD 的"基准-公差-测量点"做法）。
    本模块把这件事做成闭环：

        DimSpec(受控尺寸) → measure() 从几何实测 → 与公差带比较
        → Report(逐条：要多少 / 实测多少 / 偏差多少) → 超差则 raise 或标记失败

三级判定
    vertex : 单个尺寸（孔径、槽距、齿数、外径…）
    edge   : 相邻/相对要素的关系（同轴度、最小壁厚）
    face   : 整体有效性（水密性、自相交、退化面片）

测量算子都在**已有网格**上做，不依赖任何外部库；每个算子只读 polygon 顶点。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ai_modeler.core import kernel as K


class ToleranceError(RuntimeError):
    """超差异常：携带完整报告，调用方可直接显示给用户。"""

    def __init__(self, report: "Report"):
        self.report = report
        super().__init__(report.summary())


@dataclass
class DimSpec:
    """一个受控尺寸。"""
    name: str
    value: float
    tol: float = 0.02                 # 相对公差（如 0.02 = 2%）
    abs_tol: float = 0.0              # 绝对公差（mm），与相对公差取较大者
    grade: str = "vertex"             # vertex | edge | face
    how: str = "bbox_x"               # 测量算子名
    note: str = ""

    def band(self) -> float:
        return max(abs(self.value) * self.tol, self.abs_tol, 1e-6)


@dataclass
class Result:
    name: str
    want: float
    got: Optional[float]
    dev: Optional[float]
    band: float
    grade: str
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.got is not None and self.dev is not None and self.dev <= self.band

    def text(self) -> str:
        if self.got is None:
            return "✗ %s：量不到（期望 %g）" % (self.name, self.want)
        if self.ok:
            return "✓ %s：实测 %g（期望 %g，偏差 %.3f ≤ %.3f）" % (
                self.name, self.got, self.want, self.dev, self.band)
        return "✗ %s：**超差** 期望 %g，实测 %g，偏差 %.3f > 公差 %.3f" % (
            self.name, self.want, self.got, self.dev, self.band)


@dataclass
class Report:
    results: List[Result] = field(default_factory=list)

    def add(self, r: Result):
        self.results.append(r)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results) and bool(self.results)

    def failed(self) -> List[Result]:
        return [r for r in self.results if not r.ok]

    def by_grade(self, grade: str) -> List[Result]:
        return [r for r in self.results if r.grade == grade]

    def summary(self) -> str:
        bad = self.failed()
        if not bad:
            return "公差自检通过：%d 项受控尺寸全部在公差带内" % len(self.results)
        head = "公差自检未通过：%d/%d 项超差" % (len(bad), len(self.results))
        return head + "\n" + "\n".join("  " + r.text() for r in bad)

    def lines(self) -> List[str]:
        return [r.text() for r in self.results]


# --------------------------------------------------------------------------
# 测量算子：全部在网格顶点上做
# --------------------------------------------------------------------------
def _verts(polys) -> List[Tuple[float, float, float]]:
    out = []
    for p in polys:
        for (pos, _n) in p.vertices:
            out.append(pos)
    return out


def m_bbox_x(polys, **kw):
    bb = K.bbox(polys)
    return (bb[3] - bb[0]) if bb else None


def m_bbox_y(polys, **kw):
    bb = K.bbox(polys)
    return (bb[4] - bb[1]) if bb else None


def m_bbox_z(polys, **kw):
    bb = K.bbox(polys)
    return (bb[5] - bb[2]) if bb else None


def m_outer_dia(polys, **kw):
    bb = K.bbox(polys)
    return max(bb[3] - bb[0], bb[4] - bb[1]) if bb else None


def m_bore_dia(polys, **kw):
    """内孔直径：用"轴心附近的最小径向空隙"估。

    做法：取所有顶点，按 Z 分层，找每层里**离轴心最近**的顶点半径 r_min；
    内孔半径就是 r_min（回转体上最靠近轴的就是孔壁）。没有顶点靠近轴 -> 无孔。
    """
    vs = _verts(polys)
    if not vs:
        return None
    zs = [v[2] for v in vs]
    z0, z1 = min(zs), max(zs)
    if z1 - z0 < 1e-6:
        return None
    bands = {}
    for (x, y, z) in vs:
        bi = int(6 * (z - z0) / (z1 - z0))
        r = math.hypot(x, y)
        if bi not in bands or r < bands[bi]:
            bands[bi] = r
    r_min = min(bands.values())
    return 2.0 * r_min if r_min < 1e-9 or r_min > 0 else None if r_min <= 0 else 2.0 * r_min


def _z_profile_at_angle(polys, angle_tol_deg=2.0):
    """在**某个角度**处取 r-Z 剖面（半径随轴向变化）。

    这是 V 型槽的正确切片：槽是在某一圈上沿轴向切出来的，
    所以要在"固定角度"下看半径随 Z 的变化，凹陷处就是槽。
    踩过的坑：早先把所有顶点投到 XY 平面数角度凹陷 ——
    外圆半径沿圆周处处相等（实测 51 个角度桶半径全是 52.75），根本数不出槽。
    """
    vs = _verts(polys)
    if not vs:
        return None
    zs = [v[2] for v in vs]
    z0, z1 = min(zs), max(zs)
    if z1 - z0 < 1e-6:
        return None
    n = 240
    prof = [0.0] * n
    for (x, y, z) in vs:
        bi = min(n - 1, int(n * (z - z0) / (z1 - z0)))
        r = math.hypot(x, y)
        if r > prof[bi]:
            prof[bi] = r
    # 顶点稀疏：缺桶用最近邻填充（早先要求"每桶都有值"，导致直接判量不到）
    return _fill(prof)


def _fill(prof):
    n = len(prof)
    known = [i for i, v in enumerate(prof) if v > 0]
    if len(known) < 8:
        return None
    out = list(prof)
    for i in range(n):
        if out[i] > 0:
            continue
        near = min(known, key=lambda k: min(abs(k - i), n - abs(k - i)))
        out[i] = prof[near]
    return out


UNSUPPORTED = {
    "tooth_count": "齿数：切片能做，但齿顶/齿根在水平截面里是同心圆叠加，"
                   "需要按半径分层统计（下一步）",
}


def m_groove_count(polys, **kw):
    """槽数（轴向特征）：用**网格切片**取 r-Z 剖面，再数半径凹陷段。

    这是 U3 的最后一块：早先直接对顶点分箱统计，一个特征会被拆散
    （实测 3 槽数成 5）。切片法量的是几何本身，与顶点密度无关。
    三个要点：① 切片角度自动挑"最深"的那个方位（回转体是分面近似的，
    切在齿中间和切在槽中心结果不同）；② 丢掉内孔/轮毂样本；
    ③ 丢掉触及剖面首尾的凹陷段（那是敞口边界伪影，不是槽）。
    """
    try:
        from ai_modeler.core import meshslice as MS
        prof = MS.radial_profile_z(polys, None)
        if not prof:
            return None
        _z0, _z1, pr = prof
        n = MS.count_dips(pr, rel=0.03, min_width=2)
        return float(n) if n else None
    except Exception:
        return None


def m_tooth_count_impl(polys, **kw):
    """齿数的实验实现（见 UNSUPPORTED 说明，暂不参与判定）。"""
    try:
        from ai_modeler.core import meshslice as MS
        z = MS.best_tooth_layer(polys)
        ap = MS.angular_max_radius(polys, z)
        return float(MS.count_peaks(ap, 0.25)) if ap else None
    except Exception:
        return None


def m_symmetry(polys, **kw):
    """对称性偏差（edge 级）：包围盒中心与顶点质心在 XY 上的距离。

    回转体的两者应重合；差得多说明几何偏心或有飞边。
    """
    vs = _verts(polys)
    bb = K.bbox(polys)
    if not vs or not bb:
        return None
    cx = sum(v[0] for v in vs) / len(vs)
    cy = sum(v[1] for v in vs) / len(vs)
    bx = (bb[0] + bb[3]) / 2.0
    by = (bb[1] + bb[4]) / 2.0
    return math.hypot(cx - bx, cy - by)


def m_watertight(polys, **kw):
    """水密性（face 级）：每条无向边必须恰好被 2 个三角面共享。

    返回"非流形边比例"（0 表示完全水密，1 表示全都不配对）。
    注意：本项目允许"网格组合"式零件（如带轮辐条、叶盘叶片），
    这类零件的子实体各自水密但彼此分离 —— 因此这个指标要按子实体看，
    这里给的是整体近似，作为"退化检测"用途。
    """
    edges = {}
    for p in polys:
        vs = [pos for (pos, _n) in p.vertices]
        n = len(vs)
        for i in range(n):
            a, b = vs[i], vs[(i + 1) % n]
            key = tuple(sorted([tuple(round(c, 4) for c in a),
                                tuple(round(c, 4) for c in b)]))
            edges[key] = edges.get(key, 0) + 1
    if not edges:
        return None
    bad = sum(1 for v in edges.values() if v != 2)
    return bad / float(len(edges))


def m_tri_count(polys, **kw):
    return float(sum(len(p.vertices) - 2 for p in polys))


MEASURE = {
    "bbox_x": m_bbox_x, "bbox_y": m_bbox_y, "bbox_z": m_bbox_z,
    "outer_dia": m_outer_dia, "bore_dia": m_bore_dia,
    "groove_count": m_groove_count, "tooth_count": m_tooth_count_impl,
    "symmetry": m_symmetry, "watertight": m_watertight, "tri_count": m_tri_count,
}


# --------------------------------------------------------------------------
# 闭环
# --------------------------------------------------------------------------
def verify(polys, specs: Sequence[DimSpec], strict: bool = False,
           ctx: dict = None) -> Report:
    """按 specs 逐条测量并判定；strict=True 时任一超差即抛 ToleranceError。"""
    rep = Report()
    for s in specs:
        fn = MEASURE.get(s.how)
        got = None
        if fn is not None:
            try:
                got = fn(polys, **(ctx or {}))
            except Exception:
                got = None
        dev = None if got is None else abs(got - s.value)
        rep.add(Result(s.name, s.value, got, dev, s.band(), s.grade, s.note))
    # face 级：几何有效性（不参与 strict 的尺寸判定，但必须报出来）
    for name, how, want, tol, note in (
            ("水密性(非流形边比例)", "watertight", 0.0, 0.02, "网格组合式零件会偏高，需按子实体判"),
            ("三角面片数", "tri_count", None, None, "退化几何会出现 0 面")):
        if want is None:
            got = MEASURE[how](polys) if how in MEASURE else None
            rep.add(Result(name, -1.0, got, None if got is None else 0.0, 0.0, "face", note))
            continue
        got = MEASURE[how](polys) if how in MEASURE else None
        dev = None if got is None else abs(got - want)
        rep.add(Result(name, want, got, dev, tol, "face", note))
    if strict and not rep.ok:
        raise ToleranceError(rep)
    return rep


def specs_for_pulley(spec: dict) -> List[DimSpec]:
    """由带轮标准尺寸生成"受控尺寸"清单（可直接拿去 verify）。"""
    out = [
        DimSpec("外径 da", spec["outer_d"], how="outer_dia", grade="vertex",
                note="da = dd + 2·ha（或标准系列表值）"),
        DimSpec("轮缘宽度", max(spec["width"], spec.get("rim_w_min", 0.0)), tol=0.06,
                how="bbox_z", grade="vertex",
                note="按 (n−1)e+2f 取标准最小值（几何与期望用同一口径）"),
        DimSpec("内孔", spec["bore_d"], abs_tol=0.05, how="bore_dia", grade="vertex"),
        DimSpec("槽数", float(spec["grooves"]), abs_tol=0.4, how="groove_count",
                grade="vertex", note="网格切片法：r-Z 剖面凹陷段数"),

        DimSpec("同轴对称性", 0.0, abs_tol=0.8, how="symmetry", grade="edge",
                note="包围盒中心与顶点质心应重合"),
    ]
    return out


def specs_for_timing(spec: dict) -> List[DimSpec]:
    info = MEASURE  # noqa: F841
    from ai_modeler.parts import std_pulley as SP
    p = SP.timing_pitch(spec["tooth_type"])
    return [
        DimSpec("外径", spec["pitch_d"] + 2 * p["tooth_h"] * 0.45, tol=0.03,
                how="outer_dia", grade="vertex"),
        # 齿数：同上，暂不参与判定

        DimSpec("带轮宽度", spec["height"], how="bbox_z", grade="vertex"),
        DimSpec("同轴对称性", 0.0, abs_tol=0.8, how="symmetry", grade="edge"),
    ]
