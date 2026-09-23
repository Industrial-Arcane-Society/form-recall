# -*- coding: utf-8 -*-
"""MCP (Model Context Protocol) 服务端 —— 标准库实现, 通过 stdio 通信。

让 Claude / Codex / dsh 等 AI 客户端可以直接调用本程序的建模能力:
自然语言建模、参数化特征、布尔运算、几何统计、导出 STL/OBJ/报告/CATIA 脚本。

启动方式:  python -m ai_modeler.mcp_server
"""
from __future__ import annotations

import json
import os
import sys
import traceback

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ai_modeler import exporters, kernel as K
    from ai_modeler import docmodel as DM
    from ai_modeler import nlparser as NLP
else:
    from . import exporters, kernel as K
    from . import docmodel as DM
    from . import nlparser as NLP

SERVER_NAME = 'ai-modeler'
SERVER_VERSION = '1.0.0'
PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05']

_STATE = {'doc': DM.Document('Part1')}


def doc() -> DM.Document:
    return _STATE['doc']


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------


def _tree_text() -> str:
    d = doc()
    if not d.features:
        return '（当前模型为空）'
    lines = ['特征树 (%d 项):' % len(d.features)]
    for i, f in enumerate(d.features, 1):
        op = {'cut': '切除', 'add': '合并'}.get(f.op, f.op)
        lines.append('  %d. [%s] %s' % (i, op, f.describe()))
    return '\n'.join(lines)


def _stats_text() -> str:
    d = doc()
    body = d.build()
    st = d.stats()
    bb = st['bbox']
    lines = ['零件: %s' % d.name, '多边形面: %d  三角面片: %d' % (st['faces'], st['triangles'])]
    if bb:
        lines.append('包围盒: X[%.2f, %.2f] Y[%.2f, %.2f] Z[%.2f, %.2f] mm'
                     % (bb[0], bb[3], bb[1], bb[4], bb[2], bb[5]))
        lines.append('外形尺寸: %.2f × %.2f × %.2f mm' % st['size'])
    lines.append('体积: %.1f mm³ (%.3f cm³)' % (st['volume'], st['volume'] / 1000.0))
    lines.append('表面积: %.1f mm²' % st['area'])
    return '\n'.join(lines)


def t_build_from_text(args):
    text = args.get('text') or args.get('prompt') or ''
    if not text.strip():
        raise ValueError('缺少参数 text')
    new = bool(args.get('new_model', True))
    if new:
        _STATE['doc'] = DM.Document(args.get('name') or 'Part1')
    res = NLP.parse(text, doc() if not new else None)
    if not res.ok:
        return '解析失败: %s' % res.message
    _STATE['doc'] = res.doc
    doc().build()
    out = ['需求: ' + text, res.summary()]
    for s in res.steps:
        out.append('  · ' + s)
    for w in res.warnings:
        out.append('  ! ' + w)
    out.append('')
    out.append(_tree_text())
    out.append('')
    out.append(_stats_text())
    return '\n'.join(out)


def _add(kind, params, op=DM.ADD, label=None):
    d = doc()
    f = DM.Feature(kind, params, op)
    d.add(f)
    d.build()
    return '已添加特征 %d: %s\n%s' % (len(d.features), label or f.describe(), _stats_text())


def t_add_box(args):
    return _add('box', {'length': float(args.get('length', 100)),
                        'width': float(args.get('width', 60)),
                        'height': float(args.get('height', 20)),
                        'corner': float(args.get('corner', 0) or 0),
                        'corner_mode': args.get('corner_mode', 'fillet'),
                        'center': [float(args.get('x', 0)), float(args.get('y', 0)),
                                   float(args.get('z', 0))],
                        'base': True, 'axis': args.get('axis', 'Z')})


def t_add_cylinder(args):
    p = {'radius': float(args.get('radius', 30)), 'height': float(args.get('height', 60)),
         'center': [float(args.get('x', 0)), float(args.get('y', 0)), float(args.get('z', 0))],
         'base': bool(args.get('base', True)), 'axis': args.get('axis', 'Z'),
         'segments': int(args.get('segments', 48))}
    if args.get('radius2') is not None:
        p['radius2'] = float(args['radius2'])
    return _add('cylinder', p)


