# -*- coding: utf-8 -*-
"""命令行入口:
    python -m ai_modeler                 启动图形界面
    python -m ai_modeler --mcp           启动 MCP 服务 (stdio)
    python -m ai_modeler --mcp-config    打印 MCP 客户端配置
    python -m ai_modeler --cli "描述"    命令行建模
    python -m ai_modeler --selftest      运行自检
    python -m ai_modeler --catia-info    显示检测到的 CATIA V5 安装
    python -m ai_modeler --catia-ui <路径>  CATIA 宏调用的中文输入对话框
    python -m ai_modeler --catia-macro <路径>  CATIA 宏的完整后端（对话框+建模+结果）
    python -m ai_modeler --push "描述"     直接建到正在运行的 CATIA (cscript+COM)
    python -m ai_modeler --catia-lib     查看 CATIA 模型库(catalog) 索引
                                         可选 --search 关键词 / --rebuild
"""
from __future__ import annotations

import os
import sys


def _cli(argv):
    from . import exporters, nlparser
    text = None
    export = None
    out = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--export', '-e') and i + 1 < len(argv):
            export = argv[i + 1]
            i += 2
            continue
        if a in ('--out', '-o') and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
            continue
        if text is None:
            text = a
        i += 1
    if not text:
        print('用法: python -m ai_modeler --cli "画一个 100x60x20 的长方体" [--export out.stl]')
        return 1
    res = nlparser.parse(text)
    if not res.ok:
        print('解析失败: ' + res.message)
        return 2
    doc = res.doc
    body = doc.build()
    print(res.summary())
    for i, f in enumerate(doc.features, 1):
        print('  %d. [%s] %s' % (i, '切除' if f.op == 'cut' else '合并', f.describe()))
    st = doc.stats()
    if st['size']:
        print('外形尺寸: %.2f × %.2f × %.2f mm' % st['size'])
    print('体积: %.1f mm³   表面积: %.1f mm²   面片: %d' % (st['volume'], st['area'], st['triangles']))
    if export:
        path = out or export
        low = export.lower()
        if low.endswith('.stl'):
            exporters.export_stl(body, path, doc.name)
        elif low.endswith('.obj'):
            exporters.export_obj(body, path, doc.name)
        elif low.endswith('.py'):
            exporters.export_catia_script(doc, path)
        elif low.endswith('.txt'):
            exporters.export_report(doc, path, text)
        else:
            exporters.export_project(doc, path)
        print('已导出: ' + path)
    return 0


def _read_text_arg(argv):
    """从 --text 或 --text-file 取建模描述 (text-file 优先, 便于中文与换行)。"""
    text = None
    for i, a in enumerate(argv):
        if a == '--text' and i + 1 < len(argv):
            text = argv[i + 1]
        elif a == '--text-file' and i + 1 < len(argv):
            raw = None
            for enc in ('utf-16', 'utf-8-sig', 'utf-8', 'gbk'):
                try:
                    with open(argv[i + 1], 'r', encoding=enc) as f:
                        raw = f.read()
                    break
                except (UnicodeError, UnicodeDecodeError):
                    continue
                except OSError:
                    return None
            text = raw
    return text


def _catia_dump(argv):
    """给 CATIA 宏调用的模式: 解析描述 -> 输出特征表 (VBS 或 JSON)。"""
    from . import catia_export, nlparser
    text = _read_text_arg(argv)
    if not text or not text.strip():
        sys.stderr.write('缺少建模描述 (--text 或 --text-file)\n')
        return 2
    vbs_path = json_path = None
    for i, a in enumerate(argv):
        if a == '--catia-vbs' and i + 1 < len(argv):
            vbs_path = argv[i + 1]
        elif a == '--catia-json' and i + 1 < len(argv):
            json_path = argv[i + 1]
    res = nlparser.parse(text)
    if not res.ok:
        sys.stderr.write(res.message + '\n')
        return 3
    doc = res.doc
    doc.build()
    if vbs_path:
        catia_export.export_catia_vbs(doc, vbs_path)
    if json_path:
        catia_export.export_catia_json(doc, json_path)
    feats, warns = catia_export.build_catia_features(doc)
    sys.stdout.write('识别到 %d 个特征, 展开为 %d 个 CATIA 操作\n' % (len(doc.features), len(feats)))
    for w in warns:
        sys.stdout.write('注意: %s\n' % w)
    return 0


