# -*- coding: utf-8 -*-
r"""一键把「AI 自动建模助手」与 CATIA V5 接通。

做六件事:
  1. 探测本机所有 CATIA V5 安装 (支持 D:\catia 这类自定义目录, 支持 B26/B33 多版本共存)
  2. 选定目标版本 (默认取最新版; 可用 --version 2023 / --level B26 / --exe 指定)
  3. 把 CATIA 的路径 / 环境名 / 启动参数写进 config.json 的 catia 段
  4. 重新生成并投放 .CATScript 宏插件 (程序插件目录 + 我的文档\CATIA_Macros)
  5. 扫描 CATIA 自带的 catalog 模型库, 生成索引 kb\catia_catalogs.json
  6. 自检「中文 -> CATIA 特征表」后端链路

用法:
    python tools\connect_catia.py                 一键接通 (默认最新版 CATIA)
    python tools\connect_catia.py --version 2023  指定 2023 版
    python tools\connect_catia.py --level B26     指定 B26 (2016)
    python tools\connect_catia.py --dry-run       只看探测结果, 不写任何文件
    python tools\connect_catia.py --no-lib        不扫描模型库
"""
from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(BASE, 'tools')
for p in (BASE, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

CONFIG_PATH = os.path.join(BASE, 'config.json')

from ai_modeler import catia  # noqa: E402
from ai_modeler import catialib  # noqa: E402
import install_catia_plugin as ip  # noqa: E402


def pick_target(installs, argv):
    """按命令行参数挑一套安装。"""
    exe = level = ver = None
    for i, a in enumerate(argv):
        if a == '--exe' and i + 1 < len(argv):
            exe = argv[i + 1]
        elif a == '--level' and i + 1 < len(argv):
            level = argv[i + 1].upper()
        elif a == '--version' and i + 1 < len(argv):
            ver = argv[i + 1]
    if exe:
        for i in installs:
            if os.path.normcase(i['exe']) == os.path.normcase(os.path.abspath(exe)):
                return i, '命令行 --exe 指定'
        return None, '指定的 --exe 不在探测结果里: %s' % exe
    if level:
        for i in installs:
            if i['level'].upper() == level:
                return i, '命令行 --level 指定'
        return None, '未找到 %s 版本的 CATIA' % level
    if ver:
        for i in installs:
            if ver in i['label']:
                return i, '命令行 --version 指定'
        return None, '未找到包含 %s 的 CATIA 版本' % ver
    if not installs:
        return None, '本机没有探测到 CATIA V5'
    return installs[0], '默认取最新版本'


def load_cfg():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def write_config(info, installs):
    cfg = load_cfg()
    start_exe = os.path.join(info['win_dir'], 'code', 'bin', 'CATSTART.exe')
    cfg['catia'] = {
        'enabled': True,
        'label': info['label'],
        'exe': info['exe'],
        'install_root': info['install_root'],
        'win_dir': info['win_dir'],
        'start': start_exe,
        'start_args': catia.start_args(info),
        'env': info.get('env', ''),
        'direnv': info.get('direnv', ''),
        'release': info.get('release', ''),
        'level': info.get('level', ''),
        'macro_dir': os.path.join(os.path.expanduser('~'), 'Documents', 'CATIA_Macros'),
        'other_installs': [
            {'label': i['label'], 'exe': i['exe']}
            for i in installs if os.path.normcase(i['exe']) != os.path.normcase(info['exe'])
        ],
    }
    cfg['model_library'] = {
        'enabled': True,
        'catalog_dirs': [os.path.join(info['win_dir'], 'startup', 'components')],
        'index': os.path.join('kb', 'catia_catalogs.json'),
        'include_part_files': bool((cfg.get('model_library') or {}).get('include_part_files', False)),
    }
    return save_cfg(cfg), cfg


def main():
    argv = sys.argv[1:]
    dry = '--dry-run' in argv
    with_lib = '--no-lib' not in argv

    print('')
    print('  AI 自动建模助手 × CATIA V5  一键接通')
    print('  ' + '-' * 52)

    installs = catia.find_installs()
    print('  [1/6] 探测 CATIA 安装 ...')
    if not installs:
        print('        × 没有找到 CATIA V5。请先安装 CATIA, 或用 --exe 指定 CNEXT.exe。')
        return 1
    for i in installs:
        mark = ''
        print('        · %-18s %s%s' % (i['label'], i['exe'], mark))

    info, why = pick_target(installs, argv)
    if not info:
        print('        × %s' % why)
        return 1

    print('')
    print('  [2/6] 选定目标 ...')
    print('        %s   (%s)' % (info['label'], why))
    print('        程序  : %s' % info['exe'])
    print('        环境  : %s' % (info.get('env') or '(未找到环境文件)'))
    print('        启动  : "%s" %s' % (os.path.join(info['win_dir'], 'code', 'bin', 'CATSTART.exe'),
                                       catia.start_args(info)))

    if dry:
        print('\n  --dry-run: 未写入任何文件。')
        return 0

    print('')
    print('  [3/6] 写入 config.json ...')
    path, cfg = write_config(info, installs)
    print('        ✓ catia 段已写入 %s' % path)
    print('        ✓ model_library 段已写入')

    print('')
    print('  [4/6] 生成并投放 CATIA 宏插件 ...')
    python_exe = ip.find_python()
    macro = ip.build_macro(python_exe)
    targets = [
        ('程序插件目录', os.path.join(BASE, 'catia_plugin', ip.MACRO_NAME)),
        ('用户宏目录', os.path.join(os.path.expanduser('~'), 'Documents', 'CATIA_Macros',
                                    ip.MACRO_NAME)),
    ]
    for label, p in targets:
        try:
            size = ip.write_macro(p, macro)
            print('        ✓ %-8s %s  (%d 字节)' % (label, p, size))
        except OSError as e:
            print('        × %-8s %s  (%s)' % (label, p, e))
    print('        Python 后端: %s' % python_exe)

    if with_lib:
        print('')
        print('  [5/6] 扫描 CATIA catalog 模型库 ...')
        idx = catialib.build_index()
        print('        ✓ %d 个 catalog 条目, %d 个分类' % (idx['count'], len(idx.get('categories') or {})))
        print('        ✓ 索引: %s' % catialib.INDEX_PATH)
        top = list((idx.get('categories') or {}).items())[:6]
        if top:
            print('        样本分类: ' + ', '.join('%s(%d)' % (k, v) for k, v in top))
    else:
        print('')
        print('  [5/6] 跳过模型库扫描 (--no-lib)')

    print('')
    print('  [6/6] 自检「中文 -> CATIA 特征表」后端链路 ...')
    ok, n, out = ip.selftest_bridge(python_exe)
    if ok:
        print('        ✓ 正常 (示例解析出 %d 个 CATIA 操作)' % n)
    else:
        print('        × 后端没有输出特征表: %s' % (out.strip()[:300] if out else ''))

    print('')
    print('  ' + '-' * 52)
    print('  接通完成。剩下一步要在 CATIA 里点一次 (只需做一次):')
    print('    1. 启动 %s' % info['label'])
    print('    2. 菜单  工具(Tools) -> 宏(Macro) -> 宏...(Macros...)')
    print('    3. 「查找范围」切到:  %s' % os.path.join(BASE, 'catia_plugin'))
    print('       文件类型选 CATScript, 选中 AI自动建模助手.CATScript, 点「运行」')
    print('    4. 以后想更方便: 工具 -> 选项 -> 常规 -> 宏 -> 宏库 -> 添加, 把上面这个目录加进去')
    print('')
    print('  详细图文步骤见: 操作说明_CATIA连接版.md')
    print('')
    return 0


if __name__ == '__main__':
    sys.exit(main())
