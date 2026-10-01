# -*- coding: utf-8 -*-
r"""从手册网格数据构建「汽车基础零件参数知识库」(v2)。

输入: kb\handbook_grid.jsonl   (已做 colspan/rowspan 展开的规整网格)
输出: kb\auto_parts.json

v2 的关键改进: **动态定位表头行**。
   手册的表头不在固定位置, 而且用 d/D/B 这种符号。做法是在前几行里找
   同时包含本类别所需全部符号的那一行, 以它为表头, 下面的行才是数据。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, 'kb')
SRC = os.path.join(KB, 'handbook_grid.jsonl')
SRC2 = os.path.join(KB, 'handbook_target.jsonl')   # 定向完整重爬 (含深藏的参数表)
OUT = os.path.join(KB, 'auto_parts.json')

_num = re.compile(r'^-?\d+(?:\.\d+)?$')

# 类别 -> (匹配关键词, 需要的参数符号)
CATEGORIES = [
    ('深沟球轴承',   ['深沟球轴承'],                 ('d', 'D', 'B')),
    ('圆锥滚子轴承', ['圆锥滚子轴承'],               ('d', 'D', 'T')),
    ('推力球轴承',   ['推力球轴承'],                 ('d', 'D', 'H')),
    ('调心球轴承',   ['调心球轴承'],                 ('d', 'D', 'B')),
    ('圆柱滚子轴承', ['圆柱滚子轴承'],               ('d', 'D', 'B')),
    ('角接触球轴承', ['角接触球轴承'],               ('d', 'D', 'B')),
    ('直齿圆柱齿轮', ['直齿圆柱齿轮', '标准直齿圆柱齿轮'], ('m', 'z', 'b')),
    ('斜齿圆柱齿轮', ['斜齿圆柱齿轮'],               ('mn', 'z', 'b')),
    ('锥齿轮',       ['锥齿轮'],                     ('m', 'z')),
    ('同步带轮',     ['同步带轮'],                   ('d', 'D', 'B')),
    ('链轮',         ['链轮'],                       ('d', 'D', 'B')),
    ('V带轮',        ['V带轮'],                      ('d', 'D', 'B')),
    ('螺栓',         ['螺栓'],                       ('d', 'L')),
    ('螺母',         ['螺母'],                       ('D', 's', 'm')),
    ('垫圈',         ['垫圈'],                       ('d', 'D', 'h')),
    ('轴套',         ['轴套', '衬套'],               ('d', 'D', 'L')),
    ('油封',         ['油封'],                       ('d', 'D', 'b')),
    ('O形圈',        ['O形圈', 'O 形圈'],            ('d', 'D')),
    ('法兰',         ['法兰'],                       ('D', 'd', 'b')),
    ('联轴器',       ['联轴器'],                     ('d', 'D', 'L')),
    ('圆柱销',       ['圆柱销'],                     ('d', 'L')),
    ('圆锥销',       ['圆锥销'],                     ('d', 'L')),
    ('制动盘',       ['制动盘', '刹车盘'],           ('D', 'T', 'd')),
    # ---- 中等难度 / 汽车传动与制动零件 ----
    ('蜗杆',         ['蜗杆'],                       ('d', 'D', 'L')),
    ('蜗轮',         ['蜗轮'],                       ('d', 'D', 'B')),
    ('螺旋弹簧',     ['压缩弹簧', '圆柱螺旋压缩弹簧'], ('d', 'D', 'H')),
    ('扭转弹簧',     ['扭转弹簧'],                   ('d', 'D', 'H')),
    ('碟形弹簧',     ['碟形弹簧'],                   ('d', 'D', 'h')),
    ('挡圈',         ['孔用弹性挡圈', '轴用弹性挡圈'], ('d', 'D', 's')),
    ('花键',         ['矩形花键', '渐开线花键'],     ('d', 'D', 'b')),
    ('平键',         ['平键'],                       ('b', 'h', 'L')),
    ('半圆键',       ['半圆键'],                     ('b', 'h', 'L')),
    ('多楔带轮',     ['多楔带轮', '多楔带'],         ('d', 'D', 'B')),
    ('制动鼓',       ['制动鼓'],                     ('D', 'd', 'H')),
    ('离合器摩擦片', ['离合器摩擦片', '摩擦片'],     ('D', 'd', 'h')),
    ('手轮',         ['手轮'],                       ('d', 'D', 'H')),
    ('轴承座',       ['轴承座'],                     ('d', 'D', 'H')),
    ('螺塞',         ['螺塞'],                       ('d', 'L')),
    ('管接头',       ['管接头', '接头'],             ('d', 'D', 'L')),
    ('凸轮',         ['凸轮'],                       ('d', 'D', 'b')),
    ('棘轮',         ['棘轮'],                       ('d', 'D', 'b')),
    ('胀紧套',       ['胀紧连接套', '胀紧套'],       ('d', 'D', 'L')),
    ('齿轮轴',       ['齿轮轴'],                     ('d', 'L')),
]

# 中文表头也算
CN = {'内径': 'd', '外径': 'D', '宽度': 'B', '厚度': 'T', '高度': 'H',
      '长度': 'L', '轴径': 'd', '孔径': 'd', '齿数': 'z', '模数': 'm',
      '齿宽': 'b', '公称直径': 'd', '对边宽度': 's', '螺距': 'P'}

MAXDIM = 5000.0
RULES = {
    '深沟球轴承':   lambda p: 0.5 <= p['d'] < p['D'] <= 1200 and 0.5 <= p['B'] <= 300 and 1.2 <= p['D'] / p['d'] <= 15,
    '圆锥滚子轴承': lambda p: 0.5 <= p['d'] < p['D'] <= 1500 and 1.5 <= p['D'] / p['d'] <= 15 and 2 <= p['T'] <= 400,
    '推力球轴承':   lambda p: 0.5 <= p['d'] < p['D'] <= 1200 and 1.3 <= p['D'] / p['d'] <= 15 and 2 <= p['H'] <= 300,
    '调心球轴承':   lambda p: 0.5 <= p['d'] < p['D'] <= 1200 and 0.5 <= p['B'] <= 300 and 1.2 <= p['D'] / p['d'] <= 12,
    '圆柱滚子轴承': lambda p: 0.5 <= p['d'] < p['D'] <= 1500 and 0.5 <= p['B'] <= 400 and 1.2 <= p['D'] / p['d'] <= 15,
    '角接触球轴承': lambda p: 0.5 <= p['d'] < p['D'] <= 1200 and 0.5 <= p['B'] <= 300 and 1.2 <= p['D'] / p['d'] <= 12,
    '直齿圆柱齿轮': lambda p: 0.1 <= p['m'] <= 20 and 6 <= p['z'] <= 200 and 2 <= p['b'] <= 400,
    '斜齿圆柱齿轮': lambda p: 0.1 <= p['mn'] <= 20 and 6 <= p['z'] <= 300 and 2 <= p['b'] <= 400,
    '锥齿轮':       lambda p: 0.5 <= p['m'] <= 20 and 8 <= p['z'] <= 120,
    '同步带轮':     lambda p: 3 <= p['d'] < p['D'] <= 800 and 1.2 <= p['D'] / p['d'] <= 12 and 3 <= p['B'] <= 200,
    '链轮':         lambda p: 3 <= p['d'] < p['D'] <= 800 and 1.3 <= p['D'] / p['d'] <= 12 and 3 <= p['B'] <= 200,
    'V带轮':        lambda p: 5 <= p['d'] < p['D'] <= 1200 and 1.3 <= p['D'] / p['d'] <= 12 and 5 <= p['B'] <= 300,
    '螺栓':         lambda p: 1 <= p['d'] <= 64 and p['d'] <= p['L'] <= 1000,
    '螺母':         lambda p: 2 <= p['D'] <= 100 and 1.2 <= p['s'] / p['D'] <= 2.4 and 0.4 <= p['m'] / p['D'] <= 1.6,
    '垫圈':         lambda p: 1 <= p['d'] < p['D'] <= 400 and 1.1 <= p['D'] / p['d'] <= 5 and 0.2 <= p['h'] <= 30,
    '轴套':         lambda p: 1 <= p['d'] < p['D'] <= 500 and 1.1 <= p['D'] / p['d'] <= 6 and 2 <= p['L'] <= 500,
    '油封':         lambda p: 3 <= p['d'] < p['D'] <= 500 and 1.1 <= p['D'] / p['d'] <= 4 and 3 <= p['b'] <= 60,
    'O形圈':        lambda p: 1 <= p['d'] < p['D'] <= 800 and 1.02 <= p['D'] / p['d'] <= 3,
    '法兰':         lambda p: 5 <= p['d'] < p['D'] <= 3000 and 1.1 <= p['D'] / p['d'] <= 12 and 1 <= p['b'] <= 300,
    '联轴器':       lambda p: 5 <= p['d'] < p['D'] <= 1500 and 1.5 <= p['D'] / p['d'] <= 15 and 10 <= p['L'] <= 1000,
    '圆柱销':       lambda p: 0.5 <= p['d'] <= 60 and p['d'] <= p['L'] <= 400,
    '圆锥销':       lambda p: 0.5 <= p['d'] <= 60 and p['d'] <= p['L'] <= 400,
    '制动盘':       lambda p: 50 <= p['d'] < p['D'] <= 500 and 1.3 <= p['D'] / p['d'] <= 6 and 3 <= p['T'] <= 60,
    # ---- 中等难度 ----
    '蜗杆':         lambda p: 5 <= p['d'] < p['D'] <= 200 and 1.2 <= p['D'] / p['d'] <= 6 and 10 <= p['L'] <= 500,
    '蜗轮':         lambda p: 10 <= p['d'] < p['D'] <= 800 and 1.5 <= p['D'] / p['d'] <= 12 and 3 <= p['B'] <= 200,
    '螺旋弹簧':     lambda p: 0.2 <= p['d'] < p['D'] <= 200 and 3 <= p['D'] / p['d'] <= 30 and 5 <= p['H'] <= 800,
    '扭转弹簧':     lambda p: 0.3 <= p['d'] < p['D'] <= 200 and 3 <= p['D'] / p['d'] <= 25 and 5 <= p['H'] <= 500,
    '碟形弹簧':     lambda p: 3 <= p['d'] < p['D'] <= 400 and 1.4 <= p['D'] / p['d'] <= 4 and 0.5 <= p['h'] <= 30,
    '挡圈':         lambda p: 3 <= p['d'] < p['D'] <= 400 and 1.05 <= p['D'] / p['d'] <= 2 and 0.3 <= p['s'] <= 8,
    '花键':         lambda p: 5 <= p['d'] < p['D'] <= 300 and 1.1 <= p['D'] / p['d'] <= 2 and 1 <= p['b'] <= 40,
    '平键':         lambda p: 1 <= p['b'] <= 60 and 1 <= p['h'] <= 40 and p['b'] <= p['L'] <= 400,
    '半圆键':       lambda p: 1 <= p['b'] <= 40 and 1 <= p['h'] <= 30 and p['b'] <= p['L'] <= 120,
    '多楔带轮':     lambda p: 8 <= p['d'] < p['D'] <= 1000 and 1.2 <= p['D'] / p['d'] <= 10 and 10 <= p['B'] <= 300,
    '制动鼓':       lambda p: 80 <= p['d'] < p['D'] <= 600 and 1.05 <= p['D'] / p['d'] <= 1.6 and 40 <= p['H'] <= 400,
    '离合器摩擦片': lambda p: 60 <= p['d'] < p['D'] <= 500 and 1.2 <= p['D'] / p['d'] <= 3 and 1 <= p['h'] <= 40,
    '手轮':         lambda p: 5 <= p['d'] < p['D'] <= 800 and 2 <= p['D'] / p['d'] <= 20 and 10 <= p['H'] <= 300,
    '轴承座':       lambda p: 5 <= p['d'] < p['D'] <= 600 and 1.3 <= p['D'] / p['d'] <= 8 and 8 <= p['H'] <= 400,
    '螺塞':         lambda p: 3 <= p['d'] <= 80 and p['d'] <= p['L'] <= 120,
    '管接头':       lambda p: 3 <= p['d'] < p['D'] <= 300 and 1.2 <= p['D'] / p['d'] <= 6 and 5 <= p['L'] <= 300,
    '凸轮':         lambda p: 5 <= p['d'] < p['D'] <= 400 and 1.3 <= p['D'] / p['d'] <= 5 and 3 <= p['b'] <= 120,
    '棘轮':         lambda p: 10 <= p['d'] < p['D'] <= 500 and 1.3 <= p['D'] / p['d'] <= 5 and 3 <= p['b'] <= 120,
    '胀紧套':       lambda p: 5 <= p['d'] < p['D'] <= 500 and 1.1 <= p['D'] / p['d'] <= 2.5 and 5 <= p['L'] <= 200,
    '齿轮轴':       lambda p: 3 <= p['d'] <= 120 and p['d'] <= p['L'] <= 800,
}


def find_category(path, title):
    text = ' '.join(path) + ' ' + title
    for name, kws, syms in CATEGORIES:
        for kw in kws:
            if kw in text:
                return name, syms
    return None, None


def locate_header(grid, want):
    """在前 8 行里找同时含有所需符号的表头行, 返回 (行号, {符号: 列号})。"""
    for r in range(min(8, len(grid))):
        cells = [re.sub(r'\s+', '', str(c)) for c in grid[r]]
        m = {}
        for i, c in enumerate(cells):
            sym = c if c in want else CN.get(c)
            if sym in want and sym not in m:
                m[sym] = i
        if all(k in m for k in want):
            return r, m
    return None, None


def main():
    if not os.path.isfile(SRC):
        print('缺少 %s' % SRC)
        return 1
    parts, rejected = [], Counter()
    cat_count, pages_with = Counter(), Counter()
    n_pages = 0
    seen_url = set()

    def iter_pages():
        for path in (SRC2, SRC):
            if not os.path.isfile(path):
                continue
            with open(path, encoding='utf-8') as fh:
                for ln in fh:
                    try:
                        yield json.loads(ln)
                    except Exception:
                        continue

    for pg in iter_pages():
            u = pg.get('url')
            if u in seen_url:
                continue
            seen_url.add(u)
            n_pages += 1
            if pg.get('error') or not pg.get('grids'):
                continue
            cat, syms = find_category(pg.get('path') or [], pg.get('title') or '')
            if not cat:
                continue
            rule = RULES.get(cat)
            got_any = False
            for tb in pg['grids']:
                grid = tb['grid']
                hdr, colmap = locate_header(grid, syms)
                if hdr is None:
                    continue
                for row in grid[hdr + 1:]:
                    rec = {}
                    for sym, i in colmap.items():
                        if i < len(row) and _num.match(row[i]):
                            try:
                                rec[sym] = float(row[i])
                            except ValueError:
                                pass
                    if len(rec) < len(syms):
                        continue
                    if any(v <= 0 or v > MAXDIM for v in rec.values()):
                        rejected[cat] += 1
                        continue
                    try:
                        ok = rule(rec)
                    except Exception:
                        ok = False
                    if not ok:
                        rejected[cat] += 1
                        continue
                    rec.update({'_cat': cat, '_src': pg['url'],
                                '_title': pg.get('title', ''),
                                '_path': ' / '.join(pg.get('path') or [])})
                    parts.append(rec)
                    cat_count[cat] += 1
                    got_any = True
            if got_any:
                pages_with[cat] += 1

    print('扫描 %d 页' % n_pages)
    print('通过校验 %d 条' % len(parts))
    print('%-14s %6s %6s %6s' % ('类别', '条数', '剔除', '来源页'))
    for c in sorted(set(list(cat_count) + list(rejected))):
        print('%-14s %6d %6d %6d' % (c, cat_count.get(c, 0), rejected.get(c, 0),
                                     pages_with.get(c, 0)))
    # 去重: 同一类别下参数完全相同的记录只保留一条
    uniq, seen_key = [], set()
    for r in parts:
        key = (r['_cat'], tuple(sorted((k, v) for k, v in r.items()
                                       if not k.startswith('_'))))
        if key in seen_key:
            continue
        seen_key.add(key)
        uniq.append(r)
    print('去重: %d -> %d 条' % (len(parts), len(uniq)))
    parts = uniq
    cat_count = Counter(r['_cat'] for r in parts)

    data = {'source': '嘉立创FA机械设计手册 https://www.jlc-jdgf.com/mcbook/',
            'count': len(parts), 'by_category': dict(cat_count), 'parts': parts}
    json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('已写出 %s (%.1f KB)' % (OUT, os.path.getsize(OUT) / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
