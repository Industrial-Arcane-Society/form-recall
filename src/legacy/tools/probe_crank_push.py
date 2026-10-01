# -*- coding: utf-8 -*-
r"""曲轴推送失败: 变体对照实验(真机 CATIA)。

背景: 「曲轴 主轴颈50 连杆颈42 行程86 拐数6」展开成 25 个圆盘凸台, 推到 CATIA
在第 12 个操作上 Part.Update 失败, 之后 25-12+1 个操作全部连锁失败。

三个变体各推一次(每次新建一个零件文档, 不保存):
  base  原样重放        -> 期望复现「第 12 个失败」
  grow  每个凸台两端各外扩 0.25mm(1µm 搭接 -> 0.5mm 搭接)
  webrc 曲柄臂半径 64.0001 -> 66(把 0.1µm 的"针尖相切"拉开)

用法: python tools\probe_crank_push.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP                       # noqa: E402
from ai_modeler.catia_backend import catia_export as CE      # noqa: E402

PROMPT = '曲轴 主轴颈50 连杆颈42 行程86 拐数6'
OUT = os.path.join(ROOT, 'artifacts', 'crank_probe')


class _Doc:
    name = 'CrankProbe'
    source_prompt = PROMPT


def variants(base):
    out = {'base': [dict(e) for e in base]}

    grow = []
    for e in base:
        n = dict(e)
        if str(n['kind']).endswith('_pad'):
            n['z'] = round(n['z'] - 0.25, 6)
            n['h'] = round(n['h'] + 0.5, 6)
        grow.append(n)
    out['grow'] = grow

    web = []
    for e in base:
        n = dict(e)
        if abs(float(n.get('r', 0.0)) - 64.0001) < 0.01:
            n['r'] = 66.0
        web.append(n)
    out['webrc'] = web
    return out


def run_one(tag, entries):
    vbs = os.path.join(OUT, 'push_%s.vbs' % tag)
    log = os.path.join(OUT, 'push_%s.log' % tag)
    if os.path.exists(log):
        os.remove(log)
    text = CE.render_push_vbs(_Doc(), entries, [], out_catpart='', start_cmd='',
                              wait_sec=180, log_path=log)
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(text)
    t0 = time.time()
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, text=True,
                       errors='replace', timeout=600)
    dt = time.time() - t0
    raw = open(log, 'rb').read() if os.path.exists(log) else b''
    txt = raw.decode('gbk', 'replace')
    steps = re.findall(r'\[(\d+)/(\d+)\]', txt)
    done = re.search(r'features done: ok=(-?\d+) fail=(-?\d+)', txt)
    cur, fails = '?', []
    for line in txt.splitlines():
        m = re.match(r'\s*\S+\s+\[(\d+)/\d+\]', line)
        if m:
            cur = m.group(1)
        if 'FAILED:' in line:
            fails.append((cur, line.split('FAILED:', 1)[1].strip()))
    print('== %-6s %5.1fs  steps=%d  %s' % (tag, dt, len(steps),
                                            done.group(0) if done else 'NO_DONE'))
    print('   cscript rc=%s out=%r' % (p.returncode, (p.stdout or '')[:200]))
    for m in fails[:3]:
        print('   fail at [%s] -> %s' % m)
    return txt


def main():
    os.makedirs(OUT, exist_ok=True)
    doc = NLP.parse(PROMPT).doc
    base, warns = CE.build_catia_features(doc)
    print('baseline features=%d' % len(base))
    print('  z/kind/r:', [(e['kind'], e['z'], e.get('r')) for e in base][:6], '...')
    for tag, entries in variants(base).items():
        run_one(tag, entries)
    return 0


if __name__ == '__main__':
    sys.exit(main())
