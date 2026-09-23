# -*- coding: utf-8 -*-
'''
生成《可升级方向：提升 AI 提示词准确性与建模精度》Word 文档。

依据：
  · 公开可核实的文献（Pointer-CAD v2 / SDM / CAD-Llama / ACL 2025 / MBD 等，见 kb/papers_intl.json）
  · 本机可复现实测数据（reports/precision_diagnosis.json、pulley_learning_round*.json、eval_baseline.json）

输出：可升级方向_提升提示词准确性与建模精度.docx（项目根目录）
运行: python tools\\make_upgrade_docx.py
'''
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx import Document                                     # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH                 # noqa: E402
from docx.shared import Cm, Pt                                # noqa: E402
from docx.oxml.ns import qn                                   # noqa: E402

from make_leader_feedback_plan_docx import (                  # noqa: E402
    ACCENT, GREY, CN_FONT, CN_FONT_BODY, EN_FONT,
    set_run_font, para, heading, bullet, make_table, callout,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, '\u53ef\u5347\u7ea7\u65b9\u5411_\u63d0\u5347\u63d0\u793a\u8bcd\u51c6\u786e\u6027\u4e0e\u5efa\u6a21\u7cbe\u5ea6.docx')
REPORTS = os.path.join(ROOT, 'reports')
KB = os.path.join(ROOT, 'kb')
Q1, Q2 = '\u201c', '\u201d'
ARROW = '\u2192'
DASH = '\u2014'


