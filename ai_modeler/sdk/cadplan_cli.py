# -*- coding: utf-8 -*-
"""JSON-only command line interface for CADPlan."""
from __future__ import annotations

import argparse
import json
import sys

from ai_modeler.sdk import cadplan


def _emit(value):
    # Preview carries a live Document for SDK callers; it is intentionally omitted
    # from the CLI contract.
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if not key.startswith("_")}
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _parser():
    parser = argparse.ArgumentParser(
        prog="python -m ai_modeler --cadplan",
        description="Validate, preview, and build a data-only CADPlan through the CATIA V5 SDK.",
    )
    parser.add_argument("--json", action="store_true", default=True,
                        help="emit structured JSON on stdout (the default and only output format)")
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema", help="write the CADPlan v1 JSON Schema")
    schema.add_argument("--out", required=True, help="output .schema.json path")
    for name, help_text in (
        ("validate", "validate plan shape, required parameters, provenance, and CATIA mapping"),
        ("preview", "build local preview geometry, write a three-view PNG, and inspect planned CATIA operations"),
        ("build", "build an editable CATPart, AIMODEL, STL, three/four-view PNGs, and report"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("plan", help="CADPlan JSON file")
        if name == "preview":
            command.add_argument("--image", help="output path for the front/right/top preview PNG")
        elif name == "build":
            command.add_argument("--catpart", help="output .CATPart path; outputs are siblings")
            command.add_argument("--strict", action="store_true",
                                 help="block before CATIA if any feature is approximate or unsupported")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "schema":
        try:
            path = cadplan.write_schema(args.out)
            _emit({"state": "written", "schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
                   "path": path, "feature_count": len(cadplan.FEATURE_SPECS)})
            return 0
        except OSError as exc:
            _emit({"state": "error", "error": str(exc)})
            return 5
    if args.command == "validate":
        result = cadplan.validate_plan(args.plan)
        _emit(result)
        return 0 if result["valid"] else (3 if result["state"] == "needs_confirmation" else 2)
    if args.command == "preview":
        result = cadplan.preview_plan(args.plan, image_path=args.image)
        _emit(result)
        return 0 if result["state"] == "previewed" else 2
    if args.command == "build":
        result = cadplan.build_plan(args.plan, catpart_path=args.catpart, strict=args.strict)
        _emit(result)
        if result.get("state") == "strict_blocked":
            return 3
        return 0 if result.get("state") in {"built", "partial"} else 5
    _emit({"state": "error", "error": "unknown command"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
