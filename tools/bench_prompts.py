# -*- coding: utf-8 -*-
r"""100 条中文提示词的自动评测: 生成模型 vs 期望规格。

相似度口径 (与 tools\bench\score_A.py / score_B.py 完全一致, 便于对拍):
    总分 = 0.5*bbox + 0.4*holes + 0.1*pockets
      bbox  : 三轴各 max(0, 1-|生成-期望|/期望) 取平均
      holes : 按直径贪心配对(相对误差≤5%)后的 F1
      pockets: 1 - |生成-期望| / max(1, 期望)
期望值来自 tools\bench\cases_A.json / cases_B.json —— 由"这段描述的工程意图"
推导, 不是程序输出。生成值怎么量: 走真实链路
    描述 -> nlparser/partlib -> 特征树 -> 内核建模 -> 包围盒 + 圆孔/凹槽特征。

用法:
    python tools\bench_prompts.py                 全部 100 条 + 汇总
    python tools\bench_prompts.py --min 0.80      带门限(不达标退出码 1)
    python tools\bench_prompts.py --worst 15      列出最差 15 条
    python tools\bench_prompts.py --class 盘类     只看某一类
    python tools\bench_prompts.py -v              逐条打印
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM          # noqa: E402
from ai_modeler import kernel as K             # noqa: E402
from ai_modeler import nlparser as NLP         # noqa: E402

BENCH = os.path.join(ROOT, 'tools', 'bench')
DEADLINE_SEC = 20.0


def load_cases():
    cases = []
    for fn in ('cases_A.json', 'cases_B.json'):
        path = os.path.join(BENCH, fn)
        if os.path.isfile(path):
            cases.extend(json.load(open(path, encoding='utf-8')))
    return cases


def measure(doc):
    """从特征树量出: 圆孔列表(按实际孔数展开) / 凹槽数。

    注意孔数要算对: 一个 hole 特征可能代表 4 个角孔(corners)或 N 个均布孔(pcd),
    否则"四角各一个Ø8通孔"会被量成 1 个孔, 明明做对了却判成漏孔(实测踩过)。
    """
    holes, pockets = [], 0
    for f in doc.features:
        if f.op != DM.CUT:
            # 零件库件把孔做在特征内部(连杆大头/小头孔、转子中心孔、齿轮 bore_d…),
            # 它们同样算孔 —— 不数就会把"做对了"判成"漏孔"(实测 B29/B27 就这样被扣分)
            p = f.params or {}
            for key in ('bore_d', 'big_bore', 'small_bore', 'hub_bore', 'center_bore'):
                v = p.get(key)
                if v:
                    holes.append(float(v))
            continue
        p = f.params or {}
        if f.kind == 'hole':
            n = int(p.get('count', 1) or 1)
            pos = p.get('pos', 'center')
            if pos == 'corners':
                n = max(n, 4)
            elif pos == 'pcd':
                n = max(n, int(p.get('n', 0) or 0), 4 if not p.get('count') else n)
            holes.extend([float(p.get('diameter', 0.0))] * max(1, n))
        elif f.kind == 'pocket':
            pockets += 1
    return holes, pockets


def greedy_f1(exp, got, tol=0.05):
    if not exp and not got:
        return 1.0
    if not exp or not got:
        return 0.0
    used, tp = [False] * len(got), 0
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


def exp_holes(hs):
    out = []
    for h in hs or []:
        out.extend([float(h['dia'])] * int(h.get('count', 1) or 1))
    return out


def run_case(c):
    e = c['expect']
    row = {'id': c['id'], 'class': c['class'], 'text': c['text'],
           'bbox': None, 'got_holes': [], 'got_pockets': 0, 'err': '',
           'kinds': [], 'note': '', 'like': ''}
    res = None
    t0 = time.time()
    try:
        res = NLP.parse(c['text'])
    except Exception as ex:                       # 解析器自己崩了也要记下来
        row['err'] = '%s: %s' % (type(ex).__name__, str(ex)[:60])
    if res is not None:
        row['like'] = ' '.join(res.steps[:2])
        if res.ok:
            try:
                bb = K.bbox(res.doc.build(deadline=time.time() + DEADLINE_SEC))
                if bb:
                    row['bbox'] = [round(bb[3] - bb[0], 3), round(bb[4] - bb[1], 3),
                                   round(bb[5] - bb[2], 3)]
            except Exception as ex:
                row['err'] = '建模: %s' % str(ex)[:60]
        else:
            row['err'] = (res.message or '').split('\n')[0][:52]
        row['got_holes'], row['got_pockets'] = measure(res.doc)
        row['kinds'] = [f.kind for f in res.doc.features]
    row['sec'] = round(time.time() - t0, 1)

    eb = e['bbox']
    row['s_bbox'] = (sum(max(0.0, 1 - abs(g - x) / x) for g, x in zip(row['bbox'], eb)) / 3.0
                     if row['bbox'] else 0.0)
    row['s_holes'] = greedy_f1(exp_holes(e.get('holes')), row['got_holes'])
    ep = int(e.get('pockets', 0) or 0)
    row['s_pockets'] = 1 - abs(row['got_pockets'] - ep) / float(max(1, ep))
    row['total'] = round(0.5 * row['s_bbox'] + 0.4 * row['s_holes']
                         + 0.1 * row['s_pockets'], 3)
    row['exp'] = eb
    return row


def classify(row):
    """失败归因, 用来决定下一步改哪里。"""
    if row['err'].startswith('RecursionError'):
        return '解析器崩溃'
    if not row['bbox'] and row['err']:
        return '没解析出模型'
    if not row['bbox']:
        return '解析了但建模为空'
    if row['s_bbox'] < 0.5:
        return '外形尺寸不对'
    if row['s_holes'] < 0.5:
        return '孔不对'
    if row['s_pockets'] < 0.9:
        return '槽不对'
    return 'OK'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min', type=float, default=None, help='平均相似度门限')
    ap.add_argument('--worst', type=int, default=10)
    ap.add_argument('--class', dest='klass', default=None)
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--id', default=None)
    a = ap.parse_args()

    cases = load_cases()
    if a.klass:
        cases = [c for c in cases if a.klass in c['class']]
    if a.id:
        cases = [c for c in cases if c['id'] in a.id.split(',')]
    if not cases:
        print('没有匹配的用例'); return 2

    rows = []
    for c in cases:
        r = run_case(c)
        rows.append(r)
        if a.verbose:
            print('%-4s %-9s %.3f  bbox期望%-20s 生成%-20s 孔%s/%s %s'
                  % (r['id'], r['class'], r['total'], r['exp'], r['bbox'],
                     exp_holes(c['expect'].get('holes')), r['got_holes'],
                     r['err'] or r['like'][:34]), flush=True)

    n = len(rows)
    avg = sum(r['total'] for r in rows) / n
    print('')
    print('  ' + '=' * 66)
    print('  用例 %d 条   平均相似度 %.3f' % (n, avg))
    print('  分项: bbox %.3f   holes %.3f   pockets %.3f' % (
        sum(r['s_bbox'] for r in rows) / n, sum(r['s_holes'] for r in rows) / n,
        sum(r['s_pockets'] for r in rows) / n))
    from collections import Counter
    causes = Counter(classify(r) for r in rows)
    print('  失败归因: ' + '  '.join('%s=%d' % (k, v) for k, v in causes.most_common()))
    by_class = {}
    for r in rows:
        by_class.setdefault(r['class'], []).append(r['total'])
    print('  分类别: ' + '  '.join('%s=%.3f(%d)' % (k, sum(v) / len(v), len(v))
                                  for k, v in sorted(by_class.items())))
    worst = sorted(rows, key=lambda r: r['total'])[:a.worst]
    print('  最差 %d 条:' % len(worst))
    for r in worst:
        print('    %-4s %-9s %.3f  %-30s 期望%s 生成%s %s'
              % (r['id'], r['class'], r['total'], r['text'][:30], r['exp'],
                 r['bbox'], (r['err'] or '')[:40]))
    os.makedirs(os.path.join(ROOT, 'reports'), exist_ok=True)
    out = os.path.join(ROOT, 'reports', 'bench_prompts.json')
    json.dump(rows, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('  明细: %s' % out)
    print('  ' + '=' * 66)
    if a.min is not None and avg < a.min:
        print('  ✗ 未达门限 %.2f' % a.min)
        return 1
    print('  ✓ 达到门限 %s' % (('%.2f' % a.min) if a.min else '(未设门限)'))
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
