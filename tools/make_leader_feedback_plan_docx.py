# -*- coding: utf-8 -*-
r"""
生成《沟通解读 + 可执行方案》Word 文档。

输入：微信沟通截图（5 条消息）
输出：AI自动建模插件_沟通解读与可执行方案.docx（生成到项目根目录）

依赖：python-docx
运行：python tools\make_leader_feedback_plan_docx.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_NAME = "AI自动建模插件_沟通解读与可执行方案.docx"
OUT_PATH = os.path.join(ROOT, OUT_NAME)

CN_FONT = "微软雅黑"
CN_FONT_BODY = "等线"
EN_FONT = "Segoe UI"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
ACCENT2 = RGBColor(0xC0, 0x50, 0x4D)
GREY = RGBColor(0x59, 0x59, 0x59)


# --------------------------------------------------------------------------
# 基础排版工具
# --------------------------------------------------------------------------
def set_run_font(run, size=10.5, bold=False, color=None, cn=CN_FONT_BODY, en=EN_FONT):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = en
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), cn)
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)


def para(doc, text="", size=10.5, bold=False, color=None, align=None,
         space_before=2, space_after=4, indent=None, cn=CN_FONT_BODY, line=1.35):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if indent is not None:
        pf.left_indent = Cm(indent)
    if align is not None:
        p.alignment = align
    if text:
        set_run_font(p.add_run(text), size=size, bold=bold, color=color, cn=cn)
    return p


def heading(doc, text, level=1):
    sizes = {1: 15, 2: 12.5, 3: 11}
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(14 if level == 1 else 10)
    pf.space_after = Pt(6)
    pf.keep_with_next = True
    set_run_font(p.add_run(text), size=sizes.get(level, 11), bold=True,
                 color=ACCENT if level < 3 else ACCENT2, cn=CN_FONT)
    if level == 1:
        pb = p._element.get_or_add_pPr()
        bdr = pb.makeelement(qn("w:pBdr"), {})
        bottom = bdr.makeelement(qn("w:bottom"), {})
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "8")
        bottom.set(qn("w:space"), "3")
        bottom.set(qn("w:color"), "1F4E79")
        bdr.append(bottom)
        pb.append(bdr)
    return p


def bullet(doc, text, size=10.5, level=0, bold_prefix=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Cm(0.6 + 0.55 * level)
    pf.first_line_indent = Cm(-0.45)
    pf.space_before = Pt(1)
    pf.space_after = Pt(2)
    pf.line_spacing = 1.3
    mark = "● " if level == 0 else "○ "
    set_run_font(p.add_run(mark), size=size, color=ACCENT)
    if bold_prefix:
        set_run_font(p.add_run(bold_prefix), size=size, bold=True)
    set_run_font(p.add_run(text), size=size)
    return p


def style_table(table, header_bold=True, font_size=9.5):
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r_i, row in enumerate(table.rows):
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
                p.paragraph_format.line_spacing = 1.2
                for run in p.runs:
                    set_run_font(run, size=font_size,
                                 bold=(header_bold and r_i == 0),
                                 color=ACCENT if r_i == 0 else None)
            if r_i == 0:
                shd = cell._tc.get_or_add_tcPr().makeelement(qn("w:shd"), {})
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "DCE6F1")
                cell._tc.get_or_add_tcPr().append(shd)
    return table


def make_table(doc, headers, rows, widths=None, font_size=9.5):
    t = doc.add_table(rows=1, cols=len(headers))
    for i, h in enumerate(headers):
        t.rows[0].cells[i].text = h
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val)
    style_table(t, font_size=font_size)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)
    return t


def callout(doc, title, body, fill="FFF2CC", border="BF8F00"):
    t = doc.add_table(rows=1, cols=1)
    cell = t.rows[0].cells[0]
    cell.text = ""
    p1 = cell.paragraphs[0]
    p1.paragraph_format.space_after = Pt(2)
    set_run_font(p1.add_run(title), size=10.5, bold=True, color=RGBColor(0x7F, 0x60, 0x00), cn=CN_FONT)
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_before = Pt(0)
    p2.paragraph_format.line_spacing = 1.3
    set_run_font(p2.add_run(body), size=10)
    tcpr = cell._tc.get_or_add_tcPr()
    shd = tcpr.makeelement(qn("w:shd"), {})
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)
    borders = tcpr.makeelement(qn("w:tcBorders"), {})
    for side in ("top", "left", "bottom", "right"):
        el = borders.makeelement(qn("w:" + side), {})
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "12")
        el.set(qn("w:color"), border)
        borders.append(el)
    tcpr.append(borders)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


# --------------------------------------------------------------------------
# 文档正文
# --------------------------------------------------------------------------
def build():
    doc = Document()

    # 页面与默认字体
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

    # ---------------- 封面区 ----------------
    para(doc, "AI 自动建模插件 · 沟通解读与落地执行方案",
         size=20, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER,
         space_before=6, space_after=4, cn=CN_FONT)
    para(doc, "（依据微信沟通截图 5 条消息：开源鸿蒙实现 · 工业软件定位 · 组建 vibe coding + CATIA 小队 · 企业实测）",
         size=10, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, "生成时间：%s     整理人：项目执行侧     版本：v1.0"
         % datetime.now().strftime("%Y-%m-%d %H:%M"),
         size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    callout(doc, "一句话结论",
            "这 5 句是在给产品“定调 + 派活 + 提前铺路”：\n"
            "① 调性上要求“国产化（开源鸿蒙）+ 工业级”，不能只是玩具 Demo；\n"
            "② 组织上要求立刻组建“1–2 名 vibe coding 开发者 + 1–2 名会 CATIA 的成员”小队，形成“开发—测试—反馈”闭环；\n"
            "③ 路径上由他负责把产品推给企业做实测，用真实反馈拉升产品价值。\n"
            "你要交的不是一个插件，而是“一支能在 4 周内跑起来的小队 + 一条能被企业验证的闭环”。")

    heading(doc, "第 0 部分  快速结论：这 5 句话到底在说什么（一页纸）", 1)
    make_table(
        doc,
        ["#", "原话摘要", "真实意图", "对产品/你的硬要求", "最大风险", "你要立刻做的动作"],
        [
            ["1", "可以加开源鸿蒙 / 我们是工业软件",
             "给产品贴“国产化 + 工业级”标签，提高可讲性与合规性",
             "技术路线要能往信创/国产化靠；质量按工业软件标准（确定性、可追溯、可复现）",
             "把“基于开源鸿蒙”当成已完成的事实对外讲，一被追问就穿帮",
             "先把“开源鸿蒙”定义清楚（见 §4 三选一），先讲“未来可适配”，不讲“已实现”"],
            ["2", "尽快召集会 CATIA 的 vibe coding 成员",
             "催的是人，不是代码；他要一支小队，不是一个人硬扛",
             "今天就要拉群/找人：至少 2 名会 AI 编程 + 2 名会 CATIA",
             "招不到“又会 CATIA 又会 AI 编程”的人，导致招人卡死、进度停滞",
             "拆成两类角色招，不追求“全能选手”；先拉 2 人小分队开工"],
            ["3", "你带 1–2 人做插件，1–2 名 CATIA 成员测试反馈",
             "指定你为组长，并划清“做”与“验”的边界（不能自己既当运动员又当裁判）",
             "你负责插件开发与节奏；CATIA 成员负责验收、出问题单、指方向",
             "CATIA 成员只靠看截图提意见，反馈没有验收标准 → 反复返工",
             "今天产出《验收用例表》+《问题单模板》，24 小时内开第一次对齐会"],
            ["4", "我再把产品推给企业做实测和反馈",
             "他掌握企业渠道，但推出去了就回不了头；实测 = 信任背书，也 = 信任抵押",
             "产品要“拿得出手”：安装包、版本号、日志、回滚、保密与数据边界",
             "裸奔进企业：装不上/崩一次/涉密图纸外流 → 口碑直接归零",
             "4 周内做一次“可交付预演”；企业实测前必须过 §3 的 5 项准入自查"],
            ["5", "产品意义和价值度拉满",
             "口号，同时是带条件的承诺：价值要靠“企业实测通过”来兑现",
             "把“价值”翻译成可测指标（一次通过率、效率倍数、可追溯性）",
             "把“拉满”理解成“多做功能、做大而全”→ 4 周后什么都没验证",
             "锁定 3 个北极星指标，4 周后拿数字说话，而不是拿演示视频说话"],
        ],
        widths=[0.7, 2.6, 3.2, 3.6, 3.2, 3.9], font_size=8.5)

    # ---------------- 逐句分析 ----------------
    heading(doc, "第 1 部分  逐句详细分析", 1)
    para(doc, "说明：截图为 10:56 前后的连续对话，对方（头像一致）连发 5 条，前 3 条是要求，"
              "第 4 条是承诺，第 5 条是定性总结。以下逐句拆“字面意思 / 真实意图 / 隐含要求 / 风险 / 我要做的动作”。",
         size=10, color=GREY)

    # 第 1 句
    heading(doc, "第 1 句：“因为可以添加 1. 基于开源鸿蒙来实现；2. 我们是 AI+软件，但是是工业软件”", 2)
    para(doc, "■ 字面意思", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "他给产品补两条“加分项”：一是技术上可以走“基于开源鸿蒙（OpenHarmony）”的实现路线；"
              "二是给产品类别定性——虽然底层是 AI，但交付形态属于工业软件，不是消费级小工具。")
    para(doc, "■ 真实意图", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "开源鸿蒙是“讲故事/过评审”的抓手：信创、国产化替代、自主可控，是当前工业与国企项目最看重的硬门槛。", bold_prefix="对外可讲性：")
    bullet(doc, "“工业软件”是抬高门槛的提醒：工业软件的验收标准是准确性、确定性、可追溯性、可重复性，而不是“看着挺酷”。", bold_prefix="对内定调：")
    bullet(doc, "他在暗示：将来面对企业/评审时，你不能只有“AI + MCP + 插件”这套说法，还要能回答“国产化怎么走”。", bold_prefix="潜台词：")
    para(doc, "■ 隐含要求（必须接住的 3 件事）", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "给出一条可被追问的国产化路线说明（哪怕只是路线图，也要写得清楚、不吹牛）。", level=0)
    bullet(doc, "把质量口径从“Demo 级”抬到“工业级”：安装可复现、版本可追溯、出错可回滚、结果可复算。", level=0)
    bullet(doc, "别再用“AI 生成看起来对就行”来验收，要落到几何精度、特征树规范、尺寸可编辑性上。", level=0)
    para(doc, "■ 风险点（最重要的一句）", bold=True, size=10.5, space_before=6, space_after=2)
    callout(doc, "风险提示：不要把“可以基于开源鸿蒙”讲成“已经基于开源鸿蒙”",
            "当前项目实际是：Windows + Python 标准库的桌面程序（内置几何内核 + tkinter 预览 + MCP 服务端 + CATIA 宏通道）。\n"
            "它与 OpenHarmony 并不是同一技术栈。若对外宣称“基于开源鸿蒙”，一旦对方问“怎么装的、跑在什么设备上、有没有鸿蒙版本”，"
            "当场就会被拆穿——这比不说更伤信任。\n"
            "正确做法：用“能力分级 + 路线图”的说法（见第 4 部分 §4.3），把“已具备 / 可适配 / 规划中”分开写清楚。",
            fill="FDE9E9", border="C0504D")
    para(doc, "■ 我要做的动作", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "把 3 个问题一次性问清楚：鸿蒙是用在哪一层？（详见 §4.3 的三选一）", bold_prefix="本周：")
    bullet(doc, "产出一页《国产化/信创适配说明》，只写事实与步骤，不写形容词。", bold_prefix="本周：")
    bullet(doc, "把所有对外话术里的“基于开源鸿蒙”统一改成“兼容开源鸿蒙生态的适配路线（分阶段）”。", bold_prefix="本周：")

    # 第 2 句
    heading(doc, "第 2 句：“尽快召集 vibe coding，会操作 CATIA 的成员”", 2)
    para(doc, "■ 字面意思", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "立刻找人：既要会用 AI 编程（vibe coding）快速产出功能的人，也要会用 CATIA 的人。")
    para(doc, "■ 真实意图", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "“尽快”= 他不想听规划，想看到人已经到位、事情已经开始动。这是一条催办指令。", bold_prefix="节奏诉求：")
    bullet(doc, "CATIA 是真正的行业门槛：不懂 CATIA 的人写不出可用的插件，也判断不了模型对不对。", bold_prefix="能力诉求：")
    bullet(doc, "他默认“一个人既会 AI 编程又会 CATIA”很难找，所以他其实在要一支混编小队。", bold_prefix="组织诉求：")
    para(doc, "■ 这句话里的最大陷阱", bold=True, size=10.5, space_before=6, space_after=2)
    callout(doc, "陷阱：把“vibe coding”和“会 CATIA”理解成同一个人",
            "现实是：会 vibe coding 的人多，会 CATIA 的人也多，两样都精的人极少。\n"
            "若按“全能选手”去招，大概率招不到、拖两周，小队迟迟开不了工，直接违背“尽快”。\n"
            "正解：拆成两类角色分别招，用“共识文件”把两者焊在一起——开发不需要精通 CATIA，但必须能听懂 CATIA 成员的验收语言。",
            fill="EAF3FB", border="1F4E79")
    para(doc, "■ 我要做的动作", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "当天发出招募帖（模板见 §4.5），明确“角色 + 每周投入时长 + 干什么 + 不需要干什么”。", bold_prefix="今天：")
    bullet(doc, "目标人数 4 人：2 名 AI 编程（1 主 1 辅）+ 2 名 CATIA（1 主测 1 副测/业务视角）。", bold_prefix="今天：")
    bullet(doc, "给每个人一句话任务，避免“多人围观、无人负责”。", bold_prefix="48 小时内：")

    # 第 3 句
    heading(doc, "第 3 句：“这样你带着 1–2 vibe coding 做插件，1–2 个 Catia 成员来测试和反馈优化方向”", 2)
    para(doc, "■ 字面意思", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "明确分工与汇报关系：你是组长，带 1–2 人做插件开发；1–2 名 CATIA 成员负责测试、反馈、指出优化方向。")
    para(doc, "■ 真实意图", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "你被指定为唯一负责人（Single Owner）：进度、质量、对外交付都算在你头上。", bold_prefix="定责：")
    bullet(doc, "“做”和“验”必须分家：自己写的功能不能自己判合格，否则企业一测就垮。", bold_prefix="分权：")
    bullet(doc, "CATIA 成员不只是测试员，还要“反馈优化方向”——他们其实是需求方代表，决定下一版做什么。", bold_prefix="定方向：")
    para(doc, "■ 隐含要求：闭环要有牙齿", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "“测试和反馈”如果没有标准，就会变成“我觉得不好看”“再改改”——这是最典型的返工黑洞。"
              "必须把它工程化：验收用例 + 问题单 + 优先级 + 每周复盘。", size=10.5)
    bullet(doc, "统一入口：所有问题只进一张问题单（表格/看板），不散落在聊天记录里。", bold_prefix="闭环要件 1：")
    bullet(doc, "统一语言：把“画得不对”翻译成“第 3 步 圆角半径应为 R8，实际 R5”。", bold_prefix="闭环要件 2：")
    bullet(doc, "统一节奏：每天 15 分钟站会，每周一次版本验收会（固定时间，不临时通知）。", bold_prefix="闭环要件 3：")
    para(doc, "■ 风险点", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "CATIA 成员只看截图/录屏就给意见 → 反馈失真，改了也不算数。必须以“真机 + 真零件 + 真特征树”为准。")
    bullet(doc, "开发侧改了 A、坏了 B（无回归测试）→ 每轮迭代都在修旧 bug，命中率不升反降。")
    bullet(doc, "两名 CATIA 成员意见冲突且无人拍板 → 必须约定：以“能不能在企业现场一次跑通”为唯一裁判。")
    para(doc, "■ 我要做的动作", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "写一份《角色与分工表》（§4.2）并在群里公开，让每个人知道边界。", bold_prefix="今天：")
    bullet(doc, "产出《验收用例表》+《问题单模板》（§3.4 附表结构）。", bold_prefix="24 小时内：")
    bullet(doc, "定死两个会：每日站会 15 分钟、每周验收会 45 分钟，写进日历。", bold_prefix="24 小时内：")

    # 第 4 句
    heading(doc, "第 4 句：“我再将你们的产品推送给企业做实测和反馈”（10:56）", 2)
    para(doc, "■ 字面意思", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "他负责对外的企业渠道：把你们做出来的东西拿给企业用，拿回真实反馈。")
    para(doc, "■ 真实意图", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "这是一张“信任信用卡”：企业测试机会宝贵（通常只给一次），也是产品最值钱的背书。", bold_prefix="资源投入：")
    bullet(doc, "同时是一次“验收考试”：企业现场就是最严格的测试环境，装不上就结束了。", bold_prefix="隐性门槛：")
    bullet(doc, "他把“做完”和“被验证”两件事分开：你做后者要配合他，包括演示材料、安装包、答疑。", bold_prefix="分工含义：")
    para(doc, "■ 这句话把项目的性质改变了", bold=True, size=10.5, space_before=6, space_after=2)
    callout(doc, "重点：从“做产品”进入“被验收”阶段",
            "之前的标准是“功能做出来没有”；\n"
            "现在的标准是“陌生人拿到安装包，30 分钟内能不能在真机上跑出对的结果”。\n"
            "企业实测一旦开始，每一次崩溃都会被记住。所以真正的工作重心要从“加功能”转向“保稳定 + 可复现 + 可追溯”。",
            fill="EAF3FB", border="1F4E79")
    para(doc, "■ 风险清单（企业实测前必须堵住的 6 个洞）", bold=True, size=10.5, space_before=6, space_after=2)
    make_table(
        doc,
        ["#", "可能的翻车点", "后果", "对应动作"],
        [
            ["1", "CATIA 宏插件在企业真机上跑不起来（目前**未在真实 CATIA 上验证过**）", "第一次演示即失败，信任归零",
             "找一台真机 + 一个真 CATIA 零件，跑通“一句话 → 特征树”全链路，录屏留证"],
            ["2", "企业没有 Python / 环境不干净 / 无管理员权限", "卡在安装第一步",
             "做免安装绿色版 + 一键检测脚本 + 图文安装单页"],
            ["3", "模型尺寸/特征树不合规范，工程师不能改", "被判定“不能用于生产”",
             "验收标准加一条：生成的特征树必须能在 CATIA 里直接改参数重建"],
            ["4", "企业图纸/数据外传（涉密风险）", "合规事故，比 bug 严重得多",
             "默认离线、明确不联网、签保密承诺、日志不落零件内容"],
            ["5", "出问题无法复现、无日志、无法回滚", "问题查不动，迭代停摆",
             "验收前必须补齐：版本号 + 操作日志 + 一键回滚"],
            ["6", "演示话术过度承诺（如“完全替代人工建模”）", "预期落差，实测被差评",
             "统一话术：定位“辅助/加速”，并给出适用零件范围清单"],
        ],
        widths=[0.8, 4.6, 3.6, 6.0], font_size=8.5)
    para(doc, "■ 我要做的动作", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "准备一个“企业实测包”：安装包 + 一页安装说明 + 3 分钟演示脚本 + 5 个典型零件用例 + 问题反馈表。", bold_prefix="4 周内：")
    bullet(doc, "实测前自己做一次“陌生人测试”：找没参与开发的人，按说明独立装一遍，全程记录卡点。", bold_prefix="实测前：")
    bullet(doc, "实测后 48 小时内出《实测反馈分析》：问题分类（装不上 / 建不出 / 建错 / 体验差）+ 修复优先级。", bold_prefix="实测后：")

    # 第 5 句
    heading(doc, "第 5 句：“那咱们这个产品觉得意义和价值度拉满”", 2)
    para(doc, "■ 字面意思", bold=True, size=10.5, space_before=6, space_after=2)
    para(doc, "一句总结性肯定：如果前面几件事都做到，产品价值就站得住。")
    para(doc, "■ 真实意图", bold=True, size=10.5, space_before=6, space_after=2)
    bullet(doc, "这是激励，也是条件句——“意义和价值度”是结果，不是现状。它建立在“团队 + 实测”两件事做成的前提上。", bold_prefix="条件性：")
    bullet(doc, "他关心的是“能不能对外讲出价值”，而不是“代码写得多优雅”。所以你要主动把价值量化。", bold_prefix="关注点：")
    bullet(doc, "这句话之后，他不会再催你细节，但会看你有没有拿出可对外的东西（演示、数据、案例）。", bold_prefix="后续预期：")
    para(doc, "■ 把“价值”翻译成可测指标（必须锁定 3 个北极星指标）", bold=True, size=10.5, space_before=6, space_after=2)
    make_table(
        doc,
        ["指标", "定义", "当前基线（已知）", "4 周目标"],
        [
            ["① 一次通过率", "输入一句话，无需人工干预即得到正确模型的题目占比",
             "自测用例命中率 100%（385 条，为自建用例，非企业真实场景）",
             "在“企业真实零件抽样 30 例”上 ≥ 70%，且失败必须给出可读原因"],
            ["② 建模提速倍数", "同一零件：人工建模耗时 ÷ AI 辅助耗时",
             "尚无第三方测量数据",
             "在 5 个典型件上实测 ≥ 3 倍（含人工修正时间，不许只算生成时间）"],
            ["③ 可信度（可追溯性）", "能否回答“这模型是谁、什么时候、用什么输入生成的、怎么复现”",
             "有建模报告导出、有版本号，但无统一操作日志",
             "100% 的演示过程可复现：版本号 + 输入 + 输出文件 + 操作日志齐全"],
        ],
        widths=[2.6, 4.4, 4.6, 5.4], font_size=8.5)
    para(doc, "提示：对外沟通时用指标说话，比用“AI 很强大”有效 10 倍。这三个指标同时是给领导汇报的现成模板。",
         size=9.5, color=GREY)

    # ---------------- 战略解读 ----------------
    heading(doc, "第 2 部分  整体战略解读（这 5 句话串起来是一条什么路径）", 1)
    make_table(
        doc,
        ["层次", "对方的要求", "翻译成执行语言", "成功判据"],
        [
            ["讲什么（定位）", "开源鸿蒙 + 工业软件",
             "对外叙事 = 国产化可行 + 工业级质量；对内标准 = 确定性、可追溯、可复现",
             "能拿出一页《定位与国产化路线说明》，被追问不露怯"],
            ["谁来做（组织）", "vibe coding + CATIA 成员，你带队",
             "4 人小队：2 开发 + 2 验收；你是唯一负责人；开发与验收分离",
             "小队名单、分工表、会议日历 48 小时内落地"],
            ["怎么做（方法）", "开发做插件，CATIA 测试反馈方向",
             "用例驱动 + 问题单闭环 + 每周一个可演示版本",
             "每周都有可演示版本；问题单关闭率与回归通过率可查"],
            ["怎么验（价值）", "推给企业实测，价值拉满",
             "4 周内达到“可交付预演”标准，然后进企业实测，用数据回流迭代",
             "企业现场一次跑通；48 小时内产出反馈分析报告"],
        ],
        widths=[2.6, 4.0, 6.4, 4.6], font_size=8.5)

    para(doc, "一句话记法：", bold=True, size=10.5, space_before=8, space_after=2)
    callout(doc, "记忆口诀",
            "定位抬高（工业软件 + 国产化路线）→ 人先到位（4 人小队）→ 开发与验收分家 → 每周一个能跑的版本 → 企业实测拿数据 → 用数据回填价值。")

    # ---------------- 可执行方案 ----------------
    heading(doc, "第 3 部分  可执行方案（4 周版，谁在什么时候交什么）", 1)
    para(doc, "原则：能并行就不串行；每周五必须有一个“能被外人看见并操作”的版本；任何一周没有可演示版本，就说明节奏错了。",
         size=10, color=GREY)

    heading(doc, "3.1 人员与分工（今天就要定下来）", 2)
    make_table(
        doc,
        ["角色", "人数", "职责（做什么）", "明确不做什么", "交付物"],
        [
            ["你（组长）", "1", "排期、拆任务、对外沟通、把控“工业级质量底线”；做技术决策与最终拍板",
             "不亲手写所有功能（否则变成单点瓶颈）", "每周版本 + 周报 + 对外材料"],
            ["开发 A（vibe coding 主力）", "1", "CATIA 通道打通与插件本体；解析器/特征映射等核心功能",
             "不自己判定“做得对不对”", "每周可运行版本 + 变更说明"],
            ["开发 B（vibe coding 辅助）", "1", "测试用例自动化、日志/版本/安装包、兼容性与回归测试",
             "不擅自改需求范围", "自动化回归脚本 + 安装包"],
            ["CATIA 主测", "1", "真机验收、对照标准件判对错、写问题单、定优先级",
             "不只看截图给意见，必须真机复现", "问题单 + 验收结论 + 优先级建议"],
            ["CATIA 副测/业务视角（可兼职）", "1", "从“企业工程师会不会用”角度提体验与规范问题；准备演示脚本",
             "不参与技术方案争论", "体验问题清单 + 演示脚本"],
        ],
        widths=[3.0, 1.2, 6.2, 3.6, 3.6], font_size=8.5)

    heading(doc, "3.2 四周排期（每周都有交付物，不空转）", 2)

    heading(doc, "第 0 周（本周，2–3 天）：把人凑齐 + 把验收语言统一", 3)
    bullet(doc, "发出招募/拉群，凑齐 4 人小队，角色写清楚（§4.5 模板）。", bold_prefix="Day 1：")
    bullet(doc, "产出《术语与验收共识》（1 页）：什么叫“建对了”，什么叫“可交付”；统一 CATIA 术语与项目术语的对应。", bold_prefix="Day 1–2：")
    bullet(doc, "CATIA 成员提供 10 个真实零件样例（截图 + 图纸或尺寸 + 期望结果），作为第一批验收用例。", bold_prefix="Day 2：")
    bullet(doc, "确认 CATIA 真机环境（版本号、是否正版授权、能否装宏），这一步是整个计划的关键路径。", bold_prefix="Day 2：")
    bullet(doc, "开第一次 45 分钟对齐会：定 4 周目标、定会议时间、定问题单入口。", bold_prefix="Day 3：")
    para(doc, "本周交付物：《小队分工表》《术语与验收共识》《10 条验收用例（初版）》《CATIA 真机可用性结论》",
         size=9.5, color=GREY, bold=True)

    heading(doc, "第 1 周：打通一条真机链路（最重要的一周）", 3)
    bullet(doc, "在一台真实 CATIA 上，跑通“一句中文描述 → CATIA 里出现正确特征树并保存为 .CATPart”。", bold_prefix="目标：")
    bullet(doc, "把宏插件的安装、注册、运行步骤写成图文单页（陌生人可照做）；记录版本号。", bold_prefix="动作：")
    bullet(doc, "对 10 条验收用例逐条记录：成功 / 失败 / 失败原因（解析错、几何错、API 报错、环境问题）。", bold_prefix="动作：")
    para(doc, "本周交付物：真机演示录屏（3 分钟）+ 图文安装单页 + 10 条用例首轮结果（含失败原因分类）",
         size=9.5, color=GREY, bold=True)

    heading(doc, "第 2 周：把失败率打下来（用数据迭代）", 3)
    bullet(doc, "集中修“Top 3 失败原因”，不要撒胡椒面；每修一类，回归跑全部用例。", bold_prefix="目标：")
    bullet(doc, "建立自动化回归脚本：一条命令跑完全部用例并输出通过率图表。", bold_prefix="动作：")
    bullet(doc, "把用例从 10 条扩到 30 条（覆盖企业最常见的零件类型）。", bold_prefix="动作：")
    para(doc, "本周交付物：通过率曲线（每周对比）+ 回归脚本 + 30 条用例库",
         size=9.5, color=GREY, bold=True)

    heading(doc, "第 3 周：按“工业软件”标准补齐可信度", 3)
    bullet(doc, "加版本号与变更记录；加操作日志（输入、时间、结果、失败原因），但不记录零件敏感内容。", bold_prefix="可追溯：")
    bullet(doc, "加“一键回滚 / 恢复上一条特征树”，保证演示失误可挽回。", bold_prefix="可回滚：")
    bullet(doc, "做免安装绿色包 / 环境自检脚本；在干净机器上完整装一遍。", bold_prefix="可安装：")
    bullet(doc, "开始写国产化/信创适配说明（§4.3），把“已具备/可适配/规划中”分开说清。", bold_prefix="可对外：")
    para(doc, "本周交付物：带版本与日志的安装包 + 干净机器安装录屏 + 一页《定位与国产化路线说明》",
         size=9.5, color=GREY, bold=True)

    heading(doc, "第 4 周：可交付预演 → 交给领导推企业", 3)
    bullet(doc, "按 §3.3 的 5 项准入清单自查，全部通过才算“可交付”。", bold_prefix="自查：")
    bullet(doc, "找 1 名没参与开发的人，按说明书独立完成“安装 → 建 3 个零件 → 导出”，全程只记录卡点、不提示。", bold_prefix="陌生人测试：")
    bullet(doc, "准备 3 分钟演示脚本（固定 5 个零件，从简单到复杂，最后一个必须是企业真实件）。", bold_prefix="演示包：")
    bullet(doc, "交付给领导：安装包 + 一页说明 + 演示录屏 + 三个北极星指标数据 + 已知限制清单。", bold_prefix="交付：")
    para(doc, "本周交付物：《企业实测交付包》+ 指标数据 + 已知问题与限制说明",
         size=9.5, color=GREY, bold=True)

    heading(doc, "3.3 关键里程碑与验收标准", 2)
    make_table(
        doc,
        ["时间", "里程碑", "验收方式（谁看、看什么）"],
        [
            ["第 0 周末", "小队成形、语言统一", "4 人名单齐 + 术语共识文件 + 10 条用例"],
            ["第 1 周末", "CATIA 真机链路打通", "CATIA 主测在真机上当场跑通并签字确认"],
            ["第 2 周末", "30 条用例通过率 ≥ 70%", "自动化脚本输出通过率报告"],
            ["第 3 周末", "具备工业级可信度", "版本/日志/回滚/安装四项演示 + 干净机器安装成功"],
            ["第 4 周末", "可交付预演通过", "陌生人测试零提示成功 + 5 项准入清单全过"],
            ["企业实测后 48h", "反馈闭环完成", "《实测反馈分析》问题分类 + 修复优先级"],
        ],
        widths=[2.6, 4.6, 10.0], font_size=9)

    heading(doc, "3.4 遇到卡点怎么办（3 条最可能卡住的路）", 2)
    make_table(
        doc,
        ["卡点", "判断标准", "替代方案（不阻塞主线）"],
        [
            ["招不到“会 CATIA 又会 vibe coding”的人",
             "48 小时内没有合适人选",
             "拆成两类角色分别招；CATIA 成员可先兼职（每周 3–4 小时），先把验收语言建起来"],
            ["CATIA 真机环境拿不到（授权/版本问题）",
             "第 0 周结束仍无可用真机",
             "先用内置几何内核 + STL 走完整个迭代流程，把 CATIA 通道作为“待验证项”明确标注，不对外宣称已支持"],
            ["企业实测被推迟",
             "第 4 周仍未安排",
             "把陌生人测试结果当成“内部实测”先交付；用 §5 的五问向领导确认时间点与准入标准"],
        ],
        widths=[4.4, 3.4, 8.4], font_size=9)

    # ---------------- 待确认与风险 ----------------
    heading(doc, "第 4 部分  必须马上问清楚的 5 个问题（附参考话术）", 1)
    heading(doc, "4.1 三句话内讲清楚的沟通模板", 2)
    callout(doc, "建议回复话术（可直接改用）",
            "“收到，我按三件事推进：① 今天组建 4 人小队——我带 2 名 AI 编程开发插件，2 名会 CATIA 的同事负责真机验收和反馈方向；"
            "② 本周先打通一条真实 CATIA 链路（一句话 → 特征树 → .CATPart），这是最关键的一步；"
            "③ 4 周内做出可交付预演版，再请您推给企业实测。\n"
            "另外有两个点需要您确认一下方向：开源鸿蒙是用在插件层还是配套层？企业实测大概安排在第几周？我按这个倒排计划。”")

    heading(doc, "4.2 必须确认的 5 个问题", 2)
    make_table(
        doc,
        ["#", "问题", "为什么必须问", "不同答案意味着什么"],
        [
            ["1", "“基于开源鸿蒙”具体指哪一层？①CATIA 插件本身 ②配套采集/看图/审批端 ③未来换国产内核的新版本",
             "决定要不要现在就投人做鸿蒙适配，否则可能白做或被追问穿帮",
             "选①：技术风险极高（CATIA 是 Windows 桌面软件）；选②：可行且性价比最高；选③：先只写路线图"],
            ["2", "企业实测大概安排在第几周？对方是什么类型的企业（主机厂/零部件厂/职校）？",
             "决定“可交付预演”的截止线，以及要准备哪类零件用例",
             "给出明确时间：倒排计划；含糊：先按第 4 周内准备好"],
            ["3", "实测的验收标准是什么？跑通几个零件算合格？谁来判定正确性？",
             "避免“跑完了但没说好”这种模糊结论",
             "有标准：按标准准备用例；无标准：我们主动提交一版验收标准请对方确认"],
            ["4", "企业那边有没有正版 CATIA 与可用机器？我们的程序是否允许联网、是否有涉密要求？",
             "这是能不能进企业现场的前置条件，也是合规底线",
             "无正版/不可联网：必须强化离线方案与绿色安装包"],
            ["5", "小队成员是全职还是兼职？能投入多少小时/周？",
             "决定 4 周目标是激进还是务实",
             "每周 < 6 小时：把 4 周目标降为“真机链路打通 + 10 条用例”"],
        ],
        widths=[0.8, 5.2, 4.4, 6.0], font_size=8.5)

    heading(doc, "4.3 “开源鸿蒙”落地路线（三选一，先定方向再投入）", 2)
    make_table(
        doc,
        ["方案", "放在哪一层", "工作量", "适合场景", "对外表述"],
        [
            ["A. 插件层原生鸿蒙化", "直接把建模/解析跑在 OpenHarmony 设备上",
             "极大（CATIA 本体在 Windows，无法随之迁移）",
             "基本不可行，除非放弃 CATIA 通道，改走自研内核路线",
             "不建议现在承诺"],
            ["B. 配套层鸿蒙化（推荐起点）", "“看图 / 报工 / 审批 / 数据看板”等配套 App 或终端跑在开源鸿蒙上，插件仍驻留 Windows",
             "中等（可独立立项，不阻塞主线）",
             "信创要求场景、车间平板/一体机场景",
             "“插件端已可用；配套端已具备开源鸿蒙适配能力（分阶段落地）”"],
            ["C. 路线图承诺", "只出规划与适配说明，暂不投入开发",
             "极小",
             "当前以企业实测优先，先保交付",
             "“已规划信创/开源鸿蒙适配路线，随企业需求推进”"],
        ],
        widths=[3.2, 5.2, 2.4, 3.4, 4.2], font_size=8.5)

    heading(doc, "4.4 风险登记表（每两周复盘一次）", 2)
    make_table(
        doc,
        ["风险", "等级", "触发信号", "应对动作", "责任人"],
        [
            ["CATIA 通道始终无法真机验证", "高", "第 1 周末仍跑不通", "降级为“待验证”，对外不宣称支持，同步寻求真机/远程协助", "你"],
            ["小队凑不齐或成员投入不足", "高", "第 0 周末 < 3 人", "降低 4 周目标范围，或申请兼职支持", "你"],
            ["企业实测体验差导致口碑受损", "中高", "陌生人测试出现多处卡点", "推迟实测时间，先修准入清单，不为赶时间硬上", "你 + 主测"],
            ["合规/涉密问题", "中高", "企业要求联网或上传图纸", "默认离线、签保密条款、日志不落零件内容", "你"],
            ["对外话术过度承诺", "中", "演示中出现“完全替代人工”等表述", "统一话术：定位辅助加速 + 明确适用零件范围", "全队"],
        ],
        widths=[4.2, 1.6, 3.4, 6.4, 1.8], font_size=8.5)

    heading(doc, "4.5 招募/拉群一句话模板（可直接复制发群）", 2)
    callout(doc, "开发侧（vibe coding）",
            "【招募】AI 建模插件开发 2 名：用 AI 编程方式做 CATIA 插件与文本解析；"
            "要求：会用 AI 编程工具快速产出、能读懂 Python、每周可投入 X 小时；不需要你精通 CATIA（由 CATIA 同学负责验收）。"
            "任务：把“一句中文描述 → CATIA 里出现正确特征树”这条链路做稳。")
    callout(doc, "CATIA 侧",
            "【招募】CATIA 操作 2 名：负责真机验收与优化方向；要求：能独立用 CATIA 建中等复杂零件、能判断特征树是否规范可编辑、每周可投入 X 小时；"
            "不需要你写代码。任务：提供 10 个真实零件样例，逐条验收并写问题单（哪一步、期望值、实际值）。")

    heading(doc, "第 5 部分  附：会议机制与问题单格式", 1)
    heading(doc, "5.1 两个固定会议", 2)
    make_table(
        doc,
        ["会议", "频率/时长", "议程（固定三段）", "产出"],
        [
            ["每日站会", "每天 15 分钟", "① 昨天完成了什么 ② 今天做什么 ③ 卡在哪（需要谁支持）",
             "当天任务卡更新"],
            ["每周验收会", "每周 45 分钟（固定时间）", "① 本周版本现场演示（CATIA 真机） ② 问题单过一遍定优先级 ③ 下周目标确认",
             "验收结论 + 下周任务清单"],
        ],
        widths=[2.4, 3.2, 7.6, 4.0], font_size=9)

    heading(doc, "5.2 问题单必须包含的 7 个字段", 2)
    para(doc, "问题单的作用不是“记录不满”，而是让开发能一次改对。字段不全的问题单一律退回补充。", size=10)
    make_table(
        doc,
        ["字段", "示例", "为什么必须填"],
        [
            ["编号 / 日期", "2025-01-08 #012", "可追溯，方便统计关闭率"],
            ["版本号", "插件 v1.2.0 / 解析器规则 R3", "不写版本的问题无法复现"],
            ["输入描述", "“斜齿轮 模数 2 齿数 30 螺旋角 15 厚 20”", "最小复现条件"],
            ["操作步骤", "1) 打开 CATIA 2) 运行宏 3) 粘贴描述 4) 点生成", "让别人能一步步重放"],
            ["期望结果", "生成含螺旋角的斜齿轮，齿数 30", "判断标准（不能写“应该好看点”）"],
            ["实际结果", "生成了直齿轮，无螺旋角；特征树缺 Helix", "错误现象 + 截图/录屏"],
            ["严重级别", "A 阻断 / B 错误 / C 体验", "决定修复顺序：先 A 后 B，C 排最后"],
        ],
        widths=[3.0, 7.0, 7.2], font_size=9)

    heading(doc, "5.3 每天/每周固定自问的三句话", 2)
    bullet(doc, "今天有没有让“一句话 → 正确模型”的一次通过率变得更高？", bold_prefix="进度自检：")
    bullet(doc, "如果明天就要去企业现场，我最怕哪一步出错？今天就先解决它。", bold_prefix="风险自检：")
    bullet(doc, "我这周交付的东西，能不能给一个没参与项目的人看 3 分钟就明白？", bold_prefix="交付自检：")

    # ---------------- 页脚信息 ----------------
    doc.add_paragraph()
    para(doc, "—— 本文档依据项目现状（内置几何内核 + 中文解析引擎 + MCP 服务端 + CATIA 宏/脚本双通道，"
              "CATIA 通道尚未在真实 CATIA 上验证）与沟通截图整理，用于内部对齐与执行，不对外发布。",
         size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER)

    doc.save(OUT_PATH)
    return OUT_PATH


if __name__ == "__main__":
    path = build()
    print("已生成：%s" % path)
    print("大小：%.1f KB" % (os.path.getsize(path) / 1024.0))
