# -*- coding: utf-8 -*-
r"""把 Markdown 转成排版好的 Word (.docx)。

支持: # 标题 / ## 三级标题 / - 列表 / 1. 编号 / > 引用 / ``` 代码块 /
      | 表格 | / **粗体** / `行内代码` / --- 分隔线。
用法: python tools\make_docx_from_md.py 操作说明_CATIA连接版.md [输出.docx]
"""
from __future__ import annotations

import os
import re
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

CJK = '微软雅黑'
MONO = 'Consolas'
INLINE = re.compile(r'(\*\*.+?\*\*|`[^`]+`)')
IMG = re.compile(r'!\[[^\]]*\]\([^)]*\)')


def set_font(run, name, size=None, bold=None, color=None, italic=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.append(rf)
    rf.set(qn('w:ascii'), name)
    rf.set(qn('w:hAnsi'), name)
    rf.set(qn('w:eastAsia'), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color:
        run.font.color.rgb = RGBColor(*color)


def shade(paragraph, hex_fill):
    pr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_fill)
    pr.append(shd)


def add_inline(p, text, size=None, base_bold=False):
    text = IMG.sub('', text)
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            set_font(p.add_run(part[2:-2]), CJK, size, True)
        elif part.startswith('`') and part.endswith('`') and len(part) > 2:
            set_font(p.add_run(part[1:-1]), MONO, (size or 10.5) - 0.5)
        else:
            set_font(p.add_run(part), CJK, size, True if base_bold else None)


def add_code(doc, lines):
    for i, ln in enumerate(lines):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(2 if i == 0 else 0)
        pf.space_after = Pt(2 if i == len(lines) - 1 else 0)
        pf.left_indent = Cm(0.5)
        pf.line_spacing = 1.0
        set_font(p.add_run(ln if ln else ' '), MONO, 9.5)
        shade(p, 'F2F3F5')


def add_table(doc, rows):
    rows = [r for r in rows if not re.match(r'^\s*\|[\s:|-]+\|\s*$', r)]
    cells = [[c.strip() for c in r.strip().strip('|').split('|')] for r in rows]
    if not cells:
        return
    ncol = max(len(r) for r in cells)
    t = doc.add_table(rows=0, cols=ncol)
    t.style = 'Table Grid'
    for ri, row in enumerate(cells):
        cs = t.add_row().cells
        for ci in range(ncol):
            txt = row[ci] if ci < len(row) else ''
            para = cs[ci].paragraphs[0]
            para.paragraph_format.space_before = Pt(1)
            para.paragraph_format.space_after = Pt(1)
            add_inline(para, txt, size=9.5, base_bold=(ri == 0))
    doc.add_paragraph()


def save_with_fallback(doc, dst):
    """目标文件被 Word/WPS 占用时, 自动另存为「(更新版)」, 返回 (实际路径, 是否回退)。"""
    try:
        doc.save(dst)
        return dst, False
    except PermissionError:
        base, ext = os.path.splitext(dst)
        alt = base + '(更新版)' + ext
        doc.save(alt)
        return alt, True


def convert(md_path, docx_path):
    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()

    doc = Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Cm(2.0)
        s.left_margin = s.right_margin = Cm(2.2)
    normal = doc.styles['Normal']
    normal.font.name = CJK
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    for nm, sz in (('Heading 1', 18), ('Heading 2', 14), ('Heading 3', 12)):
        st = doc.styles[nm]
        st.font.name = CJK
        st.font.size = Pt(sz)
        st.element.rPr.rFonts.set(qn('w:eastAsia'), CJK)

    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if s.startswith('```'):                       # 代码块
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            add_code(doc, buf)
            i += 1
            continue

        if s.startswith('|') and s.endswith('|'):     # 表格
            buf = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                buf.append(lines[i].strip())
                i += 1
            add_table(doc, buf)
            continue

        if not s or s in ('---', '***', '___'):
            i += 1
            continue

        m = re.match(r'^(#{1,4})\s+(.*)$', s)
        if m:
            lvl = len(m.group(1))
            p = doc.add_heading(level=min(lvl, 3))
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            add_inline(p, m.group(2), size=18 if lvl == 1 else (14 if lvl == 2 else 12),
                       base_bold=True)
            i += 1
            continue

        if s.startswith('>'):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.space_after = Pt(4)
            add_inline(p, s.lstrip('> ').strip(), size=9.5)
            for r in p.runs:
                r.font.color.rgb = RGBColor(0x44, 0x4A, 0x55)
            i += 1
            continue

        if re.match(r'^[-*]\s+', s):
            p = doc.add_paragraph(style='List Bullet')
            add_inline(p, re.sub(r'^[-*]\s+', '', s))
            i += 1
            continue

        if re.match(r'^\d+[.)]\s+', s):
            p = doc.add_paragraph(style='List Number')
            add_inline(p, re.sub(r'^\d+[.)]\s+', '', s))
            i += 1
            continue

        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        add_inline(p, s)
        i += 1

    path, fell_back = save_with_fallback(doc, docx_path)
    return path, fell_back

if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, '操作说明_CATIA连接版.md')
    if not os.path.isabs(src):
        src = os.path.join(root, src)
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.docx'
    if not os.path.isabs(dst):
        dst = os.path.join(root, dst)
    out, fell = convert(src, dst)
    size = os.path.getsize(out)
    if fell:
        print('原文件被占用（可能在 Word/WPS 里开着），已另存为: %s  (%d 字节)' % (out, size))
    else:
        print('已生成: %s  (%d 字节)' % (out, size))
