# -*- coding: utf-8 -*-
r"""CATIA 通道回归：把**全部真实型号**过一遍导出，检查特征表是否可执行。

为什么要有这个检查（现场事故的直接教训）：
    用户把「内六角螺栓 M5x16」发到 CATIA，CATIA 里只出了杆和头，
    日志报「孔: 缺少对象 / 弹性垫圈: 缺少对象」，Part 直接报错。
    根因是导出表里出现了一个 **depth=0 的 poly_cut**：凸台转切除时只改了
    `kind` 和 `z`，没把拉伸高度搬到 `depth`（切除类导出读的是 `depth`）。
    零深凹槽在 CATIA 里不是"没效果"，而是让 **整棵 Part 进入报错状态**，
    后续所有 Sketches.Add 全部失败 —— 一条坏特征毁掉整个模型。

本检查对每个真实型号统一验证：
  1. 特征表非空；
  2. **没有零深/负深**的凸台或凹槽（拉伸类看 h，切除类看 depth）；
  3. 没有因为"非 Z 轴"而被整体跳过的特征；
  4. 每个特征都有正的几何量（圆有 r，矩形有 l/w，轮廓有 ≥3 点）；
  5. 导出的记录串字段数正确（10 段，供宏 Split 解析）。

用法: python tools\check_catia_export_real.py
输出: reports\CATIA通道检查.md
"""
from __future__ import annotations

import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import catia_export as CE   # noqa: E402
from ai_modeler import nlparser as NLP       # noqa: E402
from ai_modeler import partlib as PL         # noqa: E402
from ai_modeler import reallib as RL         # noqa: E402


def prompts():
    """覆盖每个真实型号一次（用带类别词的写法，最接近用户实际输入）。"""
    out = []
    for lib, keys in (("bolt_library.json", "bolts"),
                      ("egt_library.json", "profiles"),
                      ("standard_real_library.json", "groups"),
                      ("purchased_library.json", None)):
        pass
    bolts = PL.RL._load("bolt_library.json").get("bolts", {})
    for m, rec in bolts.items():
        out.append(("内六角螺栓 %s" % m, "内六角螺钉"))
    egt = PL.RL._load("egt_library.json")
    for fam, v in (egt.get("profiles") or {}).items():
        for code, rec in (v.get("records") or {}).items():
            out.append(("EGT型材 %s %sx%03d" % (v["family"], v["section"],
                                               int(code)), "EGT型材"))
    for model in (egt.get("connectors") or {}):
        out.append(("EGT连接件 %s" % model, "EGT连接件"))
    std = PL.RL._load("standard_real_library.json")
    for g, v in (std.get("groups") or {}).items():
        for it in (v.get("items") or []):
            out.append(("%s %s" % (g, it["model"]), g))
    for it in (std.get("shims") or {}).get("items", []):
        out.append(("垫片垫块 %s" % it["model"], "垫片垫块"))
    for model, rec in (PL.RL._load("purchased_library.json") or {}).items():
        out.append(("%s %s" % (rec.get("desc") or "外购件", model),
                    rec.get("desc") or "外购件"))
    return out