def main():
    argv = sys.argv[1:]
    if '--catia-vbs' in argv or '--catia-json' in argv:
        sys.exit(_catia_dump(argv))
    if '--catia-ui' in argv:
        from . import catiasrv
        i = argv.index('--catia-ui')
        out = argv[i + 1] if i + 1 < len(argv) else os.path.join(
            os.environ.get('TEMP', '.'), 'aim_feat.vbs')
        sys.exit(catiasrv.run_catia_ui(out))
    if '--catia-macro' in argv:
        from . import catiasrv
        i = argv.index('--catia-macro')
        out = argv[i + 1] if i + 1 < len(argv) else os.path.join(
            os.environ.get('TEMP', '.'), 'aim_macro_out.txt')
        sys.exit(catiasrv.run_catia_macro(out))
    if '--mcp' in argv:
        from . import mcp_server
        mcp_server.serve_stdio()
        return
    if '--mcp-catia' in argv or '--catia-mcp' in argv:
        # 兼容 catia-v5-mcp-server 的 CATIA MCP 服务器(78 个 catia_* 工具)
        from .catia_mcp import server as mcp_catia
        if '--selftest' in argv:
            sys.exit(mcp_catia.selftest())
        mcp_catia.serve_stdio()
        return
    if '--mcp-config' in argv:
        from . import mcp_server
        if '--catia' in argv:
            from .catia_mcp import server as mcp_catia
            print(mcp_catia.client_config())
        else:
            print(mcp_server.client_config())
        return
    if '--selftest' in argv:
        from .selftest import run_selftest
        run_selftest()
        return
    if '--catia-info' in argv:
        from . import catia
        ins = catia.find_installs()
        print('检测到 %d 套 CATIA V5 安装:' % len(ins))
        for i in ins:
            print('  * %s' % i['label'])
            print('      程序: %s' % i['exe'])
            if i['env']:
                print('      环境: %s   (-direnv "%s")' % (i['env'], i['direnv']))
            print('      启动: "%s" %s' % (os.path.join(i['win_dir'], 'code', 'bin', 'CATSTART.exe'),
                                          catia.start_args(i)))
        ok, msg = catia.probe()
        print('\n当前采用: %s' % ('\n' + msg if ok else msg))
        return
    if '--push' in argv:
        from . import catia, nlparser
        i = argv.index('--push')
        text = argv[i + 1] if i + 1 < len(argv) else ''
        if not text or text.startswith('-'):
            print('用法: python -m ai_modeler --push "画一个 100x60x20 的长方体" '
                  '[--save out.CATPart]')
            return
        save = None
        if '--save' in argv:
            j = argv.index('--save')
            save = argv[j + 1] if j + 1 < len(argv) else None
        res = nlparser.parse(text)
        if not res.ok:
            print('解析失败: %s' % res.message)
            return
        doc = res.doc
        doc.build()
        print(res.summary())
        ok, msg, script = catia.push_to_catia(doc, save_catpart=save)
        print(msg)
        if script:
            print('生成的脚本: %s' % script)
        return
    if '--catia-lib' in argv:
        from . import catialib
        if '--rebuild' in argv:
            print(catialib.summary(catialib.build_index()))
            return
        if '--search' in argv:
            i = argv.index('--search')
            kw = argv[i + 1] if i + 1 < len(argv) else ''
            rows = catialib.search(kw)
            print('关键词 %r 命中 %d 条:' % (kw, len(rows)))
            for it in rows:
                print('  %-28s [%s]  %s' % (it['name'], it['category'], it['path']))
            return
        print(catialib.summary())
        return
    if '--cli' in argv:
        i = argv.index('--cli')
        sys.exit(_cli(argv[i + 1:]))
    if '--version' in argv or '-v' in argv:
        from . import __version__
        print('AI 自动建模助手 v%s' % __version__)
        return
    from .app import main as gui_main
    gui_main()


if __name__ == '__main__':
    main()
