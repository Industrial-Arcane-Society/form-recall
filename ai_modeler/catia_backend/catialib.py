# -*- coding: utf-8 -*-
"""CATIA 模型库连接层 —— 让「AI 自动建模助手」能检索 CATIA 自带的 catalog 标准件库。

CATIA V5 的模型库以 *.catalog 文件组织, 默认放在安装目录的
    <安装目录>\\win_b64\\startup\\components\\<分类>\\*.catalog
例如 D:\\catia\\win_b64\\startup\\components\\conveyor\\Roller.catalog

本模块负责:
  1. 找到本机 CATIA 的 catalog 目录 (config.json -> catia / model_library)
  2. 扫描成索引, 存到 kb/catia_catalogs.json
  3. 按关键词检索
  4. 提供一个 tkinter 浏览窗口 (助手界面「工具 -> CATIA 模型库」)

命令行:
    python -m ai_modeler --catia-lib                      列出索引概况
    python -m ai_modeler --catia-lib --search 机器人       检索
    python -m ai_modeler --catia-lib --rebuild            重新扫描建索引
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import os
import subprocess
import sys

ROOT_DIR = str(PROJECT_ROOT)
CONFIG_PATH = os.path.join(ROOT_DIR, 'config.json')
KB_DIR = os.path.join(ROOT_DIR, 'kb')
INDEX_PATH = os.path.join(KB_DIR, 'catia_catalogs.json')

CATALOG_EXTS = ('.catalog',)
PART_EXTS = ('.catpart', '.catproduct', '.model', '.cgr', '.stp', '.step', '.igs', '.iges')

# CATIA 自带库都是英文目录名, 这里给常用的几类配中文别名, 方便用中文检索
CAT_ALIAS = {
    '标准件': ['MechanicalStandardParts', 'standard'],
    '机械标准件': ['MechanicalStandardParts'],
    '机器人': ['robot'],
    '输送线': ['conveyor'], '输送': ['conveyor'], '滚筒': ['Roller'],
    '模具': ['MoldCatalog', 'mold'],
    '焊接': ['weld'], '弧焊': ['arcWeld'], '点焊': ['spotWeld'],
    '管路': ['piping', 'tubing'], '管道': ['piping'], '管件': ['tubing'],
    '空调': ['hvac'], '暖通': ['hvac'], '风管': ['hvac'],
    '电气': ['raceway'], '桥架': ['raceway'], '线槽': ['raceway'],
    '结构': ['structure'], '钢结构': ['structure'],
    '设备': ['equipment'], '工厂': ['facility'], '厂区': ['facility'],
    '涂装': ['paint'], '喷涂': ['paint'],
    '钣金': ['SheetMetal'],
    '工装': ['tooling'], '夹具': ['tooling'],
    '贯通件': ['Penetration'],
    '空间预留': ['spaceReservation'],
    '厂房': ['facility'],
    '渲染': ['Rendering'],
    '知识规则': ['KnowledgeRules'],
}


# --------------------------------------------------------------------------
#  配置 / 目录定位
# --------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def default_catalog_dirs():
    """config.json 里登记的目录 + 由 CATIA 安装目录推导出的 components 目录。"""
    dirs = []
    cfg = load_config().get('model_library') or {}
    for d in (cfg.get('catalog_dirs') or []):
        if d:
            dirs.append(d)
    try:
        from ai_modeler.catia_backend import catia
        info = catia.best_install()
    except Exception:
        info = None
    if info:
        dirs.append(os.path.join(info['win_dir'], 'startup', 'components'))
        dirs.append(os.path.join(info['win_dir'], 'startup', 'catalogs'))
    out, seen = [], set()
    for d in dirs:
        d = os.path.abspath(d)
        k = os.path.normcase(d)
        if k in seen or not os.path.isdir(d):
            continue
        seen.add(k)
        out.append(d)
    return out


def _exts():
    cfg = load_config().get('model_library') or {}
    exts = list(CATALOG_EXTS)
    if cfg.get('include_part_files'):
        exts += list(PART_EXTS)
    return tuple(e.lower() for e in exts)


# --------------------------------------------------------------------------
#  扫描 / 索引
# --------------------------------------------------------------------------
def scan(dirs=None, max_files=20000):
    """扫描 catalog 库, 返回条目列表。"""
    dirs = dirs or default_catalog_dirs()
    exts = _exts()
    items = []
    for root in dirs:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(exts):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, root)
                parts = rel.split(os.sep)
                items.append({
                    'name': os.path.splitext(fn)[0],
                    'file': fn,
                    'category': parts[0] if len(parts) > 1 else '(根目录)',
                    'sub': ('/'.join(parts[1:-1]) if len(parts) > 2 else ''),
                    'path': full,
                    'root': root,
                    'size': st.st_size,
                    'mtime': int(st.st_mtime),
                })
                if len(items) >= max_files:
                    return items
    items.sort(key=lambda i: (i['category'].lower(), i['name'].lower()))
    return items


def build_index(dirs=None, path=INDEX_PATH):
    """扫描并写出索引文件, 返回索引 dict。"""
    items = scan(dirs)
    cats = {}
    for it in items:
        cats[it['category']] = cats.get(it['category'], 0) + 1
    index = {
        'generated': _now(),
        'dirs': default_catalog_dirs() if dirs is None else list(dirs),
        'count': len(items),
        'categories': dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))),
        'items': items,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return index


def load_index(path=INDEX_PATH):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_index(rebuild=False, path=INDEX_PATH):
    """索引不存在或为空时自动构建。"""
    idx = load_index(path)
    if rebuild or not idx.get('items'):
        idx = build_index(path=path)
    return idx


def _now():
    import time
    return time.strftime('%Y-%m-%d %H:%M:%S')


# --------------------------------------------------------------------------
#  检索
# --------------------------------------------------------------------------
def _variants(kw):
    """把关键词扩展成同义词集合 (中文别名 -> 英文目录名)。"""
    k = str(kw).strip().lower()
    out = {k}
    if k in CAT_ALIAS:
        out.update(a.lower() for a in CAT_ALIAS[k])
    else:
        for zh, ens in CAT_ALIAS.items():
            if zh.lower() in k:
                out.add(zh.lower())
                out.update(a.lower() for a in ens)
    return out


def search(keyword, items=None, limit=200):
    """按关键词检索 (空格分隔多个词, 每个词命中任一别名即可; 词之间是"与")。"""
    if items is None:
        items = ensure_index().get('items', [])
    raw = [k.strip() for k in str(keyword or '').split() if k.strip()]
    if not raw:
        return list(items[:limit])
    groups = [_variants(k) for k in raw]
    out = []
    for it in items:
        hay = ('%s %s %s %s' % (it['name'], it['category'], it.get('sub', ''), it['file'])).lower()
        if all(any(v in hay for v in grp) for grp in groups):
            out.append(it)
            if len(out) >= limit:
                break
    return out


def summary(index=None):
    """人话版概况。"""
    idx = index if index is not None else ensure_index()
    lines = ['CATIA 模型库 (catalog) 索引']
    lines.append('  索引文件: %s' % INDEX_PATH)
    lines.append('  更新时间: %s' % idx.get('generated', '未建立'))
    lines.append('  条目总数: %d 个' % idx.get('count', 0))
    for d in (idx.get('dirs') or []):
        lines.append('  扫描目录: %s' % d)
    cats = idx.get('categories') or {}
    if cats:
        lines.append('  分类 (%d 个):' % len(cats))
        for name, n in list(cats.items())[:40]:
            lines.append('    %-28s %4d 个' % (name, n))
        if len(cats) > 40:
            lines.append('    ... 其余 %d 个分类见索引文件' % (len(cats) - 40))
    return '\n'.join(lines)


# --------------------------------------------------------------------------
#  图形界面 (助手 -> 工具 -> CATIA 模型库)
# --------------------------------------------------------------------------
def open_window(parent=None, status=None):
    """打开模型库浏览窗口。parent 为 None 时自建根窗口。"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    own_root = parent is None
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title('CATIA 模型库 (catalog) —— AI 自动建模助手')
    win.geometry('980x560')

    idx = ensure_index()
    items = list(idx.get('items', []))

    top = ttk.Frame(win, padding=(10, 8))
    top.pack(side='top', fill='x')
    ttk.Label(top, text='关键词').pack(side='left')
    var_kw = tk.StringVar()
    ent = ttk.Entry(top, textvariable=var_kw, width=32)
    ent.pack(side='left', padx=6)
    ttk.Label(top, text='(空格分隔多个词；支持中文: 机器人 / 输送线 / 模具 / 标准件 等)').pack(side='left')

    info = tk.StringVar()
    ttk.Label(top, textvariable=info).pack(side='left', padx=12)

    body = ttk.Frame(win, padding=(10, 0))
    body.pack(side='top', fill='both', expand=True)
    cols = ('名称', '分类', '子类', '大小(KB)', '修改时间', '路径')
    tv = ttk.Treeview(body, columns=cols, show='headings', height=18)
    widths = (200, 130, 150, 80, 130, 420)
    for c, w in zip(cols, widths):
        tv.heading(c, text=c)
        tv.column(c, width=w, anchor='w' if c in ('名称', '路径') else 'center')
    vs = ttk.Scrollbar(body, orient='vertical', command=tv.yview)
    tv.configure(yscrollcommand=vs.set)
    tv.pack(side='left', fill='both', expand=True)
    vs.pack(side='right', fill='y')

    def fmt_time(ts):
        import time
        try:
            return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
        except Exception:
            return ''

    def refresh(*_):
        kws = var_kw.get()
        rows = search(kws, items=items)
        tv.delete(*tv.get_children())
        for it in rows:
            tv.insert('', 'end', values=(it['name'], it['category'], it.get('sub', ''),
                                         '%.1f' % (it['size'] / 1024.0), fmt_time(it['mtime']),
                                         it['path']))
        info.set('共 %d 条 / 显示 %d 条' % (len(items), len(rows)))
        return rows

    def selected_path():
        sel = tv.selection()
        if not sel:
            return None
        return tv.item(sel[0], 'values')[5]

    def reveal():
        p = selected_path()
        if not p:
            messagebox.showinfo('提示', '请先选中一行', parent=win)
            return
        subprocess.Popen(['explorer', '/select,' + os.path.normpath(p)])

    def copy_path():
        p = selected_path()
        if not p:
            return
        win.clipboard_clear()
        win.clipboard_append(p)
        if status:
            status('已复制路径')
        else:
            messagebox.showinfo('已复制', p, parent=win)

    def do_rebuild():
        nonlocal items, idx
        idx = build_index()
        items = list(idx.get('items', []))
        refresh()
        messagebox.showinfo('完成', '已重新扫描:\n%s' % summary(idx), parent=win)

    def how_to():
        messagebox.showinfo(
            '在 CATIA 里怎么用',
            '在 CATIA V5 中打开目录浏览器:\n\n'
            '  1. 打开或新建一个 CATPart / CATProduct\n'
            '  2. 菜单「工具 Tools → 目录浏览器 Catalog Browser」'
            '(或「插入 → 从目录 Insert → Catalog…」)\n'
            '  3. 用「浏览其他目录 Other Catalog…」选中下面这些 .catalog 文件\n'
            '  4. 双击条目即可把标准件插入当前模型\n\n'
            '本窗口只负责「找得到」; 真正的插入动作在 CATIA 内完成。',
            parent=win)

    bot = ttk.Frame(win, padding=(10, 8))
    bot.pack(side='bottom', fill='x')
    ttk.Button(bot, text='重新扫描', command=do_rebuild).pack(side='left')
    ttk.Button(bot, text='打开所在文件夹', command=reveal).pack(side='left', padx=6)
    ttk.Button(bot, text='复制路径', command=copy_path).pack(side='left')
    ttk.Button(bot, text='在 CATIA 里怎么用?', command=how_to).pack(side='left', padx=6)
    ttk.Button(bot, text='关闭', command=win.destroy).pack(side='right')

    ent.bind('<Return>', refresh)
    var_kw.trace_add('write', lambda *a: refresh())
    tv.bind('<Double-1>', lambda e: reveal())
    refresh()

    if own_root:
        win.mainloop()
    return win


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--rebuild' in args:
        print(summary(build_index()))
    elif '--search' in args:
        kw = args[args.index('--search') + 1] if args.index('--search') + 1 < len(args) else ''
        for it in search(kw):
            print('%s  [%s]  %s' % (it['name'], it['category'], it['path']))
    else:
        print(summary())