def t_add_sphere(args):
    return _add('sphere', {'radius': float(args.get('radius', 30)),
                           'center': [float(args.get('x', 0)), float(args.get('y', 0)),
                                      float(args.get('z', 0))],
                           'segments': int(args.get('segments', 32)),
                           'rings': int(args.get('rings', 16))})


def t_add_gear(args):
    return _add('gear', {'teeth': int(args.get('teeth', 20)),
                         'module': float(args.get('module', 2.0)),
                         'height': float(args.get('height', 10)),
                         'axis': args.get('axis', 'Z')})


def t_add_hole(args):
    p = {'diameter': float(args.get('diameter', 10)),
         'through': bool(args.get('through', True)),
         'axis': args.get('axis', 'Z'), 'pos': args.get('pos', 'center'),
         'segments': int(args.get('segments', 48))}
    for k in ('depth', 'x', 'y', 'count', 'pcd', 'margin'):
        if args.get(k) is not None:
            p[k] = float(args[k])
    return _add('hole', p, DM.CUT)


def t_add_pocket(args):
    return _add('pocket', {'length': float(args.get('length', 40)),
                           'width': float(args.get('width', 20)),
                           'depth': float(args.get('depth', 5)),
                           'corner': float(args.get('corner', 0) or 0),
                           'axis': args.get('axis', 'Z')}, DM.CUT)


def t_add_pattern(args):
    if args.get('mode', 'circular') == 'linear':
        p = {'mode': 'linear', 'count': int(args.get('count', 4)),
             'step': [float(v) for v in args.get('step', [40, 0, 0])]}
    else:
        p = {'mode': 'circular', 'count': int(args.get('count', 6)),
             'axis': args.get('axis', 'Z'), 'angle': float(args.get('angle', 360)),
             'full': True, 'center': [0.0, 0.0, 0.0],
             'compound': bool(args.get('compound', False))}
    return _add('pattern', p)


def t_add_fillet(args):
    d = doc()
    if not d.features:
        raise ValueError('模型为空, 无法倒圆角')
    idx = int(args.get('index', len(d.features))) - 1
    f = d.features[idx]
    f.params['corner'] = float(args.get('radius', 5))
    f.params['corner_mode'] = 'fillet'
    d.invalidate()
    d.build()
    return '已为第 %d 个特征 (%s) 添加圆角 R%g' % (idx + 1, f.describe(), float(args.get('radius', 5)))


def t_add_chamfer(args):
    d = doc()
    if not d.features:
        raise ValueError('模型为空, 无法倒角')
    idx = int(args.get('index', len(d.features))) - 1
    f = d.features[idx]
    f.params['corner'] = float(args.get('size', 2))
    f.params['corner_mode'] = 'chamfer'
    d.invalidate()
    d.build()
    return '已为第 %d 个特征 (%s) 添加倒角 %g' % (idx + 1, f.describe(), float(args.get('size', 2)))


def t_add_prism(args):
    prof = [[float(a), float(b)] for (a, b) in args.get('profile', [])]
    if len(prof) < 3:
        raise ValueError('profile 至少需要 3 个点, 形如 [[-50,-10],[50,-10],[50,10],[-50,10]]')
    return _add('prism', {'profile': prof, 'height': float(args.get('height', 20)),
                          'axis': args.get('axis', 'Z')})


def t_cut_box(args):
    return _add('box', {'length': float(args.get('length', 20)),
                        'width': float(args.get('width', 20)),
                        'height': float(args.get('height', 20)),
                        'center': [float(args.get('x', 0)), float(args.get('y', 0)),
                                   float(args.get('z', 0))],
                        'base': False, 'axis': args.get('axis', 'Z')}, DM.CUT)


def t_get_tree(args):
    return _tree_text()


def t_get_stats(args):
    return _stats_text()


def t_clear(args):
    _STATE['doc'] = DM.Document(args.get('name') or 'Part1')
    return '模型已清空。'


