# -*- coding: utf-8 -*-
r"""
把「零件参数」文件夹的 518 个零件抽成**参数目录**。

能抽的（真实存在、可核验）：
  · 目录结构 = 零件族与**参数组合**（如 垫片垫块\50x20x5 -> 50×20×5）
  · MyBoltLibrary.xlsx：111 行螺栓（文件名/类型/螺纹规格/长度/局部坐标系）
  · 外购件.xlsx：多个 sheet 的品牌型号库（TUNKERS/SMC/UNIVER 等）
  · CATPart 里的**元数据字符串**（V5 版本、PN 等；**不含几何**）

抽不到的（如实标注）：CATPart 的 B-rep 几何与特征树 —— 实测二进制里只有容器标签。

输出：kb/parts_param_catalog.json、reports/零件参数目录.md
用法：python tools\build_param_catalog.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"C:\Users\25643\Desktop\零件参数"
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ------------------------------------------------------------------ xlsx
def col_idx(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path):
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            r = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in r.findall(M + "si"):
                shared.append("".join(t.text or "" for t in si.iter(M + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        titles = [s.get("name") for s in wb.iter(M + "sheet")]
        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        for i, sn in enumerate(sheets):
            root = ET.fromstring(z.read(sn))
            rows = []
            for row in root.iter(M + "row"):
                cells = {}
                for c in row.iter(M + "c"):
                    t = c.get("t")
                    v = c.find(M + "v")
                    val = ""
                    if t == "s" and v is not None:
                        k = int(v.text or 0)
                        val = shared[k] if 0 <= k < len(shared) else ""
                    elif v is not None:
                        val = v.text or ""
                    cells[col_idx(c.get("r"))] = val
                if cells:
                    w = max(cells) + 1
                    rows.append([cells.get(k, "") for k in range(w)])
            out[titles[i] if i < len(titles) else sn] = rows
    return out


# ------------------------------------------------------------------ 主流程
def main():
    if not os.path.isdir(SRC):
        print("目录不存在:", SRC)
        return 1

    # ① 扫描文件树
    parts = []          # 每个 CATPart
    prods = []
    for dp, _dn, fns in os.walk(SRC):
        rel = dp[len(SRC):].lstrip("\\")
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = -1
            rec = {"file": fn, "rel_dir": rel,
                   "group": rel.split("\\")[0] if rel else "",
                   "family": rel.split("\\")[1] if len(rel.split("\\")) > 1 else "",
                   "size": sz}
            if fn.lower().endswith(".catpart"):
                parts.append(rec)
            elif fn.lower().endswith(".catproduct"):
                prods.append(rec)

    print("CATPart %d 个，CATProduct %d 个" % (len(parts), len(prods)))

    # ② 目录名里的参数组合（如 50x20x5 / M10x30 / SW80x088）
    dim_re = re.compile(r"(\d+(?:\.\d+)?)\s*[xX\u00d7]\s*(\d+(?:\.\d+)?)"
                        r"(?:\s*[xX\u00d7]\s*(\d+(?:\.\d+)?))?")
    dims_found = []
    for rec in parts:
        stem = os.path.splitext(rec["file"])[0]
        for src in (rec["rel_dir"], stem):
            m = dim_re.search(src)
            if m:
                v = [float(g) for g in m.groups() if g is not None]
                rec["params_from_name"] = v
                dims_found.append((rec["file"], v, src))
                break

    # ③ 螺栓库
    bolt_rows = []
    bp = os.path.join(SRC, "螺钉", "MyBoltLibrary.xlsx")
    if os.path.exists(bp):
        book = read_xlsx(bp)
        for name, rows in book.items():
            if not rows:
                continue
            hdr = [str(x).strip() for x in rows[0]]
            for r in rows[1:]:
                d = {}
                for i, h in enumerate(hdr):
                    d[h] = r[i] if i < len(r) else ""
                if d.get("文件名"):
                    bolt_rows.append({"sheet": name, **d})
    print("螺栓库行数 %d" % len(bolt_rows))
    bolt_types = Counter(b.get("螺栓类型") or "" for b in bolt_rows)
    bolt_specs = Counter(b.get("螺纹规格") or "" for b in bolt_rows)

    # ④ 外购件库
    buy_rows = []
    yp = os.path.join(SRC, "外购件", "外购件.xlsx")
    buy_sheets = {}
    if os.path.exists(yp):
        book = read_xlsx(yp)
        for name, rows in book.items():
            buy_sheets[name] = len(rows) - 1 if rows else 0
            if not rows:
                continue
            hdr = [str(x).strip() for x in rows[0]]
            for r in rows[1:]:
                d = {"sheet": name}
                for i, h in enumerate(hdr):
                    d[h] = r[i] if i < len(r) else ""
                if any(str(v).strip() for v in d.values()):
                    buy_rows.append(d)
    print("外购件库行数 %d（%d 个 sheet）" % (len(buy_rows), len(buy_sheets)))
    brands = Counter((b.get("品牌") or "").strip() for b in buy_rows)

    # ⑤ 汇总
    fam = Counter("%s/%s" % (r["group"], r["family"]) for r in parts
                  if r["group"])
    data = {
        "source": SRC,
        "n_catpart": len(parts), "n_catproduct": len(prods),
        "total_bytes": sum(max(0, r["size"]) for r in parts),
        "families": dict(fam.most_common()),
        "parts_with_params_in_name": len(dims_found),
        "param_examples": [{"file": f, "params": v, "from": s}
                           for f, v, s in dims_found[:40]],
        "bolt_library": {"rows": len(bolt_rows),
                         "types": dict(bolt_types),
                         "specs": dict(bolt_specs),
                         "sample": bolt_rows[:6]},
        "purchased": {"rows": len(buy_rows), "sheets": buy_sheets,
                      "brands": dict(brands.most_common(20)),
                      "sample": buy_rows[:6]},
        "limitation": ("CATPart 二进制只含元数据字符串（V5 版本/PN/容器标签），"
                       "不含 B-rep 几何与特征树 —— 实测确认，故**外形学不到**；"
                       "可学的是参数体系（族/规格/型号/参数组合）。"),
    }
    os.makedirs(KB, exist_ok=True)
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(KB, "parts_param_catalog.json"), "w",
                 encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # ⑥ 报告
    md = ["# 零件参数目录（来自 `零件参数` 文件夹）", "",
          "来源：`%s`" % SRC, "",
          "| 指标 | 值 |", "|---|---|",
          "| CATPart | %d 个 |" % len(parts),
          "| CATProduct | %d 个 |" % len(prods),
          "| 合计体积 | %.1f MB |" % (data["total_bytes"] / 1048576.0),
          "| 零件族 | %d 个 |" % len(fam),
          "| 文件名/目录名带参数组合的 | %d 个 |" % len(dims_found),
          "| 螺栓库行数 | %d |" % len(bolt_rows),
          "| 外购件库行数 | %d |" % len(buy_rows), "",
          "## 零件族分布", "", "| 族 | 数量 |", "|---|---|"]
    for k, v in fam.most_common(40):
        md.append("| %s | %d |" % (k, v))
    md += ["", "## 螺栓库（MyBoltLibrary.xlsx）", "",
           "| 螺栓类型 | 行数 |", "|---|---|"]
    for k, v in bolt_types.most_common():
        md.append("| %s | %d |" % (k or "（空）", v))
    md += ["", "| 螺纹规格 | 行数 |", "|---|---|"]
    for k, v in sorted(bolt_specs.items()):
        md.append("| %s | %d |" % (k or "（空）", v))
    md += ["", "## 外购件库（外购件.xlsx）", "", "| sheet | 行数 |", "|---|---|"]
    for k, v in buy_sheets.items():
        md.append("| %s | %d |" % (k, v))
    md += ["", "| 品牌 | 行数 |", "|---|---|"]
    for k, v in brands.most_common(20):
        md.append("| %s | %d |" % (k or "（空）", v))
    md += ["", "## 目录名里的参数组合（示例）", "",
           "| 零件 | 参数 | 来源 |", "|---|---|---|"]
    for f, v, s in dims_found[:30]:
        md.append("| %s | %s | `%s` |" % (f, "×".join("%g" % x for x in v), s))
    md += ["", "## 诚实边界", "",
           "- CATPart 是 CATIA 私有二进制：里面有 V5 版本、PN、容器标签（实测可见",
           "  `V5R26SP0HF0`、`CATProdCont`、`50x20x5`），**没有 B-rep 几何、没有特征树**。",
           "  所以「学会建模后的真实样子」在**没有 CATIA 的前提下做不到**。",
           "- 能学的是**参数体系**：零件族、规格、型号、参数组合、局部坐标系。",
           "- 标准件（螺钉/垫片/定位销）的「真实样子」另有一条路：它们的外形由",
           "  **国家标准/ISO 标准**定义（如内六角圆柱头螺钉 ISO 4762），",
           "  按标准尺寸表建模才是正解，而不是去逆向 CATPart。"]
    with io.open(os.path.join(REPORTS, "零件参数目录.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("\n零件族（前 12）:")
    for k, v in fam.most_common(12):
        print("   %-30s %d" % (k, v))
    print("\n螺栓类型:", dict(bolt_types))
    print("螺纹规格:", dict(sorted(bolt_specs.items())))
    print("外购件品牌:", dict(brands.most_common(10)))
    print("\n已写入 kb/parts_param_catalog.json")
    print("已写入 reports/零件参数目录.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
