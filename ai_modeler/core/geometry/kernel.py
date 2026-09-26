# -*- coding: utf-8 -*-
"""AI 自动建模助手 —— 几何内核 (纯标准库实现)

包含:
  * 向量 / 平面基础运算
  * BSP 树 CSG 布尔运算 (并集 / 交集 / 差集)
  * 基础特征构造: 长方体、圆柱、圆锥、球、拉伸体(棱柱)、回转体
  * 2D 轮廓工具: 矩形、圆、多边形、圆角、倒角、耳切三角化

坐标系: 右手系, Z 轴向上 (与 CATIA 默认一致)。
单位: 毫米 (mm)。
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from ai_modeler.parts import std_pulley

Vec3 = Tuple[float, float, float]
EPS = 1e-5

# --------------------------------------------------------------------------
# 向量运算
# --------------------------------------------------------------------------


def v_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_len(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_norm(a: Vec3) -> Vec3:
    ln = v_len(a)
    if ln < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / ln, a[1] / ln, a[2] / ln)


def v_lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


# --------------------------------------------------------------------------
# 平面 / 多边形 / BSP 结点
# --------------------------------------------------------------------------

COPLANAR, FRONT, BACK, SPANNING = 0, 1, 2, 3


class Plane(object):
    __slots__ = ('normal', 'w')

    def __init__(self, normal: Vec3, w: float):
        self.normal = normal
        self.w = w

    @staticmethod
    def from_points(a: Vec3, b: Vec3, c: Vec3) -> 'Plane':
        n = v_norm(v_cross(v_sub(b, a), v_sub(c, a)))
        return Plane(n, v_dot(n, a))

    def clone(self) -> 'Plane':
        return Plane(self.normal, self.w)

    def flip(self) -> None:
        self.normal = v_mul(self.normal, -1.0)
        self.w = -self.w


class Polygon(object):
    __slots__ = ('vertices', 'plane', 'tag')

    def __init__(self, vertices, plane: Plane, tag: int = 0):
        self.vertices = vertices          # [(Vec3, Vec3 normal), ...]
        self.plane = plane
        self.tag = tag

    def clone(self) -> 'Polygon':
        return Polygon([(p, n) for (p, n) in self.vertices],
                       self.plane.clone(), self.tag)

    def flip(self) -> None:
        self.vertices = [(p, v_mul(n, -1.0)) for (p, n) in reversed(self.vertices)]
        self.plane.flip()

    def points(self) -> List[Vec3]:
        return [p for (p, _n) in self.vertices]


class Node(object):
    __slots__ = ('plane', 'front', 'back', 'polygons', 'bbox')

    def __init__(self, polygons=None):
        self.plane = None
        self.front = None
        self.back = None
        self.polygons = list(polygons) if polygons else []
        #: 子树所有多边形的包围盒 (lo0,lo1,lo2,hi0,hi1,hi2)，**只增不减**。
        #  只用于剪枝：它是保守的外包盒，所以"不相交就跳过"永远安全。
        self.bbox = None


# --------------------------------------------------------------------------
# 包围盒（A′ 计划：局部布尔用的剪枝依据）
# --------------------------------------------------------------------------


def _poly_bbox(p) -> Tuple[float, ...]:
    vs = p.vertices
    lo0 = lo1 = lo2 = float('inf')
    hi0 = hi1 = hi2 = float('-inf')
    for (pos, _n) in vs:
        x, y, z = pos
        if x < lo0:
            lo0 = x
        if x > hi0:
            hi0 = x
        if y < lo1:
            lo1 = y
        if y > hi1:
            hi1 = y
        if z < lo2:
            lo2 = z
        if z > hi2:
            hi2 = z
    return (lo0, lo1, lo2, hi0, hi1, hi2)


def _polys_bbox(polys) -> Tuple[float, ...]:
    lo0 = lo1 = lo2 = float('inf')
    hi0 = hi1 = hi2 = float('-inf')
    for p in polys:
        b = _poly_bbox(p)
        if b[0] < lo0:
            lo0 = b[0]
        if b[1] < lo1:
            lo1 = b[1]
        if b[2] < lo2:
            lo2 = b[2]
        if b[3] > hi0:
            hi0 = b[3]
        if b[4] > hi1:
            hi1 = b[4]
        if b[5] > hi2:
            hi2 = b[5]
    if lo0 == float('inf'):
        return None
    return (lo0, lo1, lo2, hi0, hi1, hi2)


def _bb_union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def _bb_overlap(a, b) -> bool:
    """两个包围盒是否有重叠（含相切）。None 视为"未知"，按相交处理。"""
    if a is None or b is None:
        return True
    return not (a[3] < b[0] or b[3] < a[0] or a[4] < b[1] or b[4] < a[1]
                or a[5] < b[2] or b[5] < a[2])


def split_polygon(plane: Plane, polygon: Polygon,
                  coplanar_front: List[Polygon], coplanar_back: List[Polygon],
                  front: List[Polygon], back: List[Polygon]) -> None:
    """按 plane 切分 polygon, 结果写入四个输出列表。"""
    polygon_type = 0
    types = []
    for (pos, _n) in polygon.vertices:
        t = v_dot(plane.normal, pos) - plane.w
        if t < -EPS:
            ty = BACK
        elif t > EPS:
            ty = FRONT
        else:
            ty = COPLANAR
        polygon_type |= ty
        types.append(ty)

    if polygon_type == COPLANAR:
        if v_dot(plane.normal, polygon.plane.normal) > 0:
            coplanar_front.append(polygon)
        else:
            coplanar_back.append(polygon)
    elif polygon_type == FRONT:
        front.append(polygon)
    elif polygon_type == BACK:
        back.append(polygon)
    else:  # SPANNING
        f, b = [], []
        n = len(polygon.vertices)
        for i in range(n):
            j = (i + 1) % n
            ti, tj = types[i], types[j]
            vi = polygon.vertices[i]
            vj = polygon.vertices[j]
            if ti != BACK:
                f.append(vi)
            if ti != FRONT:
                b.append(vi)
            if (ti | tj) == SPANNING:
                denom = v_dot(plane.normal, v_sub(vj[0], vi[0]))
                t = 0.0 if abs(denom) < 1e-12 else (plane.w - v_dot(plane.normal, vi[0])) / denom
                pos = v_lerp(vi[0], vj[0], t)
                nor = v_norm(v_lerp(vi[1], vj[1], 0.5))
                f.append((pos, nor))
                b.append((pos, nor))
        if len(f) >= 3:
            front.append(Polygon(f, polygon.plane.clone(), polygon.tag))
        if len(b) >= 3:
            back.append(Polygon(b, polygon.plane.clone(), polygon.tag))


# --------------------------------------------------------------------------
# BSP 结点操作 (全部使用显式栈, 避免递归深度问题)
# --------------------------------------------------------------------------


def node_build(root: Node, polygons: Sequence[Polygon]) -> None:
    stack = [(root, list(polygons))]
    while stack:
        node, polys = stack.pop()
        if not polys:
            continue
        # 包围盒随插入**向外扩**（保守）：入栈的这批多边形一定包住它自己
        # 分裂出来的后代（分裂只会在原多边形内部切），所以这样维护的
        # `node.bbox` 永远是子树的超集 —— 剪枝判据才成立。
        node.bbox = _bb_union(node.bbox, _polys_bbox(polys))
        if node.plane is None:
            node.plane = polys[0].plane.clone()
        front, back = [], []
        for p in polys:
            split_polygon(node.plane, p, node.polygons, node.polygons, front, back)
        if front:
            if node.front is None:
                node.front = Node()
            stack.append((node.front, front))
        if back:
            if node.back is None:
                node.back = Node()
            stack.append((node.back, back))


def node_clip_polygons(root: Node, polygons: Sequence[Polygon],
                       keep: str = 'front', clip_bbox=None,
                       strict_coplanar: bool = False,
                       coplanar_inside: bool = False) -> List[Polygon]:
    """把 polygons 按 root 这棵 BSP 裁掉一半。

    keep='front'（默认，也是 CSG 差集用的）：**丢掉实体内部**的部分，
      留下的就是"实体外面"的部分；
    keep='back'：反过来，**只留实体内部**的部分。

    `coplanar_inside=True`：与结点平面**共面**的面片算作"内部"（keep='front'
      时会被丢掉）。这一条是**必需**的，不是保险：
      实测方块顶面 z=20 与刀具顶盖 z=20 齐平时，共面面片若按"同向即外部"
      分流，会整片落到 front 叶子上**原样保留**（根本不再被圆柱侧面切），
      于是"孔口那一片顶面"没被挖掉 —— 体积 67859，比正确值 65788 多 2000。
      参考实现靠"先给 A 取反"把同向共面变成反向，这里用这个开关直接表达同一件事。

    `strict_coplanar=True`：与结点平面共面的面片一律算作"外部"。
    这是复刻 csg.js 差集里 `b.invert(); b.clipTo(a); b.invert()` 那两步的净效果：
    刀具面上**与实体表面重合**的碎片不要（重合面会留下两份，渲染会闪、体积重复计）。

    `clip_bbox` 是"这堆多边形自己的包围盒"，只用来做**整体**短路：
    它和**整棵树**的包围盒（`root.bbox`，实体体积的超集）不相交时，
    这堆面全都落在实体之外，一次都不用往下切。
    ⚠ 不能拿它跟**子结点**的包围盒比：子结点的包围盒只是实体的一部分，
    "与子结点不相交"完全可能是"在实体内部但离这个子结点很远"，
    那样跳过就会把该丢的面留下来（我第一版就是这么写的，判据本身错了）。
    """
    if clip_bbox is not None and not _bb_overlap(root.bbox, clip_bbox):
        return list(polygons) if keep == 'front' else []
    result: List[Polygon] = []
    stack = [(root, list(polygons))]
    while stack:
        node, polys = stack.pop()
        if not polys:
            continue
        if node.plane is None:
            if keep == 'front':
                result.extend(polys)
            continue
        front, back = [], []
        if coplanar_inside:
            for p in polys:
                split_polygon(node.plane, p, back, back, front, back)
        elif strict_coplanar:
            for p in polys:
                split_polygon(node.plane, p, front, front, front, back)
        else:
            for p in polys:
                split_polygon(node.plane, p, front, back, front, back)
        if node.front is not None:
            stack.append((node.front, front))
        elif keep == 'front':
            result.extend(front)
        if node.back is not None:
            stack.append((node.back, back))
        elif keep == 'back':
            result.extend(back)
    return result


def node_clip_to(root: Node, bsp: Node) -> None:
    """把 root 这棵树里**落在 bsp 实体内部**的面片裁掉（就地）。

    ⚠ 这里**不做包围盒剪枝**（试过了，会把 `csg_subtract` 算错）。
    原因是 `csg_subtract` 会先给 A 取反再调它：此时"树代表的实体"是补集，
    "与 bsp 包围盒不相交"意味着"在 bsp 之外"，而这一步要丢的恰恰是"之外"的部分 ——
    跳过的方向正好相反（实测方块 + 通孔从 118433.7 变成 117128.4，多切了 1300 mm³）。
    要做局部化就必须**显式知道裁剪方向**，那就是 `solid_cut` 里
    `node_clip_polygons(..., keep=..., clip_bbox=...)` 的做法。
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.polygons:
            node.polygons = node_clip_polygons(bsp, node.polygons)
        if node.front is not None:
            stack.append(node.front)
        if node.back is not None:
            stack.append(node.back)


def solid_begin(polys: Sequence[Polygon]) -> Node:
    """把一堆面片变成可**增量切除**的实体（BSP 累加器）。"""
    n = Node()
    node_build(n, [p.clone() for p in polys])
    return n


