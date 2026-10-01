# -*- coding: utf-8 -*-
"""校验 tools\\bench\\cases_B.json 的格式与自洽性 (标准库, 只读输入)。"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, 'cases_B.json')
CLASSES = ('箱体/槽类', '齿轮类', '复合件', '轴类/标准件')
EXPECT_KEYS = {'bbox', 'holes', 'pockets', 'steps', 'note', 'ignore'}


def main():
    raw = open(P, 'rb').read()
    print('BOM          :', raw[:3] == b'\xef\xbb\xbf')
    print('size (bytes) :', len(raw))
    cases = json.loads(raw.decode('utf-8'))
    print('type/count   :', type(cases).__name__, len(cases))
    assert isinstance(cases, list) and len(cases) == 50, '必须是 50 条的 JSON 数组'

    ids, errs = [], []
    for c in cases:
        tag = c.get('id', '?')
        ids.append(tag)
        top = set(c.keys())
        if top != {'id', 'class', 'text', 'expect'}:
            errs.append((tag, '顶层字段异常: %s' % sorted(top)))
        e = c.get('expect', {})
        if set(e.keys()) - EXPECT_KEYS:
            errs.append((tag, 'expect 多出字段: %s' % sorted(set(e.keys()) - EXPECT_KEYS)))
        for k in ('bbox', 'holes', 'pockets', 'steps', 'note'):
            if k not in e:
                errs.append((tag, 'expect 缺字段 %s' % k))
        bb = e.get('bbox')
        if not (isinstance(bb, list) and len(bb) == 3
                and all(isinstance(v, (int, float)) and v > 0 for v in bb)):
            errs.append((tag, 'bbox 必须是 3 个正数: %r' % (bb,)))
        if not (isinstance(e.get('pockets'), int) and e.get('pockets') >= 0):
            errs.append((tag, 'pockets 非法: %r' % (e.get('pockets'),)))
        if not (isinstance(e.get('steps'), int) and e.get('steps') >= 0):
            errs.append((tag, 'steps 非法: %r' % (e.get('steps'),)))
        if not (isinstance(e.get('note'), str) and e.get('note').strip()):
            errs.append((tag, 'note 为空'))
        if 'ignore' in e and not (isinstance(e['ignore'], list)
                                  and e['ignore'] and all(isinstance(s, str) for s in e['ignore'])):
            errs.append((tag, 'ignore 必须是非空字符串数组'))
        hs = e.get('holes')
        if not isinstance(hs, list):
            errs.append((tag, 'holes 必须是数组'))
        else:
            for h in hs:
                if set(h.keys()) != {'dia', 'through', 'count'}:
                    errs.append((tag, 'hole 字段异常: %s' % sorted(h.keys())))
                    continue
                if not (isinstance(h['dia'], (int, float)) and h['dia'] > 0):
                    errs.append((tag, 'hole dia 必须 > 0: %r' % (h['dia'],)))
                if not isinstance(h['through'], bool):
                    errs.append((tag, 'hole through 必须是布尔: %r' % (h['through'],)))
                if not (isinstance(h['count'], int) and h['count'] >= 1):
                    errs.append((tag, 'hole count 必须是 >=1 的整数: %r' % (h['count'],)))
        if not (isinstance(c.get('text'), str) and len(c.get('text', '')) >= 10):
            errs.append((tag, 'text 过短'))
        if c.get('class') not in CLASSES:
            errs.append((tag, 'class 非法: %r' % (c.get('class'),)))

    want = ['B%02d' % i for i in range(1, 51)]
    print('id 唯一      :', len(set(ids)) == 50)
    print('id = B01..B50:', ids == want)
    print('重复 id      :', [k for k, v in collections.Counter(ids).items() if v > 1] or '无')
    print('text 唯一    :', len(set(c['text'] for c in cases)) == 50)
    print('类别分布     :', dict(collections.Counter(c['class'] for c in cases)))
    print('有孔用例     :', sum(1 for c in cases if c['expect']['holes']))
    print('有槽用例     :', sum(1 for c in cases if c['expect']['pockets'] > 0))
    print('带符号Ø/×/±/°:', sum(1 for c in cases
                              if any(s in c['text'] for s in ('Ø', '×', '±', '°'))))
    print('校验错误     :', errs if errs else '无')
    assert not errs, '存在 %d 处错误' % len(errs)
    print('结果         : OK —— 50 条用例全部通过校验')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
