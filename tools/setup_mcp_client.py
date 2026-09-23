# -*- coding: utf-8 -*-
r"""一键把本插件的 CATIA MCP 服务器写进 AI 客户端的 MCP 配置。

支持:
    Claude Desktop   %APPDATA%\Claude\claude_desktop_config.json
    Claude Code      (打印 `claude mcp add` 命令, 由用户执行)
    通用客户端       打印 JSON 片段, 或写入 --out 指定的文件

用法:
    python tools\setup_mcp_client.py                检测并写入 Claude Desktop 配置(先备份)
    python tools\setup_mcp_client.py --dry-run      只看会写什么, 不动文件
    python tools\setup_mcp_client.py --out D:\x.json  写到指定文件(合并 mcpServers 段)
    python tools\setup_mcp_client.py --name my-catia  自定义服务名(默认 catia-v5)
    python tools\setup_mcp_client.py --offline       配置"离线建模"服务器而不是 CATIA 服务器
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def entry(name: str, offline: bool = False) -> dict:
    """要写进 mcpServers 的那一段。"""
    if offline:
        from ai_modeler import mcp_server
        cfg = json.loads(mcp_server.client_config())
    else:
        from ai_modeler.catia_mcp import server as mcp_catia
        cfg = json.loads(mcp_catia.client_config())
    return {name: list(cfg['mcpServers'].values())[0]}


def claude_desktop_config() -> str:
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(appdata, 'Claude', 'claude_desktop_config.json')


def merge_into(path: str, new_entry: dict, dry: bool = False) -> str:
    data = {}
    existed = os.path.isfile(path)
    if existed:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
        except Exception as e:                                   # noqa: BLE001
            raise SystemExit('现有配置读不了(%s): %s\n请先手工备份/修正该文件。' % (path, e))
    servers = data.setdefault('mcpServers', {})
    old = servers.get(list(new_entry)[0])
    servers.update(new_entry)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if dry:
        return text
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    if existed:
        bak = path + '.bak-%s' % time.strftime('%Y%m%d-%H%M%S')
        shutil.copy2(path, bak)
        print('  已备份原配置: %s' % bak)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    if old:
        print('  已更新已有的服务项 %s' % list(new_entry)[0])
    elif existed:
        print('  已把 %s 加入现有的 mcpServers(其它服务保持不动)' % list(new_entry)[0])
    else:
        print('  已新建配置文件并写入 %s' % list(new_entry)[0])
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只显示, 不写文件')
    ap.add_argument('--out', help='写到指定文件(默认 Claude Desktop 配置)')
    ap.add_argument('--name', default='catia-v5', help='MCP 服务名(默认 catia-v5)')
    ap.add_argument('--offline', action='store_true', help='配置离线建模服务器')
    ap.add_argument('--code', action='store_true', help='额外打印 Claude Code 的注册命令')
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    try:
        ent = entry(a.name, a.offline)
    except Exception as e:                                        # noqa: BLE001
        print('✗ 生成 MCP 配置失败: %s' % e)
        return 1

    target = a.out or claude_desktop_config()
    print('')
    print('  服务名  : %s' % a.name)
    print('  类型    : %s' % ('离线建模(不装 CATIA 也能用)' if a.offline else 'CATIA V5 自动化'))
    print('  写入位置: %s%s' % (target, '   [dry-run, 未写入]' if a.dry_run else ''))
    print('')
    text = merge_into(target, ent, dry=a.dry_run)
    print('')
    print(text)
    if a.code:
        print('')
        print('  Claude Code 用这两行注册(在项目目录下执行):')
        mod = 'ai_modeler.mcp_server' if a.offline else 'ai_modeler.catia_mcp'
        print('    claude mcp add %s python -- -m %s' % (a.name, mod))
    if not a.dry_run:
        print('  提示: 重启客户端后, 让它调用 %s_connect 或 build_from_text 验证是否接上。'
              % ('catia' if not a.offline else 'build'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
