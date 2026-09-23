# -*- coding: utf-8 -*-
r"""测试新增的 5 类焊装夹具件。

⚠️ 测试字符串**用 \uXXXX 构造**，不直接写中文 —— 早先在命令行里手打中文时
   出现过「宽度」被打成「宽庨」这类别字，导致误判成插件的问题（实测踩过）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP      # noqa: E402
from ai_modeler import kernel as K          # noqa: E402

# 常用词（码点构造）
CHANGSHOU = "\u957f\u5ea6"      # 长度
KUANDU = "\u5bbd\u5ea6"         # 宽度
HOU = "\u539a\u5ea6"            # 厚度
GAODU = "\u9ad8\u5ea6"          # 高度
KONGJING = "\u5b54\u5f84"       # 孔径
KONGSHU = "\u5b54\u6570"        # 孔数
NEISUO = "\u5185\u7f29"         # 内缩
ZHENG = "\u76f4\u5f84"          # 直径
BIANHOU = "\u677f\u539a"        # 板厚
CHANGBIAN = "\u957f\u8fb9"      # 长边
DUANBIAN = "\u77ed\u8fb9"       # 短边
LJ = "\u8fde\u63a5\u5757"       # 连接块
JB = "\u57fa\u677f"             # 基板
PHBT = "\u914d\u710a\u677f\u6761"  # 配焊板条
ZT = "\u652f\u817f"             # 支腿
ZWJ = "\u6298\u5f2f\u4ef6"      # 折弯件
LJJ = "\u8fde\u63a5\u4ef6"      # 连接件
DB = "\u5e95\u677f"             # 底板

# (提示词, 期望包围盒 (x,y,z) 或 None 表示只要求能建出)
CASES = [
    ("%s %s80 %s60 %s20" % (LJ, CHANGSHOU, KUANDU, GAODU), (80, 60, 20)),
    ("%s %s100 %s80 %s25 %s12" % (LJ, CHANGSHOU, KUANDU, GAODU, KONGJING),
     (100, 80, 25)),
    ("%s %s300 %s200 %s16" % (JB, CHANGSHOU, KUANDU, HOU), (300, 200, 16)),
    ("%s %s400 %s300 %s20 %s14 %s4 %s25"
     % (JB, CHANGSHOU, KUANDU, HOU, KONGJING, KONGSHU, NEISUO), (400, 300, 20)),
    ("%s %s200 %s50 %s10" % (PHBT, CHANGSHOU, KUANDU, HOU), (200, 50, 10)),
    ("%s %s60 %s200" % (ZT, ZHENG, GAODU), (60, 60, 200)),
    ("%s %s80 %s150 %s20" % (ZT, ZHENG, GAODU, KONGJING), (80, 80, 150)),
    ("%s %s80 %s60 %s6 %s150"
     % (ZWJ, CHANGBIAN, DUANBIAN, BIANHOU, GAODU), (80, 60, 150)),
    ("%s %s120 %s40 %s10" % (LJJ, CHANGSHOU, KUANDU, HOU), (120, 40, 10)),
    ("%s %s250 %s150 %s12" % (DB, CHANGSHOU, KUANDU, HOU), (250, 150, 12)),
]


def main():
    print("=" * 88)
    print("焊装夹具件测试（%d 条）" % len(CASES))
    print("=" * 88)
    n_ok = n_dim = 0
    for t, want in CASES:
        r = NLP.parse(t)
        if not r.doc.features:
            print("❌ 无特征      %-46s" % t)
            continue
        kinds = [f.kind for f in r.doc.features]
        try:
            body = r.doc.build()
            bb = K.bbox(body) if body else None
        except Exception as e:
            print("❌ 建几何失败   %-46s %s" % (t, str(e)[:36]))
            continue
        if not bb:
            print("❌ 空网格      %-46s" % t)
            continue
        n_ok += 1
        got = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
        ok = all(abs(g - w) < 0.6 for g, w in zip(got, want))
        n_dim += 1 if ok else 0
        print("%s %-46s %-14s %6.1f×%-6.1f×%-6.1f  期望 %g×%g×%g"
              % ("✅" if ok else "⚠️", t, "+".join(kinds), got[0], got[1], got[2],
                 want[0], want[1], want[2]))
    print("\n能建出几何 %d/%d，尺寸与期望一致 %d/%d"
          % (n_ok, len(CASES), n_dim, len(CASES)))
    return 0 if n_ok == len(CASES) and n_dim == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
