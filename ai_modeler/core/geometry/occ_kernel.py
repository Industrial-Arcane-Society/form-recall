# -*- coding: utf-8 -*-
r"""OpenCASCADE（OCCT）几何内核后端 —— A 计划的可选加速/精确通道。

为什么要它（实测数据，见 `docs/升级方向…md` §0.34）：
    方块 120×120×20 上打 N 个 ⌀5 通孔：
        N=48  OCCT **0.49 s**（体积偏差 0.08%，那是真圆 vs 我 24 边形解析值之差）
        我们自己的 BSP 内核（A′ 局部布尔）27 s
    EGT006 ⌀30 圆型材真实有 **266 个径向交叉孔**，BSP 路径只能按预算建一部分。

设计原则（**不影响现有可用性**）：
  · 本模块**可选**：`OCP` 缺失时 `available()` 返回 False，上层照旧用 BSP 内核；
  · 开关走环境变量 `AI_MODELER_KERNEL=occ`；
  · 布尔统一用 **arguments/tools 列表接口**：一次布尔切多刀（逐刀切在长管上会
    O(n²)，实测 264 孔逐刀 277 s）；
  · 对外暴露 `shape_to_polys()`，把 OCCT 实体三角化回我们内核的多边形格式，
    这样**预览、度量、渲染、直写网格那些下游代码一行都不用改**。

⚠️ 实测踩过的坑（都写在对应位置）：
  1. OCCT 的 `BRepPrimAPI_MakeBox(dx,dy,dz)` **角点在原点**，不是以中心建；
  2. 一次 compound 当刀具体传给 `BRepAlgoAPI_Cut` 会**静默给出空结果**
     （实测体积 0），要用 `SetArguments/SetTools`；
  3. `BRepAlgoAPI_*` 必须 `Build()` 之后再取 `Shape()`，并检查 `IsDone()`。
"""
from __future__ import annotations

import math
import os
from typing import List, Sequence, Tuple

from ai_modeler.core import kernel as K

try:                                            # pragma: no cover
    from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
                                 BRepPrimAPI_MakePrism)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                    BRepBuilderAPI_MakeWire,
                                    BRepBuilderAPI_MakeFace)
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS, TopoDS_Compound
    from OCP.BRep import BRep_Builder
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Vec
    # OCP wheel builds have exposed this generated collection under both
    # ``collections`` and ``TopTools`` across supported releases.
    try:
        from OCP.collections import List_TopoDS_Shape
    except ImportError:
        from OCP.TopTools import TopTools_ListOfShape as List_TopoDS_Shape
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.Interface import Interface_Static
    from OCP.BRepTools import BRepTools
    _HAVE = True
except Exception:                               # noqa: BLE001
    _HAVE = False


def available() -> bool:
    """OCCT 是否可用（装 cadquery-ocp 才有）。"""
    return _HAVE


def enabled() -> bool:
    """环境变量开关：AI_MODELER_KERNEL=occ 时才用 OCC。"""
    return _HAVE and os.environ.get("AI_MODELER_KERNEL", "").lower() in ("occ", "occt", "opencascade")


def status() -> str:
    if not _HAVE:
        return "未安装（pip install cadquery-ocp；离线可用 tools\\fetch_ocp_wheel.py 下载本地装）"
    return "可用；切换：set AI_MODELER_KERNEL=occ"


# --------------------------------------------------------------------------
# 基本体
# --------------------------------------------------------------------------
def _dir(axis: str) -> "gp_Dir":
    return {'X': gp_Dir(1, 0, 0), 'Y': gp_Dir(0, 1, 0), 'Z': gp_Dir(0, 0, 1)}[
        (axis or 'Z').upper()]


def make_box(l: float, w: float, h: float, center=(0.0, 0.0, 0.0),
             base: bool = True):
    """长方体。`base=True` 时底面在 center 的轴向坐标上并朝 +轴生长（与内核一致）。"""
    cx, cy, cz = [float(v) for v in center]
    z0 = cz if base else cz - h / 2.0
    # ⚠ OCCT 的 MakeBox 角点在原点 → 必须自己平移
    shp = BRepPrimAPI_MakeBox(gp_Pnt(cx - l / 2.0, cy - w / 2.0, z0),
                              float(l), float(w), float(h)).Shape()
    return shp


