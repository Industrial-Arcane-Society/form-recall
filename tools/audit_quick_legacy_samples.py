#!/usr/bin/env python3
"""Compare the legacy public Quick parser with the Rust headless Quick path.

The 66 prompts are taken from the embedded legacy parts catalog. The Python
side calls the original ``Parser.parse`` entry point; the Rust side calls the
same headless executable used for Quick/OCCT smoke acceptance. Temporary model
outputs are removed automatically when the audit finishes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/core/cad-engine/resources/native_parts_catalog.json"


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def legacy_result(prompt: str) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src/legacy"))
    from ai_modeler.core.parsing.nlparser import Parser

    parsed = Parser().parse(prompt)
    stats = parsed.doc.stats()
    return {
        "ok": bool(parsed.ok),
        "message": parsed.message,
        "features": [
            {
                "kind": feature.kind,
                "operation": feature.op,
                "name": feature.name,
                "parameters": json_safe(feature.params or {}),
            }
            for feature in parsed.doc.features
        ],
        "dimensions_mm": json_safe(stats.get("size")),
        "volume_mm3": stats.get("volume"),
        "warnings": json_safe(parsed.warnings),
        "questions": json_safe(parsed.questions),
    }


def rust_result(
    prompt: str,
    index: int,
    *,
    executable: Path,
    resource_root: Path,
    manifest: Path,
    scratch: Path,
    timeout: float,
) -> dict[str, Any]:
    data_dir = scratch / f"case-{index:02d}"
    env = os.environ.copy()
    env["AIMODELER_OCCT_MANIFEST"] = str(manifest)
    try:
        process = subprocess.run(
            [str(executable), str(resource_root), str(data_dir), prompt],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "timeout_seconds": timeout}

    if process.returncode != 0:
        return {
            "state": "native_error",
            "exit_code": process.returncode,
            "stderr": process.stderr.strip()[-1000:],
        }
    try:
        wrapper = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        return {
            "state": "invalid_cli_json",
            "error": str(error),
            "stdout": process.stdout[-1000:],
            "stderr": process.stderr.strip()[-1000:],
        }

    document = wrapper.get("document") or {}
    report = document.get("geometry_report") or {}
    cadplan = document.get("cadplan") or {}
    return {
        "state": document.get("state", wrapper.get("status", "missing_state")),
        "message": document.get("message"),
        "features": json_safe(cadplan.get("features", document.get("features", []))),
        "dimensions_mm": json_safe(report.get("dimensions_mm")),
        "volume_mm3": report.get("volume_mm3"),
        "geometry_report_state": report.get("state"),
        "closed_solid_count": report.get("closed_solid_count"),
        "warnings": json_safe(document.get("warnings", [])),
        "unsupported": json_safe(document.get("unsupported", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", type=Path, required=True, help="ai-modeler-headless-smoke executable")
    parser.add_argument("--resources", type=Path, default=ROOT / "src/legacy", help="resource root passed to headless Quick")
    parser.add_argument("--occt-manifest", type=Path, required=True, help="staged OCCT runtime-manifest.json")
    parser.add_argument("--timeout", type=float, default=90, help="timeout per catalog prompt")
    parser.add_argument("--output", type=Path, help="write the full JSON audit to this path")
    args = parser.parse_args()

    executable = args.headless.resolve()
    resources = args.resources.resolve()
    manifest = args.occt_manifest.resolve()
    for label, path in (("headless executable", executable), ("resource root", resources), ("OCCT manifest", manifest)):
        if not path.exists():
            parser.error(f"{label} does not exist: {path}")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = []
    with tempfile.TemporaryDirectory(prefix="quick-legacy-sample-audit-") as tmp:
        scratch = Path(tmp)
        for index, item in enumerate(catalog["parts"], 1):
            prompt = item["sample_prompt"]
            legacy = legacy_result(prompt)
            rust = rust_result(
                prompt,
                index,
                executable=executable,
                resource_root=resources,
                manifest=manifest,
                scratch=scratch,
                timeout=args.timeout,
            )
            rows.append(
                {
                    "category": item["category"],
                    "input": prompt,
                    "legacy": legacy,
                    "rust": rust,
                }
            )
            print(
                f"[{index:02d}/{len(catalog['parts'])}] {item['category']}: "
                f"legacy={'complete' if legacy['ok'] else 'no_model'}, "
                f"rust={rust['state']}",
                file=sys.stderr,
                flush=True,
            )

    result = {
        "source": "legacy Parser.parse versus native Quick headless CLI",
        "catalog": str(CATALOG.relative_to(ROOT)).replace("\\", "/"),
        "sample_count": len(rows),
        "rows": rows,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8", newline="\n")
        print(f"wrote {output}")
    else:
        sys.stdout.write(encoded)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
