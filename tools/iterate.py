# -*- coding: utf-8 -*-
r"""迭代升级驱动 —— 逐轮开启能力, 量化命中率提升, 直到超过目标。

每一轮:
  1. 设定本轮启用的能力 (partlib / synonyms ...)
  2. 用同一套随机种子采样同样的评测用例 (保证可比)
  3. 跑评测, 记录命中率
  4. 分析失败样本, 输出"下一步该补什么"
目标达成或轮次用尽后, 写 reports\iterations.json 供网页报告使用。
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
REPORTS = os.path.join(ROOT, 'reports')
TARGET = 90.0

from ai_modeler import nlparser as NLP
import evaluate as EV


ROUNDS = [
    ('R1 通用规则引擎 (基线)',      {'partlib': False}, '只用通用中文规则, 不认识标准零件'),
    ('R2 接入零件库',              {'partlib': True},  '从机械设计手册学到的零件模板生效'),
    ('R3 零件库 + 同义词归一',      {'partlib': True, 'synonyms': True}, '对齐零件名/参数名的多种叫法'),
]


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    os.makedirs(REPORTS, exist_ok=True)
    n = int(os.environ.get('EVAL_N', '150'))
    cases = EV.load_cases(limit=n)
    print('评测用例 %d 条 / %d 个类别 (固定随机种子, 各轮完全可比)'
          % (len(cases), len(set(c['category'] for c in cases))))
    print('')
    rounds = []
    for i, (label, caps, desc) in enumerate(ROUNDS, 1):
        NLP.set_caps(**caps)
        print('=' * 70)
        print('第 %d 轮: %s   —— %s' % (i, label, desc))
        print('=' * 70)
        r = EV.evaluate(cases, label='R%d' % i, save=True)
        r['desc'] = desc
        r['caps'] = caps
        rounds.append(r)
        print('  >>> 命中率 %.2f%% (%d/%d), 用时 %.0fs'
              % (r['hit_rate'], r['hits'], r['n'], r['elapsed']))
        # 失败分析
        bad = [x for x in r['results'] if not x['hit']]
        if bad:
            from collections import Counter
            c1 = Counter(x['category'] for x in bad)
            c2 = Counter(x['error'][:40] or '尺寸不符' for x in bad)
            print('  未命中 TOP 类别: %s' % ', '.join('%s×%d' % kv for kv in c1.most_common(6)))
            print('  失败原因: %s' % ', '.join('%s×%d' % kv for kv in c2.most_common(4)))
        print('')
        if r['hit_rate'] >= TARGET:
            print('*** 已达到目标 %.0f%%, 提前结束 ***' % TARGET)
            break

    out = {'target': TARGET, 'n_cases': len(cases),
           'source': '嘉立创FA机械设计手册 https://www.jlc-jdgf.com/mcbook/',
           'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'rounds': [{k: v for k, v in r.items() if k != 'results'} for r in rounds],
           'detail': {r['label']: r['results'] for r in rounds}}
    p = os.path.join(REPORTS, 'iterations.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('迭代记录已写出: %s' % p)
    print('')
    print('命中率变化: ' + ' -> '.join('%.1f%%' % r['hit_rate'] for r in rounds))


if __name__ == '__main__':
    main()
