"""Compare the native Rust catalog against the old Python UI bridge results.

Run from any directory with the repository's Python environment:
    python src/apps/desktop/src-tauri/examples/native_parts_catalog_parity.py

Use --write-snapshot only when intentionally refreshing the embedded metadata
after changing the legacy Python catalog. The regular parity run never writes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tauri_cargo import run_cargo


REPO = Path(__file__).resolve().parents[5]
TAURI = REPO / "src" / "apps" / "desktop" / "src-tauri"
SNAPSHOT = REPO / "src" / "core" / "cad-engine" / "resources" / "native_parts_catalog.json"


def legacy_bridge():
    sys.path.insert(0, str(REPO / "src" / "legacy"))
    from ai_modeler.ui.bridge import Bridge

    # These read-only dispatch branches do not access instance state. Calling
    # the actual legacy route preserves its field formatting and insertion
    # order for the parity comparison without starting the bridge worker.
    return Bridge.__new__(Bridge)


def run_rust(*arguments: str):
    command = [
        "run",
        "--quiet",
        "--manifest-path",
        str(TAURI / "Cargo.toml"),
        "--example",
        "native_parts_catalog_cli",
        "--",
        *arguments,
    ]
    result = run_cargo(
        command,
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(
            f"Rust catalog CLI failed ({result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Rust catalog CLI did not emit JSON: {result.stdout[:500]!r}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-snapshot",
        action="store_true",
        help="write current old Python parts.list and samples.list output as the Rust data source",
    )
    args = parser.parse_args()

    bridge = legacy_bridge()
    expected = {
        "parts": bridge.dispatch("parts.list", {}, "f6-3-parity"),
        "samples": bridge.dispatch("samples.list", {}, "f6-3-parity"),
    }
    if args.write_snapshot:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(
            json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"wrote legacy snapshot: {len(expected['parts'])} part rows, "
            f"{len(expected['samples'])} samples -> {SNAPSHOT.relative_to(REPO)}"
        )
        return 0

    embedded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if embedded != expected:
        print("FAIL: embedded snapshot differs from legacy Python bridge output")
        print(f"  legacy parts={len(expected['parts'])}, samples={len(expected['samples'])}")
        print(f"  embedded parts={len(embedded.get('parts', []))}, samples={len(embedded.get('samples', []))}")
        return 1

    actual = run_rust("snapshot")
    if actual != expected:
        print("FAIL: Rust snapshot differs from legacy Python bridge output")
        return 1

    for query in ("圆锥滚子轴承", "球轴承", "轴", "no-such-part-catalog-entry"):
        expected_rows = bridge.dispatch("parts.list", {"query": query}, "f6-3-parity")
        actual_rows = run_rust("parts", query)
        if actual_rows != expected_rows:
            print(f"FAIL: query {query!r} differs: legacy={len(expected_rows)} Rust={len(actual_rows)}")
            return 1

    actual_samples = run_rust("samples")
    if actual_samples != expected["samples"]:
        print("FAIL: Rust sample list differs from legacy Python order/content")
        return 1

    print(
        "PASS: Rust matches legacy Python fields, row/sample order, aliases, "
        f"sample prompts, and manual counts ({len(expected['parts'])} parts, "
        f"{len(expected['samples'])} samples; 4 query cases)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
