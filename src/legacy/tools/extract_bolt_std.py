# -*- coding: utf-8 -*-
r"""
U5 扩展：内六角圆柱头螺钉标准尺寸入库（ISO 4762 / GB/T 70.1—2008）。

为什么做这个：`零件参数` 文件夹里的螺钉是 **CATIA 私有二进制**（实测只能抠出
V5 版本号/PN，没有几何），逆向不出外形。但内六角圆柱头螺钉的外形**由标准定义**：
    · 螺纹规格 d          -> 螺距 p
    · 头部直径 dk          · 头部高度 k
    · 内六角对边 s         · 内六角深度 t
    · 垫圈面直径 dw
按标准表建模才是正解，也才是可核验的（有锚点可自检）。

数据来源（公开标准值，写死为锚点供自检）：
    ISO 4762 / GB/T 70.1—2008《内六角圆柱头螺钉》常用规格
    （M3–M20；本项目零件库用到 M5/M6/M8/M10/M12）

输出：kb/bolt_std.json、kb/bolt_std.md
用法：python tools\extract_bolt_std.py
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
OUT_JSON = os.path.join(KB, "bolt_std.json")
OUT_MD = os.path.join(KB, "bolt_std.md")

#: ISO 4762 / GB/T 70.1 内六角圆柱头螺钉标准尺寸（单位 mm）
#:   d: 螺纹规格 | p: 螺距 | dk: 头部直径(max) | k: 头部高度(max)
#:   s: 内六角对边 | t: 内六角深度(min) | dw: 垫圈面直径(min) | e: 六角对角(min)
BOLTS = {
    "M3":  dict(p=0.50, dk=5.5,  k=3.0,  s=2.5,  t=1.3,  dw=4.3,  e=2.87),
    "M4":  dict(p=0.70, dk=7.0,  k=4.0,  s=3.0,  t=2.0,  dw=5.5,  e=3.44),
    "M5":  dict(p=0.80, dk=8.5,  k=5.0,  s=4.0,  t=2.5,  dw=6.9,  e=4.58),
    "M6":  dict(p=1.00, dk=10.0, k=6.0,  s=5.0,  t=3.0,  dw=8.2,  e=5.72),
    "M8":  dict(p=1.25, dk=13.0, k=8.0,  s=6.0,  t=4.0,  dw=10.8, e=6.86),
    "M10": dict(p=1.50, dk=16.0, k=10.0, s=8.0,  t=5.0,  dw=13.4, e=9.15),
    "M12": dict(p=1.75, dk=18.0, k=12.0, s=10.0, t=6.0,  dw=15.2, e=11.43),
    "M14": dict(p=2.00, dk=21.0, k=14.0, s=12.0, t=7.0,  dw=18.2, e=13.72),
    "M16": dict(p=2.00, dk=24.0, k=16.0, s=14.0, t=8.0,  dw=21.2, e=16.00),
    "M20": dict(p=2.50, dk=30.0, k=20.0, s=17.0, t=10.0, dw=26.4, e=19.44),
}

#: 长度系列（GB/T 70.1 常用长度，mm）—— 供"缺档提示最近标准长度"用
LENGTHS = [4, 5, 6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70,
           80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200]

#: 锚点自检：这几组是公开标准里查得到的确定值，用于校验表没抄错
ANCHORS = {
    "M5":  (8.5, 5.0, 4.0, 2.5),
    "M6":  (10.0, 6.0, 5.0, 3.0),
    "M8":  (13.0, 8.0, 6.0, 4.0),
    "M10": (16.0, 10.0, 8.0, 5.0),
    "M12": (18.0, 12.0, 10.0, 6.0),
}


def normalize(spec):
    """'m10' / 'M10' / '10' -> 'M10'。"""
    if spec is None:
        return None
    t = str(spec).strip().upper().replace(" ", "")
    if t.startswith("M"):
        return t if t in BOLTS else None
    if t.isdigit():
        t2 = "M" + t
        return t2 if t2 in BOLTS else None
    return None


def spec_of(spec, length=None):
    """取某个规格的完整尺寸。查不到返回 None（**不猜**）。"""
    key = normalize(spec)
    if not key:
        return None
    v = dict(BOLTS[key])
    out = {"spec": key, "standard": "ISO 4762 / GB/T 70.1—2008", **v}
    d = float(key[1:])
    out["thread_d"] = d
    out["head_d"] = v["dk"]
    out["head_h"] = v["k"]
    out["hex_s"] = v["s"]
    out["hex_t"] = v["t"]
    if length is not None:
        try:
            L = float(length)
        except (TypeError, ValueError):
            return None
        out["length"] = L
        out["thread_len"] = L          # 全螺纹（GB/T 70.1 常用全螺纹型）
        near = nearest_length(L)
        if near is not None and abs(near - L) > 1e-9:
            out["length_not_standard"] = near
    return out


def nearest_length(L):
    """给一个非标准长度，返回最接近的标准长度（供界面提示，不静默替换）。"""
    try:
        L = float(L)
    except (TypeError, ValueError):
        return None
    return min(LENGTHS, key=lambda x: abs(x - L))


def summary(s):
    if not s:
        return "标准表里没有这个规格（可用 %s）" % "、".join(sorted(BOLTS))
    t = ("内六角圆柱头螺钉 %s（%s）：头部 ⌀%g×%g、内六角对边 %g 深 %g、螺距 %g"
         % (s["spec"], s["standard"], s["head_d"], s["head_h"],
            s["hex_s"], s["hex_t"], s["p"]))
    if s.get("length"):
        t += "、长度 %g" % s["length"]
    if s.get("length_not_standard"):
        t += "（⚠️ %g 不是标准长度系列，最近的是 %g）" % (
            s["length"], s["length_not_standard"])
    return t


def explain(s):
    """提示词 -> 生成特征 的对照说明。"""
    if not s:
        return []
    out = [
        ("“%s”螺纹规格" % s["spec"],
         "螺纹大径 ⌀%g、螺距 p=%g" % (s["thread_d"], s["p"]),
         "ISO 4762 / GB/T 70.1—2008 螺纹规格表"),
        ("“长度 %g”" % s.get("length", 0),
         "螺纹段长 %g" % (s.get("thread_len") or 0),
         "GB/T 70.1 长度系列"),
        ("头部",
         "头部 ⌀%g × 高 %g（垫圈面 ⌀%g）"
         % (s["head_d"], s["head_h"], s["dw"]),
         "ISO 4762 头部尺寸（max 值）"),
        ("内六角孔",
         "对边 s=%g、深 t=%g" % (s["hex_s"], s["hex_t"]),
         "ISO 4762 内六角尺寸"),
    ]
    return out


def main():
    print("内六角圆柱头螺钉标准尺寸入库（ISO 4762 / GB/T 70.1—2008）")
    bad = []
    print("\n锚点自检（头部直径 / 头高 / 对边 / 六角深）：")
    for k, (dk, kk, s, t) in ANCHORS.items():
        v = BOLTS.get(k)
        ok = v and v["dk"] == dk and v["k"] == kk and v["s"] == s and v["t"] == t
        print("  %-5s %s 期望 %g/%g/%g/%g 实得 %s"
              % (k, "✅" if ok else "❌", dk, kk, s, t,
                 "%g/%g/%g/%g" % (v["dk"], v["k"], v["s"], v["t"]) if v else "缺"))
        if not ok:
            bad.append(k)

    db = {
        "standard": "ISO 4762 / GB/T 70.1—2008 内六角圆柱头螺钉",
        "note": "标准值（头部尺寸为 max，六角深为 min）。长度系列另列。",
        "count": len(BOLTS),
        "bolts": BOLTS,
        "length_series": LENGTHS,
        "anchors_checked": len(ANCHORS),
        "anchors_failed": bad,
    }
    os.makedirs(KB, exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    print("\n已写入 %s（%d 个规格）" % (OUT_JSON, len(BOLTS)))

    md = ["# 内六角圆柱头螺钉标准尺寸（ISO 4762 / GB/T 70.1—2008）", "",
          "| 规格 | 螺距 p | 头径 dk | 头高 k | 对边 s | 六角深 t | 垫圈面 dw |",
          "|---|---|---|---|---|---|---|"]
    for k in sorted(BOLTS, key=lambda x: float(x[1:])):
        v = BOLTS[k]
        md.append("| %s | %g | %g | %g | %g | %g | %g |"
                  % (k, v["p"], v["dk"], v["k"], v["s"], v["t"], v["dw"]))
    md += ["", "长度系列（mm）：%s" % "、".join(str(x) for x in LENGTHS), "",
           "锚点自检：%d 项，失败 %d 项 %s"
           % (len(ANCHORS), len(bad), bad or "（全部通过）")]
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 %s" % OUT_MD)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