def make_cylinder(r: float, h: float, center=(0.0, 0.0, 0.0), axis: str = 'Z',
                  base: bool = True):
    """圆柱。`base=True` 时从 center 沿 +轴长 h；否则以 center 为中点。"""
    cx, cy, cz = [float(v) for v in center]
    a = (axis or 'Z').upper()
    p0 = (cx, cy, cz) if base else {
        'Z': (cx, cy, cz - h / 2.0), 'X': (cx - h / 2.0, cy, cz),
        'Y': (cx, cy - h / 2.0, cz)}[a]
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p0), _dir(a)),
                                    float(r), float(h)).Shape()


def make_prism(profile: Sequence[Tuple[float, float]], height: float,
               center=(0.0, 0.0, 0.0), axis: str = 'Z', symmetric: bool = False):
    """把 2D 轮廓沿 axis 拉伸（用 OCCT 的 prism，真 B-rep 侧面）。"""
    u, v, w = K._axis_basis(axis)
    cx, cy, cz = [float(t) for t in center]
    e0 = gp_Pnt(cx, cy, cz)
    edges = []
    pts = [(float(a), float(b)) for (a, b) in profile]
    n = len(pts)
    for i in range(n):
        a0, b0 = pts[i]
        a1, b1 = pts[(i + 1) % n]
        p0 = gp_Pnt(cx + u[0] * a0 + v[0] * b0, cy + u[1] * a0 + v[1] * b0,
                    cz + u[2] * a0 + v[2] * b0)
        p1 = gp_Pnt(cx + u[0] * a1 + v[0] * b1, cy + u[1] * a1 + v[1] * b1,
                    cz + u[2] * a1 + v[2] * b1)
        edges.append(BRepBuilderAPI_MakeEdge(p0, p1).Edge())
    wire = BRepBuilderAPI_MakeWire()
    for e in edges:
        wire.Add(e)
    face = BRepBuilderAPI_MakeFace(wire.Wire()).Face()
    t0 = -height / 2.0 if symmetric else 0.0
    vec = gp_Vec(w[0] * height, w[1] * height, w[2] * height)
    return BRepPrimAPI_MakePrism(face, vec).Shape()


# --------------------------------------------------------------------------
# 布尔：arguments / tools 列表接口（一次切多刀）
# --------------------------------------------------------------------------
def cut_many(shape, cutters: Sequence):
    """一次布尔切掉一批刀具体。

    ⚠ 不能把 compound 当第二个参数传：OCCT 会**静默返回空结果**
      （实测 264 个刀具体的 compound 切完体积 0）。必须用 SetTools 列表。
    """
    lst = [c for c in cutters if c is not None]
    if not lst:
        return shape
    args = List_TopoDS_Shape(); args.Append(shape)
    tools = List_TopoDS_Shape()
    for c in lst:
        tools.Append(c)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("OCCT 布尔切除失败（IsDone=False）")
    return op.Shape()


def fuse_many(shapes: Sequence):
    lst = [s for s in shapes if s is not None]
    if not lst:
        return None
    if len(lst) == 1:
        return lst[0]
    args = List_TopoDS_Shape()
    for s in lst:
        args.Append(s)
    op = BRepAlgoAPI_Fuse()
    op.SetArguments(args)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("OCCT 布尔并集失败（IsDone=False）")
    return op.Shape()


# --------------------------------------------------------------------------
# 度量 / 三角化 / 导出
# --------------------------------------------------------------------------
def volume(shape) -> float:
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return float(p.Mass())


def _triangulated(shape, tol=0.12, ang=0.5):
    BRepMesh_IncrementalMesh(shape, float(tol), False, float(ang), True)


def shape_to_polys(shape, tol=0.12, max_faces: int = 800000) -> List[K.Polygon]:
    """把 OCCT 实体三角化回我们内核的多边形格式（下游代码不用改）。"""
    _triangulated(shape, tol)
    out: List[K.Polygon] = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        cur = ex.Current()
        face = TopoDS.Face_s(cur) if hasattr(TopoDS, "Face_s") else TopoDS.Face(cur)
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            nodes = []
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                nodes.append((p.X(), p.Y(), p.Z()))
            rev = (face.Orientation() == TopAbs_REVERSED)
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i)
                a, b, c = t.Get()
                pts = [nodes[a - 1], nodes[b - 1], nodes[c - 1]]
                if rev:
                    pts.reverse()
                pg = K.make_polygon(pts, 0)
                if pg is not None:
                    out.append(pg)
                    if len(out) >= max_faces:
                        ex.Next()
                        return out
        ex.Next()
    return out


