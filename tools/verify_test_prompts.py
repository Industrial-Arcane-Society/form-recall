# -*- coding: utf-8 -*-
r"""
逐条核对 kb/test_prompts.txt 里的关键尺寸是否**真的算对**。

"能解析出特征"不等于"数对了"，所以这里对每条给出**具体期望值**并核对。
核对口径：在解析出的特征参数里，找与期望同名的键；找不到就判失败（不放过）。

用法：python tools\verify_test_prompts.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP      # noqa: E402

# (提示词, {期望键: 期望值})  —— 键名用**特征参数的真实键名**（radius/height/pitch_d…）
CHECKS = [
    # 标准件
    ("\u6df1\u6c9f\u7403\u8f74\u627f \u5185\u5f8420 \u5916\u5f8447 \u5bbd\u5ea614",
     {"radius": 23.5, "height": 14.0}),
    ("\u6df1\u6c9f\u7403\u8f74\u627f 6204", {"radius": 23.5, "height": 14.0}),
    ("\u8f74\u627f 6305", {"radius": 31.0, "height": 17.0}),
    ("\u6df1\u6c9f\u7403\u8f74\u627f \u4ee3\u53f76206", {"radius": 31.0, "height": 16.0}),
    ("\u87ba\u6813 \u87ba\u7eb9\u89c4\u683c16 \u957f\u5ea680",
     {"shank_d": 16.0, "length": 80.0, "across_flats": 24.0, "head_h": 11.2}),
    ("\u87ba\u6813\u87ba\u7eb9\u89c4\u683c16\u957f\u5ea680",
     {"shank_d": 16.0, "length": 80.0}),
    ("\u57ab\u5708 \u5185\u5f8420 \u5916\u5f8440 \u539a\u5ea63",
     {"radius": 20.0, "height": 3.0}),
    ("\u5e73\u952e \u5bbd\u5ea610 \u9ad8\u5ea68 \u957f\u5ea640",
     # ⚠️ 平键用**局部轴**语义：X=宽10、Y=高8、Z=长40（键躺在平面上）。
     #    这不是通用 box 的"全局 X/Y/Z"语义，两种约定并存是既有设计，
     #    被 make_complex_testlist.py 与 bench_v2.py 两处期望钉住。
     {"length": 10.0, "width": 8.0, "height": 40.0}),
    ("\u8f74\u5957 \u5185\u5f8430 \u5916\u5f8445 \u957f\u5ea660",
     {"radius": 22.5, "height": 60.0}),
    ("\u6cd5\u5170 \u5916\u5f84160 \u5185\u5f8460 \u539a20",
     {"radius": 80.0, "height": 20.0}),
    ("\u8f74 \u76f4\u5f8430 \u957f\u5ea6120", {"radius": 15.0, "height": 120.0}),

    # 传动件
    ("\u76f4\u9f7f\u5706\u67f1\u9f7f\u8f6e \u6a21\u65703 \u9f7f\u657024 \u9f7f\u5bbd20",
     {"teeth": 24, "module": 3.0, "height": 20.0}),
    ("\u591a\u69fd\u5e26\u8f6e \u5185\u5f8425 \u5916\u5f84100 \u5bbd\u5ea630 \u69fd\u65703 A\u578b",
     {"pitch_d": 100.0, "grooves": 3, "groove_type": "A"}),
    ("\u5e26\u8f6e d25 D100 B30 n3 A\u578b",
     {"pitch_d": 100.0, "grooves": 3, "groove_type": "A"}),
    ("\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8450 \u5185\u5f8412 \u9f7f\u657010 \u5bbd10",
     {"pitch_d": 50.0, "teeth": 10, "tooth_type": "XL"}),
    ("\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8480 \u9f7f\u657020 XL\u578b",
     {"pitch_d": 80.0, "teeth": 20, "tooth_type": "XL"}),
    ("\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8450 \u9f7f\u657020 T5\u578b",
     {"pitch_d": 50.0, "teeth": 20, "tooth_type": "T5"}),
    ("\u540c\u6b65\u5e26\u8f6eXL\u578b\u8282\u5706\u76f4\u5f8450\u5185\u5f8412\u9f7f\u657010\u5bbd10",
     {"pitch_d": 50.0, "teeth": 10, "tooth_type": "XL"}),
    ("\u591a\u69fd\u5e26\u8f6e\u5185\u5f8425\u5916\u5f84100\u5bbd\u5ea630\u69fd\u65703A\u578b",
     {"pitch_d": 100.0, "grooves": 3, "groove_type": "A"}),

    # 弹簧
    ("\u87ba\u65cb\u5f39\u7c27 \u7ebf\u5f843.2 \u5916\u5f8438 \u81ea\u7531\u9ad8\u5ea6150 \u5708\u65704.75",
     {"turns": 4.75}),
    ("\u87ba\u65cb\u5f39\u7c27\u7ebf\u5f843.2\u5916\u5f8438\u81ea\u7531\u9ad8\u5ea6150\u5708\u65704.75",
     {"turns": 4.75}),
    ("\u538b\u7f29\u5f39\u7c27 \u7ebf\u5f842 \u5916\u5f8420 \u9ad8\u5ea660 \u5708\u65708",
     {"turns": 8.0}),

    # 个数量词
    ("\u591a\u69fd\u5e26\u8f6e \u5185\u5f8425 \u5916\u5f84100 \u5bbd\u5ea640 4\u4e2a\u69fd",
     {"grooves": 4}),

    # 通用形状
    ("\u957f\u65b9\u4f53\u7684\u957f120 \u5bbd80 \u9ad815",
     {"length": 120.0, "width": 80.0, "height": 15.0}),
    ("\u753b\u4e00\u4e2a 100\u00d760\u00d720 \u7684\u957f\u65b9\u4f53",
     {"length": 100.0, "width": 60.0, "height": 20.0}),
    ("\u5706\u67f1 \u76f4\u5f8460 \u9ad8120", {"radius": 30.0, "height": 120.0}),
]


def collect(text):
    """把解析出的所有特征参数合并成一个字典（后出现的覆盖前面的）。"""
    out = {}
    try:
        r = NLP.parse(text)
    except Exception:
        return out
    for f in r.doc.features:
        for k, v in (f.params or {}).items():
            if k.startswith("__"):
                continue
            out.setdefault(k, v)
    return out


def main():
    print("逐条核对关键尺寸（%d 条）\n" % len(CHECKS))
    bad = []
    for text, exp in CHECKS:
        got = collect(text)
        miss = {}
        for k, v in exp.items():
            g = got.get(k)
            ok = False
            if isinstance(v, float) and isinstance(g, (int, float)):
                ok = abs(float(g) - v) < 1e-6
            else:
                ok = (g == v)
            if not ok:
                miss[k] = (g, v)
        tag = "OK " if not miss else "BAD"
        print("%s  %-46s" % (tag, text[:46]))
        if miss:
            for k, (g, v) in miss.items():
                print("        %s: 实得 %r 期望 %r" % (k, g, v))
            bad.append(text)
    print("\n通过 %d/%d，失败 %d 条" % (len(CHECKS) - len(bad), len(CHECKS), len(bad)))
    for t in bad:
        print("   - %s" % t)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
