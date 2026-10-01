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
from ai_modeler.paths import temp_path

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import socket
import base64

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

CACHE_PATH = str(temp_path('cache', 'online.json'))


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


def fetch_page_text_detailed(url, timeout=8, limit=12000):
    """Read a result page with enough diagnostics to distinguish fetch and parse failures."""
    started = time.time()
    try:
        page = _fetch(url, timeout)
    except Exception as exc:
        diagnostic = _search_error(exc)
        diagnostic.update({'source_url': url, 'duration_seconds': round(time.time()-started, 3),
                           'body_chars': 0})
        return {'text': '', 'diagnostics': diagnostic}
    text = _text(_SCRIPT.sub(' ', page))[:max(0, int(limit))]
    status = 'ok' if text.strip() else 'empty_body'
    return {'text': text, 'diagnostics': {
        'status': status, 'source_url': url, 'http_response': True,
        'response_chars': len(page), 'body_chars': len(text),
        'content_sha256': __import__('hashlib').sha256(text.encode('utf-8')).hexdigest(),
        'duration_seconds': round(time.time()-started, 3),
    }}


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, 'reason', None)
    return isinstance(reason, (TimeoutError, socket.timeout))


def _search_error(exc: BaseException) -> dict:
    if isinstance(exc, urllib.error.HTTPError):
        return {'status': 'http_error', 'http_status': int(exc.code),
                'error': 'HTTP %s' % exc.code}
    reason = getattr(exc, 'reason', None)
    if isinstance(exc, urllib.error.URLError) and reason is not None:
        exc = reason
    if _is_timeout_error(exc):
        return {'status': 'timeout', 'error': 'request timed out'}
    return {'status': 'network_error',
            'error': str(exc)[:300] or type(exc).__name__}


def _decode_ddg_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(html.unescape(url))
    if parsed.hostname and parsed.hostname.lower().endswith('duckduckgo.com'):
        redirected = urllib.parse.parse_qs(parsed.query).get('uddg')
        if redirected:
            return redirected[0]
    return html.unescape(url)


def _decode_bing_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(html.unescape(url))
    if parsed.hostname and parsed.hostname.lower().endswith('bing.com'):
        encoded = urllib.parse.parse_qs(parsed.query).get('u', [''])[0]
        if encoded.startswith('a1'):
            value = encoded[2:]
            try:
                return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                return url
    return html.unescape(url)


def _query_relevance_terms(query: str) -> tuple[set[str], set[str]]:
    words = {value.lower() for value in re.findall(r'[A-Za-z0-9]+', query)
             if len(value) >= 3 and value.lower() not in {
                 'the', 'and', 'for', 'with', 'from', 'please', 'find', 'search',
                 'official', 'source', 'page', 'value', 'values', 'standard'}}
    codes = {value.lower() for value in re.findall(r'[A-Za-z]*\d+[A-Za-z0-9-]*', query)}
    return words, codes


def _filter_relevant_rows(query: str, rows: list[dict]) -> list[dict]:
    words, codes = _query_relevance_terms(query)
    if not words and not codes:
        return rows
    relevant_rows = []
    for row in rows:
        text = ('%s %s %s' % (row.get('title') or '', row.get('snippet') or '',
                              row.get('url') or '')).lower()
        text_words = set(re.findall(r'[a-z0-9]+', text))
        text_codes = {value.lower() for value in re.findall(r'[a-z]*\d+[a-z0-9-]*', text)}
        # When a query includes a model/grade/standard code, require a matching
        # code token; a generic word like "density" alone is too weak.
        if codes:
            code_match = any(code in text_codes or code.replace('-', '') in text.replace('-', '')
                             for code in codes)
            if not code_match:
                continue
        if words and not (words & text_words):
            continue
        relevant_rows.append(row)
    return relevant_rows


