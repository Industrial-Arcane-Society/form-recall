# -*- coding: utf-8 -*-
"""CATIA V5 探测与驱动。

本程序默认使用内置几何内核离线建模; 当检测到本机安装了 CATIA V5 时,
可以把特征树导出成 CATIA 自动化脚本, 或直接通过 COM 驱动 CATIA 建模。
COM 方式需要 pywin32:  pip install pywin32

探测范围 (v1.3.0 起):
  * 常见安装根目录, 含自定义目录 (例如 D:\\catia)
  * 所有本地盘符下的 <盘>:\\catia 与 <盘>:\\*\\win_b64\\code\\bin\\CNEXT.exe
  * C:\\Program Files\\Dassault Systemes\\B*  (多版本共存, 例如 B26 / B33)
并优先采用 config.json 的 catia 段里登记的那一套。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

CATIA_PROGID = 'CATIA.Application'

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT_DIR, 'config.json')

CNEXT_REL = os.path.join('win_b64', 'code', 'bin', 'CNEXT.exe')

# 常见安装根目录 (自定义安装目录如 D:\catia 也在这里登记)
COMMON_ROOTS = [
    r'C:\Program Files\Dassault Systemes',
    r'C:\Program Files (x86)\Dassault Systemes',
    r'D:\Program Files\Dassault Systemes',
    r'D:\Dassault Systemes',
    r'D:\catia',
    r'C:\catia',
    r'E:\catia',
    r'F:\catia',
]

# 版本代号 -> 年份 (用于排序, 越大越新)
_LEVEL_YEAR = {'B26': 2016, 'B27': 2017, 'B28': 2018, 'B29': 2019,
               'B30': 2020, 'B31': 2021, 'B32': 2022, 'B33': 2023, 'B34': 2024}


def _local_drives():
    """返回本机存在的盘符根, 例如 ['C:\\', 'D:\\']。"""
    out = []
    for letter in 'CDEFGH':
        d = letter + ':\\'
        if os.path.isdir(d):
            out.append(d)
    return out


def _env_candidates():
    """CATIA 环境文件所在目录。"""
    dirs = [os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'),
                         'DassaultSystemes', 'CATEnv')]
    dirs += [os.path.join(d, 'CATEnv') for d in _local_drives()]
    return [d for d in dirs if os.path.isdir(d)]


def _parse_env_name(name):
    """CATIA_P3.V5-6R2023.B33.txt -> ('CATIA_P3.V5-6R2023.B33', 'V5-6R2023', 'B33')"""
    base = name[:-4] if name.lower().endswith('.txt') else name
    m = re.match(r'(?i)^(CATIA\w*)\.(V5-6R\d{4})\.(B\d{2})$', base)
    if m:
        return base, m.group(2), m.group(3)
    m = re.match(r'(?i)^(CATIA\w*)\.(V5R\d{2})\.(B\d{2})$', base)
    if m:
        return base, m.group(2), m.group(3)
    return base, '', ''


def _lookup_env(install_root, win_dir=None):
    """找该安装对应的环境名 (-env) 与环境目录 (-direnv)。"""
    needles = []
    for d in (install_root, os.path.dirname(win_dir or ''), win_dir):
        if d:
            needles.append(d.replace('/', '\\').lower())
    # 1) 安装目录自带的 *.txt 环境文件 (D:\catia\CATIA_P3.V5-6R2023.B33.txt)
    for d in (install_root, win_dir):
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.lower().startswith('catia') and fn.lower().endswith('.txt'):
                env, release, level = _parse_env_name(fn)
                return env, d, release, level
    # 2) 全局 CATEnv 目录里 CATInstallPath 指向本安装的那些
    for d in _env_candidates():
        for fn in sorted(os.listdir(d)):
            if not (fn.lower().startswith('catia') and fn.lower().endswith('.txt')):
                continue
            path = os.path.join(d, fn)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    txt = f.read()
            except OSError:
                continue
            m = re.search(r'(?im)^\s*CATInstallPath\s*=\s*(.+?)\s*$', txt)
            if not m:
                continue
            if m.group(1).replace('/', '\\').lower() in needles:
                env, release, level = _parse_env_name(fn)
                return env, d, release, level
    return '', '', '', ''


def _install_info(exe):
    """由 CNEXT.exe 路径推导出一套安装信息。"""
    win_dir = os.path.dirname(os.path.dirname(os.path.dirname(exe)))   # ...\win_b64
    install_root = os.path.dirname(win_dir)
    env, direnv, release, level = _lookup_env(install_root, win_dir)
    if not level:
        m = re.search(r'(?i)\\(B\d{2})(\\|$)', install_root + '\\')
        level = m.group(1).upper() if m else ''
    year = _LEVEL_YEAR.get(level, 0)
    if not year and release:
        m = re.search(r'(\d{4})', release)
        year = int(m.group(1)) if m else 0
    label = ('V5-6R%d' % year) if year else (release or level or 'CATIA V5')
    if level:
        label += ' (%s)' % level
    return {
        'exe': exe,
        'install_root': install_root,
        'win_dir': win_dir,
        'env': env,
        'direnv': direnv,
        'release': release,
        'level': level,
        'year': year,
        'label': label,
    }


def find_installs():
    """列出本机所有 CATIA V5 安装, 新版在前。"""
    found = {}
    cands = []

    # 1) 常见根目录的直接子目录 (含多版本 B26 / B33)
    for root in COMMON_ROOTS:
        if not os.path.isdir(root):
            continue
        cands.append(os.path.join(root, CNEXT_REL))                     # 例如 D:\catia\win_b64\...
        try:
            for name in os.listdir(root):
                sub = os.path.join(root, name)
                if os.path.isdir(sub):
                    cands.append(os.path.join(sub, CNEXT_REL))          # 例如 ...\Dassault Systemes\B33\win_b64\...
        except OSError:
            pass

    # 2) 各盘符下的 <盘>:\catia 与 <盘>:\*\win_b64\code\bin\CNEXT.exe (浅层)
    for drive in _local_drives():
        cands.append(os.path.join(drive, 'catia', CNEXT_REL))
        try:
            for name in os.listdir(drive):
                sub = os.path.join(drive, name)
                if os.path.isdir(sub):
                    p = os.path.join(sub, CNEXT_REL)
                    if os.path.isfile(p):
                        cands.append(p)
        except OSError:
            pass

    for p in cands:
        if os.path.isfile(p):
            key = os.path.normcase(os.path.abspath(p))
            if key not in found:
                found[key] = _install_info(os.path.abspath(p))
    return sorted(found.values(), key=lambda i: (-i['year'], i['install_root'].lower()))


def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def configured():
    """config.json 里登记的 CATIA (若路径仍存在)。"""
    cfg = (load_config().get('catia') or {})
    exe = cfg.get('exe') or ''
    if exe and os.path.isfile(exe):
        info = _install_info(exe)
        for k in ('env', 'direnv', 'install_root', 'label'):
            if cfg.get(k):
                info[k] = cfg[k]
        info['configured'] = True
        return info
    return None


def start_args(info):
    """CATSTART.exe 的启动参数 (与安装器生成的快捷方式一致)。"""
    if not info:
        return ''
    env = info.get('env') or ''
    direnv = info.get('direnv') or info.get('install_root') or ''
    args = '-run "CNEXT.exe"'
    if env:
        args += ' -env %s' % env
    if direnv:
        args += ' -direnv "%s"' % direnv
    return args + ' -nowindow'


def best_install():
    """优先 config.json 登记的那套, 否则取版本最新的一套。"""
    return configured() or (find_installs() or [None])[0]


def find_catia_exe():
    """兼容旧接口: 返回一个可用的 CNEXT.exe 路径。"""
    info = best_install()
    return info['exe'] if info else None


def probe():
    """返回 (是否可用, 说明)。"""
    try:
        import win32com.client  # noqa: F401
        has_com = True
    except Exception:
        has_com = False
    installs = find_installs()
    exe = find_catia_exe()
    if not installs and not exe:
        msg = '未在本机找到 CATIA V5 安装目录。'
        return False, msg
    info = best_install() or {}
    desc = info.get('label', 'CATIA V5')
    msg = '已检测到 %s\n  程序: %s' % (desc, exe)
    if info.get('env'):
        msg += '\n  环境: %s   (-direnv "%s")' % (info['env'], info.get('direnv', ''))
    others = [i for i in installs if i['exe'] != exe]
    if others:
        msg += '\n  其他版本: ' + ' / '.join(i['label'] for i in others)
    msg += '\n  直推通道: 可用 (cscript + COM, 不需要 pywin32)'
    if has_com:
        msg += '\n  另外检测到 pywin32: 「导出 CATIA 自动化脚本(.py)」也可用'
    return True, msg


def connect(visible=True):
    """连接或启动 CATIA, 返回 Application 对象 (需要 pywin32)。"""
    import win32com.client
    app = win32com.client.Dispatch(CATIA_PROGID)
    try:
        app.Visible = bool(visible)
    except Exception:
        pass
    return app


def run_script(path: str):
    """用当前 Python 解释器运行生成的 CATIA 脚本。"""
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=900)
        out = (p.stdout or '') + (p.stderr or '')
        return (p.returncode == 0), (out.strip() or '脚本执行结束 (无输出)')
    except Exception as e:
        return False, '运行失败: %s' % e


# --------------------------------------------------------------------------
#  直推通道: 助手 -> CATIA (cscript + COM, 不需要 pywin32)
# --------------------------------------------------------------------------
def _decode_console(b: bytes) -> str:
    if not b:
        return ''
    for enc, err in (('utf-8', 'strict'), ('gbk', 'replace'), ('latin-1', 'replace')):
        try:
            return b.decode(enc, err)
        except Exception:
            continue
    return b.decode('latin-1', 'replace')


def _log_tail(path, n=12):
    """取推送过程日志的最后几行 (CATIA 那边卡住时用来定位)。"""
    try:
        if not path or not os.path.isfile(path):
            return ''
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = [l.rstrip() for l in f if l.strip()]
        return '\n'.join('  ' + l for l in lines[-n:])
    except Exception:
        return ''


def push_to_catia(doc, save_catpart=None, wait_sec=180, script_dir=None, keep_script=True):
    """把模型直接建到 CATIA 里。

    做法: 生成一个独立 VBScript, 用 Windows 自带的 cscript 运行; 脚本通过
    COM (GetObject) 附着到正在运行的 CATIA, 没有就按 config.json 登记的环境
    启动它, 然后新建 Part 文档并逐条重建特征。

    返回 (是否成功, 说明文本, 脚本路径)。
    """
    from . import catia_export

    info = best_install()
    if not info:
        return False, '未找到 CATIA V5 安装目录, 无法直接推送。', None

    features, warnings = catia_export.build_catia_features(doc)
    if not features:
        msg = ['这个模型没有可导出到 CATIA 的特征。']
        if warnings:
            msg.append('原因:')
            msg.extend('  ' + w for w in warnings[:6])
        msg.append('可改用「文件 → 导出 STL」，在 CATIA 里以 STL/STEP 方式导入。')
        return False, '\n'.join(msg), None

    script_dir = script_dir or tempfile.gettempdir()
    os.makedirs(script_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    vbs = os.path.join(script_dir, 'ai_modeler_push_%s.vbs' % stamp)
    log = os.path.join(script_dir, 'ai_modeler_push_%s.log' % stamp)
    try:
        if os.path.exists(log):
            os.remove(log)
    except OSError:
        pass

    start_exe = os.path.join(info['win_dir'], 'code', 'bin', 'CATSTART.exe')
    start_cmd = '"%s" %s' % (start_exe, start_args(info))

    # CATIA 的 SaveAs 遇到中文路径会卡死或报「方法 SaveAs 失败」(实测),
    # 所以先让 CATIA 存到纯 ASCII 的临时路径, 结束后再由 Python 搬到目标位置。
    final_save = save_catpart or ''
    vbs_save = ''
    if final_save:
        if final_save.isascii():
            vbs_save = final_save
        else:
            vbs_save = os.path.join(script_dir, 'ai_modeler_save_%s.CATPart' % stamp)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(vbs_save)), exist_ok=True)
        except OSError:
            pass

    catia_export.export_push_vbs(doc, vbs, out_catpart=vbs_save,
                                 start_cmd=start_cmd, wait_sec=wait_sec, log_path=log)

    try:
        p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True,
                           timeout=wait_sec + 240)
    except FileNotFoundError:
        return False, '找不到 cscript.exe（Windows 脚本宿主缺失）。', vbs
    except subprocess.TimeoutExpired:
        tail = _log_tail(log)
        msg = '等待 CATIA 响应超时（%d 秒）。' % (wait_sec + 240)
        if tail:
            msg += '\nCATIA 侧的进度日志（最后几步）: \n' + tail
        return False, msg, vbs
    except Exception as e:
        return False, '运行推送脚本失败: %s' % e, vbs

    out = _decode_console(p.stdout)
    err_text = _decode_console(p.stderr).strip()
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    ok_line = next((l for l in lines if l.startswith('OK ')), '')
    part_line = next((l for l in lines if l.startswith('PART ')), '')
    doc_line = next((l for l in lines if l.startswith('DOC ')), '')
    saved_line = next((l for l in lines if l.startswith('SAVED ')), '')
    not_saved = 'NOT_SAVED' in out
    fails = [l for l in lines if l.startswith('- ')]
    renamed = bool(final_save and vbs_save and
                   os.path.normcase(os.path.abspath(final_save)) !=
                   os.path.normcase(os.path.abspath(vbs_save)))

    ok = (p.returncode == 0 and bool(ok_line) and not err_text)

    # CATIA 存的是 ASCII 临时文件, 这里搬到你指定的（可能含中文的）路径
    move_err = ''
    if ok and final_save and vbs_save and \
            os.path.normcase(os.path.abspath(final_save)) != os.path.normcase(os.path.abspath(vbs_save)):
        try:
            if os.path.isfile(vbs_save):
                os.makedirs(os.path.dirname(os.path.abspath(final_save)), exist_ok=True)
                if os.path.exists(final_save):
                    os.remove(final_save)
                shutil.move(vbs_save, final_save)
                saved_line = 'SAVED ' + final_save
        except Exception as e:
            move_err = '已建好模型，但把文件搬到 %s 失败: %s' % (final_save, e)
    msgs = []
    if ok:
        msgs.append('已在 CATIA %s 中建模完成（%s 个操作）'
                    % (info.get('label', ''), ok_line[3:].strip()))
    elif 'CATIA_NOT_AVAILABLE' in out:
        msgs.append('无法连接或启动 CATIA，请先手动打开 CATIA 再试。')
    elif err_text:
        msgs.append('脚本执行出错: %s' % err_text.splitlines()[0])
        msgs.append('（脚本已保留，可用 cscript 手动重跑排查）')
    else:
        msgs.append('CATIA 建模未全部成功（返回码 %s）。' % p.returncode)
    if part_line:
        msgs.append('零件: %s' % part_line[5:].strip())
    if ok:
        msgs.append('模型已显示出来（CATIA 已前置 + 视图已适应）')
    if saved_line:
        msgs.append('已保存: %s' % saved_line[6:].strip())
        if renamed:
            msgs.append('（CATIA 的 SaveAs 不支持中文路径，所以先存成 %s 再改名；'
                        'CATIA 里该文档标题仍显示临时名，要继续保存请用「文件 → 另存为」）'
                        % os.path.basename(vbs_save))
    elif not_saved and ok:
        msgs.append('未自动保存：请在 CATIA 里按 Ctrl+S 或「文件 → 保存」')
    if move_err:
        msgs.append(move_err)
    if fails:
        msgs.append('以下操作需要在 CATIA 里手动处理:')
        msgs.extend('  ' + f for f in fails[:8])
    if not ok:
        tail = _log_tail(log)
        if tail:
            msgs.append('CATIA 侧进度日志（最后几步）:')
            msgs.append(tail)
    if warnings:
        msgs.append('提示:')
        msgs.extend('  ' + w for w in warnings[:5])
    if not keep_script:
        try:
            os.remove(vbs)
            vbs = None
        except OSError:
            pass
    return ok, '\n'.join(msgs) or out.strip(), vbs



if __name__ == '__main__':
    # 直接运行本文件可查看探测结果:  python ai_modeler\catia.py
    ins = find_installs()
    print('找到 %d 套 CATIA V5 安装:' % len(ins))
    for i in ins:
        print('  - %-18s %s' % (i['label'], i['exe']))
        if i['env']:
            print('      环境 %s   目录 %s' % (i['env'], i['direnv']))
        print('      启动: "%s" %s' % (os.path.join(i['win_dir'], 'code', 'bin', 'CATSTART.exe'),
                                      start_args(i)))
    ok, msg = probe()
    print('\nprobe -> %s\n%s' % (ok, msg))
