# -*- coding: utf-8 -*-
r"""生成命中率迭代报告网页 (单文件 HTML, 无外部依赖)。

输入: reports\iterations.json   (tools\iterate.py 的产物)
输出: reports\命中率迭代报告.html
"""
from __future__ import annotations

import html
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, 'reports')
SRC = os.path.join(REPORTS, 'iterations.json')
OUT = os.path.join(REPORTS, '命中率迭代报告.html')


def esc(s):
    return html.escape(str(s if s is not None else ''))


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if not os.path.isfile(SRC):
        print('缺少 %s, 请先跑 tools/iterate.py' % SRC)
        return 1
    d = json.load(open(SRC, encoding='utf-8'))
    rounds = d['rounds']
    detail = d.get('detail', {})
    target = d.get('target', 80.0)
    labels = [r['label'] for r in rounds]
    rates = [r['hit_rate'] for r in rounds]
    final = rates[-1] if rates else 0.0
    first = rates[0] if rates else 0.0

    # 各类别最终表现
    last = rounds[-1] if rounds else {}
    cats = sorted((last.get('by_category') or {}).items(),
                  key=lambda kv: kv[1]['rate'])
    # 样本对比 (最后一个轮次)
    samples = []
    for r in (detail.get(labels[-1]) if labels else []) or []:
        samples.append(r)

    maxrate = max(rates + [target]) if rates else 100
    ymax = min(100.0, max(100.0, maxrate + 5))

    def chart_points():
        n = len(rates)
        if n == 0:
            return ''
        w, h, pad = 860, 320, 46
        pts = []
        for i, v in enumerate(rates):
            x = pad + (w - 2 * pad) * (i / max(1, n - 1))
            y = h - pad - (h - 2 * pad) * (v / ymax)
            pts.append((x, y, v, labels[i]))
        return pts, w, h, pad

    pts, W, H, PAD = chart_points()
    poly = ' '.join('%.1f,%.1f' % (x, y) for (x, y, _v, _l) in pts)
    area = ''
    if pts:
        area = ('%.1f,%.1f ' % (pts[0][0], H - PAD)) + poly + \
               ' %.1f,%.1f' % (pts[-1][0], H - PAD)
    ty = H - PAD - (H - 2 * PAD) * (target / ymax)

    circles = ''.join(
        '<circle cx="%.1f" cy="%.1f" r="6" fill="#2f6fbf" stroke="#fff" stroke-width="2"/>'
        '<text x="%.1f" y="%.1f" font-size="13" fill="#1f2937" text-anchor="middle" '
        'font-weight="600">%.1f%%</text>' % (x, y, x, y - 14, v)
        for (x, y, v, _l) in pts)
    xlabels = ''.join(
        '<text x="%.1f" y="%d" font-size="12" fill="#6b7280" text-anchor="middle">%s</text>'
        % (x, H - PAD + 22, esc(l)) for (x, _y, _v, l) in pts)
    gridlines = ''
    for g in range(0, int(ymax) + 1, 20):
        gy = H - PAD - (H - 2 * PAD) * (g / ymax)
        gridlines += ('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#eef1f6"/>'
                      '<text x="%d" y="%.1f" font-size="11" fill="#9aa4b2" '
                      'text-anchor="end">%d%%</text>'
                      % (PAD, gy, W - PAD, gy, PAD - 8, gy + 4, g))

    cat_rows = ''.join(
        '<div class="catrow"><span class="catname">%s</span>'
        '<span class="barwrap"><span class="bar" style="width:%.1f%%;background:%s"></span>'
        '</span><span class="catnum">%d/%d · %.1f%%</span></div>'
        % (esc(k), v['rate'], '#16a34a' if v['rate'] >= 80 else
           ('#d97706' if v['rate'] >= 50 else '#dc2626'),
           v['hit'], v['n'], v['rate'])
        for k, v in cats)

    rnd_rows = ''.join(
        '<tr><td><b>%s</b><div class="dim">%s</div></td><td class="num">%d</td>'
        '<td class="num">%d/%d</td><td class="num big">%.2f%%</td>'
        '<td class="num">%s</td><td class="num">%.0fs</td></tr>'
        % (esc(r['label']), esc(r.get('desc', '')),
           r.get('n', 0), r['hits'], r['n'], r['hit_rate'],
           ('+' if r['hit_rate'] >= (rounds[0]['hit_rate']) else '') +
           ('%.1f' % (r['hit_rate'] - rounds[0]['hit_rate'])), r.get('elapsed', 0))
        for r in rounds)

    def fmt(v):
        return ('%.2f' % v) if isinstance(v, (int, float)) else '—'

    miss = [s for s in samples if not s['hit']][:12]
    good = [s for s in samples if s['hit']][:8]
    samp_rows = ''
    for s in (good + miss):
        ok = s['hit']
        samp_rows += (
            '<tr class="%s"><td>%s</td><td class="nl">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td><td>%s</td></tr>'
            % ('okrow' if ok else 'badrow', esc(s['category']), esc(s['nl']),
               ' × '.join(fmt(x) for x in (s['expect'] or [])),
               ' × '.join(fmt(x) for x in (s['got'] or [])) if s['got'] else '未生成',
               ('命中' if ok else esc(s['error'] or '尺寸不符'))))

    chart_svg = (
        '<svg viewBox="0 0 %d %d" width="100%%" style="max-width:920px">'
        '%s'
        '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#dc2626" '
        'stroke-dasharray="6 5" stroke-width="1.5"/>'
        '<text x="%d" y="%.1f" font-size="12" fill="#dc2626">目标 %.0f%%</text>'
        '<polygon points="%s" fill="rgba(47,111,191,.12)"/>'
        '<polyline points="%s" fill="none" stroke="#2f6fbf" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '%s%s'
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#dbe2ec"/>'
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#dbe2ec"/>'
        '</svg>'
        % (W, H, gridlines, PAD, ty, W - PAD, ty, W - PAD - 66, ty - 8, target,
           area, poly, circles, xlabels,
           PAD, PAD, PAD, H - PAD, PAD, H - PAD, W - PAD, H - PAD))

    html_doc = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 自动建模 —— 命中率迭代报告</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;
     background:#eef1f6;color:#1f2937;line-height:1.65}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px 60px}
