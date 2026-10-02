# -*- coding: utf-8 -*-
r"""OCCT-backed BREP execution for CADPlan and Document.

The CADPlan SDK uses this as its authoritative local solid kernel. If the
runtime dependency is missing or an operation fails, the caller receives an
explicit error; this path never silently falls back to BSP polygons. Renderer
and STL APIs receive a display/export tessellation derived from the BREP.

CADPlan-local BREP support is separate from CATIA feature translation. Some
legacy recipe features are converted into an explicitly approximate faceted
BREP; `GEOMETRY_SUPPORT` records that boundary. Unsupported/incomplete feature
semantics fail closed.
"""
from __future__ import annotations

import math
import os
import importlib.metadata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ai_modeler.core import kernel as K

try:                                            # pragma: no cover
    import OCP
    from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone,
                                 BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism,
                                 BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeSphere)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                    BRepBuilderAPI_MakeWire,
                                    BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakePolygon,
                                    BRepBuilderAPI_MakeSolid,
                                    BRepBuilderAPI_Sewing,
                                    BRepBuilderAPI_Transform)
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool, BRep_Builder
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.TopAbs import (TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE,
                            TopAbs_IN, TopAbs_OUT, TopAbs_REVERSED,
                            TopAbs_SHELL, TopAbs_SOLID)
    from OCP.TopExp import TopExp
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS, TopoDS_Compound
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.gp import gp_Ax1, gp_Ax2, gp_Lin, gp_Pnt, gp_Dir, gp_Vec, gp_Trsf
    # OCP wheel builds have exposed this generated collection under both
    # ``collections`` and ``TopTools`` across supported releases.
    try:
        from OCP.collections import List_TopoDS_Shape
    except ImportError:
        # cadquery-ocp 7.9+ exposes this generated collection under TopTools.
        from OCP.TopTools import TopTools_ListOfShape as List_TopoDS_Shape
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.Interface import Interface_Static
    from OCP.BRepTools import BRepTools
    _HAVE = True
    _IMPORT_ERROR = None
except Exception as exc:                        # noqa: BLE001
    _HAVE = False
    _IMPORT_ERROR = exc


def available() -> bool:
    """OCCT 是否可用（装 cadquery-ocp 才有）。"""
    return _HAVE


def enabled() -> bool:
    """True when the required local CADPlan backend is available."""
    return _HAVE


def status() -> str:
    if not _HAVE:
        return "不可用（安装 requirements-geometry.txt；%s）" % _IMPORT_ERROR
    return "可用：OCCT %s / cadquery-ocp-novtk %s" % (
        getattr(OCP, "__version__", "unknown"), _package_version())


def _package_version() -> str:
    for name in ("cadquery-ocp-novtk", "cadquery-ocp"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "unknown"


def require_available() -> None:
    if not _HAVE:
        raise RuntimeError(
            "CADPlan 本地建模需要 OpenCASCADE BREP 内核；请在运行桌面程序的"
            " Python 环境执行 `python -m pip install -r requirements-geometry.txt`。"
            "不会退回 BSP 网格内核。底层导入错误：%s" % _IMPORT_ERROR)


# --------------------------------------------------------------------------
# 基本体
# --------------------------------------------------------------------------
def _dir(axis: str) -> "gp_Dir":
    return {'X': gp_Dir(1, 0, 0), 'Y': gp_Dir(0, 1, 0), 'Z': gp_Dir(0, 0, 1)}[
        (axis or 'Z').upper()]


def make_box(l: float, w: float, h: float, center=(0.0, 0.0, 0.0),
             base: bool = True, axis: str = "Z", corner: float = 0.0,
             corner_mode: str = "fillet"):
    """Make an extruded rectangle in the requested global axis frame.

    `center` is the in-plane rectangle center and, for base=True, the bottom
    face point along the axis.  With base=False it is the geometric center.
    """
    profile = K.profile_rect(float(l), float(w), float(corner), corner_mode)
    return make_prism(profile, float(h), tuple(map(float, center)), axis,
                      symmetric=not bool(base))


def make_cylinder(r: float, h: float, center=(0.0, 0.0, 0.0), axis: str = 'Z',
                  base: bool = True, radius2: Optional[float] = None):
    """Analytic cylinder or cone, with an explicit base/center convention."""
    cx, cy, cz = [float(v) for v in center]
    a = (axis or 'Z').upper()
    p0 = (cx, cy, cz) if base else {
        'Z': (cx, cy, cz - h / 2.0), 'X': (cx - h / 2.0, cy, cz),
        'Y': (cx, cy - h / 2.0, cz)}[a]
    ax = gp_Ax2(gp_Pnt(*p0), _dir(a))
    if radius2 is None or abs(float(radius2) - float(r)) <= 1e-10:
        return BRepPrimAPI_MakeCylinder(ax, float(r), float(h)).Shape()
    return BRepPrimAPI_MakeCone(ax, float(r), float(radius2), float(h)).Shape()


def make_prism(profile: Sequence[Tuple[float, float]], height: float,
               center=(0.0, 0.0, 0.0), axis: str = 'Z', symmetric: bool = False):
    """Extrude a validated polygon profile along its explicit axis frame."""
    pts = validate_simple_profile(profile)
    u, v, w = K._axis_basis(axis)
    cx, cy, cz = [float(t) for t in center]
    if symmetric:
        cx -= w[0] * float(height) / 2.0
        cy -= w[1] * float(height) / 2.0
        cz -= w[2] * float(height) / 2.0
    edges = []
    pts = [(float(a), float(b)) for (a, b) in pts]
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


def _area2(points: Sequence[Tuple[float, float]]) -> float:
    return sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))


def _orientation(a, b, c, eps=1e-10):
    value = ((b[0] - a[0]) * (c[1] - a[1])
             - (b[1] - a[1]) * (c[0] - a[0]))
    return 0 if abs(value) <= eps else 1 if value > 0 else -1


def _on_segment(a, b, p, eps=1e-10):
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def _segments_intersect(a, b, c, d, eps=1e-10):
    ab_c, ab_d = _orientation(a, b, c, eps), _orientation(a, b, d, eps)
    cd_a, cd_b = _orientation(c, d, a, eps), _orientation(c, d, b, eps)
    if ab_c != ab_d and cd_a != cd_b:
        return True
    return ((ab_c == 0 and _on_segment(a, b, c, eps))
            or (ab_d == 0 and _on_segment(a, b, d, eps))
            or (cd_a == 0 and _on_segment(c, d, a, eps))
            or (cd_b == 0 and _on_segment(c, d, b, eps)))


def validate_simple_profile(profile: Sequence[Sequence[float]],
                            radial: bool = False) -> List[Tuple[float, float]]:
    """Return a normalized simple polygon or raise a precise geometry error."""
    points = [(float(p[0]), float(p[1])) for p in profile]
    if len(points) > 3 and all(abs(a - b) <= 1e-10
                               for a, b in zip(points[0], points[-1])):
        points.pop()
    if len(points) < 3:
        raise ValueError("轮廓至少需要三个不同的二维点。")
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        if math.dist(point, nxt) <= 1e-10:
            raise ValueError("轮廓含有重合的相邻点，无法创建封闭草图。")
        if radial and point[0] < -1e-9:
            raise ValueError("回转轮廓半径不能小于 0。")
    if abs(_area2(points)) <= 1e-10:
        raise ValueError("轮廓面积为零或退化，不能创建封闭草图。")
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or j == (i + 1) % n or i == (j + 1) % n:
                continue
            c, d = points[j], points[(j + 1) % n]
            if _segments_intersect(a, b, c, d):
                raise ValueError("轮廓自相交（线段 %d 与 %d），请修正点序。" % (i, j))
    if _area2(points) < 0:
        points.reverse()
    return points


def _wire_from_xyz(points: Sequence[Sequence[float]]):
    if len(points) < 3:
        raise ValueError("封闭轮廓至少需要三个三维点。")
    builder = BRepBuilderAPI_MakePolygon()
    for point in points:
        if len(point) != 3 or not all(math.isfinite(float(v)) for v in point):
            raise ValueError("轮廓包含非有限三维坐标。")
        builder.Add(gp_Pnt(*(float(v) for v in point)))
    builder.Close()
    if not builder.IsDone():
        raise RuntimeError("OCCT 无法闭合轮廓线。")
    return builder.Wire()


