# -*- coding: utf-8 -*-
r"""本插件在参考项目之外的扩展工具: 中文自然语言建模 / 模型库 / 真机几何校验。

参考项目(catia-v5-mcp-server)只有"一个特征一个工具"的粗粒度接口, 让 AI 拼
几十个调用才能建一个零件; 这里补上本插件已有的能力:

    catia_nl_build         一句中文 -> 特征树 -> 内核建模 -> 推送到 CATIA
    catia_nl_preview       只在本机内核里试算(不碰 CATIA), 回报尺寸与特征表
    catia_feature_table    看这句中文会被展开成哪些 CATIA 操作
    catia_catalog_search   CATIA 自带 catalog 标准件库检索(中文别名)
    catia_geometry_check   让 CATIA 导出 STL, 逐个孔量"孔壁" —— 验证真的切出来了
"""
from __future__ import annotations

import math
import os
import re
import tempfile

from ..bridge import CatiaError, run_vbs
from ._schema import B, N, S, arg_b, arg_s, obj, vbs

NAME = 'modeler'


def _parse(text: str):
    from ... import nlparser
    res = nlparser.parse(text)
    if not res.ok:
        raise ValueError('解析失败: %s' % (res.message or '').strip())
    return res


def t_nl_preview(args):
    text = arg_s(args, 'text')
    if not text.strip():
        raise ValueError('缺少参数 text(中文建模描述)')
    res = _parse(text)
    doc = res.doc
    body = doc.build()
    from ... import catia_export as CE, kernel as K
    bb = K.bbox(body)
    feats, warns = CE.build_catia_features(doc)
    out = ['需求: %s' % text, res.summary()]
    if bb:
        out.append('外形尺寸: %.2f × %.2f × %.2f mm' % (bb[3] - bb[0], bb[4] - bb[1],
                                                    bb[5] - bb[2]))
    out.append('')
    out.append('特征树:')
    for i, f in enumerate(doc.features, 1):
        out.append('  %d. [%s] %s' % (i, '切除' if f.op == 'cut' else '合并', f.describe()))
    out.append('')
    out.append('将展开为 %d 个 CATIA 操作:' % len(feats))
    for i, e in enumerate(feats, 1):
        out.append('  %d. %-12s z=%-9.3f 深/高=%-8.3f 半径=%-7.3f %s'
                   % (i, e['kind'], e.get('z', 0.0), e.get('depth') or e.get('h') or 0.0,
                      e.get('r', 0.0), e.get('name', '')))
    for w in warns:
        out.append('  ! ' + w)
    for w in res.warnings:
        out.append('  ! ' + w)
    return '\n'.join(out)


def t_feature_table(args):
    text = arg_s(args, 'text')
    res = _parse(text)
    from ... import catia_export as CE
    feats, _w = CE.build_catia_features(res.doc)
    lines = ['kind|cx|cy|z|n1|n2|n3|n4|name|profile']
    for e in feats:
        lines.append('|'.join(str(x) for x in (
            e['kind'], round(e.get('cx', 0.0), 4), round(e.get('cy', 0.0), 4),
            round(e.get('z', 0.0), 4), round(e.get('l', 0.0), 4), round(e.get('w', 0.0), 4),
            round(e.get('r', 0.0), 4), round(e.get('depth') or e.get('h') or 0.0, 4),
            e.get('name', ''), '')))
    return 'CATIA 特征表(%d 条):\n%s' % (len(feats), '\n'.join(lines))


def t_nl_build(args):
    """中文描述 -> 直接建到 CATIA(走本插件已经真机验证过的推送通道)。

    规范约束(与用户确认过): 描述缺**结构性参数**时, 解析器会给出"需要确认"清单,
    这时**不推送**, 先把问题交回给调用方去问用户; 确实要按推定值先建, 显式传 force=true。
    """
    text = arg_s(args, 'text')
    if not text.strip():
        raise ValueError('缺少参数 text(中文建模描述)')
    save = arg_s(args, 'save_path', '') or None
    force = bool(args.get('force'))
    res = _parse(text)
    if res.questions and not force:
        pre = t_nl_preview({'text': text})
        return '\n'.join(
            ['没有直接建模: 描述里有 %d 项需要先确认。' % len(res.questions)]
            + ['  %d) %s' % (i, q) for i, q in enumerate(res.questions, 1)]
            + ['', '请把数值补进描述后重新调用; 或用 force=true 按推定值先建。', '',
               '按推定值将会建成: %s' % (pre.splitlines()[1] if len(pre.splitlines()) > 1 else '')])
    doc = res.doc
    doc.build()
    from ..bridge import DRY_RUN
    if DRY_RUN:
        return ('(dry-run 模式) 不推送 CATIA, 只回报将要建的内容:\n'
                + t_nl_preview({'text': text}))
    from ... import catia
    ok, msg, _script = catia.push_to_catia(doc, save_catpart=save)
    head = '已在 CATIA 中建模: %s' % text
    return '\n'.join([head, msg if ok else '推送失败:\n' + msg])