def load(path, default=None):
    try:
        with io.open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def code_block(doc, lines):
    t = doc.add_table(rows=1, cols=1)
    cell = t.rows[0].cells[0]
    cell.text = ''
    first = True
    for ln in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.12
        set_run_font(p.add_run(ln if ln else ' '), size=8.5, cn='Consolas', en='Consolas')
    tcpr = cell._tc.get_or_add_tcPr()
    shd = tcpr.makeelement(qn('w:shd'), {})
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), 'F4F6F8')
    tcpr.append(shd)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build():
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(1.9)
    sec.bottom_margin = Cm(1.9)
    sec.left_margin = Cm(2.1)
    sec.right_margin = Cm(1.9)
    normal = doc.styles['Normal']
    normal.font.size = Pt(10.5)
    normal.font.name = EN_FONT
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), CN_FONT_BODY)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.35

    dx = load(os.path.join(REPORTS, 'precision_diagnosis.json'), {}) or {}
    ev = load(os.path.join(REPORTS, 'eval_baseline.json'), {}) or {}
    rounds = [load(os.path.join(REPORTS, 'pulley_learning_round%d.json' % i))
              for i in (1, 2, 3)]
    rounds = [r for r in rounds if r]
    papers = (load(os.path.join(KB, 'papers_intl.json'), {}) or {}).get('papers') or []

    # ---------------- 封面 ----------------
    para(doc, '\u53ef\u5347\u7ea7\u65b9\u5411\uff1a\u63d0\u5347 AI \u63d0\u793a\u8bcd\u51c6\u786e\u6027\u4e0e\u5efa\u6a21\u7cbe\u5ea6',
         size=20, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER,
         space_before=4, space_after=4, cn=CN_FONT)
    para(doc, DASH + ' \u57fa\u4e8e\u516c\u5f00\u5b66\u672f\u6587\u732e\u65b9\u6cd5\u4e0e\u672c\u673a\u53ef\u590d\u73b0\u5b9e\u6d4b\u6570\u636e\u7684\u6280\u672f\u8bf4\u660e\u4e66',
         size=10.5, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, '\u9879\u76ee\uff1aAI \u81ea\u52a8\u5efa\u6a21\u52a9\u624b\uff08\u5f53\u524d v1.3.0\uff09\u3000\uff5c\u3000\u751f\u6210\u65f6\u95f4\uff1a%s'
         % datetime.now().strftime('%Y-%m-%d %H:%M'),
         size=9.5, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)

    callout(doc, '\u7ed3\u8bba\u5148\u884c',
            '\u5b9e\u6d4b\u8bc1\u660e\u51e0\u4f55\u5185\u6838\u4e0d\u662f\u74f6\u9888\uff08\u53c2\u6570\u5c42 16/16\u3001\u51e0\u4f55\u5c42 16/16\u3001\u786c\u7ea6\u675f 14/14 \u5168\u7eff\uff09\uff0c'
            '\u74f6\u9888\u5168\u5728' + Q1 + '\u63d0\u793a\u8bcd ' + ARROW + ' \u53c2\u6570' + Q2 + '\u8fd9\u4e00\u5c42\u3002\n'
            '\u6240\u4ee5\u5347\u7ea7\u4e0d\u662f\u91cd\u5199\u51e0\u4f55\uff0c\u800c\u662f\u7167\u6587\u732e\u91cc\u7684\u4e09\u4ef6\u4e8b\u6539\u9020\u89e3\u6790\u5c42\uff1a\n'
            '\u2460 \u4e24\u6bb5\u5f0f\uff1a\u5148\u89c4\u5212' + Q1 + '\u5efa\u4ec0\u4e48\u7279\u5f81' + Q2
            + '\uff0c\u518d\u5355\u72ec\u6ce8\u5165\u5c3a\u5bf8\uff08Plan-Then-Construct\uff09\uff1b\n'
            '\u2461 \u53c2\u6570\u5f15\u7528\u8868\uff1a\u5c3a\u5bf8\u53ea\u5728\u62bd\u53d6\u9636\u6bb5\u8bfb\u4e00\u6b21\uff0c'
            '\u6784\u5efa\u671f\u53ea\u67e5\u8868\u3001\u4e0d\u518d\u89e3\u6790\u6587\u672c\uff1b\n'
            '\u2462 \u516c\u5dee\u7ea7\u81ea\u68c0\uff1a\u6309\u9876\u70b9/\u8fb9/\u9762\u4e09\u7ea7\u5728\u516c\u5dee\u5e26\u5185\u6821\u9a8c\uff0c'
            '\u8d85\u5dee\u5c31\u62a5\u9519\u56de\u9000\uff0c\u7edd\u4e0d\u9759\u9ed8\u51fa\u9519\u503c\u3002')

    # ---------------- 一、文献依据 ----------------
    heading(doc, '\u4e00\u3001\u65b9\u6cd5\u5b66\u4f9d\u636e\uff08\u516c\u5f00\u53ef\u6838\u5b9e\u7684\u6587\u732e\uff09', 1)
    para(doc, '\u77e5\u7f51\u4e3b\u68c0\u7d22\uff08kns.cnki.net\uff09\u65e0\u5934\u8bbf\u95ee\u8fd4\u56de' + Q1
              + '\u5b89\u5168\u9a8c\u8bc1' + Q2
              + '\u9875\uff0c\u5176\u516c\u5f00\u68c0\u7d22\u9875\u7684\u7ed3\u679c\u7531 JS \u5b9e\u65f6\u6e32\u67d3\uff0c'
              '\u811a\u672c\u53d6\u4e0d\u5230\u9898\u5f55\uff1b\u56e0\u6b64\u672c\u6587\u4ef6\u4e0d\u542b\u4f2a\u9020\u7684\u77e5\u7f51\u9898\u5f55\uff0c'
              '\u65b9\u6cd5\u4f9d\u636e\u5168\u90e8\u6765\u81ea\u80fd\u516c\u5f00\u6253\u5f00\u5e76\u6838\u5b9e\u7684\u6765\u6e90'
              '\uff08\u542b\u4e0e\u77e5\u7f51\u8bba\u6587\u540c\u6e90\u7684\u4e2d\u6587\u671f\u520a PDF\uff09\u3002'
              '\u77e5\u7f51\u8bf7\u6309 1.2 \u7684\u68c0\u7d22\u5f0f\u5728\u5df2\u767b\u5f55\u6d4f\u89c8\u5668\u91cc\u6838\u5bf9\u3002',
         size=10, color=GREY)

    heading(doc, '1.1 \u5df2\u6838\u5b9e\u7684\u5173\u952e\u6587\u732e', 2)
    rows = []
    for p in papers[:10]:
        rows.append([p.get('title', '')[:58], p.get('venue', ''), (p.get('why', '') or '')[:88]])
    make_table(doc, ['\u6587\u732e', '\u6765\u6e90', '\u672c\u5347\u7ea7\u8981\u7528\u7684\u65b9\u6cd5\u70b9'],
               rows, widths=[5.6, 2.8, 8.2], font_size=8)

    heading(doc, '1.2 \u77e5\u7f51\u53ef\u76f4\u63a5\u4f7f\u7528\u7684\u68c0\u7d22\u5f0f', 2)
    code_block(doc, [
        '\u4e3b\u9898\uff1a(\u5927\u8bed\u8a00\u6a21\u578b + CAD) \u6216 (\u81ea\u7136\u8bed\u8a00 + \u53c2\u6570\u5316\u5efa\u6a21) \u6216 (\u8bed\u4e49 + \u4e09\u7ef4\u6a21\u578b\u751f\u6210)',
        '\u4e3b\u9898\uff1a(\u6c7d\u8f66\u96f6\u90e8\u4ef6 + \u53c2\u6570\u5316\u5efa\u6a21) \u6216 (\u5c3a\u5bf8\u5de5\u7a0b + \u865a\u62df\u5339\u914d) \u6216 (\u516c\u5dee + \u5efa\u6a21\u7cbe\u5ea6)',
        '\u4e3b\u9898\uff1a(\u52a0\u5de5\u7279\u5f81\u8bc6\u522b + \u8bbe\u8ba1\u610f\u56fe) \u6216 (\u7279\u5f81\u63d0\u53d6 + \u6a21\u578b\u91cd\u5efa)',
        '\u4e3b\u9898\uff1a(\u6807\u51c6\u4ef6 + \u53c2\u6570\u5e93) \u6216 (\u673a\u68b0\u8bbe\u8ba1\u624b\u518c + \u6570\u5b57\u5316)',
    ])

    heading(doc, '1.3 \u6587\u732e\u7ed9\u51fa\u7684\u4e09\u6761\u786c\u7ed3\u8bba', 2)
    bullet(doc, Q1 + '\u89c6\u89c9\u76f8\u4f3c' + Q2
                + '\u4e0d\u7b49\u4e8e\u5de5\u4e1a\u7cbe\u5ea6\uff1a\u5c0f\u6570\u503c\u504f\u5dee\u5728\u5f62\u72b6\u5c42\u9762\u770b\u4e0d\u51fa\u6765\uff0c'
                  '\u5374\u76f4\u63a5\u8fdd\u53cd\u5de5\u4e1a\u516c\u5dee\u8981\u6c42\u3002', bold_prefix='\u2460 \u7cbe\u5ea6\u5b9a\u4e49\uff1a')
    bullet(doc, '\u628a\u6570\u503c\u91cf\u5316\uff08\u79bb\u6563\u5316\uff09\u8ba9 LLM \u66f4\u597d\u9884\u6d4b\uff0c'
                '\u4f46\u5f15\u5165\u4e0d\u53ef\u63a5\u53d7\u7684\u5c3a\u5bf8\u8bef\u5dee\uff1b'
                '\u6b63\u786e\u505a\u6cd5\u662f\u76f4\u63a5\u9884\u6d4b\u8fde\u7eed\u503c\uff0c'
                '\u5e76\u7528\u6307\u9488/\u5f15\u7528\u4ece\u53c2\u6570\u8868\u53d6\u503c\u3002', bold_prefix='\u2461 \u91cf\u5316\u6709\u5bb3\uff1a')
    bullet(doc, '\u628a\u53c2\u6570\u63a8\u7406\u4e0e\u51e0\u4f55\u6784\u9020\u89e3\u8026\uff08\u5148\u51fa\u8bbe\u8ba1\u8ba1\u5212\uff0c'
                '\u518d\u6ce8\u5165\u91cf\u7eb2\u53c2\u6570\uff09\uff0c'
                '\u5e76\u6309\u9876\u70b9/\u8fb9/\u9762\u4e09\u7ea7\u5728\u516c\u5dee\u5185\u6821\u9a8c\u3002',
           bold_prefix='\u2462 \u89e3\u8026\u4e0e\u6821\u9a8c\uff1a')

    # ---------------- 二、实测基线 ----------------
    heading(doc, '\u4e8c\u3001\u672c\u673a\u5b9e\u6d4b\u57fa\u7ebf\uff08\u5347\u7ea7\u524d\u5fc5\u987b\u7559\u6863\uff09', 1)
    l2 = dx.get('l2') or {}
    geo = dx.get('geometry') or {}
    inv = dx.get('invariants') or {}
    make_table(
        doc, ['\u6307\u6807', '\u6570\u503c', '\u590d\u8dd1\u547d\u4ee4'],
        [
            ['\u53c2\u6570\u5c42\uff08\u63d0\u793a\u8bcd\u6570\u503c\u843d\u5728\u6b63\u786e\u8bed\u4e49\u4f4d\u7f6e\uff09',
             '%s/%s = %s%%' % (l2.get('passed', '-'), l2.get('total', '-'), l2.get('accuracy', '-')),
             'python tools\\diagnose_precision.py'],
            ['\u51e0\u4f55\u5c42\uff08\u6210\u529f\u6784\u5efa\u4e14\u975e\u9000\u5316\uff09',
             '%s/%s = %s%%' % (geo.get('valid', '-'), geo.get('total', '-'), geo.get('accuracy', '-')),
             'python tools\\diagnose_precision.py'],
            ['\u51e0\u4f55\u4e0d\u53d8\u91cf\uff08\u6807\u51c6\u516c\u5f0f/\u89e3\u6790\u89e3\uff09',
             '%s/%s' % (inv.get('passed', '-'), inv.get('total', '-')),
             'python tools\\diagnose_precision.py'],
            ['\u5e26\u8f6e\u63d0\u793a\u8bcd\u591a\u8f6e\u5b66\u4e60',
             ' -> '.join('%d/%d' % (r['passed'], r['total']) for r in rounds) or '-',
             'python tools\\learn_pulley_prompts.py --rounds 3'],
            ['\u5168\u96f6\u4ef6\u547d\u4e2d\u7387',
             '%.2f%%\uff08%s/%s\uff0c%d \u7c7b\uff09' % (ev.get('hit_rate', 0), ev.get('hits', '-'),
                                                       ev.get('n', '-'),
                                                       len(ev.get('by_category') or {})),
             'python tools\\evaluate.py'],
            ['\u5185\u7f6e\u529f\u80fd\u81ea\u68c0', '34/34', 'python -m ai_modeler --selftest'],
        ], widths=[5.4, 5.6, 5.6], font_size=8.5)

    heading(doc, '2.1 \u8bca\u65ad\u7ed3\u8bba', 2)
    bullet(doc, '\u51e0\u4f55\u5185\u6838\u53ef\u9760\uff1a\u51e0\u4f55\u5c42\u4e0e\u4e0d\u53d8\u91cf\u5168\u7eff ' + DASH
                + ' \u5347\u7ea7\u4e0d\u8981\u52a8\u5185\u6838\uff0c\u52a8\u4e86\u53ea\u4f1a\u5f15\u5165\u65b0\u98ce\u9669\u3002',
           bold_prefix='\u2460 ')
    bullet(doc, '\u74f6\u9888\u5728\u89e3\u6790\u5c42\uff1a\u672c\u8f6e\u4fee\u6389\u7684 5 \u4e2a bug \u5168\u5c5e\u8be5\u5c42'
                '\uff08\u5e26\u8f6e\u53c2\u6570\u8bfb\u4e0d\u5168\u5bfc\u81f4\u6ca1\u69fd\u3001\u77ed\u522b\u540d\u62a2\u5360\u3001'
                '\u8fde\u5199\u6570\u5b57\u9ecf\u8fde\u3001\u96f6\u4ef6\u540d\u91cc\u7684\u69fd\u88ab\u5224\u6210\u5207\u9664\u3001'
                '\u6807\u51c6\u7cfb\u5217\u8868\u53ea\u62bd\u5230 10 \u6863\uff09\u3002', bold_prefix='\u2461 ')
    bullet(doc, '\u5931\u8d25\u662f\u7ea7\u8054\u7684\uff1a\u4e00\u4e2a\u5b57\u7b26\u6ca1\u8ba4\u51fa\u6765\uff0c'
                '\u6574\u6761\u96f6\u4ef6\u8bc6\u522b\u5931\u8d25\u5e76\u9759\u9ed8\u9000\u5316\u6210\u522b\u7684\u5f62\u72b6 ' + DASH
                + ' \u8fd9\u6b63\u662f\u53c2\u6570\u63a8\u7406\u4e0e\u51e0\u4f55\u6784\u9020\u8026\u5408\u7684\u75c7\u72b6\u3002',
           bold_prefix='\u2462 ')
    bullet(doc, '\u6ca1\u6709\u516c\u5dee\u6982\u5ff5\uff1a\u76ee\u524d\u53ea\u6709\u5305\u56f4\u76d2\u5dee 2%\uff0c'
                '\u6ca1\u6709\u9876\u70b9/\u8fb9/\u9762\u7ea7\u516c\u5dee\u6821\u9a8c\u3002', bold_prefix='\u2463 ')

    # ---------------- 三、方向总览 ----------------
    heading(doc, '\u4e09\u3001\u5347\u7ea7\u65b9\u5411\u603b\u89c8\uff08\u6309\u6027\u4ef7\u6bd4\u6392\u5e8f\uff09', 1)
    make_table(
        doc, ['\u7f16\u53f7', '\u65b9\u5411', '\u6587\u732e\u4f9d\u636e', '\u9884\u671f\u6536\u76ca\uff08\u53ef\u91cf\u5316\uff09', '\u4f18\u5148\u7ea7'],
        [
            ['U1', '\u4e24\u6bb5\u5f0f\u89e3\u6790\uff1a\u5148\u89c4\u5212\u7279\u5f81\u3001\u518d\u6ce8\u5165\u5c3a\u5bf8',
             'Pointer-CAD v2', '\u53c2\u6570\u5c42\u51c6\u786e\u7387 -> \u226595%\uff08500 \u9898\uff09\uff1b'
                               '\u9759\u9ed8\u9519\u503c -> 0', 'P0'],
            ['U2', '\u53c2\u6570\u5f15\u7528\u8868 + \u6307\u9488\u53d6\u503c\uff08\u6784\u5efa\u671f\u4e0d\u518d\u89e3\u6790\u6587\u672c\uff09',
             'Pointer-CAD v2 \u6307\u9488\u673a\u5236', '\u8fde\u5199/\u522b\u540d\u62a2\u5360\u7c7b bug -> 0', 'P0'],
            ['U3', '\u516c\u5dee\u7ea7\u81ea\u68c0\u95ed\u73af\uff08\u9876\u70b9/\u8fb9/\u9762\u4e09\u7ea7\uff09',
             'Pointer-CAD v2 \u8bc4\u6d4b\u534f\u8bae\uff1bMBD',
             '\u6784\u5efa\u6210\u529f\u4f46\u5c3a\u5bf8\u9519 -> 100% \u88ab\u62e6\u4e0b', 'P0'],
            ['U4', '\u610f\u56fe\u5230\u7279\u5f81 \u8bed\u4e49\u5c42 + \u4ea4\u4e92\u5f0f\u7ea0\u9519',
             'Semantic Direct Modeling\uff1bCIRP \u7279\u5f81\u8bc6\u522b',
             '\u7528\u6237\u53ef\u9010\u6761\u7ea0\u6b63\uff0c\u7ea0\u6b63\u53ef\u590d\u7528', 'P1'],
            ['U5', '\u6807\u51c6/\u7cfb\u5217\u6570\u636e\u5168\u91cf\u5165\u5e93 + \u7f3a\u6863\u5916\u63a8', '\u624b\u518c\u6570\u5b57\u5316',
             '\u6807\u51c6\u4ef6\u53d6\u9519\u6863 -> 0\uff1b\u7f3a\u6863\u7ed9\u51fa\u63a8\u5bfc\u503c\u4e0e\u5019\u9009\u6863\u4f4d', 'P1'],
            ['U6', '\u7ea6\u675f\u6c42\u89e3\uff08\u8349\u56fe\u7ea6\u675f + \u88c5\u914d\u7ea6\u675f\uff09',
             'CAD-Llama\uff1bSDM \u7ea6\u675f\u5c42',
             '\u590d\u6742\u4ef6\u51e0\u4f55\u4e00\u81f4\u6027\u53ef\u8bc1\u660e', 'P2'],
            ['U7', '\u8bc4\u6d4b\u57fa\u51c6 V2\uff08\u5e8f\u5217/\u53c2\u6570/\u51e0\u4f55/\u4e00\u81f4\u6027\uff09',
             'Pointer-CAD v2\uff1bACL 2025', '\u6bcf\u6b21\u6539\u52a8\u90fd\u6709\u56db\u9879\u53ef\u6bd4\u6570\u5b57', 'P1'],
            ['U8', '\u5019\u9009\u751f\u6210 + \u53ef\u884c\u6027\u6821\u9a8c\uff08\u4ee3\u7406\u6a21\u578b\u9009\u4f18\uff09',
             '\u4e24\u6bb5\u5f0f\u68c0\u7d22-\u751f\u6210\uff1bMDO Agent',
             '\u89c4\u683c\u4e0d\u5168\u65f6\u7ed9 2-3 \u4e2a\u5408\u89c4\u5019\u9009', 'P2'],
            ['U9', 'LLM \u53ea\u505a\u610f\u56fe\u7406\u89e3 + \u7ed3\u679c\u89e3\u91ca', 'SDM\uff1bMDO Agent',
             '\u53e3\u8bed\u9c81\u68d2\u6027\u63d0\u5347\uff0c\u4e0d\u5f15\u5165\u6570\u503c\u4e0d\u786e\u5b9a\u6027', 'P1'],
        ], widths=[1.2, 4.6, 3.6, 5.2, 1.4], font_size=8)

    # ---------------- 四、P0 详细设计 ----------------
    heading(doc, '\u56db\u3001P0 \u8be6\u7ec6\u8bbe\u8ba1\uff08\u53ef\u7167\u7740\u6539\u4ee3\u7801\uff09', 1)

    heading(doc, 'U1 \u4e24\u6bb5\u5f0f\u89e3\u6790\uff1a\u5148\u89c4\u5212\u3001\u518d\u6ce8\u5165\u5c3a\u5bf8', 2)
    para(doc, '\u95ee\u9898\uff1a\u73b0\u5728 handle() \u4e00\u8fb9\u8ba4\u8bcd\u3001\u4e00\u8fb9\u89e3\u6790\u5c3a\u5bf8\u3001'
              '\u4e00\u8fb9\u5efa\u7279\u5f81\uff0c\u4e09\u8005\u8026\u5408\uff1b'
              '\u4efb\u4e00\u73af\u8282\u8bef\u5224\u90fd\u4f1a\u6c61\u67d3\u540e\u7eed\uff08\u4f8b\u5982\u628a\u69fd\u65703 \u8bfb\u6210\u5207\u9664\u8981\u6c42\uff09\u3002',
         size=10)
    para(doc, '\u6539\u6cd5\uff1a\u628a\u89e3\u6790\u62c6\u6210\u4e09\u4e2a\u9636\u6bb5\u3002', size=10, bold=True)
    code_block(doc, [
        '\u9636\u6bb5 A  \u89c4\u5212\uff08\u53ea\u51b3\u5b9a\u5efa\u4ec0\u4e48\u7279\u5f81\u3001\u6309\u4ec0\u4e48\u987a\u5e8f\uff0c\u4e0d\u51fa\u73b0\u4efb\u4f55\u6570\u5b57\uff09',
        '        DesignPlan = [',
        '          {"id":"f1","op":"ADD","type":"revolve_body","role":"wheel_body"},',
        '          {"id":"f2","op":"CUT","type":"groove","role":"v_groove",',
        '           "expr":"standard:GB10412:groove","count":"@n","pitch":"@e"},',
        '          {"id":"f3","op":"CUT","type":"hole","role":"center_bore","dia":"@bore_d"},',
        '        ]',
        '\u9636\u6bb5 B  \u53c2\u6570\u6ce8\u5165\uff08\u6570\u503c\u53ea\u6765\u81ea\u53c2\u6570\u8868\uff0c\u4e0d\u91cd\u65b0\u89e3\u6790\u6587\u672c\uff09',
        '\u9636\u6bb5 C  \u6784\u5efa + \u516c\u5dee\u6821\u9a8c\uff08\u89c1 U3\uff0c\u8d85\u5dee\u5373\u62a5\u9519\u56de\u9000\uff09',
    ])
    bullet(doc, '\u9636\u6bb5 A \u7981\u6b62\u6570\u5b57\u5b57\u9762\u91cf\uff1a\u6240\u6709\u6570\u503c\u5fc5\u987b\u662f @\u5f15\u7528 ' + DASH
                + ' \u8fd9\u6837\u6570\u503c\u8bfb\u9519\u4e0e\u7ed3\u6784\u5224\u9519\u88ab\u5f7b\u5e95\u5206\u5f00\u3002',
           bold_prefix='\u89c4\u5219 1\uff1a')
    bullet(doc, '\u9636\u6bb5 B \u53ea\u8ba4\u53c2\u6570\u8868\uff1b\u5f15\u7528\u7f3a\u5931\u65f6\u4e0d\u731c\uff0c'
                '\u8d70\u5019\u9009\u6d41\u7a0b\u6216\u76f4\u63a5\u5411\u7528\u6237\u63d0\u95ee\u3002', bold_prefix='\u89c4\u5219 2\uff1a')
    bullet(doc, '\u51e1\u88ab\u5957\u5178\u578b\u503c\u7684\u5f15\u7528\u5fc5\u987b\u6807 provenance=default\uff0c'
                '\u754c\u9762\u4e0a\u7528\u4e0d\u540c\u989c\u8272\u663e\u793a\u3002', bold_prefix='\u89c4\u5219 3\uff1a')
    bullet(doc, '500 \u9898\u4eba\u5de5\u9898\u5e93\u4e0a\u53c2\u6570\u5c42\u51c6\u786e\u7387 \u226595%\uff0c'
                '\u4e14\u9759\u9ed8\u9519\u503c = 0\u3002', bold_prefix='\u9a8c\u6536\uff1a')

    heading(doc, 'U2 \u53c2\u6570\u5f15\u7528\u8868 + \u6307\u9488\u53d6\u503c', 2)
    para(doc, '\u95ee\u9898\uff1a\u6570\u503c\u5728 nlparser \u4e0e partlib \u4e24\u5904\u5404\u89e3\u6790\u4e00\u904d\u3001'
              '\u4e92\u76f8\u6c61\u67d3\uff08\u5b9e\u6d4b\u300c\u5e26\u8f6e d25 D100 B30 n3 A\u578b\u300d\u91cc n3 \u88ab B30 \u5e72\u6270\uff09\u3002',
         size=10)
    code_block(doc, [
        '# ParamTable\uff1a\u952e -> {value, unit, span, provenance}',
        '#   provenance \u5c5e\u4e8e {explicit, standard, derived, default, asked}',
        '{"bore_d":  {"value":25.0,  "span":[8,11],  "provenance":"explicit"},',
        ' "pitch_d": {"value":100.0, "span":[11,17], "provenance":"explicit"},',
        ' "n":       {"value":3,     "span":[24,27], "provenance":"explicit"},',
        ' "gt":      {"value":"A",   "span":[28,30], "provenance":"explicit"},',
        ' "outer_d": {"value":105.5, "source":"GB/T 10412 \u7cfb\u5217\u8868","provenance":"standard"},',
        ' "width":   {"value":39.32, "source":"\u8f6e\u7f18\u6700\u5c0f\u5bbd\u5ea6\u516c\u5f0f","provenance":"derived"}}',
    ])
    para(doc, '\u53d6\u503c\u4f18\u5148\u7ea7\uff08\u547d\u4e2d\u5373\u6b62\uff0c\u4e14\u547d\u4e2d\u540e\u628a\u8be5\u6bb5\u6587\u672c\u4ece\u5de5\u4f5c\u4e32\u91cc\u6316\u6389\uff09\uff1a',
         size=10, bold=True)
    make_table(
        doc, ['\u987a\u5e8f', '\u53d6\u503c\u65b9\u5f0f', '\u89e3\u51b3\u7684\u5b9e\u6d4b bug'],
        [
            ['1', '\u7eaf\u7b26\u53f7\u533a d25 / D100 / n3\uff08\u8981\u6c42\u72ec\u7acb\u6210\u8bcd\uff09',
             'n3 \u88ab B30 \u5e72\u6270'],
            ['2', '\u4e2d\u6587\u522b\u540d\u533a \u5185\u5f8425 / \u8f6e\u5bbd40\uff08\u547d\u4e2d\u540e\u6316\u6389\u8be5\u6bb5\uff09',
             '\u5bbd\u5ea6303\u4e2a\u69fd \u8bfb\u6210 303'],
            ['3', '\u9ecf\u8fde\u62c6\u5206\uff1a\u6570\u5b57\u4e32\u540e\u7d27\u8ddf\u91cf\u8bcd\u65f6\u6309\u6700\u957f\u5408\u6cd5\u524d\u7f00\u5207',
             '\u5bbd\u5ea640 \u52a0 2\u4e2a\u69fd'],
            ['4', '\u6807\u51c6\u89e3\u6790\uff1aA\u578b \u67e5\u6807\u51c6\u8868\u8865 bd/ha/hf/e/\u69fd\u89d2',
             '\u69fd\u578b\u8bfb\u4e0d\u51fa\u5bfc\u81f4\u6574\u6761\u9000\u5316'],
            ['5', '\u63a8\u5bfc\uff1a\u5916\u5f84 = dd + 2*ha\u3001\u8f6e\u7f18\u5bbd = (n-1)e + 2f',
             '\u5916\u5f84\u88ab\u5f53\u6210\u8f93\u5165\u503c'],
            ['6', '\u9ed8\u8ba4\u503c\uff1a\u4ec5\u9650\u53ef\u9009\u53c2\u6570\uff0c\u4e14\u5fc5\u987b\u6807\u8bb0\u5e76\u663e\u793a',
             '\u9759\u9ed8\u5957\u5178\u578b\u503c\u6539\u53d8\u7528\u6237\u610f\u56fe'],
        ], widths=[1.4, 8.6, 6.0], font_size=8.5)

    heading(doc, 'U3 \u516c\u5dee\u7ea7\u81ea\u68c0\u95ed\u73af\uff08\u9876\u70b9 / \u8fb9 / \u9762\u4e09\u7ea7\uff09', 2)
    para(doc, '\u95ee\u9898\uff1a\u73b0\u5728\u53ea\u6709\u5305\u56f4\u76d2\u5dee 2%\u3002'
              '\u6784\u5efa\u6210\u529f\u4f46\u67d0\u4e2a\u69fd\u6df1\u5dee 0.3 mm\uff0c\u7a0b\u5e8f\u5b8c\u5168\u65e0\u611f\u3002', size=10)
    code_block(doc, [
        '@dataclass',
        'class DimSpec:            # \u4e00\u4e2a\u53d7\u63a7\u5c3a\u5bf8',
        '    name: str             # \u4f8b\uff1agroove_pitch',
        '    value: float          # \u671f\u671b\u503c',
        '    tol: float            # \u516c\u5dee\u5e26\uff08mm\uff09\uff0cGB/T 1804 m \u7ea7\u8d77\u6b65',
        '    grade: str            # vertex | edge | face',
        '    how: str              # \u6d4b\u91cf\u65b9\u5f0f\uff1abbox_axis / circle_dia / groove_pitch',
        '',
        'def verify_tolerance(polys, table):',
        '    rep = Report()',
        '    for spec in table.dim_specs:',
        '        got = MEASURE[spec.how](polys)      # \u4ece\u51e0\u4f55\u771f\u7684\u91cf\u51fa\u6765',
        '        rep.add(spec.name, want=spec.value, got=got,',
        '                ok=abs(got - spec.value) <= spec.tol)',
        "    rep.add('watertight',     ok=is_closed(polys))",
        "    rep.add('min_wall',       ok=min_wall_thickness(polys) >= table.min_wall)",
        "    rep.add('self_intersect', ok=not has_self_intersection(polys))",
        '    return rep',
    ])
    make_table(
        doc, ['\u6d4b\u91cf\u7b97\u5b50', '\u7528\u9014', '\u5b9e\u73b0\u8981\u70b9'],
        [
            ['bbox_axis', '\u603b\u957f/\u603b\u5bbd/\u603b\u9ad8', '\u5305\u56f4\u76d2\uff0cO(n) \u9876\u70b9\u626b\u63cf'],
            ['circle_dia', '\u5b54\u5f84/\u5916\u5f84', '\u6307\u5b9a Z \u9ad8\u5ea6\u53d6\u622a\u9762\uff0c\u6700\u5c0f\u4e8c\u4e58\u62df\u5408\u5706'],
            ['groove_pitch', '\u5e26\u8f6e\u69fd\u95f4\u8ddd', '\u6cbf\u8f74\u5411\u626b\u63cf\u534a\u5f84\u6781\u5c0f\u503c\u4f4d\u7f6e\uff0c\u53d6\u76f8\u90bb\u95f4\u8ddd'],
            ['tooth_count', '\u9f7f\u6570', '\u8282\u5706\u622a\u9762\u7edf\u8ba1\u534a\u5f84\u8df3\u53d8\u6b21\u6570'],
            ['coaxial', '\u540c\u8f74\u5ea6', '\u4e24\u56de\u8f6c\u4f53\u8f74\u7ebf\u504f\u5dee\uff08\u9876\u70b9\u96c6\u5408\u8d28\u5fc3\u6295\u5f71\uff09'],
            ['wall_thickness', '\u6700\u5c0f\u58c1\u539a', '\u5c04\u7ebf\u53cc\u5411\u8ddd\u79bb\u8fd1\u4f3c'],
        ], widths=[3.2, 4.0, 9.0], font_size=8.5)
    bullet(doc, '\u6545\u610f\u6ce8\u5165 5 \u7c7b\u8d85\u5dee\uff08\u5b54\u5f84\u504f 0.2\u3001\u69fd\u8ddd\u504f 0.5\u3001'
                '\u58c1\u539a\u504f 0.3 \u7b49\uff09\uff0c\u5fc5\u987b 100% \u88ab\u62e6\u4e0b\uff0c'
                '\u5e76\u7ed9\u51fa\u54ea\u4e2a\u5c3a\u5bf8\u3001\u671f\u671b\u591a\u5c11\u3001\u5b9e\u6d4b\u591a\u5c11\u3001\u504f\u5dee\u591a\u5c11\u3002',
           bold_prefix='\u9a8c\u6536\uff1a')

    # ---------------- 五、P1/P2 ----------------
    heading(doc, '\u4e94\u3001P1 / P2 \u65b9\u5411\u8981\u70b9', 1)
    make_table(
        doc, ['\u65b9\u5411', '\u8981\u70b9', '\u9a8c\u6536'],
        [
            ['U4 \u610f\u56fe\u5230\u7279\u5f81 \u8bed\u4e49\u5c42',
             '\u628a\u5df2\u6709\u7684\u63d0\u793a\u8bcd\u5230\u7279\u5f81\u5bf9\u7167\u8868\u505a\u6210\u53ef\u4ea4\u4e92\u9762\u677f\uff1a'
             '\u6bcf\u6761\u5e26\u539f\u6587\u4f4d\u7f6e span\uff0c\u53ef\u70b9\u51fb\u56de\u539f\u6587\u3001'
             '\u53ef\u6539\u503c/\u6362\u69fd\u578b/\u91cd\u65b0\u89e3\u91ca\uff1b'
             '\u7ea0\u6b63\u7ed3\u679c\u5199\u5165 kb/intent_corrections.json\uff0c\u4e0b\u6b21\u76f4\u63a5\u547d\u4e2d',
             '3 \u6b21\u4ee5\u5185\u7684\u7ea0\u6b63\uff0c\u91cd\u590d\u51fa\u73b0\u65f6\u4e0d\u518d\u9700\u8981\u7ea0\u6b63'],
            ['U5 \u6807\u51c6\u6570\u636e\u5168\u91cf\u5165\u5e93',
             '\u6bcf\u7c7b\u6807\u51c6\u4ef6\u90fd\u6709 kb/*_std.json\uff08\u542b source/tol/\u81ea\u68c0\uff09\uff1b'
             '\u547d\u4e2d\u7cfb\u5217\u8868\u7528\u8868\u503c\uff0c\u672a\u547d\u4e2d\u5219\u6309\u516c\u5f0f\u63a8\u5bfc\u5e76\u63d0\u793a\uff0c'
             '\u540c\u65f6\u7ed9\u51fa\u6700\u63a5\u8fd1\u7684 3 \u4e2a\u6807\u51c6\u6863\u4f4d\u4f9b\u5207\u6362',
             '\u22654 \u7c7b\u6807\u51c6\u4ef6\u5177\u5907 std \u6570\u636e\u4e14\u811a\u672c\u53ef\u590d\u8dd1'],
            ['U7 \u8bc4\u6d4b\u57fa\u51c6 V2',
             '\u5e8f\u5217\u7ea7 F1\u3001\u53c2\u6570\u7ea7\u516c\u5dee\u5185\u51c6\u786e\u7387\u3001'
             '\u51e0\u4f55\u7ea7\u6709\u6548\u7387/\u8d85\u5dee\u7387\u3001\u4e00\u81f4\u6027\uff08\u9759\u9ed8\u9519\u503c\u7387\uff09\u56db\u9879\u6307\u6807\uff1b'
             '\u9898\u5e93 114 + 161 + \u8ffd\u52a0 300 \u9898',
             '\u5e8f\u5217 F1 \u22650.95\u3001\u53c2\u6570 \u226595%\u3001\u8d85\u5dee \u22642%\u3001\u9759\u9ed8\u9519\u503c 0%'],
            ['U9 LLM \u8fb9\u754c',
             'LLM \u53ea\u505a\u610f\u56fe\u7406\u89e3\u4e0e\u7ed3\u679c\u89e3\u91ca\uff0c\u7edd\u4e0d\u505a\u7b97\u672f\uff1b'
             '\u8fd4\u56de\u5fc5\u987b\u662f\u7ed3\u6784\u5316 plan\uff08\u53ea\u6709\u7279\u5f81\u540d\u4e0e\u5f15\u7528\u952e\uff0c\u6ca1\u6709\u6570\u5b57\uff09',
             'LLM \u8f93\u51fa\u4e2d\u4e0d\u542b\u4efb\u4f55\u6570\u503c\u5b57\u9762\u91cf\uff08\u53ef\u81ea\u52a8\u68c0\u67e5\uff09'],
            ['U8 \u5019\u9009\u751f\u6210',
             '\u89c4\u683c\u4e0d\u5168\u65f6\u751f\u6210 2-3 \u4e2a\u7b26\u5408\u6807\u51c6\u7684\u5019\u9009\uff0c'
             '\u6309\u8f7b\u91cf\u4ee3\u7406\u6307\u6807\u6392\u5e8f\uff0c\u9009\u5b9a\u540e\u518d\u505a\u5168\u7cbe\u5ea6\u51e0\u4f55\u6821\u9a8c',
             '\u5019\u9009\u5168\u90e8\u6ee1\u8db3\u6807\u51c6\uff1b\u7528\u6237\u53ef\u9009'],
            ['U6 \u7ea6\u675f\u6c42\u89e3',
             '\u5f15\u5165 ConstraintSolver\uff1a\u51e0\u4f55\u7ea6\u675f\uff08\u91cd\u5408/\u5e73\u884c/\u5782\u76f4/\u76f8\u5207/'
             '\u540c\u5fc3/\u5bf9\u79f0\uff09+ \u5c3a\u5bf8\u7ea6\u675f + \u88c5\u914d\u7ea6\u675f\uff0c'
             'Newton/LM \u8fed\u4ee3 + \u5197\u4f59\u68c0\u6d4b\uff1b\u7ea6\u675f\u5c42\u4e0e\u73b0\u6709 Feature \u5217\u8868\u89e3\u8026',
             '\u8fc7\u7ea6\u675f/\u6b20\u7ea6\u675f\u80fd\u660e\u786e\u62a5\u51fa\uff0c\u800c\u4e0d\u662f\u968f\u4fbf\u7ed9\u89e3'],
        ], widths=[3.0, 9.4, 4.2], font_size=8)

    # ---------------- 六、路线 ----------------
    heading(doc, '\u516d\u3001\u5b9e\u65bd\u8def\u7ebf\uff084 \u5468 + \u540e\u7eed\uff09', 1)
    make_table(
        doc, ['\u5468', '\u76ee\u6807', '\u4ea4\u4ed8\u7269', '\u9a8c\u6536\u6570\u5b57'],
        [
            ['\u7b2c 1 \u5468', 'U2 \u53c2\u6570\u8868 + U1 \u4e24\u6bb5\u5f0f\uff08\u5728\u5e26\u8f6e/\u8f74\u627f/\u6cd5\u5170\u4e09\u7c7b\u843d\u5730\uff09',
             'paramtable.py\u3001planner.py', '\u4e09\u7c7b\u56de\u5f52\u96c6\u53c2\u6570\u51c6\u786e\u7387 \u226595%\uff0c\u9759\u9ed8\u9519\u503c 0'],
            ['\u7b2c 2 \u5468', 'U3 \u516c\u5dee\u6821\u9a8c + \u6d4b\u91cf\u7b97\u5b50',
             'measure.py\u3001verify_tolerance.py', '\u6ce8\u5165 5 \u7c7b\u8d85\u5dee 100% \u88ab\u62e6'],
            ['\u7b2c 3 \u5468', 'U7 \u8bc4\u6d4b\u57fa\u51c6 V2 + \u5168\u91cf\u56de\u5f52',
             'bench_v2.py\u3001reports/bench_v2.json', '\u5e8f\u5217 F1 \u22650.95\u3001\u53c2\u6570 \u226595%\u3001\u8d85\u5dee \u22642%'],
            ['\u7b2c 4 \u5468', 'U4 \u610f\u56fe\u6838\u5bf9\u9762\u677f + U5 \u6807\u51c6\u6570\u636e\u63a8\u5e7f',
             '\u754c\u9762\u9762\u677f\u3001kb/*_std.json', '\u7ea0\u6b63\u53ef\u590d\u7528\uff1b\u22654 \u7c7b\u6807\u51c6\u4ef6\u6709 std \u6570\u636e'],
            ['\u540e\u7eed', 'U9 -> U8 -> U6', 'LLM plan \u63a5\u53e3\u3001\u5019\u9009\u751f\u6210\u3001\u7ea6\u675f\u6c42\u89e3',
             '\u590d\u6742\u4ef6\u51e0\u4f55\u4e00\u81f4\u6027\u53ef\u8bc1\u660e'],
        ], widths=[1.8, 6.2, 4.6, 4.0], font_size=8.5)

    heading(doc, '\u4e03\u3001\u98ce\u9669\u4e0e\u56de\u9000', 2)
    make_table(
        doc, ['\u98ce\u9669', '\u5f71\u54cd', '\u7f13\u89e3'],
        [
            ['\u4e24\u6bb5\u5f0f\u91cd\u6784\u5f15\u5165\u56de\u5f52', '\u73b0\u6709 100% \u6307\u6807\u6389\u4e0b\u6765',
             '\u6bcf\u6b65\u90fd\u8dd1\u300c\u81ea\u68c0 34 + \u4e0d\u53d8\u91cf 14 + \u5e26\u8f6e 114 \u9898 + \u5168\u91cf 161 \u9898\u300d\uff0c\u4efb\u4e00\u6389\u7eff\u5373\u56de\u9000'],
            ['\u516c\u5dee\u5e26\u53d6\u503c\u4e0d\u5f53', '\u8bef\u62a5\u9519\u592a\u591a\u3001\u7528\u6237\u88ab\u62e6\u4f4f',
             '\u516c\u5dee\u6309 GB/T 1804 m \u7ea7\u8d77\u6b65\uff1b\u5148\u8b66\u544a\u540e\u62e6\u4e0b\u4e24\u6863\u5f00\u5173'],
            ['LLM \u63a5\u5165\u5e26\u6765\u4e0d\u786e\u5b9a\u6027', '\u7ed3\u679c\u4e0d\u53ef\u590d\u73b0',
             'LLM \u53ea\u51fa\u7ed3\u6784\u4e0d\u51fa\u6570\uff1b\u6240\u6709\u6570\u503c\u5fc5\u987b\u80fd\u5728\u672c\u5730\u8868/\u516c\u5f0f\u91cc\u6eaf\u6e90'],
            ['\u6807\u51c6\u6570\u636e\u4e0d\u5b8c\u6574', '\u53d6\u9519\u6863',
             '\u7f3a\u6863\u5fc5\u987b\u63d0\u793a\u52a0\u63a8\u5bfc\uff0c\u7edd\u4e0d\u9759\u9ed8\u53d6\u6700\u8fd1\u6863\uff1b\u754c\u9762\u7ed9\u5019\u9009\u6863\u4f4d'],
            ['\u7ea6\u675f\u6c42\u89e3\u4e0d\u7a33\u5b9a', '\u89e3\u4e0d\u51fa\u6216\u89e3\u9519',
             '\u5148\u505a\u68c0\u6d4b\u4e0e\u62a5\u544a\uff0c\u518d\u505a\u6c42\u89e3\uff1b\u4fdd\u7559\u624b\u52a8\u6446\u4f4d\u515c\u5e95'],
        ], widths=[3.4, 4.2, 9.0], font_size=8.5)

    # ---------------- 八、总目标 ----------------
    heading(doc, '\u516b\u3001\u5347\u7ea7\u540e\u7684\u9a8c\u6536\u53e3\u5f84\uff08\u603b\u76ee\u6807\uff09', 1)
    make_table(
        doc, ['\u6307\u6807', '\u73b0\u72b6', '\u5347\u7ea7\u76ee\u6807'],
        [
            ['\u53c2\u6570\u5c42\u51c6\u786e\u7387\uff08\u4eba\u5de5\u9898\u5e93 500 \u9898\uff09', '100%\uff0816 \u9898\u5c0f\u96c6\uff09', '\u226595%\uff08500 \u9898\uff09'],
            ['\u5e8f\u5217\u7ea7\u7279\u5f81 F1', '\u672a\u6d4b', '\u22650.95'],
            ['\u51e0\u4f55\u6709\u6548\u7387', '100%\uff0816 \u9898\uff09', '\u226598%\uff08500 \u9898\uff09'],
            ['\u9759\u9ed8\u9519\u503c\u7387\uff08\u6784\u5efa\u6210\u529f\u4f46\u6570\u503c\u9519\uff09', '\u672a\u6d4b\uff08\u6709\u98ce\u9669\uff09', '0%'],
            ['\u5c3a\u5bf8\u8d85\u5dee\u7387', '\u672a\u6d4b', '\u22642% \u4e14\u5fc5\u62a5'],
            ['\u5168\u96f6\u4ef6\u547d\u4e2d\u7387', '%.2f%%' % ev.get('hit_rate', 0), '\u226593%'],
            ['CATIA \u771f\u673a\u53ef\u590d\u73b0', '\u672a\u9a8c\u8bc1', '5 \u4e2a\u5178\u578b\u4ef6 100% \u590d\u73b0'],
        ], widths=[6.4, 4.6, 5.6], font_size=9)

    doc.add_paragraph()
    para(doc, DASH + ' \u672c\u6587\u4ef6\u662f\u6280\u672f\u8bf4\u660e\u4e66\u800c\u975e\u627f\u8bfa\u4e66\uff1a'
              '\u6bcf\u6761\u76ee\u6807\u90fd\u7ed9\u4e86\u53ef\u590d\u8dd1\u7684\u9a8c\u6536\u65b9\u5f0f\uff1b'
              '\u8fbe\u4e0d\u5230\u5c31\u8bf4\u660e\u65b9\u6848\u8981\u6539\uff0c\u800c\u4e0d\u662f\u9760\u8bdd\u672f\u3002'
              '\u6587\u732e\u4f9d\u636e\u89c1 kb/papers_intl.json\uff0c'
              '\u5b9e\u6d4b\u6570\u636e\u89c1 reports/precision_diagnosis.json \u4e0e reports/\u5e26\u8f6e\u63d0\u793a\u8bcd\u5b66\u4e60\u62a5\u544a.md\u3002',
         size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER)

    doc.save(OUT)
    return OUT


if __name__ == '__main__':
    p = build()
    print('\u5df2\u751f\u6210\uff1a%s' % p)
    print('\u5927\u5c0f\uff1a%.1f KB' % (os.path.getsize(p) / 1024.0))