def _parse_bing_results(page: str) -> list[dict]:
    page = _SCRIPT.sub(' ', page or '')
    blocks = re.findall(
        r'<li\b[^>]*class=["\'][^"\']*\bb_algo\b[^"\']*["\'][^>]*>.*?</li>',
        page, re.S | re.I)
    results = []
    for block in blocks:
        match = re.search(
            r'<h2\b[^>]*>.*?<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            block, re.S | re.I)
        if not match:
            continue
        url = _decode_bing_url(match.group(1).strip())
        title = _text(match.group(2))
        if not url or not title:
            continue
        snippets = [_text(item) for item in re.findall(
            r'<p\b[^>]*>(.*?)</p>', block, re.S | re.I)]
        results.append({'title': title, 'snippet': max(snippets, key=len, default=''),
                        'url': url})
        if len(results) >= 10:
            break
    return results


def _parse_ddg_results(page: str) -> list[dict]:
    page = _SCRIPT.sub(' ', page or '')
    anchors = list(re.finditer(
        r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bresult__a\b[^"\']*["\'])'
        r'(?=[^>]*\bhref=["\']([^"\']+)["\'])[^>]*>(.*?)</a>',
        page, re.S | re.I))
    results = []
    for index, match in enumerate(anchors[:10]):
        url = _decode_ddg_url(match.group(1).strip())
        title = _text(match.group(2))
        if not url or not title:
            continue
        chunk_end = anchors[index + 1].start() if index + 1 < len(anchors) else len(page)
        tail = page[match.end():min(chunk_end, match.end() + 5000)]
        snippet_match = re.search(
            r'<(?:a|div|span)[^>]*class=["\'][^"\']*\bresult__snippet\b'
            r'[^"\']*["\'][^>]*>(.*?)</(?:a|div|span)>',
            tail, re.S | re.I)
        results.append({'title': title,
                        'snippet': _text(snippet_match.group(1)) if snippet_match else '',
                        'url': url})
    return results


def _explicit_no_results(page: str) -> bool:
    return bool(re.search(
        r'class=["\'][^"\']*\bb_no\b|there are no results|no results for|'
        r'no results found', page or '', re.I))


def search_detailed(query: str, timeout: int = 8,
                    use_cache: bool = True) -> dict:
    """Search with diagnostics while keeping result rows in the legacy shape."""
    q = (query or '').strip()
    if not q:
        return {'results': [], 'diagnostics': {
            'status': 'invalid_query', 'engine': None, 'source_url': None,
            'query': '', 'result_count': 0}}
    key = q.lower()
    if use_cache and key in _CACHE:
        cached = _CACHE[key]
        if isinstance(cached, dict) and isinstance(cached.get('results'), list):
            rows = cached['results']
            cached_diag = dict(cached.get('diagnostics') or {})
            cached_diag.update({'status': 'cache_hit' if rows else 'cache_empty',
                                'query': q, 'result_count': len(rows),
                                'source_url': None})
            cached_diag.setdefault('engine', 'unknown')
        else:
            rows = cached if isinstance(cached, list) else []
            cached_diag = {'status': 'cache_hit' if rows else 'cache_empty',
                           'engine': 'unknown', 'source_url': None,
                           'query': q, 'result_count': len(rows),
                           'cache_metadata': 'legacy_cache_without_engine'}
        return {'results': rows, 'diagnostics': {
            **cached_diag}}

    attempts = []
    engine_specs = [
        ('bing', 'https://www.bing.com/search?' + urllib.parse.urlencode(
            {'q': q, 'setlang': 'zh-CN', 'count': '12'}), _parse_bing_results),
        ('duckduckgo', 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode(
            {'q': q}), _parse_ddg_results),
    ]
    results = []
    any_response = False
    explicit_no_results = False
    for engine, url, parser in engine_specs:
        try:
            page = _fetch(url, timeout)
            any_response = True
            rows = parser(page)
            parsed_count = len(rows)
            rows = _filter_relevant_rows(q, rows)
            attempt_status = 'ok' if rows else (
                'no_results' if _explicit_no_results(page) else
                'irrelevant_results' if parsed_count else 'parser_changed')
            attempts.append({'engine': engine, 'status': attempt_status,
                             'source_url': url, 'http_response': True,
                             'result_count': len(rows), 'parsed_result_count': parsed_count,
                             'response_chars': len(page)})
            explicit_no_results = explicit_no_results or attempt_status == 'no_results'
            if rows:
                results = rows
                break
        except Exception as exc:
            attempts.append({'engine': engine, 'source_url': url,
                             'http_response': False, **_search_error(exc)})

    if results:
        status = 'ok'
        engine = next(row['engine'] for row in attempts if row.get('result_count'))
        source_url = next(row['source_url'] for row in attempts
                          if row.get('engine') == engine)
    else:
        engine = None
        source_url = None
        if any(row.get('status') == 'irrelevant_results' for row in attempts):
            status = 'irrelevant_results'
        elif explicit_no_results:
            if attempts and all(row.get('status') == 'no_results' for row in attempts):
                status = 'no_results'
            else:
                status = 'mixed_failure'
        elif any_response:
            status = 'parser_changed'
        else:
            statuses = [row.get('status') for row in attempts]
            status = 'timeout' if 'timeout' in statuses else (
                'http_error' if 'http_error' in statuses else 'network_error')

    if results:
        _CACHE[key] = {'results': results, 'diagnostics': {
            'engine': engine, 'source_url': source_url,
            'attempts': attempts, 'fallback_used': len(attempts) > 1}}
        _save_cache()
    diagnostics = {
        'status': status, 'engine': engine, 'source_url': source_url,
        'query': q, 'result_count': len(results), 'attempts': attempts,
        'fallback_used': len(attempts) > 1,
    }
    return {'results': results, 'diagnostics': diagnostics}


def search(query: str, timeout: int = 8, use_cache: bool = True):
    """Search and return the historical list of {'title','snippet','url'} rows."""
    return search_detailed(query, timeout=timeout, use_cache=use_cache)['results']


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