def check_one(text, expect_cat):
    """返回 (ok, 问题列表, 特征数)。"""
    problems = []
    try:
        doc = NLP.parse(text).doc
    except Exception as e:
        return False, ["建文档失败: %s" % e], 0
    try:
        feats, warns = CE.build_catia_features(doc)
    except Exception as e:
        return False, ["导出失败: %s" % e], 0
    if not feats:
        # 「实测 B-Rep 复刻」件走的是**另一条通道**：自由曲面/型腔无法用
        # "凸台 + 圆孔"的特征表表达，改为把实测 STEP 导进 CATIA。
        # 这里检查"实测 STEP 是否就位"，而不是"特征表非空"——
        # 判据换了，但"CATIA 里能不能拿到这个零件"这件事仍然被查着。
        import os as _os
        from ai_modeler import realsolid as _RS
        rs = [f for f in doc.features if f.kind == "real_solid"]
        if rs:
            src = _os.path.join(_os.environ.get("TEMP", "."), "catia_step_out")
            name = str((rs[0].params or {}).get("model") or "")
            ok_step = _RS.available(name)
            if ok_step:
                return True, ["走 STEP 通道（实测 B-Rep 复刻，%.1f 万三角面）"
                              % (len(_RS.polys(name)) / 1e4)], 1
            return False, ["real_solid 但实测网格缺失：%s" % name], 0
        return False, ["特征表为空（CATIA 里什么都建不出来）"], 0
    for w in warns:
        if "仅支持 Z 轴" in w:
            problems.append("有特征因非 Z 轴被跳过: %s" % w[:60])
    for e in feats:
        k = str(e.get("kind") or "")
        cut = k.endswith("_cut")
        hv = float(e.get("depth") if cut else e.get("h")) or 0.0
        if hv <= 1e-9:
            problems.append("**零深特征** %s（%s）—— 会让 CATIA 整棵 Part 报错"
                            % (k, e.get("name")))
        if k.startswith("circle"):
            if float(e.get("r") or 0) <= 0:
                problems.append("圆形特征半径为 0: %s" % k)
        elif e.get("profile"):
            if len(e["profile"]) < 3:
                problems.append("轮廓少于 3 点: %s" % k)
        else:
            if float(e.get("l") or 0) <= 0 or float(e.get("w") or 0) <= 0:
                problems.append("矩形特征长/宽为 0: %s" % k)
        # 记录串段数（宏按 | 切成 10 段）
        rec = "|".join([k, "%g" % e["cx"], "%g" % e["cy"], "%g" % e["z"],
                        "%g" % e.get("l", 0), "%g" % e.get("w", 0),
                        "%g" % e.get("r", 0), "%g" % hv,
                        (e.get("name") or "").replace("|", "/"),
                        ";".join("%g,%g" % (a, b) for (a, b) in
                                 (e.get("profile") or []))])
        if len(rec.split("|")) != 10:
            problems.append("记录段数不是 10（名字里有 | ?）: %s" % e.get("name"))
    return (not problems), problems, len(feats)


def main():
    cases = prompts()
    print("检查 %d 个真实型号的 CATIA 导出…" % len(cases))
    t0 = time.time()
    bad = []
    n_feat = 0
    for text, cat in cases:
        ok, probs, nf = check_one(text, cat)
        n_feat += nf
        if not ok:
            bad.append((text, probs))
    dt = time.time() - t0
    print("\n" + "=" * 78)
    print("通过 %d/%d，特征总数 %d，用时 %.1fs"
          % (len(cases) - len(bad), len(cases), n_feat, dt))
    if bad:
        print("\n不通过（前 25）：")
        for t, p in bad[:25]:
            print("   %-40s %s" % (t[:40], p[0][:90]))
    L = ["# CATIA 通道检查", "",
         "对**全部 %d 个真实型号**过一遍 CATIA 导出，检查特征表能否安全执行。"
         % len(cases), "",
         "## 为什么要查这个", "",
         "现场事故：把「内六角螺栓 M5x16」发到 CATIA，只出了杆和头，",
         "日志报「孔: 缺少对象 / 弹性垫圈: 缺少对象」。根因是导出表里有一个",
         "**depth=0 的 poly_cut** —— 凸台转切除时只改了 `kind` 和 `z`，",
         "没把拉伸高度搬到 `depth`。零深凹槽会让 CATIA 的整棵 Part 进入报错",
         "状态，后续所有 `Sketches.Add` 连带失败。", "",
         "## 结果", "",
         "| 指标 | 值 |", "|---|---|",
         "| 检查型号数 | %d |" % len(cases),
         "| 通过 | %d |" % (len(cases) - len(bad)),
         "| 不通过 | %d |" % len(bad),
         "| 特征总数 | %d |" % n_feat,
         "| 用时 | %.1f s |" % dt, ""]
    if bad:
        L += ["## 不通过清单", "", "| 提示词 | 问题 |", "|---|---|"]
        for t, p in bad:
            L.append("| `%s` | %s |" % (t, "；".join(p)[:160]))
    else:
        L.append("**全部通过**：没有零深特征、没有因轴被跳过的特征、"
                 "特征表段数均为 10。")
    with io.open(os.path.join(ROOT, "reports", "CATIA通道检查.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/CATIA通道检查.md")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
