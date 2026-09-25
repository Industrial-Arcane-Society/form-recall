"""Compatibility routing for modules reorganized into domain packages."""
from __future__ import annotations

import importlib
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import PathFinder
from importlib.util import spec_from_loader
import sys


CORE_MODULE_ALIASES = {
    'constraints': 'ai_modeler.core.validation.constraints',
    'corrections': 'ai_modeler.core.validation.corrections',
    'directmesh': 'ai_modeler.core.geometry.directmesh',
    'docmodel': 'ai_modeler.core.model.docmodel',
    'exporters': 'ai_modeler.core.export.exporters',
    'extract': 'ai_modeler.core.parsing.extract',
    'kernel': 'ai_modeler.core.geometry.kernel',
    'measure': 'ai_modeler.core.geometry.measure',
    'meshslice': 'ai_modeler.core.geometry.meshslice',
    'nlparser': 'ai_modeler.core.parsing.nlparser',
    'occ_kernel': 'ai_modeler.core.geometry.occ_kernel',
    'params': 'ai_modeler.core.parsing.params',
}

PART_MODULE_ALIASES = {
    'candidates': 'ai_modeler.parts.library.candidates',
    'partlib': 'ai_modeler.parts.library.partlib',
    'realholes': 'ai_modeler.parts.measured.realholes',
    'reallib': 'ai_modeler.parts.measured.reallib',
    'realparts': 'ai_modeler.parts.measured.realparts',
    'realsolid': 'ai_modeler.parts.measured.realsolid',
    'std_bearing': 'ai_modeler.parts.standard.std_bearing',
    'std_bolt': 'ai_modeler.parts.standard.std_bolt',
    'std_pin': 'ai_modeler.parts.standard.std_pin',
    'std_pulley': 'ai_modeler.parts.standard.std_pulley',
    'std_sprocket': 'ai_modeler.parts.standard.std_sprocket',
}

# Historical public imports, for example ``ai_modeler.kernel``.
MODULE_ALIASES = {
    'agentloop': 'ai_modeler.agent.agentloop',
    'llm': 'ai_modeler.agent.llm',
    'llm_plan': 'ai_modeler.agent.llm_plan',
    'online': 'ai_modeler.agent.online',
    'twostage': 'ai_modeler.agent.twostage',
    'cadplan': 'ai_modeler.sdk.cadplan',
    'cadplan_cli': 'ai_modeler.sdk.cadplan_cli',
    'clipboard_image': 'ai_modeler.sdk.clipboard_image',
    **CORE_MODULE_ALIASES,
    **PART_MODULE_ALIASES,
    'catia': 'ai_modeler.catia_backend.catia',
    'catialib': 'ai_modeler.catia_backend.catialib',
    'catiasrv': 'ai_modeler.catia_backend.catiasrv',
    'catia_export': 'ai_modeler.catia_backend.catia_export',
    'app': 'ai_modeler.ui.app',
    'softrender': 'ai_modeler.ui.softrender',
    'theme': 'ai_modeler.ui.theme',
    'viewer3d': 'ai_modeler.ui.viewer3d',
    'config': 'ai_modeler.runtime.config',
    'mcp_server': 'ai_modeler.runtime.mcp_server',
    'selftest': 'ai_modeler.runtime.selftest',
}

# Also keep import paths that existed during the first package reorganization.
NESTED_MODULE_ALIASES = {
    **{f'ai_modeler.core.{name}': target for name, target in CORE_MODULE_ALIASES.items()},
    **{f'ai_modeler.parts.{name}': target for name, target in PART_MODULE_ALIASES.items()},
}


class _AliasModuleLoader(Loader):
    """Load an alias as the canonical module, preserving shared module state."""

    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):
        return None


class _LegacyModuleFinder(MetaPathFinder):
    """Resolve historical imports without keeping a shim file per module."""

    package_name = __package__.partition('.')[0]

    def find_spec(self, fullname, path=None, target=None):
        parent, separator, short_name = fullname.rpartition('.')
        if not separator:
            return None

        if parent == self.package_name:
            module_target = MODULE_ALIASES.get(short_name)
        else:
            module_target = NESTED_MODULE_ALIASES.get(fullname)
        if module_target is None or PathFinder.find_spec(fullname, path) is not None:
            return None

        return spec_from_loader(fullname, _AliasModuleLoader(module_target))


_LEGACY_FINDER = _LegacyModuleFinder()


def install_legacy_module_finder():
    """Enable legacy imports for modules moved into focused subpackages."""
    if not any(isinstance(finder, _LegacyModuleFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _LEGACY_FINDER)


def import_legacy_module(name: str):
    """Load a historical top-level ``ai_modeler`` module by implementation path."""
    target = MODULE_ALIASES.get(name)
    if target is None:
        raise AttributeError(f"module 'ai_modeler' has no attribute {name!r}")
    module = importlib.import_module(target)
    sys.modules[f'ai_modeler.{name}'] = module
    return module


def alias_legacy_module(alias_name: str):
    """Route an explicit historical dotted import to its implementation."""
    short_name = alias_name.rsplit('.', 1)[-1]
    module = import_legacy_module(short_name)
    sys.modules[alias_name] = module
    parent_name = alias_name.rpartition('.')[0]
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, short_name, module)
    return module
