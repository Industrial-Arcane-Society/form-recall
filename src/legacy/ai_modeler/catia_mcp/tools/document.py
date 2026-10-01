# -*- coding: utf-8 -*-
r"""文档管理工具(9 个) —— 对应参考项目 catia-v5-mcp-server 的 document 模块。

这一份是**范例模块**: 其余模块(sketcher / part_design / gsd / assembly /
measurement / export)照它的写法扩展即可:
    · TOOLS 列表: (工具名, 说明, inputSchema, 处理函数)
    · 处理函数里用 bridge.run_vbs 拼一小段 VBScript, 用 Out/Fail 回传
    · 中文路径/名称可以直接写进脚本(脚本是 UTF-16, cscript 读得了)
    · 保存/另存为中文路径要绕一下: CATIA 的 SaveAs 遇到中文路径会卡,
      先存 ASCII 临时路径再在 Python 侧搬过去(真机踩过的坑)
"""
from __future__ import annotations

import os
import shutil
import tempfile

from ..bridge import CatiaError, ensure_catia, host_info, run_vbs
from ._schema import B, I, N, S, arg_b, arg_s, obj, vbs

NAME = 'document'


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

def t_connect(args):
    info = ensure_catia()
    v = info.get('vars') or {}
    out = ['已连接 CATIA V5', '  打开的文档数: %s' % v.get('doc_count', '?')]
    if v.get('active'):
        out.append('  当前活动文档: %s' % v['active'])
    out.append('')
    out.append(host_info())
    return '\n'.join(out)


def t_disconnect(args):
    return ('已断开(本实现是无状态桥: 每次调用临时用 cscript 连一次 CATIA, '
            '不长期持有 COM 句柄, 所以没有需要释放的连接)。CATIA 本身不会被关闭。')


def _new_doc(kind: str, args) -> str:
    nm = arg_s(args, 'name', '') or arg_s(args, 'part_number', '')
    body = (
        'Dim d\n'
        'Set d = NewDoc("%s", %s)\n'
        'Out "document", d.Name\n'
        'If LCase("%s") = "part" Then\n'
        '    Dim p\n'
        '    Set p = d.Part\n'
        '    Out "part", p.Name\n'
        '    Out "bodies", p.Bodies.Count\n'
        'End If\n'
    ) % (kind, vbs(nm), kind)
    r = run_vbs(body)
    v = r.get('vars') or {}
    return ('已新建 %s 文档: %s%s' % (
        'Part' if kind == 'Part' else 'Product(装配)',
        v.get('document', '(未命名)'),
        ('\n  零件: %s  几何体数: %s' % (v.get('part'), v.get('bodies'))
         if v.get('part') else '')))


def t_new_part(args):
    return _new_doc('Part', args)


def t_new_product(args):
    return _new_doc('Product', args)


def t_open_document(args):
    path = arg_s(args, 'path')
    if not path:
        raise ValueError('缺少参数 path(要打开的 .CATPart/.CATProduct 完整路径)')
    if not os.path.isfile(path):
        raise ValueError('文件不存在: %s' % path)
    body = (
        'Dim d\n'
        'Attach\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set d = gApp.Documents.Open(%s)\n'
        'If Err.Number <> 0 Then\n'
        '    Dim e\n'
        '    e = Err.Description\n'
        '    Err.Clear\n'
        '    On Error GoTo 0\n'
        '    Fail "open failed: " & e\n'
        'End If\n'
        'On Error GoTo 0\n'
        'Out "document", d.Name\n'
    ) % vbs(path)
    r = run_vbs(body)
    return '已打开文档: %s' % ((r.get('vars') or {}).get('document') or path)


def t_save_document(args):
    """保存 / 另存为。中文路径走"ASCII 临时文件 + Python 搬移"。"""
    path = arg_s(args, 'path')
    body_common = (
        'Dim d\n'
        'Set d = Act()\n'
        'If d Is Nothing Then Fail "no active document"\n'
        'Out "document", d.Name\n'
        'Out "full_name", d.FullName\n'
    )
    if not path:
        r = run_vbs(body_common + 'On Error Resume Next\nd.Save\n'
                    'If Err.Number <> 0 Then Fail "save failed: " & Err.Description\n')
        return '已保存: %s' % ((r.get('vars') or {}).get('full_name') or '')

    path = os.path.abspath(path)
    ascii_ok = all(ord(ch) < 128 for ch in path)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    if ascii_ok:
        body = body_common + 'On Error Resume Next\nErr.Clear\n'
        body += 'd.SaveAs %s\n' % vbs(path)
        body += ('If Err.Number <> 0 Then Fail "saveas failed: " & Err.Description\n'
                 'Out "saved", d.FullName\n')
        r = run_vbs(body)
        return '已另存为: %s' % ((r.get('vars') or {}).get('saved') or path)
    tmp = os.path.join(tempfile.gettempdir(), 'aim_mcp_save_%d.CATPart'
                       % (os.getpid() % 100000))
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    body = body_common + 'On Error Resume Next\nErr.Clear\n'
    body += 'd.SaveAs %s\n' % vbs(tmp)
    body += ('If Err.Number <> 0 Then Fail "saveas failed: " & Err.Description\n'
             'Out "saved", d.FullName\n')
    run_vbs(body)
    if not os.path.isfile(tmp):
        raise CatiaError('CATIA 没有生成临时文件: %s' % tmp)
    shutil.move(tmp, path)
    return ('已另存为: %s\n  (中文路径绕行: 先存 ASCII 临时文件再搬移 —— '
            'CATIA 的 SaveAs 直接写中文路径会卡死)' % path)


