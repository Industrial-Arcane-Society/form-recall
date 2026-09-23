# -*- coding: utf-8 -*-
r"""爬取「嘉立创FA机械设计手册」的参数表 (支持 HTML 合并单元格)。

源站: https://www.jlc-jdgf.com/mcbook/  公开在线机械设计手册 (GB/T 等标准件参数)

关键: 手册表格大量使用 rowspan/colspan 做多行表头, 朴素的按行取值会错位。
      本版先把表格展开成规整的二维网格再保存, 后续分析无需重新爬取。

输出: kb\handbook_grid.jsonl   每行一页 {url,title,path,grids:[{grid,nrows,ncols}]}
可断点续爬。
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, 'kb')
URLS = os.path.join(ROOT, '_mcbook_urls.json')
OUT = os.path.join(KB, 'handbook_grid.jsonl')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    'Chrome/124.0 Safari/537.36', 'Accept-Language': 'zh-CN,zh;q=0.9'}
LIMIT = 400_000
WORKERS = 8

_tag = re.compile(r'<[^>]+>')
_ws = re.compile(r'\s+')
_CELL = re.compile(r'<t([dh])([^>]*)>(.*?)</t\1>', re.S | re.I)
_TR = re.compile(r'<tr.*?</tr>', re.S | re.I)
_TB = re.compile(r'<table.*?</table>', re.S | re.I)
_CS = re.compile(r'colspan\s*=\s*["\']?\s*(\d+)', re.I)
_RS = re.compile(r'rowspan\s*=\s*["\']?\s*(\d+)', re.I)
_isnum = re.compile(r'^-?\d+(?:\.\d+)?$')


def clean(s):
    s = _tag.sub(' ', s or '')
    s = _html.unescape(s)
    return _ws.sub(' ', s.replace('\xa0', ' ')).strip()


def expand_table(tb: str):
    """把 HTML 表格展开成规整二维网格 (处理 colspan/rowspan)。"""
    trs = _TR.findall(tb)
    if not trs:
        return []
    grid = []
    pending = {}          # (row, col) -> [text, 剩余行数]
    for r, tr in enumerate(trs):
        row = []
        col = 0
        for m in _CELL.finditer(tr):
            attrs = m.group(2) or ''
            text = clean(m.group(3))
            cs = int(_CS.search(attrs).group(1)) if _CS.search(attrs) else 1
            rs = int(_RS.search(attrs).group(1)) if _RS.search(attrs) else 1
            # 先填上被上面 rowspan 占掉的格子
            while (r, col) in pending:
                row.append(pending[(r, col)][0])
                col += 1
            for k in range(cs):
                row.append(text)
                if rs > 1:
                    pending[(r, col)] = [text, rs - 1]
                col += 1
        while (r, col) in pending:
            row.append(pending[(r, col)][0])
            col += 1
        grid.append(row)
        # 递减 rowspan 计数, 到期的移除
        for key in [k for k in pending if k[0] == r]:
            pending[key][1] -= 1
            if pending[key][1] <= 0:
                del pending[key]
    # 统一列宽
    w = max((len(r) for r in grid), default=0)
    return [r + [''] * (w - len(r)) for r in grid if r]


def parse_tables(h):
    out = []
    for tb in _TB.findall(h):
        g = expand_table(tb)
        if len(g) < 3:
            continue
        nums = sum(1 for r in g for c in r if _isnum.match(c))
        if nums < 6:
            continue
        out.append({'grid': g, 'nrows': len(g), 'ncols': len(g[0]),
                    'nums': nums})
    out.sort(key=lambda t: -t['nums'])
    return out


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        buf, got = [], 0
        while got < LIMIT:
            c = r.read(65536)
            if not c:
                break
            buf.append(c)
            got += len(c)
    return b''.join(buf).decode('utf-8', 'replace')


def work(url):
    try:
        h = fetch(url)
    except Exception as e:
        return {'url': url, 'error': str(e)[:80]}
    m = re.search(r'<title>(.*?)</title>', h, re.S)
    title = clean(m.group(1)) if m else ''
    parts = [p for p in title.split('_') if p]
    if parts and parts[-1].startswith('嘉立创'):
        parts = parts[:-1]
    return {'url': url, 'title': title, 'path': parts,
            'grids': parse_tables(h)}


def main():
    os.makedirs(KB, exist_ok=True)
    urls = json.load(open(URLS, encoding='utf-8'))
    done = set()
    if os.path.isfile(OUT):
        with open(OUT, encoding='utf-8') as f:
            for line in f:
                try:
                    done.add(json.loads(line)['url'])
                except Exception:
                    pass
    todo = [u for u in urls if u not in done]
    print('总 %d, 已完成 %d, 待爬 %d' % (len(urls), len(done), len(todo)), flush=True)
    lock = threading.Lock()
    t0 = time.time()
    fout = open(OUT, 'a', encoding='utf-8')
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, rec in enumerate(ex.map(work, todo), 1):
            with lock:
                fout.write(json.dumps(rec, ensure_ascii=False) + '\n')
                if i % 100 == 0:
                    fout.flush()
                    el = time.time() - t0
                    print('  %d/%d  %.0f 页/分  已用 %.1f 分  预计还需 %.1f 分'
                          % (i, len(todo), i / el * 60, el / 60,
                             (len(todo) - i) / max(i / el, 1e-9) / 60), flush=True)
    fout.close()
    print('完成, 用时 %.1f 分钟' % ((time.time() - t0) / 60), flush=True)


if __name__ == '__main__':
    main()
