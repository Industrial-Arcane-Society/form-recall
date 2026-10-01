# -*- coding: utf-8 -*-
"""联网检索模块 —— 本地词库不认识时, 去搜索引擎找答案。

只用 Python 标准库 (urllib + re), 不需要任何 API Key。

工作方式:
  1. 把用户的中文描述丢给 Bing 搜索
  2. 解析出标题 / 摘要 / 链接
  3. 从摘要里用启发式规则抠出零件的标准参数 (直径 / 长度 / 厚度 / 齿数 ...)
  4. 拼成一条本程序能识别的中文描述, 交给内置解析引擎去建模

如果配置了大模型, 还会把搜索结果连同原问题一起交给大模型,
让它直接产出特征 JSON —— 那条路质量高得多 (RAG)。
"""
from __future__ import annotations
from ai_modeler.paths import data_path

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

CACHE_PATH = str(data_path('.cache', 'online.json'))


def _load_cache():
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


_CACHE = _load_cache()


def _save_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept': 'text/html,application/xhtml+xml',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


_TAG = re.compile(r'<[^>]+>')
_SCRIPT = re.compile(r'<(script|style)[^>]*>.*?</\1>', re.S | re.I)


def _text(fragment):
    s = _TAG.sub(' ', fragment or '')
    s = html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def key_terms(phrase: str):
    """把描述拆成关键词: 中文取 2 字滑窗, 另外提取 M8 / 6204 这类型号。"""
    terms = set()
    for run in re.findall(r'[一-鿿]{2,}', phrase):
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
        if len(run) <= 4:
            terms.add(run)
    codes = set(re.findall(r'[A-Za-z]{0,3}d{2,}[A-Za-z]*', phrase))
    return terms, codes


def relevant(item, terms, codes):
    """相关性判断。

    关键点: 只要描述里有中文关键词, 就必须命中中文词才算相关。
    否则 "M8" 这种型号会把"问界 M8 汽车"也判成相关内容。
    """
    text = (item.get('title') or '') + ' ' + (item.get('snippet') or '')
    if terms:
        return any(t in text for t in terms)
    if codes:
        return any(c in text for c in codes)
    return True


def fetch_page_text(url, timeout=6, limit=4000):
    """抓一个网页的正文纯文本, 用于从详情页里抠参数。"""
    try:
        page = _fetch(url, timeout)
    except Exception:
        return ''
    page = _SCRIPT.sub(' ', page)
    txt = _text(page)
    return txt[:limit]


def search(query: str, timeout: int = 8, use_cache: bool = True):
    """搜索并返回 [{'title','snippet','url'}, ...]。失败返回空列表。"""
    q = (query or '').strip()
    if not q:
        return []
    key = q.lower()
    if use_cache and key in _CACHE:
        return _CACHE[key]

    url = 'https://www.bing.com/search?' + urllib.parse.urlencode(
        {'q': q, 'setlang': 'zh-CN', 'count': '12'})
    try:
        page = _fetch(url, timeout)
    except Exception:
        return []

    page = _SCRIPT.sub(' ', page)
    results = []
    # Bing 的每条结果大致是 <li class="b_algo"> ... <h2><a href=..>标题</a></h2> ... <p>摘要</p>
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        link = html.unescape(m.group(1))
        title = _text(m.group(2))
        if not title:
            continue
        snip = ''
        for pm in re.finditer(r'<p[^>]*>(.*?)</p>', block, re.S):
            cand = _text(pm.group(1))
            if len(cand) > len(snip):
                snip = cand
        results.append({'title': title, 'snippet': snip, 'url': link})
        if len(results) >= 10:
            break

    if results:
        _CACHE[key] = results
        _save_cache()
    return results


# --------------------------------------------------------------------------
# 从搜索结果里抠参数
# --------------------------------------------------------------------------

_NUM = r'(\d+(?:\.\d+)?)'
_UNIT = r'\s*(毫米|mm|厘米|cm|米|m|英寸|寸|度|°)?'

# 关键词 -> 本程序能识别的说法
PARAM_WORDS = [
    ('直径', (r'直径', r'外径', r'孔径', r'螺纹直径', r'大径', r'公称直径', r'⌀', r'Φ', r'φ')),
    ('内径', (r'内径', r'通径', r'小径')),
    ('长度', (r'长度', r'总长', r'全长', r'螺杆长度', r'长')),
    ('宽度', (r'宽度', r'宽', r'对边宽度', r'对边')),
    ('高度', (r'高度', r'厚度', r'厚', r'总高', r'头部高度', r'高')),
    ('齿数', (r'齿数',)),
    ('模数', (r'模数',)),
]

