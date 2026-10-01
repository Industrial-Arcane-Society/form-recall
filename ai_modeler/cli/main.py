"""Dispatch the historical package command-line options."""
from __future__ import annotations

import sys

from . import catia as catia_commands
from . import legacy as legacy_commands


def main(argv: list[str] | None = None) -> int:
    """Run a package command; with no command, start the desktop UI."""
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--cadplan" in argv:
        from ai_modeler.sdk import cadplan_cli

        index = argv.index("--cadplan")
        return cadplan_cli.main(argv[index + 1:])
    if "--catia-vbs" in argv or "--catia-json" in argv:
        return legacy_commands.run_catia_export(argv)
    if "--catia-ui" in argv:
        return catia_commands.run_ui(argv)
    if "--catia-macro" in argv:
        return catia_commands.run_macro(argv)
    if "--mcp" in argv:
        from ai_modeler.runtime import mcp_server

        mcp_server.serve_stdio()
        return 0
    if "--mcp-catia" in argv or "--catia-mcp" in argv:
        from ai_modeler.catia_mcp import server as catia_mcp

        if "--selftest" in argv:
            return catia_mcp.selftest() or 0
        catia_mcp.serve_stdio()
        return 0
    if "--mcp-config" in argv:
        if "--catia" in argv:
            from ai_modeler.catia_mcp import server as catia_mcp

            print(catia_mcp.client_config())
        else:
            from ai_modeler.runtime import mcp_server

            print(mcp_server.client_config())
        return 0
    if "--selftest" in argv:
        from ai_modeler.runtime.selftest import run_selftest

        run_selftest()
        return 0
    if "--catia-info" in argv:
        return catia_commands.show_info()
    if "--push" in argv:
        return catia_commands.push(argv)
    if "--catia-lib" in argv:
        return catia_commands.show_library(argv)
    if "--cli" in argv:
        index = argv.index("--cli")
        return legacy_commands.run_text_cli(argv[index + 1:])
    if "--version" in argv or "-v" in argv:
        from ai_modeler import __version__

        print("AI 自动建模助手 v%s" % __version__)
        return 0

    from ai_modeler.ui.app import main as gui_main

    gui_main()
    return 0
