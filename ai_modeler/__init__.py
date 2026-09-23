# -*- coding: utf-8 -*-
"""AI 自动建模助手 —— 自然语言驱动的参数化三维建模工具。

模块:
    kernel     几何内核 (BSP-CSG 布尔运算与基础特征)
    docmodel   特征树文档模型
    nlparser   自然语言解析引擎
    exporters  STL / OBJ / JSON / CATIA 脚本导出
    viewer3d   tkinter 三维预览渲染器
    app        桌面图形界面
    mcp_server MCP 协议服务端 (供 AI 客户端调用)
    catia      CATIA V5 COM 驱动
    llm        可选的大模型解析适配层
"""
from __future__ import annotations

import sys as _sys

# 控制台编码兜底: 某些中文 Windows 代码页无法表示 ³ ² 等字符, 避免直接崩溃
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except Exception:
        pass

__version__ = '1.2.0'
__all__ = ['kernel', 'docmodel', 'nlparser', 'exporters', 'viewer3d', 'app',
           'mcp_server', 'catia', 'llm', 'config', 'selftest']
