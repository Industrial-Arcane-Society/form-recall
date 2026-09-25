# -*- coding: utf-8 -*-
r"""
网格切片工具：用平面切三角形网格，得到截面多边形。

为什么需要它（U3 的最后一块）
    之前想"从几何数槽数/齿数"，直接对顶点分箱统计 —— 结果不可靠：
    三角形网格的顶点是稀疏的，一个特征会被拆散（实测 3 槽数成 5、20 齿数成 2）。
    正确做法是按**平面切开网格**拿到真实截面，再在截面上量：
        · 轴向特征（V 槽、环槽）：切 θ = const 的半平面 -> 得到 (r, z) 截面
        · 径向特征（齿、键槽）：切 z = const 的水平面 -> 得到 (x, y) 截面
    这样量出来的是**几何本身**，与顶点密度无关。

实现要点
    · 用 Sutherland–Hodgman 把每个三角面夹到半空间内得凸多边形；
      截面上的**边界边**（恰好被一个被裁面拥有的边）就是截面轮廓。
    · 轮廓不必闭合也能量（本项目大量使用"网格组合"式零件，子实体彼此分离，
      一条闭合折线不成立）；量尺寸时按边集合统计即可。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from ai_modeler.core import kernel as K

Vec3 = Tuple[float, float, float]


# --------------------------------------------------------------------------
# 基础：平面裁剪
# --------------------------------------------------------------------------
def _clip_poly(poly: Sequence[Vec3], n: Vec3, d: float) -> List[Vec3]:
    """把凸多边形裁到半空间 {p·n <= d} 内（Sutherland–Hodgman）。"""
    out: List[Vec3] = []
    m = len(poly)
    if m < 3:
        return out
    for i in range(m):
        a, b = poly[i], poly[(i + 1) % m]
        da = a[0] * n[0] + a[1] * n[1] + a[2] * n[2] - d
        db = b[0] * n[0] + b[1] * n[1] + b[2] * n[2] - d
        if da <= 0:
            out.append(a)
        if (da < 0 < db) or (db < 0 < da):
            t = da / (da - db)
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t,
                        a[2] + (b[2] - a[2]) * t))
    return out


def _key(p: Vec3, q: float = 4):
    return (round(p[0], q), round(p[1], q), round(p[2], q))


# --------------------------------------------------------------------------
# 切片：平面 {p·n = d} 与网格求交
# --------------------------------------------------------------------------
def slice_plane(polys, n: Vec3, d: float) -> List[Tuple[Vec3, Vec3]]:
    """返回截面上的线段集合 [(p1, p2), ...]。

    做法：对每个三角面 (a) 整体裁到 {p·n <= d} 得凸多边形 poly_cut；
    对 poly_cut 的每条边，若它同时**两端都落在平面上**（|·n - d| < eps）
    且它是新产生的边（不是原始三角面的边），就是截面轮廓的一部分。
    更简单可靠的做法：把三角面裁到 {<= d} 与 {>= d} 两侧，
    两侧都被裁过的三角面里，那些"跨越平面"的边就是截面边。
    """
    eps = 1e-7
    segs: List[Tuple[Vec3, Vec3]] = []
    for p in polys:
        verts = [pos for (pos, _n) in p.vertices]
        if len(verts) < 3:
            continue
        tri = []
        for i in range(1, len(verts) - 1):          # 扇形三角化
            tri.append([verts[0], verts[i], verts[i + 1]])
        for t in tri:
            dist = [v[0] * n[0] + v[1] * n[1] + v[2] * n[2] - d for v in t]
            if all(x > eps for x in dist) or all(x < -eps for x in dist):
                continue                            # 完全在一侧，不贡献截面
            pts = []
            for i in range(3):
                a, b = t[i], t[(i + 1) % 3]
                da, db = dist[i], dist[(i + 1) % 3]
                if abs(da) <= eps:
                    pts.append(a)
                if (da < -eps < db) or (db < -eps < da):
                    tt = da / (da - db)
                    pts.append((a[0] + (b[0] - a[0]) * tt,
                                a[1] + (b[1] - a[1]) * tt,
                                a[2] + (b[2] - a[2]) * tt))
            uniq = []
            for q in pts:
                if not any(abs(q[0] - u[0]) < 1e-6 and abs(q[1] - u[1]) < 1e-6
                           and abs(q[2] - u[2]) < 1e-6 for u in uniq):
                    uniq.append(q)
            if len(uniq) >= 2:
                segs.append((uniq[0], uniq[1]))
    return segs


def slice_radial(polys, theta_deg: float = 0.0):
    """轴向特征的切片：过 Z 轴、方位角 θ 的半平面，返回 (r, z) 点集。

    半平面 {p·n = 0}（n = (-sinθ, cosθ, 0)）在 θ 与 θ+180 两侧各有一片，
    这里把两侧的点都折到 r ≥ 0，得到完整剖面的 (r, z)。
    """
    th = math.radians(theta_deg)
    n = (-math.sin(th), math.cos(th), 0.0)
    pts = []
    for (a, b) in slice_plane(polys, n, 0.0):
        for q in (a, b):
            r = math.hypot(q[0], q[1])
            pts.append((r, q[2]))
    return pts


def slice_horizontal(polys, z: float):
    """径向特征的切片：z = const 的水平面，返回 (x, y) 点集（半径可自行取模）。"""
    pts = []
    for (a, b) in slice_plane(polys, (0.0, 0.0, 1.0), z):
        for q in (a, b):
            pts.append((q[0], q[1]))
    return pts


def section_area(polys, n: Vec3, d: float) -> Optional[float]:
    """截面面积（用鞋带公式近似；把截面投影到与 n 垂直的平面）。

    用途：快速校验切片是否正确（例如圆柱的水平截面面积应等于 πr²）。
    """
    segs = slice_plane(polys, n, d)
    if not segs:
        return None
    # 以质心为原点，把线段端点按角度排序后算多边形面积
    cx = sum(p[0] for s in segs for p in s) / (2 * len(segs))
    cy = sum(p[1] for s in segs for p in s) / (2 * len(segs))
    cz = sum(p[2] for s in segs for p in s) / (2 * len(segs))
    if abs(n[2]) > 0.9:
        ax, ay = 0, 1
    elif abs(n[0]) > 0.9:
        ax, ay = 1, 2
    else:
        ax, ay = 0, 2
    pts = []
    for (a, b) in segs:
        for q in (a, b):
            pts.append((q[ax] - (cx, cy, cz)[ax], q[ay] - (cx, cy, cz)[ay]))
    if len(pts) < 3:
        return None
    pts.sort(key=lambda p: math.atan2(p[1], p[0]))
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# --------------------------------------------------------------------------
# 在截面上量特征
# --------------------------------------------------------------------------
def radial_profile_z(polys, theta_deg: float = 0.0, nbin: int = 240,
                     drop_bore: bool = True):
    """在 θ 方向的剖面里，按轴向分箱取**最大半径**（外轮廓）。

    `drop_bore=True` 时不看半径明显偏小的样本（内孔/轮毂），
    否则它们在剖面里会形成一串"低半径段"，把槽数数错。
    `theta_deg=None` 时自动挑一个**最靠"凹槽中心"**的方位角
    （在若干候选角里取"半径最小值最小"的那个）：因为回转体是分面近似的，
    切在轮齿中间与切在凹槽中心得到的剖面不同，选错角度会漏掉特征。
    """
    if theta_deg is None:
        best, best_dip = 0.0, None
        for th in range(0, 360, 3):
            q = slice_radial(polys, th)
            if not q:
                continue
            rmax = max(r for r, _z in q)
            dip = rmax - min(r for r, _z in q)
            if best_dip is None or dip > best_dip:
                best, best_dip = float(th), dip
        theta_deg = best
    pts = slice_radial(polys, theta_deg)
    if not pts:
        return None
    zs = [z for _r, z in pts]
    z0, z1 = min(zs), max(zs)
    if z1 - z0 < 1e-9:
        return None
    prof = [-1.0] * nbin
    for (r, z) in pts:
        bi = min(nbin - 1, int(nbin * (z - z0) / (z1 - z0)))
        if r > prof[bi]:
            prof[bi] = r
    if drop_bore:
        rmax = max(v for v in prof if v >= 0)
        for i, v in enumerate(prof):
            if 0 <= v < rmax * 0.75:
                prof[i] = rmax          # 内孔/轮毂按下限处理
    # 缺箱用邻居补（切片点也可能稀疏）
    known = [i for i, v in enumerate(prof) if v >= 0]
    if len(known) < 6:
        return None
    for i in range(nbin):
        if prof[i] < 0:
            near = min(known, key=lambda k: abs(k - i))
            prof[i] = prof[near]
    return z0, z1, prof


def count_dips(prof, rel: float = 0.03, min_width: int = 2,
               drop_edges: bool = True) -> int:
    """数剖面里"低于峰值 rel"的连续凹陷段数量（V 槽/环槽就是这个形状）。

    `drop_edges=True` 会**丢掉触及剖面首尾的凹陷段**：轮廓两端的切面本来就是敞开的，
    那一小段"低半径"是边界伪影，不是槽（实测 3 个槽会被数成 4）。
    """
    if not prof:
        return 0
    peak = max(prof)
    thr = peak * (1.0 - rel)
    below = [v < thr for v in prof]
    n = len(below)
    segs = []
    i = 0
    while i < n:
        if below[i]:
            j = i
            while j < n and below[j]:
                j += 1
            segs.append((i, j))
            i = j
        else:
            i += 1
    count = 0
    for (a, b) in segs:
        if drop_edges and (a == 0 or b == n):
            continue
        if b - a >= min_width:
            count += 1
    return count


def best_tooth_layer(polys):
    """挑一个**齿所在**的轴向层（半径波动最大的那一层）。

    切片必须在"有齿的那一层"做：切在轮毂/辐板层只能看到圆，数不出齿。
    """
    vs = []
    for p in polys:
        for (pos, _n) in p.vertices:
            vs.append(pos)
    if not vs:
        return 0.0
    z0 = min(v[2] for v in vs)
    z1 = max(v[2] for v in vs)
    best_z, best_span = (z0 + z1) / 2.0, -1.0
    for k in range(1, 20):
        z = z0 + (z1 - z0) * k / 20.0
        pts = slice_horizontal(polys, z)
        if len(pts) < 8:
            continue
        rs = [math.hypot(x, y) for (x, y) in pts]
        span = max(rs) - min(rs)
        if span > best_span:
            best_z, best_span = z, span
    return best_z


def angular_max_radius(polys, z: float, nbin: int = 720):
    """在 z 高度处按角度取最大半径（径向特征：齿顶/齿根）。"""
    pts = slice_horizontal(polys, z)
    if not pts:
        return None
    prof = [-1.0] * nbin
    for (x, y) in pts:
        a = int((math.degrees(math.atan2(y, x)) % 360.0) / 360.0 * nbin) % nbin
        r = math.hypot(x, y)
        if r > prof[a]:
            prof[a] = r
    known = [i for i, v in enumerate(prof) if v >= 0]
    if len(known) < 8:
        return None
    for i in range(nbin):
        if prof[i] < 0:
            near = min(known, key=lambda k: min(abs(k - i), nbin - abs(k - i)))
            prof[i] = prof[near]
    return prof


def count_peaks(prof, rel: float = 0.25) -> int:
    """数剖面里"高于（谷值+rel×峰谷差）"的连续凸起段数量（齿就是这个形状）。"""
    if not prof:
        return 0
    peak, trough = max(prof), min(prof)
    if peak - trough <= 1e-9:
        return 0
    thr = trough + (peak - trough) * rel
    above = [v >= thr for v in prof]
    n = len(above)
    count = 0
    for i in range(n):
        if above[i] and not above[i - 1]:
            count += 1
    return count
