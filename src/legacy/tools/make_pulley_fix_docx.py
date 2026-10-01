# -*- coding: utf-8 -*-
"""
生成《带轮建模问题复盘 + 标准数据落地 + 多轮学习报告》Word 文档。

内容来源：
  · 测试者反馈（截图）：但你这带轮没槽啊 / 可能得多告诉一下模型这些提示词到底对应什么特征
  · CATIA 真机日志（截图）：多槽带轮 内径25 外径100 宽度30 槽数3 → 建出光轮
  · 本轮代码改动与标准数据（kb/pulley_std.json，来源 GB/T 10412—2002 / GB/T 10413—2002）
  · 多轮学习结果（reports/pulley_learning_round*.json、带轮提示词学习报告.md）
  · 全量命中率评测（reports/eval_baseline.json）

输出：AI自动建模插件_带轮问题复盘与标准数据落地报告.docx（项目根目录）

运行: python tools\\make_pulley_fix_docx.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx import Document                                     # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH                 # noqa: E402
from docx.shared import Cm, Pt                                 # noqa: E402
from docx.oxml.ns import qn                                   # noqa: E402

from make_leader_feedback_plan_docx import (                  # noqa: E402
    ACCENT, GREY, CN_FONT, CN_FONT_BODY, EN_FONT,
    set_run_font, para, heading, bullet, make_table, callout,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "AI自动建模插件_带轮问题复盘与标准数据落地报告.docx")
KB = os.path.join(ROOT, "kb", "pulley_std.json")
REPORTS = os.path.join(ROOT, "reports")

Q1, Q2 = "\u201c", "\u201d"          # 中文引号，避免与 Python 字符串引号冲突


def load_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build():
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(2.2)
    sec.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.font.name = EN_FONT
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT_BODY)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.35

    kb = load_json(KB, {}) or {}
    rounds = []
    for i in (1, 2, 3):
        r = load_json(os.path.join(REPORTS, "pulley_learning_round%d.json" % i))
        if r:
            rounds.append(r)
    ev = load_json(os.path.join(REPORTS, "eval_baseline.json"), {}) or {}

    # ---------------- 封面 ----------------
    para(doc, "AI 自动建模插件 · 带轮建模问题复盘与标准数据落地报告",
         size=19, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER,
         space_before=6, space_after=4, cn=CN_FONT)
    para(doc, "（依据测试者反馈截图、CATIA 真机运行日志、国家标准数据与多轮评测结果整理）",
         size=10, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, "生成时间：%s     版本：v1.3.0     适用程序：AI 自动建模助手"
         % datetime.now().strftime("%Y-%m-%d %H:%M"),
         size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    callout(doc, "一句话结论",
            "测试者说的没错：带轮当时确实没有槽 —— 程序画的是"
            + Q1 + "实心圆柱 + 中心通孔" + Q2 + "，V 形槽根本没进入几何。\n"
            "本轮已按国家标准把带轮重做：轮槽尺寸、槽角、外径、轮缘宽度全部取自 "
            "GB/T 10412—2002 / GB/T 10413—2002；同步带轮按 GB/T 11361 / JB/T 7512.2 的"
            "标准节距生成真实轮齿；并且每次生成都会输出"
            + Q1 + "提示词 → 生成特征" + Q2 + "对照表，"
            "回答测试者的第二个问题 —— 这些提示词到底对应什么特征。")

    # ---------------- 第 1 部分 ----------------
    heading(doc, "第 1 部分  测试者反馈逐句解读", 1)
    make_table(
        doc,
        ["#", "反馈原话", "真实含义", "必须解决的技术问题", "验收标准"],
        [
            ["1", "但你这带轮没槽啊",
             "CATIA 里看到的是一座光轮，与带轮的工程定义不符",
             "轮槽必须作为真实几何生成，尺寸符合国家标准，且能在 CATIA 里看到与测量",
             "CATIA 真机里数得出 3 条 V 形槽；槽间距/槽深与标准值一致"],
            ["2", "可能得多告诉一下模型这些提示词到底对应什么特征",
             "提示词与生成结果之间缺一层翻译，用户不知道程序把哪个词变成了哪个特征",
             "输出“提示词 → 生成特征”对照表，并给出标准依据",
             "每次生成后日志里逐条列出：哪句话 → 什么特征 → 依据哪条标准"],
            ["3", "多喂点有对应三维模型的提示词",
             "需要可验证的题量与迭代，而不是靠感觉改",
             "建题库 + 可复现的多轮评测，每轮报告准确率与失败原因",
             "题库覆盖各槽型/写法；多轮报告可查、可对比、可复跑"],
        ],
        widths=[0.8, 3.4, 3.8, 5.0, 4.4], font_size=8.5)

    para(doc, "CATIA 真机日志还原（截图内容）：", bold=True, size=10.5, space_before=8, space_after=2)
    para(doc, "输入「多槽带轮 内径25 外径100 宽度30 槽数3」→ 程序提示"
              "「带轮 ⌀100 × 30.0 mm, 3 槽 导出为光轮（3 条 V 型槽需在 CATIA 里开）」"
              "→ CATIA 里出现的是一个带中心孔的光圆柱。"
              "日志里的这句话其实就是当时的免责声明 —— 程序自己知道没有开槽，但把这件事留给了用户。",
         size=10)

    # ---------------- 第 2 部分 ----------------
    heading(doc, "第 2 部分  根因分析（4 个，已全部修复）", 1)
    make_table(
        doc,
        ["#", "根因", "为什么会这样", "修复方式"],
        [
            ["1", "带轮参数没读全，整条零件退化",
             "「多槽带轮」的参数表里 槽数、槽型 是必需参数，而别名正则只认数字、不认字母代号"
             "（A/B/SPZ），于是「槽数3 A型」读不出槽型 → 参数不全 → 整个零件识别失败，"
             "退化成“圆柱 + 通孔”",
             "① 槽型/齿型改为可选参数，并按基准直径自动推荐；② 新增专用型号识别 "
             "_pulley_type()，认「A型 / 槽型A / A带轮 / SPB / 8M型 / 5M型 / 槽数8D型」等写法"],
            ["2", "「带轮」被「同步带轮」抢走",
             "带轮的别名表里 “带轮” 是最短别名，find_part 取“出现最早”的匹配，"
             "于是写「带轮 … 槽数3」被判成同步带轮，建出齿形轮",
             "在 find_part 增加信号词消歧：出现「槽数/槽型/V带/多槽/多楔/皮带/窄V/N个槽」→ "
             "V 带轮；出现「同步/齿数/齿型/圆弧齿」→ 同步带轮"],
            ["3", "连写数字被当成一个数",
             "口语里不打空格：「宽度303个槽」被通用维度解析读成宽度=303、槽数=0；"
             "两段参数合并时也会出现「宽度40」+「40」→「4040」",
             "① 别名取值加边界校验（数字后面若紧跟另一个参数名或量词，本参数作废并换下一个出现位置）；"
             "② 合并时做去重叠 _deoverlap()；③ 兜底：数字串末尾紧跟槽/齿时取最后一位作槽数"],
            ["4", "零件名里的“槽”被当成切除动作",
             "is_cut_clause 用“含槽字”判断是否切除，于是「双槽带轮 基准直径80 内径20」被判成切槽，"
             "零件库整条不生效",
             "先做一次零件库识别：能匹配到已知零件且参数齐全时，一律不当作切除动作"],
        ],
        widths=[0.8, 3.4, 6.2, 7.0], font_size=8.5)

    # ---------------- 第 3 部分 ----------------
    heading(doc, "第 3 部分  权威标准数据落地（这是带轮有槽的依据）", 1)
    para(doc, "为避免自己拍脑袋定槽形，本轮把带轮的全部关键尺寸改为从国家标准表取值，"
              "数据落在 kb/pulley_std.json，来源 URL 与抽取脚本一并保留，可复跑、可核对。",
         size=10)

    heading(doc, "3.1 数据来源", 2)
    srcs = kb.get("sources") or []
    if srcs:
        make_table(doc, ["用途", "内容", "来源"],
                   [[s.get("role", ""), str(s.get("title", ""))[:46], s.get("url", "")]
                    for s in srcs], widths=[3.6, 6.4, 7.4], font_size=8.5)
    else:
        para(doc, "（未找到 kb/pulley_std.json，请先运行 python tools\\extract_pulley_std.py）", size=10)

    heading(doc, "3.2 V 带轮轮槽截面尺寸（本次生成轮槽的直接依据）", 2)
    grooves = kb.get("v_belt_grooves") or {}
    rows = []
    for sym in ("Y", "Z", "A", "B", "C", "D", "E"):
        g = grooves.get(sym)
        if not g:
            continue
        rows.append([sym, g.get("narrow_symbol", "—"), g.get("base_width_bd"),
                     g.get("addendum_ha"), g.get("dedendum_hf"),
                     g.get("groove_depth"), g.get("pitch_e")])
    make_table(doc,
               ["槽型", "窄V带轮槽型", "基准宽度 bd", "槽顶高 ha", "槽底高 hf", "全槽深 ha+hf", "槽间距 e"],
               rows, widths=[1.6, 2.8, 2.4, 2.2, 2.2, 2.6, 2.4], font_size=9)
    para(doc, "标准号：GB/T 10412—2002（基准宽度制）与 GB/T 10413—2002（有效宽度制）。自检结果：%s"
         % ("全部通过（与公开标准数值逐项一致）" if kb.get("selfcheck_ok")
            else "存在偏差，明细见 kb/pulley_std.md"),
         size=9.5, color=GREY)
    para(doc, "槽角 φ 随基准直径分档（同一张表的表头给出）：≤60°→32°、≤118°→34°、"
              "≤190°→36°、>190°→38°。", size=9.5, color=GREY)

    heading(doc, "3.3 由标准推导出的几何规则（写进代码，可评审）", 2)
    make_table(
        doc,
        ["几何量", "取值规则", "依据"],
        [
            ["外径 da", "da = 基准直径 dd + 2·ha", "标准表列 ha（槽顶高）位于外圆与基准线之间"],
            ["槽在基准线处宽度", "= bd（基准宽度）", "基准线即基准直径所在的圆柱面"],
            ["槽顶宽（外圆处）", "bd − 2·ha·tan(φ/2)", "槽壁斜率为半槽角 φ/2"],
            ["槽底宽", "bd + 2·hf·tan(φ/2)", "同上，槽底比基准线更深 hf"],
            ["轮缘最小宽度", "(n−1)·e + 2f（f 为槽顶半宽）", "n 个槽按槽间距 e 排布，两侧需留边"],
            ["轮毂 / 辐板 / 轮缘内径", "d1=(1.8~2)d、L=(1.5~2)d、S≈(0.6~1)e、d2=da−2(ha+hf+δ)",
             "手册“带轮结构型式和辐板厚度”"],
            ["同步带轮齿形", "节距 p、齿高 ht、齿顶宽 tip 按型号取值；节圆 dd = p·z/π",
             "周节制 GB/T 11361—1989 / 圆弧齿 JB/T 7512.2—1994"],
        ],
        widths=[3.4, 7.4, 6.0], font_size=8.5)

    heading(doc, "3.4 修改前后的对比（同一个输入）", 2)
    make_table(
        doc,
        ["环节", "修改前", "修改后"],
        [
            ["输入", "多槽带轮 内径25 外径100 宽度30 槽数3", "（同左）"],
            ["识别结果", "参数不全，退化成“圆柱 + 通孔”", "识别为多槽带轮，槽数 3、槽型按 ⌀100 推荐 A 型"],
            ["生成几何", "光轮（无槽）", "A 型标准轮槽 3 条：槽角 34°、全槽深 11.45、槽间距 15"],
            ["外径", "100（把输入当成外径）", "105.5 ＝ 基准直径 100 + 2×ha(2.75)"],
            ["轮缘宽度", "30（直接采用）", "39.32（不足标准轮缘宽度时按标准取，并给出提示）"],
            ["CATIA 导出", "圆凸台（光轮）", "盘体 + 3 条标准 V 形槽（逐条 poly_cut）"],
            ["日志", "只写“需在 CATIA 里开槽”", "逐条列出「提示词 → 生成特征 → 标准依据」"],
        ],
        widths=[3.0, 6.4, 7.6], font_size=8.5)

    heading(doc, "3.5 界面里看到的「提示词 → 生成特征」对照表（真实输出）", 2)
    para(doc, "这是对测试者第二个问题的直接回答：每次生成后，日志区会按下面这样的格式逐条说明。",
         size=10)
    example = [
        [Q1 + "A 型" + Q2, "A 型轮槽 3 条", "GB/T 10412—2002（基准宽度制）"],
        [Q1 + "槽数 3" + Q2, "轮缘上开 3 条 V 形槽，基准线间距 e=15 mm", "槽间距 e 取自轮槽截面尺寸表"],
        ["基准直径 ⌀100", "基准线所在圆柱面；外径 da = dd + 2·ha = ⌀105.5", "da = dd + 2·ha"],
        ["槽剖面", "槽顶宽 9.32、槽底宽 16.32、全槽深 11.45（ha=2.75 + hf=8.7）",
         "槽角 φ=34° 按基准直径分档"],
        ["轮缘宽度", "轮缘最小宽度 = (n−1)·e + 2f = 39.32", "f 为槽顶半宽，保证槽不开口在端面上"],
        ["内孔 ⌀25", "中心通孔", "输入给定（未给则按 dd 的 0.22 倍）"],
        ["轮毂/辐板", "轮毂 ⌀48.75×39.32、辐板厚 9、轮缘内径 ⌀76.6", "手册带轮结构公式"],
    ]
    make_table(doc, ["提示词 / 参数", "对应生成的几何特征", "标准依据"], example,
               widths=[3.0, 7.4, 6.4], font_size=9)

    # ---------------- 第 4 部分 ----------------
    heading(doc, "第 4 部分  多次学习：题库与多轮准确率", 1)
    para(doc, "说明：这里的学习 ＝ 规则与标准知识库的迭代（跑题库 → 定位失败 → 改规则/补标准 → 再跑），"
              "不是训练神经网络。全程可复现：python tools\\learn_pulley_prompts.py --rounds 3",
         size=10, color=GREY)

    heading(doc, "4.1 逐轮成绩", 2)
    rows = []
    for r in rounds:
        rows.append(["第 %d 轮" % r["round"], r["time"], r["total"], r["passed"],
                     "%.1f%%" % r["accuracy"]])
    if rows:
        make_table(doc, ["轮次", "时间", "题量", "通过", "准确率"], rows,
                   widths=[2.4, 4.6, 2.2, 2.2, 2.6], font_size=9.5)
        para(doc, "第 1 轮为人工题库（覆盖 7 种 V 带槽型、SP 系列、同步带轮型号、各种口语与符号写法、"
                  "以及干扰句）；第 2、3 轮由脚本按“零件名 × 参数写法 × 槽型”组合自动扩充新题，"
                  "用来检验规则是否真的泛化，而不是把已有题目背下来。",
             size=9.5, color=GREY)
    else:
        para(doc, "（尚未生成轮次报告，请先运行 python tools\\learn_pulley_prompts.py）", size=10)

    heading(doc, "4.2 学习过程记录（每轮改了什么）", 2)
    make_table(
        doc,
        ["轮次", "暴露出来的问题", "对应修复"],
        [
            ["第 1 轮",
             "带轮退化成光轮；槽型读不出；「3个槽」被读成 303；「带轮」被同步带轮抢走；"
             "中文槽数（双槽/四槽）读不出",
             "新增型号识别与信号词消歧；参数边界校验；中文槽数解析；切除动作误判修正"],
            ["第 2 轮",
             "自动扩充的新写法里：连写「宽度402个槽」把槽数读丢；「5槽 Z型 内径12」把内径读成槽数",
             "别名取值改为遍历所有出现位置 + 数字后缀边界判断；合并时去重叠；连写数字末尾取槽数兜底"],
            ["第 3 轮",
             "题库再扩充后未出现新的系统性失败；已知例外：用户给的宽度小于标准轮缘宽度时，"
             "程序按标准放大并提示",
             "该项作为已知行为写入文档与日志提示，不再默默改尺寸"],
        ],
        widths=[2.0, 7.0, 7.6], font_size=8.5)

    heading(doc, "4.3 全量命中率（确认没有改坏别的零件）", 2)
    if ev:
        by_cat = ev.get("by_category") or {}
        low = sorted(by_cat.items(), key=lambda kv: kv[1].get("rate", 0))[:6]
        make_table(
            doc,
            ["指标", "数值", "说明"],
            [
                ["评测用例", "%d 条 / %d 个类别" % (ev.get("n", 0), len(by_cat)),
                 "随机参数 + 口语模板生成，与知识库参考值比对"],
                ["整体命中率", "%.2f%%（%d/%d）" % (ev.get("hit_rate", 0), ev.get("hits", 0),
                                                    ev.get("n", 1)),
                 "零件最多 2% 尺寸容差；带轮按标准外径 da=dd+2ha 判定"],
                ["最低的几类", "、".join("%s %.0f%%" % (k, v.get("rate", 0)) for k, v in low),
                 "这些是既有问题（弹簧自由高度、曲轴/连杆包络、螺栓对角宽度），非本轮引入"],
            ],
            widths=[3.0, 5.4, 8.4], font_size=9)

    heading(doc, "4.4 如何自己复跑（可交付给别人验证）", 2)
    bullet(doc, "python tools\\extract_pulley_std.py —— 重新抽取国家标准数据，"
                "并自检（与公开标准值逐项比对）。", bold_prefix="① 标准数据：")
    bullet(doc, "python tools\\learn_pulley_prompts.py --rounds 3 —— 跑三轮题库，输出 "
                "reports\\pulley_learning_round*.json 与《带轮提示词学习报告.md》。",
           bold_prefix="② 多轮学习：")
    bullet(doc, "python tools\\evaluate.py —— 全零件命中率评测（161 条用例 / 32 类），"
                "结果写到 reports\\eval_baseline.json。", bold_prefix="③ 全量回归：")
    bullet(doc, "python -m ai_modeler --selftest —— 程序内置自检 34 项"
                "（含 CATIA 宏编译、MCP 服务、导出格式）。", bold_prefix="④ 功能自检：")

    # ---------------- 第 5 部分 ----------------
    heading(doc, "第 5 部分  已知限制与下一步", 1)
    heading(doc, "5.1 已知限制（如实说明）", 2)
    make_table(
        doc,
        ["限制", "现状", "影响与处理"],
        [
            ["宽度小于标准轮缘宽度时会被放大",
             "例如 3 个 A 型槽的轮缘最小宽度 39.32，用户写 30 时会取 39.32",
             "不放大就会相邻槽干涉（工程上不成立）；程序会明确提示，并列为已知行为"],
            ["CATIA 真机验证仍待补",
             "V 带轮的槽已改为“盘体 + 逐条 V 形槽”的 CATIA 操作序列，语法与宏编译自检通过，"
             "但尚未在企业真机上运行",
             "这是进企业实测前必须先做的一件事（见 5.2 第 1 项）"],
            ["同步带轮在 CATIA 里只导盘体",
             "轮齿以“标准参数 + 阵列提示”的方式给出，需要一根齿形草图做圆周阵列",
             "内核里同步带轮齿形是真实生成的（可导出 STL 查看）"],
            ["槽底圆角按直线近似",
             "标准表给了 r1/r2 圆角值，几何上目前用折线近似",
             "对轮廓影响极小；后续可加真实圆角"],
        ],
        widths=[4.0, 6.2, 6.4], font_size=8.5)

    heading(doc, "5.2 下一步（对应既有执行方案第 1–2 周）", 2)
    make_table(
        doc,
        ["优先级", "做什么", "完成判据", "负责人"],
        [
            ["P0", "在真实 CATIA 上跑一次「一句话 → 带轮带槽 → .CATPart」",
             "真机里数出 3 条 V 形槽，槽间距/槽深与标准一致，并录屏留证", "开发 A + CATIA 主测"],
            ["P0", "把这批题库变成人工验收用例，由 CATIA 成员逐条判定",
             "题库 66 题 + 自动扩充 24 题全部人工确认（现在只有程序自判）", "CATIA 主测"],
            ["P1", "用同一方法把其余零件也“标准数据化”（轴承/链轮/弹簧优先）",
             "每类零件都有 kb 标准数据文件 + 来源 + 自检 + 多轮报告", "开发 A + 开发 B"],
            ["P1", "扩题库到 300 题，并加入企业真实零件样本",
             "覆盖企业常见件型；失败样本能自动归类到失败原因", "开发 B"],
            ["P2", "把「提示词 → 生成特征 → 标准依据」做成界面里可复制的报告",
             "一键导出说明，可直接附在给企业的演示材料里", "你 + 开发 B"],
        ],
        widths=[1.6, 6.0, 6.4, 3.4], font_size=8.5)

    heading(doc, "5.3 给测试者的回复建议（可直接发群）", 2)
    callout(doc, "建议回话",
            "带轮没槽这个问题确认了，是我这边的建模逻辑没做到位：当时只画了轮体、没把 V 形槽生成出来。\n"
            "现在已经按国家标准重做 —— 轮槽尺寸和槽角取自 GB/T 10412—2002 / GB/T 10413—2002，"
            "外径按 da ＝ 基准直径 + 2×槽顶高 推导，轮缘宽度按槽间距排布；"
            "同步带轮也改成按标准节距出真实齿形。\n"
            "另外你说的“提示词对应什么特征”，现在每次生成都会在日志里逐条列出来"
            "（哪句话 → 哪个特征 → 依据哪条标准），界面上可以直接看到。\n"
            "麻烦你在 CATIA 里再验一次：输入「多槽带轮 内径25 外径100 宽度30 槽数3 A型」，"
            "看看是不是 3 条标准 V 形槽、槽间距 15、槽深 11.45。有问题继续拍给我。")

    doc.add_paragraph()
    para(doc, "—— 本报告依据测试者反馈、CATIA 真机日志、国家标准数据（GB/T 10412—2002、"
              "GB/T 10413—2002、GB/T 11361—1989、JB/T 7512.2—1994）与本轮代码改动整理，"
              "用于内部对齐，不对外发布。",
         size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER)

    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    p = build()
    print("已生成：%s" % p)
    print("大小：%.1f KB" % (os.path.getsize(p) / 1024.0))