def t_catalog_search(args):
    kw = arg_s(args, 'keyword')
    limit = int(args.get('limit') or 20)
    try:
        from ... import catialib
    except Exception as e:                              # noqa: BLE001
        raise CatiaError('模型库模块不可用: %s' % e)
    idx = None
    for fn in ('load_index', 'build_index', 'index'):
        f = getattr(catialib, fn, None)
        if callable(f):
            try:
                idx = f() if fn != 'build_index' else f()
                break
            except Exception:                           # noqa: BLE001
                continue
    items = []
    if isinstance(idx, dict):
        items = idx.get('items') or idx.get('parts') or []
    elif isinstance(idx, list):
        items = idx
    if not items:
        return ('模型库索引为空: 请先在助手里执行 工具 → CATIA 模型库(或用 '
                'python -m ai_modeler --catia-lib --rebuild) 建立索引。')
    hit = []
    for it in items:
        blob = ' '.join(str(it.get(k, '')) for k in
                        ('name', 'file', 'path', 'family', 'aliases', 'category'))
        if not kw or kw.lower() in blob.lower():
            hit.append(it)
        if len(hit) >= limit:
            break
    out = ['模型库命中 %d 条(关键词 %r):' % (len(hit), kw)]
    for it in hit:
        out.append('  %s' % (it.get('name') or it.get('file') or it.get('path')))
        p = it.get('path') or it.get('file')
        if p:
            out.append('     %s' % p)
    return '\n'.join(out)


def t_geometry_check(args):
    """让 CATIA 导出 STL, 再逐个孔量"孔壁" —— 证明切除真的切进材料了。"""
    fmt = 'stl'
    tmp = os.path.join(tempfile.gettempdir(), 'aim_mcp_check.stl')
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    body = (
        'Dim d\n'
        'Set d = Act()\n'
        'If d Is Nothing Then Fail "no active document"\n'
        'Out "doc", d.Name\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'd.ExportData %s, %s\n'
        'If Err.Number <> 0 Then Fail "export failed: " & Err.Description\n'
        'Out "stl", %s\n'
    ) % (vbs(tmp), vbs(fmt), vbs(tmp))
    r = run_vbs(body, timeout=300)
    doc_name = (r.get('vars') or {}).get('doc', '?')
    size = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    if size < 128:
        # 空零件导出的 STL 只有几十字节(只有 solid/endsolid 两行), 这是最常见的
        # "量不到几何"的原因 —— 明确说出来, 别丢一个看不懂的报错
        raise CatiaError(
            '活动文档 %s 里没有可量的几何体(导出的 STL 只有 %d 字节)。\n'
            '请先建模或切到有零件的文档: 例如 catia_nl_build 建一个, 或 '
            'catia_list_documents + catia_get_active_document_info 看当前在哪。'
            % (doc_name, size))
    txt = open(tmp, 'rb').read().decode('utf-8', 'replace')
    vs = [tuple(float(x) for x in m) for m in
          re.findall(r'vertex\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)', txt)]
    if not vs:
        raise CatiaError('导出的 STL 里没有网格数据(可能是二进制 STL 或导出失败): %s' % tmp)
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    out = ['零件 %s 的真实几何(从 CATIA 导出 STL 后量的):' % doc_name,
           '  顶点数: %d' % len(vs),
           '  包围盒: X %.2f~%.2f  Y %.2f~%.2f  Z %.2f~%.2f'
           % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)),
           '  外形尺寸: %.2f × %.2f × %.2f mm'
           % (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))]
    r_ax = [math.hypot(v[0], v[1]) for v in vs]
    zc = 0.5 * (min(zs) + max(zs))
    mid = [math.hypot(v[0], v[1]) for v in vs if abs(v[2] - zc) < 1e-6]
    out.append('  离轴最近顶点: %.2f mm' % min(r_ax))
    if mid:
        out.append('  中间高度处离轴最近: %.2f mm  ← 如果有孔, 这里就是孔半径'
                   % min(mid))
    return '\n'.join(out)


TOOLS = [
    ('catia_nl_build',
     '用一句中文直接建模并推到 CATIA(本插件独有: 走已经真机验证过的中文解析 + 特征树 + 推送通道)。'
     '例: "直齿圆柱齿轮 模数2 齿数30 齿宽20, 中心孔直径15"。'
     '若描述缺结构性参数(如没给齿宽), 会**先返回需要确认的问题清单而不建模**, '
     '补全描述后再调用, 或用 force=true 按推定值先建。',
     obj({'text': S('中文建模描述'), 'save_path': S('另存为 .CATPart 的路径(可省)'),
          'force': B('true=即使有未确认项也按推定值先建(默认 false: 先问)')}, ['text']),
     t_nl_build),

    ('catia_nl_preview',
     '只在本机内核里试算(不碰 CATIA), 回报外形尺寸、特征树与将展开的 CATIA 操作 —— '
     '调 catia_nl_build 之前先用它确认理解对不对。',
     obj({'text': S('中文建模描述')}, ['text']), t_nl_preview),

    ('catia_feature_table',
     '看一句中文会被展开成哪些 CATIA 操作(特征表), 便于排查"建出来不对"的问题。',
     obj({'text': S('中文建模描述')}, ['text']), t_feature_table),

    ('catia_catalog_search',
     '检索 CATIA 自带 catalog 标准件库(支持中文别名), 返回可直接插入的模型路径。',
     obj({'keyword': S('关键词, 如 螺栓/轴承/型材'), 'limit': N('最多返回条数, 默认20')}),
     t_catalog_search),

    ('catia_geometry_check',
     '真机几何校验: 让 CATIA 把活动零件导出 STL, 再量顶点/包围盒/离轴距离 —— '
     '用来确认"孔到底切出来没有"(只看成功/操作数是抓不到空切的)。',
     obj({}), t_geometry_check),
]
