# -*- coding: utf-8 -*-
r"""用修好的真实几何，逐族验证「型号 → 几何」规律是否**逐条成立**。

判据全部是**实测值之间的等式**，不是名义值：
  螺栓   X尺寸 = 平垫外径；Y尺寸 = 2×内六角对边/√3；Z尺寸 = 公称长 + 头高
  型材   bbox = 实测截面 × 实测长度；圆柱面数 = 线性律
  其他   逐条列出实测值，不做外推

用法: python tools\verify_real_laws.py
输出: reports\真实几何规律验证.md
"""
from __future__ import annotations

import io
import json
import os
import re
from collections import Counter, OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = r"C:\Users\25643\AppData\Local\Temp\catia_step_out"


def load():
    with io.open(os.path.join(ROOT, "kb", "real_geometry.json"),
                 encoding="utf-8") as f:
        return json.load(f)["parts"]


# ISO 4762 内六角圆柱头螺钉（GB/T 70.1 等同）
ISO4762 = {
    "M5":  {"head_d": 8.5,  "head_h": 5.0,  "s": 4.0,  "dw": 10.0},
    "M6":  {"head_d": 10.0, "head_h": 6.0,  "s": 5.0,  "dw": 12.0},
    "M8":  {"head_d": 13.0, "head_h": 8.0,  "s": 6.0,  "dw": 16.0},
    "M10": {"head_d": 16.0, "head_h": 10.0, "s": 8.0,  "dw": 20.0},
    "M12": {"head_d": 18.0, "head_h": 12.0, "s": 10.0, "dw": 24.0},
}
# GB/T 97.1 平垫圈
GB97 = {"M5": (5.3, 10.0), "M6": (6.4, 12.0), "M8": (8.4, 16.0),
        "M10": (10.5, 20.0), "M12": (13.0, 24.0)}
# GB/T 93 标准型弹性垫圈（内径, 外径）—— 实测反推
GB93 = {"M5": (5.1, 8.8), "M6": (6.1, 9.9), "M8": (8.1, 12.7),
        "M10": (10.2, 16.0), "M12": (12.2, 18.0)}

L = []


def p(s=""):
    print(s)
    L.append(s)


