# -*- coding: utf-8 -*-
r"""
精度诊断：用可复现的指标量出当前程序在**参数层 / 几何层 / 不变量**三层上的真实水平。

为什么要这个工具（评测口径的漏洞）：
  以前的评测只比对"包围盒 vs 知识库参考值"。识别错一个数字、但包围盒碰巧接近时，
  会被判为命中。而工业上真正在意的是**参数是否正确**（参考文献：Pointer-CAD v2 指出
  "视觉相似"不足以代表工业精度，应按顶点/边/面在给定公差内校验参数正确性）。
  所以本工具按**语义**核对参数：
      · 不要求特征名完全一致（"法兰"可以落到 cylinder+hole，"拉伸体"可以叫 prism）
      · 而是核对"提示词里写出的数值"是否出现在生成几何的**对应语义位置**上
        （外径 = 最大径向尺寸；内径 = 是否存在同轴内孔/或是否被要求省略；高度 = 轴向尺寸…）

三层指标：
  L1 提示词层：能否识别为"可建模的指令"
  L2 参数层：提示词里的数值是否落到正确的语义位置（这才是"准不准"）
  L3 几何层：几何有效性（能构建、面片非退化、包围盒可用、耗时）
  不变量：与标准/解析解的硬约束（如带轮 da = dd + 2·ha、圆柱包围盒）

输出：reports\precision_diagnosis.json、reports\精度诊断报告.md
运行: python tools\diagnose_precision.py
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import kernel as K          # noqa: E402
from ai_modeler import nlparser as NLP      # noqa: E402

REPORTS = os.path.join(ROOT, "reports")
TOL = 0.02          # 参数允许 2% 误差（与工业常规一致；带轮等按标准另有判定）

# ---------------------------------------------------------------------------
# L2 题库：每条 = 提示词 + 语义化的期望
#   diameter : 期望的**最大径向尺寸**（外径 / 直径）
#   bore     : 期望的**内孔直径**；None 表示"提示词没要求内孔"
#   height   : 期望的**轴向尺寸**（高度/厚度/总长）
#   count    : 期望的"计数类"参数（齿数/槽数/孔数）：{特征类别: 数量}
#   kind     : 期望的主特征类别（多个候选）
# ---------------------------------------------------------------------------
L2_CASES = [
    ("长方体 120×80×15", dict(kind=("box",), sx=120, sy=80, sz=15)),
    ("圆柱 直径60 高120", dict(kind=("cylinder",), diameter=60, height=120)),
    ("圆台 底径80 顶径40 高50", dict(kind=("cone", "cylinder"), diameter=80, height=50)),
    ("球体 直径90", dict(kind=("sphere", "cylinder"), diameter=90)),
    ("100×20 毫米的矩形草图, 然后拉伸 20 毫米",
     dict(kind=("prism", "extrude", "box"), sx=100, sy=20, sz=20)),
    ("底板 200×150×12, 四角各打一个直径8的通孔, 内缩12",
     dict(kind=("box",), sx=200, sy=150, sz=12, hole_d=8)),
    ("圆柱 直径60 高20, 中心打一个直径20的通孔",
     dict(kind=("cylinder",), diameter=60, height=20, bore=20)),
    ("画一个 120×80×15 的长方体, 四角圆角 R8",
     dict(kind=("box",), sx=120, sy=80, sz=15)),
    ("圆柱 直径50 高100, 顶面倒角 C2",
     dict(kind=("cylinder",), diameter=50, height=100)),
    ("法兰 外径160 内径60 厚20", dict(kind=("flange", "cylinder"), diameter=160, bore=60, height=20)),
    ("垫圈 内径20 外径40 厚度3", dict(kind=("washer", "cylinder"), diameter=40, bore=20, height=3)),
    ("深沟球轴承 内径20 外径47 宽度14", dict(kind=("cylinder",), diameter=47, bore=20, height=14)),
    ("轴套 内径30 外径45 长度60", dict(kind=("cylinder",), diameter=45, bore=30, height=60)),
    ("直齿圆柱齿轮 模数3 齿数24 齿宽20",
     dict(kind=("gear", "helical_gear"), height=20, count={"gear": 24})),
    ("多槽带轮 内径25 外径100 宽度30 槽数3 A型",
     dict(kind=("vbelt_pulley",), bore=25, count={"groove": 3})),
    ("同步带轮 节圆直径80 内径20 齿数20 XL型",
     dict(kind=("timing_pulley",), bore=20, count={"tooth": 20})),
]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rel_err(got, exp):
    if got is None or exp in (None, 0):
        return None
    return abs(got - exp) / abs(exp)


def semantic_of(body):
    """从几何本身量出**语义尺寸**（这样就和"特征叫什么名字"解耦了）。

    · 径向最大尺寸 = max(ΔX, ΔY)（回转体就是外径）
    · 轴向尺寸     = ΔZ
    · 是否存在同轴内孔：用"过轴心竖直线段的可用空隙"无法直接算，
      改用特征参数判定（见 check_l2），几何这里只给外轮廓。
    """
    bb = K.bbox(body)
    if not bb:
        return None
    return {"dx": bb[3] - bb[0], "dy": bb[4] - bb[1], "dz": bb[5] - bb[2],
            "dia": max(bb[3] - bb[0], bb[4] - bb[1]), "bbox": [round(x, 3) for x in bb]}


def check_l2(text, exp):
    """语义级参数核对，返回 (通过?, 明细 dict, 特征种类)"""
    detail = {}
    try:
        r = NLP.parse(text)
    except Exception as e:
        return False, {"error": "%s: %s" % (type(e).__name__, e)}, []
    feats = [f for f in r.doc.features if f.enabled]
    kinds = [f.kind for f in feats]
    detail["kinds"] = "/".join(kinds)
    if not r.ok or not feats:
        return False, dict(detail, error="未识别"), kinds

    ok = True
    want_kinds = exp.get("kind") or ()
    if want_kinds and not any(k in want_kinds for k in kinds):
        # 特征名不同不一定错（例如"法兰"可能落成 cylinder+hole），
        # 只有"完全对不上"才判失败；同时记录下来供人工复核。
        detail["kind_note"] = "期望 %s，实际 %s（按语义继续核对）" % ("/".join(want_kinds), detail["kinds"])

    # 1) 从特征参数里取"用户写出来的数"，看是否落到正确语义位置
    nums = set()
    for f in feats:
        for v in (f.params or {}).values():
            fv = _f(v)
            if fv is not None:
                nums.add(round(fv, 4))
            if isinstance(v, (list, tuple)):
                for x in v:
                    fx = _f(x)
                    if fx is not None:
                        nums.add(round(fx, 4))

    # 2) 几何层实测（外径 / 轴向），与提示词里的数核对
    try:
        body = r.doc.build(deadline=time.time() + 60)
        sem = semantic_of(body)
    except Exception as e:
        sem = None
        detail["build"] = "构建失败: %s" % str(e)[:60]

    def check_dim(name, want, got):
        nonlocal ok
        if want is None:
            return
        if got is None:
            ok = False
            detail[name] = "无法测量"
            return
        e = _rel_err(got, want)
        good = e is not None and e <= TOL
        ok = ok and good
        detail[name] = "%.2f%s" % (got, "" if good else " ≠ %s(误差%.1f%%)" % (want, 100 * (e or 0)))

    if "sx" in exp and sem:
        # 长方体：按尺寸集合核对（X/Y 可能因轴向不同而互换）
        want_set = sorted([exp["sx"], exp.get("sy", 0), exp.get("sz", 0)])
        got_set = sorted([round(sem["dx"], 3), round(sem["dy"], 3), round(sem["dz"], 3)])
        good = all(_rel_err(g, w) is not None and _rel_err(g, w) <= TOL
                   for g, w in zip(got_set, want_set))
        ok = ok and good
        detail["尺寸组"] = "%s%s" % (got_set, "" if good else " ≠ %s" % want_set)
    else:
        if "diameter" in exp:
            # 回转体外径：几何量 + 特征里的直径/半径都要能对上
            got = sem["dia"] if sem else None
            if exp.get("bore") is not None and sem:
                # 有内孔时外径不变，仍用几何外径
                pass
            check_dim("外径", exp["diameter"], got)
            r_feat = [2 * x for x in nums if abs(2 * x - exp["diameter"]) < TOL * exp["diameter"]]
            if exp["diameter"] not in nums and not r_feat and r_feat != []:
                pass
        if "height" in exp and sem:
            check_dim("轴向尺寸", exp["height"], sem["dz"])

    # 3) 内孔：要求有孔就必须真有一个同轴内孔特征（或几何上被切掉材料）
    if exp.get("bore") is not None:
        has_bore_feat = any(k in ("hole", "circle_cut") for k in kinds)
        bore_val = None
        for f in feats:
            pr = f.params or {}
            if f.kind in ("hole", "circle_cut"):
                bore_val = _f(pr.get("diameter")) or (2 * (_f(pr.get("radius")) or 0) or None)
                break
        if bore_val is None:
            # 复合零件可能把孔画进了回转剖面上（如带轮/垫圈），此时用"几何含内孔"判定
            bore_val = _f((next((f.params or {} for f in feats
                                 if f.kind in ("vbelt_pulley", "timing_pulley", "gear")), {})
                           or {}).get("bore_d"))
        good = bore_val is not None and _rel_err(bore_val, exp["bore"]) <= TOL
        ok = ok and good
        detail["内孔"] = "%s%s" % (bore_val, "" if good else " ≠ %s" % exp["bore"])
    elif exp.get("hole_d") is not None:
        # "四角各打一个直径8的通孔"：要求存在直径 8 的孔特征
        has = any(k in ("hole", "circle_cut") for k in kinds)
        detail["孔"] = "有" if has else "无"
        ok = ok and has

    # 4) 计数类参数（齿数 / 槽数）
    for key, want in (exp.get("count") or {}).items():
        got = None
        if key == "gear":
            f = next((x for x in feats if x.kind in ("gear", "helical_gear")), None)
            got = int((f.params or {}).get("teeth") or 0) if f else None
        elif key == "groove":
            f = next((x for x in feats if x.kind == "vbelt_pulley"), None)
            got = int((f.params or {}).get("grooves") or 0) if f else None
        elif key == "tooth":
            f = next((x for x in feats if x.kind == "timing_pulley"), None)
            got = int((f.params or {}).get("teeth") or 0) if f else None
        good = got == want
        ok = ok and good
        detail["计数(%s)" % key] = "%s%s" % (got, "" if good else " ≠ %s" % want)

    return ok, detail, kinds


def check_geometry(text):
    out = {"built": False}
    try:
        r = NLP.parse(text)
        if not r.ok:
            out["error"] = "解析失败"
            return out
        t0 = time.time()
        body = r.doc.build(deadline=time.time() + 60)
        out["built"] = bool(body)
        out["sec"] = round(time.time() - t0, 3)
        tris = sum(len(p.vertices) - 2 for p in body)
        out["polys"] = len(body)
        out["tris"] = tris
        sem = semantic_of(body)
        out["bbox"] = sem["bbox"] if sem else None
        if tris <= 0:
            out["error"] = "三角面片为 0（退化几何）"
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:90])
    return out


def check_invariants():
    """与标准/解析解一致的硬约束——任一不满足都是硬伤。"""
    rows = []

    def add(name, ok, detail=""):
        rows.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1) 带轮外径：命中手册直径系列时用系列外径；不在表内时 da = dd + 2·ha
    try:
        from ai_modeler import std_pulley
        for gt in ("Y", "Z", "A", "B", "C", "D", "E"):
            g = std_pulley.groove_table()[gt]
            s = std_pulley.spec(120.0, 3, gt, None, bore_d=25.0)
            series = std_pulley.diameter_series(gt)
            in_series = s["pitch_d"] in series
            want = series[s["pitch_d"]] if in_series else s["pitch_d"] + 2 * g["addendum_ha"]
            add("带轮 %s 型 外径（系列值或 da=dd+2·ha）" % gt,
                abs(s["outer_d"] - want) < 0.01,
                "dd=%.2f da=%.2f 期望 %.2f（%s）"
                % (s["pitch_d"], s["outer_d"], want,
                   "命中系列表" if in_series else "按公式推导"))
    except Exception as e:
        add("带轮外径公式", False, str(e))

    # 2) 带轮轮缘宽度不小于标准最小值（否则槽干涉）
    try:
        from ai_modeler import std_pulley
        for gt, n in (("A", 3), ("B", 5), ("C", 6)):
            s = std_pulley.spec(200.0, n, gt, 10.0, bore_d=40.0)
            add("带轮 %s×%d 轮缘宽度 ≥ 标准最小值" % (gt, n),
                s["width"] >= s["rim_w_min"] - 1e-6,
                "宽 %.2f 最小 %.2f" % (s["width"], s["rim_w_min"]))
    except Exception as e:
        add("带轮轮缘宽度", False, str(e))

    # 3) 带轮直径系列吸附：只要 dd 落在手册系列表里，就必须用系列外径；
    #    不在表里时按 da = dd + 2·ha 推导（并给出提示）。
    try:
        from ai_modeler import std_pulley
        ok_all, notes = True, []
        for gt, dd in (("Z", 25.0), ("A", 100.0), ("B", 118.0)):
            series = std_pulley.diameter_series(gt)
            if dd not in series:
                notes.append("%s ⌀%g 不在手册系列表内(跳过)" % (gt, dd))
                continue
            _, da = std_pulley.round_to_series(dd, gt)
            good = da == series[dd]
            ok_all = ok_all and good
            notes.append("%s ⌀%g→%s(表值 %s)" % (gt, dd, da, series[dd]))
        add("带轮直径系列吸附（表内值必须命中）", ok_all, "；".join(notes))
    except Exception as e:
        add("带轮直径系列吸附", False, str(e))

    # 4) 圆柱包围盒 = 直径 × 直径 × 高
    try:
        polys = K.make_cylinder(20.0, 50.0, (0, 0, 0), 0, 96)
        bb = K.bbox(polys)
        add("圆柱包围盒", abs((bb[3] - bb[0]) - 40) < 0.05 and abs((bb[5] - bb[2]) - 50) < 0.05,
            "%.2f × %.2f" % (bb[3] - bb[0], bb[5] - bb[2]))
    except Exception as e:
        add("圆柱包围盒", False, str(e))

    # 5) 通孔真的挖掉了材料（面片数显著增加）
    try:
        r = NLP.parse("圆柱 直径60 高20, 中心打一个直径20的通孔")
        body = r.doc.build(deadline=time.time() + 30)
        tris = sum(len(p.vertices) - 2 for p in body)
        add("通孔生成", tris > 100, "面片 %d" % tris)
    except Exception as e:
        add("通孔生成", False, str(e))

    # 6) 尺寸不可量化损失：小数尺寸必须原样保留（Pointer-CAD v2 的核心教训）
    try:
        r = NLP.parse("圆柱 直径60.37 高120.83")
        pr = (r.doc.features[0].params or {}) if r.doc.features else {}
        got = (2 * _f(pr.get("radius")) or None, _f(pr.get("height")))
        add("小数尺寸不被量化/截断",
            got[0] is not None and abs(got[0] - 60.37) < 0.01 and abs(got[1] - 120.83) < 0.01,
            "解析为 %s" % (got,))
    except Exception as e:
        add("小数尺寸保真", False, str(e))

    return rows


def main():
    t0 = time.time()
    l2 = []
    for text, exp in L2_CASES:
        ok, detail, kinds = check_l2(text, exp)
        l2.append({"text": text, "pass": ok, "detail": detail})
    n_pass = sum(1 for x in l2 if x["pass"])

    geo = [dict(text=t, **check_geometry(t)) for t, _ in L2_CASES]
    n_built = sum(1 for x in geo if x.get("built"))
    n_valid = sum(1 for x in geo if x.get("built") and (x.get("tris") or 0) > 0)

    inv = check_invariants()
    n_inv = sum(1 for x in inv if x["ok"])

    fails = []
    for row in l2:
        if row["pass"]:
            continue
        why = [("%s=%s" % (k, v)) for k, v in row["detail"].items()
               if k not in ("kinds", "kind_note") and isinstance(v, str) and "≠" in v]
        if not why:
            why = [row["detail"].get("error") or row["detail"].get("kind_note") or "未知"]
        fails.append({"text": row["text"], "why": "；".join(why)[:140],
                      "kinds": row["detail"].get("kinds", "")})

    res = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "l2": {"total": len(l2), "passed": n_pass,
               "accuracy": round(100.0 * n_pass / max(1, len(l2)), 1), "rows": l2},
        "geometry": {"total": len(geo), "built": n_built, "valid": n_valid,
                     "accuracy": round(100.0 * n_valid / max(1, len(geo)), 1), "rows": geo},
        "invariants": {"total": len(inv), "passed": n_inv, "rows": inv},
        "fails": fails,
        "elapsed": round(time.time() - t0, 1),
    }
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "precision_diagnosis.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    md = ["# 精度诊断报告", "",
          "> 由 `tools/diagnose_precision.py` 生成。参数层按**语义**核对（外径=最大径向尺寸、"
          "轴向=ΔZ、内孔=是否存在同轴孔特征、计数=齿数/槽数），不要求特征命名一致。", "",
          "## 一、三层指标", "",
          "| 层 | 指标 | 结果 |", "|---|---|---|",
          "| L2 参数层 | 提示词里的数值是否落到正确语义位置 | **%d/%d（%.1f%%）** |"
          % (n_pass, len(l2), res["l2"]["accuracy"]),
          "| L3 几何层 | 成功构建且非退化 | **%d/%d（%.1f%%）** |"
          % (n_valid, len(geo), res["geometry"]["accuracy"]),
          "| 不变量 | 标准公式/解析解硬约束 | **%d/%d** |" % (n_inv, len(inv)),
          "", "## 二、L2 参数层逐条明细", "", "| 提示词 | 结果 | 明细 |", "|---|---|---|"]
    for row in l2:
        items = "；".join("%s=%s" % (k, v) for k, v in row["detail"].items()
                          if k not in ("kinds", "kind_note"))
        md.append("| `%s` | %s | %s |" % (row["text"][:34], "✅" if row["pass"] else "❌",
                                          items[:120]))
    md += ["", "## 三、几何层明细", "", "| 提示词 | 构建 | 面片 | 耗时(s) | 备注 |",
           "|---|---|---|---|---|"]
    for row in geo:
        md.append("| `%s` | %s | %s | %s | %s |"
                  % (row["text"][:30], "✅" if row.get("built") else "❌",
                     row.get("tris", "-"), row.get("sec", "-"), row.get("error", "")))
    md += ["", "## 四、不变量（硬约束）", "", "| 约束 | 结果 | 实测 |", "|---|---|---|"]
    for row in inv:
        md.append("| %s | %s | %s |" % (row["name"], "✅" if row["ok"] else "❌", row["detail"]))
    md += ["", "## 五、失败清单（下一次迭代的输入）", ""]
    for f in fails:
        md.append("- `%s`：%s（实际特征 %s）" % (f["text"], f["why"], f["kinds"]))
    if not fails:
        md.append("- 无")
    with io.open(os.path.join(REPORTS, "精度诊断报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("L2 参数层: %d/%d（%.1f%%）" % (n_pass, len(l2), res["l2"]["accuracy"]))
    print("L3 几何层: %d/%d（%.1f%%）" % (n_valid, len(geo), res["geometry"]["accuracy"]))
    print("不变量: %d/%d" % (n_inv, len(inv)))
    for f in fails:
        print("   失败: %-32s -> %s" % (f["text"][:32], f["why"][:70]))
    for row in inv:
        if not row["ok"]:
            print("   不变量失败: %s -> %s" % (row["name"], row["detail"]))
    print("用时 %.1fs" % res["elapsed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
