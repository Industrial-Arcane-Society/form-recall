# -*- coding: utf-8 -*-
r"""工具实现的公共小工具: JSON Schema 构造 + Python -> VBScript 字面量转义。

所有工具模块都按这个约定写:
    TOOLS = [(工具名, 说明, inputSchema, 处理函数), ...]
    处理函数(args: dict) -> str        (返回给 AI 客户端的文本)
    出错要么 raise CatiaError/ValueError, 要么在脚本里 Fail "说明"
"""
from __future__ import annotations


def S(desc: str) -> dict:
    return {'type': 'string', 'description': desc}


def N(desc: str) -> dict:
    return {'type': 'number', 'description': desc}


def I(desc: str) -> dict:
    return {'type': 'integer', 'description': desc}


def B(desc: str) -> dict:
    return {'type': 'boolean', 'description': desc}


def A(desc: str, items=None) -> dict:
    return {'type': 'array', 'description': desc, 'items': items or {'type': 'number'}}


def obj(props: dict, required=(), desc: str = '') -> dict:
    out = {'type': 'object', 'properties': props,
           'required': list(required), 'additionalProperties': False}
    if desc:
        out['description'] = desc
    return out


def vbs(value) -> str:
    """把 Python 值转成 VBScript 字面量(字符串会转义引号)。"""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    return '"' + s.replace('"', '""') + '"'


def vbs_num(value, default=0.0) -> str:
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return repr(float(default))


def arg_f(args: dict, key: str, default: float = 0.0) -> float:
    v = args.get(key, None)
    if v is None or v == '':
        return float(default)
    return float(v)


def arg_s(args: dict, key: str, default: str = '') -> str:
    v = args.get(key, None)
    return default if v is None else str(v)


def arg_i(args: dict, key: str, default: int = 0) -> int:
    v = args.get(key, None)
    if v is None or v == '':
        return int(default)
    return int(float(v))


def arg_b(args: dict, key: str, default: bool = False) -> bool:
    v = args.get(key, None)
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'on', '是')


def lines(args: dict, *names) -> str:
    """把若干"说明: 值"拼成多行文本, 空值跳过。"""
    out = []
    for n in names:
        v = args.get(n)
        if v not in (None, ''):
            out.append('%s=%s' % (n, v))
    return ' '.join(out)
