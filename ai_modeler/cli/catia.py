"""Legacy CATIA command handlers used by the package CLI."""
from __future__ import annotations

import os


def _option_value(argv: list[str], option: str, default: str | None) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return default
    return argv[index + 1] if index + 1 < len(argv) else default


def run_ui(argv: list[str]) -> int:
    from ai_modeler.catia_backend import catiasrv

    output = _option_value(
        argv, "--catia-ui", os.path.join(os.environ.get("TEMP", "."), "aim_feat.vbs"))
    return catiasrv.run_catia_ui(output) or 0


def run_macro(argv: list[str]) -> int:
    from ai_modeler.catia_backend import catiasrv

    output = _option_value(
        argv, "--catia-macro", os.path.join(os.environ.get("TEMP", "."), "aim_macro_out.txt"))
    return catiasrv.run_catia_macro(output) or 0


def show_info() -> int:
    from ai_modeler.catia_backend import catia

    installs = catia.find_installs()
    print("检测到 %d 套 CATIA V5 安装:" % len(installs))
    for install in installs:
        print("  * %s" % install["label"])
        print("      程序: %s" % install["exe"])
        if install["env"]:
            print("      环境: %s   (-direnv \"%s\")" % (install["env"], install["direnv"]))
        print("      启动: \"%s\" %s" % (
            os.path.join(install["win_dir"], "code", "bin", "CATSTART.exe"),
            catia.start_args(install)))
    ok, message = catia.probe()
    print("\n当前采用: %s" % ("\n" + message if ok else message))
    return 0


def push(argv: list[str]) -> int:
    from ai_modeler.catia_backend import catia
    from ai_modeler.core import nlparser

    index = argv.index("--push")
    text = argv[index + 1] if index + 1 < len(argv) else ""
    if not text or text.startswith("-"):
        print('用法: python -m ai_modeler --push "画一个 100x60x20 的长方体" [--save out.CATPart]')
        return 0

    save = _option_value(argv, "--save", None)
    result = nlparser.parse(text)
    if not result.ok:
        print("解析失败: %s" % result.message)
        return 0
    doc = result.doc
    doc.build()
    print(result.summary())
    ok, message, script = catia.push_to_catia(doc, save_catpart=save)
    print(message)
    if script:
        print("生成的脚本: %s" % script)
    # The historical launcher reports CATIA failure in its output but keeps
    # a successful process exit; preserve that behavior for existing callers.
    return 0


def show_library(argv: list[str]) -> int:
    from ai_modeler.catia_backend import catialib

    if "--rebuild" in argv:
        print(catialib.summary(catialib.build_index()))
        return 0
    if "--search" in argv:
        keyword = _option_value(argv, "--search", "") or ""
        rows = catialib.search(keyword)
        print("关键词 %r 命中 %d 条:" % (keyword, len(rows)))
        for item in rows:
            print("  %-28s [%s]  %s" % (item["name"], item["category"], item["path"]))
        return 0
    print(catialib.summary())
    return 0
