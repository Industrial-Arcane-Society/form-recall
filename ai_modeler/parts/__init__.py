"""Part catalogs grouped into reusable libraries and source-specific parts."""
from __future__ import annotations

import importlib as _importlib

from ai_modeler._aliases import PART_MODULE_ALIASES as _MODULES

__all__ = sorted(_MODULES)


def __getattr__(name: str):
    target = _MODULES.get(name)
    if target is None:
        raise AttributeError(f"module 'ai_modeler.parts' has no attribute {name!r}")
    module = _importlib.import_module(target)
    globals()[name] = module
    return module


def __dir__():
    return sorted(set(globals()) | set(_MODULES))
