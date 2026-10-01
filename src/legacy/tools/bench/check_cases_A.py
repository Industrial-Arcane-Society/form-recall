# -*- coding: utf-8 -*-
r"""校验 tools\bench\cases_A.json (A 组 50 条评测用例)。

检查项:
  1. UTF-8 无 BOM, JSON 可解析
  2. 恰好 50 条, id 为 A01..A50 且唯一
  3. 每条字段齐全 (id/class/text/expect; expect 内 bbox/holes/pockets/steps/note)
  4. bbox 三个数均为正数; holes 每项 dia>0, through 为布尔, count>=1
  5. pockets / steps 为非负整数; ignore 若存在则为字符串列表
用法: python tools\bench\check_cases_A.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ROOT, 'tools', 'bench', 'cases_A.json')
CLASSES = ('基础体', '板类', '盘类', '管件/回转体')
EXPECT_KEYS = {'bbox', 'holes', 'pockets', 'steps', 'note'}


def main() -> int:
    errs = []
    warns = []
    raw = open(PATH, 'rb').read()
    if raw.startswith(b'\xef\xbb\xbf'):
        errs.append('文件带 UTF-8 BOM')
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception as e:
        print('JSON 解析失败: %s' % e)
        return 1
    if not isinstance(data, list):
        errs.append('顶层不是 JSON 数组')
        data = []

    n = len(data)
    if n != 50:
        errs.append('条数为 %d, 应为 50' % n)

    ids = []
    dist = {}
    holes_total = 0
    steps_cases = 0
    pocket_cases = 0
    complex_cases = []          # 一行 4 个以上参数 + 多特征的图纸式长句
    for i, c in enumerate(data):
        tag = '第%d条' % (i + 1)
        if not isinstance(c, dict):
            errs.append('%s 不是对象' % tag)
            continue
        cid = c.get('id')
        tag = cid or tag
        ids.append(cid)

        extra = set(c) - {'id', 'class', 'text', 'expect', 'ignore'}
        if extra:
            warns.append('%s 有多余字段: %s' % (tag, sorted(extra)))
        for k in ('id', 'class', 'text', 'expect'):
            if k not in c:
                errs.append('%s 缺字段 %s' % (tag, k))
        if not isinstance(c.get('text'), str) or not c.get('text', '').strip():
            errs.append('%s text 为空' % tag)
        elif not re.search(r'[\u4e00-\u9fff]', c['text']):
            errs.append('%s text 不含中文' % tag)
        else:
            dist[c['class']] = dist.get(c['class'], 0) + 1
        if c.get('class') not in CLASSES:
            errs.append('%s class 非法: %r' % (tag, c.get('class')))
        if 'ignore' in c and (not isinstance(c['ignore'], list)
                              or not all(isinstance(x, str) for x in c['ignore'])):
            errs.append('%s ignore 应为字符串列表' % tag)

        e = c.get('expect')
        if not isinstance(e, dict):
            errs.append('%s expect 不是对象' % tag)
            continue
        miss = EXPECT_KEYS - set(e)
        if miss:
            errs.append('%s expect 缺字段: %s' % (tag, sorted(miss)))
        unknown = set(e) - EXPECT_KEYS
        if unknown:
            errs.append('%s expect 有非法字段: %s' % (tag, sorted(unknown)))

        bb = e.get('bbox')
        if (not isinstance(bb, list) or len(bb) != 3
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and v > 0 for v in bb)):
            errs.append('%s bbox 必须是 3 个正数: %r' % (tag, bb))

        hs = e.get('holes')
        if not isinstance(hs, list):
            errs.append('%s holes 不是数组' % tag)
        else:
            holes_total += len(hs)
            for j, h in enumerate(hs):
                if not isinstance(h, dict):
                    errs.append('%s holes[%d] 不是对象' % (tag, j))
                    continue
                if set(h) != {'dia', 'through', 'count'}:
                    errs.append('%s holes[%d] 字段应为 dia/through/count: %s'
                                % (tag, j, sorted(h)))
                dia = h.get('dia')
                if not isinstance(dia, (int, float)) or isinstance(dia, bool) or dia <= 0:
                    errs.append('%s holes[%d] dia 必须 >0: %r' % (tag, j, dia))
                if not isinstance(h.get('through'), bool):
                    errs.append('%s holes[%d] through 必须是布尔: %r'
                                % (tag, j, h.get('through')))
                cnt = h.get('count')
                if not isinstance(cnt, int) or isinstance(cnt, bool) or cnt < 1:
                    errs.append('%s holes[%d] count 必须是 >=1 的整数: %r' % (tag, j, cnt))

        for k in ('pockets', 'steps'):
            v = e.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                errs.append('%s %s 必须是非负整数: %r' % (tag, k, v))
        if isinstance(e.get('steps'), int) and e['steps'] > 0:
            steps_cases += 1
        if isinstance(e.get('pockets'), int) and e['pockets'] > 0:
            pocket_cases += 1
        if not isinstance(e.get('note'), str) or not e['note'].strip():
            errs.append('%s note 为空' % tag)

        # 图纸式长句统计: >=4 个尺寸数字 且 至少 2 类特征关键词
        txt = c.get('text', '')
        nums = re.findall(r'\d+(?:\.\d+)?', txt)
        kw = sum(1 for k in ('孔', '槽', '法兰', '台阶', '凸台', '圆角', '倒角', '扩孔')
                 if k in txt)
        if len(nums) >= 4 and kw >= 2:
            complex_cases.append(tag)

    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        errs.append('id 重复: %s' % dup)
    expect_ids = ['A%02d' % i for i in range(1, 51)]
    if [x for x in ids if x] != expect_ids:
        bad = [x for x in expect_ids if x not in ids] + [x for x in ids if x not in expect_ids]
        errs.append('id 不是 A01..A50 顺序: %s' % bad[:10])

    print('文件: %s' % PATH)
    print('BOM : %s' % ('有(!)' if raw.startswith(b'\xef\xbb\xbf') else '无'))
    print('JSON: 解析成功, 共 %d 条' % n)
    print('id  : 唯一 %d 个 (%s .. %s)' % (len(set(ids)), ids[0], ids[-1]))
    print('类别分布: %s' % '  '.join('%s=%d' % (k, dist[k]) for k in CLASSES if k in dist))
    print('holes 条目总数 %d; 含台阶用例 %d 条; 含凹槽用例 %d 条'
          % (holes_total, steps_cases, pocket_cases))
    print('图纸式长句(>=4 参数且多特征) %d 条: %s'
          % (len(complex_cases), ' '.join(complex_cases)))
    for w in warns:
        print('提示: %s' % w)
    if errs:
        print('\n❌ 校验失败 %d 项:' % len(errs))
        for e in errs:
            print('  - %s' % e)
        return 1
    print('\n✅ 校验全部通过: 50 条, 字段齐全, bbox 三轴均为正数, holes dia>0, id 唯一')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