def t_set_param(args):
    d = doc()
    idx = int(args.get('index', 0)) - 1
    if not (0 <= idx < len(d.features)):
        raise ValueError('特征序号超出范围 (1..%d)' % len(d.features))
    key = args.get('key')
    if not key:
        raise ValueError('缺少参数 key')
    f = d.features[idx]
    val = args.get('value')
    try:
        cur = f.params.get(key)
        if isinstance(cur, bool):
            val = bool(val)
        elif isinstance(cur, int):
            val = int(float(val))
        else:
            val = float(val)
    except (TypeError, ValueError):
        pass
    f.params[key] = val
    d.invalidate()
    d.build()
    return '已更新第 %d 个特征: %s\n%s' % (idx + 1, f.describe(), _stats_text())


def t_delete_feature(args):
    d = doc()
    idx = int(args.get('index', 0)) - 1
    if not (0 <= idx < len(d.features)):
        raise ValueError('特征序号超出范围')
    f = d.features.pop(idx)
    d.invalidate()
    d.build()
    return '已删除: %s\n%s' % (f.describe(), _tree_text())


def t_export(args):
    d = doc()
    body = d.build()
    if not body:
        raise ValueError('模型为空, 没有可导出的几何体')
    fmt = (args.get('format') or 'stl').lower()
    path = args.get('path')
    if not path:
        outdir = args.get('dir') or os.path.join(os.path.expanduser('~'), 'Desktop')
        path = os.path.join(outdir, '%s.%s' % (d.name or 'part',
                                               'txt' if fmt == 'report' else fmt))
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    if fmt == 'stl':
        exporters.export_stl(body, path, d.name, binary=bool(args.get('binary', True)))
    elif fmt == 'obj':
        exporters.export_obj(body, path, d.name)
    elif fmt in ('report', 'txt'):
        exporters.export_report(d, path, d.source_prompt)
    elif fmt == 'catia':
        exporters.export_catia_script(d, path)
    elif fmt in ('aimodel', 'json'):
        exporters.export_project(d, path)
    else:
        raise ValueError('不支持的格式: %s (可选 stl/obj/report/catia/aimodel)' % fmt)
    return '已导出 %s: %s (%d 字节)' % (fmt.upper(), path, os.path.getsize(path))


def t_examples(args):
    lines = ['可用的自然语言示例:']
    for e in NLP.EXAMPLES:
        lines.append('  · ' + e)
    return '\n'.join(lines)


def t_explain_real(args):
    """查真实型号库：这个型号是什么、现实（CATIA 导出的 STEP 实测）长什么样。"""
    from . import reallib as RL
    text = (args.get('text') or '').strip()
    if not text:
        raise ValueError('需要 text，例如 "EGT001 SW80x088" 或 "KMS-M15-G14-H06"')
    r = RL.lookup(text)
    if not r:
        avail = RL.available()
        return ('真实型号库里没有命中 `%s`。\n'
                '库里现有：%s\n'
                '（没命中就不猜 —— 这条纪律是本轮迭代的核心）'
                % (text, '、'.join('%s %d 条' % kv for kv in avail.items())))
    L = [RL.explain(text), '', '【真实尺寸】']
    p = r.get('params') or {}
    for k, v in p.items():
        if k.startswith('__'):
            continue
        L.append('  %s = %s' % (k, v))
    if r.get('limits'):
        L.append('')
        L.append('【诚实边界】')
        for x in r['limits']:
            L.append('  · ' + x)
    return '\n'.join(L)


