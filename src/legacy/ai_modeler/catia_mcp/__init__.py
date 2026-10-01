# -*- coding: utf-8 -*-
r"""CATIA V5 MCP 服务器(兼容 catia-v5-mcp-server 工具面)。

    python -m ai_modeler.catia_mcp          # stdio 上的 MCP 服务器
    python -m ai_modeler.catia_mcp --list-tools
    python -m ai_modeler.catia_mcp --config

模块划分(与参考项目一致):
    server.py      协议路由(initialize/tools/resources/prompts)
    bridge.py      cscript + COM 桥(不依赖 pywin32)
    tools/         7 个工具模块 + 本插件扩展(modeler)
"""
from __future__ import annotations

__version__ = '2.0.0'
