# -*- coding: utf-8 -*-
r"""把「AI 自动建模助手」安装成 CATIA V5 的宏插件。

做了四件事:
  1. 定位本机 Python 解释器
  2. 把宏模板 + 共享构建核心生成为可用的 .CATScript
     (纯 ASCII + CRLF 编码: CATIA 的 CATScript 加载器不接受 UTF-16/BOM 文件)
  3. 投放到程序目录, 并尽力投放到用户文档目录
  4. 自检「中文 -> 特征表」后端链路是否通畅, 并打印 CATIA 里的注册步骤

用法:  python tools\install_catia_plugin.py
       (或双击 安装CATIA插件.bat)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(BASE, 'catia_plugin')
TPL = os.path.join(PLUGIN_DIR, 'AI自动建模助手.CATScript.tpl')
CORE = os.path.join(PLUGIN_DIR, 'catia_build_core.vbs.txt')
MACRO_NAME = 'AI自动建模助手.CATScript'

_CATIA_INFO = None


def find_python():
    exe = sys.executable
    for cand in (os.path.join(os.path.dirname(exe), 'python.exe'), exe):
        if os.path.isfile(cand):
            return cand
    return exe


def find_catia():
    """定位 CATIA V5 的 CNEXT.exe。

    优先用 ai_modeler.catia 的探测器 (支持 D:\\catia 等自定义目录与 B26/B33 多版本共存,
    并优先采用 config.json 里登记的那一套), 失败时退回老的四目录扫描。
    """
    global _CATIA_INFO
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        from ai_modeler import catia as catia_mod
        _CATIA_INFO = catia_mod.best_install()
        if _CATIA_INFO:
            return _CATIA_INFO['exe']
    except Exception:
        pass
    roots = [r'C:\Program Files\Dassault Systemes', r'C:\Program Files (x86)\Dassault Systemes',
             r'D:\Program Files\Dassault Systemes', r'D:\Dassault Systemes', r'D:\catia', r'C:\catia']
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - root.count(os.sep) > 6:
                continue
            for fn in filenames:
                if fn.lower() == 'cnext.exe':
                    return os.path.join(dirpath, fn)
    return None


def _vbs_expr(s):
    """把字符串转成**纯 ASCII** 的 VBScript 表达式。

    非 ASCII 字符 (例如项目路径里的中文「ai自动建模插件」) 用 ChrW() 拼接,
    这样宏文件本身只有 ASCII 字节, 运行时却能还原出正确的中文路径。
    """
    parts, buf = [], []

    def flush():
        if buf:
            parts.append('"%s"' % ''.join(buf).replace('"', '""'))
            del buf[:]

    for ch in str(s):
        if 32 <= ord(ch) < 127:
            buf.append(ch)
        else:
            flush()
            parts.append('ChrW(%d)' % ord(ch))
    flush()
    return ' & '.join(parts) if parts else '""'


def build_macro(python_exe):
    """生成可用的 .CATScript 文本。

    !! 结果必须是纯 ASCII !!
    CATIA 的 CATScript 加载器不接受 UTF-16 / 带 BOM 的 UTF-8 文件:
    它会把 BOM 读成一个 "?" 字符, 于是整个脚本只剩一个非法字符, 报
        ScriptingERR_1002 / Microsoft VBScript 编译器错误 / 无效字符 / 直线 1
    所以模板里不放中文, 路径里的中文也用 ChrW() 表达 —— 中文提示由
    Python 后端 (--catia-ui) 在运行时提供。
    """
    with open(TPL, 'r', encoding='utf-8') as f:
        text = f.read()
    with open(CORE, 'r', encoding='utf-8') as f:
        core = f.read()
    text = text.replace('__PYTHON__', _vbs_expr(python_exe))
    text = text.replace('__BASEDIR__', _vbs_expr(BASE))
    text = text.replace("'__BUILD_CORE__", core)
    try:
        text.encode('ascii')
    except UnicodeEncodeError as e:
        raise SystemExit('  [错误] 宏模板含非 ASCII 字符 (%s)。\n'
                         '         CATIA 会报「无效字符」, 请把中文文本移到 Python 后端。' % e)
    return text


def write_macro(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 纯 ASCII + CRLF: CATIA CATScript 加载器的要求 (不要写 UTF-16/BOM)
    with open(path, 'w', encoding='ascii', newline='\r\n') as f:
        f.write(text)
    return os.path.getsize(path)


def selftest_bridge(python_exe):
    """验证 中文 -> 特征表 的后端链路。"""
    tmp = tempfile.gettempdir()
    desc = os.path.join(tmp, 'aim_selftest_desc.txt')
    feat = os.path.join(tmp, 'aim_selftest_feat.vbs')
    with open(desc, 'w', encoding='utf-16') as f:
        f.write('法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120')
    if os.path.exists(feat):
        os.remove(feat)
    p = subprocess.run([python_exe, '-m', 'ai_modeler', '--catia-vbs', feat,
                        '--text-file', desc], cwd=BASE, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    ok = os.path.isfile(feat) and os.path.getsize(feat) > 0
    n = 0
    if ok:
        with open(feat, 'r', encoding='utf-16') as f:
            for line in f:
                if line.startswith('FEAT_'):
                    n += 1
    for p_ in (desc, feat):
        try:
            os.remove(p_)
        except OSError:
            pass
    return ok, n, (p.stdout or '') + (p.stderr or '')


def main():
    print('')
    print('  AI 自动建模助手 —— CATIA V5 插件安装')
    print('  ' + '-' * 44)

    python_exe = find_python()
    print('  Python   : %s' % python_exe)

    catia = find_catia()
    if catia:
        print('  CATIA    : 已检测到  %s' % catia)
        if _CATIA_INFO:
            print('  版本     : %s' % _CATIA_INFO.get('label', ''))
            if _CATIA_INFO.get('env'):
                print('  环境     : %s   (-direnv "%s")' % (_CATIA_INFO['env'],
                                                            _CATIA_INFO.get('direnv', '')))
    else:
        print('  CATIA    : 未检测到 (插件会先装好, 等你装完 CATIA 即可直接用)')

    macro = build_macro(python_exe)

    docs = os.path.join(os.path.expanduser('~'), 'Documents', 'CATIA_Macros')
    if os.environ.get('AIMODELER_PACKAGED_DESKTOP') == '1':
        targets = [('用户宏目录', os.path.join(docs, MACRO_NAME))]
        registration_dir = docs
    else:
        targets = [('程序插件目录', os.path.join(PLUGIN_DIR, MACRO_NAME)),
                   ('用户文档目录', os.path.join(docs, MACRO_NAME))]
        registration_dir = PLUGIN_DIR

    saved = []
    for label, path in targets:
        try:
            size = write_macro(path, macro)
            saved.append((label, path, size, True))
        except OSError as e:
            saved.append((label, path, 0, False))
            saved[-1] = (label, path, str(e), False)

    print('')
    for label, path, size, ok in saved:
        if ok:
            print('  [OK]   %s' % label)
            print('         %s  (%d 字节)' % (path, size))
        else:
            print('  [跳过] %s  权限不足, 可稍后以管理员身份重跑' % label)
            print('         %s' % path)

    print('')
    print('  后端链路自检 ...')
    ok, n, out = selftest_bridge(python_exe)
    if ok:
        print('  [OK]   中文 -> 特征表 解析正常 (示例解析出 %d 个 CATIA 操作)' % n)
    else:
        print('  [失败] 后端没有输出特征表')
        print('         %s' % out.strip()[:400])

    print('')
    print('  ' + '-' * 44)
    print('  在 CATIA 里注册插件 (只需做一次):')
    print('    1. 打开 CATIA V5')
    print('    2. 菜单 工具(Tools) -> 宏(Macro) -> 宏...(Macros...)')
    print('    3. 「查找范围」切到下面这个目录:')
    print('         %s' % registration_dir)
    print('    4. 文件类型选 CATScript, 选中 AI自动建模助手.CATScript, 点「运行」')
    print('')
    print('  想让它出现在宏列表里更方便, 可以把这个目录加进宏库:')
    print('    工具 -> 选项 -> 常规 -> 宏 -> 宏库 -> 添加 -> 选上面的目录')
    print('')
    return 0


if __name__ == '__main__':
    sys.exit(main())
