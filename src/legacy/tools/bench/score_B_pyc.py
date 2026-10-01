# -*- coding: utf-8 -*-
r"""对照实验: 用 11:52 的 partlib .pyc (损坏前) 顶替当前 partlib.py, 重跑 B 组打分。

只读实验, 不修改任何项目文件。目的: 区分"用例本身难"与"partlib.py 被写坏导致零件库失效"。
用法: python tools\bench\score_B_pyc.py [pyc路径]
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

PYC = (sys.argv[1] if len(sys.argv) > 1 else
       os.path.join(ROOT, 'ai_modeler', '__pycache__', 'partlib.cpython-314.pyc'))

# ---- 先把 pyc 版的 partlib 塞进 sys.modules, 让 nlparser 的 `from . import partlib` 命中它 ----
import ai_modeler                                    # noqa: E402
spec = importlib.util.spec_from_file_location(
    'ai_modeler.partlib', PYC,
    loader=importlib.machinery.SourcelessFileLoader('ai_modeler.partlib', PYC))
mod = importlib.util.module_from_spec(spec)
sys.modules['ai_modeler.partlib'] = mod
spec.loader.exec_module(mod)
setattr(ai_modeler, 'partlib', mod)
print('已加载 %s -> %d 个零件类别' % (os.path.basename(PYC), len(mod.PARTS)))

from ai_modeler import docmodel as DM                # noqa: E402
from ai_modeler import kernel as K                   # noqa: E402
from ai_modeler import nlparser as NLP               # noqa: E402

CASES = os.path.join(ROOT, 'tools', 'bench', 'cases_B.json')


def got_features(doc):
    holes, pockets = [], 0
    for f in doc.features:
        if f.op != DM.CUT:
            continue
        p = f.params or {}
        if f.kind == 'hole':
            holes.append(float(p.get('diameter', 0.0)))
        elif f.kind == 'pocket':
            pockets += 1
    return holes, pockets


def exp_holes(hs):
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
    prec, rec = tp / float(len(got)), tp / float(len(exp))
    return 0.0 if (prec + rec) == 0 else 2 * prec * rec / (prec + rec)


def main():
    cases = json.load(open(CASES, encoding='utf-8'))
    rows = []
    for c in cases:
        e = c['expect']
        t0 = time.time()
        res, bb, err = None, None, ''
        try:
            res = NLP.parse(c['text'])
            if res.ok:
                body = res.doc.build(deadline=time.time() + 20)
                b = K.bbox(body)
                if b:
                    bb = [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)]
            else:
                err = res.message.split('\n')[0][:30]
        except Exception as ex:
            err = '%s: %s' % (type(ex).__name__, str(ex)[:30])
        gh, gp = got_features(res.doc) if res is not None else ([], 0)
        s_bb = (sum(max(0.0, 1 - abs(g - x) / x) for g, x in zip(bb, e['bbox'])) / 3.0
                if bb else 0.0)
        s_h = greedy_f1(exp_holes(e['holes']), gh)
        s_p = 1 - abs(gp - e['pockets']) / float(max(1, e['pockets']))
        tot = round(0.5 * s_bb + 0.4 * s_h + 0.1 * s_p, 3)
        rows.append({'id': c['id'], 'total': tot, 's_bbox': round(s_bb, 3),
                     's_holes': round(s_h, 3), 's_pockets': round(s_p, 3),
                     'exp_bbox': e['bbox'], 'got_bbox': bb,
                     'exp_holes': exp_holes(e['holes']), 'got_holes': gh,
                     'exp_pockets': e['pockets'], 'got_pockets': gp, 'err': err,
                     'sec': round(time.time() - t0, 1)})
        print('%-4s 总%.3f bbox%.2f 孔%.2f 槽%.2f | 期望%-22s 生成%-22s | 孔期望%-24s 生成%-20s %s'
              % (rows[-1]['id'], tot, s_bb, s_h, s_p, e['bbox'], bb,
                 exp_holes(e['holes']), gh, err), flush=True)
    n = len(rows)
    print('\n平均总分 %.3f (bbox %.3f / holes %.3f / pockets %.3f)' % (
        sum(r['total'] for r in rows) / n,
        sum(r['s_bbox'] for r in rows) / n,
        sum(r['s_holes'] for r in rows) / n,
        sum(r['s_pockets'] for r in rows) / n))
    print('总分 0 或解析失败: %s' % ' '.join(
        '%s=%.2f' % (r['id'], r['total']) for r in rows if r['total'] <= 0.35))
    print('最差 10: %s' % ' '.join('%s=%.2f' % (r['id'], r['total'])
                                   for r in sorted(rows, key=lambda r: r['total'])[:10]))
    print('最好 10: %s' % ' '.join('%s=%.2f' % (r['id'], r['total'])
                                   for r in sorted(rows, key=lambda r: -r['total'])[:10]))
    json.dump(rows, open(os.path.join(ROOT, 'reports', 'bench_B_selfcheck_pyc.json'),
                         'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('已存 reports\\bench_B_selfcheck_pyc.json')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
