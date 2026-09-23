# -*- coding: utf-8 -*-
r"""按导出的 521 个真实零件生成「AI 建模训练提示词」语料。

生成原则（与上一轮迭代的纪律一致）：
  · 每题都带**真实型号 token**（M10x30 / EGT001 SW80x088 / KMS-M15-G14-H06 …），
    答案 = 真实类别 + 实测几何，因此**可以被现有工具自动评分**；
  · 生成器自检：每一条提示词都必须能被 `reallib.lookup` 解析回同一型号，
    解析不出来的当场剔出并计数（正常应为 0）；
  · 每个型号多个问法（型号直呼 / 裸型号 / 带类别词 / 自然语言包裹），
    训练"同一个零件换说法也能认"；
  · 训练/测试按**稳定哈希** 8:2 切分（同一份数据每次切出来都一样，
    不会"这轮测试题下轮变训练题"）；
  · 另给**负例题**（库外型号），期望答案 = 未命中/拒绝 —— 训练"不猜"。

用法: python tools\make_training_prompts.py
输出: AI训练提示词\ 目录（全部 jsonl、训练集/测试集 提示词与答案、负例题、说明）
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import reallib as RL       # noqa: E402

KB = os.path.join(ROOT, "kb")
OUT = os.path.join(ROOT, "AI训练提示词")

NEGATIVES = [
    "KMS-M99-XYZ-123",
    "M13x50",
    "EGT009 SW70x500",
    "UCBH99ZZZZ999",
    "GB825_88_M99",
    "BES_M99MI_XXX",
    "分气块 KMS-M99-ABC-999",
    "完全不存在的型号XYZ123",
    "随便给我建一个零件",
    "轴承 99999",
    "内六角螺栓 M14x88",
    "定位销 KMS-M01-P999-A99",
]


def _load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def _env_str(env):
    if not env:
        return "-"
    return "×".join("%g" % float(v) for v in sorted(env, reverse=True))


def _add(cases, cid, prompt, cat, model, env, trust, source, group, variant):
    cases.append({
        "id": cid, "prompt": prompt, "category": cat, "model": model,
        "envelope": [round(float(v), 3) for v in env] if env else None,
        "envelope_str": _env_str(env), "trust": trust, "source": source,
        "group": group, "variant": variant,
    })


def build():
    cases = []

    # ---------- 1. 内六角螺钉（55）----------
    bolts = _load("bolt_library.json")["bolts"]
    for m, rec in bolts.items():
        sp, ln = rec["spec"], rec["length"]
        env = rec["envelope"]
        src = "kb/bolt_library.json"
        _add(cases, "bolt:%s:1" % m, "内六角螺栓 %s" % m, "内六角螺钉", m,
             env, "exact", src, "内六角螺钉", "带类别词")
        _add(cases, "bolt:%s:2" % m, m, "内六角螺钉", m, env, "exact", src,
             "内六角螺钉", "裸型号")
        _add(cases, "bolt:%s:3" % m, "帮我建%s内六角螺钉" % m, "内六角螺钉", m,
             env, "exact", src, "内六角螺钉", "自然语言")
        _add(cases, "bolt:%s:4" % m, "内六角圆柱头螺钉 %sx%g 带弹垫和平垫"
             % (sp, ln), "内六角螺钉", m, env, "exact", src, "内六角螺钉",
             "带规格描述")

    # ---------- 2. EGT 型材（289）----------
    egt = _load("egt_library.json")
    for fam, v in egt["profiles"].items():
        for code, rec in v["records"].items():
            tok = "%s %sx%03d" % (v["family"], v["section"], int(code))
            short = "%sx%03d" % (v["section"], int(code))
            env = [float(x) for x in rec["section"]] + [rec["length"]]
            src = "kb/egt_library.json"
            _add(cases, "egt:%s:%s:1" % (fam, code), "EGT型材 %s" % tok,
                 "EGT型材", tok, env, "exact", src, "EGT型材", "带类别词")
            _add(cases, "egt:%s:%s:2" % (fam, code), tok, "EGT型材", tok, env,
                 "exact", src, "EGT型材", "裸型号")
            _add(cases, "egt:%s:%s:3" % (fam, code), "型材 %s" % short,
                 "EGT型材", tok, env, "exact", src, "EGT型材", "简写")
            _add(cases, "egt:%s:%s:4" % (fam, code),
                 "TUNKERS铝型材 %s，按真实尺寸建模" % short, "EGT型材", tok,
                 env, "exact", src, "EGT型材", "自然语言")

    # ---------- 3. EGT 连接件（73）----------
    for model, rec in egt["connectors"].items():
        f = rec["files"][0]
        env = f.get("envelope")
        trust = ("exact" if f.get("n_solids", 1) <= 1 and env
                 else "assembly_scope") if env else "no_geometry"
        src = "kb/egt_library.json"
        _add(cases, "egtc:%s:1" % model, "EGT连接件 %s" % model, "EGT连接件",
             model, env, trust, src, "EGT连接件", "带类别词")
        _add(cases, "egtc:%s:2" % model, "%s 按真实尺寸建模" % model,
             "EGT连接件", model, env, trust, src, "EGT连接件", "自然语言")

    # ---------- 4. 厂内标准件 ----------
    std = _load("standard_real_library.json")
    for g, v in std.get("groups", {}).items():
        for it in v.get("items", []):
            model = it["model"]
            env = it.get("envelope") if it.get("trust") == "exact" else None
            trust = it.get("trust") or ("exact" if env else "no_geometry")
            src = "kb/standard_real_library.json"
            _add(cases, "std:%s:1" % model, "%s %s" % (g, model), g, model,
                 env, trust, src, g, "带类别词")
            _add(cases, "std:%s:2" % model, model, g, model, env, trust, src, g,
                 "裸型号")
            _add(cases, "std:%s:3" % model, "帮我建%s，型号%s" % (g, model), g,
                 model, env, trust, src, g, "自然语言")
    for it in std.get("shims", {}).get("items", []):
        model = it["model"]
        # 跳过无几何的占位件（`50x20x5` / `50x50x5` / `65x20x5` 三个源 CATPart
        # 本身是空的，`SHAPE_REPRESENTATION(' ',$,$)`，没有可训练的几何）
        if it.get("trust") == "no_geometry" or not it.get("envelope"):
            continue
        env = it.get("envelope") if it.get("trust") == "exact" else None
        trust = it.get("trust") or ("exact" if env else "no_geometry")
        src = "kb/standard_real_library.json"
        _add(cases, "shim:%s:1" % model, "垫片垫块 %s" % model, "垫片垫块",
             model, env, trust, src, "垫片垫块", "带类别词")
        _add(cases, "shim:%s:2" % model, model, "垫片垫块", model, env, trust,
             src, "垫片垫块", "裸型号")

    # ---------- 5. 外购件（17）----------
    pur = _load("purchased_library.json")
    for model, rec in pur.items():
        desc, brand = rec.get("desc") or "外购件", rec.get("brand") or ""
        env = rec.get("envelope") if rec.get("trust") == "exact" else None
        trust = rec.get("trust") or "no_geometry"
        src = "kb/purchased_library.json"
        _add(cases, "pur:%s:1" % model, "%s %s" % (desc, model), desc, model,
             env, trust, src, desc, "带类别词")
        _add(cases, "pur:%s:2" % model, model, desc, model, env, trust, src,
             desc, "裸型号")
        _add(cases, "pur:%s:3" % model, "%s 品牌%s 型号%s" % (desc, brand,
                                                            model), desc,
             model, env, trust, src, desc, "自然语言")

    # ---------- 负例（期望：未命中 / 不猜）----------
    neg_cases = []
    for i, p in enumerate(NEGATIVES):
        neg_cases.append({"id": "neg:%02d" % (i + 1), "prompt": p,
                          "category": None, "model": None, "envelope": None,
                          "envelope_str": "-", "trust": None,
                          "source": "负例", "group": "负例", "variant": "库外型号"})

    # ---------- 自检：每条正例都必须解析回同一型号 ----------
    bad = []
    for c in cases:
        hit = RL.lookup(c["prompt"])
        got = (hit or {}).get("model")
        def nm(s):
            return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()
        if got is None or nm(got) != nm(c["model"]):
            bad.append((c["prompt"], c["model"], got))
    for p in NEGATIVES:
        if RL.lookup(p) is not None:
            bad.append((p, "期望未命中", "实际命中了"))
    print("正例 %d 条，负例 %d 条；自检不合格 %d 条："
          % (len(cases), len(neg_cases), len(bad)))
    for b in bad[:20]:
        print("   ✗ %s" % (b,))
    # 不合格的剔出（不让答案里出现编不出来的东西）
    bad_ids = set()
    for c in cases:
        hit = RL.lookup(c["prompt"])
        got = (hit or {}).get("model")
        def nm2(s):
            return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()
        if got is None or nm2(got) != nm2(c["model"]):
            bad_ids.add(c["id"])
    good = [c for c in cases if c["id"] not in bad_ids]

    # ---------- 8:2 稳定切分 ----------
    train, test = [], []
    for c in good:
        h = int(hashlib.md5(c["id"].encode("utf-8")).hexdigest()[:8], 16)
        (train if h % 10 < 8 else test).append(c)
    # 负例全部进训练集
    train += neg_cases

    # ---------- 写文件 ----------
    os.makedirs(OUT, exist_ok=True)
    with io.open(os.path.join(OUT, "全部提示词.jsonl"), "w",
                 encoding="utf-8") as f:
        for c in good + neg_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    for name, subset in (("训练集", train), ("测试集", test)):
        with io.open(os.path.join(OUT, "%s_提示词.txt" % name), "w",
                     encoding="utf-8") as f:
            for c in subset:
                f.write(c["prompt"] + "\n")
        with io.open(os.path.join(OUT, "%s_答案.txt" % name), "w",
                     encoding="utf-8") as f:
            for c in subset:
                f.write("%s\t%s\t%s\t%s\n" % (
                    c["id"],
                    c["category"] or "未命中",
                    c["model"] or "-",
                    c["envelope_str"]))
    with io.open(os.path.join(OUT, "负例_不应建模.txt"), "w",
                 encoding="utf-8") as f:
        for c in neg_cases:
            f.write(c["prompt"] + "\n")

    stats = {"positive": len(good), "negative": len(neg_cases),
             "train": len(train), "test": len(test),
             "unresolved": len(bad)}
    with io.open(os.path.join(OUT, "_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print("→ AI训练提示词\\ 目录")
    print("   正例 %d / 负例 %d；训练集 %d / 测试集 %d"
          % (len(good), len(neg_cases), len(train), len(test)))
    return stats


if __name__ == "__main__":
    build()
