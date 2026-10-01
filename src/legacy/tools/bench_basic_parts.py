# -*- coding: utf-8 -*-
r"""
按真实零件库建"基础零件题库"，跑命中率，不达标就继续学。

## 命中率口径（两层分开量，避免上一版的量具错误）

上一版我拿 `nlparser` 输出的**通用特征参数**（cylinder 的 radius/height）
去核对 `partlib` 的参数键（d/D/B）——**口径不同，必然全错**，
于是得到 17.6% 这种假数字（而同一批零件在专门测试里是 10/10 全对的）。

正确做法：
  · **类别**：用 `partlib.find_part(文本)`（这才是"认成了哪个零件"的判据）；
  · **参数**：用 `partlib.model_params(文本)['params']`（partlib 自己的键名口径）；
  · 另外单独校验**几何能建出来**（`nlparser.parse().doc.build()` 非空）。

三样都对才算命中。用法：python tools\bench_basic_parts.py [--target 80]
输出：reports\基础零件命中率.md、kb\basic_parts_cases.json
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP      # noqa: E402
from ai_modeler import partlib as PL        # noqa: E402

# ---- 用码点构造中文，避免命令行转换出别字（本轮踩过） ----
L = "\u957f\u5ea6"          # 长度
CHANG = "\u957f"            # 长
W = "\u5bbd\u5ea6"          # 宽度
T = "\u539a\u5ea6"          # 厚度
H = "\u9ad8\u5ea6"          # 高度
JIE = "\u622a\u9762"        # 截面
BI = "\u58c1\u539a"         # 壁厚
ZHI = "\u76f4\u5f84"        # 直径
NEI = "\u5185\u5f84"        # 内径
WAI = "\u5916\u5f84"        # 外径
TJ = "\u53f0\u9636\u76f4\u5f84"   # 台阶直径
TG = "\u53f0\u9ad8"         # 台高
CB = "\u957f\u8fb9"         # 长边
DB = "\u77ed\u8fb9"         # 短边
BH = "\u677f\u539a"         # 板厚

EGT = "EGT\u578b\u6750"
XING = "\u578b\u6750"
DP = "\u57ab\u7247\u57ab\u5757"
DK = "\u57ab\u5757"
DW = "\u5b9a\u4f4d\u9500"
NJ = "\u5185\u516d\u89d2\u87ba\u9489"
LJ = "\u8fde\u63a5\u5757"
JB = "\u57fa\u677f"
ZT = "\u652f\u817f"
ZW = "\u6298\u5f2f\u4ef6"
LJJ = "\u8fde\u63a5\u4ef6"
GB = "\u6df1\u6c9f\u7403\u8f74\u627f"
CL = "\u76f4\u9f7f\u5706\u67f1\u9f7f\u8f6e"
BC = "\u591a\u69fd\u5e26\u8f6e"
TB = "\u540c\u6b65\u5e26\u8f6e"
FL = "\u6cd5\u5170"
DQ = "\u57ab\u5708"
ZOU = "\u8f74\u5957"
PJ = "\u5e73\u952e"
LX = "\u87ba\u65cb\u5f39\u7c27"

#: (提示词, 期望类别, {partlib 参数键: 值})
CASES = [
    # ---------- EGT 型材 ----------
    ("%s %s80 %s500" % (EGT, JIE, L), "EGT型材", {"sec_w": 80.0, "L": 500.0}),
    ("%s %s50 %s1000" % (EGT, JIE, L), "EGT型材", {"sec_w": 50.0, "L": 1000.0}),
    ("%s %s80 %s4078" % (EGT, JIE, L), "EGT型材", {"sec_w": 80.0, "L": 4078.0}),
    ("%s %s80 %s1200" % (XING, JIE, L), "EGT型材", {"sec_w": 80.0, "L": 1200.0}),
    ("%s %s80 %s800 %s4" % (EGT, JIE, L, BI), "EGT型材", {"sec_w": 80.0, "L": 800.0}),
    # ---------- 垫片垫块（真实规格）----------
    ("%s %s50 %s20 %s5" % (DP, L, W, T), "垫片垫块", {"L": 50.0, "W": 20.0, "t": 5.0}),
    ("%s %s50 %s50 %s5" % (DP, L, W, T), "垫片垫块", {"L": 50.0, "W": 50.0, "t": 5.0}),
    ("%s %s65 %s20 %s5" % (DP, L, W, T), "垫片垫块", {"L": 65.0, "W": 20.0, "t": 5.0}),
    ("%s %s50 %s20 %s3" % (DK, L, W, T), "垫片垫块", {"t": 3.0}),
    # ---------- 定位销 ----------
    ("%s %s12 %s40" % (DW, ZHI, L), "定位销", {"d": 12.0, "L": 40.0}),
    ("%s %s16 %s50" % (DW, ZHI, L), "定位销", {"d": 16.0, "L": 50.0}),
    ("%s %s10 %s30 %s20 %s6" % (DW, ZHI, L, TJ, TG), "定位销", {"d": 10.0, "L": 30.0}),
    # ---------- 内六角螺钉 ----------
    ("%s M5x16" % NJ, "内六角螺钉", {"d": 5.0, "L": 16.0}),
    ("%s M6x20" % NJ, "内六角螺钉", {"d": 6.0, "L": 20.0}),
    ("%s M8x25" % NJ, "内六角螺钉", {"d": 8.0, "L": 25.0}),
    ("%s M10x30" % NJ, "内六角螺钉", {"d": 10.0, "L": 30.0}),
    ("%s M12x50" % NJ, "内六角螺钉", {"d": 12.0, "L": 50.0}),
    ("%s M10x120" % NJ, "内六角螺钉", {"d": 10.0, "L": 120.0}),
    # ---------- 夹具件 ----------
    ("%s %s120 %s80 %s30" % (LJ, L, W, H), "连接块", {"L": 120.0, "W": 80.0}),
    ("%s %s500 %s300 %s20" % (JB, L, W, T), "基板", {"L": 500.0, "W": 300.0}),
    ("%s %s80 %s200" % (ZT, ZHI, H), "支腿", {"D": 80.0, "H": 200.0}),
    ("%s %s100 %s70 %s8 %s180" % (ZW, CB, DB, BH, H), "折弯件", {"L1": 100.0}),
    ("%s %s150 %s50 %s12" % (LJJ, L, W, T), "连接件", {"L": 150.0, "W": 50.0}),
    # ---------- 已有类别（回归基线）----------
    ("%s 6204" % GB, "深沟球轴承", {"d": 20.0, "D": 47.0, "B": 14.0}),
    ("%s %s20 %s47 %s14" % (GB, NEI, WAI, W), "深沟球轴承", {"d": 20.0, "D": 47.0}),
    ("%s \u6a21\u65703 \u9f7f\u657024 \u9f7f\u5bbd20" % CL, "直齿圆柱齿轮",
     {"m": 3.0, "z": 24.0, "b": 20.0}),
    ("%s %s25 %s100 %s30 \u69fd\u65703 A\u578b" % (BC, NEI, WAI, W), "多槽带轮",
     {"D": 100.0, "n": 3.0, "t": "A"}),
    ("%s \u8282\u5706\u76f4\u5f8450 %s12 \u9f7f\u657010 \u5bbd10" % (TB, NEI), "同步带轮",
     {"D": 50.0, "z": 10.0}),
    ("%s %s160 %s60 \u539a20" % (FL, WAI, NEI), "法兰", {"D": 160.0, "d": 60.0}),
    ("%s %s20 %s40 %s3" % (DQ, NEI, WAI, T), "垫圈", {"d": 20.0, "D": 40.0}),
    ("%s %s30 %s45 %s60" % (ZOU, NEI, WAI, L), "轴套", {"d": 30.0, "D": 45.0}),
    ("%s %s10 %s8 %s40" % (PJ, W, H, L), "平键", {"b": 10.0, "h": 8.0}),
    ("%s \u7ebf\u5f843.2 %s38 \u81ea\u7531\u9ad8\u5ea6150 \u5708\u65704.75" % (LX, WAI),
     "螺旋弹簧", {"d": 3.2, "D": 38.0}),
]


def probe(text):
    """两层分开量：(类别, 参数, 几何面数)。"""
    cat = PL.find_part(text)
    params = {}
    r = PL.model_params(text)
    if r:
        params = {k: v for k, v in (r.get("params") or {}).items()
                  if not k.startswith("__")}
    nfaces = 0
    try:
        doc = NLP.parse(text).doc
        body = doc.build()
        nfaces = len(body) if body else 0
    except Exception:
        nfaces = -1
    return cat, params, nfaces


def main():
    target = 80.0
    if "--target" in sys.argv:
        try:
            target = float(sys.argv[sys.argv.index("--target") + 1])
        except Exception:
            pass

    print("=" * 92)
    print("基础零件命中率（%d 条；类别 + 关键参数 + 能建几何 三者都对才命中）"
          % len(CASES))
    print("=" * 92)
    n_cat = n_par = n_geo = n_hit = 0
    rows = []
    for text, wcat, wpar in CASES:
        gcat, gpar, nf = probe(text)
        ok_c = (gcat == wcat)
        ok_p = all(abs(float(gpar.get(k, -1e9)) - float(v)) < 1e-6
                   if isinstance(v, (int, float)) else gpar.get(k) == v
                   for k, v in wpar.items())
        ok_g = nf > 0
        n_cat += ok_c
        n_par += ok_p
        n_geo += ok_g
        hit = ok_c and ok_p and ok_g
        n_hit += hit
        miss = []
        if not ok_c:
            miss.append("类别 %s≠%s" % (gcat, wcat))
        if not ok_p:
            miss.append("参数 实得%s 期望%s"
                        % ({k: gpar.get(k) for k in wpar}, wpar))
        if not ok_g:
            miss.append("未建出几何(面数%s)" % nf)
        rows.append({"text": text, "want_cat": wcat, "got_cat": gcat,
                     "want": wpar, "hit": hit, "miss": " ".join(miss)})
        print("%s %-46s %s" % ("✅" if hit else "❌", text[:46],
                               " ".join(miss)))

    rate = 100.0 * n_hit / len(CASES)
    print("\n命中率 %.1f%%（%d/%d）   目标 %.0f%%" % (rate, n_hit, len(CASES), target))
    print("分项：类别 %d/%d、参数 %d/%d、几何 %d/%d"
          % (n_cat, len(CASES), n_par, len(CASES), n_geo, len(CASES)))
    print("结论：%s" % ("达标 ✅" if rate >= target else "未达标 ❌ 需继续学习"))

    os.makedirs(os.path.join(ROOT, "kb"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with io.open(os.path.join(ROOT, "kb", "basic_parts_cases.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"total": len(CASES), "hit": n_hit, "rate": round(rate, 2),
                   "target": target, "cat_ok": n_cat, "par_ok": n_par,
                   "geo_ok": n_geo, "rows": rows}, f, ensure_ascii=False, indent=1)
    md = ["# 基础零件命中率", "",
          "口径：**类别正确 + partlib 参数正确 + 能建出几何**，三者都对才算命中。", "",
          "| 命中率 | 命中/总数 | 类别 | 参数 | 几何 | 目标 | 结论 |",
          "|---|---|---|---|---|---|---|",
          "| **%.1f%%** | %d/%d | %d/%d | %d/%d | %d/%d | %.0f%% | %s |"
          % (rate, n_hit, len(CASES), n_cat, len(CASES), n_par, len(CASES),
             n_geo, len(CASES), target,
             "达标 ✅" if rate >= target else "未达标 ❌"), "",
          "| 提示词 | 期望类别 | 实得 | 结果 | 说明 |", "|---|---|---|---|---|"]
    for r in rows:
        md.append("| `%s` | %s | %s | %s | %s |"
                  % (r["text"], r["want_cat"], r["got_cat"],
                     "✅" if r["hit"] else "❌", r["miss"]))
    with io.open(os.path.join(ROOT, "reports", "基础零件命中率.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 reports/基础零件命中率.md")
    return 0 if rate >= target else 1


if __name__ == "__main__":
    sys.exit(main())
