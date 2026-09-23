# -*- coding: utf-8 -*-
r"""定向完整重爬: 只对"标题命中目标零件类别"的页面做完整读取。

之前的全量爬取为了省流量只读了前 400KB, 结果漏掉了深藏在 1.5~3MB 页面里
的参数表 (例如深沟球轴承 GB/T276 表在 400KB 之后)。
本脚本用已爬到的标题筛出相关页, 只重爬这些页, 完整读取。

输出: kb\handbook_target.jsonl
"""
from __future__ import annotations
import json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import crawl_handbook as CH

KB = os.path.join(ROOT, 'kb')
URLS = os.path.join(ROOT, '_target_urls.json')
OUT = os.path.join(KB, 'handbook_target.jsonl')
CH.LIMIT = 4_000_000
CH.WORKERS = 10


def work(url):
    try:
        h = CH.fetch(url)
    except Exception as e:
        return {'url': url, 'error': str(e)[:80]}
    m = re.search(r'<title>(.*?)</title>', h, re.S)
    title = CH.clean(m.group(1)) if m else ''
    parts = [p for p in title.split('_') if p]
    if parts and parts[-1].startswith('嘉立创'):
        parts = parts[:-1]
    return {'url': url, 'title': title, 'path': parts,
            'grids': CH.parse_tables(h), 'bytes': len(h)}


def main():
    urls = json.load(open(URLS, encoding='utf-8'))
    done = set()
    if os.path.isfile(OUT):
        with open(OUT, encoding='utf-8') as f:
            for line in f:
                try: done.add(json.loads(line)['url'])
                except Exception: pass
    todo = [u for u in urls if u not in done]
    print('待重爬 %d / %d' % (len(todo), len(urls)), flush=True)
    lock, t0 = threading.Lock(), time.time()
    fout = open(OUT, 'a', encoding='utf-8')
    got_tab = 0
    with ThreadPoolExecutor(CH.WORKERS) as ex:
        for i, rec in enumerate(ex.map(work, todo), 1):
            with lock:
                fout.write(json.dumps(rec, ensure_ascii=False) + '\n')
                if rec.get('grids'): got_tab += 1
                if i % 50 == 0:
                    fout.flush()
                    el = time.time() - t0
                    print('  %d/%d  有表格 %d  已用 %.1f 分  预计还需 %.1f 分'
                          % (i, len(todo), got_tab, el/60,
                             (len(todo)-i)/max(i/el,1e-9)/60), flush=True)
    fout.close()
    print('完成, 用时 %.1f 分钟, 有表格 %d 页' % ((time.time()-t0)/60, got_tab), flush=True)


if __name__ == '__main__':
    main()