def write_step(shape, path: str) -> bool:
    w = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    w.Transfer(shape, STEPControl_AsIs)
    return w.Write(path) == 1


def write_brep(shape, path: str) -> bool:
    return bool(BRepTools.Write_s(shape, path))


# --------------------------------------------------------------------------
# 文档级构建：把 docmodel 的特征表建成 OCCT 实体（**多刀一次布尔**）
# --------------------------------------------------------------------------
def _bbox(shape):
    """(lo, hi) 六元组，供孔位/轴线范围使用。"""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b)
    # ⚠ 不能用 b.Get()：cadquery-ocp 里它返回未注册类型 Bnd_Box::Limits，
    #   直接 TypeError（实测 "Unregistered type : Bnd_Box::Limits"）。
    #   用逐分量访问器。
    # 注意大小写：cadquery-ocp 里是 GetXMin/GetXMax（不是 GetXmin）
    return (b.GetXMin(), b.GetYMin(), b.GetZMin(),
            b.GetXMax(), b.GetYMax(), b.GetZMax())


def _axis_index(axis: str) -> int:
    return "XYZ".index((axis or 'Z').upper())


def _place(a: float, b: float, axis: str, along: float):
    """与内核 `_place` 同一口径：面内两轴按**升序**（= 提取器 (u,v) 口径）。"""
    ax = (axis or 'Z').upper()
    if ax == 'X':
        return (along, a, b)
    if ax == 'Y':
        return (a, along, b)
    return (a, b, along)


def _holes_of(feature, bb):
    """把 `hole` 特征展开成"每个孔一根圆柱刀具体"。"""
    p = feature.params
    axis = (p.get('axis') or 'Z').upper()
    r = max(1e-3, float(p.get('diameter', 10)) / 2.0)
    seg = 24
    pm = p.get('pos', 'center')
    if pm == 'corners':
        if bb:
            m = float(p.get('margin', 0) or 0) + r
            ai = _axis_index(axis)
            lo1, hi1 = bb[ai], bb[3 + ai]
            oth = [i for i in range(3) if i != ai]
            lo2, hi2 = bb[oth[0]], bb[3 + oth[0]]
            lo3, hi3 = bb[oth[1]], bb[3 + oth[1]]
            positions = [(lo2 + m, lo3 + m), (hi2 - m, lo3 + m),
                         (hi2 - m, hi3 - m), (lo2 + m, hi3 - m)]
        else:
            positions = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
    elif pm == 'pcd':
        n = max(2, int(p.get('count', 4)))
        pcd = float(p.get('pcd', 60.0)) / 2.0
        ph = math.radians(float(p.get('phase', 0.0)))
        positions = [(pcd * math.cos(2 * math.pi * i / n + ph),
                      pcd * math.sin(2 * math.pi * i / n + ph)) for i in range(n)]
    elif p.get('x') is not None or p.get('y') is not None:
        positions = [(float(p.get('x', 0.0)), float(p.get('y', 0.0)))]
    else:
        positions = [(0.0, 0.0)]
    through = bool(p.get('through', True)) or not p.get('depth')
    if bb:
        lo, hi = bb[_axis_index(axis)], bb[3 + _axis_index(axis)]
    else:
        lo, hi = -50.0, 50.0
    out = []
    for (a, b) in positions:
        if through:
            length = (hi - lo) + 20.0
            start = lo - 10.0
        else:
            depth = min(float(p.get('depth', 5.0)), max(1e-3, hi - lo))
            length = depth + 1.0
            start = hi - depth
        c = _place(a, b, axis, start)
        out.append(make_cylinder(r, length, c, axis, base=True))
    return out


