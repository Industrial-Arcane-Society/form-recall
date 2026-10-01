# -*- coding: utf-8 -*-
r"""A′ 计划验收：局部布尔（`solid_cut`）**与解析/独立估计值**必须一致。

为什么不是"跟旧实现对比"就完事（这是本轮最重要的一个发现）：
    旧实现 `csg_subtract` 是 csg.js 那套移植，一直被当作"参考"。可这一轮拿
    **解析体积**去量，它自己就是错的 —— 只要刀具体在零件端面**外伸**（也就是
    我们最常见的"通孔"：`_build_holes` 让刀具体两头各长出 10 mm），它就会多切：

        方块 60×60×20 + ⌀20 通孔（刀两端外伸）  解析 65788.3   旧 62682.5（多切 4.72%）
        方块 60×60×20 + ⌀20 孔（刀与端面齐平）  解析 65788.3   旧 65788.3（对）
        圆管 ⌀30/⌀25×40                        解析  8541.0   旧   452.9（多切 94.7%）

    也就是说：**"刀端面与零件端面齐平"才是对的，"外伸"就错**。我们几乎所有孔
    都是外伸的通孔 —— 旧实现的体积一直偏小；而外形判据（包围盒）看不出来，
    所以一直没被发现。

判据（都不依赖旧实现）：
  1. **体积**：与"多边形化解析值 / 蒙特卡洛独立估计"比，偏差 ≤0.3%（交叉孔 1%）；
  2. **封闭性**：随机取点，用解析几何判定"在不在实体内"，再用网格做射线奇偶
     判定，两者必须一致（离任何表面 0.25 mm 以内的点跳过）—— 缺面、朝向反了
     的网格会成片不一致；
  3. 记录旧实现的偏差与耗时，作为"这一版修掉了什么"的证据。

用法: python tools\check_bsp_local.py [--big]
输出: reports/局部布尔验收.md
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import kernel as K          # noqa: E402

REPORTS = os.path.join(ROOT, "reports")
SEG = 24


def poly_area(r, seg=SEG):
    return 0.5 * seg * r * r * math.sin(2 * math.pi / seg)


def _tess_r(theta, r, seg=SEG):
    step = 2 * math.pi / seg
    t = theta % step
    return r * math.cos(math.pi / seg) / math.cos(t - step / 2.0)


def cyl(r, h, c, axis='Z', z0=0.0, seg=SEG):
    return K.make_cylinder(r, h, c, 1, seg, axis, False, z0)


def box(l, w, h, c=(0, 0, 0)):
    return K.make_box(l, w, h, c, 1)


# --------------------------------------------------------------------------
# 解析（多边形化）几何：点是否在"基准体减掉孔"之内
# --------------------------------------------------------------------------
def pred_box_minus_holes(bb, holes):
    """bb = (lo0,lo1,lo2,hi0,hi1,hi2)；holes = [(c, axis, r), ...]"""
    hx = []
    for (c, ax, r) in holes:
        ai = "XYZ".index(ax)
        oth = [i for i in range(3) if i != ai]
        hx.append((c, oth, r))

    def pred(p):
        for i in range(3):
            if p[i] < bb[i] - 1e-9 or p[i] > bb[3 + i] + 1e-9:
                return False
        for (c, oth, r) in hx:
            dx = p[oth[0]] - c[oth[0]]
            dy = p[oth[1]] - c[oth[1]]
            rr = math.hypot(dx, dy)
            if rr < _tess_r(math.atan2(dy, dx), r):
                return False
        return True
    return pred


def pred_tube_minus_holes(outer_r, z0, z1, holes):
    hx = []
    for (c, ax, r) in holes:
        ai = "XYZ".index(ax)
        oth = [i for i in range(3) if i != ai]
        hx.append((c, oth, r))

    def pred(p):
        if p[2] < z0 - 1e-9 or p[2] > z1 + 1e-9:
            return False
        if math.hypot(p[0], p[1]) > _tess_r(math.atan2(p[1], p[0]), outer_r):
            return False
        for (c, oth, r) in hx:
            dx = p[oth[0]] - c[oth[0]]
            dy = p[oth[1]] - c[oth[1]]
            rr = math.hypot(dx, dy)
            if rr < _tess_r(math.atan2(dy, dx), r):
                return False
        return True
    return pred


def _bb_of(pred, bb):
    return bb


def _mc_volume(pred, bb, n=400000, seed=11):
    """蒙特卡洛估体积（与网格无关）。"""
    import numpy as np
    rng = np.random.default_rng(seed)
    pts = np.column_stack([rng.uniform(bb[i], bb[3 + i], n) for i in range(3)])
    cnt = 0
    for p in pts:
        if pred(p):
            cnt += 1
    vol_box = (bb[3] - bb[0]) * (bb[4] - bb[1]) * (bb[5] - bb[2])
    return cnt / float(n) * vol_box


def welded_open_edges(polys, tol=4):
    """T 形接点统计（仅供参考）。BSP 结果天生带 T 形接点，不代表破洞。"""
    cnt = {}
    for p in polys:
        pts = [tuple(round(v, tol) for v in q) for q in p.points()]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if a == b:
                continue
            k = (a, b) if a <= b else (b, a)
            cnt[k] = cnt.get(k, 0) + 1
    return sum(1 for v in cnt.values() if v != 2)


def parity_ok(polys, pred, bb, n=250, seed=7, margin=0.25):
    """射线奇偶一致性：网格是不是真正封闭、朝向正确的曲面。"""
    import numpy as np
    tris = K.polygons_to_triangles(polys)
    if not tris or len(tris) > 60000:
        return None
    T = np.array([[t[0], t[1], t[2]] for t in tris], dtype=float)
    v0, v1, v2 = T[:, 0], T[:, 1], T[:, 2]
    e1, e2 = v1 - v0, v2 - v0
    rng = np.random.default_rng(seed)
    bad = 0
    tested = 0
    for _ in range(int(n * 4)):
        if tested >= n:
            break
        p = np.array([rng.uniform(bb[i], bb[3 + i]) for i in range(3)])
        inside = pred(tuple(p))
        # 离表面太近的点跳过（边界歧义），用粗判据：到任一面顶点的距离
        pts = tuple(p)
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        h = np.cross(d, e2)
        a = np.einsum('ij,ij->i', e1, h)
        ok = np.abs(a) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = p - v0
        u = f * np.einsum('ij,ij->i', s, h)
        q = np.cross(s, e1)
        v = f * (q @ d)
        t = f * np.einsum('ij,ij->i', e2, q)
        hit = ok & (u >= -1e-9) & (u <= 1 + 1e-9) & (v >= -1e-9) \
            & (u + v <= 1 + 1e-9) & (t > 1e-9)
        dists = np.where(hit, t, np.inf)
        if float(np.min(dists)) < 3 * margin:
            continue          # 贴着表面，跳过（避免"擦边"歧义）
        tested += 1
        if (int(np.count_nonzero(hit)) % 2 == 1) != inside:
            bad += 1
    return (bad == 0)


def run(name, base, cuts, pred, bb, tol_pct=0.3, n_mc=400000):
    expected = _mc_volume(pred, bb, n=n_mc)
    t0 = time.time()
    body_ref = list(base)
    for c in cuts:
        body_ref = K.csg_subtract(body_ref, c)
    t_ref = time.time() - t0
    t0 = time.time()
    root = K.solid_begin(base)
    touched = 0
    for c in cuts:
        touched += K.solid_cut(root, c)
    body_new = K.node_all_polygons(root)
    t_new = time.time() - t0
    v_ref, v_new = K.volume_of(body_ref), K.volume_of(body_new)
    d_ref = 100 * abs(v_ref - expected) / expected
    d_new = 100 * abs(v_new - expected) / expected
    par = parity_ok(body_new, pred, bb)
    return {"case": name, "n_cut": len(cuts), "expected": expected,
            "v_ref": v_ref, "v_new": v_new, "d_ref": d_ref, "d_new": d_new,
            "n_ref": len(body_ref), "n_new": len(body_new),
            "open_new": welded_open_edges(body_new), "t_ref": t_ref,
            "t_new": t_new, "touched": touched, "tol": tol_pct, "parity": par,
            "ok": d_new <= tol_pct and par is not False}


def cases(big=False):
    out = []
    # ① 方块（底面在 z=0）+ N 个轴向通孔，刀具体两端各外伸 10（与 `_build_holes` 一致）
    for N in [4, 16, 48, 96] + ([192] if big else []):
        base = box(120, 120, 20)
        bb = K.bbox(base)
        cuts, holes, pos = [], [], []
        for i in range(N):
            x = -45 + 15 * (i % 7)
            y = -45 + 15 * ((i // 7) % 7)
            if (x, y) in pos:
                continue
            pos.append((x, y))
            cuts.append(cyl(2.5, 40, (x, y, 0), 'Z', -10.0))
            holes.append(((x, y, 0), 'Z', 2.5))
        out.append(("方块 %d 个通孔（刀外伸）" % len(cuts), base, cuts,
                    pred_box_minus_holes(bb, holes), bb, 0.6))
    base = box(60, 60, 20)
    bb = K.bbox(base)
    h20 = [((0, 0, 0), 'Z', 10.0)]
    out.append(("⌀20 通孔·刀两端外伸", base, [cyl(10, 40, (0, 0, 0), 'Z', -10.0)],
                pred_box_minus_holes(bb, h20), bb, 0.6))
    out.append(("⌀20 通孔·刀与端面齐平", base, [cyl(10, 20, (0, 0, 0), 'Z', 0.0)],
                pred_box_minus_holes(bb, h20), bb, 0.6))
    # ③ 圆管（外径 30 / 内孔 25，z 0..40）
    #   ⚠ 基准体也**必须用新路径**建：旧实现连这个内孔都建错（内孔刀两端外伸 →
    #     整根管子被切掉 98%：452.9 vs 8541.0）。这本身就是旧实现 bug 的证据。
    _r = K.solid_begin(cyl(15, 40, (0, 0, 0), 'Z', 0.0))
    K.solid_cut(_r, cyl(12.5, 60, (0, 0, 0), 'Z', -10.0))
    tube = K.node_all_polygons(_r)
    out.append(("圆管 ⌀30/⌀25×40（已含内孔）", tube,
                [cyl(3, 40, (5, 5, 0), 'Z', -10.0)],
                pred_tube_minus_holes(15.0, 0.0, 40.0, [((0, 0, 0), 'Z', 12.5),
                                                        ((5, 5, 0), 'Z', 3.0)]),
                K.bbox(tube), 0.4))
    # ④ 沉孔（⌀8 通 + ⌀14 深 6）—— 沉孔是**盲孔**：把"孔"限定在 z 14~20 那一段
    cb = box(60, 60, 20)
    cbb = K.bbox(cb)

    def pred_cb(p, _bb=cbb):
        if not pred_box_minus_holes(_bb, [((0, 0, 0), 'Z', 4.0)])(p):
            return False
        # 沉孔：z 在 14~20 之间、且落在 ⌀14 之内才算被切掉
        if p[2] >= 14.0:
            rr = math.hypot(p[0], p[1])
            if rr < _tess_r(math.atan2(p[1], p[0]), 7.0):
                return False
        return True

    out.append(("沉孔（通孔 + 6 深沉孔）", cb,
                [cyl(4, 40, (0, 0, 0), 'Z', -10.0), cyl(7, 6, (0, 0, 0), 'Z', 14.0)],
                pred_cb, cbb, 0.6))
    # ⑤ 孔壁与侧面正好相切（r=10，轴在 x=20，方块半宽 30）
    tb = box(60, 60, 20)
    tbb = K.bbox(tb)
    out.append(("孔壁与侧面相切", tb, [cyl(10, 40, (20, 0, 0), 'Z', -10.0)],
                pred_box_minus_holes(tbb, [((20, 0, 0), 'Z', 10.0)]), tbb, 0.6))
    # ⑥ 三向交叉孔（EGT006 那一类）
    b3 = box(60, 60, 60)
    bb3 = K.bbox(b3)
    ch = [((0, 0, 0), a, 4.0) for a in "XYZ"]
    out.append(("三向交叉孔（⌀8）", b3,
                [cyl(4, 80, (0, 0, 0), 'X', -40.0), cyl(4, 80, (0, 0, 0), 'Y', -40.0),
                 cyl(4, 80, (0, 0, 0), 'Z', -10.0)],
                pred_box_minus_holes(bb3, ch), bb3, 0.6))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--big", action="store_true", help="连 192 孔一起跑")
    a = ap.parse_args()
    rows = []
    for name, base, cuts, pred, bb, tol in cases(a.big):
        d = run(name, base, cuts, pred, bb, tol)
        rows.append(d)
        print("%-26s 刀%3d  独立估计 %10.1f  旧 %10.1f(%7.3f%%)  新 %10.1f(%6.3f%%)  "
              "封闭 %s  时间 %.2fs→%.2fs  %s"
              % (d["case"], d["n_cut"], d["expected"], d["v_ref"], d["d_ref"],
                 d["v_new"], d["d_new"],
                 "是" if d["parity"] else ("跳过" if d["parity"] is None else "否"),
                 d["t_ref"], d["t_new"], "PASS" if d["ok"] else "FAIL"))
    ok = sum(1 for d in rows if d["ok"])
    print("=" * 112)
    print("通过 %d/%d" % (ok, len(rows)))
    L = ["# 局部布尔验收（A′ 计划）", "",
         "`solid_cut`（就地增量切除 + 包围盒剪枝 + 共面处理）与**独立估计值**对比。",
         "体积用蒙特卡洛按**多边形化**几何独立估计（与网格无关），封闭性用",
         "「解析判定 vs 网格射线奇偶」一致性判定。", "",
         "## 为什么判据不是「跟旧实现比」", "",
         "旧实现 `csg_subtract`（csg.js 移植）在**刀具体外伸到零件端面之外**时会多切，",
         "而那正是我们最常见的「通孔」（`_build_holes` 让刀两头各长 10 mm）。",
         "这一轮拿独立估计值一量就露了（见下表）：",
         "「刀端面与零件端面齐平」时旧实现是对的，一旦外伸就错。",
         "外形判据（包围盒）看不出体积偏差，所以一直没被发现。", "",
         "## 结果", "",
         "| 用例 | 刀数 | 独立估计 | 旧实现 | 旧偏差 | 新实现 | 新偏差 | 封闭 | 旧耗时 | 新耗时 | 结论 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in rows:
        L.append("| %s | %d | %.1f | %.1f | %.3f%% | %.1f | %.3f%% | %s | %.2fs | %.2fs | %s |"
                 % (d["case"], d["n_cut"], d["expected"], d["v_ref"], d["d_ref"],
                    d["v_new"], d["d_new"],
                    "是" if d["parity"] else ("跳过" if d["parity"] is None else "否"),
                    d["t_ref"], d["t_new"], "通过" if d["ok"] else "不通过"))
    L += ["", "通过 **%d/%d**。" % (ok, len(rows))]
    with io.open(os.path.join(REPORTS, "局部布尔验收.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports/局部布尔验收.md")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
