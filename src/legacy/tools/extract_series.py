# -*- coding: utf-8 -*-
r"""从手册语料里抽取"标准系列"数值表, 存成结构化数据。

手册里大部分高难度零件 (齿轮/花键/蜗杆) 只有公式表和公差表, 没有现成尺寸表;
但弹簧这一类是有**真实标准系列**的 (中径 / 有效圈数 / 自由高度), 抽出来给
自测用例当参数池用 —— 这样测的就是真实会出现的规格, 而不是随手编的数字。

输出: kb/spring_series.json
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, 'kb', 'handbook_grid.jsonl')
OUT = os.path.join(ROOT, 'kb', 'spring_series.json')
PAGE = '11017'          # 圆柱螺旋弹簧参数选择
SRC = 'https://www.jlc-jdgf.com/mcbook/11017.htm'


def grids(rec):
    for g in (rec.get('grids') or []):
        gg = g.get('grid') if isinstance(g, dict) else g
        if gg:
            yield gg


def numbers(row):
    """把一行单元格里的数字抠出来, 去掉空格和括弧标记。"""
    out = []
    for c in row:
        s = str(c).replace(' ', '').replace('（', '(').replace('）', ')')
        for m in re.findall(r'\(?\d+(?:\.\d+)?\)?', s):
            try:
                out.append(float(m.strip('()')))
            except ValueError:
                pass
    return out


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    page = None
    for line in open(GRID, encoding='utf-8'):
        rec = json.loads(line)
        if PAGE in rec.get('url', ''):
            page = rec
            break
    if not page:
        print('没找到页面', PAGE)
        return
    gg = list(grids(page))[0]

    # 按 "表 N ..." 标题行切块
    blocks = {}
    cur = None
    for row in gg:
        first = str(row[0]).replace(' ', '') if row else ''
        m = re.match(r'^表(\d)', first)
        if m:
            cur = int(m.group(1))
            blocks[cur] = []
            continue
        if cur is not None:
            blocks[cur].append(row)

    series = {}
    names = {1: '中径D', 2: '压缩有效圈数n', 4: '自由高度H0'}
    for idx, name in names.items():
        vals = []
        for row in blocks.get(idx, []):
            vals.extend(numbers(row))
        vals = sorted({round(v, 3) for v in vals if v > 0})
        series[name] = vals

    data = {'source': SRC, 'title': page.get('title', ''), 'series': series,
            'note': '摘自嘉立创FA机械设计手册「圆柱螺旋弹簧参数选择」GB/T 1358 系列'}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for k, v in series.items():
        print('  %-10s %3d 个标准值: %s ... %s' %
              (k, len(v), v[:6], v[-3:]))
    print('已写出', OUT)


if __name__ == '__main__':
    main()
