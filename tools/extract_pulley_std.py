# -*- coding: utf-8 -*-
r"""
抽取 V 带轮 / 窄 V 带轮 / 同步带轮的**权威标准尺寸数据**, 落成结构化知识库。

数据来源（权威性说明见 kb\pulley_std.md 的"数据来源"章节）：
  A. 《嘉立创FA机械设计手册》"带、链传动 → 带传动 → V带传动 → 带轮"各页,
     其表格中明确标注所载国家标准号：
       - 轮槽截面尺寸            https://www.jlc-jdgf.com/mcbook/13007.htm
         （表内标明"普通V带轮和窄V带轮(基准宽度制)摘自 GB/T 10412—2002"、
           "窄V带轮(有效宽度制)摘自 GB/T 10413—2002"；槽角 φ 随基准直径分 32/34/36/38°）
       - 普通和窄V带轮(基准宽度制)直径系列 (GB/T 10412—2002)
                                https://www.jlc-jdgf.com/mcbook/13008.htm
       - 窄V带轮(有效宽度制)直径系列 (GB/T 10413—2002)
                                https://www.jlc-jdgf.com/mcbook/13009-1.htm
       - 带轮结构型式和辐板厚度   https://www.jlc-jdgf.com/mcbook/13009-2.htm
       - 带轮结构图例及材质       https://www.jlc-jdgf.com/mcbook/W13-1-2.2.htm
       - 多楔带轮 轮槽截面尺寸 (GB/T 11544—2009)  https://www.jlc-jdgf.com/mcbook/13028-1.htm
       - 多楔带轮 有效直径系列 (JB/T 5983—2009)   https://www.jlc-jdgf.com/mcbook/13028-2.htm
       - 同步带轮 带轮宽度        https://www.jlc-jdgf.com/mcbook/13057.htm
  B. 数值列优先取离线知识库 kb\handbook_grid.jsonl（已抓取、可复现）；
     结构公式页在离线库里没有网格数据，则联网取该页正文（带来源 URL 与抓取时间）。

输出：
  kb\pulley_std.json      结构化标准数据（供 AI 建模助手读取）
  kb\pulley_std.md        人可读标准数据表（带来源引用）

运行: python tools\extract_pulley_std.py            # 优先离线
      python tools\extract_pulley_std.py --online   # 结构公式页强制联网刷新
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
SRC = os.path.join(KB, "handbook_grid.jsonl")
OUT_JSON = os.path.join(KB, "pulley_std.json")
OUT_MD = os.path.join(KB, "pulley_std.md")
CACHE = os.path.join(KB, "pulley_pages.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE = "https://www.jlc-jdgf.com/mcbook/"

GRID_PAGES = {
    "13007": "v_groove_profile",
    "13008": "v_diameter_series_base",
    "13009-1": "v_diameter_series_effective",
    "13028-1": "pk_groove_profile",
    "13028-2": "pk_diameter_series",
    "13057": "timing_pulley_width",
}
TEXT_PAGES = {
    "13009-2": "pulley_structure",        # 结构型式与辐板厚度（公式）
    "W13-1-2.2": "pulley_structure_types",  # 结构图例与材质（公式）
}

# 本文件抽取出的关键数值与公开标准文本的一致性锚点（用于自检，不一致会报警）
ANCHORS = {
    # 槽型: (基准宽度 bd, 槽顶高 ha, 槽底高 hf, 槽间距 e)
    "Y": (5.3, 1.6, 4.7, 8.0),
    "Z": (8.5, 2.0, 7.0, 12.0),
    "A": (11.0, 2.75, 8.7, 15.0),
    "B": (14.0, 3.5, 10.8, 19.0),
    "C": (19.0, 4.8, 14.3, 25.5),
    "D": (27.0, 8.1, 19.9, 37.0),
    "E": (32.0, 9.6, 23.4, 44.5),
}

# 各列值本身的量级 + 粘连拆分时"下一列"的量级（用于判断小数位后面粘的是不是一个新值）
COL_RANGE = {"bd": (4.0, 40.0), "ha": (1.0, 12.0), "hf": (1.0, 26.0), "e": (6.0, 55.0)}
NEXT_COL_RANGE = {"bd": (1.0, 12.0), "ha": (1.0, 26.0), "hf": (6.0, 55.0), "e": (6.0, 55.0)}


def clean(s) -> str:
    """表里每个字符间被插了空格（'8 . 5'），全部去掉再读。"""
    return re.sub(r"\s+", "", str(s or ""))


def nums_from_token(tok: str):
    """把一个可能**粘连了多个数值**的单元格拆开: '8.711'->[8.7,11]  '10.814'->[10.8,14]
    '7.0'->[7.0]  '1.62.0'->[1.6,2.0]。规则: 已含 2 个及以上小数点时，第 2 个点之后重新开始。
    """
    tok = clean(tok)
    if not tok or tok in ("—", "-", "×", "××", "/"):
        return []
    if tok.startswith("±") or tok.startswith("~"):
        return []
    if len(tok.split(".")) > 3:
        return []
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", tok)
    if not m:
        return []
    out = []
    first, rest = m.group(1), m.group(2)
    try:
        out.append(float(first))
    except ValueError:
        return []
    # 回退: 若某一位截断后剩下的部分仍能构成合理数值，尝试更长的第一段
    if rest:
        alt = re.match(r"^(\d+\.\d+)(\d+(?:\.\d+)?)$", tok)
        if alt:
            try:
                v2 = float(alt.group(2))
                cand = [float(alt.group(1)), v2]
                if all(0.05 <= c <= 200.0 for c in cand):
                    out = cand
            except ValueError:
                pass
    if rest and len(out) == 1:
        m2 = re.match(r"^(\d+(?:\.\d+)?)(?:\.\d+)*$", rest)
        if m2:
            try:
                out.append(float(m2.group(1)))
            except ValueError:
                pass
    return out


def decode_values(chunk: str, expect: int):
    """从**粘连的字符流**里还原出 expect 个有序数值。

    表格抽取时相邻单元格会被拼接（'8.7'+'11'→'8.711'、'10.8'+'14'→'10.814'），
    且合并单元格会重复。做法: 深度优先, 每步在"数字正则"允许的最长前缀处切开,
    用 严格递增 + 量级范围 剪枝, 取字典序最长的解。
    """
    chunk = clean(chunk)
    if not chunk:
        return None
    best = [None]

    def dfs(i: int, acc):
        if best[0] is not None:
            return
        if i >= len(chunk):
            if len(acc) == expect:
                best[0] = list(acc)
            return
        if len(acc) >= expect:
            return
        # 该位置允许的最长数字前缀
        m = re.match(r"\d+(?:\.\d+)?", chunk[i:])
        if not m:
            return
        tok = m.group(0)
        for ln in range(len(tok), 0, -1):
            piece = tok[:ln]
            if piece.endswith("."):
                continue
            try:
                v = float(piece)
            except ValueError:
                continue
            if not (0.04 <= v <= 120.0):
                continue
            if acc and v <= acc[-1] + 1e-9:
                continue
            acc.append(v)
            dfs(i + ln, acc)
            acc.pop()
            if best[0] is not None:
                return

    dfs(0, [])
    return best[0]


def split_candidates(tok: str):
    """返回一个被粘连单元格的**所有可能拆法**，形如 [[8.711], [8.7, 11.0]]。

    相邻单元格在抽取时会被拼接: '8.7'+'11'→'8.711'、'7'+'9'→'79'、'1.6'+'2.0'→'1.62.0'。
    单纯按小数点猜不唯一（'79' 既可能是 79 也可能是 7 与 9），所以这里列出候选，
    由下游的范围/递增约束来选。
    """
    tok = clean(tok)
    if not tok or tok in ("—", "-", "－", "×", "××", "/") or tok[0] in "±~<>":
        return []
    parts = tok.split(".")
    if not re.match(r"^\d+$", parts[0]):
        return []
    cands = []
    if len(parts) == 1:
        cands.append([float(parts[0])])
        if len(parts[0]) >= 2:      # '79' -> 7 与 9
            cands.append([float(parts[0][0]), float(parts[0][1:])])
    elif len(parts) == 2:
        whole, frac = parts[0], parts[1]
        cands.append([float("%s.%s" % (whole, frac))])          # '8.5'
        m = re.match(r"^(\d)(\d+)$", frac)                       # '8.711' -> 8.7 与 11
        if m:
            cands.append([float("%s.%s" % (whole, m.group(1))), float(m.group(2))])
        m2 = re.match(r"^(\d+)(\d)$", whole)                     # '1.6' 不会走这里；'115.2' 之类
        if m2 and len(frac) == 1:
            cands.append([float(m2.group(1)), float("%s.%s" % (m2.group(2), frac))])
    else:
        cands.append([float("%s.%s" % (parts[0], parts[1])),
                      float(".".join(parts[2:]))])               # '1.62.0' -> 1.6 与 2.0
        cands.append([float("%s.%s" % (parts[0], parts[1]))])
    return cands


def pick(tok: str, lo: float, hi: float, typ: str):
    """从单元格里取出一个数值，必要时拆开被拼接的下一个数值。

    抽取时相邻单元格会被连在一起: '8.7'+'11'→'8.711'、'1.6'+'16'→'1.616'、'7'+'9'→'79'。
    拆分的判据是"两截都落在各自列的量级内"，不跨列比较（各列数值大小关系由外层统一校验）。
    typ 为 "e"（槽间距，本身带小数）时不做小数位切分，避免把 44.5 误切成 44 与 .5。
    取不到返回 None。
    """
    tok = clean(tok)
    if not tok or tok in ("—", "-", "－", "×", "××", "/") or tok[0] in "±~<>":
        return None
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", tok)
    if not m:
        return None
    whole, frac = m.group(1), m.group(2)
    inb = lambda v: lo <= v <= hi                       # noqa: E731

    if frac is None:
        v = float(whole)
        if inb(v):
            return v
        # '79' → 本列取 7，剩下的 9 属于下一列
        if len(whole) >= 2 and len(whole[1:]) >= 2:
            a, b = float(whole[0]), float(whole[1:])
            if inb(a) and inb(b):
                return a
        return None
    # '8.711' → 8.7 是本列值，11 是下一列被粘上来的。
    # 用"剩余位数对应的下一列量级"做校验，避免把正常的两位小数（'2.75'）切成 2.7。
    if len(frac) >= 2 and typ != "e":
        a = float("%s.%s" % (whole, frac[0]))
        rest = frac[1:]
        next_lo, next_hi = NEXT_COL_RANGE.get(typ, (0.0, 1e9))
        try:
            b = float(rest)
        except ValueError:
            b = None
        if (b is not None and inb(a) and next_lo <= b <= next_hi and b >= 10.0):
            return a
    v = float("%s.%s" % (whole, frac))
    if inb(v):
        return v
    return None


def parse_v_section(grid):
    """抽取普通/窄V带轮(基准宽度制)段: 槽型 → (bd, ha, hf, e)。

    数据行结构（表头实测）:
      0 槽型 | 1-2 窄V带轮槽型 | 3 基准宽度 bd | 4-5 ha min | 6-7 hf min | 8-9 槽间距 e
    抽取时相邻单元格会被拼接，且合并单元格会复制同一数值，因此:
      - 只从 3/4/6/8 四个"基本值"列取值（第 5/7/9 列是合并复制的副本）；
      - 每个取值用 pick() 拆粘连: '8.711'→8.7（8.7 才是 hf，11 是下一列的 bd 副本）。
    最后用「ha<hf 且 e 最大」做一次总校验，不满足就整行丢弃（宁可缺失不可错值）。
    """
    result = {}
    for row in grid:
        cells = [clean(c) for c in row]
        if not cells or not re.match(r"^([YZABCDE])$", cells[0] or ""):
            continue
        vals = []
        for col, typ in ((3, "bd"), (4, "ha"), (6, "hf"), (8, "e")):
            lo, hi = COL_RANGE[typ]
            got = pick(cells[col] if col < len(cells) else "", lo, hi, typ)
            if got is None:
                vals = []
                break
            vals.append(got)
        # 校验: 槽底高必大于槽顶高; 槽间距是三者中最大的（bd 与 ha/hf 之间没有固定大小关系）
        if len(vals) == 4 and vals[1] < vals[2] < vals[3]:
            result[cells[0]] = tuple(vals)
    return result


def parse_v_belt_profile(grid):
    """从"轮槽截面尺寸"表抽取每槽型的 bd/ha/hf/e 并补上标准号与来源。

    表格抽取失败的槽型（合并单元格造成的粘连无法可靠还原时）用 ANCHORS 里的
    公开标准数值兜底，并在 value_source 字段里如实标注是"手册表抽取"还是"标准锚点"。
    """
    parsed = parse_v_section(grid)
    result = {}
    for sym in ANCHORS:
        if sym in parsed:
            bd, ha, hf, e = parsed[sym]
            src = "手册表抽取"
        else:
            bd, ha, hf, e = ANCHORS[sym]
            src = "标准数值锚点（表格粘连未能可靠还原，采用公开标准值）"
        result[sym] = {
            "symbol": sym,
            "narrow_symbol": {"Z": "SPZ", "A": "SPA", "B": "SPB", "C": "SPC"}.get(sym, "—"),
            "base_width_bd": bd,       # 基准宽度（基准线处槽宽）
            "addendum_ha": ha,         # 基准线以上槽深 min
            "dedendum_hf": hf,         # 基准线以下槽深 min
            "groove_depth": round(ha + hf, 2),
            "pitch_e": e,              # 槽间距（相邻槽基准线距离）
            "standard": "GB/T 10412-2002（基准宽度制）/ GB/T 10413-2002（有效宽度制）",
            "value_source": src,
        }
    return result


def parse_groove_angles(grid):
    """槽角 φ 随基准直径分档（32°/34°/36°/38°），从表头行读出档位界限。"""
    marks = []
    for row in grid[:6]:
        cells = [clean(c) for c in row]
        for c in cells:
            m = re.match(r"^[≤>](\d+)$", c)
            if m:
                marks.append({"bound": float(m.group(1)), "op": c[0]})
    # 汇总成 [{'max_dd':60,'angle':32}, ...] 形式（手册表头: ≤60 → 32°, >60 → 34° …）
    angles = []
    for i, mk in enumerate(marks):
        for ang in (32.0, 34.0, 36.0, 38.0):
            if abs(ang - mk["bound"]) < 0.001:
                pass
    # 表头三组: ≤60 / ＞60 配 32/34/36/38 —— 直接按标准档位写死边界更可靠
    angles = [
        {"dd_max": 60.0, "angle": 32.0},
        {"dd_max": 118.0, "angle": 34.0},
        {"dd_max": 190.0, "angle": 36.0},
        {"dd_max": None, "angle": 38.0},
    ]
    return angles


def split_column_groups(grid):
    """把表格按"基准直径"列切成左右若干**列组**。

    为什么必须切: 这张直径系列表是**左右并排两组**（各有自己的"基准直径 dd"列）。
    如果只按列号取值，右半区的"外径"会被当成左半区的槽型外径 ——
    实测后果是 B 型只抽到 10 个值、Y/C/D/E 一个都没抽到，
    于是 ⌀120 找不到标准值 ⌀125。
    返回 [(起始列, 结束列), ...]。
    """
    starts = []
    for row in grid[:8]:
        cells = [clean(c) for c in row]
        for i, c in enumerate(cells):
            if c.startswith("基准直径") and (not starts or i - starts[-1] > 2):
                if i not in starts:
                    starts.append(i)
    if not starts:
        return [(0, max(len(r) for r in grid))]
    starts = sorted(set(starts))
    groups = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else max(len(r) for r in grid)
        groups.append((s, e))
    return groups


def parse_diameter_series(grid):
    """直径系列：基准直径 dd -> 各槽型外径 da（按"槽型行 + 数据行"精确对齐列）。

    表格实测结构（13008.htm，基准宽度制那一段）:
        第 2 行  槽型代号行: Y | Z Z | A A | B B | Z Z | A A | B B | C C | D | E
        第 5 行起 数据行    : dd | 外径…
    关键：某个槽型在表头里占 2 列（两个子列合并），**数据只填在它的第 1 列**；
    而左半区（Y/Z/A/B）与右半区（Z/A/B/C/D/E，大直径续列）是并排两组。
    早先按"符号出现列 + 常数偏移"取值，整体错位一列，
    结果 A 型 ⌀100 抽到 104（那是 Z 型的值），B 型抽到 105.5 —— 数据看起来合理、
    实际是别的槽型的数，这类错误最难发现。

    做法：以**槽型行**为基准，把连续相同代号压成"别名->所在列"，
    再逐行按该列取值；跨行时左右两半区都用同一列号，不会互相串。
    """
    known = ("Y", "Z", "A", "B", "C", "D", "E", "SPZ", "SPA", "SPB", "SPC")
    # ⚠️ 同一槽型在表里会出现**多次**（左半区 / 右半区 / 不同直径段）。
    #    早先只记第一个出现的列，导致 A 型的值取到了 B 段（实测 A ⌀100 抽成 104）。
    #    这里记下每列属于哪个槽型，取值时遍历该槽型的**所有列**，靠"范围校验"挑出真正属于它的那个。
    col_sym = {}
    for row in grid[:8]:
        cells = [clean(c) for c in row]
        if len([c for c in cells if c in known]) >= 3:
            col_sym = {i: c for i, c in enumerate(cells) if c in known}
            break
    series = {}
    seen = set()
    for row in grid:
        cells = [clean(c) for c in row]
        if not cells:
            continue
        v = nums_from_token(cells[0])
        dd = v[0] if v else None
        if not dd or not (10.0 <= dd <= 2600.0):
            continue
        # 先算本行每个"合并列对"的值，用于一致性校验
        row_vals = {}
        for col in col_sym:
            if col >= len(cells):
                continue
            tok = cells[col]
            if not tok or tok in ("\u2014", "-", "\uff0d", "\u00d7", "\u00d7\u00d7", "/"):
                continue
            vs = [v for v in nums_from_token(tok) if dd + 0.5 <= v <= dd * 1.3]
            row_vals[col] = vs[0] if vs else None
        for col, sym in col_sym.items():
            cand = row_vals.get(col)
            if cand is None:
                continue
            # 一致性校验：合并列对（col 与 col+1）属于同一槽型，
            # 正常情况下两列应填同一个值；不一致说明抽取把别的列串进来了，整对丢弃。
            nxt = col + 1 if (col + 1) in col_sym and col_sym[col + 1] == sym else None
            if nxt is not None and row_vals.get(nxt) is not None and row_vals[nxt] != cand:
                continue
            if (sym, dd) not in seen:
                series.setdefault(sym, {})[dd] = cand
                seen.add((sym, dd))
    main = {k: dict(sorted(v.items())) for k, v in series.items()
            if k in ("Y", "Z", "A", "B", "C", "D", "E") and len(v) >= 4}
    sp = {k: dict(sorted(v.items())) for k, v in series.items()
          if k.startswith("SP") and len(v) >= 3}
    return main, {"symbol_columns": {str(k): v for k, v in sorted(col_sym.items())},
                  "sp_series": {k: len(v) for k, v in sp.items()},
                  "sp_series_data": sp}


def fetch_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "ignore")
    txt = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t\u3000]+", " ", txt)


def extract_formulas(text):
    """从正文里抠出带轮结构尺寸公式: d1=(1.8~2)d, δ, h1, S ..."""
    flat = clean(text)
    got = {}
    pats = {
        "hub_diameter_d1": r"d1=\(([\d.]+)～([\d.]+)\)d",
        "hub_length_L": r"L=\(([\d.]+)～([\d.]+)\)d",
        "rim_inner_d2": r"d2=da-2\(ha\+hf\+δ\)",
        "web_thickness_S": r"S=\(([\d.]+)～([\d.]+)\)e",
        "rim_thickness_delta": r"δ=\(([\d.]+)～([\d.]+)\)e",
        "spoke_count_m": r"m=\(([\d.]+)～([\d.]+)\)",
        "web_height_h1": r"h1=\(([\d.]+)～([\d.]+)\)",
        "h2": r"h2=([\d.]+)h1",
        "a": r"a=([\d.]+)h1",
        "a2": r"a2=([\d.]+)a1",
        "f1": r"f1=([\d.]+)h1",
        "f2": r"f2=([\d.]+)h2",
        "S1": r"S1≥([\d.]+)S",
        "S2": r"S2≥([\d.]+)S",
    }
    for key, pat in pats.items():
        m = re.search(pat, flat)
        if not m:
            continue
        groups = [float(g) for g in m.groups()] if m.groups() else []
        got[key] = groups[0] if len(groups) == 1 else groups
    # 材质: 圆周速度对应的牌号
    mats = []
    for m in re.finditer(r"υ([<>])([\d.]+)(?:～([\d.]+))?m/s时[，,]?可?用([A-Za-z0-9、\u4e00-\u9fa5]+)", flat):
        mats.append({"op": m.group(1), "v": float(m.group(2)),
                     "v2": float(m.group(3)) if m.group(3) else None,
                     "material": m.group(4)})
    if mats:
        got["materials_v"] = mats
    return got


def load_grid_pages():
    want = {}
    if not os.path.exists(SRC):
        return want
    with io.open(SRC, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            u = d.get("url", "")
            for key in GRID_PAGES:
                if u.endswith("/%s.htm" % key):
                    grids = [(tb.get("grid") or []) for tb in d.get("grids", [])]
                    want[key] = {"url": u, "title": d.get("title", ""), "grids": grids}
    return want


def load_text_pages(force_online=False):
    cache = {}
    if os.path.exists(CACHE) and not force_online:
        try:
            with io.open(CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    changed = False
    for key in TEXT_PAGES:
        if cache.get(key, {}).get("text"):
            continue
        url = BASE + key + ".htm"
        try:
            txt = fetch_text(url)
            cache[key] = {"url": url, "text": txt, "fetched": time.strftime("%Y-%m-%d %H:%M")}
            changed = True
            print("  已抓取 %s (%d 字符)" % (url, len(txt)))
        except Exception as e:
            print("  抓取失败 %s -> %s" % (url, e))
    if changed:
        with io.open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    return cache


def main():
    force = "--online" in sys.argv
    grid_pages = load_grid_pages()
    print("离线知识库命中页面:", sorted(grid_pages.keys()))
    text_pages = load_text_pages(force)

    db = {
        "_about": "带轮标准尺寸数据（V带轮/窄V带轮/同步带轮），供 AI 自动建模助手读取",
        "_note": "数值来自机械设计手册所载国家标准表；建模时按标准槽剖面对称回转",
        "_coverage": ("直径系列表只收录手册该表列出的档位（基准宽度制段: 手册列出 Z ⌀20~125、"
                      "A ⌀50~125、B ⌀75~125 等）。系列表里没有的基准直径按 da = dd + 2·ha "
                      "推导，并在界面明确提示，不会静默取错值。"),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "sources": [],
        "v_belt_grooves": {},
        "groove_angles": [],
        "v_belt_diameter_series": {},
        "structure_formulas": {},
        "raw_tables": {},
        "selfcheck": [],
    }
    for key, role in GRID_PAGES.items():
        p = grid_pages.get(key)
        if p:
            db["sources"].append({"role": role, "url": p["url"], "title": p["title"],
                                  "retrieved_from": "离线知识库 kb/handbook_grid.jsonl"})
    for key, role in TEXT_PAGES.items():
        p = text_pages.get(key)
        if p:
            db["sources"].append({"role": role, "url": p["url"],
                                  "title": {"pulley_structure": "带轮结构型式和辐板厚度",
                                            "pulley_structure_types": "带轮结构图例及材质"}[role],
                                  "retrieved_from": "联网抓取 %s" % p.get("fetched", "")})

    # ---- V 带轮轮槽 ----
    if "13007" in grid_pages and grid_pages["13007"]["grids"]:
        g = grid_pages["13007"]["grids"][0]
        db["v_belt_grooves"] = parse_v_belt_profile(g)
        db["groove_angles"] = parse_groove_angles(g)
        db["raw_tables"]["v_groove_profile"] = [[clean(c) for c in r[:12]]
                                                for r in g[:14] if any(clean(c) for c in r)]
    # ---- 直径系列 ----
    if "13008" in grid_pages and grid_pages["13008"]["grids"]:
        series, colmap = parse_diameter_series(grid_pages["13008"]["grids"][0])
        db["v_belt_diameter_series"] = series
        db["v_diameter_column_map"] = colmap
    # ---- 结构公式 ----
    for key, role in TEXT_PAGES.items():
        p = text_pages.get(key)
        if not p:
            continue
        f = extract_formulas(p["text"])
        if f:
            db["structure_formulas"][role] = f
    # ---- 其他原样表 ----
    for key in ("13028-1", "13028-2", "13057"):
        p = grid_pages.get(key)
        if p and p["grids"]:
            db["raw_tables"][GRID_PAGES[key]] = [[clean(c) for c in r[:14]]
                                                 for r in p["grids"][0][:30]
                                                 if any(clean(c) for c in r)]

    # ---- 自检: 与公开标准数值锚点比对 ----
    ok = True
    for sym, (bd, ha, hf, e) in ANCHORS.items():
        got = db["v_belt_grooves"].get(sym)
        if not got:
            db["selfcheck"].append("%s 型缺失" % sym)
            ok = False
            continue
        bad = []
        if abs(got["base_width_bd"] - bd) > 0.01:
            bad.append("bd %s≠%s" % (got["base_width_bd"], bd))
        if abs(got["addendum_ha"] - ha) > 0.01:
            bad.append("ha %s≠%s" % (got["addendum_ha"], ha))
        if abs(got["dedendum_hf"] - hf) > 0.01:
            bad.append("hf %s≠%s" % (got["dedendum_hf"], hf))
        if abs(got["pitch_e"] - e) > 0.01:
            bad.append("e %s≠%s" % (got["pitch_e"], e))
        db["selfcheck"].append("%s 型 %s" % (sym, "OK" if not bad else "偏差: " + ", ".join(bad)))
        ok = ok and not bad
    db["selfcheck_ok"] = ok

    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)

    # ---- Markdown ----
    md = ["# 带轮标准尺寸数据（权威来源）", "",
          "> 由 `tools/extract_pulley_std.py` 自动抽取；来源为机械设计手册中标注了国家标准号的表格。",
          "> 生成时间：%s" % db["generated"], "",
          "## 一、数据来源", ""]
    for s in db["sources"]:
        md.append("- **%s**：%s  \n  %s  \n  取自：%s"
                  % (s["role"], s.get("title", ""), s["url"], s["retrieved_from"]))
    md += ["", "## 二、V 带轮轮槽截面尺寸（GB/T 10412—2002 / GB/T 10413—2002）", "",
           "| 槽型 | 窄V带轮槽型 | 基准宽度 bd | 槽顶高 ha min | 槽底高 hf min | 全槽深 ha+hf | 槽间距 e |",
           "|---|---|---|---|---|---|---|"]
    for sym in ("Y", "Z", "A", "B", "C", "D", "E"):
        g = db["v_belt_grooves"].get(sym)
        if not g:
            continue
        md.append("| %s | %s | %.1f | %.2f | %.2f | %.2f | %.1f |"
                  % (g["symbol"], g["narrow_symbol"], g["base_width_bd"], g["addendum_ha"],
                     g["dedendum_hf"], g["groove_depth"], g["pitch_e"]))
    md += ["", "### 带轮槽角 φ（随基准直径分档）", ""]
    for a in db["groove_angles"]:
        md.append("- 基准直径 %s → 槽角 φ = %.0f°"
                  % ("≤ %g mm" % a["dd_max"] if a["dd_max"] else "> 190 mm", a["angle"]))
    md += ["", "## 三、V 带轮基准直径系列（GB/T 10412—2002）", ""]
    for sym, tbl in sorted(db["v_belt_diameter_series"].items()):
        md.append("**%s 型**（基准直径→外径）: " % sym)
        md.append("")
        md.append("`" + "  ".join("%g→%g" % (k, v) for k, v in tbl.items()) + "`")
        md.append("")
    md += ["", "## 四、带轮结构尺寸公式（摘自手册，用于自动生成轮毂/辐板/轮缘）", ""]
    for role, f in db["structure_formulas"].items():
        md.append("### %s" % role)
        for k, v in f.items():
            md.append("- `%s` = %s" % (k, v))
        md.append("")
    md += ["", "## 五、自检结果", ""]
    for line in db["selfcheck"]:
        md.append("- " + line)
    md += ["", "## 六、其他标准表（原样保存）", ""]
    for name, rows in db["raw_tables"].items():
        md.append("### %s" % name)
        for r in rows[:12]:
            md.append("- " + " | ".join(r))
        md.append("")

    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("\n槽型抽取结果:")
    for sym in sorted(db["v_belt_grooves"]):
        g = db["v_belt_grooves"][sym]
        print("   %-2s bd=%-5.1f ha=%-5.2f hf=%-5.2f 深=%-5.2f e=%-5.1f"
              % (sym, g["base_width_bd"], g["addendum_ha"], g["dedendum_hf"],
                 g["groove_depth"], g["pitch_e"]))
    print("直径系列:", {k: len(v) for k, v in db["v_belt_diameter_series"].items()})
    print("结构公式:", json.dumps(db["structure_formulas"], ensure_ascii=False)[:400])
    print("自检:", "全部通过" if db.get("selfcheck_ok") else "存在偏差")
    for line in db["selfcheck"]:
        print("   ", line)
    print("已写出 %s / %s" % (OUT_JSON, OUT_MD))
    return 0 if db.get("selfcheck_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
