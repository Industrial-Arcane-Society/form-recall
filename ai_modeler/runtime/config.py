# -*- coding: utf-8 -*-
"""配置读写 (config.json)。"""
from __future__ import annotations
from ai_modeler.paths import config_path

import json
import os

CONFIG_PATH = str(config_path())

DEFAULTS = {
    'llm': {'enabled': False, 'base_url': 'https://api.openai.com/v1',
            'model': 'gpt-4o-mini', 'api_key': ''},
    'viewer': {'edges': True, 'grid': True, 'axes': True, 'perspective': True,
               'max_faces': 6000},
    'default_tessellation': {'cylinder_segments': 48, 'sphere_segments': 32},
    'build': {'time_budget': 30.0, 'max_faces': 60000},
    'online': {'enabled': True, 'timeout': 8},
    'last_export_dir': '',
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return _merge(DEFAULTS, json.load(f))
    except Exception:
        return dict(DEFAULTS)


def save(cfg: dict) -> str:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH
