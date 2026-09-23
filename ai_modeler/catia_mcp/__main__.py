# -*- coding: utf-8 -*-
r"""入口: python -m ai_modeler.catia_mcp [--config|--list-tools|--selftest|--tools]

默认行为: 在 stdio 上跑 MCP 服务器(给 Claude Desktop / Claude Code / 任何 MCP 客户端用)。
"""
from __future__ import annotations

import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from ai_modeler.catia_mcp import bridge, server      # noqa: E402


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    if '--config' in argv:
        print(server.client_config())
        return 0
    if '--list-tools' in argv or '--tools' in argv:
        tools, report = server.all_tools()
        catia = [t for t in tools if t[0].startswith('catia_')]
        other = [t for t in tools if not t[0].startswith('catia_')]
        print('CATIA 工具 %d 个:' % len(catia))
        for (n, d, _s, _f) in catia:
            print('  %-34s %s' % (n, d.split('\n')[0][:70]))
        print('')
        print('本插件扩展工具 %d 个:' % len(other))
        for (n, d, _s, _f) in other:
            print('  %-34s %s' % (n, d.split('\n')[0][:70]))
        print('')
        for line in report:
            print('  · %s' % line)
        return 0
    if '--selftest' in argv:
        return server.selftest()
    if '--catia-info' in argv:
        print(bridge.host_info())
        return 0
    if '--dry-run' in argv:
        os.environ['AI_MODELER_MCP_DRYRUN'] = '1'
        argv = [a for a in argv if a != '--dry-run']

    if '-h' in argv or '--help' in argv:
        print(__doc__)
        print('用法:')
        print('  python -m ai_modeler.catia_mcp                 以 MCP 服务器方式运行(stdio)')
        print('  python -m ai_modeler.catia_mcp --config        打印 MCP 客户端配置片段')
        print('  python -m ai_modeler.catia_mcp --list-tools    列出全部工具')
        print('  python -m ai_modeler.catia_mcp --selftest      协议 + 工具面自检')
        print('  python -m ai_modeler.catia_mcp --catia-info    探测本机 CATIA')
        return 0

    server.serve_stdio()
    return 0


if __name__ == '__main__':
    sys.exit(main())
