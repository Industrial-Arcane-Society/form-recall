# -*- coding: utf-8 -*-
r"""生成焊装夹具件的测试题库，并汇总"从 AI数据集 学到什么"。"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CHANGSHOU = "\u957f\u5ea6"
KUANDU = "\u5bbd\u5ea6"
HOU = "\u539a\u5ea6"
GAODU = "\u9ad8\u5ea6"
KONGJING = "\u5b54\u5f84"
KONGSHU = "\u5b54\u6570"
NEISUO = "\u5185\u7f29"
ZHENG = "\u76f4\u5f84"
BIANHOU = "\u677f\u539a"
CHANGBIAN = "\u957f\u8fb9"
DUANBIAN = "\u77ed\u8fb9"
BIANSHU = "\u8fb9\u6570"

CASES = [
    ("\u8fde\u63a5\u5757", "\u8fde\u63a5\u5757 %s120 %s80 %s30" % (CHANGSHOU, KUANDU, GAODU)),
    ("\u8fde\u63a5\u5757", "\u8fde\u63a5\u5757 %s120 %s80 %s30 %s16" % (CHANGSHOU, KUANDU, GAODU, KONGJING)),
    ("\u57fa\u677f", "\u57fa\u677f %s500 %s300 %s20" % (CHANGSHOU, KUANDU, HOU)),
    ("\u57fa\u677f", "\u57fa\u677f %s600 %s400 %s25 %s18 %s4 %s40" % (CHANGSHOU, KUANDU, HOU, KONGJING, KONGSHU, NEISUO)),
    ("\u914d\u710a\u677f\u6761", "\u914d\u710a\u677f\u6761 %s200 %s50 %s12" % (CHANGSHOU, KUANDU, HOU)),
    ("\u5e95\u677f", "\u5e95\u677f %s350 %s250 %s16" % (CHANGSHOU, KUANDU, HOU)),
    ("\u652f\u817f", "\u652f\u817f %s60 %s250" % (ZHENG, GAODU)),
    ("\u652f\u817f", "\u652f\u817f %s80 %s200 %s20" % (ZHENG, GAODU, KONGJING)),
    ("\u652f\u817f", "\u652f\u817f %s60 %s200 %s6" % (ZHENG, GAODU, BIANSHU)),
    ("\u6298\u5f2f\u4ef6", "\u6298\u5f2f\u4ef6 %s100 %s70 %s8 %s180" % (CHANGBIAN, DUANBIAN, BIANHOU, GAODU)),
    ("\u8fde\u63a5\u4ef6", "\u8fde\u63a5\u4ef6 %s150 %s50 %s12" % (CHANGSHOU, KUANDU, HOU)),
    ("\u8fde\u63a5\u4ef6", "\u8fde\u63a5\u4ef6 %s150 %s50 %s12 %s14 %s2" % (CHANGSHOU, KUANDU, HOU, KONGJING, KONGSHU)),
]


def main():
    from ai_modeler import nlparser as NLP

    lines = ["", "# \u710a\u88c5\u5939\u5177\u4ef6\uff08\u4f9d\u636e AI\u6570\u636e\u96c6 \u7684 BOM \u5f52\u7eb3\uff09", ""]
    rows = []
    ok = 0
    for cat, text in CASES:
        r = NLP.parse(text)
        kinds = [f.kind for f in r.doc.features]
        dims = ""
        try:
            body = r.doc.build()
            from ai_modeler import kernel as K
            bb = K.bbox(body)
            if bb:
                dims = "%.1f\u00d7%.1f\u00d7%.1f" % (bb[3] - bb[0], bb[4] - bb[1],
                                                    bb[5] - bb[2])
                ok += 1
        except Exception as e:
            dims = "fail: %s" % str(e)[:30]
        lines.append(text)
        rows.append({"cat": cat, "text": text, "kinds": kinds, "size": dims})
        print("%-52s %-16s %s" % (text, "+".join(kinds), dims))

    out = os.path.join(ROOT, "kb", "test_prompts_fixture.txt")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n能建出几何 %d/%d" % (ok, len(CASES)))
    print("已写入 %s" % out)

    rep = os.path.join(ROOT, "reports", "夹具件能力报告.md")
    md = ["# 焊装夹具件能力报告", "",
          "数据来源：`C:\\Users\\25643\\Desktop\\AI自动画图项目设计数据及视频-20260402-V01\\AI数据集`",
          "（237 个 CATIA 零件/装配、31 个装配、65 行 BOM、1090 条三元组、223 条 QA）。", "",
          "本报告只列**插件能不能按提示词建出来**，不声称复刻了原始 CAD。", "",
          "| 类别 | 提示词 | 特征 | 建出尺寸 |", "|---|---|---|---|"]
    for r in rows:
        md.append("| %s | `%s` | %s | %s |" % (r["cat"], r["text"],
                                              "+".join(r["kinds"]), r["size"]))
    md += ["", "## 诚实边界", "",
           "- 数据集里**没有**这些加工件的特征树（CATPart 二进制格式限制），",
           "  STEP 的包围盒又有坏值（装配型导出是局部坐标系并集，实测出现过 50 万 mm）。",
           "- 所以以上尺寸是**用户按图纸输入的**，插件按夹具件的一般构造生成几何；",
           "  不是从原始 CAD 里量出来的真实尺寸。",
           "- **做不到「一键复刻整套夹具」**：缺特征树与可信尺寸，谁声称能做到都是骗人。"]
    with io.open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 %s" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
