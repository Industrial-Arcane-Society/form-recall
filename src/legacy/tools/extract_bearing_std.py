# -*- coding: utf-8 -*-
r"""
U5：标准尺寸数据入库（轴承）—— 抽取 GB/T 276 深沟球轴承的**标准尺寸系列**。

数据来源（机械设计手册"滚动轴承 → 常用滚动轴承尺寸及性能参数"，表内标注国家标准号）：
    · 轴径 3～25   深沟球轴承 (GB/T 276—2013)  https://www.jlc-jdgf.com/mcbook/7-2-52-01.htm
    · 轴径 30～70  深沟球轴承 (GB/T 276—2013)  https://www.jlc-jdgf.com/mcbook/7-2-52-02.htm
    · 轴径 75～140 深沟球轴承 (GB/T 276—2013)  https://www.jlc-jdgf.com/mcbook/7-2-52-03.htm
    · 轴径 150～500 深沟球轴承 (GB/T 276—2013) https://www.jlc-jdgf.com/mcbook/7-2-52-04.htm

表格结构（实测）：主表列序为
    0 d(内径) | 1 D(外径) | 2 B(宽度) | 3 Cr | 4 C0r | 5 脂 | 6 油 | 7 W | 8 轴承代号
其中 d 列是**合并单元格**（同内径的多个代号只写一次），因此不用 d 列配行，
改为**由轴承代号反推内径**再取该行的 D、B —— 这也是 GB/T 276 的编码规则：
    · "617/x""618/x""619/x"  → 内径 = x（可为小数，如 617/0.6 → 0.6）
    · 其余 4 位数字代号         → 内径 = 末两位 × 5（00/01/02/03 特例：10/12/15/17）
    · 5 位代号（如 61806）      → 内径 = 末两位 × 5（61806 → 30）

自检锚点（公开的标准值，写死在这里当"金标准"）：
    6204 = 20×47×14、6205 = 25×52×15、6305 = 25×62×17、6000 = 10×26×8、
    6001 = 12×28×8、6002 = 15×32×9、6200 = 10×30×9

输出：kb/bearing_std.json、kb/bearing_std.md
运行：python tools\extract_bearing_std.py
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
OUT_JSON = os.path.join(KB, "bearing_std.json")
OUT_MD = os.path.join(KB, "bearing_std.md")
CACHE = os.path.join(KB, "bearing_pages.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Accept-Language": "zh-CN,zh;q=0.9"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

BASE = "https://www.jlc-jdgf.com/mcbook/"
PAGES = {
    "7-2-52-01": "轴径3～25 深沟球轴承 (GB/T 276—2013)",
    "7-2-52-02": "轴径30～70 深沟球轴承 (GB/T 276—2013)",
    "7-2-52-03": "轴径75～140 深沟球轴承 (GB/T 276—2013)",
    "7-2-52-04": "轴径150～500 深沟球轴承 (GB/T 276—2013)",
}

#: 公开的标准值锚点（内径 d, 外径 D, 宽度 B）—— 抽取结果必须与之一致，否则报错
ANCHORS = {
    "6000": (10.0, 26.0, 8.0),
    "6001": (12.0, 28.0, 8.0),
    "6002": (15.0, 32.0, 9.0),
    "6004": (20.0, 42.0, 12.0),
    "6006": (30.0, 55.0, 13.0),
    "6200": (10.0, 30.0, 9.0),
    "6202": (15.0, 35.0, 11.0),
    "6204": (20.0, 47.0, 14.0),
    "6205": (25.0, 52.0, 15.0),
    "6206": (30.0, 62.0, 16.0),
    "6300": (10.0, 35.0, 11.0),
    "6304": (20.0, 52.0, 15.0),
    "6305": (25.0, 62.0, 17.0),
    "6306": (30.0, 72.0, 19.0),
    "6403": (17.0, 62.0, 17.0),
}

#: 末两位 -> 内径 的特例（GB/T 276 编码规则）
_BORE_SPECIAL = {"00": 10.0, "01": 12.0, "02": 15.0, "03": 17.0}


# ------------------------------------------------------------------ 抓取
def fetch_tables(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        html = r.read().decode("utf-8", "ignore")
    out = []
    for tm in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S | re.I):
        rows = []
        for rm in re.finditer(r"<tr[^>]*>(.*?)</tr>", tm.group(1), re.S | re.I):
            row = []
            for cm in re.finditer(r"<t[dh][^>]*>(.*?)</t[dh]>", rm.group(1), re.S | re.I):
                txt = re.sub(r"<[^>]+>", "", cm.group(1))
                txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
                row.append(re.sub(r"\s+", "", txt))
            if row:
                rows.append(row)
        if rows:
            out.append(rows)
    return out


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
        if cache.get(key, {}).get("rows"):
            continue
        url = BASE + key + ".htm"
        try:
            tabs = fetch_tables(url)
            rows = tabs[0] if tabs else []
            cache[key] = {"url": url, "title": title, "rows": rows,
                          "fetched": time.strftime("%Y-%m-%d %H:%M")}
            changed = True
            print("  已抓取 %s（%d 行）" % (url, len(rows)))
        except Exception as e:
            print("  抓取失败 %s -> %s" % (url, e))
    if changed:
        with io.open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    return cache


# ------------------------------------------------------------------ 解析
def bore_from_code(code):
    """由轴承代号反推内径（GB/T 276 编码规则）。认不出返回 None。

    ⚠️ 判定顺序**不能颠倒**：必须先认 4~5 位纯数字代号，再认 "617/x" 写法。
    早先让 `(?:617|618|619)(\\d{2})` 排在前面，"61700" 会被它贪婪吃掉前两位
    （分组拿到 "70" → 内径 70 又被 D 比较挡掉 → 整条丢失或算成 0），
    实测 61700/61800/61900 三条内径全变成 0。
    """
    c = (code or "").strip().upper()
    if not c:
        return None
    # ① 5 位代号（61700/61806/16006…）：末两位是内径码
    if re.match(r"^\d{5}$", c):
        tail = c[-2:]
        if tail in _BORE_SPECIAL:
            return _BORE_SPECIAL[tail]
        v = float(tail) * 5.0
        return v if 4 <= int(tail) <= 99 else None
    # ② 带斜杠的分数内径写法（617/0.6、618/1）
    m = re.match(r"^(?:617|618|619)/(\d+(?:\.\d+)?)$", c)
    if m:
        return float(m.group(1))
    # ③ 老的 4 位代号（6000/6204/6305…）
    if re.match(r"^\d{4}$", c):
        tail = c[-2:]
        if tail in _BORE_SPECIAL:
            return _BORE_SPECIAL[tail]
        return float(tail) * 5.0
    return None


def series_of(code):
    """取系列号用于分组展示：617/618/619/160 是 3 位前缀，其余按前 2 位。"""
    c = (code or "").upper()
    for p in ("617", "618", "619", "160"):
        if c.startswith(p):
            return p
    return c[:2]


def _num(tok):
    t = re.sub(r"\s+", "", str(tok or ""))
    if not t:
        return None
    t = t.replace("×", "x")
    m = re.match(r"^(\d+(?:\.\d+)?)$", t)
    return float(m.group(1)) if m else None


def extract_table(rows):
    """从一张深沟球轴承表里抽出 {代号: {d,D,B,series}}。"""
    out = {}
    # 定位表头行（含 'd','D','B' 与 '60000型'）
    head_i = None
    for i, r in enumerate(rows[:12]):
        j = " ".join(r)
        if "60000" in j and re.search(r"\bd\b", j):
            head_i = i
            break
    if head_i is None:
        return out
    head = rows[head_i]
    try:
        i_d = head.index("d")
        i_D = head.index("D")
        i_B = head.index("B")
    except ValueError:
        i_d, i_D, i_B = 0, 1, 2
    # 代号列：表头里形如 '60000型'
    i_code = None
    for k, h in enumerate(head):
        if "型" in h and re.match(r"^\d{4,6}型?$", h.replace("型", "") + "型") is None:
            pass
        if "60000" in h:
            i_code = k
    if i_code is None:
        i_code = 8
    for r in rows[head_i + 1:]:
        if len(r) <= i_code:
            continue
        code = r[i_code].strip()
        if not code or not re.match(r"^(\d{4,5}|(?:617|618|619)/\d+(?:\.\d+)?)$", code):
            continue
        bore = bore_from_code(code)
        D = _num(r[i_D]) if len(r) > i_D else None
        B = _num(r[i_B]) if len(r) > i_B else None
        if bore is None or D is None or bore <= 0:
            continue
        # 编码规则反推出的内径必须与外径量级相容（外径必然大于内径）
        if D <= bore:
            continue
        series = series_of(code)
        out[code] = {"d": bore, "D": D, "B": B, "series": series}
    return out


def main():
    print("U5 轴承标准数据入库（GB/T 276—2013 深沟球轴承）")
    cache = load_pages()
    all_rows = {k: v.get("rows") or [] for k, v in cache.items()}
    table = {}
    for key in PAGES:
        got = extract_table(all_rows.get(key) or [])
        print("  %s -> %d 个轴承代号" % (key, len(got)))
        table.update(got)

    # 锚点自检：抽取结果必须与公开标准值一致
    print("\n锚点自检：")
    bad = []
    for code, (d, D, B) in sorted(ANCHORS.items()):
        got = table.get(code)
        if not got:
            print("  %-6s 缺（表里没有这个代号）" % code)
            bad.append((code, "缺"))
            continue
        ok = (abs(got["d"] - d) < 1e-6 and abs(got["D"] - D) < 1e-6
              and abs(got["B"] - B) < 1e-6)
        print("  %-6s %s 期望 %gx%gx%g  实测 %gx%gx%g"
              % (code, "✅" if ok else "❌", d, D, B,
                 got["d"], got["D"], got["B"]))
        if not ok:
            bad.append((code, "值不符"))

    db = {
        "standard": "GB/T 276—2013 滚动轴承 深沟球轴承 外形尺寸",
        "source": [{"url": v["url"], "title": v["title"]} for v in cache.values()],
        "note": "内径由轴承代号按 GB/T 276 编码规则反推；D/B 取自手册尺寸表。",
        "series_note": {
            "617": "超特轻(0)系列", "618": "特轻(1)系列", "619": "特轻(2)系列",
            "160": "特轻(3)系列", "60": "轻(1)系列", "62": "轻(2)系列",
            "63": "中(3)系列", "64": "中(4)系列",
        },
        "count": len(table),
        "bearings": dict(sorted(table.items())),
        "anchors_checked": len(ANCHORS),
        "anchors_failed": [c for c, _ in bad],
    }
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    print("\n已写入 %s（%d 条）" % (OUT_JSON, len(table)))

    lines = ["# 深沟球轴承标准尺寸（GB/T 276—2013）", "",
             "数据来源：机械设计手册「常用滚动轴承尺寸及性能参数」，表内标注国家标准号。", ""]
    for s in db["source"]:
        lines.append("* %s — %s" % (s["title"], s["url"]))
    lines += ["", "| 轴承代号 | 内径 d | 外径 D | 宽度 B | 系列 |", "|---|---|---|---|---|"]
    for code, v in db["bearings"].items():
        lines.append("| %s | %g | %g | %s | %s |"
                     % (code, v["d"], v["D"],
                        ("%g" % v["B"]) if v["B"] is not None else "—",
                        db["series_note"].get(v["series"], v["series"])))
    lines += ["", "锚点自检：%d 项，失败 %d 项 %s"
              % (db["anchors_checked"], len(db["anchors_failed"]),
                 db["anchors_failed"] or "（全部通过）")]
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("已写入 %s" % OUT_MD)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
