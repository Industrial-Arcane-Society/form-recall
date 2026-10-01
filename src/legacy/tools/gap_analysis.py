# -*- coding: utf-8 -*-
r"""
量差距：我的建模插件 vs AI数据集 里的真实夹具零件。

问题不是"能不能复刻"，而是**具体差在哪、差多少**。这里量三件事：
  1. 数据集的零件类别分布 vs 插件现有的 41 个零件类别 —— 交集与缺口；
  2. 数据集的零件名/几何规模（包围盒、面数）—— 真实夹具零件的复杂度量级；
  3. BOM 里出现的特征词（孔/槽/螺纹/沉孔…）—— 插件要支持哪些几何特征。

用法：python tools\gap_analysis.py
输出：reports/夹具数据差距分析.md、kb/fixture_gap.json
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DS = (r"C:\Users\25643\Desktop\AI自动画图项目设计数据及视频-20260402-V01"
      r"\AI数据集")


def rd(name):
    p = os.path.join(DS, "data", name + ".jsonl")
    out = []
    if not os.path.exists(p):
        return out
    with io.open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    return out


def main():
    items = rd("items")
    bom = rd("bom_rows")
    geo = rd("geometry_step")
    asm = rd("assemblies")
    qa = rd("qa_pairs")
    tri = rd("triples")

    try:
        from ai_modeler import partlib as PL
        plugin_cats = list(PL.PARTS.keys())
    except Exception as e:
        plugin_cats = []
        print("读插件零件库失败: %s" % e)

    print("=" * 76)
    print("差距分析：插件 vs 真实夹具数据")
    print("=" * 76)
    print("数据集 items        %4d 条" % len(items))
    print("数据集 assemblies   %4d 条" % len(asm))
    print("数据集 bom_rows     %4d 条" % len(bom))
    print("数据集 geometry     %4d 条" % len(geo))
    print("数据集 qa_pairs     %4d 条" % len(qa))
    print("数据集 triples      %4d 条" % len(tri))
    print("插件零件类别        %4d 个" % len(plugin_cats))

    # ---- 1. 类别分布 ----
    cats = Counter(it.get("category") or "?" for it in items)
    print("\n[1] 数据集零件类别分布")
    for k, v in cats.most_common():
        print("    %-34s %4d" % (k, v))

    # 插件的类别名与数据集类别的对应（人工映射，只做粗对照）
    mapping = {
        "standard_component_library": "可能对应 平键/挡圈/垫圈/螺栓 等",
        "gb_fastener_library": "对应 螺栓/螺母/垫圈",
        "purchased_component": "外购件（传感器/气缸）—— 插件无",
        "fixture_machined_part": "夹具加工件（板/座/臂）—— 插件无对应类别",
        "reference_project_machined_part": "同上",
        "body_panel_product_data": "车身钣金件 —— 插件无",
        "weld_stud_product_data": "焊接螺柱 —— 插件无（近似：螺栓）",
    }
    print("\n[2] 与插件能力的粗对照")
    for k, v in cats.most_common():
        print("    %-34s -> %s" % (k, mapping.get(k, "?")))

    # ---- 2. 真实零件的几何规模 ----
    if geo:
        print("\n[3] STEP 几何规模（真实件复杂度）")
        sizes = []
        for g in geo:
            bb = g.get("bbox") or {}
            sz = bb.get("size") or bb.get("size_mm")
            if isinstance(sz, (list, tuple)) and len(sz) == 3:
                sizes.append((g.get("file_name") or g.get("file") or "?", sz,
                              g.get("face_count") or g.get("n_faces")))
        if sizes:
            for nm, sz, fc in sizes[:13]:
                print("    %-52s %.0f×%.0f×%.0f  面 %s"
                      % (str(nm)[:52], sz[0], sz[1], sz[2], fc))
        else:
            k0 = list(geo[0].keys())
            print("    （geometry 记录字段：%s）" % k0)

    # ---- 3. BOM 里的名称与材料 ----
    if bom:
        print("\n[4] BOM 里的零件名称（前 20）")
        names = Counter((b.get("name") or "").strip() for b in bom if b.get("name"))
        for k, v in names.most_common(20):
            print("    %-26s ×%d" % (k, v))
        mats = Counter((b.get("material") or "").strip() for b in bom if b.get("material"))
        if mats:
            print("\n    材料分布: %s"
                  % "、".join("%s×%d" % (k, v) for k, v in mats.most_common(12)))
        stds = Counter((b.get("standard") or "").strip() for b in bom if b.get("standard"))
        if stds:
            print("    标准号: %s"
                  % "、".join("%s×%d" % (k, v) for k, v in stds.most_common(12)))

    # ---- 4. QA 类型 ----
    if qa:
        print("\n[5] QA 对类型分布（可用于微调的任务形态）")
        for k, v in Counter(q.get("type") or "?" for q in qa).most_common():
            print("    %-28s %4d" % (k, v))

    # ---- 5. 三元组关系类型 ----
    if tri:
        print("\n[6] 知识三元组的关系类型")
        for k, v in Counter(t.get("predicate") or "?" for t in tri).most_common():
            print("    %-34s %4d" % (k, v))

    data = {
        "dataset_items": len(items), "dataset_assemblies": len(asm),
        "dataset_bom": len(bom), "dataset_geometry": len(geo),
        "dataset_qa": len(qa), "dataset_triples": len(tri),
        "plugin_categories": plugin_cats,
        "category_distribution": dict(cats),
        "category_mapping": mapping,
    }
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "kb"), exist_ok=True)
    with io.open(os.path.join(ROOT, "kb", "fixture_gap.json"), "w",
                 encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("\n已写入 kb/fixture_gap.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
