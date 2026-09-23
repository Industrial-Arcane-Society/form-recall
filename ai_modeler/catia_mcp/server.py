# -*- coding: utf-8 -*-
r"""CATIA V5 MCP 服务器 —— 兼容 catia-v5-mcp-server 的工具面 + 本插件的离线建模能力。

协议: MCP(Model Context Protocol), JSON-RPC 2.0 over stdio, 一行一个消息。
支持的方法(客户端会按需调用):
    initialize / notifications/initialized / ping
    tools/list / tools/call
    resources/list / resources/read
    prompts/list / prompts/get
与参考项目(catia_mcp/server.py)的差别:
    · 传输层纯标准库, 不需要 `pip install mcp`
    · COM 通道用 cscript + VBScript(不依赖 pywin32), 真机验证过中文版 CATIA 的一堆坑
    · 除 78 个 catia_* 工具外, 另有离线建模工具(不装 CATIA 也能用)与资源/提示词
"""
from __future__ import annotations

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))       # .../ai_modeler/catia_mcp
ROOT = os.path.dirname(os.path.dirname(HERE))           # 项目根目录
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from . import bridge                                    # noqa: E402
from .tools import load_tools                           # noqa: E402

SERVER_NAME = 'catia-v5-mcp-server'
SERVER_VERSION = '2.0.0'
PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05']
DEFAULT_PROTOCOL = '2025-06-18'


def all_tools():
    """catia_* 工具 + 本插件离线建模工具。"""
    catia_tools, report = load_tools()
    offline = []
    try:
        from ..mcp_server import TOOLS as OFF
        offline = [(n, d, s, f) for (n, d, s, f) in OFF]
    except Exception as e:                                # noqa: BLE001
        report.append('离线建模工具载入失败: %s' % e)
    return catia_tools + offline, report


def list_tools_payload():
    tools, _ = all_tools()
    out = []
    for (name, desc, schema, _fn) in tools:
        out.append({'name': name, 'description': desc, 'inputSchema': schema})
    return out


def _find(name):
    tools, _ = all_tools()
    for (tn, _d, _s, fn) in tools:
        if tn == name:
            return fn
    return None


def _resources():
    return [
        {'uri': 'catia://host/install', 'name': 'CATIA 安装与通道信息',
         'description': '本机 CATIA 版本、安装目录、环境名与可用通道', 'mimeType': 'text/plain'},
        {'uri': 'catia://server/config', 'name': 'MCP 客户端配置',
         'description': '把这个服务器加进 Claude Desktop / Claude Code 的配置片段',
         'mimeType': 'application/json'},
        {'uri': 'catia://server/tools', 'name': '工具清单',
         'description': '当前服务器暴露的全部工具(含本插件扩展)', 'mimeType': 'text/plain'},
    ]


def read_resource(uri: str) -> str:
    if uri == 'catia://host/install':
        return bridge.host_info()
    if uri == 'catia://server/config':
        return client_config()
    if uri == 'catia://server/tools':
        lines = []
        for t in list_tools_payload():
            lines.append('%-34s %s' % (t['name'], t['description'].split('\n')[0]))
        return '\n'.join(lines)
    raise ValueError('未知资源: %s' % uri)


def _prompts():
    return [
        {'name': 'build_part_from_text',
         'description': '用中文描述直接建模并推到 CATIA(build_from_text + catia_* 工具链)',
         'arguments': [{'name': 'text', 'description': '中文建模需求', 'required': True}]},
        {'name': 'design_bracket',
         'description': '设计一个典型的安装支架(底板 + 四角孔 + 立筋)',
         'arguments': [{'name': 'length', 'description': '底板长', 'required': False}]},
        {'name': 'export_for_manufacturing',
         'description': '把当前零件导出 STEP/STL 并截图',
         'arguments': [{'name': 'path', 'description': '导出路径', 'required': False}]},
    ]