def build_document(doc, tol: float = 0.12):
    """把 `docmodel.Document` 建成 OCCT 实体。

    支持的特征：box / cylinder / extrude_holes / hole / prism / pocket(矩形)。
    切除**按组一次布尔**（同一特征产生的多个刀具体一起切）——
    实测 264 个孔逐刀切要 277 s，一次布尔 0.05~30 s 级别。
    """
    if not _HAVE:
        raise RuntimeError("OCCT 不可用")
    shape = None
    cuts = []
    for f in doc.features:
        if not f.enabled:
            continue
        p = f.params
        k = f.kind
        bb = _bbox(shape) if shape is not None else None
        if k == 'box':
            c = tuple(float(v) for v in p.get('center', (0, 0, 0)))
            h = float(p.get('height', 10))
            l = float(p.get('length', 10))
            w = float(p.get('width', 10))
            ax = (p.get('axis') or 'Z').upper()
            base = bool(p.get('base', True))
            u, v, wv = K._axis_basis(ax)
            # 面内两轴按升序（与内核 extrude_profile 口径一致）
            p0 = (c[0] - (u[0] * l + v[0] * w) / 2.0,
                  c[1] - (u[1] * l + v[1] * w) / 2.0,
                  c[2] - (u[2] * l + v[2] * w) / 2.0)
            s = make_box(l, w, h, p0, base=True) if base else \
                make_box(l, w, h, (p0[0], p0[1], p0[2]), base=True)
            # 非 Z 轴时按轴向重排
            if ax != 'Z':
                prof = K.profile_rect(l, w, 0.0, 'fillet')
                s = make_prism(prof, h, c, ax, symmetric=not base)
            if shape is None:
                shape = s
            else:
                cuts.append(s)
        elif k == 'cylinder':
            c = tuple(float(v) for v in p.get('center', (0, 0, 0)))
            h = float(p.get('height', 10))
            base = bool(p.get('base', True))
            s = make_cylinder(float(p.get('radius', 5)), h, c,
                              (p.get('axis') or 'Z'), base=base)
            if shape is None:
                shape = s
            else:
                cuts.append(s)
        elif k == 'extrude_holes':
            sa = float(p.get('sec_a', 80)); sb = float(p.get('sec_b', 80))
            ln = float(p.get('length', 100))
            ax = (p.get('axis') or 'Z').upper()
            base = float(p.get('base', 0.0))
            prof = K.profile_rect(sa, sb, 0.0, 'fillet')
            ai = _axis_index(ax)
            cen = [0.0, 0.0, 0.0]
            cen[ai] = base + ln / 2.0
            s = make_prism(prof, ln, tuple(cen), ax, symmetric=True)
            if shape is None:
                shape = s
                bb2 = _bbox(shape)
                for h in (p.get('holes') or []):
                    d = float(h.get('d') or 0.0)
                    if d <= 0:
                        continue
                    hax = (h.get('axis') or 'Z').upper()
                    hai = _axis_index(hax)
                    oth = [i for i in range(3) if i != hai]
                    u = float(h.get('u') or 0.0)
                    v = float(h.get('v') or 0.0)
                    rmin, rsiz = p.get('ref_min'), p.get('ref_size')
                    if rmin and rsiz:
                        u -= float(rmin[oth[0]]) + float(rsiz[oth[0]]) / 2.0
                        v -= float(rmin[oth[1]]) + float(rsiz[oth[1]]) / 2.0
                    cen2 = [0.0, 0.0, 0.0]
                    cen2[oth[0]] = u
                    cen2[oth[1]] = v
                    cen2[hai] = base - 10.0
                    cuts.append(make_cylinder(d / 2.0, ln + 20.0, tuple(cen2),
                                              hax, base=True))
            else:
                cuts.append(s)
        elif k == 'hole':
            if bb is None:
                continue
            if f.op == 'cut':
                cuts.extend(_holes_of(f, bb))
            else:
                s = _holes_of(f, bb)
                cuts.extend(s)
        elif k == 'prism':
            prof = [(float(a), float(b)) for (a, b) in p.get('profile', [])]
            if len(prof) >= 3:
                s = make_prism(prof, float(p.get('height', 10)),
                               tuple(float(v) for v in p.get('center', (0, 0, 0))),
                               (p.get('axis') or 'Z'))
                if shape is None:
                    shape = s
                else:
                    cuts.append(s)
        elif k == 'pocket':
            c = tuple(float(v) for v in (p.get('x') or 0.0, p.get('y') or 0.0))
            if bb:
                ai = 2
                c = ((bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0)
            depth = float(p.get('depth', 5.0))
            top = bb[5] + 0.5 if bb else depth
            prof = K.profile_rect(float(p.get('length', 20)), float(p.get('width', 20)),
                                  float(p.get('corner', 0) or 0), 'fillet')
            cuts.append(make_prism(prof, depth + 0.5,
                                   (c[0], c[1], top - depth - 0.25), 'Z'))
    if shape is None:
        return None
    if cuts:
        shape = cut_many(shape, cuts)
    return shape


def build_polys(doc, tol: float = 0.12):
    """建出实体并三角化回多边形（预览/度量/渲染直接可用）。"""
    s = build_document(doc, tol)
    return shape_to_polys(s, tol) if s is not None else []