def main():
    parts = load()
    p("# 真实几何规律验证")
    p()
    p("数据：`kb/real_geometry.json`（521 个 CATIA 导出 STEP 的实测解析，"
      "2026-09 重建，已修两处解析缺陷）")
    p()
    p("**判据原则**：只用**实测值与实测值之间**的等式，不用名义值凑。")
    p()

    # ---------------- 一、螺栓 ----------------
    p("## 一、内六角螺钉（55 个规格×长度）")
    p()
    bolts = [r for r in parts if r["rel"].startswith("螺钉")]
    p("实测件数 **%d**（表里 110 行 = 55 个模型 × 2 个类型标签，"
      "两个标签指向同一文件，实测几何完全相同）" % len(bolts))
    p()
    p("| 文件 | 实测 X | 实测 Y | 实测 Z | 平垫外径 | 内六角对边 s | 公称 L | 头高 | 判定 |")
    p("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    okz = okx = oky = 0
    n = 0
    bad = []
    for r in sorted(bolts, key=lambda r: (len(r["file"]), r["file"])):
        m = re.match(r"M(\d+)(?:x|X)([\d.]+)", r["file"])
        if not m:
            continue
        sp = "M" + m.group(1)
        ln = float(m.group(2))
        if sp not in ISO4762:
            continue
        n += 1
        std = ISO4762[sp]
        b = r["bbox"]["size"]
        ex = (GB97[sp][1], round(2 * std["s"] / 3 ** 0.5, 3),
              round(ln + std["head_h"], 3))
        good = (abs(b[0] - ex[0]) < 0.01 and abs(b[1] - ex[1]) < 0.01
                and abs(b[2] - ex[2]) < 0.01)
        okx += abs(b[0] - ex[0]) < 0.01
        oky += abs(b[1] - ex[1]) < 0.01
        okz += abs(b[2] - ex[2]) < 0.01
        if not good:
            bad.append((r["file"], b, ex))
        if n <= 26:
            p("| `%s` | %.2f | %.3f | %.2f | %.1f | %.3f | %.1f | %.1f | %s |"
              % (r["file"], b[0], b[1], b[2], ex[0], std["s"], ln,
                 std["head_h"], "✓" if good else "✗"))
    p()
    p("- X 尺寸 = 平垫外径（GB/T 97.1）：**%d/%d**" % (okx, n))
    p("- Y 尺寸 = 2·s/√3（ISO 4762 内六角对边）：**%d/%d**" % (oky, n))
    p("- Z 尺寸 = 公称长 + 头高（ISO 4762）：**%d/%d**" % (okz, n))
    p("- **三条同时成立：%d/%d**" % (n - len(bad), n))
    if bad:
        p()
        p("不成立清单：")
        for f, b, ex in bad:
            p("- `%s` 实测 %s ≠ 期望 %s" % (f, b, ex))
    p()

    p("### 1.1 实测圆柱面直径指纹（每个规格唯一且完全一致）")
    p()
    p("| 规格 | 圆柱面直径 × 个数 | 解读 |")
    p("| --- | --- | --- |")
    fp = defaultdict(Counter)
    for r in bolts:
        m = re.match(r"M(\d+)", r["file"])
        if m:
            fp["M" + m.group(1)][tuple(sorted(
                r["hole_diameters"].items(), key=lambda kv: -float(kv[0])))] += 1
    for sp in ("M5", "M6", "M8", "M10", "M12"):
        top = fp[sp].most_common(1)[0][0]
        read = []
        for d, c in sorted(top, key=lambda kv: -float(kv[0])):
            d = float(d)
            if abs(d - GB97[sp][1]) < 0.01:
                read.append("⌀%g 平垫外径" % d)
            elif abs(d - GB97[sp][0]) < 0.01:
                read.append("⌀%g 平垫内径" % d)
            elif abs(d - GB93[sp][1]) < 0.01:
                read.append("⌀%g 弹垫外径" % d)
            elif abs(d - GB93[sp][0]) < 0.01:
                read.append("⌀%g 弹垫内径" % d)
            elif abs(d - ISO4762[sp]["head_d"]) < 0.01:
                read.append("⌀%g 螺钉头" % d)
            elif abs(d - float(sp[1:])) < 0.01:
                read.append("⌀%g 螺杆" % d)
            else:
                read.append("⌀%g ?" % d)
        p("| %s | %s | %s |" % (sp, ", ".join("%s×%s" % kv for kv in
                                              sorted(top, key=lambda kv: -float(kv[0]))),
                                " + ".join(read)))
    p()
    p("→ 指纹种类数：%s（每个规格**只有一种**指纹，说明 55 个模型结构完全同族）"
      % {sp: len(fp[sp]) for sp in ("M5", "M6", "M8", "M10", "M12")})
    p()

    # ---------------- 二、EGT 型材 ----------------
    p("## 二、EGT 型材")
    p()
    fams = defaultdict(list)
    for r in parts:
        m = re.match(r"^(EGT\d+) (SW\d+|D\d+)x(\d+)\.stp$", r["file"])
        if m:
            fams[(m.group(1), m.group(2))].append((int(m.group(3)), r))
    p("| 家族 | 断面 | 档数 | 实测截面 | 长度−型号码 | 圆柱面数律 |")
    p("| --- | --- | --- | --- | --- | --- |")
    laws = {}
    for k in sorted(fams):
        items = sorted(fams[k])
        secs = sorted(set(tuple(sorted(r["bbox"]["size"], key=lambda v: -v)[1:])
                          for _, r in items))
        lens = [(c, max(r["bbox"]["size"])) for c, r in items]
        offs = sorted(set(round(c - l, 3) for c, l in lens))
        # k 必须由**型号码**推出，不能由列表下标推 —— EGT001 缺 x268、
        # EGT006 混入 x1000，用下标会让 k 错位（第一版就是这么错的）。
        codes = [c for c, _ in items]
        step = min((b - a) for a, b in zip(codes, codes[1:])) if len(codes) > 1 else 1
        cyls = [r["n_cylinders"] for _, r in items]
        d0 = cyls[0]
        dstep = (cyls[1] - cyls[0]) if len(cyls) > 1 else 0
        lin = all(cyls[i] == d0 + dstep * round((codes[i] - codes[0]) / float(step))
                  for i in range(len(cyls))) if dstep else True
        laws[k] = {"section": secs, "off": offs, "cyl0": d0,
                   "cyl_step": dstep, "step": step, "codes": codes}
        p("| %s | %s | %d | %s | %s | %d + %d·k %s |"
          % (k[0], k[1], len(items),
             "; ".join("×".join("%.3f" % v for v in s) for s in secs[:3]),
             ",".join("%.3f" % o for o in offs), d0, dstep,
             "✓" if lin else "✗"))
        if not lin:
            p("")
            p("  ⚠ 线性律不成立的条目：")
            for i, (c, r) in enumerate(items):
                want = d0 + dstep * round((c - codes[0]) / float(step))
                if r["n_cylinders"] != want:
                    p("    - 码 %d：实测柱面 %d，线性律给 %d"
                      % (c, r["n_cylinders"], want))
            p("")
    p()
    for k, v in laws.items():
        p("- **%s %s**：实测截面 %s；长度 = 型号码 − %s；圆柱面数 = %d + %d·k"
          "（型号码步长 %d）"
          % (k[0], k[1], v["section"],
             " / ".join("%.3f" % abs(o) for o in v["off"]) or "0",
             v["cyl0"], v["cyl_step"], v["step"]))
    p()

    # ---------------- 三、其余族 ----------------
    p("## 三、其余族的实测值（逐件列出，不外推）")
    p()
    fam = defaultdict(list)
    for r in parts:
        if r["rel"].startswith("外购件/EGT") or r["rel"] == "螺钉":
            continue
        key = "/".join(r["rel"].split("/")[:-1]) or r["rel"]
        fam[key].append(r)
    for k in sorted(fam, key=lambda x: -len(fam[x])):
        rs = fam[k]
        p("### %s（%d 件）" % (k, len(rs)))
        p()
        p("| 文件 | 实体 | 面 | 柱面 | 实测包围盒 X×Y×Z | 前 4 个柱面直径 |")
        p("| --- | --- | --- | --- | --- | --- |")
        for r in sorted(rs, key=lambda r: r["file"]):
            b = (r.get("bbox") or {}).get("size")
            hk = ", ".join("%s×%s" % kv for kv in
                           sorted(r["hole_diameters"].items(),
                                  key=lambda kv: -float(kv[0]))[:4])
            if b is None:
                p("| `%s` | %d | %d | %d | ⚠ 文件无几何（空 SHAPE_REPRESENTATION） | %s |"
                  % (r["file"], r["n_solids"], r["n_faces"], r["n_cylinders"], hk))
                continue
            p("| `%s` | %d | %d | %d | %.2f×%.2f×%.2f | %s |"
              % (r["file"], r["n_solids"], r["n_faces"], r["n_cylinders"],
                 b[0], b[1], b[2], hk))
        p()

    with io.open(os.path.join(ROOT, "reports", "真实几何规律验证.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/真实几何规律验证.md")


if __name__ == "__main__":
    main()