_UNIT_MUL = {'厘米': 10, 'cm': 10, '米': 1000, 'm': 1000, '英寸': 25.4, '寸': 25.4}


def _to_mm(v, unit):
    return v * _UNIT_MUL.get(unit or '', 1.0)


def extract_params(snippets):
    """从一堆文本里抠出 {关键词: 值} ，取出现次数最多的那个值。"""
    joined = ' '.join(snippets)
    tally = {}
    for (name, words) in PARAM_WORDS:
        pat = '|'.join(words)
        for m in re.finditer('(?:' + pat + r')' + r'\s*(?:为|是|=|:|：)?\s*' + _NUM + _UNIT, joined):
            try:
                val = _to_mm(float(m.group(1)), m.group(2))
            except (TypeError, ValueError):
                continue
            if val <= 0 or val > 100000:
                continue
            tally.setdefault(name, {})
            tally[name][round(val, 3)] = tally[name].get(round(val, 3), 0) + 1
    out = {}
    for name, counts in tally.items():
        out[name] = max(counts.items(), key=lambda kv: kv[1])[0]
    return out


# 常见标准件的“补充常识”: 搜索引擎未必直接给出, 但工程师都知道
STD_HINTS = {
    '内六角': {'头径系数': 1.5, '头高系数': 1.0, '杆长默认': 3.0},
    '螺栓': {'头径系数': 1.5, '头高系数': 1.0, '杆长默认': 3.0},
    '螺钉': {'头径系数': 1.6, '头高系数': 1.0, '杆长默认': 3.0},
    '螺母': {'厚系数': 0.8, '对边系数': 1.6},
    '垫圈': {'厚系数': 0.2, '外径系数': 2.2},
    '轴承': {},
    '法兰': {},
    '齿轮': {},
}


def guess_std(name, dia):
    for key, hint in STD_HINTS.items():
        if key in name:
            return key, hint
    return None, {}


def build_candidate(phrase: str, results, extra_texts=None):
    """把搜索结果拼成一条本程序能识别的描述, 返回 (描述, 依据文本) 或 (None, 原因)。"""
    if not results:
        return None, '没有搜索到结果'
    snippets = [r['snippet'] for r in results if r['snippet']]
    titles = [r['title'] for r in results]
    if extra_texts:
        snippets = snippets + list(extra_texts)
    params = extract_params(snippets + titles)
    why = []
    for k, v in params.items():
        why.append('%s≈%g' % (k, v))

    m = re.search(r'(?<![A-Za-z0-9])[Mm]\s*(\d+(?:\.\d+)?)(?![0-9])', phrase)
    if m:
        # 螺纹公称直径是最权威的, 直接覆盖网页里抓来的杂值
        params['直径'] = float(m.group(1))
        why.append('公称直径 M%s (以此为准)' % m.group(1))

    # 轴承型号 6204 -> 内径 = 04 × 5 = 20mm
    mb = re.search(r'(?<![0-9])([23678])([0-9])([0-9]{2})(?![-0-9])', phrase)
    if mb and '轴承' in phrase and '内径' not in params and '直径' not in params:
        tail = int(mb.group(3))
        bore = 10.0 if tail == 0 else (12.0 if tail == 1 else (15.0 if tail == 2
                else (17.0 if tail == 3 else tail * 5.0)))
        params['内径'] = bore
        why.append('按轴承型号 %s 推算内径 %gmm' % (mb.group(0), bore))

    # 铝型材 4040 / 2020 -> 截面 40x40
    mp = re.search(r'(?<![0-9])([0-9]{2})([0-9]{2})(?![0-9])', phrase)
    if mp and ('型材' in phrase or '方管' in phrase):
        a, b = int(mp.group(1)), int(mp.group(2))
        if 10 <= a <= 200 and 10 <= b <= 200:
            why.append('按型材截面 %d×%d 估算 (壁厚 2mm, 默认长度 100mm, 用圆管近似)'
                       % (a, b))
            return '套筒 外径%g 内径%g 长100' % (a, max(1, a - 4)), '; '.join(why)

    dia = params.get('直径') or params.get('内径')
    if not dia:
        return None, ('搜索结果里没有找到可用的尺寸参数。'
                      '（搜到 %d 条结果，但没有带单位的直径/长度等信息）' % len(results))

    std, hint = guess_std(phrase, dia)
    length = params.get('长度') or params.get('高度') or round(dia * hint.get('杆长默认', 3.0), 2)
    head_d = round(dia * hint.get('头径系数', 1.0), 2)
    head_h = round(dia * hint.get('头高系数', 1.0), 2)

    if std in ('内六角', '螺栓', '螺钉'):
        desc = ('圆柱 直径%g 高%g, 再在顶上合并一个圆柱 直径%g 高%g'
                % (dia, length, head_d, head_h))
        # 本程序没有"合并两个圆柱"的模板, 退化成能建出来的形状
        desc = '阶梯轴 直径%g 长%g, 直径%g 长%g' % (dia, length, head_d, head_h)
        why.append('按%s估算: 杆径%g 杆长%g 头径%g 头高%g'
                   % (std, dia, length, head_d, head_h))
    elif std == '螺母':
        desc = '套筒 外径%g 内径%g 长%g' % (round(dia * hint.get('对边系数', 1.6), 2),
                                            dia, round(dia * hint.get('厚系数', 0.8), 2))
        why.append('按%s估算' % std)
    elif std == '垫圈':
        desc = '套筒 外径%g 内径%g 长%g' % (round(dia * hint.get('外径系数', 2.2), 2),
                                            dia, round(dia * hint.get('厚系数', 0.2), 2))
        why.append('按%s估算' % std)
    else:
        parts = []
        if '直径' in params:
            parts.append('直径%g' % params['直径'])
        if '内径' in params:
            parts.append('内径%g' % params['内径'])
        parts.append('长%g' % length)
        desc = '套筒 ' + ' '.join(parts)
        why.append('按搜到的尺寸估算')

    return desc, '; '.join(why)