def solid_cut(root: Node, cutter_polys: Sequence[Polygon]) -> int:
    """**局部**布尔切除：把 cutter 从 root 里挖掉（就地改 root）。

    与 `csg_subtract` 的结果语义相同，但：
      · 不做 `node_invert(root)`（那是 O(全部结点) 的整树翻转）；
      · 不重建 root 的 BSP 树（旧实现每切一刀都要拿全部面片重建一次）；
      · 只处理与刀具包围盒相交的面片。
    返回本次真正被处理的面片数（诊断用）。

    正确性论证（也是判据，见 `tools/check_bsp_local.py` 的等价性对比）：
      差集 A−B 的面 = (A 的面在 B 之外的部分) ∪ (B 的面在 A 之内的部分, 翻转)。
      · "A 的面在 B 之外" = 拿 B 的树裁掉内部（`node_clip_polygons`，默认 front 语义）；
      · "B 的面在 A 之内" = 拿 A 的树只留内部（keep='back'），再整体翻转；
      · 开关（invert）那几步在旧实现里是为了让"内部/外部"语义对调，
        这里用 keep 参数直接表达，效果一致但不碰整棵树。
    """
    if root.plane is None or not cutter_polys:
        return 0
    b = solid_begin(cutter_polys)
    # ① A 的面：干掉落在刀具内部的部分（只碰刀具附近）
    touched = [0]

    def _walk():
        stack = [root]
        while stack:
            node = stack.pop()
            if node.polygons and _bb_overlap(node.bbox, b.bbox):
                # ⚠ 还要按**每张面片自己的包围盒**过滤一次。结点的包围盒是
                #   "只增不减"的保守盒，跑过几十刀之后高层结点的盒子会包住整个
                #   零件 —— 只按结点判，就会把几千张离刀很远的**长面**也塞进
                #   `node_clip_polygons`：刀具的侧面平面是无限大的，那些长面
                #   跨过它们就被切成一堆碎片（实测每刀 16 万次 split、单刀 1 秒）。
                #   面片自己的盒子与刀具盒子不相交 ⇒ 它整张都在刀具之外 ⇒ 不用切。
                near, far = [], []
                for p in node.polygons:
                    if _bb_overlap(_poly_bbox(p), b.bbox):
                        near.append(p)
                    else:
                        far.append(p)
                if near:
                    clipped = node_clip_polygons(b, near, coplanar_inside=True)
                    node.polygons = far + clipped if far else clipped
                    touched[0] += len(near)
            # 子结点也按包围盒剪：这里的动作是**明确方向**的（"丢掉落在刀具
            # 内部的面片"），所以"子树与刀具包围盒不相交 ⇒ 不用切"成立。
            if node.front is not None and _bb_overlap(node.front.bbox, b.bbox):
                stack.append(node.front)
            if node.back is not None and _bb_overlap(node.back.bbox, b.bbox):
                stack.append(node.back)

    _walk()
    # ② B 的面：只留落在 A 内部的部分，翻转后并进 A
    #   `strict_coplanar=True` 复刻 csg.js 里 `b.invert(); b.clipTo(a); b.invert()`
    #   那两步的净效果：与 A 表面**重合**的刀具面片不要（否则会出现两张重合面）。
    cbb = _polys_bbox(cutter_polys)
    inner = node_clip_polygons(root, [p.clone() for p in cutter_polys],
                               keep='back', clip_bbox=cbb,
                               strict_coplanar=True)
    if inner:
        for p in inner:
            p.flip()
        node_build(root, inner)
    return touched[0]


def node_invert(root: Node) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        for p in node.polygons:
            p.flip()
        if node.plane is not None:
            node.plane.flip()
        node.front, node.back = node.back, node.front
        if node.front is not None:
            stack.append(node.front)
        if node.back is not None:
            stack.append(node.back)


def node_all_polygons(root: Node) -> List[Polygon]:
    out: List[Polygon] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.extend(node.polygons)
        if node.front is not None:
            stack.append(node.front)
        if node.back is not None:
            stack.append(node.back)
    return out


def _make_node(polys: Sequence[Polygon]) -> Node:
    n = Node()
    node_build(n, [p.clone() for p in polys])
    return n


# --------------------------------------------------------------------------
# CSG 布尔运算
# --------------------------------------------------------------------------


def csg_union(pa: Sequence[Polygon], pb: Sequence[Polygon]) -> List[Polygon]:
    a, b = _make_node(pa), _make_node(pb)
    node_clip_to(a, b)
    node_clip_to(b, a)
    node_invert(b)
    node_clip_to(b, a)
    node_invert(b)
    node_build(a, node_all_polygons(b))
    return node_all_polygons(a)


def csg_subtract(pa: Sequence[Polygon], pb: Sequence[Polygon]) -> List[Polygon]:
    a, b = _make_node(pa), _make_node(pb)
    node_invert(a)
    node_clip_to(a, b)
    node_clip_to(b, a)
    node_invert(b)
    node_clip_to(b, a)
    node_invert(b)
    node_build(a, node_all_polygons(b))
    node_invert(a)
    return node_all_polygons(a)


def csg_intersect(pa: Sequence[Polygon], pb: Sequence[Polygon]) -> List[Polygon]:
    a, b = _make_node(pa), _make_node(pb)
    node_invert(a)
    node_clip_to(b, a)
    node_invert(b)
    node_clip_to(a, b)
    node_clip_to(b, a)
    node_build(a, node_all_polygons(b))
    node_invert(a)
    return node_all_polygons(a)


# --------------------------------------------------------------------------
# 多边形构造辅助
# --------------------------------------------------------------------------


def make_polygon(points: Sequence[Vec3], tag: int = 0,
                 center: Vec3 = None) -> Polygon:
    """由点列构造多边形；center 非空时保证法向朝外。"""
    pts = list(points)
    if len(pts) < 3:
        return None
    normal = (0.0, 0.0, 0.0)
    for i in range(1, len(pts) - 1):
        normal = v_norm(v_cross(v_sub(pts[i], pts[0]), v_sub(pts[i + 1], pts[0])))
        if v_len(normal) > 1e-9:
            break
    if v_len(normal) < 1e-9:
        return None
    if center is not None:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        if v_dot(normal, (cx - center[0], cy - center[1], cz - center[2])) < 0:
            pts.reverse()
            normal = v_mul(normal, -1.0)
    w = v_dot(normal, pts[0])
    return Polygon([(p, normal) for p in pts], Plane(normal, w), tag)


def polygons_to_triangles(polys: Sequence[Polygon]) -> List[Tuple[Vec3, Vec3, Vec3, int]]:
    """扇形三角化，供渲染与 STL/OBJ 导出使用。"""
    tris = []
    for p in polys:
        pts = p.points()
        if len(pts) < 3:
            continue
        for i in range(1, len(pts) - 1):
            tris.append((pts[0], pts[i], pts[i + 1], p.tag))
    return tris


# --------------------------------------------------------------------------
# 2D 轮廓工具 (工作在草图平面 local XY)
# --------------------------------------------------------------------------


def profile_rect(length: float, width: float, corner: float = 0.0,
                 corner_mode: str = 'fillet', segments: int = 8) -> List[Tuple[float, float]]:
    """矩形轮廓，可选圆角/倒角。corner 为半径或倒角边长。"""
    hl, hw = length / 2.0, width / 2.0
    try:
        corner = float(corner or 0.0)
    except (TypeError, ValueError):
        corner = 0.0
    corner = max(0.0, min(corner, min(hl, hw) - 1e-6))
    if corner <= 1e-9:
        return [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]

    pts: List[Tuple[float, float]] = []
    # 四个角: (中心x, 中心y, 起始角)
    corners = [
        (hl - corner, -hw + corner, -90.0),
        (hl - corner, hw - corner, 0.0),
        (-hl + corner, hw - corner, 90.0),
        (-hl + corner, -hw + corner, 180.0),
    ]
    for (cx, cy, a0) in corners:
        if corner_mode == 'chamfer':
            # 倒角: 用两个端点替代圆弧
            a = math.radians(a0)
            b = math.radians(a0 + 90.0)
            pts.append((cx + corner * math.cos(a), cy + corner * math.sin(a)))
            pts.append((cx + corner * math.cos(b), cy + corner * math.sin(b)))
        else:
            n = max(2, int(segments))
            for k in range(n + 1):
                a = math.radians(a0 + 90.0 * k / n)
                pts.append((cx + corner * math.cos(a), cy + corner * math.sin(a)))
    return pts


def profile_circle(radius: float, segments: int = 48) -> List[Tuple[float, float]]:
    n = max(8, int(segments))
    return [(radius * math.cos(2 * math.pi * i / n),
             radius * math.sin(2 * math.pi * i / n)) for i in range(n)]


def profile_polygon(sides: int, radius: float, rotation: float = 0.0) -> List[Tuple[float, float]]:
    n = max(3, int(sides))
    return [(radius * math.cos(2 * math.pi * i / n + rotation),
             radius * math.sin(2 * math.pi * i / n + rotation)) for i in range(n)]


def profile_scale(profile, sx: float, sy: float = None):
    sy = sx if sy is None else sy
    return [(x * sx, y * sy) for (x, y) in profile]


def _signed_area(pts) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _point_in_triangle(px, py, ax, ay, bx, by, cx, cy) -> bool:
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def triangulate_2d(pts: Sequence[Tuple[float, float]]) -> List[Tuple[int, int, int]]:
    """耳切法三角化简单多边形，返回顶点索引三元组。"""
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    idx = list(range(n))
    if _signed_area(pts) < 0:
        idx.reverse()
    tris = []
    guard = 0
    while len(idx) > 3 and guard < n * n + 64:
        guard += 1
        ear_found = False
        m = len(idx)
        for i in range(m):
            i0, i1, i2 = idx[(i - 1) % m], idx[i], idx[(i + 1) % m]
            ax, ay = pts[i0]
            bx, by = pts[i1]
            cx, cy = pts[i2]
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if cross <= 1e-12:
                continue
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                px, py = pts[j]
                if _point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
                    ok = False
                    break
            if ok:
                tris.append((i0, i1, i2))
                idx.pop(i)
                ear_found = True
                break
        if not ear_found:
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    if not tris:  # 退化兜底: 扇形
        tris = [(0, i, i + 1) for i in range(1, n - 1)]
    return tris


# --------------------------------------------------------------------------
# 3D 基础特征
# --------------------------------------------------------------------------


