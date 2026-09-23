# -*- coding: utf-8 -*-
r"""按约定的打分公式跑一遍 A 组用例 (自检用, 不参与期望值定义)。

总分 = 0.5*bbox + 0.4*holes + 0.1*pockets
  bbox  : 三轴 max(0, 1-|生成-期望|/期望) 取平均
  holes : 直径贪心配对后的 F1
  pockets: 1-|生成-期望|/max(1,期望)
仅用于定位"最容易出错"的用例, 期望值全部来自描述本身, 与程序输出无关。
用法: python tools\bench\score_A.py
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM          # noqa: E402
from ai_modeler import kernel as K             # noqa: E402
from ai_modeler import nlparser as NLP         # noqa: E402

CASES = os.path.join(ROOT, 'tools', 'bench', 'cases_A.json')


def got_features(doc):
    """从特征树里读出: bbox / 圆形切除 / 凹槽数。"""
    holes, pockets = [], 0
    for f in doc.features:
        if f.op != DM.CUT:
            continue
        p = f.params or {}
        if f.kind == 'hole':
            holes.append({'dia': float(p.get('diameter', 0.0)),
                          'through': bool(p.get('through')),
                          'count': int(p.get('count', 1))})
        elif f.kind == 'pocket':
            pockets += 1
    return holes, pockets


def exp_hole_list(hs):
    out = []
    for h in hs:
        out.extend([h['dia']] * int(h.get('count', 1)))
    return out


def greedy_f1(exp, got, tol=0.05):
    if not exp and not got:
        return 1.0
    if not exp or not got:
        return 0.0
    used = [False] * len(got)
    tp = 0
    for e in exp:
        best, bd = -1, None
        for i, g in enumerate(got):
            if used[i]:
                continue
            d = abs(g - e) / max(1e-9, e)
            if d <= tol and (bd is None or d < bd):
                best, bd = i, d
        if best >= 0:
            used[best] = True
            tp += 1
    prec = tp / float(len(got))
    rec = tp / float(len(exp))
    return 0.0 if (prec + rec) == 0 else 2 * prec * rec / (prec + rec)


def main():
    cases = json.load(open(CASES, encoding='utf-8'))
    rows = []
    for c in cases:
        e = c['expect']
        row = {'id': c['id'], 'class': c['class'], 'err': '', 'bbox': None,
               'total': 0.0, 's_bbox': 0.0, 's_holes': 0.0, 's_pockets': 0.0,
               'got_holes': None, 'got_pockets': None}
        t0 = time.time()
        res = None
        try:
            res = NLP.parse(c['text'])
            if res.ok:
                body = res.doc.build(deadline=time.time() + 20)
                bb = K.bbox(body)
                if bb:
                    row['bbox'] = [round(bb[3] - bb[0], 3), round(bb[4] - bb[1], 3),
                                   round(bb[5] - bb[2], 3)]
            else:
                row['err'] = res.message.split('\n')[0][:40]
        except Exception as ex:
            row['err'] = '%s: %s' % (type(ex).__name__, str(ex)[:40])
        gh, gp = got_features(res.doc) if res is not None else ([], 0)
        row['got_holes'] = gh
        row['got_pockets'] = gp
        if row['bbox']:
            row['s_bbox'] = sum(max(0.0, 1 - abs(g - x) / x)
                                for g, x in zip(row['bbox'], e['bbox'])) / 3.0
        row['s_holes'] = greedy_f1(exp_hole_list(e['holes']),
                                   [h['dia'] for h in gh])
        row['s_pockets'] = 1 - abs(gp - e['pockets']) / float(max(1, e['pockets']))
        row['total'] = round(0.5 * row['s_bbox'] + 0.4 * row['s_holes']
                             + 0.1 * row['s_pockets'], 3)
        row['sec'] = round(time.time() - t0, 1)
        rows.append(row)
        print('%-4s %-8s bbox期望%-22s 生成%-22s 孔期望%s 生成%s 总 %.3f %s'
              % (row['id'], row['class'], e['bbox'], row['bbox'],
                 exp_hole_list(e['holes']), [h['dia'] for h in gh],
                 row['total'], row['err']), flush=True)
    avg = sum(r['total'] for r in rows) / len(rows)
    print('\n平均总分 %.3f   bbox 平均 %.3f   holes 平均 %.3f' % (
        avg, sum(r['s_bbox'] for r in rows) / len(rows),
        sum(r['s_holes'] for r in rows) / len(rows)))
    print('最差 8 条: %s' % ' '.join('%s=%.2f' % (r['id'], r['total'])
                                    for r in sorted(rows, key=lambda r: r['total'])[:8]))
    json.dump(rows, open(os.path.join(ROOT, 'reports', 'bench_A_selfcheck.json'),
                         'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
