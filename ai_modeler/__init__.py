# -*- coding: utf-8 -*-
"""AI 自动建模助手。

Implementation modules live in focused subpackages (``agent``, ``catia_backend``,
``catia_mcp``, ``cli``, ``core``, ``parts``, ``runtime``, ``sdk`` and ``ui``).
Historical top-level imports remain available through lazy compatibility aliases.
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
           'mcp_server', 'catia', 'llm', 'config', 'selftest', 'cadplan',
           'agentloop']


# Keep the historical ``from ai_modeler import <module>`` surface while
# implementations live in focused subpackages.
from ._aliases import MODULE_ALIASES as _MODULE_ALIASES
from ._aliases import import_legacy_module as _import_legacy_module
from ._aliases import install_legacy_module_finder as _install_legacy_module_finder

_install_legacy_module_finder()

def __getattr__(name):
    if name in _MODULE_ALIASES:
        module = _import_legacy_module(name)
        globals()[name] = module
        return module
    raise AttributeError(f"module 'ai_modeler' has no attribute {name!r}")

def __dir__():
    return sorted(set(globals()) | set(_MODULE_ALIASES))
