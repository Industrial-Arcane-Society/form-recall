# -*- coding: utf-8 -*-
r"""从 STEP 里提取**带位置的真实特征**：每个圆柱面的直径、轴线、轴向范围。

为什么需要这个（用户反馈："其他零件都只是一个大体形状，零件细节都没有"）：
    之前建模只用了三样东西 —— 包围盒（外形）、圆柱面直径指纹（有哪些直径）、
    实体/面计数。**位置信息全部丢掉了**，所以孔只能按"预算"平均摆在长边上，
    外形对得上、细节完全不对。

本工具把 STEP 里的圆柱面解析成可建模的孔特征：
    · 直径 d
    · 轴线方向（X/Y/Z，按主分量判定）
    · 轴线在垂直平面上的位置（x, y）
    · 轴向范围 z0~z1（由同一个圆柱上的两个端面圆推出）→ 能判**通孔/盲孔**
    · 该圆柱是否落在零件包络内（落在远处的是 CATIA 的构造几何，必须剔除）

⚠️ 坐标可信度：单实体零件的坐标是可信的（实测：螺钉/分气块/销座都对得上）；
   少部分**装配导出**的零件会把骨架坐标混进来 —— 那些圆柱的轴线位置会落在
   包络之外，被"包络内"这条规则自动剔掉，并单独计数上报，不静默使用。

用法:
    python tools\extract_step_features.py                 # 全部（读 kb/real_geometry.json 的清单）
    python tools\extract_step_features.py --only KMS-M15  # 只跑文件名含该串的
    python tools\extract_step_features.py --limit 20
输出: kb\real_features.json、reports\真实特征提取报告.md
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

# 直接复用 build_real_geometry 里的正则（保持一致，避免两套解析口径）
import build_real_geometry as B   # noqa: E402

KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")


def parse_features(path):
    """解析一个 STEP，返回圆柱特征列表 + 包络。"""
    cPts, dirs = {}, {}
    a2p_pt, a2p_ax = {}, {}
    cyls = {}                      # id -> (placement_id, radius)
    circ = {}                      # id -> (placement_id, radius)
    vtx = []
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            m = B._RE_ENTITY.match(line)
            if not m:
                continue
            eid, et, args = int(m.group(1)), m.group(2), m.group(3)
            if et == "CARTESIAN_POINT":
                m2 = B._RE_XYZ.search(args)
                if m2:
                    try:
                        p = tuple(float(m2.group(i + 1)) for i in range(3))
                    except ValueError:
                        continue
                    cPts[eid] = p
                    if "Vertex" in args:
                        vtx.append(p)
                        for i in range(3):
                            lo[i] = min(lo[i], p[i])
                            hi[i] = max(hi[i], p[i])
            elif et == "DIRECTION":
                m2 = B._RE_XYZ.search(args)
                if m2:
                    dirs[eid] = tuple(float(m2.group(i + 1)) for i in range(3))
            elif et == "AXIS2_PLACEMENT_3D":
                m2 = re.search(r"#(\d+)\s*(?:,\s*#(\d+))?", args)
                if m2:
                    a2p_pt[eid] = int(m2.group(1))
                    if m2.group(2):
                        a2p_ax[eid] = int(m2.group(2))
            elif et == "CYLINDRICAL_SURFACE":
                m2 = re.search(r"#(\d+)\s*,\s*(%s)" % B._NUM, args)
                if m2:
                    try:
                        cyls[eid] = (int(m2.group(1)), float(m2.group(2)))
                    except ValueError:
                        pass
            elif et == "CIRCLE":
                m2 = re.search(r"#(\d+)\s*,\s*(%s)" % B._NUM, args)
                if m2:
                    try:
                        circ[eid] = (int(m2.group(1)), float(m2.group(2)))
                    except ValueError:
                        pass
    if not vtx:
        return None
    span = [hi[i] - lo[i] for i in range(3)]
    #: 包络外扩一点（构造几何的轴线落得很远，这个阈值足够区分）
    pad = [max(2.0, 0.05 * span[i]) for i in range(3)]
    inside = lambda p: all(lo[i] - pad[i] <= p[i] <= hi[i] + pad[i]
                           for i in range(3))

    def axis_of(d):
        i = max(range(3), key=lambda k: abs(d[k]))
        return "XYZ"[i], i

    # ---- 先把圆按"轴线"归组，用来求每个圆柱的轴向范围 ----
    #  同一个圆柱的两个端面圆：半径相同、轴线共线
    cir_by_r = defaultdict(list)
    for pid, r in circ.values():
        pv = cPts.get(a2p_pt.get(pid, -1))
        if pv is None or not inside(pv):
            continue
        ax = dirs.get(a2p_ax.get(pid, -1), (0.0, 0.0, 1.0))
        letter, ai = axis_of(ax)
        cir_by_r[(round(r, 3), letter)].append(pv)

    feats = []
    dropped = 0
    for _cid, (pid, r) in cyls.items():
        pv = cPts.get(a2p_pt.get(pid, -1))
        if pv is None:
            dropped += 1
            continue
        if not inside(pv):
            dropped += 1
            continue
        ax = dirs.get(a2p_ax.get(pid, -1), (0.0, 0.0, 1.0))
        letter, ai = axis_of(ax)
        others = [k for k in range(3) if k != ai]
        # 轴向范围：找同半径、同轴线的端面圆
        zs = [p[ai] for p in cir_by_r.get((round(r, 3), letter), [])
              if all(abs(p[k] - pv[k]) < 0.05 for k in others)]
        z0, z1 = (min(zs), max(zs)) if len(zs) >= 2 else (None, None)
        feats.append({
            "d": round(2.0 * r, 3),
            "axis": letter,
            "u": round(pv[others[0]], 3),
            "v": round(pv[others[1]], 3),
            "w": round(pv[ai], 3),
            "z0": round(z0, 3) if z0 is not None else None,
            "z1": round(z1, 3) if z1 is not None else None,
        })
    # ---- 合并"同一物理圆柱被拆成多个圆柱面"的记账 ----
    # 实测：CATIA 导出的 STEP 里，一个完整圆柱常被写成 2 个半圆柱面
    # （M10x30 导出 12 个圆柱面，实际只有 6 个圆柱：头/杆/平垫内外/弹垫内外）。
    # 不合并的话，任何"数量比对"都会凭空差一倍 —— 那是我这边记账错了，不是模型错。
    def merge(cs):
        out = []
        for c in sorted(cs, key=lambda c: (-c["d"], c["axis"], c["u"], c["v"])):
            for o in out:
                if (o["axis"] == c["axis"] and abs(o["d"] - c["d"]) <= 0.01
                        and abs(o["u"] - c["u"]) <= 0.02
                        and abs(o["v"] - c["v"]) <= 0.02):
                    if o["z0"] is None or c["z0"] is None:
                        o["z0"] = o["z1"] = None
                    else:
                        o["z0"] = min(o["z0"], c["z0"])
                        o["z1"] = max(o["z1"], c["z1"])
                    o["faces"] = o.get("faces", 1) + 1
                    break
            else:
                cc = dict(c)
                cc["faces"] = 1
                out.append(cc)
        return out

    feats = merge(feats)
    fp = Counter(f["d"] for f in feats)
    return {
        "envelope": [round(span[i], 3) for i in range(3)],
        "bbox_min": [round(v, 3) for v in lo],
        "bbox_max": [round(v, 3) for v in hi],
        "cylinders": feats,
        "n_kept": len(feats),
        "n_dropped": dropped,
        "diameters": {("%g" % d): n for d, n in
                      sorted(fp.items(), key=lambda kv: -kv[1])},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--src", default=B.DEFAULT_IN)
    a = ap.parse_args()

    files = []
    for dp, _dn, fns in os.walk(a.src):
        for fn in fns:
            if fn.lower().endswith((".stp", ".step")):
                files.append(os.path.join(dp, fn))
    files.sort()
    if a.only:
        files = [f for f in files if a.only.lower() in f.lower()]
    if a.limit:
        files = files[:a.limit]
    print("解析 %d 个 STEP 的真实特征…" % len(files))

    out = {}
    t0 = time.time()
    for i, p in enumerate(files):
        if (i + 1) % 50 == 0:
            print("  %d/%d  (%.0fs)" % (i + 1, len(files), time.time() - t0))
        try:
            r = parse_features(p)
        except Exception as e:
            r = None
            print("  ✗ %s: %s" % (os.path.basename(p), e))
        if r:
            r["file"] = os.path.basename(p)
            r["rel"] = os.path.relpath(p, a.src)
            out[os.path.basename(p)] = r

    with io.open(os.path.join(KB, "real_features.json"), "w",
                 encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→ kb/real_features.json（%d 个零件，用时 %.0fs）"
          % (len(out), time.time() - t0))

    # ---- 报告：抽几个族看细节抓得对不对 ----
    L = ["# 真实特征提取报告", "",
         "从 %d 个 STEP 里提取**带位置**的圆柱特征（直径 / 轴向 / 位置 / 轴向范围）。"
         % len(out), "",
         "## 为什么做这个", "",
         "用户反馈：除螺钉外，其它零件「只是一个大体形状，零件细节都没有」。",
         "根因是之前建模只用了包围盒 + 直径指纹，**位置信息全丢了**，",
         "孔只能按预算平均摆在长边上。本工具把位置补回来。", ""]
    fams = defaultdict(list)
    for k, v in out.items():
        fams["/".join(v["rel"].split("\\")[:-1]) or "根"].append(v)
    for fam in sorted(fams, key=lambda x: -len(fams[x]))[:14]:
        rs = fams[fam]
        kept = sum(r["n_kept"] for r in rs)
        drop = sum(r["n_dropped"] for r in rs)
        L.append("### %s（%d 件）" % (fam, len(rs)))
        L.append("")
        L.append("- 提取到圆柱特征 %d 个；因落在包络外被剔除 %d 个"
                 % (kept, drop))
        for r in sorted(rs, key=lambda r: r["file"])[:4]:
            hs = ", ".join("⌀%g@(%g,%g)%s" % (
                f["d"], f["u"], f["v"],
                "" if f["z0"] is None else
                ("通" if abs((f["z1"] or 0) - (f["z0"] or 0)) > 1.0 else "浅"))
                for f in r["cylinders"][:6])
            L.append("- `%s`：%d 个 → %s" % (r["file"], r["n_kept"], hs))
        L.append("")
    with io.open(os.path.join(REPORTS, "真实特征提取报告.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports/真实特征提取报告.md")


if __name__ == "__main__":
    main()