header{background:linear-gradient(110deg,#3b82d6,#24568f);color:#fff;border-radius:14px;
       padding:28px 32px;box-shadow:0 4px 18px rgba(36,86,143,.22)}
header h1{margin:0 0 6px;font-size:25px;letter-spacing:.5px}
header p{margin:2px 0;opacity:.92;font-size:13.5px}
.kpis{display:flex;gap:16px;flex-wrap:wrap;margin:22px 0}
.kpi{flex:1;min-width:170px;background:#fff;border:1px solid #dbe2ec;border-radius:12px;
     padding:16px 20px}
.kpi .lab{font-size:12.5px;color:#6b7280}
.kpi .val{font-size:29px;font-weight:700;color:#2f6fbf;margin-top:4px;
          font-variant-numeric:tabular-nums}
.kpi .val.small{font-size:19px}
.kpi.good .val{color:#16a34a}
.card{background:#fff;border:1px solid #dbe2ec;border-radius:12px;padding:22px 26px;
      margin-bottom:20px}
.card h2{margin:0 0 4px;font-size:17px;display:flex;align-items:center;gap:9px}
.card h2::before{content:"";width:4px;height:17px;background:#2f6fbf;border-radius:2px}
.card .sub{color:#9aa4b2;font-size:12.5px;margin:0 0 16px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{background:#f6f8fc;color:#5b6675;font-weight:600;text-align:left;padding:9px 11px;
   border-bottom:1px solid #dbe2ec;font-size:12.5px}
td{padding:9px 11px;border-bottom:1px solid #eef1f6;vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.big{font-weight:700;color:#2f6fbf}
tr.okrow td{background:#f4fbf6}
tr.badrow td{background:#fff7f5}
.dim{color:#9aa4b2;font-size:11.5px}
.nl{font-family:Consolas,monospace;font-size:12.5px;color:#255a9b}
.catrow{display:flex;align-items:center;gap:12px;padding:5px 0;font-size:13px}
.catname{width:130px;flex:none;color:#374151}
.barwrap{flex:1;height:16px;background:#f0f3f8;border-radius:8px;overflow:hidden}
.bar{display:block;height:100%;border-radius:8px;transition:width .4s}
.catnum{width:130px;flex:none;text-align:right;color:#5b6675;
        font-variant-numeric:tabular-nums;font-size:12.5px}
footer{color:#9aa4b2;font-size:12px;text-align:center;margin-top:34px}
code{background:#f0f3f8;padding:1px 6px;border-radius:4px;font-size:12.5px}
</style></head><body><div class="wrap">
<header>
  <h1>AI 自动建模助手 —— 命中率迭代报告</h1>
  <p>知识来源：__SOURCE__</p>
  <p>评测用例 __NCASES__ 条 · 生成时间 __TIME__</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="lab">起始命中率（基线）</div><div class="val">__FIRST__%</div></div>
  <div class="kpi good"><div class="lab">最终命中率</div><div class="val">__FINAL__%</div></div>
  <div class="kpi"><div class="lab">提升幅度</div><div class="val">+__GAIN__ 个百分点</div></div>
  <div class="kpi"><div class="lab">目标</div><div class="val small">__TARGET__%
     __VERDICT__</div></div>
</div>

<div class="card">
  <h2>命中率迭代曲线</h2>
  <p class="sub">每一轮开启一项新能力，用完全相同的评测用例测量。虚线为目标线。</p>
  __CHART__
</div>

<div class="card">
  <h2>各轮明细</h2>
  <p class="sub">同一个随机种子采样，各轮严格可比。</p>
  <table><thead><tr><th>轮次</th><th class="num">用例</th><th class="num">命中</th>
  <th class="num">命中率</th><th class="num">较基线</th><th class="num">用时</th></tr></thead>
  <tbody>__ROUNDS__</tbody></table>
</div>

<div class="card">
  <h2>分类别表现（最后一轮）</h2>
  <p class="sub">绿色 ≥80%，橙色 ≥50%，红色 &lt;50%。</p>
  __CATS__
</div>

<div class="card">
  <h2>生成模型 vs 参考模型（抽样对比）</h2>
  <p class="sub">参考值来自知识库原始参数；生成值是本程序实际建出的三维模型外形尺寸（mm）。</p>
  <table><thead><tr><th>类别</th><th>自然语言描述</th><th class="num">参考尺寸</th>
  <th class="num">生成尺寸</th><th>判定</th></tr></thead>
  <tbody>__SAMPLES__</tbody></table>
</div>

<footer>由 tools/make_report.py 生成 · 数据文件 reports/iterations.json</footer>
</div></body></html>'''

    html_doc = (html_doc
                .replace('__SOURCE__', esc(d.get('source', '')))
                .replace('__NCASES__', str(d.get('n_cases', 0)))
                .replace('__TIME__', esc(d.get('generated_at', '')))
                .replace('__FIRST__', '%.2f' % first)
                .replace('__FINAL__', '%.2f' % final)
                .replace('__GAIN__', '%.1f' % (final - first))
                .replace('__TARGET__', '%.0f' % target)
                .replace('__VERDICT__', '✅ 已达成' if final >= target else '⏳ 未达成')
                .replace('__CHART__', chart_svg)
                .replace('__ROUNDS__', rnd_rows)
                .replace('__CATS__', cat_rows or '<p class="dim">（无数据）</p>')
                .replace('__SAMPLES__', samp_rows or '<tr><td colspan="5">（无数据）</td></tr>'))

    os.makedirs(REPORTS, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html_doc)
    print('报告已生成: %s  (%.1f KB)' % (OUT, os.path.getsize(OUT) / 1024))
    print('最终命中率 %.2f%% (目标 %.0f%%)' % (final, target))
    return 0


if __name__ == '__main__':
    sys.exit(main())
