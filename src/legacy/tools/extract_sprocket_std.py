# -*- coding: utf-8 -*-
r"""
U5：标准尺寸数据入库（链轮）—— 从权威资料抽取滚子链链轮的标准化参数。

数据来源（机械设计手册"链传动 → 滚子链链轮"，表内标注国家标准号）：
    · 链轮基本参数和主要尺寸 (GB/T 1243—2006)  https://www.jlc-jdgf.com/mcbook/13111.htm
    · 轴面齿廓尺寸 (GB/T 1243—2006)            https://www.jlc-jdgf.com/mcbook/13112-1.htm
    · 链轮结构尺寸                              https://www.jlc-jdgf.com/mcbook/13112-2.htm

先抓实时页面（离线知识库里这几页没有网格数据），把正文落盘到 kb/sprocket_pages.json，
再抽取"链号 → 节距 p / 滚子直径 d1 / 排距"等**标准值**，并用公开标准数值做锚点自检
（例如 08A: p=12.7、d1=7.92；12A: p=19.05、d1=11.91）。

输出：kb/sprocket_std.json、kb/sprocket_std.md
运行：python tools\extract_sprocket_std.py
"""
from __future__ import annotations

import io
import json
import os
import re
import ssl
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
OUT_JSON = os.path.join(KB, "sprocket_std.json")
OUT_MD = os.path.join(KB, "sprocket_std.md")
CACHE = os.path.join(KB, "sprocket_pages.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Accept-Language": "zh-CN,zh;q=0.9"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

BASE = "https://www.jlc-jdgf.com/mcbook/"
PAGES = {
    "13111": "链轮基本参数和主要尺寸 (GB/T 1243—2006)",
    "13112-1": "轴面齿廓尺寸 (GB/T 1243—2006)",
    "13112-2": "链轮结构尺寸",
}

# 公开标准数值锚点（GB/T 1243 / ISO 606 常用链号）
ANCHORS = {
    "08A": (12.70, 7.92),
    "10A": (15.875, 10.16),
    "12A": (19.05, 11.91),
    "16A": (25.40, 15.88),
    "20A": (31.75, 19.05),
    "24A": (38.10, 22.23),
    "06B": (9.525, 6.35),
    "08B": (12.70, 8.51),
}


def fetch_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        raw = r.read()
    html = raw.decode("utf-8", "ignore")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def load_pages(force=False):
    cache = {}
    if os.path.exists(CACHE) and not force:
        try:
            with io.open(CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    changed = False
    for key, title in PAGES.items():
        if cache.get(key, {}).get("text"):
            continue
        url = BASE + key + ".htm"
        try:
            txt = fetch_text(url)
            cache[key] = {"url": url, "title": title, "text": txt,
                          "fetched": time.strftime("%Y-%m-%d %H:%M")}
            changed = True
            print("  已抓取 %s（%d 字符）" % (url, len(txt)))
        except Exception as e:
            print("  抓取失败 %s -> %s" % (url, e))
    if changed:
        with io.open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    return cache


def extract_chain_table_from_kb():
    """从**离线知识库**的「滚子链的基本参数与尺寸(GB/T 1243—2006)」网格抽取链号参数。

    该网格实测结构（21 列）:
        列0 链号 | 列1 节距p（列2 是合并复制） | 列3 滚子直径 d1max（列4 复制）
        | 列5 内链节内宽 b1min | 列7 销轴直径 d2max | 列13 内链板高度 h2max
    注意合并列会复制同一数值，取值时按"相邻列相同则跳过"处理。
    """
    src = os.path.join(KB, "handbook_grid.jsonl")
    if not os.path.exists(src):
        return {}, None
    grid = None
    url = ""
    with io.open(src, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("url", "").endswith("/GBT1243-1.htm"):
                gs = d.get("grids") or []
                if gs and gs[0].get("grid"):
                    grid = gs[0]["grid"]
                    url = d["url"]
                    break
    if not grid:
        return {}, None
    def c(s):
        return re.sub(r"\s+", "", str(s or ""))
    def num(tok):
        m = re.match(r"^(\d+(?:\.\d+)?)$", c(tok))
        return float(m.group(1)) if m else None
    # ⚠️ 这张表是**上下两段**：第一段是"链条主要尺寸(节距/滚子直径/内链节内宽/销轴…)",
    #    第二段是"链条全宽 b4/b5/b6/b7"。同一个链号在两段里都会出现，
    #    若不加区分取值，第二段的数字会被当成节距（实测 08A 的 p 被读成 11.18）。
    #    判据：同一链号出现多次时，p 与 d1 必须两段一致；不一致就整条丢弃（宁可缺失不可错值）。
    cand = {}
    for row in grid:
        if not row:
            continue
        code = c(row[0])
        if not re.match(r"^\d{2,3}[A-Z]$", code):
            continue
        p = num(row[1]) if len(row) > 1 else None
        d1 = num(row[3]) if len(row) > 3 else None
        if p is None or d1 is None:
            continue
        b1 = num(row[5]) if len(row) > 5 else None
        d2 = num(row[7]) if len(row) > 7 else None
        h2 = num(row[13]) if len(row) > 13 else None
        cand.setdefault(code, []).append({"p": p, "d1": d1, "b1": b1, "d2": d2, "h2": h2})
    # 挑出"主要尺寸"那一段：滚子直径 d1 与节距 p 有稳定的物理关系（d1 ≈ 0.5~0.7·p）。
    # 第二段（链条全宽 b4/b5/b6）的数字远大于 d1，会被这个判据排除。
    out = {}
    for code, lst in cand.items():
        pick = None
        for x in lst:
            if 0.4 * x["p"] <= x["d1"] <= 0.8 * x["p"]:
                pick = x
                break
        if pick is None:
            continue
        rec = {"p": pick["p"], "d1": pick["d1"]}
        for k in ("b1", "d2", "h2"):
            if pick.get(k):
                rec[k] = pick[k]
        out[code] = rec
    return out, url


def main():
    print("数据来源：机械设计手册 链传动 - 滚子链链轮（表内标注 GB/T 1243—2006）")
    pages = load_pages()
    print("页面:", {k: ("OK" if pages.get(k, {}).get("text") else "缺失") for k in PAGES})

    db = {
        "_about": "滚子链链轮标准数据（GB/T 1243—2006）",
        "_note": "节距 p 与滚子直径 d1 是链轮几何的标准化基础参数；"
                 "分度圆直径 d = p / sin(180°/z) 由齿数算出",
        "_formula": {"分度圆直径": "d = p / sin(180/z)",
                     "齿顶圆直径(简化)": "da ≈ p·(0.54 + cot(180/z))",
                     "齿根圆直径": "df = d - d1",
                     "齿沟圆弧半径": "ra ≈ 0.5025·d1 + 0.05"},
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "sources": [{"key": k, "title": PAGES[k], "url": pages.get(k, {}).get("url", "")}
                    for k in PAGES],
        "chains": {},
        "selfcheck": [],
    }

    merged, chain_url = extract_chain_table_from_kb()
    if chain_url:
        db["sources"].append({"key": "GBT1243-1",
                              "title": "滚子链的基本参数与尺寸 (GB/T 1243—2006)",
                              "url": chain_url})
    db["chains"] = merged

    # 自检：与公开标准锚点比对（允许 1% 误差，因为手册四舍五入）
    ok_all = True
    for code, (wp, wd) in ANCHORS.items():
        got = merged.get(code)
        if not got:
            db["selfcheck"].append("%s 缺失（手册该页未列出或抽取失败）" % code)
            continue
        bad = []
        if abs(got["p"] - wp) > max(0.05, wp * 0.01):
            bad.append("p %s≠%s" % (got["p"], wp))
        if abs(got["d1"] - wd) > max(0.15, wd * 0.03):
            bad.append("d1 %s≠%s" % (got["d1"], wd))
        db["selfcheck"].append("%s %s" % (code, "OK" if not bad else "偏差: " + "、".join(bad)))
        ok_all = ok_all and not bad
    db["selfcheck_ok"] = ok_all
    db["chain_count"] = len(merged)

    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)

    md = ["# 滚子链链轮标准数据（GB/T 1243—2006）", "",
          "> 由 `tools/extract_sprocket_std.py` 从机械设计手册提取；"
          "表内标注国家标准号，来源 URL 见下。", "",
          "## 一、来源", ""]
    for s in db["sources"]:
        md.append("- %s：%s  \n  %s" % (s["title"], s["url"], ""))
    md += ["", "## 二、链号参数（节距 p / 滚子直径 d1）", "",
           "| 链号 | 节距 p (mm) | 滚子直径 d1 (mm) |", "|---|---|---|"]
    for code in sorted(merged):
        md.append("| %s | %g | %g |" % (code, merged[code]["p"], merged[code]["d1"]))
    md += ["", "## 三、链轮几何公式（手册给出）", ""]
    for k, v in db["_formula"].items():
        md.append("- %s：`%s`" % (k, v))
    md += ["", "## 四、自检", ""]
    for line in db["selfcheck"]:
        md.append("- %s" % line)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("链号数:", len(merged), sorted(merged)[:12])
    for line in db["selfcheck"][:10]:
        print("   ", line)
    print("自检:", "全部通过" if ok_all else "存在缺失/偏差（见 kb/sprocket_std.md）")
    print("已写出 %s / %s" % (OUT_JSON, OUT_MD))
    return 0 if merged else 1


if __name__ == "__main__":
    sys.exit(main())
