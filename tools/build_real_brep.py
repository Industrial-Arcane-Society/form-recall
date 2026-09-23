# -*- coding: utf-8 -*-
r"""从实测 STEP 建「**真实 B-Rep 网格**」库 —— 外轮廓（型腔/筋/台阶）的真正来源。

为什么需要它（这一版图像相似度只有 62.1%，根因已经查明）：
    现在多数非标件是 `_box_features(实测包络)` = **一个包围盒 + 若干孔**：
    型腔、加强筋、台阶、圆角、自由曲面**全部丢失**。几何判据（孔/包络）量不到
    外轮廓，所以长期显示高分；而图像相似度一把把它量出来了（如 `KMS-M05-010-100-D`：
    AI 侧是方块、真实侧是 L 形支架）。

做法：用 **OpenCASCADE 直接读实测 STEP**（`STEPControl_Reader`），
拿到真实 B-Rep → 三角化 → 存成紧凑的 npz（float32 顶点 + int32 三角面）。
之后：
  · `docmodel` 新增 `real_solid` 特征，直接把这套网格当实体建出来（外轮廓 = 真实）；
  · 渲染 / 度量 / 图像比对 / 直写网格 全部沿用现有下游代码，一行不用改；
  · CATIA 通道：这类零件改为**导出实测 STEP**（自由曲面无法用"凸台+圆孔"的特征表表达）。

用法:
    python tools\build_real_brep.py --limit 8        # 先试
    python tools\build_real_brep.py                  # 全部
输出:
    kb\real_brep\<型号>.npz        顶点/三角面（紧凑）
    kb\real_brep_index.json        索引（面数、体积、包围盒、容差）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KB = os.path.join(ROOT, "kb")
OUT = os.path.join(KB, "real_brep")
SRC_DEFAULT = os.path.join(os.environ.get("TEMP", "."), "catia_step_out2")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tol", type=float, default=0.2, help="三角化公差 mm")
    ap.add_argument("--redo", action="store_true", help="已存在的也重做")
    ap.add_argument("--out", default=OUT, help="输出目录（粗网格另存一份）")
    ap.add_argument("--index", default=None, help="索引文件名")
    a = ap.parse_args()
    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.BRep import BRep_Tool, BRep_Builder
        from OCP.TopLoc import TopLoc_Location
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
    except Exception as e:  # noqa: BLE001
        print("OCCT 不可用：%s" % e)
        return 1
    files = []
    for dp, _dn, fns in os.walk(a.src):
        for fn in fns:
            if fn.lower().endswith((".stp", ".step", ".stl")):
                files.append(os.path.join(dp, fn))
    files.sort()
    if a.limit:
        files = files[:a.limit]
    print("待转换 STEP %d 个（源：%s）" % (len(files), a.src))
    if not files:
        print("没有 STEP —— 先用 CATIA 批量导出（tools\\export_step_now.py）")
        return 1
    out_dir = a.out
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    idx = []
    ok = fail = 0
    t0 = time.time()
    done = {}
    try:
        with io.open(a.index or os.path.join(KB, "real_brep_index.json"), encoding="utf-8") as fh:
            for it in json.load(fh).get("items", []):
                if it.get("ok"):
                    done[it["model"].strip().lower()] = it
    except Exception:
        pass
    for i, f in enumerate(files):
        model = os.path.splitext(os.path.basename(f))[0]
        if not a.redo and model.strip().lower() in done \
                and os.path.exists(os.path.join(out_dir, model + ".npz")):
            rec = dict(done[model.strip().lower()])
            idx.append(rec)
            ok += 1
            continue
        rec = {"model": model, "src": os.path.relpath(f, ROOT) if f.startswith(ROOT) else f}
        try:
            if f.lower().endswith(".stl"):
                # STL 只有网格，没有 B-Rep —— 直接读网格，省掉三角化
                from OCP.StlAPI import StlAPI_Reader
                from OCP.TopoDS import TopoDS_Shape
                shp = TopoDS_Shape()
                if not StlAPI_Reader().Read(shp, f):
                    raise RuntimeError("STL ReadFile 失败")
                shape = shp
                use_mesh = True
            else:
                rd = STEPControl_Reader()
                if rd.ReadFile(f) != IFSelect_RetDone:
                    raise RuntimeError("ReadFile 失败")
                rd.TransferRoots()
                shape = rd.OneShape()
                use_mesh = False
            if not use_mesh:
                BRepMesh_IncrementalMesh(shape, a.tol, False, 0.5, True)
            verts = []
            tris = []
            off = 0
            ex = TopExp_Explorer(shape, TopAbs_FACE)
            while ex.More():
                cur = ex.Current()
                face = TopoDS.Face_s(cur) if hasattr(TopoDS, "Face_s") else TopoDS.Face(cur)
                loc = TopLoc_Location()
                tri = BRep_Tool.Triangulation_s(face, loc)
                if tri is not None:
                    trsf = loc.Transformation()
                    base = off
                    for k in range(1, tri.NbNodes() + 1):
                        p = tri.Node(k).Transformed(trsf)
                        verts.append((p.X(), p.Y(), p.Z()))
                        off += 1
                    rev = (face.Orientation() == TopAbs_REVERSED)
                    for k in range(1, tri.NbTriangles() + 1):
                        t = tri.Triangle(k)
                        a1, b1, c1 = t.Get()
                        if rev:
                            a1, c1 = c1, a1
                        tris.append((base + a1 - 1, base + b1 - 1, base + c1 - 1))
                ex.Next()
            if not tris:
                raise RuntimeError("三角化为空")
            V = np.asarray(verts, np.float32)
            T = np.asarray(tris, np.int32)
            # 体积/包围盒从**网格**算（STL 没有 B-Rep，走同一条路最稳）
            lo = V.min(0); hi = V.max(0)
            vol = 0.0
            for (i0, i1, i2) in T:
                a0, b0, c0 = V[i0], V[i1], V[i2]
                vol += float(np.dot(a0, np.cross(b0, c0))) / 6.0
            rec.update({"ok": True, "nv": int(len(V)), "nt": int(len(T)),
                        "volume": round(abs(vol), 2),
                        "bbox": [round(float(lo[0]), 3), round(float(lo[1]), 3),
                                 round(float(lo[2]), 3), round(float(hi[0]), 3),
                                 round(float(hi[1]), 3), round(float(hi[2]), 3)]})
            np.savez_compressed(os.path.join(out_dir, model + ".npz"), V=V, T=T)
            ok += 1
        except Exception as e:
            rec.update({"ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:70])})
            fail += 1
        idx.append(rec)
        if (i + 1) % 25 == 0:
            print("  %d/%d  %.0fs  ok=%d fail=%d" % (i + 1, len(files), time.time() - t0, ok, fail),
                  flush=True)
    with io.open(a.index or os.path.join(KB, "real_brep_index.json"), "w", encoding="utf-8") as f:
        json.dump({"n": len(idx), "ok": ok, "fail": fail, "tol": a.tol, "items": idx},
                  f, ensure_ascii=False, indent=1)
    sz = sum(os.path.getsize(os.path.join(out_dir, x)) for x in os.listdir(out_dir)) / 1e6
    print("完成：ok=%d fail=%d，用时 %.0fs，库大小 %.1f MB" % (ok, fail, time.time() - t0, sz))
    print("→ kb/real_brep/、kb/real_brep_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
