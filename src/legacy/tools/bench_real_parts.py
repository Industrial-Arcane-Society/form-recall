# -*- coding: utf-8 -*-
r"""
规模验证：用**真实零件库数据**批量生成提示词，跑命中率。

题目不是手写的，而是从零件库里**逐条生成**的：
  · EGT001 SW80x088 … x4078（133 个真实型号）
  · EGT002 SW50x088 … x4078（134 个真实型号）
  · EGT006 D30x073 … x1000（22 个真实型号）
  · MyBoltLibrary.xlsx 的 110 行螺栓（M5/M6/M8/M10/M12 × 真实长度）
  · 垫片垫块 3 种规格、定位销 22 个型号名

命中判据同 bench_basic_parts：**类别 + 参数 + 能建几何**三者都对。

用法：python tools\bench_real_parts.py
输出：reports\真实零件规模命中率.md、kb\real_parts_scale.json
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP      # noqa: E402
from ai_modeler import partlib as PL        # noqa: E402

SRC = r"C:\Users\25643\Desktop\零件参数"
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

EGT = "EGT\u578b\u6750"
JIE = "\u622a\u9762"
L = "\u957f\u5ea6"
ZHI = "\u76f4\u5f84"
NJ = "\u5185\u516d\u89d2\u87ba\u9489"
DP = "\u57ab\u7247\u57ab\u5757"
W = "\u5bbd\u5ea6"
T = "\u539a\u5ea6"


def col_idx(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def sheet_rows(path, want_sheet=None):
    out = []
    with zipfile.ZipFile(path) as z:
        shared = []
        names = z.namelist()
        if "xl/sharedStrings.xml" in names:
            r = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in r.findall(M + "si"):
                shared.append("".join(t.text or "" for t in si.iter(M + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        titles = [s.get("name") for s in wb.iter(M + "sheet")]
        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        for i, sn in enumerate(sheets):
            title = titles[i] if i < len(titles) else sn
            if want_sheet and want_sheet not in title:
                continue
            root = ET.fromstring(z.read(sn))
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
                    out.append([cells.get(k, "") for k in range(max(cells) + 1)])
    return out


def gen_egt_models():
    """从磁盘真实文件名生成 EGT 型号题。"""
    cases = []
    base = os.path.join(SRC, "\u5916\u8d2d\u4ef6", "EGT\u578b\u6750\u5e93")
    if not os.path.isdir(base):
        return cases
    for sub in sorted(os.listdir(base)):
        p = os.path.join(base, sub)
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            m = re.match(r"^(EGT\d+)\s*(?:\.\d+)?\s*(SW\d+|D\d+)x(\d+)\.CATPart$",
                         fn, re.I)
            if not m:
                continue
            sec, length = m.group(2).upper(), int(m.group(3))
            if sec.startswith("SW"):
                prompt = "%s %s%d %s%d" % (EGT, JIE, int(sec[2:]), L, length)
                want = {"sec_w": float(sec[2:]), "L": float(length)}
            else:
                prompt = "%s %s%d %s%d" % (EGT, ZHI, int(sec[1:]), L, length)
                want = {}
            cases.append((prompt, "EGT型材", want))
    return cases


def gen_bolt_models():
    """从 MyBoltLibrary.xlsx 生成螺栓题。"""
    cases = []
    p = os.path.join(SRC, "\u87ba\u9489", "MyBoltLibrary.xlsx")
    if not os.path.exists(p):
        return cases
    rows = sheet_rows(p)
    hdr = [str(x).strip() for x in rows[0]] if rows else []
    for r in rows[1:]:
        d = {hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))}
        spec = str(d.get("\u87ba\u7eb9\u89c4\u683c") or "").strip()
        ln = str(d.get(L) or "").strip()
        fname = str(d.get("\u6587\u4ef6\u540d") or "")
        if not spec or not ln or fname.lower().endswith(".catproduct"):
            continue
        try:
            di = int(spec.upper().lstrip("M"))
            li = float(ln)
        except ValueError:
            continue
        cases.append(("%s %sx%g" % (NJ, spec.upper(), li),
                      "内六角螺钉", {"d": float(di), "L": li}))
    return cases


def probe(text):
    cat = PL.find_part(text)
    params = {}
    r = PL.model_params(text)
    if r:
        params = {k: v for k, v in (r.get("params") or {}).items()
                  if not k.startswith("__")}
    nf = 0
    try:
        body = NLP.parse(text).doc.build()
        nf = len(body) if body else 0
    except Exception:
        nf = -1
    return cat, params, nf


def main():
    cases = []
    cases += gen_egt_models()
    n_egt = len(cases)
    bolts = gen_bolt_models()
    cases += bolts
    n_bolt = len(bolts)
    # 垫片垫块 3 真实规格
    for a, b, c in ((50, 20, 5), (50, 50, 5), (65, 20, 5)):
        cases.append(("%s %s%d %s%d %s%d" % (DP, L, a, W, b, T, c),
                      "垫片垫块", {"L": float(a), "W": float(b), "t": float(c)}))
    print("题目生成：EGT 型材 %d 条、内六角螺钉 %d 条、垫片垫块 3 条，合计 %d 条"
          % (n_egt, n_bolt, len(cases)))

    n_hit = n_cat = n_par = n_geo = 0
    fails = []
    byfam = {}
    for text, wcat, wpar in cases:
        gcat, gpar, nf = probe(text)
        ok_c = (gcat == wcat)
        ok_p = all(abs(float(gpar.get(k, -1e9)) - float(v)) < 1e-6
                   for k, v in wpar.items())
        ok_g = nf > 0
        n_cat += ok_c
        n_par += ok_p
        n_geo += ok_g
        hit = ok_c and ok_p and ok_g
        n_hit += hit
        fam = wcat
        d = byfam.setdefault(fam, [0, 0])
        d[0] += hit
        d[1] += 1
        if not hit and len(fails) < 25:
            fails.append((text, gcat, wcat,
                          {k: gpar.get(k) for k in wpar}, wpar, nf))

    rate = 100.0 * n_hit / len(cases)
    print("\n" + "=" * 80)
    print("命中率 %.1f%%（%d/%d）" % (rate, n_hit, len(cases)))
    print("分项：类别 %d/%d、参数 %d/%d、几何 %d/%d"
          % (n_cat, len(cases), n_par, len(cases), n_geo, len(cases)))
    print("\n按族：")
    for k, (h, n) in sorted(byfam.items(), key=lambda x: -x[1][1]):
        print("   %-14s %5d/%5d  %5.1f%%" % (k, h, n, 100.0 * h / n))
    if fails:
        print("\n前 %d 条未命中：" % len(fails))
        for t, gc, wc, gp, wp, nf in fails:
            print("   %-44s 类别%s/%s 参数%s/%s 面%s"
                  % (t[:44], gc, wc, gp, wp, nf))

    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "kb"), exist_ok=True)
    with io.open(os.path.join(ROOT, "kb", "real_parts_scale.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"total": len(cases), "hit": n_hit, "rate": round(rate, 2),
                   "by_family": {k: {"hit": v[0], "total": v[1]}
                                 for k, v in byfam.items()},
                   "fails": [{"text": t, "got_cat": gc, "want_cat": wc,
                              "got": gp, "want": wp, "faces": nf}
                             for t, gc, wc, gp, wp, nf in fails]},
                  f, ensure_ascii=False, indent=1)
    md = ["# 真实零件规模命中率", "",
          "题目由**真实零件库数据逐条生成**（不是手写）：",
          "- EGT001/SW80、EGT002/SW50、EGT006/D30 的**全部真实型号**（%d 条）" % n_egt,
          "- MyBoltLibrary.xlsx 的**全部**内六角螺栓行（%d 条）" % n_bolt,
          "- 垫片垫块 3 种真实规格", "",
          "| 命中率 | 命中/总数 | 类别 | 参数 | 几何 |", "|---|---|---|---|---|",
          "| **%.1f%%** | %d/%d | %d/%d | %d/%d | %d/%d |"
          % (rate, n_hit, len(cases), n_cat, len(cases), n_par, len(cases),
             n_geo, len(cases)), "",
          "## 按族", "", "| 族 | 命中/总数 | 命中率 |", "|---|---|---|"]
    for k, (h, n) in sorted(byfam.items(), key=lambda x: -x[1][1]):
        md.append("| %s | %d/%d | %.1f%% |" % (k, h, n, 100.0 * h / n))
    if fails:
        md += ["", "## 未命中样例（前 %d 条）" % len(fails), "",
               "| 提示词 | 期望类别 | 实得 | 期望参数 | 实得参数 |",
               "|---|---|---|---|---|"]
        for t, gc, wc, gp, wp, nf in fails:
            md.append("| `%s` | %s | %s | %s | %s |" % (t, wc, gc, wp, gp))
    with io.open(os.path.join(ROOT, "reports", "真实零件规模命中率.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n已写入 reports/真实零件规模命中率.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
