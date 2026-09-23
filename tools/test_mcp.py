# -*- coding: utf-8 -*-
"""通过 stdio 端到端测试 MCP 服务端。

用法:  python tools/test_mcp.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.Popen([sys.executable, '-m', 'ai_modeler.mcp_server'],
                            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)

    def rpc(msg):
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line) if line.strip() else None

    print('== MCP stdio 端到端测试 ==')
    r = rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                        'clientInfo': {'name': 'test', 'version': '1'}}})
    print('initialize  ->', r['result']['serverInfo'])
    r = rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
    names = [t['name'] for t in r['result']['tools']]
    print('tools/list  -> %d 个工具' % len(names))
    for n in names:
        print('   -', n)
    r = rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
        'name': 'build_from_text',
        'arguments': {'text': '画一个 120×80×15 的长方体, 四角圆角 R8, 中心打一个直径 20 的通孔'}}})
    print('build_from_text ->')
    print(r['result']['content'][0]['text'])
    r = rpc({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
        'name': 'export_model',
        'arguments': {'format': 'stl', 'dir': os.path.join(ROOT, '\u8f93\u51fa')}}})
    print('export_model ->', r['result']['content'][0]['text'])
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    print('== 测试结束 ==')


if __name__ == '__main__':
    main()
