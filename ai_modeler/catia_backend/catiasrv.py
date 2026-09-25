# -*- coding: utf-8 -*-
r"""CATIA 插件后端服务 —— 为 CATScript 宏提供中文界面、解析与建模。

为什么需要它:
    CATIA 的 CATScript 加载器**不接受 UTF-16 / 带 BOM 的 UTF-8** 文件
    (BOM 会被读成 "?" 并报 ScriptingERR_1002 / 无效字符), 所以
    `AI自动建模助手.CATScript` 必须是纯 ASCII 文件, 不能内嵌中文提示,
    也不适合在 CATIA 脚本宿主里做混合造型 —— 于是宏只当一个"瘦启动器",
    中文界面与建模都由本模块负责。

用法:
    python -m ai_modeler --catia-macro <结果文件>   宏的完整后端(常驻对话框, 可连续建模)
    python -m ai_modeler --catia-ui    <特征表文件> 只弹一次输入框并写特征表(兼容旧用法)

常驻对话框: 每建完一个零件, 窗口不关闭, 结果留在窗口里, 可以接着输入下一个;
模型不会自动保存, 请在 CATIA 里手动保存。窗口关闭后, 宏会把本次会话汇总弹出来。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import os
import sys
import time

# 输入框里可一键填入的示例
EXAMPLES = [
    ('带孔底板', '底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12'),
    ('圆角长方体+通孔', '画一个 120×80×15 的长方体, 四角圆角 R8, 在中心打一个直径 20 的通孔'),
    ('法兰盘', '法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120'),
    ('阶梯轴', '阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40'),
    ('直齿圆柱齿轮', '齿轮 模数 3 齿数 24 厚 20, 中心孔直径 20'),
    ('整体叶盘', '叶盘 外径 220 厚 30 叶片数 24 叶展 45'),
    ('三角转子', '三角转子 外接圆半径60 厚度40 中心孔24'),
    ('套筒', '套筒 外径 80 内径 50 长 100'),
    ('圆柱', '直径 60 高 120 的圆柱'),
]

FONT_UI = 'Microsoft YaHei UI'


def _cfg_path():
    root = str(PROJECT_ROOT)
    return os.path.join(root, 'config.json')


def _load_last_prompt():
    """记住上次输入, 省得每次重敲。"""
    try:
        import json
        with open(_cfg_path(), 'r', encoding='utf-8') as f:
            return (json.load(f).get('catia') or {}).get('last_prompt', '') or ''
    except Exception:
        return ''


def _save_last_prompt(text):
    try:
        import json
        p = _cfg_path()
        with open(p, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        cfg.setdefault('catia', {})['last_prompt'] = text
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------
#  建模: 解析 -> 内核建模 -> 推到 CATIA (不保存, 由用户手动保存)
# --------------------------------------------------------------------------
def build_once(text, save_catpart=None):
    """建模一次, 返回要显示给用户的行列表。"""
    from ai_modeler.catia_backend import catia
    from ai_modeler.core import nlparser

    res = nlparser.parse(text)
    if not res.ok:
        return ['解析失败: %s' % res.message]
    doc = res.doc
    doc.build()
    lines = []
    try:
        st = doc.stats()
        if st.get('size'):
            lines.append('尺寸: %.1f × %.1f × %.1f mm   特征 %d 项'
                         % (st['size'][0], st['size'][1], st['size'][2], len(doc.features)))
    except Exception:
        pass
    ok, msg, script = catia.push_to_catia(doc, save_catpart=save_catpart)
    lines.extend([l for l in msg.splitlines() if l.strip()])
    return lines


def _stamp():
    return time.strftime('%H:%M:%S')


# --------------------------------------------------------------------------
#  常驻会话窗口 (宏的默认界面)
# --------------------------------------------------------------------------
def open_session():
    """常驻对话框: 连续建模, 结果留在窗口里, 关掉窗口才结束。

    返回本次会话的所有输出行 (宏会在结束时弹出来)。
    """
    import threading
    import tkinter as tk
    from tkinter import ttk

    state = {'lines': [], 'busy': False, 'count': 0}
    win = tk.Tk()
    win.title('AI 自动建模助手 —— CATIA 连续建模')
    win.geometry('760x600')
    win.minsize(620, 460)
    win.attributes('-topmost', True)

    head = tk.Frame(win, padx=14, pady=8)
    head.pack(fill='x')
    tk.Label(head, text='输入一句中文 → 点「⚡ 生成并重建到 CATIA」',
             font=(FONT_UI, 12, 'bold'), anchor='w').pack(fill='x')
    tk.Label(head, text='窗口不会自动关闭：建完可以接着输入下一个零件。'
                        '模型不会自动保存，请在 CATIA 里按 Ctrl+S 保存。',
             fg='#555', anchor='w', justify='left', wraplength=700).pack(fill='x', pady=(3, 0))

    body = tk.Frame(win, padx=14)
    body.pack(fill='x')
    txt = tk.Text(body, height=3, wrap='word', font=(FONT_UI, 10))
    txt.pack(fill='x')
    txt.insert('1.0', _load_last_prompt() or EXAMPLES[2][1])

    pick = tk.Frame(win, padx=14, pady=6)
    pick.pack(fill='x')
    tk.Label(pick, text='常用示例：', fg='#555').pack(side='left')
    var = tk.StringVar()
    cb = ttk.Combobox(pick, textvariable=var, width=18, state='readonly',
                      values=[n for n, _ in EXAMPLES])
    cb.pack(side='left')

    def use_example(_e=None):
        for n, d in EXAMPLES:
            if n == var.get():
                txt.delete('1.0', 'end')
                txt.insert('1.0', d)
                break
    cb.bind('<<ComboboxSelected>>', use_example)

    btns = tk.Frame(win, padx=14, pady=2)
    btns.pack(fill='x')
    btn_build = ttk.Button(btns, text='⚡  生成并重建到 CATIA')
    btn_build.pack(side='left')
    ttk.Button(btns, text='关闭', command=lambda: win.destroy()).pack(side='right')
    tk.Label(btns, text='Ctrl+Enter', fg='#888').pack(side='left', padx=8)
    busy_lbl = tk.Label(btns, text='', fg='#0a7', font=(FONT_UI, 10, 'bold'))
    busy_lbl.pack(side='left', padx=10)

    res_head = tk.Frame(win, padx=14, pady=4)
    res_head.pack(fill='x', pady=(8, 0))
    tk.Label(res_head, text='结果（每次建模都会追加在这里）', fg='#555', anchor='w').pack(side='left')
    count_lbl = tk.Label(res_head, text='本次会话已建 0 个零件', fg='#888', anchor='e')
    count_lbl.pack(side='right')

    res_box = tk.Frame(win, padx=14, pady=6)
    res_box.pack(fill='both', expand=True, pady=(0, 12))
    out = tk.Text(res_box, wrap='word', height=14, font=(FONT_UI, 10),
                  background='#fafafa', relief='solid', borderwidth=1)
    sb = ttk.Scrollbar(res_box, orient='vertical', command=out.yview)
    out.configure(yscrollcommand=sb.set)
    out.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    out.configure(state='disabled')

    def log(line):
        text = '[%s] %s\n' % (_stamp(), line)
        state['lines'].append(text.rstrip('\n'))
        out.configure(state='normal')
        out.insert('end', text)
        out.see('end')
        out.configure(state='disabled')

    def do_build(_e=None):
        if state['busy']:
            return
        desc = txt.get('1.0', 'end').strip()
        if not desc:
            log('请先输入零件描述')
            return
        _save_last_prompt(desc)
        state['busy'] = True
        btn_build.configure(state='disabled')
        busy_lbl.configure(text='建模中…')
        log('▶ %s' % desc)

        def work():
            try:
                lines = build_once(desc)
            except Exception as e:
                lines = ['出错: %s' % e]

            def done():
                for l in lines:
                    log('    ' + l)
                state['busy'] = False
                state['count'] += 1
                count_lbl.configure(text='本次会话已建 %d 个零件' % state['count'])
                btn_build.configure(state='normal')
                busy_lbl.configure(text='')
                txt.delete('1.0', 'end')      # 清空, 方便直接输入下一个
                txt.focus_set()
                log('    （窗口不关闭，可以继续输入下一个零件）')
            win.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    btn_build.configure(command=do_build)
    win.bind('<Control-Return>', do_build)
    txt.focus_set()

    # 常驻窗口有个坑: 程序代码更新后, 这个进程还在跑旧代码, 用户会以为"改了没用"。
    # 所以定期比对源码时间戳, 一旦发现更新就明确提示"关掉窗口重开"。
    def code_stamp():
        source_root = os.path.join(str(PROJECT_ROOT), 'ai_modeler')
        newest = 0.0
        try:
            for current, dirs, files in os.walk(source_root):
                dirs[:] = [name for name in dirs if name != '__pycache__']
                for fn in files:
                    if fn.endswith('.py'):
                        newest = max(newest, os.path.getmtime(os.path.join(current, fn)))
        except OSError:
            pass
        return newest

    boot_stamp = code_stamp()

    def watch_code():
        if code_stamp() > boot_stamp + 1:
            busy_lbl.configure(text='⚠ 程序已更新：请关掉本窗口后重新运行宏', fg='#c00')
            log('⚠ 检测到助手程序代码已更新：本窗口仍在用旧版本，'
                '请「关闭」本窗口，然后在 CATIA 里重新运行一次宏。')
            return
        win.after(20000, watch_code)

    win.after(20000, watch_code)

    win.update_idletasks()
    try:
        win.lift()
        win.focus_force()
    except Exception:
        pass
    win.mainloop()
    return state['lines']


# --------------------------------------------------------------------------
#  单次输入框 (兼容 --catia-ui)
# --------------------------------------------------------------------------
def ask_description(parent=None, initial=''):
    """弹出中文输入对话框, 返回描述文本; 取消返回 None。"""
    import tkinter as tk
    from tkinter import ttk

    own_root = parent is None
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title('AI 自动建模助手 —— CATIA 插件')
    win.geometry('660x430')
    win.attributes('-topmost', True)

    result = {'text': None}

    head = tk.Frame(win, padx=14, pady=6)
    head.pack(fill='x', pady=(12, 0))
    tk.Label(head, text='用一句中文描述你要建的零件', font=(FONT_UI, 12, 'bold'),
             anchor='w').pack(fill='x')
    tk.Label(head, text='点「生成并重建」后，CATIA 会自动把模型建出来并显示。',
             fg='#555', anchor='w').pack(fill='x', pady=(3, 0))

    body = tk.Frame(win, padx=14)
    body.pack(fill='both', expand=True)
    txt = tk.Text(body, height=8, wrap='word', font=(FONT_UI, 10))
    txt.pack(fill='both', expand=True)
    txt.insert('1.0', initial or EXAMPLES[1][1])
    txt.focus_set()

    pick = tk.Frame(win, padx=14, pady=6)
    pick.pack(fill='x')
    tk.Label(pick, text='常用示例：', fg='#555').pack(side='left')
    var = tk.StringVar()
    cb = ttk.Combobox(pick, textvariable=var, width=16, state='readonly',
                      values=[n for n, _ in EXAMPLES])
    cb.pack(side='left')

    def use_example(_e=None):
        for n, d in EXAMPLES:
            if n == var.get():
                txt.delete('1.0', 'end')
                txt.insert('1.0', d)
                break
    cb.bind('<<ComboboxSelected>>', use_example)

    tip = tk.Label(win, text='提示：尺寸单位一律 mm；圆角写 R8，倒角写 C2；阵列写「沿 X 方向阵列 4 个, 间距 40」。',
                   fg='#777', anchor='w', padx=14, justify='left')
    tip.pack(fill='x')

    btns = tk.Frame(win, padx=14, pady=10)
    btns.pack(fill='x')

    def ok(_e=None):
        t = txt.get('1.0', 'end').strip()
        result['text'] = t or None
        win.destroy()

    def cancel(_e=None):
        result['text'] = None
        win.destroy()

    ttk.Button(btns, text='生成并重建到 CATIA', command=ok).pack(side='right')
    ttk.Button(btns, text='取消', command=cancel).pack(side='right', padx=8)
    win.bind('<Control-Return>', ok)
    win.bind('<Escape>', cancel)
    win.protocol('WM_DELETE_WINDOW', cancel)

    win.update_idletasks()
    try:
        win.lift()
        win.focus_force()
    except Exception:
        pass

    if own_root:
        win.mainloop()
    else:
        win.wait_window()
    return result['text']


# --------------------------------------------------------------------------
#  CLI 入口
# --------------------------------------------------------------------------
def _write_result(outfile, lines, code):
    """把结果同时打印并写成 UTF-16 文件 (CATIA 宏会把它读出来显示)。"""
    text = '\n'.join(lines) + '\n'
    sys.stdout.write(text)
    if outfile:
        try:
            with open(outfile, 'w', encoding='utf-16', newline='\r\n') as f:
                f.write(text)
        except OSError:
            pass
    return code


def run_catia_macro(outfile):
    """`python -m ai_modeler --catia-macro <结果文件>` —— CATIA 宏的完整后端。

    常驻对话框, 建完不关闭, 可以连续建模; 不自动保存; 建完把模型显示出来。
    自动化入口(测试用): 环境变量 AI_MODELER_CATIA_BATCH (用 ;; 分隔多条描述)
    或 AI_MODELER_CATIA_TEXT (单条)。
    """
    batch = (os.environ.get('AI_MODELER_CATIA_BATCH') or '').strip()
    if batch:
        lines = []
        for text in [t.strip() for t in batch.split(';;') if t.strip()]:
            lines.append('[%s] ▶ %s' % (_stamp(), text))
            lines.extend('    ' + l for l in build_once(text))
        return _write_result(outfile, lines, 0)

    auto = (os.environ.get('AI_MODELER_CATIA_TEXT') or '').strip()
    if auto:
        sys.stdout.write('[auto] 使用 AI_MODELER_CATIA_TEXT, 跳过对话框\n')
        return _write_result(outfile, build_once(auto), 0)

    lines = open_session()
    if not lines:
        lines = ['（本次会话没有建模）']
    lines.append('（模型未自动保存：请在 CATIA 里手动保存）')
    return _write_result(outfile, lines, 0)


def write_feature_table(doc, path, prompt='', warnings=None, aborted=False):
    """把特征树写成 VBScript 特征表 (UTF-16LE + BOM, FSO 以 Unicode 读取)。"""
    from ai_modeler.catia_backend import catia_export
    if aborted:
        text = "' CANCELLED by the user\nFEAT_COUNT = 0\n"
    else:
        features, warns = catia_export.build_catia_features(doc)
        warnings = list(warnings or []) + list(warns)
        text = catia_export.format_vbs_table(features)
    lines = [text]
    lines.append("' generated: " + time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
    lines.append("' prompt: " + (prompt or '') + '\n')
    for w in (warnings or []):
        lines.append("' note: " + w + '\n')
    with open(path, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(''.join(lines))
    return path


def run_catia_ui(featfile):
    """`python -m ai_modeler --catia-ui <路径>`: 只弹一次输入框并写特征表。

    设置环境变量 AI_MODELER_CATIA_TEXT 可直接用该文本、跳过对话框。
    """
    from ai_modeler.core import nlparser

    auto = (os.environ.get('AI_MODELER_CATIA_TEXT') or '').strip()
    if auto:
        sys.stdout.write('[auto] 使用 AI_MODELER_CATIA_TEXT, 跳过对话框\n')
        desc = auto
    else:
        desc = ask_description(initial=_load_last_prompt())
    if not desc:
        write_feature_table(None, featfile, prompt='', aborted=True)
        sys.stderr.write('已取消。\n')
        return 1

    _save_last_prompt(desc)
    res = nlparser.parse(desc)
    if not res.ok:
        write_feature_table(None, featfile, prompt=desc, aborted=True)
        sys.stderr.write('解析失败: %s\n' % res.message)
        return 2

    doc = res.doc
    doc.build()
    write_feature_table(doc, featfile, prompt=desc)
    sys.stdout.write('已写出特征表: %s\n' % featfile)
    sys.stdout.write(res.summary() + '\n')
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    out = args[0] if args else os.path.join(os.environ.get('TEMP', '.'), 'aim_macro_out.txt')
    sys.exit(run_catia_macro(out))
