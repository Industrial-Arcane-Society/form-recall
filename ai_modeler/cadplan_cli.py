"""Backward-compatible import path; implementation lives in a domain package."""
import os as _os
import sys as _sys
if __package__ in (None, ''):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from ai_modeler._aliases import alias_legacy_module as _alias_legacy_module
_alias_legacy_module('ai_modeler.sdk.cadplan_cli')

if __name__ == '__main__':
    from ai_modeler.sdk.cadplan_cli import main
    raise SystemExit(main())
