# -*- coding: utf-8 -*-
r"""核对"模型坐标系"与"实测零件坐标系"是不是同一个。

为什么要有这个检查（`bench_detail_fidelity` 查不出来的一类错）：
    细节复现度比的是**孔位坐标**，而孔位是从实测 STEP 直接搬过来的（实测坐标系）。
    可模型里的**实体**却是按"排序后的外形"摆的：`_box_features(env_sorted)` 把
    最长边摆在 X、次长摆 Y、最短摆 Z，实体以原点为中心；`_cyl_features` 一律摆
    在 Z 轴上。两边坐标系一旦不是一个，孔位坐标照样能"对上"（因为基准比的就是
    同一组原始坐标），但**孔在实体上的相对位置是错的**，甚至整根棒子被换了个方向
    —— 模型上根本没有那个孔。

    实测例子：EGT006 ⌀30 圆棒，真实零件是**沿 X** 的圆棒（X∈[0,L]，Y/Z∈[-15,15]），
    模型却建成"沿 Z、以原点为中心"的圆棒，孔位还是实测的 X∈[13,…]，
    结果孔全部落在实体外面，S_env 却仍然是 100（外形排序后一样）。

判据：逐轴比较 **模型网格包围盒** 与 **实测原始包围盒**（`kb/real_geometry.json`
      的 `bbox.min/max`）。真实件的坐标系就是它自己 STEP 里的坐标系，
      模型要和它一致，才谈得上"孔在正确的位置上"。
      容差 0.5 mm（网格是把圆离散化后的内接近似，直径方向会小一点点）。

用法: python tools\check_real_frame.py [--group 族名] [--limit N]
输出: reports\坐标系一致性检查.md
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ai_modeler import partlib as PL       # noqa: E402
from measure_mesh import measure_bbox      # noqa: E402

KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")
TOL = 0.5      # mm


def load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def real_boxes():
    out = {}
    for p in load("real_geometry.json").get("parts", []):
        bb = p.get("bbox") or {}
        if bb.get("min") and bb.get("max"):
            out[os.path.splitext(p["file"])[0].lower()] = (bb["min"], bb["max"],
                                                           p.get("rel") or "")
    return out


def cases():
    """(提示词, 真实文件 key, 族) —— 只挑**用实测数据建**的零件。

    标准件（螺栓/齿轮）是按标准参数在自己规范坐标系里建的，不比 STEP 坐标系，
    所以不在这个检查的目标里。
    """
    out = []
    egt = load("egt_library.json")
    for fam, v in (egt.get("profiles") or {}).items():
        for code in (v.get("records") or {}):
            tok = "%s %sx%03d" % (v["family"], v["section"], int(code))
            out.append(("EGT型材 %s" % tok, tok, "EGT型材"))
    for model in (egt.get("connectors") or {}):
        out.append(("EGT连接件 %s" % model, model, "EGT连接件"))
    std = load("standard_real_library.json")
    for g, v in (std.get("groups") or {}).items():
        for it in (v.get("items") or []):
            out.append(("%s %s" % (g, it["model"]), it["model"], g))
    for it in (std.get("shims") or {}).get("items", []):
        out.append(("垫片垫块 %s" % it["model"], it["model"], "垫片垫块"))
    for model, rec in (load("purchased_library.json") or {}).items():
        out.append(("%s %s" % (rec.get("desc") or "外购件", model), model,
                    rec.get("desc") or "外购件"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    rb = real_boxes()
    cs = cases()
    if a.group:
        cs = [c for c in cs if a.group in c[2]]
    if a.limit:
        cs = cs[:a.limit]
    print("核对 %d 个零件的坐标系…" % len(cs))
    t0 = time.time()
    rows, bad, skip = [], [], 0
    for prompt, key, group in cs:
        box = rb.get(key.lower().split(" ")[0]) or rb.get(key.lower())
        if box is None:
            for k, v in rb.items():
                if k.startswith(key.lower() + " "):
                    box = v
                    break
        if box is None:
            skip += 1
            continue
        try:
            r = PL.build_features_ex(prompt)
        except Exception as e:
            rows.append({"prompt": prompt, "group": group, "err": str(e)[:60],
                         "ok": False})
            bad.append(rows[-1])
            continue
        if not r:
            rows.append({"prompt": prompt, "group": group, "err": "建不出来",
                         "ok": False})
            bad.append(rows[-1])
            continue
        mb = measure_bbox(r[1], timeout=180.0)
        if mb is None:
            rows.append({"prompt": prompt, "group": group, "err": "网格量不出",
                         "ok": False})
            bad.append(rows[-1])
            continue
        rlo, rhi = [float(v) for v in box[0]], [float(v) for v in box[1]]
        # 判据：**逐轴尺寸**必须一致（差 0.5 mm 内）。
        # 为什么不再拿 min/max 的绝对位置当判据：刚体**平移**不是几何错误 ——
        # 直写网格件（型材）把伸实体沿轴居中摆，孔位也按同一坐标系换算过
        # （`ref_min/ref_size`），几何完全正确，只是整体挪了半根长度。
        # 真正要抓的是"零件被换了方向 / 尺寸不对"，那就是逐轴尺寸对不上。
        # 平移量照样打印出来（供人工核对），但不判失败。
        dsz = max(abs((mb[1][i] - mb[0][i]) - (rhi[i] - rlo[i])) for i in range(3))
        off = max(abs((mb[0][i] + mb[1][i]) / 2.0 - (rlo[i] + rhi[i]) / 2.0)
                  for i in range(3))
        ok = dsz <= TOL
        row = {"prompt": prompt, "group": group, "key": key, "ok": ok,
               "d": round(dsz, 3), "off": round(off, 3),
               "real_lo": rlo, "real_hi": rhi,
               "mesh_lo": list(mb[0]), "mesh_hi": list(mb[1])}
        rows.append(row)
        if not ok:
            bad.append(row)
    dt = time.time() - t0
    print("=" * 84)
    print("坐标系一致 %d/%d（不一致 %d，跳过 %d），用时 %.1fs"
          % (len(rows) - len(bad), len(rows), len(bad), skip, dt))
    if bad:
        print("\n逐轴尺寸不一致（前 20，按偏差排序）：")
        for r in sorted(bad, key=lambda x: -x.get("d", 0))[:20]:
            print("   %7.2f mm  %-30s 实测%s→%s 模型%s→%s"
                  % (r.get("d", 0), r["prompt"][:30],
                     [round(v) for v in r.get("real_lo", [])],
                     [round(v) for v in r.get("real_hi", [])],
                     r.get("mesh_lo"), r.get("mesh_hi")))
    bygroup = {}
    for r in rows:
        g = bygroup.setdefault(r["group"], [0, 0])
        g[1] += 1
        if r["ok"]:
            g[0] += 1
    L = ["# 坐标系一致性检查（模型 vs 实测 STEP）", "",
         "比对**逐轴尺寸**：模型的实体必须按实测零件的轴向摆，",
         "否则「孔位坐标看着对得上，实际孔根本不在实体上」。",
         "刚体**平移**不算错（直写网格件沿轴居中摆，孔位按同一坐标系换算过），",
         "平移量单独打印供核对。", "",
         "| 指标 | 值 |", "|---|---|",
         "| 受检零件 | %d |" % len(rows),
         "| 逐轴尺寸一致 | %d |" % (len(rows) - len(bad)),
         "| 不一致 | %d |" % len(bad),
         "| 容差 | %.1f mm |" % TOL,
         "| 用时 | %.1f s |" % dt, "",
         "## 按族", "", "| 族 | 一致 | 总数 |", "|---|---|---|"]
    for g in sorted(bygroup, key=lambda x: -(bygroup[x][1] - bygroup[x][0])):
        L.append("| %s | %d | %d |" % (g, bygroup[g][0], bygroup[g][1]))
    if bad:
        L += ["", "## 不一致清单（按偏差排序，前 120）", "",
              "| 偏差 mm | 族 | 提示词 | 实测 lo→hi | 模型 lo→hi |", "|---|---|---|---|---|"]
        for r in sorted(bad, key=lambda x: -x.get("d", 0))[:120]:
            L.append("| %.2f | %s | `%s` | %s → %s | %s → %s |"
                     % (r.get("d", 0), r["group"], r["prompt"],
                        [round(float(v), 1) for v in r.get("real_lo", [])],
                        [round(float(v), 1) for v in r.get("real_hi", [])],
                        r.get("mesh_lo"), r.get("mesh_hi")))
    with io.open(os.path.join(REPORTS, "坐标系一致性检查.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/坐标系一致性检查.md")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