def _axis_basis(axis: str):
    """返回 (u, v, w) —— w 为拉伸/轴方向。"""
    a = (axis or 'Z').upper()
    if a == 'X':
        return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    if a == 'Y':
        return (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def extrude_profile(profile: Sequence[Tuple[float, float]], height: float,
                    center: Vec3, axis: str = 'Z', tag: int = 0,
                    symmetric: bool = False, z0: float = 0.0) -> List[Polygon]:
    """把 2D 轮廓沿 axis 拉伸 height, 轮廓平面过 center。

    symmetric=True 时关于 center 对称拉伸; 否则从 z0 起向正方向拉伸。
    """
    u, v, w = _axis_basis(axis)
    cx, cy, cz = center
    if symmetric:
        h0, h1 = -height / 2.0, height / 2.0
    else:
        h0, h1 = z0, z0 + height
    origin = (cx, cy, cz)

    def P(a: float, b: float, t: float) -> Vec3:
        return (origin[0] + u[0] * a + v[0] * b + w[0] * t,
                origin[1] + u[1] * a + v[1] * b + w[1] * t,
                origin[2] + u[2] * a + v[2] * b + w[2] * t)

    n = len(profile)
    if n < 3:
        return []
    mid = (h0 + h1) / 2.0
    center3 = P(0.0, 0.0, mid)
    polys: List[Polygon] = []
    # 侧面
    for i in range(n):
        a0 = profile[i]
        a1 = profile[(i + 1) % n]
        quad = [P(a0[0], a0[1], h0), P(a1[0], a1[1], h0),
                P(a1[0], a1[1], h1), P(a0[0], a0[1], h1)]
        pg = make_polygon(quad, tag, center3)
        if pg:
            polys.append(pg)
    # 端盖 (耳切三角化保证凸性)
    area = _signed_area(profile)
    tris = triangulate_2d(profile)
    for (i0, i1, i2) in tris:
        if area >= 0:
            order = (i0, i1, i2)
        else:
            order = (i0, i2, i1)
        top = [P(profile[k][0], profile[k][1], h1) for k in order]
        bot = [P(profile[k][0], profile[k][1], h0) for k in order]
        pg = make_polygon(top, tag, center3)
        if pg:
            polys.append(pg)
        pg = make_polygon(bot, tag, center3)
        if pg:
            polys.append(pg)
    return polys


def make_box(length: float, width: float, height: float, center: Vec3 = (0, 0, 0),
             tag: int = 0, corner: float = 0.0, corner_mode: str = 'fillet',
             axis: str = 'Z') -> List[Polygon]:
    """长方体 (可带竖直棱圆角/倒角)。length->u, width->v, height->w。"""
    prof = profile_rect(length, width, corner, corner_mode)
    return extrude_profile(prof, height, center, axis=axis, tag=tag)


def make_cylinder(radius: float, height: float, center: Vec3 = (0, 0, 0),
                  tag: int = 0, segments: int = 48, axis: str = 'Z',
                  symmetric: bool = False, z0: float = 0.0,
                  radius2: float = None) -> List[Polygon]:
    """圆柱 / 圆锥 / 圆台。radius2 给定时为圆台。"""
    r1 = float(radius)
    r2 = r1 if radius2 is None else float(radius2)
    seg = max(8, int(segments))
    u, v, w = _axis_basis(axis)
    cx, cy, cz = center
    if symmetric:
        h0, h1 = -height / 2.0, height / 2.0
    else:
        h0, h1 = z0, z0 + height

    def P(a: float, b: float, t: float) -> Vec3:
        return (cx + u[0] * a + v[0] * b + w[0] * t,
                cy + u[1] * a + v[1] * b + w[1] * t,
                cz + u[2] * a + v[2] * b + w[2] * t)

    center3 = P(0.0, 0.0, (h0 + h1) / 2.0)
    polys: List[Polygon] = []
    for i in range(seg):
        a0 = 2 * math.pi * i / seg
        a1 = 2 * math.pi * (i + 1) / seg
        p0 = (r1 * math.cos(a0), r1 * math.sin(a0))
        p1 = (r1 * math.cos(a1), r1 * math.sin(a1))
        q0 = (r2 * math.cos(a0), r2 * math.sin(a0))
        q1 = (r2 * math.cos(a1), r2 * math.sin(a1))
        if abs(r1 - r2) < 1e-9:
            quad = [P(p0[0], p0[1], h0), P(p1[0], p1[1], h0),
                    P(q1[0], q1[1], h1), P(q0[0], q0[1], h1)]
            pg = make_polygon(quad, tag, center3)
            if pg:
                polys.append(pg)
        else:
            for tri in ((p0, p1, q1), (p0, q1, q0)):
                pts = [P(a[0], a[1], h0 if k < 2 else h1) for k, a in enumerate(tri)]
                pg = make_polygon(pts, tag, center3)
                if pg:
                    polys.append(pg)
    # 端盖
    for t, r in ((h0, r1), (h1, r2)):
        if r <= 1e-9:
            continue
        pts = [P(r * math.cos(2 * math.pi * i / seg),
                 r * math.sin(2 * math.pi * i / seg), t) for i in range(seg)]
        pg = make_polygon(pts, tag, center3)
        if pg:
            polys.append(pg)
    return polys


def make_sphere(radius: float, center: Vec3 = (0, 0, 0), tag: int = 0,
                segments: int = 32, rings: int = 16) -> List[Polygon]:
    seg = max(8, int(segments))
    rg = max(4, int(rings))
    cx, cy, cz = center
    polys: List[Polygon] = []
    for j in range(rg):
        t0 = math.pi * j / rg
        t1 = math.pi * (j + 1) / rg
        for i in range(seg):
            a0 = 2 * math.pi * i / seg
            a1 = 2 * math.pi * (i + 1) / seg
            def S(t, a):
                return (cx + radius * math.sin(t) * math.cos(a),
                        cy + radius * math.sin(t) * math.sin(a),
                        cz + radius * math.cos(t))
            v00, v01 = S(t0, a0), S(t0, a1)
            v10, v11 = S(t1, a0), S(t1, a1)
            if j == 0:
                pts = [v00, v11, v10]
            elif j == rg - 1:
                pts = [v00, v01, v10]
            else:
                pts = [v00, v01, v11, v10]
            pg = make_polygon(pts, tag, center)
            if pg:
                polys.append(pg)
    return polys


def make_revolve(profile: Sequence[Tuple[float, float]], angle: float = 360.0,
                 center: Vec3 = (0, 0, 0), tag: int = 0, segments: int = 48,
                 axis: str = 'Z') -> List[Polygon]:
    """把 XZ 平面内的闭合轮廓绕 axis 回转 angle 度。profile 的 x 为半径, y 为轴向坐标。"""
    if len(profile) < 3:
        return []
    seg = max(3, int(segments))
    full = abs(angle) >= 359.999
    total = math.radians(angle)
    n = seg if full else seg
    cx, cy, cz = center
    u, v, w = _axis_basis(axis)

    def P(r: float, z: float, a: float) -> Vec3:
        a = a
        return (cx + u[0] * (r * math.cos(a)) + v[0] * (r * math.sin(a)) + w[0] * z,
                cy + u[1] * (r * math.cos(a)) + v[1] * (r * math.sin(a)) + w[1] * z,
                cz + u[2] * (r * math.cos(a)) + v[2] * (r * math.sin(a)) + w[2] * z)

    m = len(profile)
    steps = n if full else n
    polys: List[Polygon] = []
    zs = [p[1] for p in profile]
    zmid = (min(zs) + max(zs)) / 2.0
    rmax = max(p[0] for p in profile)
    center3 = P(rmax * 0.4, zmid, 0.0)
    for i in range(steps):
        a0 = total * i / n
        a1 = total * (i + 1) / n
        for k in range(m):
            r0, z0 = profile[k]
            r1, z1 = profile[(k + 1) % m]
            quad = [P(r0, z0, a0), P(r1, z1, a0), P(r1, z1, a1), P(r0, z0, a1)]
            pts = [quad[0], quad[1], quad[2]] if (abs(r0) < 1e-9 or abs(r1) < 1e-9) else quad
            pg = make_polygon(pts, tag, center3)
            if pg:
                polys.append(pg)
    if not full:
        area = _signed_area(profile)
        tris = triangulate_2d(profile)
        for (i0, i1, i2) in tris:
            order = (i0, i1, i2) if area >= 0 else (i0, i2, i1)
            for a in (0.0, total):
                pts = [P(profile[k][0], profile[k][1], a) for k in order]
                pg = make_polygon(pts, tag, center3)
                if pg:
                    polys.append(pg)
    return polys


def profile_airfoil(chord: float = 100.0, thickness: float = 0.12,
                    camber: float = 0.0, camber_pos: float = 0.4,
                    n: int = 28) -> List[Tuple[float, float]]:
    """NACA 四位翼型轮廓 (闭合点列, 前缘在 x=0, 后缘在 x=chord)。"""
    c = float(chord)
    n = max(8, int(n))
    up, lo = [], []
    for i in range(n + 1):
        beta = math.pi * i / n
        x = 0.5 * (1.0 - math.cos(beta))
        yt = 5.0 * thickness * (0.2969 * math.sqrt(x) - 0.1260 * x
                                - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4)
        if camber:
            m, pp = camber, camber_pos
            if x < pp:
                yc = m / (pp * pp) * (2 * pp * x - x * x)
                dy = 2 * m / (pp * pp) * (pp - x)
            else:
                yc = m / ((1 - pp) ** 2) * ((1 - 2 * pp) + 2 * pp * x - x * x)
                dy = 2 * m / ((1 - pp) ** 2) * (pp - x)
            th = math.atan(dy)
            up.append(((x - yt * math.sin(th)) * c, (yc + yt * math.cos(th)) * c))
            lo.append(((x + yt * math.sin(th)) * c, (yc - yt * math.cos(th)) * c))
        else:
            up.append((x * c, yt * c))
            lo.append((x * c, -yt * c))
    return list(reversed(up)) + lo[1:-1]


def profile_gear(teeth: int, module: float, addendum: float = 1.0,
                 dedendum: float = 1.25) -> List[Tuple[float, float]]:
    """直齿圆柱齿轮的近似轮廓 (梯形齿 + 齿根圆弧)。"""
    z = max(6, int(teeth))
    m = float(module)
    rp = m * z / 2.0
    ra = rp + addendum * m
    rf = max(1.0, rp - dedendum * m)
    half = math.pi / (2.0 * z)
    pts = []
    for i in range(z):
        a0 = 2.0 * math.pi * i / z
        for (da, r) in ((-half * 1.55, rf), (-half * 1.15, rf + 0.15 * m),
                        (-half * 0.95, rp * 0.99), (-half * 0.48, ra),
                        (half * 0.48, ra), (half * 0.95, rp * 0.99),
                        (half * 1.15, rf + 0.15 * m), (half * 1.55, rf)):
            a = a0 + da
            pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def make_loft(sections, tag: int = 0) -> List[Polygon]:
    """由若干等点数截面放样成实体。sections: [[Vec3, ...], ...]"""
    if not sections or len(sections) < 2:
        return []
    n = len(sections[0])
    if n < 3:
        return []
    allpts = [p for sec in sections for p in sec]
    cx = sum(p[0] for p in allpts) / len(allpts)
    cy = sum(p[1] for p in allpts) / len(allpts)
    cz = sum(p[2] for p in allpts) / len(allpts)
    center3 = (cx, cy, cz)
    polys: List[Polygon] = []
    for s in range(len(sections) - 1):
        A, B = sections[s], sections[s + 1]
        for i in range(n):
            j = (i + 1) % n
            quad = [A[i], A[j], B[j], B[i]]
            if K_almost_same(A[i], A[j]):
                continue
            if K_almost_same(B[i], B[j]):
                continue
            pg = make_polygon(quad, tag, center3)
            if pg:
                polys.append(pg)
    # 端盖 (从形心扇形三角化)
    for idx, sec in ((0, sections[0]), (-1, sections[-1])):
        cxx = sum(p[0] for p in sec) / n
        cyy = sum(p[1] for p in sec) / n
        czz = sum(p[2] for p in sec) / n
        cen = (cxx, cyy, czz)
        for i in range(n):
            j = (i + 1) % n
            pg = make_polygon([cen, sec[i], sec[j]], tag, center3)
            if pg:
                polys.append(pg)
    return polys


def K_almost_same(a: Vec3, b: Vec3, tol: float = 1e-9) -> bool:
    return (abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol and abs(a[2] - b[2]) < tol)


# --------------------------------------------------------------------------
# 中等难度特征: 螺旋 / 花键 / 凸轮 / 辐条轮 / 多槽带轮
# --------------------------------------------------------------------------


def make_helix(radius: float, wire_r: float, height: float, turns: float,
               center: Vec3 = (0, 0, 0), tag: int = 0, seg_per_turn: int = 14,
               axis: str = 'Z', taper: float = 0.0,
               wire_seg: int = 8, taper_start: float = 0.0) -> List[Polygon]:
    """把圆形截面沿螺旋线扫掠成弹簧 (radius 螺旋半径, wire_r 线半径, taper 收缩率)。"""
    seg = max(6, int(round(max(0.25, turns) * seg_per_turn)))
    u, v, w = _axis_basis(axis)
    cx, cy, cz = center
    prof = [(wire_r * math.cos(2 * math.pi * k / wire_seg),
             wire_r * math.sin(2 * math.pi * k / wire_seg)) for k in range(wire_seg)]
    sections = []
    for i in range(seg + 1):
        t = i / float(seg)
        a = 2 * math.pi * turns * t
        # taper_start: 前这一段保持圆柱, 之后才开始收缩。
        # 锥形弹簧总要有一段圆柱起始圈, 否则"最大外径"只在某一个方位出现,
        # 转 90 度方向量出来就小了几个百分点。
        ts = max(0.0, min(0.9, float(taper_start)))
        te = (t - ts) / (1.0 - ts) if t > ts else 0.0
        r = radius * (1.0 - taper * te)
        z = height * t
        nx, ny = math.cos(a), math.sin(a)
        ox = cx + u[0] * r * nx + v[0] * r * ny + w[0] * z
        oy = cy + u[1] * r * nx + v[1] * r * ny + w[1] * z
        oz = cz + u[2] * r * nx + v[2] * r * ny + w[2] * z
        sec = []
        for (px, py) in prof:
            sec.append((ox + u[0] * px * nx + v[0] * px * ny + w[0] * py,
                        oy + u[1] * px * nx + v[1] * px * ny + w[1] * py,
                        oz + u[2] * px * nx + v[2] * px * ny + w[2] * py))
        sections.append(sec)
    return make_loft(sections, tag)


def make_spoked_wheel(outer_r: float, rim_w: float, hub_r: float, height: float,
                      spokes: int = 5, spoke_w: float = None,
                      center: Vec3 = (0, 0, 0), tag: int = 0,
                      seg: int = 48) -> List[Polygon]:
    """辐条轮 (手轮 / 方向盘 / 辐板轮盘): 外圈 + 轮毂 + N 根辐条。"""
    h = float(height)
    spoke_w = float(spoke_w) if spoke_w else max(4.0, (outer_r - hub_r) * 0.22)
    # 外圈用一次回转做出来 (剖面: 矩形减去内孔) —— 比布尔差集快得多
    ri = max(1.0, outer_r - rim_w)
    ring_prof = [(ri, -h / 2.0), (outer_r, -h / 2.0),
                 (outer_r, h / 2.0), (ri, h / 2.0)]
    polys = list(make_revolve(ring_prof, 360.0, center, tag, seg, 'Z'))
    # 轮毂也是回转体
    hub_prof = [(max(1.0, hub_r * 0.35), -h / 2.0), (hub_r, -h / 2.0),
                (hub_r, h / 2.0), (max(1.0, hub_r * 0.35), h / 2.0)]
    polys.extend(make_revolve(hub_prof, 360.0, center, tag, max(24, seg // 2), 'Z'))
    # 辐条: 彼此不相交, 直接网格组合即可, 无需布尔并集 (快几十倍)
    n = max(2, int(spokes))
    mid = (outer_r + hub_r) / 2.0
    for i in range(n):
        m = mat_rot('Z', 360.0 * i / n)
        # 注意: make_box 在 Z 方向是从 center 沿 +Z 长出 height (不是居中),
        # 所以这里要把中心压到 -h/2, 否则辐条会比外圈高出一个厚度。
        cub = make_box(outer_r - hub_r, spoke_w, h, (mid, 0.0, -h / 2.0), tag)
        for p in cub:
            verts = []
            for (pos, nor) in p.vertices:
                verts.append((v_add(mat_apply(m, pos), center),
                              v_norm(mat_apply(m, nor))))
            nrm = v_norm(mat_apply(m, p.plane.normal))
            polys.append(Polygon(verts, Plane(nrm, v_dot(nrm, verts[0][0])), p.tag))
    return polys


def profile_spline(bore_r: float, tooth_h: float, teeth: int = 8,
                   tooth_w: float = 0.5) -> List[Tuple[float, float]]:
    """矩形花键的 2D 截面轮廓 (轴伸用)。

    两处讲究:
      · 齿顶做成以 ro 为半径的**圆弧**, 而不是一根弦 —— 弦的中点在内侧,
        "跨齿测量"会比标称大径小一圈;
      · 整体相位转 step/4。齿数不能被 4 整除时 (例如 6 齿), 齿顶不可能同时
        对准 X 轴和 Y 轴, 不调相位的话包围盒会比大径小 4% 以上。
        转 step/4 后齿顶落在两个轴附近, 包围盒才等于大径。
    """
    z = max(4, int(teeth))
    ro = bore_r + tooth_h
    step = 2 * math.pi / z
    half = step * tooth_w / 2.0
    tip = half * 0.85
    phase = step / 4.0
    pts = []
    for i in range(z):
        a0 = i * step + phase
        # 齿槽底 (两齿之间)
        pts.append((bore_r * math.cos(a0 - step / 2 + 1e-4),
                    bore_r * math.sin(a0 - step / 2 + 1e-4)))
        pts.append((bore_r * math.cos(a0 - half), bore_r * math.sin(a0 - half)))
        # 齿侧上升到齿顶
        pts.append((ro * math.cos(a0 - tip), ro * math.sin(a0 - tip)))
        # 齿顶圆弧
        for k in range(1, 3):
            aa = a0 - tip + 2.0 * tip * k / 3.0
            pts.append((ro * math.cos(aa), ro * math.sin(aa)))
        pts.append((ro * math.cos(a0 + tip), ro * math.sin(a0 + tip)))
        # 齿侧下降
        pts.append((bore_r * math.cos(a0 + half), bore_r * math.sin(a0 + half)))
    return pts


def profile_cam(base_r: float, lift: float, angle: float = 120.0,
                n: int = 72) -> List[Tuple[float, float]]:
    """盘形凸轮 2D 轮廓: 基圆 + 简谐上升 + 顶部保持 + 简谐下降。"""
    ang = max(10.0, min(350.0, float(angle)))
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        deg = math.degrees(a)
        if deg < ang * 0.6:
            t = deg / max(1e-6, ang * 0.6)
            r = base_r + lift * 0.5 * (1 - math.cos(math.pi * t))
        elif deg < ang:
            r = base_r + lift
        else:
            t = (deg - ang) / max(1e-6, 360.0 - ang)
            r = base_r + lift * 0.5 * (1 + math.cos(math.pi * t))
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def make_vbelt_pulley(pitch_d: float, grooves: int, height: float,
                      center: Vec3 = (0, 0, 0), tag: int = 0,
                      groove_depth: float = None, seg: int = 48,
                      bore_d: float = 0.0, groove_type: str = None,
                      standard: bool = True) -> List[Polygon]:
    """多槽 V 带轮（按国家标准轮槽剖面生成真正的 V 形槽）。

    标准路径（默认）：轮槽尺寸、槽角、外径、轮缘宽度全部来自
    GB/T 10412—2002 / GB/T 10413—2002（见 ai_modeler/std_pulley.py 与 kb/pulley_std.json）：
      · 基准线在基准直径 dd 的圆柱面上，槽在基准线上下分别深 ha / hf；
      · 外径 da = dd + 2·ha，因此基准线低于轮缘外圆 ha；
      · 轮缘宽度不小于 (n-1)·e + 2f（e 为槽间距、f 为槽顶半宽）；
      · 带轮整体（轮缘 + 辐板 + 轮毂 + 内孔）一次回转成形，避免布尔运算爆炸。
    `standard=False` 时退回旧的"圆柱上开 V 槽"近似（仅兼容旧调用）。
    """
    h = float(height)
    if standard:
        spec = std_pulley.spec(pitch_d, grooves, groove_type, h, bore_d,
                               keep_width=bool(h))
        prof = std_pulley.profile(spec)
        polys = list(make_revolve(prof, 360.0, center, tag, seg, 'Z'))
        # 大带轮加辐条（辐条彼此不相交，直接网格组合，比布尔并集快得多）
        if spec.get("spokes"):
            r_hub = spec["hub_d"] / 2.0
            r_rim = spec["outer_d"] / 2.0
            mid = (r_hub + r_rim) / 2.0
            n = int(spec["spokes"])
            sw = max(6.0, (r_rim - r_hub) * 0.28)
            for i in range(n):
                m = mat_rot('Z', 360.0 * i / n)
                for p in make_box(r_rim - r_hub, sw, spec["web_s"],
                                  (mid, 0.0, spec["web_z"]), tag):
                    verts = []
                    for (pos, nor) in p.vertices:
                        verts.append((v_add(mat_apply(m, pos), center),
                                      v_norm(mat_apply(m, nor))))
                    nrm = v_norm(mat_apply(m, p.plane.normal))
                    polys.append(Polygon(verts, Plane(nrm, v_dot(nrm, verts[0][0])), p.tag))
        return polys

    r = pitch_d / 2.0
    gd = float(groove_depth) if groove_depth else max(3.0, r * 0.16)
    n = max(1, int(grooves))
    rb = max(1.0, bore_d / 2.0) if bore_d else r * 0.28
    raw: List[Tuple[float, float]] = [(rb, -h / 2.0), (r, -h / 2.0)]
    # 侧面从下往上走, 遇到槽就切出 V 形 (夹紧到端面内, 避免槽尖戳出轮宽)
    for i in range(n):
        zc = -h / 2.0 + h * (i + 1) / (n + 1)
        raw.append((r, min(h / 2.0, max(-h / 2.0, zc - gd * 0.85))))
        raw.append((r - gd, zc))
        raw.append((r, min(h / 2.0, max(-h / 2.0, zc + gd * 0.85))))
    raw.append((r, h / 2.0))
    raw.append((rb, h / 2.0))
    # 去掉重合点, 否则回转三角化会退化
    prof: List[Tuple[float, float]] = []
    for pt in raw:
        if not prof or abs(pt[0] - prof[-1][0]) > 1e-9 or abs(pt[1] - prof[-1][1]) > 1e-9:
            prof.append(pt)
    return make_revolve(prof, 360.0, center, tag, seg, 'Z')


def make_timing_pulley(pitch_d: float, teeth: int, height: float,
                       tooth_type: str = 'XL', bore_d: float = 0.0,
                       center: Vec3 = (0, 0, 0), tag: int = 0,
                       seg: int = 64) -> List[Polygon]:
    """同步带轮：按标准节距生成**角向**轮齿（齿高/齿顶宽/节距见 std_pulley.py）。

    ⚠️ 这里踩过一个很隐蔽的坑（U7 评测抓出来的）：
       早先把齿形当成**轴向剖面**去回转（像 V 带轮的槽那样），但齿是**角向**特征 ——
       齿沿圆周分布，不是沿轴向排列。那段剖面沿轴向就跨了 z·p（20 齿 × 5.08 ≈ 101mm），
       回转 360° 时自身缠绕，实测轴向尺寸虚高 3.3 倍（要 30，得 99）。
    正确做法：
       ① 盘体用回转体（(半径, 轴向) 剖面，含内孔）；
       ② 每个齿做一个**平面凸多边形**放在 Z=0 平面，再沿 +Z 拉伸成棱柱；
       ③ 齿绕轴心按 360°/z 均布 —— 齿距因此严格等于标准节距 p = π·d_pitch/z。
    凸多边形按逆时针给出，外法向由叉积保证朝外（三角化对缠绕方向敏感）。
    """
    info = std_pulley.timing_pitch(tooth_type)
    p = float(info["pitch"])
    z = max(6, int(teeth))
    h = float(height)
    r_pitch = max(2.0, pitch_d / 2.0)
    r_root = r_pitch - info["tooth_h"] * 0.55      # 齿根圆（轮齿之间）
    r_tip = r_pitch + info["tooth_h"] * 0.45       # 齿顶圆
    r_in = max(1.0, bore_d / 2.0) if bore_d else max(2.0, r_root * 0.35)
    half_tip = info["tooth_tip"] / 2.0
    half_root = max(half_tip + 0.2, p / 2.0 - half_tip)
    cx, cy, cz = center

    # ---- 齿距自洽性检查（U3：几何必须自洽，不能悄悄建出"齿比齿距还宽"的轮子）
    # 齿的切向宽度是**常数**（标准梯形齿，= 齿顶宽），而齿距 p = π·dd/z 随直径变化。
    # 当 dd 与 z 不相配时会出现 half_root < half_tip —— 也就是"齿比齿距还宽"，
    # 相邻齿必然互相重叠。这种网格里齿形是错的，任何"齿数测量"都不可能读对
    # （实测：10 齿建出 20 个径向凸起、自相关估出 3/4/8/12 这些谐波值），
    # 所以必须**明确告知用户输入自相矛盾**，并给出这个齿数所需的最小节圆直径。
    pitch_conflict = None
    if p / 2.0 - half_tip < half_tip:
        min_dd = z * info["tooth_tip"] / math.pi
        pitch_conflict = (
            "%d 齿 %s 型同步带轮的节距 p=π·dd/z=%.2f mm 小于齿顶宽 %.2f mm，"
            "相邻齿会互相重叠（几何自相矛盾）。按 %d 齿算，节圆直径至少需 ⌀%.2f mm；"
            "或改用 %d 齿配 ⌀%g mm。"
            % (z, str(tooth_type or 'XL').upper(), p, info["tooth_tip"],
               z, min_dd, max(6, int(round(math.pi * pitch_d / info["tooth_tip"]))),
               pitch_d))

    # ---- ① 盘体：轴向剖面回转（首尾都在轴上 -> 实心盘 + 内孔）
    # 剖面的轴向值本身就是 ±h/2，回转中心就是零件中心，**不要再减 h/2**（否则整体错位半个高度）
    body = make_revolve([(r_in, -h / 2.0), (r_root, -h / 2.0),
                         (r_root, h / 2.0), (r_in, h / 2.0)],
                        360.0, (cx, cy, cz), tag, seg, 'Z')

    # ---- ②③ 轮齿：2D 多边形沿 Z 拉伸（make_twisted_prism，扭转角 0），绕轴均布
    polys = list(body)
    step = 360.0 / z
    for i in range(z):
        local = [(r_pitch - 1.0, -half_root), (r_root, -half_root),
                 (r_tip, -half_tip), (r_tip, half_tip),
                 (r_root, half_root), (r_pitch - 1.0, half_root)]
        m = mat_rot('Z', step * i)
        prof2 = []
        for (r, t) in local:                     # (半径, 切向) -> 平面点
            wx, wy, _ = mat_apply(m, (r, t, 0.0))
            prof2.append((wx, wy))
        tooth = make_twisted_prism(prof2, h, 0.0, (cx, cy, cz), tag, 1, 'Z')
        polys.extend(tooth if isinstance(tooth, list) else [tooth])
    if pitch_conflict:
        # 把矛盾挂在网格上，导出/自检层会原样报给用户（不静默）
        try:
            setattr(polys, "warnings", [pitch_conflict])
        except Exception:
            pass
        try:
            for _p in polys:
                _p.pitch_warning = pitch_conflict
        except Exception:
            pass
    return polys


def mat_rot(axis: str, deg: float):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    a = (axis or 'Z').upper()
    if a == 'X':
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if a == 'Y':
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def mat_apply(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2])


# --------------------------------------------------------------------------
# 统计信息
# --------------------------------------------------------------------------


def bbox(polys: Sequence[Polygon]):
    if not polys:
        return None
    xs, ys, zs = [], [], []
    for p in polys:
        for (pos, _n) in p.vertices:
            xs.append(pos[0])
            ys.append(pos[1])
            zs.append(pos[2])
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


# ==========================================================================
# 高难度 / 极高难度零件图元
#   (汽车制造: 斜齿轮 / 锥齿轮 / 曲轴 / 凸轮轴 / 叶轮 / 活塞 / 连杆 /
#              万向节十字轴 / 涡旋盘 / 转子发动机转子)
# 这些零件的共同点是"不能再靠一个圆柱+几个孔糊弄", 必须有真正的特征形状。
# 一律采用**网格组合**而不是布尔并集 —— 布尔并集在这么多子形体上会平方级爆炸。
# ==========================================================================

def make_twisted_prism(profile, height, twist_deg=0.0, center: Vec3 = (0, 0, 0),
                       tag: int = 0, segments: int = 20, axis: str = 'Z',
                       scale_top: float = 1.0, scale_bottom: float = 1.0,
                       offset_top: Tuple[float, float] = (0.0, 0.0)) -> List[Polygon]:
    """沿轴拉伸、同时逐渐扭转 (并可线性缩放/平移) 的棱柱。

    斜齿轮的螺旋齿、叶轮的扭曲叶片都靠它。剖面在拉伸方向上居中。
    """
    u, v, w = _axis_basis(axis)
    cx, cy, cz = center
    h = float(height)
    seg = max(2, int(segments))
    base = list(profile)
    if len(base) > 1 and abs(base[0][0] - base[-1][0]) < 1e-12 \
            and abs(base[0][1] - base[-1][1]) < 1e-12:
        base = base[:-1]            # 去掉重复的闭合点, 否则侧壁会多一条零面积带
    secs = []
    for k in range(seg + 1):
        t = k / float(seg)
        ang = math.radians(twist_deg * t)
        s = scale_bottom + (scale_top - scale_bottom) * t
        ca, sa = math.cos(ang), math.sin(ang)
        ox = offset_top[0] * t
        oy = offset_top[1] * t
        z = -h / 2.0 + h * t
        sec = []
        for (px, py) in base:
            qx = (px * ca - py * sa) * s + ox
            qy = (px * sa + py * ca) * s + oy
            sec.append((cx + u[0] * qx + v[0] * qy + w[0] * z,
                        cy + u[1] * qx + v[1] * qy + w[1] * z,
                        cz + u[2] * qx + v[2] * qy + w[2] * z))
        secs.append(sec)
    return make_loft(secs, tag)


def _tri_poly(a, b, c, tag: int = 0, flip: bool = False) -> Optional[Polygon]:
    """由三个顶点造一个三角面片, 法向自动算 (退化三角形返回 None)。"""
    if flip:
        b, c = c, b
    n = v_cross(v_sub(b, a), v_sub(c, a))
    ln = v_len(n)
    if ln < 1e-12:
        return None
    n = (n[0] / ln, n[1] / ln, n[2] / ln)
    return Polygon([(a, n), (b, n), (c, n)], Plane(n, v_dot(n, a)), tag)


def _annulus_cap(ring_a, ring_b, place, z, tag: int = 0, flip: bool = False):
    """两个同心环之间的圆环端面 (双指针扫掠三角化)。

    两个环都相对中心星形, 所以按极角归并扫一遍就能得到正确三角剖分;
    外环的尖角顶点原样保留, 不会被"削圆"。
    """
    def key(p):
        a = math.atan2(p[1], p[0])
        return a if a >= 0 else a + 2.0 * math.pi

    A = sorted(ring_a, key=key)
    B = sorted(ring_b, key=key)
    n, m = len(A), len(B)
    out: List[Polygon] = []
    pa, pb = A[0], B[0]
    i = j = 0
    guard = 0
    while (i < n or j < m) and guard <= (n + m) * 2 + 4:
        guard += 1
        na = key(A[(i + 1) % n]) + (2.0 * math.pi if (i + 1) >= n else 0.0)
        nb = key(B[(j + 1) % m]) + (2.0 * math.pi if (j + 1) >= m else 0.0)
        if i < n and (j >= m or na <= nb):
            i += 1
            pn = A[i % n]
            t = _tri_poly(place(pb[0], pb[1], z), place(pn[0], pn[1], z),
                          place(pa[0], pa[1], z), tag, flip)
            pa = pn
        else:
            j += 1
            pn = B[j % m]
            t = _tri_poly(place(pa[0], pa[1], z), place(pb[0], pb[1], z),
                          place(pn[0], pn[1], z), tag, flip)
            pb = pn
        if t:
            out.append(t)
    return out


def make_twisted_tube(outer, inner, height: float, twist_deg: float = 0.0,
                      center: Vec3 = (0, 0, 0), tag: int = 0, segments: int = 20,
                      axis: str = 'Z', scale_top: float = 1.0,
                      scale_bottom: float = 1.0,
                      offset_top: Tuple[float, float] = (0.0, 0.0)) -> List[Polygon]:
    """带中心通孔、可以边拉伸边扭转的棱柱 —— 斜齿轮 / 锥齿轮的毛坯。

    孔是**直接画进几何**的, 不走布尔差集: 扭转齿廓上全是近似共面的面,
    拿它做 BSP 差集会指数级分裂, 实测直接卡死 (>45 秒)。
    """
    u, v, w = _axis_basis(axis)
    cx, cy, cz = center
    h = float(height)
    seg = max(2, int(segments))

    def clean(pr):
        pr = list(pr)
        if len(pr) > 1 and abs(pr[0][0] - pr[-1][0]) < 1e-12 \
                and abs(pr[0][1] - pr[-1][1]) < 1e-12:
            pr = pr[:-1]
        return pr

    bo, bi = clean(outer), clean(inner)

    def place(px, py, z):
        return (cx + u[0] * px + v[0] * py + w[0] * z,
                cy + u[1] * px + v[1] * py + w[1] * z,
                cz + u[2] * px + v[2] * py + w[2] * z)

    def ring(pr, t):
        ang = math.radians(twist_deg * t)
        s = scale_bottom + (scale_top - scale_bottom) * t
        ca, sa = math.cos(ang), math.sin(ang)
        z = -h / 2.0 + h * t
        ox = offset_top[0] * t
        oy = offset_top[1] * t
        return [((px * ca - py * sa) * s + ox, (px * sa + py * ca) * s + oy, z)
                for (px, py) in pr]

    O = [ring(bo, k / float(seg)) for k in range(seg + 1)]
    I = [ring(bi, k / float(seg)) for k in range(seg + 1)]
    polys: List[Polygon] = []
    # 侧壁: 外壁法向朝外, 内壁法向朝内 (翻转)
    for k in range(seg):
        for arr, flip in ((O, False), (I, True)):
            cur, nxt = arr[k], arr[k + 1]
            L = len(cur)
            for i in range(L):
                j = (i + 1) % L
                for t in (_tri_poly(place(*cur[i]), place(*cur[j]), place(*nxt[j]), tag, flip),
                          _tri_poly(place(*cur[i]), place(*nxt[j]), place(*nxt[i]), tag, flip)):
                    if t:
                        polys.append(t)
    # 上下端面
    for k, flip in ((0, True), (seg, False)):
        polys += _annulus_cap(O[k], I[k], place, O[k][0][2], tag, flip)
    return polys


def make_helical_gear(teeth: int, module: float, face_width: float,
                      helix_deg: float = 18.0, bore_d: float = 0.0,
                      center: Vec3 = (0, 0, 0), tag: int = 0,
                      segments: int = 20) -> List[Polygon]:
    """斜齿圆柱齿轮: 齿廓沿轴向一边拉伸一边扭转 (螺旋角)。"""
    z = max(6, int(teeth))
    m = float(module)
    b = float(face_width)
    rp = max(1e-6, m * z / 2.0)                       # 分度圆半径
    # 螺旋角 -> 整个齿宽上扭转多少度
    twist = math.degrees(b * math.tan(math.radians(max(0.0, min(45.0, helix_deg)))) / rp)
    prof = profile_gear(z, m)
    bore_r = float(bore_d) / 2.0 if bore_d else max(0.5, rp * 0.28)
    bore_r = min(bore_r, rp * 0.80)
    return make_twisted_tube(prof, profile_circle(bore_r, 40), b, twist,
                             center, tag, max(6, min(segments, z)), 'Z')


def make_bevel_gear(teeth: int, module: float, face_width: float,
                    cone_deg: float = 45.0, bore_d: float = 0.0,
                    center: Vec3 = (0, 0, 0), tag: int = 0,
                    segments: int = 12) -> List[Polygon]:
    """直齿锥齿轮 (简化): 齿廓沿节锥收缩拉伸。

    大端分度圆 = m*z, 沿齿宽方向半径按 tan(节锥角) 线性缩小。
    """
    z = max(6, int(teeth))
    m = float(module)
    F = float(face_width)
    R = m * z / 2.0
    shrink = F * math.tan(math.radians(max(5.0, min(75.0, cone_deg)))) / max(1e-6, R)
    scale_top = max(0.3, 1.0 - shrink)
    prof = profile_gear(z, m)
    bore_r = float(bore_d) / 2.0 if bore_d else max(0.5, R * 0.28)
    bore_r = min(bore_r, R * 0.70)
    return make_twisted_tube(prof, profile_circle(bore_r, 40), F, 0.0,
                             center, tag, max(4, min(segments, z // 2)), 'Z',
                             scale_top=scale_top)


def crankshaft_size(main_d: float, pin_d: float, stroke: float, throws: int,
                    total_len: float = None):
    """曲轴总体尺寸 (轴向长度, 偏心方向半径, 垂直方向半径)。
    建造器和外形算法共用同一份公式。

    ⚠️ 描述里显式给了「总长」时必须照它走: 早先只按 主轴颈/行程/拐数 估算,
    实测「曲轴 … 四拐 总长320」被算成 619mm(基准 B30 就是这么失分的)。
    """
    d = float(main_d)
    dp = float(pin_d)
    S = float(stroke)
    n = max(1, int(throws))
    # 曲柄臂是"主轴圆 + 偏心连杆颈圆的外公切凸包"(哑铃形, 见 make_crankshaft):
    #   · 偏心方向外缘 = 行程/2 + 连杆颈半径 —— 连杆颈外缘正好与臂相切;
    #   · 垂直方向外缘 = 主轴颈半径        —— 臂与主轴颈齐平(锻造曲轴的过渡)。
    # 所以外形**不是各向同性的**: 旧版把臂做成 r=行程/2+连杆颈/2 的整圆盘,
    # 才会得到 128×128, 那样连杆颈被完全包在臂里, 看着像"轴+垫圈"。
    r_off = S / 2.0 + dp / 2.0            # 偏心方向
    r_perp = max(d / 2.0, dp / 2.0)       # 垂直于偏心方向
    if total_len:
        return float(total_len), r_off, r_perp
    spacing = max(S * 1.8, d * 2.2)
    return spacing * n, r_off, r_perp


def conrod_size(length: float, big_d: float, small_d: float):
    """连杆总体尺寸 (轴向总长, 最大外径)。

    取材时输入可能把"小头"写得比"大头"还大, 这时两个方向的包络取的是**大的那个**,
    所以这里返回 max 而不是直接返回 big_d。
    """
    L = float(length)
    r1 = float(big_d) / 2.0
    r2 = float(small_d) / 2.0
    return L + (r1 + r2) * 0.5, max(float(big_d), float(small_d))


def make_hex_bolt(shank_d: float, length: float, across_flats: float,
                  head_h: float, center: Vec3 = (0, 0, 0), tag: int = 0,
                  segments: int = 48) -> List[Polygon]:
    """六角头螺栓: 六棱柱头 + 光杆圆柱, 沿 Z 轴(头在下, 杆往上)。

    几何定义(不能含糊):
      · 「对边 s」= 六边形两**平行面**之间的距离 —— 这是扳手口径, 也是图纸标注值;
      · 因此六边形**外接圆半径** R = s / (2·cos30°) = s / √3 ≈ 0.5774·s
        (外接圆直径 e = 2R ≈ 1.1547·s, GB/T 5782 里叫"对角宽度 e");
      · 头部高度 k, 杆长 L → 总高 L + k; 杆与头同轴。
    实现方式: 头(六棱柱)与杆(圆柱)的网格**直接拼接** —— 两个凸体拼接不需要布尔,
    省掉 CSG 那一步(实测对一个螺栓能省一个数量级的三角化)。
    螺纹不做真实牙型(CATIA 侧用装饰螺纹/注释表达), 这是与用户确认过的口径。
    """
    d = max(0.5, float(shank_d))
    L = max(1.0, float(length))
    s = max(1.0, float(across_flats))
    k = max(0.5, float(head_h))
    R = s / math.sqrt(3.0)                 # 外接圆半径(对角宽度的一半)
    cx, cy, cz = center

    polys: List[Polygon] = []
    # --- 头部: 正六棱柱, 一条对边平行于 X 轴(工程上常见的摆法) ---
    head_prof = []
    for i in range(6):
        a = math.radians(60.0 * i)
        head_prof.append((R * math.cos(a), R * math.sin(a)))
    polys += extrude_profile(head_prof, k, (cx, cy, cz), 'Z', tag)

    # --- 杆: 圆柱, 从头面往上长 ---
    polys += make_cylinder(d / 2.0, L, (cx, cy, cz + k), tag,
                           max(8, int(segments)))
    return polys


def make_socket_head_bolt(shank_d: float, length: float, head_d: float,
                          head_h: float, hex_s: float, hex_t: float,
                          center: Vec3 = (0, 0, 0), tag: int = 0,
                          segments: int = 48) -> List[Polygon]:
    """内六角圆柱头螺钉（ISO 4762 / GB/T 70.1—2008）。

    与 `make_hex_bolt` 的区别（这是"内六角"三个字的全部含义）：
      · 头部是**圆柱**（⌀dk × 高 k），不是六棱柱；
      · 头端面有一个**内六角孔**（对边 s、深 t）—— 用六棱柱布尔减做出来。

    几何口径：
      · 头在 Z=0..k，杆从 Z=k 往上长 L（与 make_hex_bolt 同向，便于拼接装配）；
      · 「对边 s」是六边形两平行面的距离，外接圆半径 R = s/√3；
      · 内六角孔的**对角朝向**与头部圆无关，这里让一条对边平行 X 轴（常见摆法）；
      · 螺纹不做真实牙型（与项目既有口径一致，CATIA 侧用装饰螺纹表达）。
    """
    d = max(0.5, float(shank_d))
    L = max(1.0, float(length))
    dk = max(d + 0.5, float(head_d))
    k = max(0.5, float(head_h))
    s = max(0.5, float(hex_s))
    t = max(0.2, float(hex_t))
    R = s / math.sqrt(3.0)                 # 内六角外接圆半径
    cx, cy, cz = center

    head = make_cylinder(dk / 2.0, k, (cx, cy, cz), tag, max(8, int(segments)))
    # 内六角孔：略放大一点避免与头侧面共面（共面布尔在 BSP 里容易出零面积面）
    prof = []
    for i in range(6):
        a = math.radians(60.0 * i + 30.0)
        prof.append((R * math.cos(a), R * math.sin(a)))
    socket = extrude_profile(prof, t, (cx, cy, cz - 0.01), 'Z', tag)
    try:
        head = csg_subtract(head, socket)
    except Exception:
        pass                                # 布尔失败时退化成无内六角的圆柱头（不静默丢头部）
    shank = make_cylinder(d / 2.0, L, (cx, cy, cz + k), tag,
                          max(8, int(segments)))
    return list(head) + list(shank)


# --------------------------------------------------------------------------
# 曲轴曲柄臂: "主轴圆 + 偏心连杆颈圆的外公切凸包"(哑铃形)
#   网格建模与 CATIA 导出共用这一份轮廓, 免得两边长得不一样。
# --------------------------------------------------------------------------


def crank_cheek_tangents(main_r: float, pin_r: float, dy: float):
    """主轴圆(中心 (0,0)) 与偏心连杆颈圆(中心 (dy,0)) 的两条外公切线的切点。

    外公切线: 直线 {n·x = c} 同时以 r1/r2 切两圆(两圆同侧) =>
    c = r1 + n·C1 = r2 + n·C2 => n·(C2-C1) = r1 - r2, 于是 n_u = (main_r-pin_r)/dy,
    切点 T = C1 + main_r·n, S = C2 + pin_r·n。
    返回 ((T_上, S_上), (T_下, S_下)); 两圆同心/内含时返回 None。
    """
    dyr = float(dy)
    if abs(dyr) < 1e-9:
        return None
    nu = (float(main_r) - float(pin_r)) / dyr
    if abs(nu) >= 1.0:
        return None
    nv = math.sqrt(max(0.0, 1.0 - nu * nu))
    return [((main_r * nu, main_r * sgn * nv),
             (dyr + pin_r * nu, pin_r * sgn * nv)) for sgn in (1.0, -1.0)]


def _ang(cu, cv, p):
    return math.atan2(p[1] - cv, p[0] - cu)


def _arc_through(cu, cv, radius, a_from, a_to, a_mid, segments):
    """从 a_from 转到 a_to 的圆弧采样, 且保证经过 a_mid(转向由它们决定)。"""
    two = 2.0 * math.pi
    sweep = (a_to - a_from) % two
    if (a_mid - a_from) % two > sweep:
        sweep -= two
    cnt = max(2, int(round(float(segments) * abs(sweep) / two)))
    return [(cu + radius * math.cos(a_from + sweep * i / cnt),
             cv + radius * math.sin(a_from + sweep * i / cnt)) for i in range(cnt + 1)]


def crank_cheek_geometry(main_r: float, pin_r: float, dy: float, segments: int = 24):
    """哑铃形曲柄臂的二维要素(平面: u=偏心方向, v=垂直方向), 建模与导出共用。

      outline   外轮廓闭合点列(凸包): 主轴外弧 + 外公切线 + 连杆颈外弧 + 外公切线
      main_far  主轴圆外弧点列 T+ -> T-(走"背向连杆颈"一侧)  = 臂的外壁
      main_near 主轴圆内弧点列 T+ -> T-(走"朝向连杆颈"一侧)  = 端面的内边界
      pin_far   连杆颈圆外弧点列 S- -> S+(走"背向主轴"一侧)  = 臂的外壁
      pin_near  连杆颈圆内弧点列 S+ -> S-(走"朝向主轴"一侧)  = 端面的内边界
    两圆同心/内含时返回 None(调用方退回整圆盘)。
    """
    tg = crank_cheek_tangents(main_r, pin_r, dy)
    if tg is None:
        return None
    (tp, sp), (tm, sm) = tg
    to_pin = 0.0 if dy > 0 else math.pi          # 主轴 -> 连杆颈 的方向角
    away_pin = to_pin + math.pi
    main_far = _arc_through(0.0, 0.0, main_r, _ang(0.0, 0.0, tp), _ang(0.0, 0.0, tm),
                            away_pin, segments)
    main_near = _arc_through(0.0, 0.0, main_r, _ang(0.0, 0.0, tp), _ang(0.0, 0.0, tm),
                             to_pin, segments)
    pin_far = _arc_through(dy, 0.0, pin_r, _ang(dy, 0.0, sm), _ang(dy, 0.0, sp),
                           to_pin, segments)
    pin_near = _arc_through(dy, 0.0, pin_r, _ang(dy, 0.0, sp), _ang(dy, 0.0, sm),
                            away_pin, segments)
    return {'outline': list(main_far) + [sm] + list(pin_far[1:]),
            'main_far': main_far, 'main_near': main_near,
            'pin_far': pin_far, 'pin_near': pin_near,
            'T_up': tp, 'T_dn': tm, 'S_up': sp, 'S_dn': sm}


def _ear_clip_2d(loop):
    """耳切法把简单多边形(点列, 首尾不重复)三角化成三角形点列。

    曲柄臂的两个端面都是**非凸**的月牙形, 网格与导出用的扇形三角化
    (polygons_to_triangles 从 pts[0] 扇形铺)会把它们切错, 所以这里先自己切好。
    """
    pts = list(loop)
    n = len(pts)
    if n < 3:
        return []
    area = 0.0
    for i in range(n):
        u0, v0 = pts[i]
        u1, v1 = pts[(i + 1) % n]
        area += u0 * v1 - u1 * v0
    if area < 0.0:
        pts.reverse()
    idx = list(range(len(pts)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        d1 = cross(a, b, p)
        d2 = cross(b, c, p)
        d3 = cross(c, a, p)
        return (d1 > 1e-12 and d2 > 1e-12 and d3 > 1e-12)

    guard = 0
    while len(idx) > 3 and guard < 4 * len(idx):
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:                 # 凹点/退化
                continue
            if any(inside(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append([a, b, c])
            idx.pop(k)
            break
        else:
            break
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            tris.append([pts[idx[0]], pts[idx[k]], pts[idx[k + 1]]])
    return tris


def make_crankshaft(main_d: float, pin_d: float, stroke: float, throws: int,
                    center: Vec3 = (0, 0, 0), tag: int = 0,
                    segments: int = 32, total_len: float = None) -> List[Polygon]:
    """沿 X 轴构造连续曲轴外壳，不保留零件相交处的内部端面。

    每拐 = 两片**哑铃形曲柄臂**(主轴圆与偏心连杆颈圆的外公切凸包) + 中间的偏心
    连杆颈。臂在偏心方向伸到连杆颈外缘(与连杆颈相切)、垂直方向与主轴颈齐平,
    所以连杆颈是**露在臂外**的 —— 侧视/斜视都能直接看出曲拐。
    (旧版把臂做成与主轴同心的整圆盘 r=行程/2+连杆颈/2, 连杆颈被完全包在里面,
     任何角度都只能看到"一根轴 + 一堆垫圈"。)
    """
    d = float(main_d)
    dp = float(pin_d)
    S = float(stroke)
    n = max(1, int(throws))
    seg = max(8, int(segments))
    main_r = d / 2.0
    pin_r = dp / 2.0
    L, _r_off, _r_perp = crankshaft_size(d, dp, S, n, total_len)
    spacing = L / float(n)
    web_w = min(max(2.0, d * 0.28), spacing * 0.25)
    pin_len = spacing * 0.42
    cx, cy, cz = center
    polys: List[Polygon] = []

    def point(x, y, z, r, angle):
        return (x, y + r * math.cos(angle), z + r * math.sin(angle))

    def cylinder_side(x0, x1, y, z, radius, arc=None):
        """圆柱侧面; arc 给点列(相对 (y,z) 的绝对平面坐标)时只画那条弧。"""
        if x1 - x0 <= 1e-9 or radius <= 0:
            return
        if arc is None:
            pts = [(y + radius * math.cos(2.0 * math.pi * j / seg),
                    z + radius * math.sin(2.0 * math.pi * j / seg)) for j in range(seg + 1)]
        else:
            pts = arc
        mid = ((x0 + x1) / 2.0, y, z)
        for j in range(len(pts) - 1):
            (u0, v0), (u1, v1) = pts[j], pts[j + 1]
            poly = make_polygon([(x0, u0, v0), (x0, u1, v1),
                                 (x1, u1, v1), (x1, u0, v0)], tag, mid)
            if poly:
                polys.append(poly)

    def disc_face(x, radius, normal_sign):
        poly = make_polygon([point(x, cy, cz, radius, 2.0 * math.pi * j / seg)
                             for j in range(seg)], tag)
        if poly:
            if poly.plane.normal[0] * normal_sign < 0:
                poly.flip()
            polys.append(poly)

    def loop_face(x, loop, x_in):
        """x 处的月牙端面(非凸), 用耳切法三角化后逐个铺面。"""
        for tri in _ear_clip_2d(loop):
            poly = make_polygon([(x, cy + p[0], cz + p[1]) for p in tri], tag,
                                (x_in, cy, cz))
            if poly:
                polys.append(poly)

    def cheek(xa, xb, dy, main_at_left):
        """[xa, xb] 之间的一片哑铃形曲柄臂。

        main_at_left=True: xa 侧接主轴颈(该端面 = 凸包 - 主轴圆), xb 侧接连杆颈。
        """
        g = crank_cheek_geometry(main_r, pin_r, dy, seg)
        if g is None:                                   # 退化: 退回整圆盘
            cylinder_side(xa, xb, cy, cz, max(main_r, pin_r))
            return
        tp, tm, sp, sm = g['T_up'], g['T_dn'], g['S_up'], g['S_dn']
        main_far, main_near = g['main_far'], g['main_near']
        pin_far, pin_near = g['pin_far'], g['pin_near']
        # 外壁: 主轴圆外弧 + 连杆颈圆外弧 + 两条外公切线
        cylinder_side(xa, xb, cy, cz, main_r, main_far)
        cylinder_side(xa, xb, cy + dy, cz, pin_r, pin_far)
        mid = (xa, cy + dy / 2.0, cz)
        for (p, q) in ((tp, sp), (tm, sm)):
            poly = make_polygon([(xa, cy + p[0], cz + p[1]), (xa, cy + q[0], cz + q[1]),
                                 (xb, cy + q[0], cz + q[1]), (xb, cy + p[0], cz + p[1])],
                                tag, mid)
            if poly:
                polys.append(poly)
        # 端面: 凸包 - 被接走的那一侧的圆(月牙); 扇形顶点取另一个圆心, 区域对它星形。
        loop_main = [sp] + list(reversed(pin_far))[1:] + [tm] + \
            list(reversed(main_near))[1:]
        loop_pin = [tm] + list(reversed(main_far))[1:] + [sp] + list(pin_near)[1:]
        ends = ((xa, main_at_left, xa + 1.0), (xb, not main_at_left, xb - 1.0))
        for (x, removed_is_main, x_in) in ends:
            loop_face(x, loop_main if removed_is_main else loop_pin, x_in)

    left_end = cx - L / 2.0
    right_end = cx + L / 2.0
    cursor = left_end
    disc_face(left_end, main_r, -1)
    for i in range(n):
        x0 = cx - L / 2.0 + spacing * (i + 0.5)
        dy = (S / 2.0) if (i % 2 == 0) else (-S / 2.0)
        pin_left = x0 - pin_len / 2.0
        pin_right = x0 + pin_len / 2.0
        web_left = pin_left - web_w
        web_right = pin_right + web_w

        cylinder_side(cursor, web_left, cy, cz, main_r)          # 主轴颈
        cheek(web_left, pin_left, dy, True)                      # 左曲柄臂
        cylinder_side(pin_left, pin_right, cy + dy, cz, pin_r)   # 偏心连杆颈
        cheek(pin_right, web_right, dy, False)                   # 右曲柄臂
        cursor = web_right
    cylinder_side(cursor, right_end, cy, cz, main_r)
    disc_face(right_end, main_r, 1)
    return polys


def camshaft_size(base_d: float, lift: float, lobes: int, length: float,
                  lobe_deg: float = 0.0, n_prof: int = 48):
    """凸轮轴外形 (总长, Y 向直径, Z 向直径)。

    凸轮不是轴对称的 —— 轮廓上"最大外径"只出现在升程段对应的那一段角度里,
    所以 Y / Z 两个方向量出来并不相等, 跟凸轮数量和相位都有关。
    这里按建造器同一套公式把各凸轮的轮廓并集算出来, 保证外形算法和实际几何一致。
    """
    base_r = max(1.0, float(base_d) / 2.0)
    lf = max(0.0, float(lift))
    n = max(1, int(lobes))
    prof = profile_cam(base_r, lf, 120.0, n_prof)
    my = mz = 0.0
    for i in range(n):
        a = math.radians(lobe_deg + 360.0 * i / n)
        ca, sa = math.cos(a), math.sin(a)
        for (px, py) in prof:
            my = max(my, abs(px * ca - py * sa))
            mz = max(mz, abs(px * sa + py * ca))
    return float(length), 2.0 * my, 2.0 * mz


def make_camshaft(base_d: float, lift: float, lobes: int, length: float,
                  journal_d: float = None, center: Vec3 = (0, 0, 0), tag: int = 0,
                  segments: int = 24, lobe_deg: float = 0.0) -> List[Polygon]:
    """凸轮轴: 一根轴 + 若干个相位错开的凸轮 (沿 X 轴)。"""
    base_r = max(1.0, float(base_d) / 2.0)
    lf = max(0.0, float(lift))
    n = max(1, int(lobes))
    L = max(2.0, float(length))
    jd = float(journal_d) if journal_d else max(2.0, base_r * 1.1)
    outer_r = base_r + lf
    if jd / 2.0 > outer_r:
        jd = outer_r * 2.0                       # 轴颈不能比凸轮还高
    cx, cy, cz = center
    lobe_w = max(2.0, L / (n * 1.8))
    polys: List[Polygon] = []
    polys += make_cylinder(jd / 2.0, L, (cx - L / 2.0, cy, cz), tag, segments,
                           'X', symmetric=False)
    prof = profile_cam(base_r, lf, 120.0, 48)
    for i in range(n):
        x0 = cx - L / 2.0 + L * (i + 0.5) / n
        m = mat_rot('X', lobe_deg + 360.0 * i / n)
        # 凸轮轮廓在 YZ 平面内, 直接用旋转矩阵摆好再平移
        vs2 = []
        secs = []
        for zz in (-lobe_w / 2.0, lobe_w / 2.0):
            sec = []
            for (px, py) in prof:
                p3 = (0.0, px, py)
                q = mat_apply(m, p3)
                sec.append((x0 + zz + q[0], cy + q[1], cz + q[2]))
            secs.append(sec)
        polys += make_loft(secs, tag)
    return polys


def make_impeller(hub_d: float, tip_d: float, height: float, blades: int,
                  twist_deg: float = 50.0, center: Vec3 = (0, 0, 0), tag: int = 0,
                  segments: int = 12, blade_t: float = None) -> List[Polygon]:
    """涡轮 / 压气机叶轮: 轮毂 + 若干扭曲叶片 (整体叶盘的基础形状)。

    叶片从轮毂根部一路扭到外缘。生成完之后把整组叶片的半径**归一化**到 tip_d/2,
    这样外径就严格等于标称值 —— 扭转会让叶尖的径向投影变来变去, 不归一化尺寸会飘。
    """
    rh = max(1.0, float(hub_d) / 2.0)
    rt = max(rh * 1.05, float(tip_d) / 2.0)
    h = max(1.0, float(height))
    n = max(3, int(blades))
    blade_len = rt - rh
    chord = max(1.0, blade_len * 0.62)
    th = float(blade_t) if blade_t else max(0.6, blade_len * 0.15)
    cx, cy, cz = center
    polys: List[Polygon] = []
    # 轮毂: 下大上小的圆锥台, 轴向占满 [-h/2, h/2]
    polys += make_cylinder(rh, h, (cx, cy, cz - h / 2.0), tag, max(24, segments * 2),
                           'Z', symmetric=False, radius2=rh * 0.55)
    # 后盘: 半径正好等于外径的一半 —— 叶轮的"外径"由盘体严格保证。
    # 否则叶片扭转之后径向投影会飘, 包围盒量出来比标称外径小两三个百分点。
    disc_h = max(0.5, h * 0.28)
    polys += make_revolve([(rh * 0.45, -h / 2.0), (rt, -h / 2.0),
                           (rt, -h / 2.0 + disc_h), (rh * 0.45, -h / 2.0 + disc_h)],
                          360.0, (cx, cy, cz), tag, max(32, segments * 3), 'Z')
    # 叶片截面: (弦向, 轴向), 弦向是切向而不是径向, 免得叶尖戳出去
    prof = [(-chord / 2.0, -h / 2.0), (chord / 2.0, -h / 2.0),
            (chord / 2.0, h / 2.0), (-chord / 2.0, h / 2.0)]
    seg = max(4, int(segments))
    # 先造一遍量出最大半径, 再整体缩放到 rt
    raw = []
    for k in range(seg + 1):
        t = k / float(seg)
        r = rh + blade_len * 1.35 * t
        ang = math.radians(twist_deg * t)
        ca, sa = math.cos(ang), math.sin(ang)
        sec = []
        for (tc, ax) in prof:
            qy = tc * ca - ax * sa
            qz = tc * sa + ax * ca
            sec.append((r, qy, qz))
        raw.append(sec)
    rmax = max(math.hypot(p[0], p[1]) for sec in raw for p in sec) or 1.0
    zmax = max(abs(p[2]) for sec in raw for p in sec) or 1.0
    k_scale = rt / rmax
    k_z = (h / 2.0) / zmax          # 扭转会让叶片顶到轮宽外面, 轴向也归一化一次
    for i in range(n):
        m = mat_rot('Z', 360.0 * i / n)
        secs = []
        for sec in raw:
            out = []
            for (r, qy, qz) in sec:
                q = mat_apply(m, (r * k_scale, qy * k_scale, qz * k_z))
                out.append((cx + q[0], cy + q[1], cz + q[2]))
            secs.append(out)
        polys += make_loft(secs, tag)
    return polys


def make_piston(d: float, height: float, pin_d: float, grooves: int = 3,
                center: Vec3 = (0, 0, 0), tag: int = 0,
                segments: int = 48) -> List[Polygon]:
    """活塞: 回转体 (含环槽) 再横着掏一个活塞销孔。

    环槽直接画进回转剖面里 —— 用布尔去逐个切槽会慢一个数量级。
    """
    rr = max(1.0, float(d) / 2.0)
    h = max(1.0, float(height))
    ng = max(0, int(grooves))
    gd = max(0.4, rr * 0.06)                       # 槽深
    gw = max(0.6, h * 0.035)                       # 槽宽
    z_top = h / 2.0
    z_bot = -h / 2.0
    prof = [(rr * 0.55, z_bot), (rr, z_bot)]
    if ng:
        # 环槽统一排在顶部 34% 的高度带里, 并且只放排得下的 ——
        # 否则"扁而粗"的活塞会把环槽排到底面外面, 总高被撑长。
        band = h * 0.34
        step = band / float(ng)
        for i in range(ng):
            z0 = z_top - step * (i + 0.5)
            lo = z0 - gw
            if z0 >= z_top or lo <= z_bot + gw * 0.5:
                continue
            prof.append((rr, z0))
            prof.append((rr - gd, z0 - gw * 0.15))
            prof.append((rr - gd, z0 - gw * 0.85))
            prof.append((rr, lo))
    prof.append((rr, z_top))
    prof.append((rr * 0.55, z_top))
    polys = make_revolve(prof, 360.0, center, tag, segments, 'Z')
    if pin_d and pin_d > 0:
        # 销孔还要受高度限制: 销孔半径一旦超过半个活塞高, 就会把 ±X 两个方向的
        # 外壁整条切掉, X 向包围盒直接塌下去 (实测能少 3%)。
        pr = min(float(pin_d) / 2.0, rr * 0.75, h * 0.45)
        polys = csg_subtract(polys, make_cylinder(pr, rr * 3.0, center, tag, 32,
                                                  'X', symmetric=True))
    return polys


def make_conrod(length: float, big_d: float, big_bore: float,
                small_d: float, small_bore: float, shank_w: float = None,
                center: Vec3 = (0, 0, 0), tag: int = 0,
                segments: int = 32, thick: float = None) -> List[Polygon]:
    """连杆: 大头 (曲柄销) + 小头 (活塞销) + 中间工字杆, 沿 X 轴。

    大头/小头是**轴线沿 Z 的环**: 外径 D 在 X-Y 平面内, 轴向厚度是 thick。
    (早先是绕 X 轴的回转体, 于是"厚度"被算成了大头外径 —— 实测包围盒 Z
     给出 56 而不是描述里的杆厚 22。)
    大头中心在 x = -L/2, 小头中心在 x = +L/2, 所以总长 = L + (D1+D2)/2。
    """
    L = max(2.0, float(length))
    D1 = max(2.0, float(big_d))
    D2 = max(1.5, float(small_d))
    # 孔径不能大过外径, 否则环被切没了
    b1 = min(max(0.5, float(big_bore)), D1 * 0.90)
    b2 = min(max(0.4, float(small_bore)), D2 * 0.90)
    w = float(shank_w) if shank_w else max(2.0, min(D1, D2) * 0.42)
    t = float(thick) if thick else max(2.0, min(D1, D2) * 0.6)
    half = max(0.4, t / 2.0)
    cx, cy, cz = center
    r1, r2 = D1 / 2.0, D2 / 2.0
    polys: List[Polygon] = []
    # 大头: 轴线沿 Z 的环
    x1 = cx - L / 2.0
    prof1 = [(max(0.4, b1 / 2.0), -half), (r1, -half), (r1, half), (max(0.4, b1 / 2.0), half)]
    for p in make_revolve(prof1, 360.0, (x1, cy, cz), tag, segments, 'Z'):
        polys.append(p)
    # 小头
    x2 = cx + L / 2.0
    prof2 = [(max(0.3, b2 / 2.0), -half), (r2, -half), (r2, half), (max(0.3, b2 / 2.0), half)]
    for p in make_revolve(prof2, 360.0, (x2, cy, cz), tag, segments, 'Z'):
        polys.append(p)
    # 杆身: 一根扁方 (工字梁的简化)
    mid = (x1 + x2) / 2.0
    sw = min(w, D1 * 0.85, D2 * 0.85)      # 杆身不能比大头还宽, 否则包围盒会跑偏
    # make_box 的 X / Y 是**居中**的, 只有 Z 是从 center 往上长 —— 别把 X 也当成基准对齐
    polys += make_box(L, sw, t, (mid, cy, cz - t / 2.0), tag)
    return polys


def make_cross_joint(span: float, journal_d: float, center: Vec3 = (0, 0, 0),
                     tag: int = 0, segments: int = 24) -> List[Polygon]:
    """万向节十字轴: 中心块 + 四个互相垂直的轴颈。"""
    L = max(2.0, float(span))
    d = max(1.0, float(journal_d))
    r = d / 2.0
    cx, cy, cz = center
    hub = min(L * 0.30, d * 0.9)      # 中心块不能比轴颈还粗, 否则包围盒被它撑大
    polys: List[Polygon] = []
    polys += make_box(hub, hub, hub, (cx, cy, cz - hub / 2.0), tag)
    half = (L - hub) / 2.0
    for ax, sgn in (('X', -1), ('X', 1), ('Y', -1), ('Y', 1)):
        if ax == 'X':
            base = (cx + sgn * hub / 2.0 if sgn > 0 else cx - hub / 2.0 - half, cy, cz)
        else:
            base = (cx, cy + sgn * hub / 2.0 if sgn > 0 else cy - hub / 2.0 - half, cz)
        polys += make_cylinder(r, half, base, tag, segments, ax, symmetric=False)
    return polys


def make_scroll_disc(outer_d: float, height: float, wall_t: float,
                     turns: float = 2.5, center: Vec3 = (0, 0, 0), tag: int = 0,
                     segments: int = 120) -> List[Polygon]:
    """涡旋压缩机涡旋盘: 底盘 + 阿基米德螺线涡旋壁。

    底盘半径就是外径的一半 —— 螺线本身不是圆的, 没有底盘的话
    X / Y 两个方向量出来都比标称外径小一大截。
    """
    R = max(2.0, float(outer_d) / 2.0)
    h = max(1.0, float(height))
    wt = max(0.5, float(wall_t))
    tt = max(0.5, float(turns))
    r0 = R * 0.20
    pitch = max(0.5, (R - wt / 2.0 - r0) / tt)
    cx, cy, cz = center
    prof = profile_scroll(r0, pitch, tt, wt, segments)
    polys = make_twisted_prism(prof, h, 0.0, (cx, cy, cz), tag, 2)
    polys += make_cylinder(R, max(0.5, h * 0.35), (cx, cy, cz - h / 2.0), tag,
                           max(48, segments // 2), 'Z', symmetric=False)
    return polys


def profile_scroll(r0: float, pitch: float, turns: float, wall_t: float,
                   n: int = 120) -> List[Tuple[float, float]]:
    """涡旋压缩机动盘的渐开线阿基米德螺线壁 (2D)。"""
    N = max(24, int(n))
    tt = max(0.5, float(turns))
    outer, inner = [], []
    for i in range(N + 1):
        th = 2.0 * math.pi * tt * i / N
        r = r0 + pitch * th / (2.0 * math.pi)
        outer.append(((r + wall_t / 2.0) * math.cos(th), (r + wall_t / 2.0) * math.sin(th)))
        inner.append(((r - wall_t / 2.0) * math.cos(th), (r - wall_t / 2.0) * math.sin(th)))
    return outer + list(reversed(inner))


def profile_rotor(R: float, n: int = 48, flank: float = 0.82) -> List[Tuple[float, float]]:
    """转子发动机的三角转子 (Reuleaux 式圆角三角形)。"""
    pts = []
    verts = [math.radians(90.0 + 120.0 * i) for i in range(3)]
    for i in range(n):
        a = 2.0 * math.pi * i / n
        # 取到三个顶点的最大投影, 做出圆角三角形
        rr = max(f for f in (
            R * (flank + (1.0 - flank) * max(0.0, math.cos(a - v)) ** 6)
            for v in verts))
        pts.append((rr * math.cos(a), rr * math.sin(a)))
    return pts


def volume_of(polys: Sequence[Polygon]) -> float:
    """由散度定理估算封闭网格体积。"""
    vol = 0.0
    for (a, b, c, _t) in polygons_to_triangles(polys):
        vol += v_dot(a, v_cross(b, c)) / 6.0
    return abs(vol)


def surface_area(polys: Sequence[Polygon]) -> float:
    area = 0.0
    for (a, b, c, _t) in polygons_to_triangles(polys):
        area += v_len(v_cross(v_sub(b, a), v_sub(c, a))) / 2.0
    return area
