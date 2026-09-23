# -*- coding: utf-8 -*-
r"""
从真实 STEP 建「型号 → 真实几何」对照表。

数据来源：CATIA 批处理导出的 521 个 `.stp`（`%TEMP%\catia_step_out`）。
这是**唯一**能拿到真实几何的路 —— CATPart 是私有二进制，
实测只能抠出 `V5R26SP0HF0` 这类元数据；STEP 是 ISO-10303-21 文本格式，可解析。

每个零件量这些**真实**指标：
  · 包围盒（x/y/z 尺寸 + 最小值，用于判断建模原点约定）
  · 实体数（MANIFOLD_SOLID_BREP）、闭壳/开壳
  · 面数、按曲面类型分类（平面/圆柱/球/锥/环面/B样条）
  · **孔径表**：每个圆柱面的直径 × 出现次数（这是"真实样子"的核心）
  · 圆/直线/边数、B样条面数（判断是不是自由曲面件）

⚠️ 大文件流式读，不整份进内存（最大单个 20MB+，521 个合计 907MB）。

用法：python tools\build_real_geometry.py [输入目录]
输出：kb/real_geometry.json、reports/真实几何对照表.md
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = r"C:\Users\25643\AppData\Local\Temp\catia_step_out"
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

# 实体行：#123=ENTITY_NAME(args) ;
_RE_ENTITY = re.compile(r"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*;?\s*$")
# 数值 token：必须接受 CATIA 实际写出的几种形式
#   `-0.`（尾点无小数位）、`123.`、`.5`、`+1.0`、`1.0E-15`、`-4.2`
# 旧版写成 `-?\d+(?:\.\d+)?`，**匹配不了 `-0.`**，于是大量顶点被静默丢弃
# （实测 M8x20.stp 有 36 个 Vertex 点，旧正则只认下 2 个，
#   导致该件 bbox 被算成 8.4×0×0.8）。这是第二个必须修的正确性缺陷。
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?"
_RE_XYZ = re.compile(r"\(\s*(%s)\s*,\s*(%s)\s*,\s*(%s)\s*\)"
                     % (_NUM, _NUM, _NUM))

#: 计入统计的面类型
SURFACE_KINDS = ("PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
                 "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
                 "B_SPLINE_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS",
                 "SURFACE_OF_REVOLUTION", "SURFACE_OF_LINEAR_EXTRUSION",
                 "RATIONAL_B_SPLINE_SURFACE", "CURVE_BOUNDED_SURFACE")


def analyze(path, max_bytes=64 * 1024 * 1024):
    """流式解析一个 STEP，返回几何概要。"""
    ent_types = Counter()
    # 只看需要的实体，避免把 900MB 全建成字典
    cyl_ax, cyl_r = {}, {}
    dirs = {}
    #: AXIS2_PLACEMENT_3D 的 id → 定位点；以及它的法向 DIRECTION id
    a2p_pt, a2p_ax = {}, {}
    #: (AXIS2_PLACEMENT_3D id, 半径) —— 用于把圆弧的极值并进包围盒
    circles = {}
    #: 点 id → 坐标（圆弧定位点要回查；大文件约 10 万点，可接受）
    cPts = {}
    #: 真几何引用的曲线 id（`EDGE_CURVE` / `SURFACE_CURVE` / `TRIMMED_CURVE`）。
    #  只有被它们引用的圆才算实体轮廓；CATIA 会在文件里塞**构图的辅助圆**
    #  （与 `Line Origine` 同源），若不排除会把包围盒吹到几千毫米。
    shape_curves = set()
    _RE_REF = re.compile(r"#(\d+)")
    pts_min = [float("inf")] * 3
    pts_max = [float("-inf")] * 3
    #: 只用 `VERTEX` 点算包围盒 —— 见下方对 `Line Origine` 的说明
    vtx_min = [float("inf")] * 3
    vtx_max = [float("-inf")] * 3
    #: 顶点列表，用于**分位数裁剪**包围盒（装配导出会混入远处骨架点）
    vtx_list = []
    n_pts = 0
    n_vtx = 0
    n_lines = 0
    product = None
    units = None
    schema = None
    size = os.path.getsize(path)
    if size > max_bytes:
        return {"file": os.path.basename(path), "bytes": size,
                "skipped": "文件超过 %d MB，跳过" % (max_bytes // 1024 // 1024)}

    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line:
                continue
            n_lines += 1
            if schema is None and "FILE_SCHEMA" in line:
                m = re.search(r"'([^']+)'", line)
                if m:
                    schema = m.group(1)
            if units is None and "SI_UNIT" in line and "MILLI" in line:
                units = "mm"
            m = _RE_ENTITY.match(line.strip())
            if not m:
                continue
            eid, etype, args = int(m.group(1)), m.group(2), m.group(3)
            ent_types[etype] += 1
            if etype == "CARTESIAN_POINT":
                m2 = _RE_XYZ.search(args)
                if m2:
                    n_pts += 1
                    xyz = []
                    for i in range(3):
                        try:
                            xyz.append(float(m2.group(i + 1)))
                        except ValueError:
                            xyz.append(0.0)
                    for i in range(3):
                        if xyz[i] < pts_min[i]:
                            pts_min[i] = xyz[i]
                        if xyz[i] > pts_max[i]:
                            pts_max[i] = xyz[i]
                    cPts[eid] = tuple(xyz)
                    # ✅ 只用顶点算真实包围盒。
                    # 实测坑：CATIA 会把构造直线（`LINE`）的 `'Line Origine'`
                    # 写在**半径恰好 500000 mm** 处（例：吊环 M16 的点
                    # (496770.711, 8.5e-11, -56759.796)，模长 = 500000.000）。
                    # 若把这些点计入，包围盒会变成 993541×17.7×707079 这种
                    # 荒谬值 —— 早期版本的 521 条记录全部被污染。
                    # `'Vertex'` 点是真实棱边的端点，不受影响。
                    if "Vertex" in args:
                        n_vtx += 1
                        for i in range(3):
                            if xyz[i] < vtx_min[i]:
                                vtx_min[i] = xyz[i]
                            if xyz[i] > vtx_max[i]:
                                vtx_max[i] = xyz[i]
                        vtx_list.append(tuple(xyz))
            elif etype == "DIRECTION":
                m2 = _RE_XYZ.search(args)
                if m2:
                    dirs[eid] = (float(m2.group(1)), float(m2.group(2)),
                                 float(m2.group(3)))
            elif etype == "AXIS2_PLACEMENT_3D":
                # AXIS2_PLACEMENT_3D(name, #point, #axis, #refdir)
                m2 = re.search(r"#(\d+)\s*(?:,\s*#(\d+))?", args)
                if m2:
                    a2p_pt[eid] = int(m2.group(1))
                    if m2.group(2):
                        a2p_ax[eid] = int(m2.group(2))
            elif etype in ("CIRCLE", "ELLIPSE"):
                # CIRCLE(name, #axis2placement, radius)
                m2 = re.search(r"#(\d+)\s*,\s*(%s)" % _NUM, args)
                if m2:
                    try:
                        circles[eid] = (int(m2.group(1)), float(m2.group(2)))
                    except ValueError:
                        pass
            elif etype == "EDGE_CURVE":
                # EDGE_CURVE(name, #v1, #v2, #geometry, same_sense)
                for mm in _RE_REF.finditer(args):
                    shape_curves.add(int(mm.group(1)))
            elif etype in ("SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE",
                           "TRIMMED_CURVE", "COMPOSITE_CURVE",
                           "B_SPLINE_CURVE_WITH_KNOTS"):
                for mm in _RE_REF.finditer(args):
                    shape_curves.add(int(mm.group(1)))
            elif etype == "CYLINDRICAL_SURFACE":
                m2 = re.search(r"#(\d+)\s*,\s*(%s)" % _NUM, args)
                if m2:
                    cyl_ax[eid] = int(m2.group(1))
                    try:
                        cyl_r[eid] = float(m2.group(2))
                    except ValueError:
                        pass
            elif etype == "PRODUCT" and product is None:
                m2 = re.match(r"\s*'([^']*)'", args)
                if m2:
                    product = m2.group(1).strip()

    # ---- 圆弧极值并入包围盒 ----
    # 顶点包围盒对**光顺圆柱**会偏小：圆柱面在极值方向未必落下一个顶点
    # （实测 `KMS-M01-P097-A20` 的 ⌀22 台阶段被量成 X=19.65）。
    # 圆位于过 `point`、法向 `axis` 的平面内，半径 r，则它在世界轴 e_i 上的
    # 最大偏离 = r·√(1−(axis·e_i)²) —— 这个式子是精确的，下面据此补极值。
    n_circ_ext = 0
    n_circ_skip = 0
    if circles and (n_vtx or not shape_curves):
        circ_min = list(vtx_min) if n_vtx else [float("inf")] * 3
        circ_max = list(vtx_max) if n_vtx else [float("-inf")] * 3
        for cid, (pid, r) in circles.items():
            if shape_curves and cid not in shape_curves:
                n_circ_skip += 1
                continue
            pt = a2p_pt.get(pid)
            if pt is None:
                continue
            pv = cPts.get(pt)
            if pv is None:
                continue
            ax = dirs.get(a2p_ax.get(pid, -1), (0.0, 0.0, 1.0))
            nl = math.sqrt(sum(c * c for c in ax)) or 1.0
            ax = tuple(c / nl for c in ax)
            for i in range(3):
                d = r * math.sqrt(max(0.0, 1.0 - ax[i] * ax[i]))
                if pv[i] - d < circ_min[i]:
                    circ_min[i] = pv[i] - d
                if pv[i] + d > circ_max[i]:
                    circ_max[i] = pv[i] + d
            n_circ_ext += 1

    def _mk(lo, hi):
        return {"min": [round(v, 3) for v in lo],
                "max": [round(v, 3) for v in hi],
                "size": [round(hi[i] - lo[i], 3) for i in range(3)]}

    # ---- 分位数裁剪包围盒 ----
    # 装配导出的 STEP 会把远处骨架/参考几何的顶点也带进来（实测 UNIVER 气缸里
    # 反复出现 2720.50 / 1372.01 这两个相同坐标），使包围盒变成"装配体范围"。
    # 取 0.5%~99.5% 分位可剔除这类稀疏离群点，同时不影响单实体零件
    # （单实体零件裁剪后各轴尺寸不变，见 kb/real_geometry_trust.json 的 A 级）。
    bbox_trim = None
    if len(vtx_list) >= 8:
        lo = [0.0] * 3
        hi = [0.0] * 3
        for i in range(3):
            col = sorted(v[i] for v in vtx_list)
            lo[i] = col[int(0.005 * (len(col) - 1))]
            hi[i] = col[int(0.995 * (len(col) - 1) + 0.5)]
        bbox_trim = _mk(lo, hi)

    # 优先用顶点包围盒（无构造线污染）；没有顶点时才退回全部点
    bbox = bbox_raw = None
    if n_pts:
        bbox_raw = _mk(pts_min, pts_max)
    if n_vtx:
        bbox_vertex = _mk(vtx_min, vtx_max)
    else:
        bbox_vertex = None
    # 最终包围盒 = 顶点包围盒 ∪ 圆弧极值（圆弧极值更可靠，见上文公式）
    if n_vtx and n_circ_ext:
        bbox = _mk(circ_min, circ_max)
        # 标记出被圆弧修正过的轴，便于复核
        fix = [i for i in range(3)
               if abs(bbox["size"][i] - bbox_vertex["size"][i]) > 1e-6]
        if fix:
            bbox["widened_axes"] = ["XYZ"[i] for i in fix]
    elif bbox_vertex:
        bbox = bbox_vertex
    elif n_pts:
        bbox = bbox_raw
        bbox["fallback"] = "无 VERTEX 点，退回全点包围盒（可能含构造线，慎用）"
    # 圆柱面直径指纹：圆柱半径（STEP 里的字面属性，不受坐标污染影响）→ 直径
    dia = Counter()
    for eid, r in cyl_r.items():
        dia[round(2.0 * r, 3)] += 1
    # ⚠ 第一版按**出现次数**排序后只留 12 档 —— 装配导出件有上千个小圆柱面，
    #   于是 ⌀50/⌀63 这种"缸径"被挤掉了（实测 UCBH50 的直径表里根本没有 ⌀50），
    #   直接导致"按实测指纹建模"失效。改成**按直径从大到小**留 30 档：
    #   次数仍然保留在值里，只是选取口径换了。
    items = sorted(dia.items(), key=lambda x: -x[0])[:30]
    hole_diameters = {("%g" % d): n for d, n in items}
    #: 另存一份"最常出现的"，供需要频次的场合使用
    cyl_by_count = {("%g" % d): n for d, n in
                    sorted(dia.items(), key=lambda x: -x[1])[:12]}
    n_solids = ent_types.get("MANIFOLD_SOLID_BREP", 0) + \
        ent_types.get("BREP_WITH_VOIDS", 0)
    return {
        "file": os.path.basename(path),
        "bytes": size,
        "lines": n_lines,
        "schema": schema,
        "product": product,
        "n_solids": n_solids,
        "n_faces": ent_types.get("ADVANCED_FACE", 0),
        "n_cylinders": len(cyl_r),
        "n_points": n_pts,
        "n_vertices": n_vtx,
        "n_circles": ent_types.get("CIRCLE", 0),
        "n_lines_geom": ent_types.get("LINE", 0),
        "n_bspline_surf": (ent_types.get("B_SPLINE_SURFACE", 0) +
                           ent_types.get("B_SPLINE_SURFACE_WITH_KNOTS", 0) +
                           ent_types.get("RATIONAL_B_SPLINE_SURFACE", 0)),
        "n_planes": ent_types.get("PLANE", 0),
        "n_spheres": ent_types.get("SPHERICAL_SURFACE", 0),
        "n_cones": ent_types.get("CONICAL_SURFACE", 0),
        "n_tori": ent_types.get("TOROIDAL_SURFACE", 0),
        "bbox": bbox,
        "bbox_raw": bbox_raw,
        "bbox_vertex": bbox_vertex,
        "bbox_trim": bbox_trim,
        "n_circ_ext": n_circ_ext,
        "hole_diameters": hole_diameters,
        "n_distinct_diameters": len(dia),
        "cyl_by_count": cyl_by_count,
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IN
    if not os.path.isdir(src):
        print("目录不存在:", src)
        return 1
    files = []
    for dp, _dn, fns in os.walk(src):
        for fn in fns:
            if fn.lower().endswith(".stp") or fn.lower().endswith(".step"):
                files.append(os.path.join(dp, fn))
    files.sort()
    print("找到 %d 个 STEP，开始分析（流式，逐个处理）…" % len(files))

    out = []
    n_ok = n_skip = 0
    for i, p in enumerate(files, 1):
        try:
            r = analyze(p)
        except Exception as e:
            r = {"file": os.path.basename(p), "error": str(e)[:80]}
        # 记录相对目录（保留族信息）
        r["rel"] = os.path.relpath(p, src).replace("\\", "/")
        if r.get("skipped") or r.get("error"):
            n_skip += 1
        else:
            n_ok += 1
        out.append(r)
        if i % 50 == 0 or i == len(files):
            print("  已处理 %d/%d（成功 %d 跳过 %d）" % (i, len(files), n_ok, n_skip))

    os.makedirs(KB, exist_ok=True)
    os.makedirs(REPORTS, exist_ok=True)
    db = {"source": src, "n_files": len(files), "n_ok": n_ok, "n_skip": n_skip,
          "parts": out}
    with io.open(os.path.join(KB, "real_geometry.json"), "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    print("\n已写入 kb/real_geometry.json（%d 条）" % len(out))

    # ---- 按族汇总 ----
    fam = {}
    for r in out:
        d = r["rel"].split("/")[0] if "/" in r["rel"] else "(根)"
        sub = r["rel"].split("/")[1] if r["rel"].count("/") >= 1 else ""
        key = "%s/%s" % (d, sub) if sub else d
        fam.setdefault(key, []).append(r)
    print("\n按族统计（族 | 件数 | 面数中位 | 包围盒示例）：")
    md = ["# 真实几何对照表（从 CATIA 导出的 STEP 量出来）", "",
          "数据来源：`%s`（%d 个 STEP，%d 个解析成功）" % (src, len(files), n_ok),
          "",
          "**这些数字是从真实 CAD 导出的 STEP 里量出来的，不是推测值。**", "",
          "## 按族汇总", "",
          "| 族 | 件数 | 面数(中位) | 实体(中位) | 典型包围盒 |", "|---|---|---|---|---|"]
    for k in sorted(fam, key=lambda x: -len(fam[x])):
        rs = [r for r in fam[k] if r.get("bbox")]
        if not rs:
            md.append("| %s | %d | - | - | - |" % (k, len(fam[k])))
            continue
        faces = sorted(r["n_faces"] for r in rs)
        sols = sorted(r["n_solids"] for r in rs)
        ex = rs[0]["bbox"]["size"]
        print("  %-46s %4d 件  面数中位 %5d  例 %s"
              % (k, len(fam[k]), faces[len(faces) // 2],
                 "×".join("%g" % v for v in ex)))
        md.append("| %s | %d | %d | %d | %s |"
                  % (k, len(fam[k]), faces[len(faces) // 2],
                     sols[len(sols) // 2],
                     "×".join("%g" % v for v in ex)))

    md += ["", "## 明细（前 200 件）", "",
           "| 文件 | 实体 | 面数 | 圆柱面 | 平面 | 包围盒(x×y×z) | 主要孔径 |",
           "|---|---|---|---|---|---|---|"]
    for r in out[:200]:
        if not r.get("bbox"):
            continue
        bb = r["bbox"]["size"]
        hd = "、".join("⌀%s×%d" % (k, v) for k, v in
                       list(r.get("hole_diameters", {}).items())[:4])
        md.append("| %s | %d | %d | %d | %d | %s | %s |"
                  % (r["rel"], r["n_solids"], r["n_faces"], r["n_cylinders"],
                     r["n_planes"], "×".join("%g" % v for v in bb), hd or "—"))
    with io.open(os.path.join(REPORTS, "真实几何对照表.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 reports/真实几何对照表.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
