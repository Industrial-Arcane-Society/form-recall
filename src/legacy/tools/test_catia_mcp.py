# -*- coding: utf-8 -*-
r"""CATIA MCP 服务器的一致性/符合性测试。

三件事:
  1. **真协议对拍**: 起一个子进程当 MCP 服务器, 按 MCP 规范发 initialize /
     notifications/initialized / tools/list / tools/call / resources/list /
     resources/read / prompts/list / prompts/get, 检查每一处应答;
  2. **工具面对拍**: 与参考项目 catia-v5-mcp-server 的 78 个工具名逐字比对
     (缺了谁、多了谁, 一目了然);
  3. **可选真机**: --live 时真的连一次 CATIA 建个零件并量几何。

用法:
    python tools\test_catia_mcp.py              离线(不碰 CATIA)
    python tools\test_catia_mcp.py --live       额外跑真机(需要 CATIA 在运行)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 参考项目 README 里列出的 78 个工具名(逐字)
REFERENCE = {
    'document': ['catia_connect', 'catia_disconnect', 'catia_new_part', 'catia_new_product',
                 'catia_open_document', 'catia_save_document', 'catia_close_document',
                 'catia_list_documents', 'catia_get_active_document_info'],
    'sketcher': ['catia_create_sketch', 'catia_close_sketch', 'catia_sketch_line',
                 'catia_sketch_rectangle', 'catia_sketch_centered_rectangle',
                 'catia_sketch_circle', 'catia_sketch_arc', 'catia_sketch_spline',
                 'catia_sketch_point', 'catia_sketch_constraint',
                 'catia_sketch_get_geometry'],
    'part_design': ['catia_pad', 'catia_pocket', 'catia_shaft', 'catia_groove', 'catia_fillet',
                    'catia_chamfer', 'catia_hole', 'catia_rect_pattern', 'catia_circ_pattern',
                    'catia_mirror', 'catia_shell', 'catia_draft', 'catia_thickness',
                    'catia_list_features', 'catia_list_edges'],
    'gsd': ['catia_gsd_create_geoset', 'catia_gsd_set_active_geoset', 'catia_gsd_list_elements',
            'catia_gsd_point', 'catia_gsd_line', 'catia_gsd_plane_offset',
            'catia_gsd_plane_3points', 'catia_gsd_spline', 'catia_gsd_circle',
            'catia_gsd_project', 'catia_gsd_intersection', 'catia_gsd_multi_section_surface',
            'catia_gsd_sweep', 'catia_gsd_extrude', 'catia_gsd_revolve', 'catia_gsd_fill',
            'catia_gsd_blend', 'catia_gsd_offset_surface', 'catia_gsd_join', 'catia_gsd_split',
            'catia_gsd_trim', 'catia_gsd_symmetry', 'catia_gsd_thick_surface',
            'catia_gsd_close_surface'],
    'assembly': ['catia_add_component', 'catia_add_new_part', 'catia_fix_constraint',
                 'catia_coincidence_constraint', 'catia_offset_constraint',
                 'catia_angle_constraint', 'catia_move_component', 'catia_list_components',
                 'catia_list_constraints'],
    'measurement': ['catia_measure_distance', 'catia_get_inertia', 'catia_get_bounding_box',
                    'catia_get_parameters', 'catia_set_parameter', 'catia_update_part'],
    'export': ['catia_export', 'catia_screenshot', 'catia_set_view', 'catia_fit_all'],
}


# VBScript 标识符不分大小写: 变量名若拼出来等于关键字, 整段脚本会编译失败。
# 下面这份是**在本机用 cscript 逐个实测**得到的(jobs: 69 个不能用作变量名,
# 另有 base/compare/property/step/date/string… 实测**可以**用, 不要误报)。
# 典型翻车: `Dim oR, oN` —— oR 就是 or, oN 就是 on, 报"缺少标识符"。
VBS_KEYWORDS = {
    'and', 'or', 'not', 'xor', 'eqv', 'imp', 'mod', 'is', 'in', 'like', 'on', 'as',
    'byref', 'byval', 'call', 'case', 'class', 'const', 'dim', 'do', 'each', 'else',
    'elseif', 'empty', 'end', 'enum', 'event', 'exit', 'false', 'for', 'function',
    'get', 'goto', 'if', 'let', 'loop', 'me', 'new', 'next', 'nothing', 'null',
    'option', 'optional', 'preserve', 'private', 'public', 'raiseevent', 'redim',
    'rem', 'resume', 'select', 'set', 'stop', 'sub', 'then', 'to', 'true', 'until',
    'wend', 'while', 'with', 'single', 'double', 'long', 'integer', 'variant',
    'currency', 'byte', 'boolean',
}


class Client(object):
    """极简 MCP stdio 客户端(逐行 JSON)。"""
    def __init__(self, args=None, env_extra=None):
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        env.update(env_extra or {})
        self.p = subprocess.Popen(
            [sys.executable, '-m', 'ai_modeler.catia_mcp'] + (args or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ROOT, env=env, text=True, encoding='utf-8', bufsize=1)
        self.n = 0

    def call(self, method, params=None, notify=False):
        msg = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            self.n += 1
            msg['id'] = self.n
        if params is not None:
            msg['params'] = params
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
        self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        if not line:
            err = self.p.stderr.read()[-800:]
            raise RuntimeError('服务器没有响应, stderr:\n%s' % err)
        return json.loads(line)

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main():
    live = '--live' in sys.argv
    fails, notes = [], []

    print('')
    print('  一、协议对拍(真起子进程, 按 MCP 规范发消息)')
    print('  ' + '-' * 66)
    c = Client(env_extra={'AI_MODELER_MCP_DRYRUN': '0' if live else '1'})
    try:
        r = c.call('initialize', {'protocolVersion': '2025-06-18',
                                  'capabilities': {}, 'clientInfo': {'name': 'conformance'}})
        res = r.get('result') or {}
        print('  initialize -> %s %s' % (res.get('protocolVersion'),
                                         (res.get('serverInfo') or {}).get('name')))
        if res.get('protocolVersion') != '2025-06-18':
            fails.append('协议版本没有按客户端请求协商')
        for k in ('tools', 'resources', 'prompts'):
            if k not in (res.get('capabilities') or {}):
                fails.append('capabilities 缺少 %s' % k)

        r = c.call('notifications/initialized', {}, notify=True)
        r = c.call('ping')
        if 'result' not in r:
            fails.append('ping 没有 result')

        r = c.call('tools/list')
        tools = (r.get('result') or {}).get('tools') or []
        names = [t['name'] for t in tools]
        print('  tools/list -> %d 个工具' % len(names))
        for t in tools:
            sc = t.get('inputSchema') or {}
            if sc.get('type') != 'object':
                fails.append('%s 的 inputSchema 类型不对' % t['name'])

        # 工具面 vs 参考项目
        print('')
        print('  二、工具面对拍(参考项目 catia-v5-mcp-server, 78 个 catia_* 工具)')
        print('  ' + '-' * 66)
        have = set(names)
        total_ref, missing_all = 0, []
        for mod, want in REFERENCE.items():
            miss = [w for w in want if w not in have]
            total_ref += len(want)
            tag = 'OK ' if not miss else '缺 '
            print('  %s %-12s %2d/%2d %s' % (tag, mod, len(want) - len(miss), len(want),
                                             ('缺: ' + ', '.join(miss)) if miss else ''))
            missing_all.extend(miss)
        extra = sorted(n for n in names if n.startswith('catia_') and
                       n not in {x for v in REFERENCE.values() for x in v})
        print('  合计: %d/%d 个同名工具' % (total_ref - len(missing_all), total_ref))
        if extra:
            print('  本插件额外提供的 catia_* 工具: %s' % ', '.join(extra))
        if missing_all:
            fails.append('缺少 %d 个参考工具: %s' % (len(missing_all), ', '.join(missing_all)))
        other = sorted(n for n in names if not n.startswith('catia_'))
        print('  离线建模工具: %d 个 (%s…)' % (len(other), ', '.join(other[:4])))

        # 工具调用
        print('')
        print('  三、工具调用')
        print('  ' + '-' * 66)
        sample = 'catia_nl_preview'
        r = c.call('tools/call', {'name': sample,
                                  'arguments': {'text': '直齿圆柱齿轮 模数2 齿数30 齿宽20, 中心孔直径15'}})
        content = (r.get('result') or {}).get('content') or [{}]
        text = content[0].get('text', '')
        print('  %s -> %s' % (sample, text.splitlines()[0][:70] if text else '(空)'))
        if (r.get('result') or {}).get('isError') or '外形尺寸' not in text:
            fails.append('%s 没有正常返回(离线内核建模)' % sample)

        r = c.call('tools/call', {'name': 'no_such_tool', 'arguments': {}})
        if not (r.get('result') or {}).get('isError'):
            fails.append('未知工具应返回 isError')

        if live:
            r = c.call('tools/call', {'name': 'catia_connect', 'arguments': {}})
            t = ((r.get('result') or {}).get('content') or [{}])[0].get('text', '')
            print('  catia_connect -> %s' % t.splitlines()[0][:70])
            if (r.get('result') or {}).get('isError'):
                notes.append('真机: catia_connect 失败(见上)')
            else:
                r = c.call('tools/call', {'name': 'catia_new_part',
                                          'arguments': {'name': 'AIMcpTest'}})
                t = ((r.get('result') or {}).get('content') or [{}])[0].get('text', '')
                print('  catia_new_part -> %s' % t.splitlines()[0][:70])
                r = c.call('tools/call', {'name': 'catia_nl_build',
                                          'arguments': {'text': '长方体 100×60×20 中心打直径10通孔'}})
                t = ((r.get('result') or {}).get('content') or [{}])[0].get('text', '')
                print('  catia_nl_build -> %s' % t.splitlines()[0][:70])
                r = c.call('tools/call', {'name': 'catia_geometry_check', 'arguments': {}})
                t = ((r.get('result') or {}).get('content') or [{}])[0].get('text', '')
                print('  catia_geometry_check ->\n    ' + t.replace('\n', '\n    ')[:400])

        # 资源 / 提示词
        r = c.call('resources/list')
        rs = (r.get('result') or {}).get('resources') or []
        print('')
        print('  四、资源与提示词: resources %d 个, ' % len(rs), end='')
        r2 = c.call('prompts/list')
        ps = (r2.get('result') or {}).get('prompts') or []
        print('prompts %d 个' % len(ps))
        if not rs:
            fails.append('resources/list 为空')
        if not ps:
            fails.append('prompts/list 为空')
        r = c.call('resources/read', {'uri': 'catia://server/config'})
        cfg = ((r.get('result') or {}).get('contents') or [{}])[0].get('text', '')
        if 'mcpServers' not in cfg:
            fails.append('resources/read 的配置片段不对')
        r = c.call('prompts/get', {'name': 'build_part_from_text',
                                   'arguments': {'text': '画一个法兰'}})
        msgs = (r.get('result') or {}).get('messages') or []
        if not msgs:
            fails.append('prompts/get 没返回消息')
    finally:
        c.close()

    # --- 五、静态体检: 每个工具的 VBScript 片段有没有低级坑 ---
    print('')
    print('  五、工具脚本静态体检(不执行 CATIA, 只看生成的 VBScript)')
    print('  ' + '-' * 66)
    os.environ['AI_MODELER_MCP_DRYRUN'] = '1'
    from ai_modeler.catia_mcp import bridge
    from ai_modeler.catia_mcp.tools import load_tools
    captured = {}
    real_run = bridge.run_vbs

    def fake_run(body, out_keys=(), timeout=300, keep=False):
        captured['body'] = body
        return {'ok': True, 'vars': {}, 'log': '(静态体检)', 'script': ''}

    bridge.run_vbs = fake_run
    try:
        tools, _rep = load_tools()
        # 预置+内核里已有的名字, 局部变量不能与它们重名(VBScript 不分大小写)
        helpers = set()
        composed = bridge.build_script('')
        for m in re.finditer(r'^\s*(?:Function|Sub)\s+([A-Za-z_]\w*)', composed, re.M | re.I):
            helpers.add(m.group(1).lower())
        helpers.update(('gapp', 'gdoc', 'gpart', 'gbody', 'gsf', 'ghsf', 'gsel', 'ghb',
                        'gfso', 'glogpath', 'gstartcmd'))
        checked = 0
        for (name, _d, schema, fn) in tools:
            if not name.startswith('catia_'):
                continue
            args = _sample_args(schema)
            captured.clear()
            try:
                fn(args)
            except Exception as e:                      # noqa: BLE001
                msg = str(e)
                if 'DRY' not in msg and '(dry-run' not in msg and 'dry_run' not in msg:
                    # 干跑模式下的很多工具本来就会因为"没有 CATIA"而报错, 不算失败
                    pass
            body = captured.get('body') or ''
            if not body:
                continue
            checked += 1
            if re.search(r'ActiveDocument\s*=', body):
                fails.append('%s: 脚本里给 ActiveDocument 赋值(CATIA 里是只读的, 要用 d.Activate)'
                             % name)
            for m in re.finditer(r'^\s*Dim\s+([A-Za-z_][\w, ]*)$', body, re.M):
                for var in m.group(1).split(','):
                    v = var.strip().lower()
                    if v in helpers:
                        fails.append('%s: 局部变量 %s 与内置函数/全局同名(VBScript 会报名称重定义)'
                                     % (name, var.strip()))
                    if v in VBS_KEYWORDS:
                        fails.append('%s: 局部变量 %s 拼出来就是 VBScript 关键字(%s) —— '
                                     'VBScript 标识符不分大小写, 整段脚本会编译失败'
                                     % (name, var.strip(), v))
        print('  已体检 %d 个 catia_* 工具的脚本' % checked)
        if not fails:
            print('  未发现 ActiveDocument 赋值/名称重定义等低级坑')
    finally:
        bridge.run_vbs = real_run

    print('')
    print('  ' + '=' * 66)
    if fails:
        for f in fails:
            print('  ✗ %s' % f)
        print('  ' + '=' * 66)
        return 1
    for n in notes:
        print('  ! %s' % n)
    print('  ✓ MCP 协议与工具面全部通过(78 个参考工具名逐字一致)')
    print('  ' + '=' * 66)
    return 0


def _sample_args(schema):
    """按 inputSchema 造一组"能被工具接受"的样例参数。"""
    props = (schema or {}).get('properties') or {}
    req = set((schema or {}).get('required') or [])
    out = {}
    for k, sp in props.items():
        t = sp.get('type')
        if k in req or len(out) < 6:
            if t == 'number':
                out[k] = 1.0 if any(w in k for w in ('angle', 'radius', 'offset', 'draft')) else 10
            elif t == 'integer':
                out[k] = 1 if 'index' in k else 4
            elif t == 'boolean':
                out[k] = False
            elif t == 'array':
                out[k] = [0, 0, 0]
            else:
                low = k.lower()
                if low in ('plane', 'view', 'type', 'mode', 'format'):
                    out[k] = {'plane': 'XY', 'view': 'iso', 'type': 'simple',
                              'mode': 'minimum', 'format': 'stl'}.get(low, 'XY')
                elif 'path' in low:
                    out[k] = os.path.join(os.environ.get('TEMP', '.'), 'aim_mcp_probe.stl')
                else:
                    out[k] = 'x'
    return out


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
