# -*- coding: utf-8 -*-
r"""细节复现度基准：把 AI 建出来的模型与**真实 STEP 特征**逐项对比。

用户的要求（原话）：「螺丝的复现度有 95%，其他零件的复现都只是一个大体形状，
零件细节都没有……相似度不在 95% 以上的就进行修复，要求全部模型都能够用
ai 提示词建模出来的与实际建模的相似度有 95% 以上。」

所以这里定义一套**可算、可复核**的相似度，三个分量：

  1. **外形 S_env**（权重 0.30）
     建出来的网格外形 vs STEP 实测外形，逐轴公差 0.1 mm。

  2. **孔特征 S_hole**（权重 0.50）—— **这一项才是"细节"**
     把真实 STEP 里每个圆柱面解析成「直径 + 轴线 + 位置」（见
     `tools/extract_step_features.py`），再看建出来的模型里有没有对应的孔：
     位置容差 0.5 mm、直径容差 0.2 mm、轴一致。
     打分用 F1 = 2PR/(P+R)：
       · 召回 R：真实有、模型也有的比例（漏孔要扣分）
       · 精确 P：模型有、真实也有的比例（**编造出来的孔同样扣分**）

  3. **圆柱面数量 S_count**（权重 0.20）
     真实圆柱面总数 vs 模型里的数量，取 min/max 的比值。
     型材那种上千个齿纹面的零件在这一项上会低 —— 这是真相，不掩盖。

用法:
    python tools\bench_detail_fidelity.py                 # 全部
    python tools\bench_detail_fidelity.py --group 分气块    # 只跑某族
    python tools\bench_detail_fidelity.py --worst 30       # 只看最差 30 个
输出: reports\细节复现度.md、kb\detail_fidelity.json
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
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ai_modeler import partlib as PL       # noqa: E402
from ai_modeler import reallib as RL       # noqa: E402
from measure_mesh import measure_bbox      # noqa: E402

KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

TOL_ENV = 0.10      # mm 外形逐轴公差
TOL_POS = 0.50      # mm 孔位公差
TOL_DIA = 0.20      # mm 孔径公差
W_ENV, W_HOLE, W_CNT = 0.30, 0.50, 0.20
#: 小于这个直径的圆柱面**不算孔**。
#: 实测依据：支座(铝) LZ01001001_H100 的 89 个圆柱面里有 45 个是 ⌀0.4/⌀0.8
#: 这类面（倒圆/过渡面），把它们当孔去比对既比不出、也不该建。
MIN_HOLE_D = 2.0


def load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def _axis_of_dir(c):
    """把方向向量判成 X/Y/Z 主分量。"""
    i = max(range(3), key=lambda k: abs(c[k]))
    return "XYZ"[i]


def built_features(fs):
    """从**建出来的特征表**里抽出圆柱特征（直径/轴/位置）与外形。"""
    cyls = []
    for f in fs or []:
        pr = f.params or {}
        if f.kind == "real_solid":
            # 实测 B-Rep 复刻：模型网格**就是**真实零件，里面的孔就是实测孔。
            # 所以这里按实测圆柱面记账（否则会被算成"一个孔都没有"）。
            from ai_modeler import realsolid as _RS
            for c in _RS.cylinders(str(pr.get("model") or "")):
                if float(c["d"]) < MIN_HOLE_D:
                    continue
                ha = c["axis"]
                ai2 = "XYZ".index(ha)
                oth = [k for k in range(3) if k != ai2]
                cc = [0.0, 0.0, 0.0]
                cc[oth[0]] = float(c["u"])
                cc[oth[1]] = float(c["v"])
                cyls.append({"d": float(c["d"]), "axis": ha, "w": None, "h": None,
                             "c": cc, "op": f.op})
            continue

        if f.kind == "extrude_holes":
            # 直写网格（B 计划）：一个特征里带着**全部**孔，逐孔抽出来比对。
            # ⚠ 这里的孔坐标是**实测坐标系**的原始值，必须先按 `ref_min/ref_size`
            #   换算到"以拉伸体中心为原点"的模型坐标系，才能和别的特征一起比 ——
            #   不换算的话 289 个型材的孔位会整体错开半根长度（实测命中 1/13）。
            rmin, rsiz = pr.get("ref_min"), pr.get("ref_size")
            for h in (pr.get("holes") or []):
                ha = (h.get("axis") or "Z").upper()
                ai2 = "XYZ".index(ha)
                oth = [k for k in range(3) if k != ai2]
                u = float(h.get("u") or 0.0)
                v = float(h.get("v") or 0.0)
                if rmin and rsiz:
                    u -= float(rmin[oth[0]]) + float(rsiz[oth[0]]) / 2.0
                    v -= float(rmin[oth[1]]) + float(rsiz[oth[1]]) / 2.0
                c = [0.0, 0.0, 0.0]
                c[oth[0]] = u
                c[oth[1]] = v
                cyls.append({"d": float(h.get("d") or 0.0), "axis": ha,
                             "w": None, "h": None, "c": c, "op": f.op})
            continue
        axis = (pr.get("axis") or "Z").upper()
        ai = "XYZ".index(axis) if axis in "XYZ" else 2
        if f.kind == "cylinder":
            d = 2.0 * float(pr.get("radius") or 0.0)
            c = pr.get("center") or (0, 0, 0)
            h = float(pr.get("height") or 0.0)
            off = float(c[ai]) if pr.get("base") else float(c[ai]) - h / 2.0
            cyls.append({"d": d, "axis": axis, "w": off, "h": h,
                         "c": [float(c[0]), float(c[1]), float(c[2])],
                         "op": f.op})
        elif f.kind == "hole":
            d = float(pr.get("diameter") or 0.0)
            # 孔的位置：pos=center 时取 x/y；pcd 时按分度圆展开
            pos = pr.get("pos", "center")
            pts = []
            if pos == "pcd":
                n = max(2, int(pr.get("count", 4)))
                rr = float(pr.get("pcd", 60.0)) / 2.0
                pts = [(rr * math.cos(2 * math.pi * i / n),
                        rr * math.sin(2 * math.pi * i / n)) for i in range(n)]
            else:
                pts = [(float(pr.get("x", 0.0)), float(pr.get("y", 0.0)))]
            for (x, y) in pts:
                c = [0.0, 0.0, 0.0]
                others = [k for k in range(3) if k != ai]
                c[others[0]] = x
                c[others[1]] = y
                cyls.append({"d": d, "axis": axis, "w": None, "h": None,
                             "c": c, "op": f.op})
    return cyls


def _key(f, ai):
    others = [k for k in range(3) if k != ai]
    return (round(f["c"][others[0]], 2), round(f["c"][others[1]], 2))


def _merge_cyls(cyls):
    """把"同一物理圆柱被拆成多个面"的记账合并成一条。

    ⚠ 这一步必须在**两边都做**，否则比对永远不公平：CATIA 导出的 STEP 会把
    一个完整圆柱写成 2 个（有时 4 个）圆柱面 —— 实测 M10x30 的 12 个圆柱面
    其实只有 5 个圆柱（平垫外/头+弹垫外/平垫内/弹垫内/杆）。
    真实侧已在提取阶段合并；模型侧（头 + 弹垫外同为 ⌀16）也要同样合并，
    否则会把"建模正确"误判成"多了一个孔"。

    ⚠⚠ 比较的坐标必须是**垂直于轴线的那两个**。第一版固定比 c[0]/c[1]，
    对 X 轴孔（垂直面是 Y-Z）就会把沿长度方向排开的不同孔误合并 ——
    实测把 48 个孔合并成了 17 个，直接把复现度算低。
    """
    out = []
    for c in cyls:
        ai = "XYZ".index(c["axis"])
        others = [k for k in range(3) if k != ai]
        for o in out:
            if (o["axis"] != c["axis"] or abs(o["d"] - c["d"]) > 0.01):
                continue
            if math.hypot(o["c"][others[0]] - c["c"][others[0]],
                          o["c"][others[1]] - c["c"][others[1]]) <= 0.02:
                o["n"] = o.get("n", 1) + 1
                break
        else:
            cc = dict(c)
            cc["n"] = 1
            out.append(cc)
    return out


def match_holes(real, built, real_c=(0.0, 0.0, 0.0), model_c=(0.0, 0.0, 0.0)):
    """位置+直径+轴 三者都对上才算命中；返回 (P, R, F1, 命中数, 漏, 多)。

    `real_c` / `model_c` 是**真实零件**与**建出来的实体**各自包围盒的中心
    （逐轴）。孔位一律换算成"相对自己零件中心"的坐标再比：
    这样刚体平移不影响分数（模型摆在哪个坐标系不重要），但
    "零件被换了方向 / 孔落在实体外面"会被如实扣分。
    """
    def rel(c, ai, others, cen):
        return (c[others[0]] - cen[others[0]], c[others[1]] - cen[others[1]])

    ridx = []
    for r in real:
        ai = "XYZ".index(r["axis"])
        others = [k for k in range(3) if k != ai]
        ru = float(r["u"]) - real_c[others[0]]
        rv = float(r["v"]) - real_c[others[1]]
        ridx.append((r["axis"], ru, rv, r["d"]))
    bidx = []
    for b in built:
        ai = "XYZ".index(b["axis"])
        others = [k for k in range(3) if k != ai]
        bidx.append((b["axis"], b["c"][others[0]] - model_c[others[0]],
                     b["c"][others[1]] - model_c[others[1]], b["d"]))
    used_b = [False] * len(bidx)
    hit = 0
    for (ra, ru, rv, rd) in ridx:
        best, bestd = -1, 1e9
        for j, (ba, bu, bv, bd) in enumerate(bidx):
            if used_b[j] or ba != ra or abs(bd - rd) > TOL_DIA:
                continue
            dd = math.hypot(bu - ru, bv - rv)
            if dd <= TOL_POS and dd < bestd:
                best, bestd = j, dd
        if best >= 0:
            used_b[best] = True
            hit += 1
    R = hit / float(len(ridx)) if ridx else 1.0
    P = hit / float(len(bidx)) if bidx else (1.0 if not ridx else 0.0)
    F1 = (2 * P * R / (P + R)) if (P + R) > 0 else 0.0
    return P, R, F1, hit, len(ridx) - hit, len(bidx) - hit


def score_one(prompt, real_feat, real_env, real_box=None):
    out = {"prompt": prompt}
    try:
        r = PL.build_features_ex(prompt)
    except Exception as e:
        out.update({"error": str(e)[:80], "S": 0.0})
        return out
    if not r:
        out.update({"error": "建不出来", "S": 0.0})
        return out
    _cat, fs, _bb, _notes = r
    cyls = _merge_cyls(built_features(fs))
    # 外形：量**建出来的网格**的逐轴包围盒（不排序，后面还要用它做坐标系对齐）
    mb = measure_bbox(fs, timeout=180.0)   # 多孔件慢，给足
    mesh = tuple(sorted((mb[1][i] - mb[0][i] for i in range(3)), reverse=True)) if mb else None
    out["mesh_env"] = mesh
    if mesh and real_env:
        s_env = 0.0
        for i in range(3):
            d = abs(mesh[i] - real_env[i])
            s_env += 1.0 if d <= TOL_ENV else max(0.0, 1.0 - d / max(real_env[i], 1.0))
        s_env /= 3.0
    else:
        s_env = 0.0
    #: 坐标系对齐（**这一版新加的，堵住一个假通过**）：
    #  孔位来自实测坐标系，模型实体却可能摆在别处（典型：`_box_features` 按
    #  "最长边→X" 摆、圆棒一律摆 Z 轴、型材沿轴居中）。只比原始坐标时，
    #  哪怕孔整个落在实体外面、"位置"也照样对得上。
    #  判据改成比**相对零件自身包围盒中心**的坐标 —— 刚体平移不再影响分数，
    #  但"孔不在零件上/零件被换了方向"会立刻暴露。
    #  用**中心**而不是 min：网格把圆离散成内接多边形，lo/hi 会同时内缩，
    #  中心不受离散化影响（半径方向误差 ≤0.5 mm，正好卡在孔位公差上）。
    model_c = [0.0, 0.0, 0.0]
    if mb:
        model_c = [(mb[0][i] + mb[1][i]) / 2.0 for i in range(3)]
    real_c = [0.0, 0.0, 0.0]
    if real_box:
        real_c = [(float(real_box[0][i]) + float(real_box[1][i])) / 2.0
                  for i in range(3)]
    # ⚠ `real_solid` 件（实测 B-Rep 复刻）的模型坐标系**就是实测坐标系**，
    #   不能再拿网格中心减一次：kb 的 bbox 含"圆的极值"、比三角化外形略大，
    #   两个中心能差 2 mm > 0.5 mm 孔位公差 → 实测 104 个孔全部被判"没对上"。
    if any(getattr(f, "kind", "") == "real_solid" for f in (fs or [])):
        model_c = list(real_c)
    # 孔：小于 MIN_HOLE_D 的圆柱面是倒圆/过渡面，不算孔（两边同一口径）
    real_cyls = [c for c in (real_feat.get("cylinders") or [])
                 if float(c["d"]) >= MIN_HOLE_D]
    cyls = [c for c in cyls if float(c["d"]) >= MIN_HOLE_D]
    P, R, F1, hit, miss, extra = match_holes(real_cyls, cyls, real_c, model_c)
    # 数量
    nr, nb = len(real_cyls), len(cyls)
    s_cnt = (min(nr, nb) / float(max(nr, nb))) if max(nr, nb) else 1.0
    S = W_ENV * s_env + W_HOLE * F1 + W_CNT * s_cnt
    out.update({"S": round(100.0 * S, 1), "S_env": round(100 * s_env, 1),
                "S_hole": round(100 * F1, 1), "S_cnt": round(100 * s_cnt, 1),
                "P": round(100 * P, 1), "R": round(100 * R, 1),
                "n_real": nr, "n_built": nb, "hit": hit,
                "miss": miss, "extra": extra,
                "model_c": [round(v, 2) for v in model_c],
                "real_c": [round(v, 2) for v in real_c],
                "built_dias": dict(Counter("%g" % c["d"] for c in cyls)
                                   .most_common(8))})
    return out


def cases():
    """(提示词, 真实特征 key, 族名)"""
    out = []
    feats = load("real_features.json")
    for model, rec in load("bolt_library.json").get("bolts", {}).items():
        out.append(("内六角螺栓 %s" % model, model, "内六角螺钉"))
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
    ap.add_argument("--worst", type=int, default=25)
    a = ap.parse_args()

    feats = load("real_features.json")
    fmap = {}
    for k, v in feats.items():
        fmap[os.path.splitext(k)[0].lower()] = v
    # 真实**外形**以 kb/real_geometry.json 的"顶点 ∪ 圆弧极值"为准：
    # real_features 里的包络是**顶点跨度**，对圆零件会偏小
    # （吊环顶点跨度 35×13.98×89.5，实际 63×35×89.5），两边口径必须一致，
    # 否则会拿"偏小的真实值"去要求模型，白扣外形分。
    rg_env = {}
    rg_box = {}
    for p2 in load("real_geometry.json").get("parts", []):
        bb = p2.get("bbox") or {}
        if bb.get("size"):
            k2 = os.path.splitext(p2["file"])[0].lower()
            rg_env[k2] = bb["size"]
            if bb.get("min") and bb.get("max"):
                rg_box[k2] = (bb["min"], bb["max"])
    cs = cases()
    if a.group:
        cs = [c for c in cs if a.group in c[2]]
    print("对比 %d 个零件（真实特征取自 %d 个 STEP）" % (len(cs), len(feats)))

    t0 = time.time()
    rows = []
    skipped = 0
    for i, (prompt, key, group) in enumerate(cs):
        if (i + 1) % 50 == 0:
            print("  %d/%d (%.0fs)" % (i + 1, len(cs), time.time() - t0))
        rf = None
        for cand in (key, key.replace(" ", ""), key.replace(" ", "_")):
            rf = fmap.get(cand.lower())
            if rf:
                break
        if rf is None:
            rf = fmap.get(key.lower())
        if rf is None:
            # 连接件的文件名带后缀（`EGT013 300x450 SW80.stp`），型号只有 `EGT013`
            # —— 不做前缀匹配的话 73 个连接件全都"匹配不到真实特征"，
            # 等于这一族根本没被测。取前缀命中的第一个（同型号只有一份实测）。
            kl = key.lower() + " "
            for k2, v2 in fmap.items():
                if k2.startswith(kl):
                    rf = v2
                    break
        if rf is None:
            skipped += 1
            continue
        env = rg_env.get(key.lower()) or sorted(rf["envelope"], reverse=True)
        box = rg_box.get(key.lower())
        if box is None:
            for k2, v2 in rg_box.items():
                if k2.startswith(key.lower() + " "):
                    box = v2
                    break
        row = score_one(prompt, rf, sorted(env, reverse=True), box)
        row.update({"group": group, "key": key})
        rows.append(row)

    ok = [r for r in rows if "S" in r]
    worst = sorted(ok, key=lambda r: r["S"])
    by = defaultdict(list)
    for r in ok:
        by[r["group"]].append(r["S"])
    avg = sum(r["S"] for r in ok) / len(ok) if ok else 0.0
    ge95 = sum(1 for r in ok if r["S"] >= 95.0)
    print("\n" + "=" * 84)
    print("细节复现度：平均 **%.1f%%**，≥95%% 的有 %d/%d，未匹配到真实特征的 %d 条"
          % (avg, ge95, len(ok), skipped))
    print("\n按族（平均分 / ≥95 比例）：")
    for g in sorted(by, key=lambda x: -len(by[x])):
        v = by[g]
        print("   %-14s %5.1f%%   %3d/%3d" % (g, sum(v) / len(v),
                                              sum(1 for x in v if x >= 95), len(v)))
    print("\n最差 %d 个：" % min(a.worst, len(worst)))
    for r in worst[:a.worst]:
        print("   %5.1f%%  外形%5.1f 孔%5.1f 数%5.1f  (真实孔%4d 模型%4d 命中%4d 漏%4d 多%4d)  %s"
              % (r["S"], r["S_env"], r["S_hole"], r["S_cnt"], r["n_real"],
                 r["n_built"], r["hit"], r["miss"], r["extra"], r["prompt"][:34]))

    with io.open(os.path.join(KB, "detail_fidelity.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"avg": round(avg, 2), "ge95": ge95, "total": len(ok),
                   "tol": {"env": TOL_ENV, "pos": TOL_POS, "dia": TOL_DIA},
                   "weights": {"env": W_ENV, "hole": W_HOLE, "cnt": W_CNT},
                   "by_group": {g: {"avg": round(sum(v) / len(v), 2),
                                    "n": len(v),
                                    "ge95": sum(1 for x in v if x >= 95)}
                                for g, v in by.items()},
                   "rows": ok}, f, ensure_ascii=False, indent=1)

    L = ["# 细节复现度（AI 建模 vs 真实 STEP 特征）", "",
         "用户要求：**全部模型的相似度 ≥95%**。", "",
         "## 相似度怎么算的", "",
         "| 分量 | 权重 | 判据 |", "|---|---|---|",
         "| 外形 S_env | %.2f | 建出的**网格**外形 vs STEP 实测外形，逐轴公差 %.2f mm |"
         % (W_ENV, TOL_ENV),
         "| **孔特征 S_hole** | %.2f | 真实每个圆柱面（直径+轴线+**位置**）在模型里"
         "有没有对应的；位置公差 %.2f mm、孔径公差 %.2f mm；取 F1（漏孔与**编造孔**"
         "都扣分）。位置一律换算成**相对零件自身包围盒中心**的坐标再比 |"
         % (W_HOLE, TOL_POS, TOL_DIA),
         "| 圆柱面数量 S_cnt | %.2f | min/max 比值 |" % W_CNT, "",
         "> 这一版之所以要重做验收：旧判据只比**包围盒**，所以「大体形状对、细节没有」"
         "完全测不出来。孔特征这一项占一半权重，就是冲着「细节」去的。", "",
         "> 坐标系对齐（本轮补的口径）：孔位来自实测坐标系，模型实体却可能摆在别处"
         "（典型：方块按「最长边→X」摆、圆棒一律摆 Z 轴、型材沿轴居中）。只比原始"
         "坐标时，**孔整个落在实体外面也照样算命中** —— EGT006 ⌀30 圆棒就是这么"
         "「外形 100、孔位 93」的。现在两边都换成「相对自身包围盒中心」再比："
         "刚体平移不影响分数，但零件被换方向 / 孔不在零件上会立刻扣分。", "",
         "## 总体", "",
         "| 指标 | 值 |", "|---|---|",
         "| 平均相似度 | **%.1f%%** |" % avg,
         "| ≥95%% 的零件 | **%d / %d** |" % (ge95, len(ok)),
         "| 未匹配到真实特征 | %d |" % skipped, "",
         "## 按族", "", "| 族 | 平均 | ≥95% | 件数 |", "|---|---|---|---|"]
    for g in sorted(by, key=lambda x: -len(by[x])):
        v = by[g]
        L.append("| %s | %.1f%% | %d | %d |"
                 % (g, sum(v) / len(v), sum(1 for x in v if x >= 95), len(v)))
    L += ["", "## 最差 %d 个" % min(a.worst, len(worst)), "",
          "| 相似度 | 外形 | 孔 | 数量 | 真实圆柱 | 模型圆柱 | 命中 | 漏 | 多 | 提示词 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in worst[:a.worst]:
        L.append("| %.1f%% | %.1f | %.1f | %.1f | %d | %d | %d | %d | %d | `%s` |"
                 % (r["S"], r["S_env"], r["S_hole"], r["S_cnt"], r["n_real"],
                    r["n_built"], r["hit"], r["miss"], r["extra"], r["prompt"]))
    with io.open(os.path.join(REPORTS, "细节复现度.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/细节复现度.md、kb/detail_fidelity.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
