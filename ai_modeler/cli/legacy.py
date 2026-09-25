"""Compatibility commands retained from the original single-file CLI."""
from __future__ import annotations

import sys


def run_text_cli(argv: list[str]) -> int:
    """Run the original text-to-solid command and optional file export."""
    from ai_modeler.core import exporters, nlparser

    text = None
    export = None
    out = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--export", "-e") and index + 1 < len(argv):
            export = argv[index + 1]
            index += 2
            continue
        if arg in ("--out", "-o") and index + 1 < len(argv):
            out = argv[index + 1]
            index += 2
            continue
        if text is None:
            text = arg
        index += 1

    if not text:
        print('用法: python -m ai_modeler --cli "画一个 100x60x20 的长方体" [--export out.stl]')
        return 1

    result = nlparser.parse(text)
    if not result.ok:
        print("解析失败: " + result.message)
        return 2

    doc = result.doc
    body = doc.build()
    print(result.summary())
    for index, feature in enumerate(doc.features, 1):
        operation = "切除" if feature.op == "cut" else "合并"
        print("  %d. [%s] %s" % (index, operation, feature.describe()))
    stats = doc.stats()
    if stats["size"]:
        print("外形尺寸: %.2f × %.2f × %.2f mm" % stats["size"])
    print("体积: %.1f mm³   表面积: %.1f mm²   面片: %d" % (
        stats["volume"], stats["area"], stats["triangles"]))

    if export:
        path = out or export
        extension = export.lower()
        if extension.endswith(".stl"):
            exporters.export_stl(body, path, doc.name)
        elif extension.endswith(".obj"):
            exporters.export_obj(body, path, doc.name)
        elif extension.endswith(".py"):
            exporters.export_catia_script(doc, path)
        elif extension.endswith(".txt"):
            exporters.export_report(doc, path, text)
        else:
            exporters.export_project(doc, path)
        print("已导出: " + path)
    return 0


def _read_text_arg(argv: list[str]) -> str | None:
    """Read ``--text`` or ``--text-file`` input, preferring the latter."""
    text = None
    for index, arg in enumerate(argv):
        if arg == "--text" and index + 1 < len(argv):
            text = argv[index + 1]
        elif arg == "--text-file" and index + 1 < len(argv):
            raw = None
            for encoding in ("utf-16", "utf-8-sig", "utf-8", "gbk"):
                try:
                    with open(argv[index + 1], "r", encoding=encoding) as file:
                        raw = file.read()
                    break
                except UnicodeError:
                    continue
                except OSError:
                    return None
            text = raw
    return text


def run_catia_export(argv: list[str]) -> int:
    """Export parsed legacy features as CATIA VBS and/or JSON."""
    from ai_modeler.catia_backend import catia_export
    from ai_modeler.core import nlparser

    text = _read_text_arg(argv)
    if not text or not text.strip():
        sys.stderr.write("缺少建模描述 (--text 或 --text-file)\n")
        return 2

    vbs_path = json_path = None
    for index, arg in enumerate(argv):
        if arg == "--catia-vbs" and index + 1 < len(argv):
            vbs_path = argv[index + 1]
        elif arg == "--catia-json" and index + 1 < len(argv):
            json_path = argv[index + 1]

    result = nlparser.parse(text)
    if not result.ok:
        sys.stderr.write(result.message + "\n")
        return 3

    doc = result.doc
    doc.build()
    if vbs_path:
        catia_export.export_catia_vbs(doc, vbs_path)
    if json_path:
        catia_export.export_catia_json(doc, json_path)
    features, warnings = catia_export.build_catia_features(doc)
    sys.stdout.write("识别到 %d 个特征, 展开为 %d 个 CATIA 操作\n" % (
        len(doc.features), len(features)))
    for warning in warnings:
        sys.stdout.write("注意: %s\n" % warning)
    return 0