def t_load_project(args):
    path = args.get('path')
    if not path or not os.path.isfile(path):
        raise ValueError('工程文件不存在: %r' % path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    _STATE['doc'] = DM.Document.from_dict(data)
    doc().build()
    return '已载入工程: %s\n%s' % (path, _tree_text())


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

def _p(props, required):
    return {'type': 'object', 'properties': props, 'required': required,
            'additionalProperties': False}


S = lambda d: {'type': 'string', 'description': d}
N = lambda d: {'type': 'number', 'description': d}
I = lambda d: {'type': 'integer', 'description': d}
B = lambda d: {'type': 'boolean', 'description': d}

TOOLS = [
    ('build_from_text', '用中文自然语言直接建模。例: "画一个 120×80×15 的长方体, 四角圆角 R8, 中心打一个直径 20 的通孔"。'
                        '这是最常用的入口, 会整体替换当前模型。',
     _p({'text': S('中文建模需求描述'), 'name': S('零件名称(可选)'),
         'new_model': B('是否清空当前模型, 默认 true')}, ['text']), t_build_from_text),

    ('add_box', '添加长方体 (以底面中心定位)。',
     _p({'length': N('长(X), 默认100'), 'width': N('宽(Y), 默认60'), 'height': N('高(Z), 默认20'),
         'corner': N('竖直棱圆角半径或倒角边长'), 'corner_mode': S('fillet 或 chamfer'),
         'x': N('底面中心X'), 'y': N('底面中心Y'), 'z': N('底面Z'), 'axis': S('拉伸轴 X/Y/Z')}, []), t_add_box),

    ('add_cylinder', '添加圆柱 / 圆台。',
     _p({'radius': N('底面半径'), 'radius2': N('顶面半径(圆台用, 0为圆锥)'), 'height': N('高度'),
         'x': N('中心X'), 'y': N('中心Y'), 'z': N('基准Z'), 'axis': S('轴 X/Y/Z'),
         'base': B('是否以 z 为底面'), 'segments': I('圆周分段数, 默认48')}, []), t_add_cylinder),

    ('add_sphere', '添加球体。',
     _p({'radius': N('半径'), 'x': N('中心X'), 'y': N('中心Y'), 'z': N('中心Z')}, []), t_add_sphere),

    ('add_gear', '添加直齿圆柱齿轮 (梯形齿近似)。',
     _p({'teeth': I('齿数'), 'module': N('模数 mm'), 'height': N('厚度 mm')}, []), t_add_gear),

    ('add_prism', '把任意 2D 闭合轮廓拉伸成实体。',
     _p({'profile': {'type': 'array', 'description': '轮廓点 [[x,y], ...], 至少3个点',
                     'items': {'type': 'array', 'items': {'type': 'number'}}},
         'height': N('拉伸高度'), 'axis': S('拉伸轴')}, ['profile']), t_add_prism),

    ('add_hole', '打孔 (切除)。支持中心孔、四角孔、分度圆均布孔、指定坐标孔。',
     _p({'diameter': N('孔径'), 'through': B('是否通孔, 默认true'), 'depth': N('盲孔深度'),
         'pos': S('center | corners | pcd | coord'), 'axis': S('孔轴 X/Y/Z'),
         'x': N('X 坐标(coord 模式)'), 'y': N('Y 坐标'),
         'count': I('均布数量(pcd 模式)'), 'pcd': N('分度圆直径(pcd 模式)'),
         'margin': N('距边距离(corners 模式)')}, []), t_add_hole),

    ('add_pocket', '铣一个矩形凹槽 (切除)。',
     _p({'length': N('槽长'), 'width': N('槽宽'), 'depth': N('槽深'),
         'corner': N('槽底圆角'), 'axis': S('进刀轴')}, []), t_add_pocket),

    ('add_pattern_linear', '对上一个特征做线性阵列。',
     _p({'count': I('数量'), 'step': {'type': 'array', 'items': {'type': 'number'},
                                      'description': '阵列步距向量 [dx,dy,dz]'}}, ['count']),
     lambda a: t_add_pattern(dict(a, mode='linear'))),

    ('add_pattern_circular', '对上一个特征做圆周阵列 (例如叶片绕轮盘均布)。',
     _p({'count': I('数量'), 'axis': S('旋转轴 X/Y/Z'), 'angle': N('总角度, 默认360'),
         'compound': B('true 时直接组合网格(更快), 默认 false 走布尔并集')}, ['count']),
     lambda a: t_add_pattern(dict(a, mode='circular'))),

    ('add_fillet', '给指定特征加圆角 (支持长方体、齿轮、轮廓拉伸的竖直棱)。',
     _p({'radius': N('圆角半径'), 'index': I('特征序号, 默认最后一个')}, ['radius']), t_add_fillet),

    ('add_chamfer', '给指定特征加倒角。',
     _p({'size': N('倒角边长'), 'index': I('特征序号, 默认最后一个')}, ['size']), t_add_chamfer),

    ('cut_box', '用一个长方体做切除 (挖方腔)。',
     _p({'length': N('长'), 'width': N('宽'), 'height': N('高'),
         'x': N('中心X'), 'y': N('中心Y'), 'z': N('中心Z'), 'axis': S('轴')}, []), t_cut_box),

    ('get_feature_tree', '查看当前特征树。', _p({}, []), t_get_tree),
    ('get_stats', '查看包围盒、体积、表面积等几何统计。', _p({}, []), t_get_stats),
    ('clear_model', '清空当前模型。', _p({'name': S('新零件名')}, []), t_clear),
    ('set_feature_param', '修改已有特征的参数并重新生成。',
     _p({'index': I('特征序号(从1开始)'), 'key': S('参数名, 如 length/width/height/radius/diameter/corner'),
         'value': S('新值')}, ['index', 'key', 'value']), t_set_param),
    ('delete_feature', '删除某个特征。', _p({'index': I('特征序号(从1开始)')}, ['index']), t_delete_feature),
    ('export_model', '导出模型: stl / obj / report(建模报告) / catia(自动化脚本) / aimodel(工程文件)。',
     _p({'format': S('stl | obj | report | catia | aimodel'), 'path': S('完整输出路径'),
         'dir': S('只给目录时自动命名')}, ['format']), t_export),
    ('load_project', '载入 .aimodel 工程文件。', _p({'path': S('工程文件路径')}, ['path']), t_load_project),
    ('list_examples', '列出可以使用的自然语言建模示例。', _p({}, []), t_examples),

    ('explain_real_model',
     '查「真实型号库」：这个型号是什么零件、现场真实几何长什么样'
     '（数据来自 CATIA 导出的 521 个 STEP 的实测解析）。'
     '用于"先搞清是什么，再建模"，避免凭空捏造。',
     _p({'text': S('型号或描述，如 "EGT001 SW80x088" / "KMS-M15-G14-H06" / '
                   '"M10x30" / "UCBH50OCSK0105"')}, ['text']), t_explain_real),
]


def list_tools():
    out = []
    for (name, desc, schema, _fn) in TOOLS:
        out.append({'name': name, 'description': desc, 'inputSchema': schema})
    return out


# --------------------------------------------------------------------------
# JSON-RPC / MCP
# --------------------------------------------------------------------------


def handle_request(msg):
    method = msg.get('method')
    mid = msg.get('id')
    params = msg.get('params') or {}

    def ok(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def err(code, message):
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': message}}

    if method == 'initialize':
        want = params.get('protocolVersion')
        ver = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return ok({'protocolVersion': ver,
                   'capabilities': {'tools': {'listChanged': False}},
                   'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION}})
    if method in ('notifications/initialized', 'initialized'):
        return None
    if method == 'ping':
        return ok({})
    if method == 'tools/list':
        return ok({'tools': list_tools()})
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        fn = None
        for (tn, _d, _s, tfn) in TOOLS:
            if tn == name:
                fn = tfn
                break
        if fn is None:
            return ok({'content': [{'type': 'text', 'text': '未知工具: %s' % name}],
                       'isError': True})
        try:
            text = fn(args)
            return ok({'content': [{'type': 'text', 'text': str(text)}], 'isError': False})
        except Exception as e:
            return ok({'content': [{'type': 'text',
                                    'text': '执行 %s 失败: %s\n%s' % (name, e, traceback.format_exc()[-600:])}],
                       'isError': True})
    if method and method.startswith('notifications/'):
        return None
    if mid is None:
        return None
    return err(-32601, 'Method not found: %s' % method)


def client_config() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, 'ai_modeler', 'mcp_server.py')
    cfg = {'mcpServers': {'ai-modeler': {'command': sys.executable,
                                         'args': [script],
                                         'cwd': root,
                                         'env': {}}}}
    return json.dumps(cfg, ensure_ascii=False, indent=2)


def serve_stdio():
    """标准输入输出上的 MCP 主循环 (每行一个 JSON 消息)。"""
    inp = sys.stdin
    out = sys.stdout
    while True:
        line = inp.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            resp = handle_request(msg)
        except Exception as e:
            resp = {'jsonrpc': '2.0', 'id': msg.get('id'),
                    'error': {'code': -32603, 'message': str(e)}}
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + '\n')
            out.flush()


def main():
    if '--config' in sys.argv:
        print(client_config())
        return
    if '--list-tools' in sys.argv:
        for t in list_tools():
            print('%-24s %s' % (t['name'], t['description'].split('\n')[0]))
        return
    serve_stdio()


if __name__ == '__main__':
    main()
