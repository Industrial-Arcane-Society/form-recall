# -*- coding: utf-8 -*-
r"""B 计划：**直写网格** —— 等截面拉伸件 + 直孔，完全绕开布尔运算。

为什么需要（实测依据）：
    纯 Python BSP-CSG 正确且稳定做多孔的上限约 **96 个孔**
    （48 孔 3.9s/误差0.04%、96 孔 36.6s/0.07%、**192 孔体积错 2%**）；
    而 EGT001 SW80x4078 真实有 **545 个孔**。逐个布尔永远做不到。

本模块的做法（不做任何布尔）：
    把"方截面沿轴拉伸 + 若干直通孔"直接拼成三角形：
      · 6 个面各自三角化，面上的圆孔位置**挖空**（Delaunay + 质心过滤，numpy/scipy 加速）；
      · 每个孔补一段圆柱壁；
      · 孔壁与端面/侧面的界线正好落在同一组圆顶点上，天然密封。

**前提条件（已实测验证）**：孔与孔不能相交 —— 否则两段圆柱壁会互相穿插。
`tools/check_profile_holes.py` 实测：289 个型材里 **267 个无相交**（EGT001/EGT002），
只有 22 个 EGT006（⌀30 圆型材）会相交，那些走 A′ 的局部布尔。

用法（库）:
    from ai_modeler import directmesh as D
    polys = D.extrude_with_holes(sec_a=80, sec_b=80, length=118, axis='Y',
                                 base=-59, holes=[...], seg=24)
    # holes: [{'d': 11, 'axis': 'Z', 'u': 0.0, 'v': 14.0, 'through': True}, ...]
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

from . import kernel as K
from .docmodel import ADD, CUT, Feature

_AX = {"X": 0, "Y": 1, "Z": 2}


def _circle(radius: float, seg: int) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * math.pi, seg, endpoint=False)
    return np.stack([radius * np.cos(t), radius * np.sin(t)], axis=1)


def _tri_area2(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _handed(ha: int, hop0: int, hop1: int) -> int:
    """环向基 (hop0, hop1) 相对孔轴 ha 的手性：+1 右手、−1 左手。

    只有三个轴，直接列出来最清楚（也避免写通用叉乘反而绕）：
      · ha=X(0)：基 (1,2)=(Y,Z) → Y×Z=X ✓ 右手
      · ha=Y(1)：基 (0,2)=(X,Z) → X×Z=−Y ✗ 左手
      · ha=Z(2)：基 (0,1)=(X,Y) → X×Y=Z ✓ 右手
    """
    return 1 if (ha, hop0, hop1) in ((0, 1, 2), (2, 0, 1)) else -1


def _grid(lo: float, hi: float, step: float = 12.0) -> np.ndarray:
    """在 [lo,hi] 上按固定步长取一串**确定性**的采样坐标（含两端）。

    为什么要按"全局坐标"取样、而不是每个面各自等分：
    相邻两个面共享一条棱（例如 x=+40、z=+40 那条沿 Y 的棱）。如果两个面各自
    按自己的边长等分，落在这条棱上的点位置不同 → 共享棱对不上 → 网格不封闭
    （实测：非封闭边 64 条）。按同一个全局区间取样，两个面算出来的点**逐个相同**。
    """
    span = float(hi) - float(lo)
    n = max(1, int(math.ceil(span / step)))
    return np.linspace(lo, hi, n + 1)


def _triangulate_rect_with_holes(a, b, holes, seg=24,
                                 grid_u=None, grid_v=None):
    """把 a×b 的矩形（以原点为中心）挖掉若干圆孔后三角化。

    返回 (pts[N,2], tris[M,3])，都在矩形自身坐标系里（中心为原点）。
    `grid_u`/`grid_v` 是边界采样点（按全局坐标算好传进来，保证与相邻面一致）。

    做法：把矩形边界点 + 各孔圆周边界一起丢给 `scipy.spatial.Delaunay`，
    然后**丢掉"质心落在任一孔内"的三角形**。
    孔边界点很密，Delaunay 会把相邻圆上点连成边界边，于是挖掉后留下的边界
    就是那条圆周折线本身 —— 与圆柱壁用的是同一组点，两者天然密封。
    """
    from scipy.spatial import Delaunay

    gu = _grid(-a / 2, a / 2) if grid_u is None else grid_u
    gv = _grid(-b / 2, b / 2) if grid_v is None else grid_v
    pts = []
    # 四条边：沿 u 的两条边用同一组 gu，沿 v 的两条边用同一组 gv
    for v in (gv[0], gv[-1]):
        pts.append(np.stack([gu, np.full(len(gu), v)], axis=1))
    for u in (gu[0], gu[-1]):
        pts.append(np.stack([np.full(len(gv), u), gv], axis=1))

    keep_holes = []
    for h in holes:
        r = h["d"] / 2.0
        if h.get("ring"):
            circ = np.array(h["ring"], dtype=float)      # 用孔壁那组点，逐点相同
            c = circ.mean(axis=0)
        else:
            c = np.array([h["u"], h["v"]], dtype=float)
            circ = _circle(r, seg) + c[None, :]
        if (abs(c[0]) + r > a / 2 - 1e-6) or (abs(c[1]) + r > b / 2 - 1e-6):
            continue                       # 贴边/越界的孔不在这条路径处理
        pts.append(circ)
        keep_holes.append((c, r))

    P = np.vstack(pts)
    # ⚠ 必须去重：四条边循环会把**每个角点放两遍**，重复点会让 Delaunay 造出
    #   退化三角形。
    P = np.unique(np.round(P, 9), axis=0)

    # ⚠ **不要**给"没有孔的面"单独走"扇形三角化"：扇形中心与同一条边上的
    #   共线采样点会组成**零面积三角形**，而且相邻两个面各发一次，于是每条盒子棱
    #   被 4 个三角形共用（实测"非封闭边 6 条"就是这么来的）。
    #   一律走 Delaunay + 过滤：没有孔时"质心在孔内"的过滤自然是空操作，
    #   但得到的是**合法三角化**，且保留全部边界采样点（相邻面的棱才对得上）。
    tri = Delaunay(P)
    simp = tri.simplices
    if keep_holes:
        cent = P[simp].mean(axis=1)
        inside = np.zeros(len(simp), dtype=bool)
        for (c, r) in keep_holes:
            d2 = ((cent - c[None, :]) ** 2).sum(axis=1)
            inside |= d2 <= (r * 1.0000001) ** 2
        simp = simp[~inside]
    # 再去掉零面积三角形（共线三点）
    if len(simp):
        p0, p1, p2 = P[simp[:, 0]], P[simp[:, 1]], P[simp[:, 2]]
        area2 = np.abs((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
                       - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0]))
        simp = simp[area2 > 1e-9]
    return P, simp


def _poly(pts, normal, tag=0):
    """由点表 + 外法向造一个 `kernel.Polygon`。

    ⚠ 必须**同时**保证"顶点绕向"与法向一致（右手定则）。只设 plane/normal 不够：
    孔壁那四边形的点是循环生成的，绕向随轴向而变 —— 实测出现"法向朝外、绕向却是
    顺时针"的组合，于是按散度定理算体积时，这个孔不是被挖掉，而是**凭空多出
    5010 mm³**（侧向孔那一版就是这样，外形对、体积反而比实心盒还大）。
    """
    n = K.v_norm(normal)
    if len(pts) >= 3:
        cr = K.v_cross(K.v_sub(pts[1], pts[0]), K.v_sub(pts[2], pts[0]))
        if K.v_dot(cr, n) < 0:
            pts = list(reversed(pts))
    w = K.v_dot(n, pts[0])
    return K.Polygon([(p, n) for p in pts], K.Plane(n, w), tag)


def extrude_with_holes(sec_a: float, sec_b: float, length: float,
                       axis: str = "Z", base: float = 0.0,
                       holes: Sequence[Dict] = (), seg: int = 24,
                       tag: int = 0, ref_min: Sequence[float] = None,
                       ref_size: Sequence[float] = None) -> List:
    """方截面（sec_a × sec_b）沿 `axis` 拉伸 `length`，并挖掉若干**直通孔**。

    `holes` 每项：{'d','axis','u','v','through'}。孔轴可以是三个方向中的任意一个：
      · 与拉伸轴相同 → 打在**两个端面**上；
      · 与拉伸轴不同 → 打在**一对侧面**上。

    ⚠ `ref_min`/`ref_size` 是零件在**它自己坐标系**里的包围盒（从 STEP 实测来的）。
    实测的孔坐标就在那个坐标系里（例如 EGT001 SW80x088 的孔 y ∈ 0~88），
    而本函数建的盒子以原点为中心 —— 不平移的话，孔会被摆到盒子外面或者在错误位置
    （实测：外形 123.5 而不是 88）。给上 ref_min/ref_size 后内部自动平移。

    返回 `kernel.Polygon` 列表。**不做任何布尔运算。**
    """
    ai = _AX[(axis or "Z").upper()]
    ua, va = [k for k in range(3) if k != ai]
    lo, hi = float(base), float(base) + float(length)
    hit = abs(float(sec_a) - float(sec_b)) < 1e-9

    #: 各轴方向的"实体范围"：拉伸轴用 [lo,hi]，另两轴用截面半宽
    rng = {}
    rng[ai] = (lo, hi)
    rng[ua] = (-sec_a / 2.0, sec_a / 2.0)
    rng[va] = (-sec_b / 2.0, sec_b / 2.0)

    #: 世界坐标 → 本地坐标的平移量（把实测坐标搬到"以原点为中心"的建坐标系里）
    dims = {ai: float(length), ua: float(sec_a), va: float(sec_b)}
    if ref_min is not None and ref_size is not None:
        shift = [0.0, 0.0, 0.0]
        for k in range(3):
            shift[k] = -(float(ref_min[k]) + float(ref_size[k]) / 2.0)
    else:
        shift = [0.0, 0.0, 0.0]

    def loc(axis_idx: int, coord: float) -> float:
        return float(coord) + shift[axis_idx]

    # ---- 每个孔的 3D 环点：**一次性算好**，孔壁与面上的孔共用同一组点 ----
    # ⚠ 关键：环所在的两个坐标轴必须取成**右手系**（hop0 × hop1 = +孔轴），
    #   否则 Y 轴孔的绕向会与 X/Z 轴孔相反 —— 实测这两种孔的体积一个偏高
    #   10.66%、一个偏高 0.01%，怎么改"法向朝里朝外"都对不齐。
    #   面内的孔不再自己造圆，而是把同一组 3D 环点投影到面内两轴
    #   （投影自动处理镜像），于是孔壁与面的孔**必然共点**，网格天然密封。
    ring3d = {}
    for idx, h in enumerate(holes):
        ha = _AX[(h.get("axis") or "Z").upper()]
        # ⚠ 面内两轴必须用**升序**（= 提取器存 (u,v) 的口径：另外两个轴按索引升序）。
        #   试过按右手系取 (ha+1, ha+2)：Y 轴孔的 (u,v) 会被转置（提取器存的是
        #   (X,Z)，右手系是 (Z,X)）→ 孔摆错位置、外形从 88×50×50 变成 111×104×50。
        #   至于"环的绕向"，不再靠轴序控制 —— 孔壁四边形的朝里/朝外由几何判据定（见下）。
        hop0, hop1 = [q for q in range(3) if q != ha]
        r = float(h["d"]) / 2.0
        cu, cv = loc(hop0, h["u"]), loc(hop1, h["v"])
        pts = []
        for (x, y) in _circle(r, seg):
            p = [0.0, 0.0, 0.0]
            p[hop0] = cu + float(x)
            p[hop1] = cv + float(y)
            pts.append(p)
        ring3d[idx] = (ha, hop0, hop1, cu, cv, r, pts)

    polys = []
    # ---------------- 6 个面 ----------------
    # 面对 (轴k, 符号)：面内两轴是另外两个。
    # ⚠ 一个孔会打穿哪些面？**只有当孔轴与面的法向平行时**（孔轴 == 面法向轴）
    #   才会打穿这对面。第一版把这条判反了（`ha == k` 时 continue），
    #   结果**两个端面根本没开孔**：轴向孔完全没被挖掉，网格也不封闭
    #   （实测：体积偏高 1.47%、非封闭边 592 条）。
    for k in range(3):
        inner = [q for q in range(3) if q != k]
        A, B = rng[inner[0]][1] - rng[inner[0]][0], rng[inner[1]][1] - rng[inner[1]][0]
        # 边界采样按**全局坐标**算，保证相邻面在共享棱上取到同一组点
        gu = _grid(rng[inner[0]][0], rng[inner[0]][1]) - (rng[inner[0]][0] + A / 2.0)
        gv = _grid(rng[inner[1]][0], rng[inner[1]][1]) - (rng[inner[1]][0] + B / 2.0)
        fh = []
        for idx, h in enumerate(holes):
            ha, hop0, hop1, cu, cv, r, rp = ring3d[idx]
            if ha != k:
                continue
            # 把 3D 环点投影到这条面的面内两轴（inner[0], inner[1]）
            fh.append({"d": float(h["d"]),
                       "ring": [(p[inner[0]], p[inner[1]]) for p in rp]})
        P, tris = _triangulate_rect_with_holes(A, B, fh, seg=seg,
                                               grid_u=gu, grid_v=gv)
        lo_k, hi_k = rng[k]
        for sgn in (-1, 1):
            z = lo_k if sgn < 0 else hi_k
            pts3 = []
            for (pu, pv) in P:
                p = [0.0, 0.0, 0.0]
                p[inner[0]] = float(pu)
                p[inner[1]] = float(pv)
                p[k] = z
                pts3.append(tuple(p))
            for t in tris:
                a3, b3, c3 = pts3[t[0]], pts3[t[1]], pts3[t[2]]
                if sgn < 0:
                    a3, c3 = c3, a3          # 外法向朝 -k
                vecs = [K.v_sub(b3, a3), K.v_sub(c3, a3)]
                p = _poly([a3, b3, c3], K.v_cross(vecs[0], vecs[1]), tag)
                if p.plane.normal[k] * sgn < 0:      # 保证朝外
                    p.flip()
                polys.append(p)

    # ---------------- 每个孔一段圆柱壁 ----------------
    for idx, h in enumerate(holes):
        ha, hop0, hop1, cu, cv, r, rp = ring3d[idx]
        a0, a1 = rng[ha]
        n_ring = len(rp)
        for i in range(n_ring):
            p_i, p_j = rp[i], rp[(i + 1) % n_ring]
            a0p = list(p_i); a0p[ha] = a0; a0p = tuple(a0p)
            a1p = list(p_i); a1p[ha] = a1; a1p = tuple(a1p)
            b0p = list(p_j); b0p[ha] = a0; b0p = tuple(b0p)
            b1p = list(p_j); b1p[ha] = a1; b1p = tuple(b1p)
            # 孔壁朝里/朝外：取决于环向基相对孔轴的**手性**，这是可以推导的，
            # 不该靠试符号（实测两种取法的体积偏差分别是 0.09% 与 12.80%）。
            #   环向基 = (hop0, hop1)，升序，与提取器存 (u,v) 的口径一致。
            #   右手基（e0 × e1 = +e_轴）时，四点顺序取 [a0,a1,b1,b0] 的法向
            #   指向轴心 —— 正是孔壁要的方向；左手基则要用 [a0,b0,b1,a1]。
            if _handed(ha, hop0, hop1) > 0:
                quad = [a0p, a1p, b1p, b0p]
            else:
                quad = [a0p, b0p, b1p, a1p]
            nrm = K.v_cross(K.v_sub(quad[1], quad[0]), K.v_sub(quad[2], quad[0]))
            polys.append(_poly(quad, nrm, tag))
    return polys


def make_feature(section, holes, axis="Z", base=0.0, seg=24):
    """把"方截面 + 直孔"包装成一个 `Feature`（供零件库直接返回）。"""
    return Feature("extrude_holes", {
        "sec_a": float(section[0]), "sec_b": float(section[1]),
        "length": float(section[2]), "axis": axis, "base": float(base),
        "holes": list(holes), "segments": int(seg)}, ADD,
        name="等截面拉伸件 %g×%g×%g + %d 个直孔（直写网格）"
             % (section[0], section[1], section[2], len(holes)))
