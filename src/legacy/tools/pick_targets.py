# -*- coding: utf-8 -*-
r"""从已爬到的标题里挑出需要完整重爬的页面 (标题命中目标零件类别)。"""
from __future__ import annotations
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import build_kb as B

SRC = os.path.join(ROOT, 'kb', 'handbook_grid.jsonl')
OUT = os.path.join(ROOT, '_target_urls.json')


def main():
    pats = [k for _n, kws, _s in B.CATEGORIES for k in kws]
    pages = []
    with open(SRC, encoding='utf-8') as f:
        for line in f:
            try:
                pages.append(json.loads(line))
            except Exception:
                pass
    cand = []
    for p in pages:
        text = ' '.join(p.get('path') or []) + ' ' + (p.get('title') or '')
        if any(k in text for k in pats):
            cand.append(p['url'])
    json.dump(cand, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('候选页 %d (总 %d)' % (len(cand), len(pages)))
    from collections import Counter
    c = Counter()
    for p in pages:
        text = ' '.join(p.get('path') or []) + ' ' + (p.get('title') or '')
        for name, kws, _s in B.CATEGORIES:
            if any(k in text for k in kws):
                c[name] += 1
    print('类别 -> 页数:')
    for k, v in c.most_common(40):
        print('   %-16s %d' % (k, v))


if __name__ == '__main__':
    main()
