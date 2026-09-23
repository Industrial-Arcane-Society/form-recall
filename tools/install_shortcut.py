# -*- coding: utf-8 -*-
"""在桌面创建「AI 自动建模助手」快捷方式。

用法:  python tools/install_shortcut.py
       (或双击文件夹里的 「安装桌面快捷方式.bat」)

原理: 生成一段临时 PowerShell 脚本 (UTF-8 BOM, 避免中文路径乱码),
      调用 WScript.Shell 创建 .lnk, 完成后删除临时文件。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(BASE, 'assets', 'app.ico')
LNK_NAME = 'AI 自动建模助手.lnk'


def find_python():
    if getattr(sys, 'frozen', False):
        return sys.executable
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if os.path.isfile(w):
        return w
    return exe


def desktop_dir():
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
        if buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser('~'), 'Desktop')


PS_TEMPLATE = u'''$ErrorActionPreference = 'Stop'
$lnkPath = @'
{LNK}
'@
$base = @'
{BASE}
'@
$target = @'
{TARGET}
'@
$icon = @'
{ICON}
'@
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.Arguments = '-m ai_modeler'
$lnk.WorkingDirectory = $base
$lnk.Description = 'AI 自动建模助手 —— 用中文描述即可生成三维模型'
if (Test-Path -LiteralPath $icon) {{ $lnk.IconLocation = $icon }}
$lnk.WindowStyle = 1
$lnk.Save()
if (Test-Path -LiteralPath $lnkPath) {{
    Write-Output ('OK|' + $lnkPath + '|' + (Get-Item -LiteralPath $lnkPath).Length)
}} else {{
    Write-Output 'FAIL|快捷方式未生成'
}}
'''


def main():
    py = find_python()
    desk = desktop_dir()
    if not os.path.isdir(desk):
        print('找不到桌面目录: %s' % desk)
        return 1
    lnk = os.path.join(desk, LNK_NAME)
    script = PS_TEMPLATE.format(LNK=lnk, BASE=BASE, TARGET=py, ICON=ICON)

    print('')
    print('  AI 自动建模助手 —— 桌面快捷方式安装')
    print('  ------------------------------------')
    print('  Python  : %s' % py)
    print('  程序目录: %s' % BASE)
    print('  桌面    : %s' % lnk)
    print('')

    fd, tmp = tempfile.mkstemp(suffix='.ps1')
    os.close(fd)
    with open(tmp, 'w', encoding='utf-8-sig') as f:   # BOM: PowerShell 5.1 才能正确读中文
        f.write(script)
    try:
        p = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', tmp], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        out = ((p.stdout or '') + (p.stderr or '')).strip()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if out.startswith('OK|'):
        print('  [完成] 已创建桌面快捷方式:')
        print('         %s' % lnk)
        print('')
        print('  以后双击桌面上的「AI 自动建模助手」即可启动程序。')
        print('')
        return 0
    print('  [失败] %s' % (out or '未知错误'))
    print('')
    print('  可改用文件夹里的「启动 AI自动建模助手.vbs」直接运行。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