def get_prompt(name: str, args: dict) -> str:
    a = args or {}
    if name == 'build_part_from_text':
        text = a.get('text') or '画一个 100×60×20 的长方体, 中心打一个直径 10 的通孔'
        return ('先调用 build_from_text 用中文描述建模并检查尺寸, 再用 catia_new_part + '
                'build_from_text 的结果推送到 CATIA, 最后 catia_get_bounding_box 复核。\n'
                '需求: %s' % text)
    if name == 'design_bracket':
        L = a.get('length') or 120
        return ('支架设计步骤:\n1. catia_new_part("bracket")\n'
                '2. 底板 %s×80×5: catia_create_sketch(plane="XY") + catia_sketch_centered_rectangle'
                '(length=%s, width=80) + catia_close_sketch + catia_pad(height=5)\n'
                '3. 四角 M6 孔: catia_hole(diameter=6.6, pos="corners", margin=10)\n'
                '4. 两条立筋 30 高: catia_create_sketch(plane="XZ") + catia_sketch_rectangle'
                ' + catia_pad(height=30)\n5. catia_get_bounding_box 复核外形。' % (L, L))
    if name == 'export_for_manufacturing':
        p = a.get('path') or 'C:/export/part'
        return ('导出流程:\n1. catia_update_part 先强制重建\n'
                '2. catia_export(format="step", path="%s.stp")\n'
                '3. catia_export(format="stl", path="%s.stl")\n'
                '4. catia_set_view(view="iso") + catia_screenshot(path="%s.png")' % (p, p, p))
    raise ValueError('未知提示词: %s' % name)


def client_config() -> str:
    cfg = {'mcpServers': {'catia-v5': {
        'command': sys.executable,
        'args': ['-m', 'ai_modeler.catia_mcp'],
        'cwd': ROOT,
        'env': {'PYTHONIOENCODING': 'utf-8'}}}}
    return json.dumps(cfg, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# JSON-RPC 路由
# --------------------------------------------------------------------------

def handle_request(msg: dict, state: dict):
    method = msg.get('method')
    mid = msg.get('id')
    params = msg.get('params') or {}

    def ok(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def err(code, message, data=None):
        e = {'code': code, 'message': message}
        if data:
            e['data'] = data
        return {'jsonrpc': '2.0', 'id': mid, 'error': e}

    if method == 'initialize':
        want = params.get('protocolVersion')
        ver = want if want in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL
        state['client'] = (params.get('clientInfo') or {}).get('name', '')
        return ok({
            'protocolVersion': ver,
            'capabilities': {
                'tools': {'listChanged': False},
                'resources': {'subscribe': False, 'listChanged': False},
                'prompts': {'listChanged': False},
                'logging': {},
            },
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': ('CATIA V5 自动化 + 离线参数化建模。中文描述建模可直接用 '
                             'build_from_text; 要动 CATIA 就先 catia_connect, '
                             '再 catia_new_part, 然后逐步加特征, 最后 catia_export。'),
        })

    if method in ('notifications/initialized', 'initialized', 'notifications/cancelled'):
        return None

    if method == 'ping':
        return ok({})

    if method == 'tools/list':
        return ok({'tools': list_tools_payload()})

    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        fn = _find(name)
        if fn is None:
            return ok({'content': [{'type': 'text', 'text': '未知工具: %s' % name}],
                       'isError': True})
        try:
            text = fn(args)
            return ok({'content': [{'type': 'text', 'text': str(text)}], 'isError': False})
        except Exception as e:                            # noqa: BLE001
            detail = traceback.format_exc()[-800:]
            return ok({'content': [{'type': 'text',
                                    'text': '执行 %s 失败: %s\n%s' % (name, e, detail)}],
                       'isError': True})

    if method == 'resources/list':
        return ok({'resources': _resources()})

    if method == 'resources/read':
        uri = params.get('uri')
        try:
            text = read_resource(uri)
        except Exception as e:                            # noqa: BLE001
            return err(-32602, '读取资源失败: %s' % e)
        return ok({'contents': [{'uri': uri, 'mimeType': 'text/plain', 'text': text}]})

    if method == 'prompts/list':
        return ok({'prompts': _prompts()})

    if method == 'prompts/get':
        try:
            text = get_prompt(params.get('name'), params.get('arguments'))
        except Exception as e:                            # noqa: BLE001
            return err(-32602, '提示词不存在: %s' % e)
        return ok({'description': params.get('name'),
                   'messages': [{'role': 'user',
                                 'content': {'type': 'text', 'text': text}}]})

    if method and str(method).startswith('notifications/'):
        return None
    if mid is None:
        return None
    return err(-32601, 'Method not found: %s' % method)


def serve_stdio():
    state = {}
    inp, out = sys.stdin, sys.stdout
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
            resp = handle_request(msg, state)
        except Exception as e:                            # noqa: BLE001
            resp = {'jsonrpc': '2.0', 'id': msg.get('id'),
                    'error': {'code': -32603, 'message': str(e)}}
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + '\n')
            out.flush()