def make_revolve(profile: Sequence[Sequence[float]], angle: float = 360.0,
                 center=(0.0, 0.0, 0.0), axis: str = "Z"):
    """Sweep a validated [radius, axial-coordinate] profile around X/Y/Z."""
    pts = validate_simple_profile(profile, radial=True)
    angle = float(angle)
    if abs(angle) <= 1e-9 or abs(angle) > 360.0 + 1e-8:
        raise ValueError("回转角度必须在 (-360,0) 或 (0,360] 度内。")
    ax = (axis or "Z").upper()
    if ax not in "XYZ":
        raise ValueError("回转轴必须为 X、Y 或 Z。")
    u, _, w = K._axis_basis(ax)
    c = tuple(float(v) for v in center)
    points = [(c[0] + u[0] * r + w[0] * z,
               c[1] + u[1] * r + w[1] * z,
               c[2] + u[2] * r + w[2] * z) for r, z in pts]
    wire = _wire_from_xyz(points)
    face = BRepBuilderAPI_MakeFace(wire, True)
    if not face.IsDone():
        raise ValueError("回转轮廓不能形成平面封闭面。")
    op = BRepPrimAPI_MakeRevol(
        face.Face(), gp_Ax1(gp_Pnt(*c), _dir(ax)), math.radians(angle), True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("OCCT 回转失败。")
    return op.Shape()


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
    args = List_TopoDS_Shape(); args.Append(lst[0])
    tools = List_TopoDS_Shape()
    for s in lst[1:]:
        tools.Append(s)
    op = BRepAlgoAPI_Fuse()
    op.SetArguments(args)
    op.SetTools(tools)
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


def surface_area(shape) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return float(props.Mass())


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
# BREP validation, faceted approximation bridge, and ordered feature execution
# --------------------------------------------------------------------------
GEOMETRY_SUPPORT = {
    "box": ("exact", "OCCT 棱柱；带圆角/倒角时沿用离散轮廓近似"),
    "cylinder": ("exact", "解析圆柱或圆锥面"),
    "socket_head_bolt": ("approximate", "当前参数化网格转封闭 facet-BREP"),
    "sphere": ("exact", "OCCT 解析球面"),
    "prism": ("exact", "多边形轮廓棱柱"),
    "revolve": ("exact", "简单闭合半径-轴向轮廓的 OCCT 解析回转"),
    "gear": ("approximate", "梯形齿廓为当前简化齿形，几何由多边形棱柱表达"),
    "loft": ("approximate", "翼型截面离散后由 OCCT 放样"),
    "hole": ("exact", "圆柱刀具体与 BREP 布尔切除"),
    "pocket": ("exact", "矩形/离散圆角轮廓与 BREP 布尔切除"),
    "pattern": ("exact", "按 CADPlan 来源特征执行实体平移/旋转变换"),
    "helix": ("approximate", "现有螺旋网格转封闭 facet-BREP"),
    "spoked_wheel": ("exact", "解析轮缘与轮毂回转体、显式拉伸辐条并集成一个闭合实体"),
    "piston": ("exact", "闭合多段线剖面原生 OCCT 回转，横向销孔解析布尔切除"),
    "crankshaft": ("approximate", "解析主轴颈/偏心连杆颈与矩形曲柄臂并集，曲柄臂外形近似"),
    "vbelt_pulley": ("approximate", "标准槽轴向截面原生 OCCT 回转；槽根间距为保证剖面简单可能近似调整，输入轮宽不足时明确拒绝"),
    "timing_pulley": ("approximate", "现有同步带轮配方转封闭 facet-BREP"),
    "worm": ("approximate", "现有蜗杆配方转封闭 facet-BREP"),
    "helical_gear": ("approximate", "保留当前省略螺旋角的直齿近似并转 facet-BREP"),
    "bevel_gear": ("approximate", "现有锥齿轮网格配方转 facet-BREP"),
    "camshaft": ("approximate", "现有凸轮轴网格配方转 facet-BREP"),
    "impeller": ("approximate", "现有叶轮网格配方转 facet-BREP"),
    "conrod": ("approximate", "解析大小头环与外公切梯形杆身并集，工字截面近似"),
    "cross_joint": ("approximate", "现有万向节网格配方转 facet-BREP"),
    "scroll_disc": ("approximate", "简单多边形涡旋壁原生拉伸并与背盘合并；壁面以多段线近似渐开线"),
    "rotor": ("approximate", "圆角三角转子离散轮廓原生拉伸并按需切中心孔"),
    "shell": ("unavailable", "CADPlan 未指定要移除的面；缺少可执行的抽壳边界"),
    "extrude_holes": ("exact", "矩形棱柱加解析圆柱贯穿孔"),
    "real_solid": ("approximate", "内置离散实物网格转封闭 facet-BREP"),
    "hex_bolt": ("approximate", "现有螺栓网格配方转封闭 facet-BREP"),
}


class GeometryBuildError(RuntimeError):
    """A feature failed without mutating the last valid intermediate BREP."""

    def __init__(self, message, last_valid_shape=None, report=None):
        super().__init__(message)
        self.last_valid_shape = last_valid_shape
        self.report = report or {}


def _bbox(shape):
    """Return a finite global XYZ bounding box."""
    if shape is None:
        return None
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    # Add_s expands the result by the stored edge/vertex tolerances, which
    # creates a visible ~1e-7 mm size error even for analytic boxes.  Use the
    # precise geometric bounds and do not include shape tolerances in the
    # dimension summary.
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        return None
    # Current cadquery-ocp 7.9 returns a registered six-float tuple here.
    # Keep a fallback for older bindings that expose per-component accessors.
    try:
        result = tuple(box.Get())
    except Exception:
        result = (box.GetXMin(), box.GetYMin(), box.GetZMin(),
                  box.GetXMax(), box.GetYMax(), box.GetZMax())
    if not all(math.isfinite(float(v)) for v in result):
        return None
    return tuple(float(v) for v in result)


def _shape_count(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _free_edge_count(shape):
    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    return sum(1 for i in range(1, mapping.Extent() + 1)
               if mapping.FindFromIndex(i).Size() != 2)


SELF_INTERSECTION_FACE_LIMIT = 15000


def _self_intersection_check(shape, face_limit=SELF_INTERSECTION_FACE_LIMIT):
    faces = _shape_count(shape, TopAbs_FACE)
    if faces > face_limit:
        return {"status": "not_checked_complexity_limit", "face_count": faces,
                "face_limit": face_limit,
                "method": "BOPAlgo_ArgumentAnalyzer self-interference"}
    try:
        from OCP.BOPAlgo import BOPAlgo_ArgumentAnalyzer
        checker = BOPAlgo_ArgumentAnalyzer()
        checker.SetShape1(shape)
        checker.SelfInterMode = True
        checker.SetUseOBB(True)
        checker.SetRunParallel(True)
        checker.Perform()
        if checker.HasErrors():
            return {"status": "failed", "face_count": faces,
                    "face_limit": face_limit,
                    "method": "BOPAlgo_ArgumentAnalyzer(SelfInterMode=True, UseOBB=True)",
                    "message": "OCCT 自相交分析报告错误。"}
        result = checker.GetCheckResult()
        # OCP 7.9 exposes this as BOPAlgo_ListOfCheckResult (a linked-list
        # wrapper), not an indexed sequence: it has First/RemoveFirst but no
        # Value(i). Keep the status unknown if a future binding changes again.
        statuses = []
        while not result.IsEmpty():
            item = result.First()
            check_status = item.GetCheckStatus()
            statuses.append(getattr(check_status, "name", str(check_status)))
            result.RemoveFirst()
        intersecting = "BOPAlgo_SelfIntersect" in statuses
        return {"status": "self_intersection" if intersecting else "checked_no_intersection",
                "face_count": faces, "face_limit": face_limit,
                "check_result_count": len(statuses),
                "check_statuses": statuses,
                "method": "BOPAlgo_ArgumentAnalyzer(SelfInterMode=True, UseOBB=True)"}
    except Exception as exc:  # report the capability boundary, never infer False
        return {"status": "not_checked_api_error", "face_count": faces,
                "face_limit": face_limit,
                "method": "BOPAlgo_ArgumentAnalyzer self-interference",
                "message": str(exc)}


def inspect_shape(shape, self_intersection_face_limit=SELF_INTERSECTION_FACE_LIMIT,
                  check_self_intersection=True):
    """Deterministic OCCT topology and mass-property facts for a candidate."""
    require_available()
    if shape is None or shape.IsNull():
        return {"brep_valid": False, "error": "empty_shape", "volume_mm3": 0.0,
                "bbox_mm": None, "solid_count": 0, "shell_count": 0,
                "closed": False, "free_edge_count": None,
                "self_intersection": {"status": "not_checked_empty"}}
    analyzer = BRepCheck_Analyzer(shape, True)
    valid = bool(analyzer.IsValid())
    solid_count = _shape_count(shape, TopAbs_SOLID)
    shell_count = _shape_count(shape, TopAbs_SHELL)
    closed_shell_count = 0
    ex = TopExp_Explorer(shape, TopAbs_SHELL)
    while ex.More():
        try:
            shell = TopoDS.Shell_s(ex.Current())
            if BRep_Tool.IsClosed_s(shell):
                closed_shell_count += 1
        except Exception:
            pass
        ex.Next()
    try:
        vol = volume(shape)
    except Exception:
        vol = float("nan")
    try:
        free_edges = _free_edge_count(shape)
    except Exception:
        free_edges = None
    return {
        "brep_valid": valid,
        "shape_type": shape.ShapeType().name,
        "solid_count": solid_count,
        "connected_component_count": solid_count,
        "shell_count": shell_count,
        "closed_shell_count": closed_shell_count,
        "closed": shell_count > 0 and closed_shell_count == shell_count,
        "edge_count": _shape_count(shape, TopAbs_EDGE),
        "face_count": _shape_count(shape, TopAbs_FACE),
        "free_edge_count": free_edges,
        "volume_mm3": float(vol),
        "bbox_mm": _bbox(shape),
        "self_intersection": (_self_intersection_check(
            shape, self_intersection_face_limit) if check_self_intersection else
            {"status": "deferred_to_final_shape", "face_count": _shape_count(shape, TopAbs_FACE)}),
        "validity_method": "BRepCheck_Analyzer",
        "geometry_scope": "native_OCCT_BREP_only_visual_comparison_not_performed",
    }


def _assert_valid_solid(shape, label):
    info = inspect_shape(shape, check_self_intersection=False)
    if not info["brep_valid"]:
        raise RuntimeError("%s 的 OCCT BREP 无效。" % label)
    if info["solid_count"] <= 0 or not info["closed"]:
        raise RuntimeError("%s 未形成至少一个闭合实体。" % label)
    if not math.isfinite(info["volume_mm3"]) or info["volume_mm3"] <= 1e-12:
        raise RuntimeError("%s 实体体积无效。" % label)
    return info


def _compound(shapes):
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def _triangle_mesh_components(triangles, vertex_digits=7):
    """Group triangles by shared edges before sewing intersecting recipe parts."""
    count = len(triangles)
    parent = list(range(count))
    rank = [0] * count

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    def point_key(point):
        return tuple(round(float(axis), vertex_digits) for axis in point)

    edges = {}
    for index, triangle in enumerate(triangles):
        points = [point_key(point) for point in triangle[:3]]
        for edge_index in range(3):
            a, b = points[edge_index], points[(edge_index + 1) % 3]
            key = tuple(sorted((a, b)))
            existing = edges.get(key)
            if existing is None:
                edges[key] = index
            else:
                union(index, existing)
    grouped = {}
    for index, triangle in enumerate(triangles):
        grouped.setdefault(find(index), []).append(triangle)
    return list(grouped.values())


def _sew_triangle_component(triangles, tolerance):
    sewing = BRepBuilderAPI_Sewing(float(tolerance), True, True, True, False)
    for triangle in triangles:
        pts = [tuple(float(v) for v in point) for point in triangle[:3]]
        if any(not all(math.isfinite(v) for v in point) for point in pts):
            raise ValueError("离散特征包含非有限坐标。")
        if K.v_len(K.v_cross(K.v_sub(pts[1], pts[0]),
                             K.v_sub(pts[2], pts[0]))) <= 1e-12:
            continue
        wire = _wire_from_xyz(pts)
        face = BRepBuilderAPI_MakeFace(wire, True)
        if not face.IsDone():
            raise ValueError("OCCT 无法构造某个离散三角面。")
        sewing.Add(face.Face())
    sewing.Perform()
    sewn = sewing.SewedShape()
    shells = []
    ex = TopExp_Explorer(sewn, TopAbs_SHELL)
    while ex.More():
        shells.append(TopoDS.Shell_s(ex.Current()))
        ex.Next()
    if not shells and sewn.ShapeType() == TopAbs_SHELL:
        shells = [TopoDS.Shell_s(sewn)]
    if not shells:
        raise ValueError("三角面未缝合成 shell（自由边 %d）。" % sewing.NbFreeEdges())
    solids = []
    for index, shell in enumerate(shells, 1):
        if not BRep_Tool.IsClosed_s(shell):
            raise ValueError("shell 未闭合（第 %d 个 shell，自由边 %d）。" %
                             (index, sewing.NbFreeEdges()))
        solid_builder = BRepBuilderAPI_MakeSolid(shell)
        if not solid_builder.IsDone():
            raise ValueError("第 %d 个闭合 shell 无法构造 solid。" % index)
        solids.append(solid_builder.Solid())
    return solids, sewing.NbFreeEdges()


def _polygon_mesh_to_brep(polygons, tolerance=1e-5,
                          max_faces=120000):
    """Convert legacy recipe triangles to validated faceted BREP components.

    Surface groups that overlap in space (for example a helical thread crossing
    a shaft) are sewn independently, then Boolean-unioned as solids. This avoids
    creating a non-manifold shell by sewing crossing surfaces together. Any
    component with open edges or an invalid Boolean is rejected explicitly.
    """
    require_available()
    triangles = K.polygons_to_triangles(polygons)
    if not triangles:
        raise ValueError("特征没有可转换的三角面。")
    if len(triangles) > int(max_faces):
        raise ValueError("facet-BREP 面数 %d 超过上限 %d。" %
                         (len(triangles), int(max_faces)))
    groups = _triangle_mesh_components(triangles)
    solids, free_edges = [], []
    for index, group in enumerate(groups, 1):
        try:
            group_solids, group_free_edges = _sew_triangle_component(group, tolerance)
        except Exception as exc:
            raise ValueError("离散部件 %d/%d 缝合失败：%s" %
                             (index, len(groups), exc)) from exc
        solids.extend(group_solids)
        free_edges.append(group_free_edges)
    if not solids:
        raise ValueError("离散特征未形成任何闭合实体。")
    unmerged = len(solids) > 1
    result = solids[0] if len(solids) == 1 else fuse_many(solids)
    info = _assert_valid_solid(result, "离散特征")
    if info.get("free_edge_count") != 0 or not info.get("closed"):
        raise ValueError("离散特征 Boolean union 后未得到闭合 BREP（自由边 %s）。" %
                         info.get("free_edge_count"))
    info["conversion"] = "faceted_brep_approximation"
    info["input_triangle_count"] = len(triangles)
    info["mesh_component_count"] = len(groups)
    info["input_solid_component_count"] = len(solids)
    info["component_fusion_attempted"] = unmerged
    info["component_sewing_free_edge_counts"] = free_edges
    info["sewing_tolerance_mm"] = float(tolerance)
    return result, info


def _axis_index(axis: str) -> int:
    axis = (axis or 'Z').upper()
    if axis not in "XYZ":
        raise ValueError("axis 必须为 X、Y 或 Z。")
    return "XYZ".index(axis)


def _coaxial_hole_axis(first: Mapping[str, Any], second: Mapping[str, Any],
                       tolerance: float = 1e-5) -> bool:
    """True when two axis-aligned hole centerlines describe the same line.

    Axis direction may be reversed. Points can differ along that direction;
    only perpendicular distance matters.
    """
    axes = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
    a = axes.get(str(first.get("axis", "Z")).upper())
    b = axes.get(str(second.get("axis", "Z")).upper())
    if a is None or b is None or abs(sum(x * y for x, y in zip(a, b))) < 1.0 - tolerance:
        return False
    p = tuple(float(x) for x in first.get("centerline_mm", ()))
    q = tuple(float(x) for x in second.get("centerline_mm", ()))
    if len(p) != 3 or len(q) != 3:
        return False
    delta = tuple(q[i] - p[i] for i in range(3))
    cross = (delta[1] * a[2] - delta[2] * a[1],
             delta[2] * a[0] - delta[0] * a[2],
             delta[0] * a[1] - delta[1] * a[0])
    return math.sqrt(sum(value * value for value in cross)) <= tolerance


def _same_through_hole_axis(first: Mapping[str, Any], second: Mapping[str, Any],
                            tolerance: float = 1e-5) -> bool:
    """True when a new through cut is fully contained in an existing through bore."""
    if not first.get("through") or not second.get("through"):
        return False
    old_diameter = float(first.get("diameter_mm", 0.0) or 0.0)
    new_diameter = float(second.get("diameter_mm", 0.0) or 0.0)
    # A wider new through bore is a legitimate enlargement.
    return (old_diameter > 0 and new_diameter > 0
            and new_diameter <= old_diameter + tolerance
            and _coaxial_hole_axis(first, second, tolerance))


def _plane_indices(axis: str):
    return [i for i in range(3) if i != _axis_index(axis)]


def _place(a: float, b: float, axis: str, along: float):
    """Plane coordinates use ascending global axes, as Document does."""
    axis = (axis or 'Z').upper()
    if axis == 'X':
        return (float(along), float(a), float(b))
    if axis == 'Y':
        return (float(a), float(along), float(b))
    return (float(a), float(b), float(along))


def _bounds_for_axis(bb, axis):
    i = _axis_index(axis)
    return bb[i], bb[i + 3]


def _hole_positions(params, bb, axis):
    mode = params.get("pos", "center")
    plane = _plane_indices(axis)
    lo1, hi1 = bb[plane[0]], bb[plane[0] + 3]
    lo2, hi2 = bb[plane[1]], bb[plane[1] + 3]
    if mode == "corners":
        r = float(params.get("diameter", 10.0)) / 2.0
        margin = float(params.get("margin", 0.0) or 0.0) + r
        return [(lo1 + margin, lo2 + margin), (hi1 - margin, lo2 + margin),
                (hi1 - margin, hi2 - margin), (lo1 + margin, hi2 - margin)]
    if mode == "pcd":
        count = max(1, int(params.get("count", 4)))
        radius = float(params.get("pcd", 60.0)) / 2.0
        phase = math.radians(float(params.get("phase", 0.0)))
        return [(radius * math.cos(2 * math.pi * i / count + phase),
                 radius * math.sin(2 * math.pi * i / count + phase))
                for i in range(count)]
    if params.get("pos") == "point" and isinstance(params.get("point"), (list, tuple)):
        point = params["point"]
        if len(point) != 3:
            raise ValueError("点孔必须提供全局 XYZ point。")
        return [(float(point[plane[0]]), float(point[plane[1]]))]
    if params.get("x") is not None or params.get("y") is not None:
        return [(float(params.get("x", (lo1 + hi1) / 2.0)),
                 float(params.get("y", (lo2 + hi2) / 2.0)))]
    return [((lo1 + hi1) / 2.0, (lo2 + hi2) / 2.0)]


def _hole_tools(feature, bbox, feature_id=None, host_shape=None):
    params = feature.params
    axis = (params.get("axis") or "Z").upper()
    low, high = _bounds_for_axis(bbox, axis)
    through = bool(params.get("through", True)) or not params.get("depth")
    diameter = float(params.get("diameter", 10.0))
    if diameter <= 0:
        raise ValueError("孔径必须大于零。")
    depth = None
    if not through:
        depth = float(params["depth"])
        depth_tolerance = max(1e-6, (high - low) * 1e-8)
        if depth <= 0 or depth >= (high - low) - depth_tolerance:
            raise ValueError("盲孔深度必须大于零且小于实体轴向厚度；不能静默改为通孔。")
    radius = diameter / 2.0
    tools, records = [], []
    entry_point = params.get("entry_point")
    direction = params.get("direction")
    if (entry_point is None) != (direction is None):
        raise ValueError("显式盲孔入口必须同时提供 entry_point 与 direction。")
    if entry_point is not None and through:
        raise ValueError("entry_point/direction 只适用于 through=false 的盲孔。")
    if entry_point is not None and params.get("pos", "center") != "point":
        raise ValueError("显式盲孔入口要求 pos=point。")
    if direction is not None and str(direction).lower() not in {"positive", "negative"}:
        raise ValueError("盲孔 direction 必须为 positive 或 negative。")
    for index, (a, b) in enumerate(_hole_positions(params, bbox, axis), 1):
        if through:
            start, length = low - 10.0, high - low + 20.0
            center = _place(a, b, axis, start)
            tools.append(make_cylinder(radius, length, center, axis, base=True))
            records.append({
                "feature_id": feature_id or feature.feature_id or feature.name,
                "instance_index": index, "axis": axis,
                "centerline_mm": list(_place(a, b, axis, (low + high) / 2.0)),
                "diameter_mm": diameter, "depth_mm": None,
                "through": True, "planned_status": "pending",
            })
            continue
        if entry_point is None:
            start, length = high - depth, depth + 1.0
            center = _place(a, b, axis, start)
            tools.append(make_cylinder(radius, length, center, axis, base=True))
            records.append({
                "feature_id": feature_id or feature.feature_id or feature.name,
                "instance_index": index, "axis": axis,
                "centerline_mm": list(_place(a, b, axis, (low + high) / 2.0)),
                "diameter_mm": diameter, "depth_mm": depth,
                "through": False, "planned_status": "pending",
            })
            continue
        if host_shape is None:
            raise ValueError("显式盲孔入口需要切前 BREP，才能核对入口面与局部材料深度。")
        if (not isinstance(entry_point, (list, tuple)) or len(entry_point) != 3
                or any(isinstance(value, bool) or not math.isfinite(float(value))
                       for value in entry_point)):
            raise ValueError("entry_point 必须为三个有限数值 [x,y,z]。")
        entry = tuple(float(value) for value in entry_point)
        plane = _plane_indices(axis)
        if (abs(entry[plane[0]] - float(a)) > 1e-5
                or abs(entry[plane[1]] - float(b)) > 1e-5):
            raise ValueError("entry_point 必须与 point 位于同一孔轴线上。")
        margin = max(1e-3, (high - low) * 1e-6)
        line = _line_material_segments(host_shape, entry, axis, low - margin, high + margin)
        entry_coordinate = entry[_axis_index(axis)]
        local_intervals = [list(map(float, row["axis_interval_mm"]))
                           for row in line["material_segments"]]
        local_tolerance = max(1e-5, (high - low) * 1e-7)
        if str(direction).lower() == "positive":
            matches = [interval for interval in local_intervals
                       if abs(interval[0] - entry_coordinate) <= local_tolerance]
            opening_side = "negative"
        else:
            matches = [interval for interval in local_intervals
                       if abs(interval[1] - entry_coordinate) <= local_tolerance]
            opening_side = "positive"
        if len(matches) != 1:
            raise ValueError(
                "entry_point 必须位于 direction 指向的切前局部材料端面；"
                "检测到 %d 个匹配材料区间。" % len(matches))
        host_interval = matches[0]
        local_span = host_interval[1] - host_interval[0]
        depth_tolerance = max(1e-6, local_span * 1e-8)
        if depth >= local_span - depth_tolerance:
            raise ValueError("盲孔深度必须小于入口处局部材料厚度；否则应声明贯穿孔。")
        epsilon = max(1e-5, min(1e-3, depth * 1e-6))
        base_coordinate = (entry_coordinate - epsilon if str(direction).lower() == "positive"
                           else entry_coordinate - depth)
        base_center = _place(a, b, axis, base_coordinate)
        tools.append(make_cylinder(radius, depth + epsilon, base_center, axis, base=True))
        records.append({
            "feature_id": feature_id or feature.feature_id or feature.name,
            "instance_index": index, "axis": axis,
            "centerline_mm": list(entry),
            "diameter_mm": diameter, "depth_mm": depth,
            "through": False, "opening_side": opening_side,
            "host_axis_interval_mm": host_interval,
            "entry_point_mm": list(entry), "direction": str(direction).lower(),
            "planned_status": "pending",
        })
    return tools, records


def _pocket_tool(feature, bbox):
    p = feature.params
    axis = (p.get("axis") or "Z").upper()
    plane = _plane_indices(axis)
    low, high = _bounds_for_axis(bbox, axis)
    center_a = float(p.get("x", (bbox[plane[0]] + bbox[plane[0] + 3]) / 2.0))
    center_b = float(p.get("y", (bbox[plane[1]] + bbox[plane[1] + 3]) / 2.0))
    depth = float(p.get("depth", 5.0))
    if depth <= 0:
        raise ValueError("凹槽深度必须大于零。")
    depth_tolerance = max(1e-6, (high - low) * 1e-8)
    if depth >= (high - low) - depth_tolerance:
        raise ValueError("盲槽深度必须小于实体轴向厚度；贯穿切除请使用通孔特征。")
    side = str(p.get("side", "positive")).lower()
    if side not in {"positive", "negative"}:
        raise ValueError("pocket.side 必须为 positive 或 negative。")
    # Keep the theoretical floor exact. Boolean tolerance extension is only
    # outside the target body and must never deepen the requested recess.
    epsilon = max(1e-5, min(1e-3, depth * 1e-6))
    profile = K.profile_rect(float(p.get("length", 20.0)),
                             float(p.get("width", 20.0)),
                             float(p.get("corner", 0.0) or 0.0),
                             p.get("corner_mode", "fillet"))
    if side == "positive":
        origin_along, cutter_height = high - depth, depth + epsilon
    else:
        origin_along, cutter_height = low - epsilon, depth + epsilon
    origin = _place(center_a, center_b, axis, origin_along)
    return make_prism(profile, cutter_height, origin, axis)


def _measure_pocket_depth(shape, feature, before_bbox):
    """Measure a pocket floor from the requested exterior face via exact BREP spans."""
    p = feature.params
    axis = (p.get("axis") or "Z").upper()
    low, high = _bounds_for_axis(before_bbox, axis)
    requested = float(p.get("depth", 5.0))
    side = str(p.get("side", "positive")).lower()
    center_a, center_b = _hole_positions(
        {"pos": "center", "x": p.get("x"), "y": p.get("y")},
        before_bbox, axis)[0]
    if axis == "Y":
        half_a, half_b = float(p.get("width", 20.0)) / 2.0, float(p.get("length", 20.0)) / 2.0
    else:
        half_a, half_b = float(p.get("length", 20.0)) / 2.0, float(p.get("width", 20.0)) / 2.0
    # Search interior points to avoid coincident hole axes or other cut features.
    fractions = (0.0, -0.35, 0.35, -0.68, 0.68)
    probe_points = []
    for fa in fractions:
        for fb in fractions:
            if abs(fa) >= 0.68 or abs(fb) >= 0.68:
                continue
            probe_points.append(_place(center_a + fa * half_a,
                                       center_b + fb * half_b, axis,
                                       (low + high) / 2.0))
    margin = max(1e-3, (high - low) * 1e-6)
    measurements = []
    for point in probe_points:
        line = _line_material_segments(shape, point, axis,
                                       low - margin, high + margin)
        spans = line["material_segments"]
        if not spans:
            continue
        if side == "positive":
            floor = max(segment["axis_interval_mm"][1] for segment in spans)
            measured = high - floor
        else:
            floor = min(segment["axis_interval_mm"][0] for segment in spans)
            measured = floor - low
        if measured >= -1e-5:
            measurements.append({"probe_point_mm": list(point),
                                 "measured_depth_mm": max(0.0, measured),
                                 "floor_coordinate_mm": floor,
                                 "material_axis_segments": spans,
                                 "line_intersections": line.get("intersection_count"),
                                 "line_method": line.get("method")})
    tolerance = max(1e-3, requested * 1e-5)
    if not measurements:
        return {"state": "not_measurable", "requested_depth_mm": requested,
                "measured_depth_mm": None, "tolerance_mm": tolerance,
                "reason": "凹槽内未找到可测量的实体底面轴线；无法据此宣称深度正确。"}
    # The maximum valid probe depth measures the deepest actual floor and is
    # stable when some candidates coincide with hole bores.
    selected = max(measurements, key=lambda row: row["measured_depth_mm"])
    measured = selected["measured_depth_mm"]
    error = measured - requested
    return {"state": "measured" if abs(error) <= tolerance else "mismatch",
            "requested_depth_mm": requested, "measured_depth_mm": measured,
            "signed_error_mm": error, "tolerance_mm": tolerance,
            "side": side, "axis": axis,
            "source_face_coordinate_mm": high if side == "positive" else low,
            "floor_coordinate_mm": selected["floor_coordinate_mm"],
            "selected_probe_point_mm": selected["probe_point_mm"],
            "candidate_probe_count": len(measurements),
            "candidate_measurements": measurements,
            "method": "IntCurvesFace_ShapeIntersector face event intervals + BRepClass3d solid interval classification"}


def _classify(shape, point, tolerance=1e-6):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    classifier = BRepClass3d_SolidClassifier(shape, gp_Pnt(*map(float, point)), tolerance)
    return classifier.State() == TopAbs_IN


def _line_material_segments(shape, center, axis, start, end):
    """Intersect the complete planned axis with the BREP and classify each span."""
    axis = (axis or "Z").upper()
    along = _axis_index(axis)
    origin = list(map(float, center))
    origin[along] = 0.0  # make IntCurvesFace W parameters equal global axis coordinates
    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, 1e-7)
    intersector.Perform(gp_Lin(gp_Pnt(*origin), _dir(axis)), float(start), float(end))
    if not intersector.IsDone():
        raise RuntimeError("OCCT 轴线与面求交未完成。")
    breaks = [float(start), float(end)]
    for index in range(1, intersector.NbPnt() + 1):
        value = float(intersector.WParameter(index))
        if start < value < end:
            breaks.append(value)
    breaks.sort()
    unique = []
    for value in breaks:
        if not unique or abs(value - unique[-1]) > 1e-8:
            unique.append(value)
    segments = []
    for lo, hi in zip(unique, unique[1:]):
        if hi - lo <= 1e-9:
            continue
        point = list(map(float, center))
        point[along] = (lo + hi) / 2.0
        segments.append({"axis_interval_mm": [lo, hi],
                         "midpoint_mm": (lo + hi) / 2.0,
                         "inside_solid": _classify(shape, point)})
    return {"method": "IntCurvesFace_ShapeIntersector exact line/trimmed-face events + BRepClass3d interval classification",
            "intersection_count": intersector.NbPnt(), "segments": segments,
            "material_segments": [row for row in segments if row["inside_solid"]]}


def _cylindrical_faces_on_axis(shape, center, axis, diameter,
                               tolerance=1e-4):
    """Measure analytic cylindrical faces coaxial with one planned hole."""
    axis = (axis or "Z").upper()
    along = _axis_index(axis)
    expected = [0.0, 0.0, 0.0]
    expected[along] = 1.0
    center = tuple(float(v) for v in center)
    candidates, exact = [], []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        surface = BRepAdaptor_Surface(face, True)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            ax = cylinder.Axis()
            location, direction = ax.Location(), ax.Direction()
            d = (float(direction.X()), float(direction.Y()), float(direction.Z()))
            parallel = abs(sum(d[i] * expected[i] for i in range(3)))
            origin = (float(location.X()), float(location.Y()), float(location.Z()))
            delta = tuple(center[i] - origin[i] for i in range(3))
            projection = sum(delta[i] * d[i] for i in range(3))
            line_distance = math.sqrt(sum((delta[i] - projection * d[i]) ** 2
                                          for i in range(3)))
            if parallel >= 1.0 - 1e-8 and line_distance <= tolerance:
                radius = float(cylinder.Radius())
                v0, v1 = float(surface.FirstVParameter()), float(surface.LastVParameter())
                c0 = origin[along] + d[along] * v0
                c1 = origin[along] + d[along] * v1
                row = {"radius_mm": radius, "diameter_mm": radius * 2.0,
                       "axis_line_distance_mm": line_distance,
                       "axis_parallel_dot": parallel,
                       "axis_interval_mm": [min(c0, c1), max(c0, c1)],
                       "surface_type": "analytic_cylinder"}
                candidates.append(row)
                if (math.isfinite(radius) and math.isfinite(c0) and math.isfinite(c1)
                        and abs(radius * 2.0 - float(diameter)) <= tolerance):
                    exact.append(row)
        explorer.Next()
    return candidates, exact


def _pure_cylindrical_tool_intent(tool, feature_id, host_bbox, host_shape=None,
                                 prior_holes=None):
    """Infer a bore intent from actual cutter topology, independent of kind."""
    if tool is None or tool.IsNull() or not host_bbox:
        return None
    cylinder_faces, planar_faces, other_faces = [], 0, 0
    explorer = TopExp_Explorer(tool, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        surface = BRepAdaptor_Surface(face, True)
        surface_type = surface.GetType()
        if surface_type == GeomAbs_Cylinder:
            cyl = surface.Cylinder()
            axis = cyl.Axis()
            direction = (float(axis.Direction().X()), float(axis.Direction().Y()),
                         float(axis.Direction().Z()))
            cylinder_faces.append((float(cyl.Radius()), axis.Location(), direction))
        elif surface_type == GeomAbs_Plane:
            planar_faces += 1
        else:
            other_faces += 1
        explorer.Next()
    if not cylinder_faces:
        return None
    if len(cylinder_faces) != 1 or planar_faces != 2 or other_faces:
        return {"feature_id": feature_id, "classification": "unverified_cylindrical_cut",
                "status": "unverified",
                "reason": "切刀包含圆柱面但不是可解析的单圆柱实体；孔实例与实际切除语义未核验。",
                "cylindrical_face_count": len(cylinder_faces)}
    radius, location, direction = cylinder_faces[0]
    axis_index = max(range(3), key=lambda i: abs(direction[i]))
    if abs(abs(direction[axis_index]) - 1.0) > 1e-8:
        return {"feature_id": feature_id, "classification": "unverified_cylindrical_cut",
                "status": "unverified", "reason": "圆柱刀具轴线不平行于全局 XYZ，尚无解析实例检查。",
                "diameter_mm": 2.0 * radius}
    axis = "XYZ"[axis_index]
    tool_bbox = _bbox(tool)
    low, high = host_bbox[axis_index], host_bbox[axis_index + 3]
    tool_low, tool_high = tool_bbox[axis_index], tool_bbox[axis_index + 3]
    span = high - low
    tol = max(1e-5, span * 1e-7)
    axis_point = [float(location.X()), float(location.Y()), float(location.Z())]
    centerline = list(axis_point)
    centerline[axis_index] = (low + high) / 2.0
    # The whole part's bounding box may be extended by unrelated features (for
    # example a boss at another XY location). Infer the cutter opening against
    # actual material intervals on its own axis, before applying the cut.
    local_intervals = []
    if host_shape is not None:
        margin = max(1e-3, span * 1e-6)
        try:
            line = _line_material_segments(host_shape, axis_point, axis,
                                           low - margin, high + margin)
            local_intervals = [list(map(float, row["axis_interval_mm"]))
                               for row in line.get("material_segments", [])]
        except Exception as exc:
            return {"feature_id": feature_id,
                    "classification": "unverified_cylindrical_cut",
                    "status": "unverified", "axis": axis,
                    "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                    "tool_bbox_mm": tool_bbox,
                    "local_interval_error": str(exc),
                    "reason": "无法完成切前实体的孔轴线局部材料求交；不使用全局包围盒猜测孔开口或深度。"}
        if not local_intervals:
            candidate = {"axis": axis, "centerline_mm": centerline,
                         "diameter_mm": 2.0 * radius, "through": True}
            for previous in prior_holes or []:
                prior_intervals = previous.get("host_axis_intervals_mm") or []
                if (not prior_intervals or not _same_through_hole_axis(previous, candidate)
                        or previous.get("superseded_by")):
                    continue
                interval_tolerance = max(1e-5, span * 1e-7)
                if not all(tool_low <= float(interval[0]) + interval_tolerance
                           and tool_high >= float(interval[1]) - interval_tolerance
                           for interval in prior_intervals):
                    continue
                # An earlier through cut may remove every material interval on
                # this centerline. Reuse that provenance only if the current
                # pre-cut BREP still measures it as open and supported.
                measured_prior = _check_hole_instances(host_shape, [previous])[0]
                if measured_prior.get("status") == "verified_open":
                    return {"feature_id": feature_id, "instance_index": 1,
                            "axis": axis, "centerline_mm": centerline,
                            "diameter_mm": 2.0 * radius, "depth_mm": None,
                            "through": True, "planned_status": "pending",
                            "source_geometry": "OCCT cylindrical cutter topology + remeasured prior through-hole axis",
                            "classification": "verified_intent_candidate",
                            "tool_bbox_mm": tool_bbox,
                            "host_axis_intervals_mm": prior_intervals}
            return {"feature_id": feature_id,
                    "classification": "unverified_cylindrical_cut",
                    "status": "unverified", "axis": axis,
                    "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                    "tool_bbox_mm": tool_bbox,
                    "host_axis_intervals_mm": [],
                    "reason": "切前实体在刀具轴线上没有可确认的局部材料区间；不使用全局包围盒猜测孔开口或深度。"}
    # A cutter can span the whole local material path even when an unrelated
    # boss extends the model bbox. Use all intervals on this axis, not the
    # global bbox, to recognize that through cut.
    local_through = bool(local_intervals) and all(
        tool_low <= interval_low + max(1e-5, (interval_high - interval_low) * 1e-7)
        and tool_high >= interval_high - max(1e-5, (interval_high - interval_low) * 1e-7)
        for interval_low, interval_high in local_intervals)
    if (tool_low <= low + tol and tool_high >= high - tol) or local_through:
        return {"feature_id": feature_id, "instance_index": 1, "axis": axis,
                "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                "depth_mm": None, "through": True, "planned_status": "pending",
                "source_geometry": ("OCCT cylindrical cutter topology + local host material intervals"
                                    if local_through else "OCCT cylindrical cutter topology"),
                "classification": "verified_intent_candidate",
                "tool_bbox_mm": tool_bbox,
                "host_axis_intervals_mm": local_intervals}
    overlapping_intervals = []
    for interval_low, interval_high in local_intervals:
        local_tol = max(1e-5, (interval_high - interval_low) * 1e-7)
        overlap = min(tool_high, interval_high) - max(tool_low, interval_low)
        if overlap > local_tol:
            overlapping_intervals.append([interval_low, interval_high])
    if len(overlapping_intervals) > 1:
        return {"feature_id": feature_id,
                "classification": "unverified_cylindrical_cut",
                "status": "unverified", "axis": axis,
                "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                "tool_bbox_mm": tool_bbox,
                "host_axis_intervals_mm": local_intervals,
                "cutter_overlapped_material_intervals_mm": overlapping_intervals,
                "reason": "盲孔刀具与轴线上的多个分离实体区间相交；无法将其作为单一盲孔深度核验。"}
    for interval_low, interval_high in local_intervals:
        local_tol = max(1e-5, (interval_high - interval_low) * 1e-7)
        if (tool_high >= interval_high - local_tol
                and interval_low + local_tol < tool_low < interval_high - local_tol):
            depth = interval_high - tool_low
            centerline[axis_index] = (interval_low + interval_high) / 2.0
            return {"feature_id": feature_id, "instance_index": 1, "axis": axis,
                    "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                    "depth_mm": depth, "through": False, "opening_side": "positive",
                    "host_axis_interval_mm": [interval_low, interval_high],
                    "planned_status": "pending",
                    "source_geometry": "OCCT cylindrical cutter topology + local host material interval",
                    "classification": "verified_intent_candidate",
                    "tool_bbox_mm": tool_bbox}
        if (tool_low <= interval_low + local_tol
                and interval_low + local_tol < tool_high < interval_high - local_tol):
            depth = tool_high - interval_low
            centerline[axis_index] = (interval_low + interval_high) / 2.0
            return {"feature_id": feature_id, "instance_index": 1, "axis": axis,
                    "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
                    "depth_mm": depth, "through": False, "opening_side": "negative",
                    "host_axis_interval_mm": [interval_low, interval_high],
                    "planned_status": "pending",
                    "source_geometry": "OCCT cylindrical cutter topology + local host material interval",
                    "classification": "verified_intent_candidate",
                    "tool_bbox_mm": tool_bbox}
    return {"feature_id": feature_id, "classification": "unverified_cylindrical_cut",
            "status": "unverified", "axis": axis,
            "centerline_mm": centerline, "diameter_mm": 2.0 * radius,
            "tool_bbox_mm": tool_bbox,
            "reason": "圆柱刀具未贯穿目标轴向包围盒；盲深或局部开口需进一步定向测量。"}


def _outer_wall_supported(shape, center, axis, radius, intervals):
    """Require material around the declared bore on an actual wall section."""
    axis = (axis or "Z").upper()
    along = _axis_index(axis)
    plane = _plane_indices(axis)
    stations = sorted({(row["axis_interval_mm"][0] + row["axis_interval_mm"][1]) / 2.0
                       for row in intervals
                       if row["axis_interval_mm"][1] - row["axis_interval_mm"][0] > 1e-7})
    full_sections = []
    ring_supported = []
    for station in stations:
        values = []
        for index in range(24):
            angle = 2.0 * math.pi * index / 24.0
            point = list(map(float, center))
            point[along] = station
            point[plane[0]] += radius * 1.25 * math.cos(angle)
            point[plane[1]] += radius * 1.25 * math.sin(angle)
            values.append(_classify(shape, point))
        is_supported = all(values)
        ring_supported.append(is_supported)
        if is_supported:
            full_sections.append(station)
    return {"supported": bool(full_sections), "ring_supported": ring_supported,
            "sampled_axis_stations_mm": stations,
            "supported_axis_stations_mm": full_sections,
            "sample_count_per_section": 24,
            "method": "BRepClass3d 24-point ring at 1.25 x measured radius, on measured cylinder-wall spans"}


def _check_hole_instances(shape, records):
    bbox = _bbox(shape)
    if not bbox:
        return [dict(row, status="not_measured", reason="模型包围盒无效。")
                for row in records]
    checked = []
    for row in records:
        if row.get("superseded_by"):
            checked.append(dict(
                row, status="superseded",
                reason="后续已提交的贯穿孔完全覆盖该实例；该实例不再单独作为最终孔壁验收目标。"))
            continue
        axis = row["axis"]
        along = _axis_index(axis)
        center = list(map(float, row["centerline_mm"]))
        low, high = bbox[along], bbox[along + 3]
        span = high - low
        margin = max(1e-3, span * 1e-6)
        radius = float(row["diameter_mm"]) / 2.0
        candidates, exact_faces = _cylindrical_faces_on_axis(
            shape, center, axis, row["diameter_mm"])
        through = bool(row["through"])
        if row.get("preserved_wall_interval_mm"):
            expected = [float(value) for value in row["preserved_wall_interval_mm"]]
            span_tolerance = max(1e-5, span * 1e-7)
            matching_segment = next((face for face in exact_faces
                                     if abs(face["axis_interval_mm"][0] - expected[0]) <= span_tolerance
                                     and abs(face["axis_interval_mm"][1] - expected[1]) <= span_tolerance), None)
            support = _outer_wall_supported(
                shape, center, axis, radius, [matching_segment] if matching_segment else [])
            segment_length = max(0.0, expected[1] - expected[0])
            status = ("verified_blind_segment"
                      if matching_segment and support["supported"]
                      else "partially_superseded_unverified")
            checked.append(dict(
                row, status=status,
                reason=("后续沉孔覆盖上段；保留的原孔径圆柱壁段长度及环形支撑已测量。"
                        if status == "verified_blind_segment"
                        else "原孔只部分被后续特征覆盖；剩余圆柱壁段未能完整测量。"),
                measured_diameter_mm=(matching_segment["diameter_mm"] if matching_segment else None),
                measured_depth_mm=segment_length if matching_segment else None,
                preserved_wall_measurement={
                    "requested_interval_mm": expected,
                    "measured_interval_mm": matching_segment.get("axis_interval_mm") if matching_segment else None,
                    "wall_support": support,
                }))
            continue
        local_host_interval = row.get("host_axis_interval_mm")
        local_low, local_high = ((float(local_host_interval[0]), float(local_host_interval[1]))
                                 if local_host_interval and len(local_host_interval) == 2
                                 else (low, high))
        local_span = local_high - local_low
        if through:
            line = _line_material_segments(shape, center, axis,
                                           low - margin, high + margin)
            centerline_clear = not line["material_segments"]
            support = _outer_wall_supported(
                shape, center, axis, radius, exact_faces)
            if not centerline_clear:
                status, reason = "blocked", "计划孔轴线的完整包围盒跨度内仍有 BREP 实体区间。"
            elif not exact_faces:
                status, reason = ("diameter_mismatch" if candidates else "no_cylindrical_wall",
                                  "轴线贯通，但未找到与计划轴线同轴且直径相符的解析圆柱面。")
            elif not support["supported"]:
                status, reason = "opening_clipped", "轴线为空且有匹配圆柱面，但孔周环形材料不完整，可能切到零件边缘。"
            else:
                status, reason = "verified_open", "全包围盒轴跨度无实体区间；同轴解析圆柱面实测直径匹配，且孔壁截面有完整环形材料。"
            line_proof = line
            depth_measured = None
        else:
            depth = float(row["depth_mm"])
            opening_side = row.get("opening_side", "positive")
            tip = local_high - depth if opening_side == "positive" else local_low + depth
            eps = max(1e-5, min(1e-3, local_span * 1e-7))
            if opening_side == "positive":
                line = _line_material_segments(shape, center, axis,
                                               tip + eps, local_high + margin)
                tip_probe_along = tip - eps
            else:
                line = _line_material_segments(shape, center, axis,
                                               local_low - margin, tip - eps)
                tip_probe_along = tip + eps
            path_clear = not line["material_segments"]
            tip_point = list(center)
            tip_point[along] = tip_probe_along
            tip_supported = _classify(shape, tip_point)
            support = _outer_wall_supported(
                shape, center, axis, radius, exact_faces)
            shoulder_supported = False
            if not tip_supported:
                narrower_faces = [face for face in candidates
                                  if face["diameter_mm"] < float(row["diameter_mm"]) - 1e-5
                                  and face["axis_interval_mm"][0] < tip_probe_along + eps
                                  and face["axis_interval_mm"][1] >= tip_probe_along - 2 * eps]
                plane = _plane_indices(axis)
                for narrower in narrower_faces:
                    sample_radius = (float(narrower["diameter_mm"]) / 2.0 + radius) / 2.0
                    probes = []
                    for index in range(24):
                        angle = 2.0 * math.pi * index / 24.0
                        point = list(center)
                        point[along] = tip - eps
                        point[plane[0]] += sample_radius * math.cos(angle)
                        point[plane[1]] += sample_radius * math.sin(angle)
                        probes.append(_classify(shape, point))
                    if all(probes):
                        shoulder_supported = True
                        break
            intervals = [item["axis_interval_mm"] for item in exact_faces]
            wall_open_high = local_high if opening_side == "positive" else tip
            wall_open_low = tip if opening_side == "positive" else local_low
            depth_measured = max((min(end, wall_open_high) - max(start, wall_open_low)
                                  for start, end in intervals
                                  if min(end, wall_open_high) > max(start, wall_open_low)), default=0.0)
            if not path_clear:
                status, reason = "blocked", "盲孔声明路径内仍有实体，实际深度短于计划深度。"
            elif not tip_supported and not shoulder_supported:
                status, reason = "depth_mismatch", "计划盲孔底部之外仍未检测到实体，可能已切穿或实际更深。"
            elif not exact_faces:
                status, reason = ("diameter_mismatch" if candidates else "no_cylindrical_wall",
                                  "未找到与计划轴线及孔径相符的解析圆柱壁面。")
            elif not support["supported"]:
                status, reason = "opening_clipped", "盲孔轴线/圆柱面存在，但孔壁截面缺少完整环形材料。"
            elif shoulder_supported:
                status, reason = "verified_counterbore", "盲孔在阶梯肩部终止；肩部外环有材料，且下方存在更小同轴孔。"
            else:
                status, reason = "verified_blind", "计划深度内轴线无实体，孔底后仍有实体，且解析圆柱壁面的孔径与环形支撑均匹配。"
            line_proof = line
        checked.append(dict(
            row, status=status, reason=reason,
            axis_surface_crossings=line_proof.get("intersection_count"),
            centerline_material_segments=line_proof.get("material_segments"),
            ring_supported=support.get("ring_supported", []),
            measured_diameter_mm=(exact_faces[0]["diameter_mm"] if exact_faces else None),
            diameter_candidates_mm=sorted({round(item["diameter_mm"], 8)
                                            for item in candidates}),
            measured_depth_mm=depth_measured,
            matching_cylindrical_faces=exact_faces,
            centerline_proof=line_proof,
            wall_support=support,
            method="OCCT trimmed-face line intersections + BRepClass3d interval/ring classification + analytic cylindrical-face radius/axis measurement"))
    return checked


def _transform_shape(shape, vector=None, axis=None, angle_deg=0.0, center=(0, 0, 0)):
    transform = gp_Trsf()
    if axis:
        transform.SetRotation(gp_Ax1(gp_Pnt(*map(float, center)), _dir(axis)),
                              math.radians(float(angle_deg)))
        if vector and any(abs(float(v)) > 1e-12 for v in vector):
            translate = gp_Trsf()
            translate.SetTranslation(gp_Vec(*map(float, vector)))
            transform = translate.Multiplied(transform)
    elif vector:
        transform.SetTranslation(gp_Vec(*map(float, vector)))
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def _loft_shape(feature):
    sections = feature._loft_sections()
    if len(sections) < 2:
        raise ValueError("放样至少需要两个封闭截面。")
    rotate = feature.params.get("rotate")
    translate = tuple(feature.params.get("translate", (0.0, 0.0, 0.0)))
    if rotate:
        from ai_modeler.core.model.docmodel import mat_rot, mat_apply
        matrix = mat_rot(rotate[0], float(rotate[1]))
        sections = [[tuple(mat_apply(matrix, point)[i] + translate[i]
                           for i in range(3)) for point in section]
                    for section in sections]
    elif any(abs(float(v)) > 1e-12 for v in translate):
        sections = [[tuple(float(point[i]) + float(translate[i]) for i in range(3))
                     for point in section] for section in sections]
    builder = BRepOffsetAPI_ThruSections(True, False, 1e-5)
    for section in sections:
        builder.AddWire(_wire_from_xyz(section))
    builder.Build()
    if not builder.IsDone():
        raise RuntimeError("OCCT 放样失败，截面顺序或轮廓不兼容。")
    return builder.Shape()


def _feature_geometry(doc, feature, bbox, max_mesh_faces, host_shape=None):
    p, kind = feature.params, feature.kind
    center = tuple(float(v) for v in p.get("center", (0.0, 0.0, 0.0)))
    axis = (p.get("axis") or "Z").upper()
    if kind == "box":
        shape = make_box(float(p.get("length", 10)), float(p.get("width", 10)),
                         float(p.get("height", 10)), center,
                         bool(p.get("base", True)), axis,
                         float(p.get("corner", 0.0) or 0.0),
                         p.get("corner_mode", "fillet"))
        return [shape], [], "approximate" if float(p.get("corner", 0.0) or 0.0) else "exact", "analytic_prism"
    if kind == "cylinder":
        return [make_cylinder(float(p.get("radius", 5.0)), float(p.get("height", 10.0)),
                              center, axis, bool(p.get("base", True)), p.get("radius2"))], [], "exact", "analytic_cylinder_or_cone"
    if kind == "sphere":
        return [BRepPrimAPI_MakeSphere(gp_Pnt(*center), float(p.get("radius", 5.0))).Shape()], [], "exact", "analytic_sphere"
    if kind == "prism":
        return [make_prism(p.get("profile") or [], float(p.get("height", 10.0)),
                           center, axis)], [], "exact", "analytic_extrusion_of_polygon"
    if kind == "revolve":
        return [make_revolve(p.get("profile") or [], float(p.get("angle", 360.0)),
                             center, axis)], [], "exact", "analytic_occt_revolve"
    if kind == "socket_head_bolt":
        # Preserve the legacy ISO-like threaded shank, but build the head and
        # socket with analytic OCCT features.  The thread remains a faceted
        # approximation; fusing it into the head prevents the touching pieces
        # from being reported as separate solids.
        d = float(p.get("shank_d", 8.0))
        pitch = float(p.get("thread_pitch", 0.0) or 0.0)
        length = float(p.get("length", 25.0))
        head_d = float(p.get("head_d", 13.0))
        head_h = float(p.get("head_h", 8.0))
        hex_s = float(p.get("hex_s", 6.0))
        hex_t = float(p.get("hex_t", 4.0))
        if min(d, length, head_d, head_h, hex_s, hex_t) <= 0 or hex_t > head_h:
            raise ValueError("内六角螺钉尺寸必须为正，且 hex_t 不得大于 head_h。")
        cx, cy, cz = center
        head = make_cylinder(head_d / 2.0, head_h, (cx, cy, cz), "Z", True)
        hex_r = hex_s / math.sqrt(3.0)
        socket_profile = [(hex_r * math.cos(math.radians(60 * i + 30)),
                           hex_r * math.sin(math.radians(60 * i + 30)))
                          for i in range(6)]
        socket = make_prism(socket_profile, hex_t + 0.02,
                            (cx, cy, cz + head_h - hex_t - 0.01), "Z")
        head = cut_many(head, [socket])
        shank_polys = K.make_threaded_cylinder(
            d, pitch, length + 0.02, (cx, cy, cz + head_h - 0.01), 0,
            max(24, int(p.get("segments", 48)))) if pitch > 0 else K.make_cylinder(
                d / 2.0, length + 0.02, (cx, cy, cz + head_h - 0.01), 0,
                max(24, int(p.get("segments", 48))))
        shank, _ = _polygon_mesh_to_brep(shank_polys, max_faces=max_mesh_faces)
        result = fuse_many([head, shank])
        _assert_valid_solid(result, "内六角螺钉")
        return [result], [], "approximate", "analytic_socket_head_with_faceted_thread_brep"
    if kind == "spoked_wheel":
        outer_r = float(p.get("outer_r", 60.0))
        rim_w = float(p.get("rim_w", 6.0))
        hub_r = float(p.get("hub_r", 15.0))
        height = float(p.get("height", 8.0))
        count = int(p.get("spokes", 5))
        spoke_w = float(p.get("spoke_w") or max(4.0, (outer_r - hub_r) * 0.22))
        inner_r = outer_r - rim_w
        hub_bore_r = max(1.0, hub_r * 0.35)
        cx, cy, cz = center
        if min(outer_r, rim_w, hub_r, height, spoke_w) <= 0 or count < 2:
            raise ValueError("辐条轮尺寸必须为正且至少有两根辐条。")
        if inner_r <= hub_r or spoke_w >= 2.0 * hub_r:
            raise ValueError("辐条轮轮缘内径必须大于轮毂半径，辐条宽度必须小于轮毂直径。")
        z0, z1 = cz - height / 2.0, cz + height / 2.0
        ring = make_revolve([(inner_r, -height / 2.0),
                             (outer_r, -height / 2.0),
                             (outer_r, height / 2.0),
                             (inner_r, height / 2.0)], 360.0, center, "Z")
        hub = make_revolve([(hub_bore_r, -height / 2.0),
                            (hub_r, -height / 2.0),
                            (hub_r, height / 2.0),
                            (hub_bore_r, height / 2.0)], 360.0, center, "Z")
        radial_start = hub_r - max(0.05, min(0.25, rim_w * 0.05))
        radial_length = outer_r - radial_start
        mid = radial_start + radial_length / 2.0
        bars = []
        for index in range(count):
            bar = make_box(radial_length, spoke_w, height,
                           (cx + mid, cy, z0), True, "Z")
            bars.append(_transform_shape(
                bar, axis="Z", angle_deg=360.0 * index / count,
                center=center))
        result = fuse_many([ring, hub] + bars)
        _assert_valid_solid(result, "辐条轮")
        return [result], [], "exact", "analytic_ring_hub_with_box_spokes"
    if kind == "piston":
        diameter = float(p.get("diameter", 40.0))
        height = float(p.get("height", 35.0))
        pin_d = float(p.get("pin_d", 0.0) or 0.0)
        grooves = max(0, int(p.get("grooves", 3)))
        radius = diameter / 2.0
        if min(diameter, height) <= 0 or pin_d < 0:
            raise ValueError("活塞直径与高度必须为正，销孔直径不能为负。")
        inner_r = max(1.0, radius * 0.55)
        groove_depth = max(0.4, radius * 0.06)
        groove_width = max(0.6, height * 0.035)
        z_top, z_bottom = height / 2.0, -height / 2.0
        profile = [(inner_r, z_bottom), (radius, z_bottom)]
        if grooves:
            band = height * 0.34
            step = band / float(grooves)
            for index in reversed(range(grooves)):
                zc = z_top - step * (index + 0.5)
                low = zc - groove_width
                if zc >= z_top or low <= z_bottom + groove_width * 0.5:
                    continue
                profile.extend([(radius, low), (radius - groove_depth, zc - groove_width * 0.85),
                                (radius - groove_depth, zc - groove_width * 0.15),
                                (radius, zc)])
        profile.extend([(radius, z_top), (inner_r, z_top)])
        result = make_revolve(profile, 360.0, center, "Z")
        if pin_d > 0:
            pin_radius = min(pin_d / 2.0, radius * 0.75, height * 0.45)
            cutter = make_cylinder(pin_radius, radius * 3.0, center, "X", False)
            result = cut_many(result, [cutter])
        _assert_valid_solid(result, "活塞")
        return [result], [], "exact", "analytic_grooved_revolve_with_pin_bore"
    if kind == "crankshaft":
        cx, cy, cz = center
        main_d = float(p.get("main_d", 20.0))
        pin_d = float(p.get("pin_d", 16.0))
        stroke = float(p.get("stroke", 30.0))
        throws = max(1, int(p.get("throws", 2)))
        length = float(p.get("length") or max(stroke * 1.8, main_d * 2.2) * throws)
        if min(main_d, pin_d, stroke, length) <= 0:
            raise ValueError("曲轴主轴颈、连杆颈、行程和总长必须为正。")
        main_r, pin_r = main_d / 2.0, pin_d / 2.0
        spacing = length / throws
        pin_length = spacing * 0.42
        web_w = min(max(2.0, main_d * 0.28), spacing * 0.25)
        shaft = make_cylinder(main_r, length, center, "X", False)
        parts = [shaft]
        for index in range(throws):
            x0 = cx - length / 2.0 + spacing * (index + 0.5)
            dy = stroke / 2.0 if index % 2 == 0 else -stroke / 2.0
            pin = make_cylinder(pin_r, pin_length, (x0, cy + dy, cz), "X", False)
            parts.append(pin)
            y0, y1 = sorted((cy - main_r, cy + dy - pin_r))
            y2, y3 = sorted((cy + main_r, cy + dy + pin_r))
            ylo, yhi = min(y0, y2), max(y1, y3)
            zlo, zhi = cz - max(main_r, pin_r), cz + max(main_r, pin_r)
            for xmid in (x0 - pin_length / 2.0 - web_w / 2.0,
                         x0 + pin_length / 2.0 + web_w / 2.0):
                parts.append(make_box(web_w, yhi - ylo, zhi - zlo,
                                      (xmid, (ylo + yhi) / 2.0, zlo), True, "Z"))
        result = fuse_many(parts)
        _assert_valid_solid(result, "曲轴")
        return [result], [], "approximate", "analytic_journals_with_rectangular_webs"
    if kind == "conrod":
        cx, cy, cz = center
        length = max(2.0, float(p.get("length", 80.0)))
        big_d = max(2.0, float(p.get("big_d", 30.0)))
        small_d = max(1.5, float(p.get("small_d", 18.0)))
        big_bore = min(max(0.5, float(p.get("big_bore", 20.0))), big_d * 0.90)
        small_bore = min(max(0.4, float(p.get("small_bore", 10.0))), small_d * 0.90)
        shank_w = max(2.0, float(p.get("shank_w", min(big_d, small_d) * 0.42)))
        thickness = max(2.0, float(p.get("thick", min(big_d, small_d) * 0.6)))
        r_big, r_small = big_d / 2.0, small_d / 2.0
        bore_big, bore_small = big_bore / 2.0, small_bore / 2.0
        x_big, x_small = cx - length / 2.0, cx + length / 2.0
        rings = [
            make_revolve([(bore_big, -thickness / 2.0), (r_big, -thickness / 2.0),
                          (r_big, thickness / 2.0), (bore_big, thickness / 2.0)],
                         360.0, (x_big, cy, cz), "Z"),
            make_revolve([(bore_small, -thickness / 2.0), (r_small, -thickness / 2.0),
                          (r_small, thickness / 2.0), (bore_small, thickness / 2.0)],
                         360.0, (x_small, cy, cz), "Z"),
        ]
        delta_ratio = (r_big - r_small) / length
        if abs(delta_ratio) >= 1.0:
            raise ValueError("连杆长度不足以连接大小头的外公切杆身。")
        tangent_y = math.sqrt(1.0 - delta_ratio * delta_ratio)
        radial_big = r_big - min(shank_w / 2.0, (r_big - bore_big) * 0.8)
        radial_small = r_small - min(shank_w / 2.0, (r_small - bore_small) * 0.8)
        inset = 0.25
        # Profile coordinates are local to `center`; pre-applying x_big/cy
        # here and passing `center` to make_prism translates the web twice.
        web_profile = [
            (-length / 2.0 + (radial_big - inset) * delta_ratio,
             (radial_big - inset) * tangent_y),
            (length / 2.0 + (radial_small - inset) * delta_ratio,
             (radial_small - inset) * tangent_y),
            (length / 2.0 + (radial_small - inset) * delta_ratio,
             -(radial_small - inset) * tangent_y),
            (-length / 2.0 + (radial_big - inset) * delta_ratio,
             -(radial_big - inset) * tangent_y),
        ]
        web = make_prism(web_profile, thickness, center, "Z", True)
        result = fuse_many(rings + [web])
        _assert_valid_solid(result, "连杆")
        return [result], [], "approximate", "analytic_annular_ends_with_external_tangent_web"
    if kind == "impeller":
        hub_d = float(p.get("hub_d", 20.0))
        tip_d = float(p.get("tip_d", 60.0))
        height = float(p.get("height", 15.0))
        blades = max(3, int(p.get("blades", 6)))
        twist_deg = float(p.get("twist_deg", 50.0))
        blade_t = float(p.get("blade_t", 0.0) or 0.0)
        segments = max(4, int(p.get("segments", 12)))
        rh = max(1.0, hub_d / 2.0)
        rt = max(rh * 1.05, tip_d / 2.0)
        height = max(1.0, height)
        blade_len = rt - rh
        chord = max(1.0, blade_len * 0.62)
        blade_axial = min(height, blade_t) if blade_t > 0 else height
        cx, cy, cz = center
        hub = make_cylinder(rh, height, (cx, cy, cz - height / 2.0),
                            "Z", True, rh * 0.55)
        disc_height = max(0.5, height * 0.28)
        disc = make_revolve([(rh * 0.45, -height / 2.0), (rt, -height / 2.0),
                             (rt, -height / 2.0 + disc_height),
                             (rh * 0.45, -height / 2.0 + disc_height)],
                            360.0, center, "Z")
        section_profile = [(-chord / 2.0, -blade_axial / 2.0),
                           (chord / 2.0, -blade_axial / 2.0),
                           (chord / 2.0, blade_axial / 2.0),
                           (-chord / 2.0, blade_axial / 2.0)]
        raw_sections = []
        for step_index in range(segments + 1):
            t = step_index / float(segments)
            radial = rh + blade_len * 1.35 * t
            angle = math.radians(twist_deg * t)
            ca, sa = math.cos(angle), math.sin(angle)
            section = []
            for tangent, axial in section_profile:
                qy = tangent * ca - axial * sa
                qz = tangent * sa + axial * ca
                section.append((radial, qy, qz))
            raw_sections.append(section)
        radial_max = max(math.hypot(point[0], point[1])
                         for section in raw_sections for point in section) or 1.0
        axial_max = max(abs(point[2]) for section in raw_sections for point in section) or 1.0
        radial_scale = rt / radial_max
        axial_scale = min(1.0, (height / 2.0) / axial_max)
        blade_shapes = []
        for blade_index in range(blades):
            blade_sections = []
            blade_angle = math.radians(360.0 * blade_index / blades)
            cb, sb = math.cos(blade_angle), math.sin(blade_angle)
            for section in raw_sections:
                points = []
                for radial, qy, qz in section:
                    x, y = radial * radial_scale, qy * radial_scale
                    points.append((cx + x * cb - y * sb,
                                   cy + x * sb + y * cb,
                                   cz + qz * axial_scale))
                blade_sections.append(points)
            builder = BRepOffsetAPI_ThruSections(True, False, 1e-5)
            for section in blade_sections:
                builder.AddWire(_wire_from_xyz(section))
            builder.Build()
            if not builder.IsDone():
                raise RuntimeError("叶轮原生叶片放样失败：截面拓扑不兼容。")
            blade_shapes.append(builder.Shape())
        result = fuse_many([hub, disc] + blade_shapes)
        _assert_valid_solid(result, "叶轮")
        return [result], [], "approximate", "analytic_hub_backplate_with_native_twisted_blade_lofts"
    if kind == "scroll_disc":
        outer_r = max(2.0, float(p.get("outer_d", 60.0)) / 2.0)
        height = max(1.0, float(p.get("height", 8.0)))
        wall_t = max(0.5, float(p.get("wall_t", 2.0)))
        turns = max(0.5, float(p.get("turns", 2.5)))
        segments = max(24, int(p.get("segments", 120)))
        r0 = outer_r * 0.20
        pitch = max(0.5, (outer_r - wall_t / 2.0 - r0) / turns)
        profile = K.profile_scroll(r0, pitch, turns, wall_t, segments)
        wall = make_prism(profile, height, center, "Z", True)
        disc_height = max(0.5, height * 0.35)
        cx, cy, cz = center
        disc = make_cylinder(outer_r, disc_height,
                             (cx, cy, cz - height / 2.0), "Z", True)
        result = fuse_many([wall, disc])
        _assert_valid_solid(result, "涡旋盘")
        return [result], [], "approximate", "analytic_prismatic_spiral_and_backplate"
    if kind == "rotor":
        radius = float(p.get("radius", 60.0))
        segments = int(p.get("n", 48))
        height = float(p.get("height", 40.0))
        bore_d = float(p.get("bore_d", 0.0) or 0.0)
        if radius <= 0 or height <= 0 or bore_d < 0 or bore_d >= radius * 1.64:
            raise ValueError("转子半径/高度须为正，中心孔径须小于轮廓最窄尺寸。")
        profile = K.profile_rotor(radius, max(3, segments))
        result = make_prism(profile, height, center, "Z", True)
        if bore_d > 0:
            cutter = make_cylinder(bore_d / 2.0, height + 0.02,
                                   center, "Z", False)
            result = cut_many(result, [cutter])
        _assert_valid_solid(result, "转子")
        return [result], [], "approximate", "native_prismatic_rounded_triangle_with_bore"
    if kind == "vbelt_pulley" and (p.get("groove_type") or p.get("outer_d")):
        # The standard profile is an axial section. Preserve the requested
        # width: reject groove layouts that cannot fit rather than silently
        # changing a sourced dimension.
        spec = K.std_pulley.spec(
            float(p.get("pitch_d", 80.0)), int(p.get("grooves", 3)),
            p.get("groove_type"), float(p.get("height", 40.0)),
            float(p.get("bore_d", 0.0) or 0.0), keep_width=True)
        if spec.get("spokes"):
            raise NotImplementedError("大直径辐条式 V 带轮尚未有原生 BREP 辐条并体实现。")
        min_width = ((spec["grooves"] - 1) *
                     max(spec["pitch_e"], spec["root_w"] + 0.5)
                     + spec["root_w"] + 0.5)
        requested_width = float(p.get("height", 40.0))
        if requested_width + 1e-8 < min_width:
            raise ValueError(
                "输入轮宽 %.6g mm 无法容纳 %d 个标准 V 槽；无自交剖面的最小轮宽为 %.6g mm。"
                % (requested_width, spec["grooves"], min_width))
        spec["pitch_e"] = max(spec["pitch_e"], spec["root_w"] + 0.5)
        pulley = make_revolve(K.std_pulley.profile(spec), 360.0, center, "Z")
        builder = "analytic_standard_vbelt_revolve"
        return [pulley], [], "approximate", builder
    if kind == "gear":
        profile = K.profile_gear(int(p.get("teeth", 20)), float(p.get("module", 2.0)))
        return [make_prism(profile, float(p.get("height", 10.0)), center, axis,
                           symmetric=not bool(p.get("base", True)))], [], "approximate", "polygonal_tooth_profile"
    if kind == "loft":
        return [_loft_shape(feature)], [], "approximate", "occt_loft_of_discrete_airfoil_sections"
    if kind == "hole":
        if bbox is None:
            raise ValueError("没有先行实体，无法根据零件边界放置孔。")
        tools, holes = _hole_tools(feature, bbox, feature.feature_id, host_shape)
        return tools, holes, "exact", "analytic_cylindrical_bores"
    if kind == "pocket":
        if bbox is None:
            raise ValueError("没有先行实体，无法根据零件边界放置凹槽。")
        tool = _pocket_tool(feature, bbox)
        pstat = "approximate" if float(p.get("corner", 0) or 0) else "exact"
        return [tool], [], pstat, "polygonal_prismatic_pocket_tool"
    if kind == "pattern":
        return [], [], "exact", "resolved_by_source_relation"
    if kind == "shell":
        raise NotImplementedError(GEOMETRY_SUPPORT["shell"][1])
    if kind == "extrude_holes":
        length, a, b = float(p.get("length", 100)), float(p.get("sec_a", 80)), float(p.get("sec_b", 80))
        ax = (p.get("axis") or "Z").upper()
        base = float(p.get("base", 0.0))
        ai = _axis_index(ax)
        c = [0.0, 0.0, 0.0]; c[ai] = base + length / 2.0
        body = make_prism(K.profile_rect(a, b, 0.0, "fillet"), length,
                          tuple(c), ax, symmetric=True)
        bb = _bbox(body)
        cutters, holes = [], []
        for index, item in enumerate(p.get("holes") or [], 1):
            diameter = float(item.get("d") or 0.0)
            if diameter <= 0:
                raise ValueError("内置孔 %d 的直径无效。" % index)
            hax = (item.get("axis") or "Z").upper()
            plane = _plane_indices(hax)
            u, v = float(item.get("u") or 0.0), float(item.get("v") or 0.0)
            if p.get("ref_min") and p.get("ref_size"):
                u -= float(p["ref_min"][plane[0]]) + float(p["ref_size"][plane[0]]) / 2.0
                v -= float(p["ref_min"][plane[1]]) + float(p["ref_size"][plane[1]]) / 2.0
            lo, hi = _bounds_for_axis(bb, hax)
            tools.append(make_cylinder(diameter / 2.0, hi - lo + 20.0,
                                       _place(u, v, hax, lo - 10.0), hax, True))
            holes.append({"feature_id": feature.feature_id or feature.name,
                          "instance_index": index, "axis": hax,
                          "centerline_mm": list(_place(u, v, hax, (lo + hi) / 2.0)),
                          "diameter_mm": diameter, "depth_mm": None,
                          "through": True, "planned_status": "pending"})
        if tools:
            body = cut_many(body, tools)
            _assert_valid_solid(body, "含通孔拉伸体")
        return [body], holes, "exact", "analytic_prism_with_cylindrical_bores"
    if kind in {"real_solid", "hex_bolt", "helix",
                "vbelt_pulley", "timing_pulley", "worm",
                "helical_gear", "bevel_gear", "crankshaft", "camshaft",
                "impeller", "conrod", "cross_joint", "scroll_disc",
                "rotor"}:
        context = {"tag": 0, "bbox": bbox}
        meshes = [poly for solid in feature.build(context) for poly in solid]
        result, conversion = _polygon_mesh_to_brep(meshes, max_faces=max_mesh_faces)
        return [result], [], "approximate", conversion["conversion"]
    raise NotImplementedError("当前 Document 特征 %r 未映射到 BREP 后端。" % kind)


def _pattern_geometry(doc, feature, feature_shapes_by_index, feature_holes_by_index):
    params = feature.params
    relations = getattr(feature, "relations", []) or []
    relation = next((row for row in relations if row.get("type") == "pattern_source"), None)
    source_id = relation.get("feature_id") if relation else None
    if source_id:
        source_index = next((i for i, f in enumerate(doc.features)
                             if f.feature_id == source_id), None)
        if source_index is None or source_index not in feature_shapes_by_index:
            raise ValueError("pattern_source 指向不存在或未构建的特征 id=%s。" % source_id)
    else:
        source_index = next((i for i in range(len(doc.features) - 1, -1, -1)
                             if doc.features[i] is not feature and i in feature_shapes_by_index), None)
        if source_index is None:
            raise ValueError("pattern 没有可用的源特征。")
        source_id = doc.features[source_index].feature_id or str(source_index)
    source_shapes = feature_shapes_by_index[source_index]
    source_holes = feature_holes_by_index.get(source_index, [])
    count = int(params.get("count", 1))
    if count < 1:
        raise ValueError("pattern count 必须至少为 1。")
    out, holes = [], []
    if params.get("mode", "linear") == "circular":
        axis = (params.get("axis") or "Z").upper()
        angle = float(params.get("angle", 360.0))
        full = bool(params.get("full", abs(angle - 360.0) <= 1e-8))
        step = angle / count if full else angle / max(1, count - 1)
        center = tuple(params.get("center", (0.0, 0.0, 0.0)))
        for i in range(1, count):
            out.extend(_transform_shape(shape, axis=axis, angle_deg=step * i,
                                        center=center) for shape in source_shapes)
            for row in source_holes:
                c = tuple(row["centerline_mm"])
                u, v, w = K._axis_basis(axis)
                rel = (c[0] - center[0], c[1] - center[1], c[2] - center[2])
                angle_rad = math.radians(step * i)
                co, si = math.cos(angle_rad), math.sin(angle_rad)
                ru = K.v_add(K.v_mul(u, K.v_dot(rel, u) * co - K.v_dot(rel, v) * si),
                             K.v_mul(v, K.v_dot(rel, u) * si + K.v_dot(rel, v) * co))
                rw = K.v_mul(w, K.v_dot(rel, w))
                transformed = K.v_add(center, K.v_add(ru, rw))
                holes.append(dict(row, centerline_mm=list(transformed),
                                  instance_index=int(row.get("instance_index", 1)) + i * len(source_holes)))
    else:
        step = tuple(float(v) for v in params.get("step", (10.0, 0.0, 0.0)))
        if len(step) != 3:
            raise ValueError("线性阵列 step 必须是全局 XYZ 三元组。")
        for i in range(1, count):
            shift = tuple(value * i for value in step)
            out.extend(_transform_shape(shape, vector=shift) for shape in source_shapes)
            for row in source_holes:
                c = tuple(row["centerline_mm"])
                holes.append(dict(row,
                                  centerline_mm=[c[j] + shift[j] for j in range(3)],
                                  instance_index=int(row.get("instance_index", 1)) + i * len(source_holes)))
    return out, holes, source_id


def build_document(doc, tolerance: float = 1e-5,
                   max_mesh_faces: int = 120000,
                   self_intersection_face_limit: int = SELF_INTERSECTION_FACE_LIMIT):
    """Execute Document features in order as transactional OCCT BREP ops.

    No BSP fallback is performed.  Each operation is validated before it is
    committed as the next intermediate; exceptions carry the last valid shape.
    """
    require_available()
    if isinstance(self_intersection_face_limit, bool):
        raise ValueError("self_intersection_face_limit 必须是正整数。")
    try:
        self_intersection_face_limit = int(self_intersection_face_limit)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("self_intersection_face_limit 必须是正整数。") from exc
    if self_intersection_face_limit < 1:
        raise ValueError("self_intersection_face_limit 必须是正整数。")
    current = None
    bbox = None
    features_by_id = {}
    shapes_by_index: Dict[int, List[Any]] = {}
    holes_by_index: Dict[int, List[Dict[str, Any]]] = {}
    per_feature = []
    prior_holes = []
    unverified_cut_intents = []
    for index, feature in enumerate(doc.features):
        if not feature.enabled:
            continue
        feature_id = feature.feature_id or str(index)
        before_volume = volume(current) if current is not None else 0.0
        candidate = None
        after_volume = None
        delta = None
        failure_code = "feature_build_failed"
        related_feature_ids: List[str] = []
        warning = None
        try:
            support = GEOMETRY_SUPPORT.get(feature.kind)
            if support and support[0] == "unavailable":
                # Auto mode can retain the editable candidate while clearly
                # recording that this feature did not change local geometry.
                # Strict CATIA support is decided by the SDK before execution.
                per_feature.append({
                    "feature_id": feature_id, "kind": feature.kind,
                    "operation": feature.op, "geometry_support": "unavailable",
                    "builder": None, "state": "skipped",
                    "warning": support[1],
                })
                shapes_by_index[index] = []
                holes_by_index[index] = []
                continue
            if feature.kind == "pattern":
                tools, planned_holes, source_id = _pattern_geometry(
                    doc, feature, shapes_by_index, holes_by_index)
                geom_status, builder_name = "exact", "explicit_pattern_transform"
                source_method = "declared_relation" if any(
                    row.get("type") == "pattern_source" for row in (feature.relations or [])) else "previous_feature_compatibility"
            else:
                tools, planned_holes, geom_status, builder_name = _feature_geometry(
                    doc, feature, bbox, max_mesh_faces, current)
                source_id, source_method = None, None
            feature_cut_intents = []
            if feature.op == "cut" and current is not None and not planned_holes:
                for tool in tools:
                    intent = _pure_cylindrical_tool_intent(
                        tool, feature_id, bbox, current, prior_holes)
                    if intent is None:
                        continue
                    feature_cut_intents.append(intent)
                    if intent.get("classification") == "verified_intent_candidate":
                        planned_holes.append(intent)
                    else:
                        unverified_cut_intents.append(intent)
            if feature.kind == "pattern" and not tools:
                per_feature.append({
                    "feature_id": feature_id, "kind": feature.kind,
                    "operation": feature.op, "geometry_support": "exact",
                    "builder": "explicit_pattern_transform", "state": "no_op",
                    "source_feature_id": source_id,
                    "source_resolution": source_method,
                    "warning": "阵列数量为 1；源特征已存在，没有新增实例。",
                })
                shapes_by_index[index] = []
                holes_by_index[index] = []
                continue
            if not tools:
                raise ValueError("特征未产生可执行的实体或刀具体。")
            if planned_holes and feature.op == "cut" and feature.kind != "pattern":
                prior_instances = list(prior_holes)
                duplicates = []
                for hole in planned_holes:
                    matches = [old for old in prior_instances
                               if _same_through_hole_axis(old, hole)]
                    if matches:
                        duplicates.extend(matches)
                    prior_instances.append(hole)
                if duplicates:
                    failure_code = "duplicate_through_axis"
                    related_feature_ids = sorted({
                        str(row.get("feature_id")) for row in duplicates
                        if row.get("feature_id") is not None
                    })
                    related_feature_ids = sorted(set(related_feature_ids + [feature_id]))
                    raise ValueError(
                        "贯穿孔轴线已被不小于当前孔径的贯穿孔占据；新切除不会去除材料。关联特征：%s。"
                        % ", ".join(related_feature_ids))
            if feature.op == "cut" and current is None:
                raise ValueError("切除特征前不存在目标实体。")
            before_bbox = bbox
            if current is None:
                if feature.op == "cut":
                    raise ValueError("首个实体不能是切除特征。")
                candidate = fuse_many(tools)
            elif feature.op == "cut":
                candidate = cut_many(current, tools)
            elif feature.op == "add":
                candidate = fuse_many([current] + tools)
            else:
                raise ValueError("operation 必须是 add 或 cut，收到 %r。" % feature.op)
            if candidate is None or candidate.IsNull():
                raise RuntimeError("OCCT 操作返回空实体；当前特征未提交。")
            info = _assert_valid_solid(candidate, "特征 %s (%s)" % (feature_id, feature.kind))
            after_volume = float(info["volume_mm3"])
            delta = after_volume - before_volume
            if feature.op == "cut" and delta >= -max(1e-8, before_volume * 1e-10):
                if feature.kind == "pattern":
                    warning = ("阵列刀具体没有去除新增材料（可能与源孔的贯穿空间重合）；"
                               "保留现有实体并将该阵列标记为无几何变化。")
                    per_feature.append({
                        "feature_id": feature_id, "kind": feature.kind,
                        "operation": feature.op, "geometry_support": geom_status,
                        "builder": builder_name, "state": "no_op",
                        "source_feature_id": source_id,
                        "source_resolution": source_method,
                        "before_volume_mm3": before_volume,
                        "after_volume_mm3": after_volume,
                        "volume_delta_mm3": delta,
                        "warning": warning,
                        "failure_code": "cut_no_material_change",
                    })
                    # Keep tools available for a later relation, but do not
                    # count overlapping array locations as new hole instances.
                    shapes_by_index[index] = list(tools)
                    holes_by_index[index] = []
                    continue
                # Check axis-aligned external bounds for a cutter face that is
                # exactly coincident with a target bbox face.  This gives a
                # useful, measured remediation hint without treating arbitrary
                # zero-volume booleans as face coincidence.
                candidate_bounds = [_bbox(tool) for tool in tools]
                touching = False
                current_bounds = before_bbox
                tol = 1e-7
                if current_bounds:
                    for bounds in candidate_bounds:
                        if not bounds:
                            continue
                        for axis_index in range(3):
                            other = [i for i in range(3) if i != axis_index]
                            overlap = all(
                                min(bounds[i + 3], current_bounds[i + 3])
                                - max(bounds[i], current_bounds[i]) > tol
                                for i in other)
                            face_match = (
                                abs(bounds[axis_index + 3] - current_bounds[axis_index]) <= tol
                                or abs(bounds[axis_index] - current_bounds[axis_index + 3]) <= tol)
                            if overlap and face_match:
                                touching = True
                                break
                        if touching:
                            break
                if touching:
                    failure_code = "coincident_face"
                    warning = ("切除面与实体外表面完全重合，实际去除体积为 0；"
                               "请将切面向实体内部移动至少 0.001 mm。")
                else:
                    failure_code = "cut_no_material_change"
                    warning = "切除没有改变材料（刀具体未与实体相交或已位于空腔中）。"
                raise RuntimeError(warning)
            pocket_depth = None
            if feature.kind == "pocket" and feature.op == "cut":
                pocket_depth = _measure_pocket_depth(candidate, feature, before_bbox)
                if pocket_depth.get("state") != "measured":
                    raise RuntimeError(
                        "凹槽深度实测为 %s（要求 %s mm，实测 %s mm，误差容限 %s mm）；"
                        "已回退该凹槽特征。" % (
                            pocket_depth.get("state"),
                            pocket_depth.get("requested_depth_mm"),
                            pocket_depth.get("measured_depth_mm"),
                            pocket_depth.get("tolerance_mm")))
            # Atomic commit occurs only after OCCT completed and the result is valid.
            current = candidate
            bbox = _bbox(current)
            shapes_by_index[index] = list(tools)
            holes_by_index[index] = list(planned_holes)
            if planned_holes and feature.op == "cut":
                for added_hole in planned_holes:
                    new_diameter = float(added_hole.get("diameter_mm", 0.0) or 0.0)
                    for old_hole in prior_holes:
                        old_diameter = float(old_hole.get("diameter_mm", 0.0) or 0.0)
                        if not _coaxial_hole_axis(old_hole, added_hole):
                            continue
                        old_depth = float(old_hole.get("depth_mm") or 0.0)
                        new_depth = float(added_hole.get("depth_mm") or 0.0)
                        if added_hole.get("through") and new_diameter + 1e-5 >= old_diameter:
                            old_hole["superseded_by"] = feature_id
                        elif (not old_hole.get("through") and not added_hole.get("through")
                              and new_diameter + 1e-5 >= old_diameter
                              and new_depth + 1e-5 >= old_depth):
                            old_hole["superseded_by"] = feature_id
                        elif (not old_hole.get("through") and not added_hole.get("through")
                              and new_diameter > old_diameter + 1e-5
                              and old_depth > new_depth + 1e-5):
                            _low, high = _bounds_for_axis(bbox, old_hole["axis"])
                            old_hole["partially_superseded_by"] = feature_id
                            old_hole["preserved_wall_interval_mm"] = [
                                high - old_depth, high - new_depth]
                        elif (old_hole.get("through") is False
                              and added_hole.get("through")
                              and old_diameter > new_diameter + 1e-5):
                            _low, high = _bounds_for_axis(bbox, old_hole["axis"])
                            old_hole["partially_superseded_by"] = feature_id
                            old_hole["preserved_wall_interval_mm"] = [
                                high - old_depth, high]
            prior_holes.extend(planned_holes)
            per_feature.append({
                "feature_id": feature_id, "kind": feature.kind,
                "operation": feature.op, "geometry_support": geom_status,
                "builder": builder_name, "state": "applied",
                "source_feature_id": source_id, "source_resolution": source_method,
                "solid_count": info["solid_count"],
                "brep_valid": info["brep_valid"],
                "before_volume_mm3": before_volume,
                "after_volume_mm3": after_volume,
                "volume_delta_mm3": delta,
                "cylindrical_cut_intents": feature_cut_intents,
                "depth_measurement": pocket_depth,
                "facet_count": info.get("face_count") if builder_name == "faceted_brep_approximation" else None,
            })
        except Exception as exc:
            failed_effect = {
                "feature_id": feature_id,
                "operation": feature.op,
                "state": ("no_op" if failure_code in {"cut_no_material_change", "coincident_face"}
                          and after_volume is not None and delta is not None
                          and abs(delta) <= max(1e-8, before_volume * 1e-10)
                          else "failed" if after_volume is not None else "unmeasured"),
                "kind": feature.kind,
                "before_volume_mm3": before_volume,
                "after_volume_mm3": after_volume,
                "volume_delta_mm3": delta,
                "warning": warning,
                "reason": str(exc),
                "failure_code": failure_code,
                "related_feature_ids": related_feature_ids,
            }
            report = {
                "backend": "occt_brep", "kernel": status(),
                "geometry_state": "failed", "state": "failed",
                "brep_valid": bool(current is not None and BRepCheck_Analyzer(current, True).IsValid()),
                "failed_feature_id": feature_id, "failed_feature_kind": feature.kind,
                "failure_code": failure_code,
                "related_feature_ids": related_feature_ids,
                "failed_feature_parameters": dict(feature.params),
                "error": str(exc), "feature_results": per_feature,
                "failed_feature_effect": failed_effect,
                "volume_mm3": volume(current) if current is not None else None,
                "bbox_mm": _bbox(current) if current is not None else None,
                "hole_instances": _check_hole_instances(current, prior_holes) if current is not None else [],
                "unverified_cut_intents": list(unverified_cut_intents),
                "visual_validation": "not_performed",
                "transaction": "失败特征未提交；保留此前有效中间实体",
            }
            raise GeometryBuildError(
                "特征 %s (%s) 建模失败：%s" % (feature_id, feature.kind, exc),
                last_valid_shape=current, report=report) from exc
    if current is None:
        raise GeometryBuildError("CADPlan 未生成任何实体。", last_valid_shape=None,
                                 report={"geometry_state": "failed", "state": "failed"})
    _assert_valid_solid(current, "最终模型")
    final_info = inspect_shape(
        current, self_intersection_face_limit=self_intersection_face_limit)
    hole_checks = _check_hole_instances(current, prior_holes)
    self_status = (final_info.get("self_intersection") or {}).get("status")
    geometry_state = ("failed" if not final_info["brep_valid"] or not final_info["closed"]
                      or self_status == "self_intersection"
                      else "needs_review" if any(row["status"] not in
                                                 {"verified_open", "verified_blind",
                                                  "verified_counterbore", "verified_blind_segment",
                                                  "superseded"}
                                                 for row in hole_checks)
                      or any(row.get("state") in {"skipped", "no_op"}
                             for row in per_feature)
                      or self_status not in {"checked_no_intersection", "not_applicable"}
                      or unverified_cut_intents
                      else "passed")
    report = {
        "backend": "occt_brep", "kernel": status(),
        "self_intersection_face_limit": self_intersection_face_limit,
        "geometry_state": geometry_state, "state": geometry_state,
        **final_info,
        "feature_results": per_feature,
        "hole_instances": hole_checks,
        "unverified_cut_intents": unverified_cut_intents,
        "hole_feature_count": sum(1 for feature in doc.features
                                   if feature.enabled and feature.kind == "hole"),
        "measured_hole_source_feature_count": len({str(row.get("feature_id")) for row in hole_checks
                                                     if row.get("feature_id") is not None}),
        "unverified_cut_intent_count": len(unverified_cut_intents),
        "planned_hole_instance_count": len(prior_holes),
        "active_hole_instance_count": sum(1 for row in hole_checks
                                           if row.get("status") != "superseded"),
        "superseded_hole_instance_count": sum(1 for row in hole_checks
                                               if row.get("status") == "superseded"),
        "active_through_hole_count": sum(1 for row in hole_checks
                                         if row.get("through")
                                         and row.get("status") != "superseded"),
        "through_hole_count": sum(1 for row in hole_checks
                                   if row["through"] and row["status"] == "verified_open"),
        "all_declared_holes_verified": (not unverified_cut_intents and all(row["status"] in
                                           {"verified_open", "verified_blind",
                                            "verified_counterbore", "verified_blind_segment",
                                            "superseded"}
                                           for row in hole_checks)),
        "visual_validation": "not_performed",
        "catia_executed": False,
        "build_transaction": "每个特征验证成功后才提交；失败保留上一有效实体",
    }
    return current, report


def build_polys(doc, tol: float = 0.12):
    """Build the authoritative BREP then triangulate for the existing renderer."""
    shape, _ = build_document(doc)
    return shape_to_polys(shape, tol) if shape is not None else []