def t_close_document(args):
    keep = arg_b(args, 'save', False)
    body = (
        'Dim d\n'
        'Set d = Act()\n'
        'If d Is Nothing Then Fail "no active document"\n'
        'Out "document", d.Name\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        '%s\n'
        'If Err.Number <> 0 Then\n'
        '    Dim e\n'
        '    e = Err.Description\n'
        '    Err.Clear\n'
        '    On Error GoTo 0\n'
        '    Fail "close failed: " & e\n'
        'End If\n'
        'On Error GoTo 0\n'
        'Out "closed", "1"\n'
    ) % ('d.Save' if keep else 'd.Close')
    r = run_vbs(body)
    return '已关闭文档: %s' % ((r.get('vars') or {}).get('document') or '')


def t_list_documents(args):
    body = (
        'Dim i, d, n\n'
        'Attach\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'n = gApp.Documents.Count\n'
        'Err.Clear\n'
        'Out "count", n\n'
        'For i = 1 To n\n'
        '    Set d = gApp.Documents.Item(i)\n'
        '    Out "doc" & i, d.Name & "\t" & TypeName(d)\n'
        'Next\n'
        'On Error GoTo 0\n'
    )
    r = run_vbs(body)
    v = r.get('vars') or {}
    n = int(v.get('count') or 0)
    out = ['打开的文档 %d 个:' % n]
    for i in range(1, n + 1):
        out.append('  %d. %s' % (i, v.get('doc%d' % i, '?')))
    return '\n'.join(out)


def t_get_active_document_info(args):
    body = (
        'Dim d, p, i\n'
        'Set d = Act()\n'
        'If d Is Nothing Then Fail "no active document"\n'
        'Out "name", d.Name\n'
        'Out "full_name", d.FullName\n'
        'Out "type", TypeName(d)\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set p = d.Part\n'
        'If Err.Number = 0 And Not p Is Nothing Then\n'
        '    Out "part", p.Name\n'
        '    Out "bodies", p.Bodies.Count\n'
        '    Out "body", MainBodyOf(p).Name\n'
        '    Out "shapes", MainBodyOf(p).Shapes.Count\n'
        '    Out "sketches", MainBodyOf(p).Sketches.Count\n'
        '    Out "hybrid_bodies", p.HybridBodies.Count\n'
        '    Out "parameters", p.Parameters.Count\n'
        'End If\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
    )
    r = run_vbs(body)
    v = r.get('vars') or {}
    out = ['活动文档: %s' % v.get('name', '?'),
           '  类型: %s' % v.get('type', '?'),
           '  路径: %s' % v.get('full_name', '')]
    if v.get('part'):
        out += ['  零件: %s' % v['part'],
                '  实体(PartBody): %s' % v.get('body', '?'),
                '  几何体数: %s   特征数: %s   草图数: %s' % (
                    v.get('bodies'), v.get('shapes'), v.get('sketches')),
                '  几何图形集: %s   参数数: %s' % (v.get('hybrid_bodies'),
                                            v.get('parameters'))]
    return '\n'.join(out)


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_connect', '连接 CATIA V5(没在运行就按配置启动), 并返回本机安装信息。',
     obj({}), t_connect),

    ('catia_disconnect', '断开与 CATIA 的连接(本实现无状态, 只作提示)。',
     obj({}), t_disconnect),

    ('catia_new_part', '新建一个 CATPart 零件文档并设为活动文档。',
     obj({'name': S('零件名(可留空)')}), t_new_part),

    ('catia_new_product', '新建一个 CATProduct 装配文档。',
     obj({'name': S('产品名(可留空)')}), t_new_product),

    ('catia_open_document', '打开已有的 .CATPart / .CATProduct 文件。',
     obj({'path': S('文件完整路径')}, ['path']), t_open_document),

    ('catia_save_document', '保存活动文档; 给了 path 就另存为(中文路径自动绕行)。',
     obj({'path': S('另存为的完整路径(可省=原地保存)')}), t_save_document),

    ('catia_close_document', '关闭活动文档。',
     obj({'save': B('关闭前是否保存, 默认 false')}), t_close_document),

    ('catia_list_documents', '列出当前 CATIA 里打开的所有文档。',
     obj({}), t_list_documents),

    ('catia_get_active_document_info', '查看活动文档的详细信息(类型/实体/特征数/草图数/参数数)。',
     obj({}), t_get_active_document_info),
]