def ask(phrase, timeout=8, deep=True):
    """一站式: 搜索 -> 过滤 -> 抠参数 -> 给出建议描述。

    返回 dict: {'ok', 'results', 'suggestion', 'reason', 'query', 'notes'}
    """
    q = phrase.strip()
    notes = []
    terms, codes = key_terms(q)

    tried = []
    results = []
    for suffix in (' 标准 规格 尺寸表 mm', ' 规格参数 mm', ' 尺寸', ''):
        query = q + suffix
        if query.strip() in tried:
            continue
        tried.append(query.strip())
        raw = search(query, timeout)
        if not raw:
            continue
        keep = [r for r in raw if relevant(r, terms, codes)]
        if len(keep) >= 2:
            results = keep
            notes.append('查询「%s」命中 %d 条相关结果 (共 %d 条)' % (query, len(keep), len(raw)))
            break
        if not results:
            results = raw
            notes.append('查询「%s」命中 %d 条 (相关性过滤后不足, 保留原始结果)'
                         % (query, len(keep)))
    if not results:
        return {'ok': False, 'results': [], 'suggestion': None,
                'reason': '搜索没有返回结果 (网络不可用或被拦截)', 'query': q, 'notes': notes}

    snippets = [r['snippet'] for r in results if r['snippet']]
    titles = [r['title'] for r in results]

    # 再抓前两个详情页的正文, 参数通常在页面里而不在摘要里
    if deep:
        for r in results[:2]:
            body = fetch_page_text(r['url'], timeout)
            if body:
                snippets.append(body)
                notes.append('已读取详情页: %s' % (r['title'][:30] or r['url'][:40]))

    params = extract_params(snippets + titles)
    if params:
        notes.append('从网页中提取到参数: ' +
                     ', '.join('%s=%g' % (k, v) for k, v in params.items()))

    suggestion, reason = build_candidate(q, results, snippets)
    return {'ok': bool(suggestion), 'results': results, 'suggestion': suggestion,
            'reason': reason, 'query': q, 'notes': notes, 'params': params}


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    for p in (sys.argv[1:] or ['M8 内六角螺栓']):
        r = ask(p)
        print('查询: %s' % p)
        print('  结果 %d 条' % len(r['results']))
        for x in r['results'][:3]:
            print('   - %s' % x['title'][:60])
            print('     %s' % x['snippet'][:110])
        print('  建议: %s' % r['suggestion'])
        print('  依据: %s' % r['reason'])
