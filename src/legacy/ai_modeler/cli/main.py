"""Dispatch the historical package command-line options."""
from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path

from . import catia as catia_commands
from . import legacy as legacy_commands


def _print_help(file=None) -> None:
    import sys

    file = file or sys.stdout
    file.write(
        "用法: python -m ai_modeler [命令]\n\n"
        "不带命令时启动桌面界面。\n"
        "  agent / --agent   使用 DSH Agent 根据文字和参考图建模\n"
        "  --cli             原无 LLM 快速模式\n"
        "  --cadplan         CADPlan SDK 命令\n"
        "  --catia-info      查看 CATIA 连接信息\n"
        "  --mcp             启动 MCP 服务\n"
        "  --version         显示版本\n"
        "  --help            显示此帮助\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run a package command; with no command, start the desktop UI."""
    argv = list(sys.argv[1:] if argv is None else argv)

    # The Tauri process starts this private mode as its persistent Python
    # backend. It must be checked before the ordinary no-argument UI path.
    if "--ui-bridge" in argv:
        from ai_modeler.ui.bridge import serve_stdio

        return serve_stdio()

    legacy_ui = "--legacy-ui" in argv
    if legacy_ui:
        argv.remove("--legacy-ui")

    if argv and argv[0] in {"agent", "--agent"}:
        from . import agent as agent_commands

        return agent_commands.main(argv[1:])
    if argv and argv[0] in {"--help", "-h"}:
        _print_help()
        return 0

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

        return run_selftest()
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

    if not legacy_ui and not argv:
        from ai_modeler.paths import PROJECT_ROOT

        candidates = [
            PROJECT_ROOT.parent / "apps" / "desktop" / "src-tauri" / "target" / "release" / "ai-modeler.exe",
            PROJECT_ROOT.parent / "apps" / "desktop" / "src-tauri" / "target" / "debug" / "ai-modeler.exe",
        ]
        tauri_exe = next((path for path in candidates if path.is_file()), None)
        if tauri_exe:
            env = os.environ.copy()
            python_executable = Path(sys.executable)
            if python_executable.name.lower() == "pythonw.exe":
                console_python = python_executable.with_name("python.exe")
                if console_python.is_file():
                    python_executable = console_python
            env["AIMODELER_PYTHON"] = str(python_executable)
            env["AIMODELER_ROOT"] = str(PROJECT_ROOT)
            subprocess.Popen([str(tauri_exe)], cwd=str(PROJECT_ROOT), env=env)
            return 0

    if argv:
        _print_help(sys.stderr)
        return 2

    from ai_modeler.ui.app import main as gui_main

    gui_main()
    return 0
