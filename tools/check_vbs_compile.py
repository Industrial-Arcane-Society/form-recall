# -*- coding: utf-8 -*-
r"""编译体检: 把每个工具生成的 VBScript 交给 cscript **只编译不执行**, 抓语法错误。

技巧: 把工具片段包进 `Sub AimCheck() ... End Sub` 且**不调用**它 ——
预置/内核里的 Sub/Function 也不会被执行, 所以既验证了语法, 又完全不碰 CATIA。
(这是唯一能覆盖"整段脚本必须可编译"这类坑的办法: 运行时报错会被 try 吞掉,
 但编译错误会让所有 Out 都拿不到, 现场表现是"脚本没有正常结束"。)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = r'C:\Users\25643\Desktop\ai自动建模插件'
sys.path.insert(0, ROOT)
os.environ['AI_MODELER_MCP_DRYRUN'] = '1'

from ai_modeler.catia_mcp import bridge                    # noqa: E402
from ai_modeler.catia_mcp.tools import MODULES, EXTRA_MODULES, load_tools  # noqa: E402

captured = {}


def fake(body, out_keys=(), timeout=300, keep=False):
    captured['body'] = body
    return {'ok': True, 'vars': {}, 'log': '', 'script': ''}


def sample_args(schema):
    props = (schema or {}).get('properties') or {}
    req = set((schema or {}).get('required') or [])
    out = {}
    for k, sp in props.items():
        if k not in req and len(out) >= 8:
            continue
        t = sp.get('type')
        low = k.lower()
        if t == 'number':
            out[k] = 45.0 if 'angle' in low else (2.0 if 'radius' in low else 10)
        elif t == 'integer':
            out[k] = 1
        elif t == 'boolean':
            out[k] = False
        elif t == 'array':
            out[k] = [0, 0, 0]
        else:
            out[k] = ({'plane': 'XY', 'view': 'iso', 'type': 'simple', 'mode': 'minimum',
                       'format': 'stl', 'reference': 'XY'}.get(low)
                      or (os.path.join(tempfile.gettempdir(), 'aim_compile.stl')
                          if 'path' in low else ('Part1/XY' if 'element' in low else 'x')))
    return out


def compile_only(body: str) -> str:
    """返回 '' = 通过, 否则 cscript 的报错原文。"""
    script = bridge.build_script('Sub AimCheck()\n' + body + '\nEnd Sub\n')
    p = os.path.join(tempfile.gettempdir(), 'aim_compile_check.vbs')
    with open(p, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(script)
    r = subprocess.run(['cscript', '//nologo', p], capture_output=True)
    out = (r.stdout + r.stderr).decode('gbk', 'replace').strip()
    # 编译通过时预置里的顶层语句会执行(连 CATIA), 这里不关心输出, 只看有没有"编译器错误"
    if '编译器错误' in out:
        return out.splitlines()[-1] if out.splitlines() else out
    return ''


def main():
    tools, report = load_tools()
    real = bridge.run_vbs
    bridge.run_vbs = fake
    bad, okc, skipped = [], 0, 0
    try:
        import importlib
        for m in MODULES + EXTRA_MODULES:
            try:
                mod = importlib.import_module('ai_modeler.catia_mcp.tools.' + m)
            except Exception:                                # noqa: BLE001
                continue
            if hasattr(mod, 'run_vbs'):
                mod.run_vbs = fake
        for (name, _d, schema, fn) in tools:
            if not name.startswith('catia_'):
                continue
            captured.clear()
            try:
                fn(sample_args(schema))
            except Exception:                                # noqa: BLE001
                pass
            body = captured.get('body') or ''
            if not body:
                skipped += 1
                continue
            err = compile_only(body)
            if err:
                bad.append((name, err))
                print('✗ %-34s %s' % (name, err[-96:]))
            else:
                okc += 1
    finally:
        bridge.run_vbs = real
    print('')
    print('编译体检: 通过 %d, 失败 %d, 没抓到脚本 %d' % (okc, len(bad), skipped))
    for line in report:
        print('  · %s' % line)
    return 1 if bad else 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