# --------------------------------------------------------------------------
# 自检(离线): 协议 + 工具面
# --------------------------------------------------------------------------

def selftest(verbose: bool = True) -> int:
    fails = []
    tools, report = all_tools()
    names = [t[0] for t in tools]
    if verbose:
        print('  工具模块加载情况:')
        for line in report:
            print('    - %s' % line)
        print('  工具总数: %d (catia_*: %d)' % (len(names),
                                           len([n for n in names if n.startswith('catia_')])))

    init = handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                           'params': {'protocolVersion': '2024-11-05',
                                      'clientInfo': {'name': 'selftest'}}}, {})
    r = init.get('result') or {}
    if r.get('protocolVersion') != '2024-11-05':
        fails.append('initialize 没有回协商后的协议版本: %r' % r.get('protocolVersion'))
    for k in ('tools', 'resources', 'prompts'):
        if k not in (r.get('capabilities') or {}):
            fails.append('capabilities 缺少 %s' % k)
    if (r.get('serverInfo') or {}).get('name') != SERVER_NAME:
        fails.append('serverInfo.name 不对')

    lst = handle_request({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, {})
    got = (lst.get('result') or {}).get('tools') or []
    if len(got) != len(tools):
        fails.append('tools/list 返回 %d 个, 实际 %d 个' % (len(got), len(tools)))
    seen = set()
    for t in got:
        n = t.get('name')
        if not n or n in seen:
            fails.append('工具名重复/为空: %r' % n)
        seen.add(n)
        if not t.get('description'):
            fails.append('%s 缺少 description' % n)
        sc = t.get('inputSchema') or {}
        if sc.get('type') != 'object' or 'properties' not in sc:
            fails.append('%s 的 inputSchema 不是 object/properties' % n)
        if not str(n).startswith(('catia_', 'build_', 'add_', 'get_', 'set_', 'clear_',
                                  'delete_', 'export_', 'load_', 'list_', 'cut_')):
            fails.append('%s 命名不符合惯例' % n)

    # 未知工具要回 isError, 而不是崩
    bad = handle_request({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                          'params': {'name': 'no_such_tool', 'arguments': {}}}, {})
    if not ((bad.get('result') or {}).get('isError')):
        fails.append('未知工具没有返回 isError')

    # 未知方法要回 -32601
    nf = handle_request({'jsonrpc': '2.0', 'id': 4, 'method': 'no/such'}, {})
    if (nf.get('error') or {}).get('code') != -32601:
        fails.append('未知方法没有回 -32601')

    # 通知不该有响应
    if handle_request({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, {}) is not None:
        fails.append('notifications/initialized 不该有响应')

    # 资源与提示词
    rl = handle_request({'jsonrpc': '2.0', 'id': 5, 'method': 'resources/list'}, {})
    if not ((rl.get('result') or {}).get('resources')):
        fails.append('resources/list 为空')
    rr = handle_request({'jsonrpc': '2.0', 'id': 6, 'method': 'resources/read',
                         'params': {'uri': 'catia://server/tools'}}, {})
    if not ((rr.get('result') or {}).get('contents')):
        fails.append('resources/read 没返回内容')
    pl = handle_request({'jsonrpc': '2.0', 'id': 7, 'method': 'prompts/list'}, {})
    if not ((pl.get('result') or {}).get('prompts')):
        fails.append('prompts/list 为空')
    pg = handle_request({'jsonrpc': '2.0', 'id': 8, 'method': 'prompts/get',
                         'params': {'name': 'design_bracket', 'arguments': {}}}, {})
    if not ((pg.get('result') or {}).get('messages')):
        fails.append('prompts/get 没返回消息')

    if verbose:
        print('')
        if fails:
            for f in fails:
                print('  ✗ %s' % f)
        else:
            print('  ✓ MCP 协议自检全部通过')
    return 1 if fails else 0
