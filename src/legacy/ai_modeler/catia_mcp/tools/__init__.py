# -*- coding: utf-8 -*-
r"""catia_mcp 工具模块集合。

每个模块导出 TOOLS = [(名字, 说明, inputSchema, 函数), ...]。
模块缺失/写坏都不影响其它模块加载(方便并行开发与增量升级)。
"""
from __future__ import annotations

import importlib
import os

# 参考项目 catia-v5-mcp-server 的 7 个模块(名字与它保持一致, 便于对拍)
MODULES = ('document', 'sketcher', 'part_design', 'gsd', 'assembly',
           'measurement', 'export')

# 本插件在参考项目之外额外提供的工具(自然语言建模 / 模型库 / 几何校验)
EXTRA_MODULES = ('modeler',)


def load_tools(verbose: bool = False):
    """返回 (工具列表, 加载报告)。"""
    here = os.path.dirname(os.path.abspath(__file__))
    tools, report = [], []
    for name in MODULES + EXTRA_MODULES:
        path = os.path.join(here, name + '.py')
        if not os.path.isfile(path):
            report.append('%s: 未实现(跳过)' % name)
            continue
        try:
            mod = importlib.import_module('.' + name, __package__)
        except Exception as e:                       # noqa: BLE001
            report.append('%s: 导入失败 %s' % (name, e))
            continue
        got = list(getattr(mod, 'TOOLS', []) or [])
        tools.extend(got)
        report.append('%s: %d 个工具' % (name, len(got)))
    return tools, report
