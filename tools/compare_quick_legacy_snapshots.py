#!/usr/bin/env python3
"""Compare two outputs from audit_quick_legacy_samples.py.

The comparison is read-only. Numeric geometry fields use
``abs(a-b) <= abs_tol + rel_tol * max(abs(a), abs(b))``; state, geometry
report state, and closed-solid count are compared exactly. Optional expectation
ranges turn summary checks into an acceptance gate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


COMPARE_FIELDS = (
    ("state", False),
    ("dimensions_mm", True),
    ("geometry_report_state", False),
    ("closed_solid_count", False),
    ("volume_mm3", True),
)


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read snapshot {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError(f"{path}: expected an object containing a rows array")
    rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(data["rows"], 1):
        if not isinstance(row, dict) or not isinstance(row.get("input"), str):
            raise ValueError(f"{path}: row {index} has no string input")
        prompt = row["input"]
        if prompt in rows:
            raise ValueError(f"{path}: duplicate input at row {index}: {prompt!r}")
        if not isinstance(row.get("legacy"), dict) or not isinstance(row.get("rust"), dict):
            raise ValueError(f"{path}: row {index} must contain legacy and rust objects")
        rows[prompt] = row
    declared_count = data.get("sample_count")
    if declared_count is not None and declared_count != len(rows):
        raise ValueError(
            f"{path}: sample_count={declared_count} but rows contains {len(rows)} unique inputs"
        )
    return {"data": data, "rows": rows}


def numeric_equal(left: Any, right: Any, abs_tol: float, rel_tol: float) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        a, b = float(left), float(right)
        if not math.isfinite(a) or not math.isfinite(b):
            return a == b
        return abs(a - b) <= abs_tol + rel_tol * max(abs(a), abs(b))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            numeric_equal(a, b, abs_tol, rel_tol) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            numeric_equal(left[key], right[key], abs_tol, rel_tol) for key in left
        )
    return left == right


def format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse_range(value: str) -> tuple[int, int]:
    """Accept a single integer or an inclusive MIN:MAX range."""
    parts = value.split(":")
    if len(parts) == 1:
        minimum = maximum = int(parts[0])
    elif len(parts) == 2:
        minimum, maximum = map(int, parts)
    else:
        raise argparse.ArgumentTypeError("expected N or MIN:MAX")
    if minimum < 0 or maximum < minimum:
        raise argparse.ArgumentTypeError("range must satisfy 0 <= MIN <= MAX")
    return minimum, maximum


def in_range(label: str, value: int, expected: tuple[int, int] | None) -> bool:
    if expected is None:
        return True
    low, high = expected
    passed = low <= value <= high
    state = "PASS" if passed else "FAIL"
    print(f"EXPECT {label}: {value} in [{low}, {high}] {state}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="baseline JSON snapshot")
    parser.add_argument("after", type=Path, help="candidate JSON snapshot")
    parser.add_argument(
        "--abs-tol", type=float, default=1e-6, help="absolute geometry tolerance (default: 1e-6)"
    )
    parser.add_argument(
        "--rel-tol", type=float, default=1e-9, help="relative geometry tolerance (default: 1e-9)"
    )
    parser.add_argument(
        "--expect-legacy-success",
        type=parse_range,
        metavar="N|MIN:MAX",
        help="expected legacy-success count in the after snapshot",
    )
    parser.add_argument(
        "--expect-rust-complete",
        type=parse_range,
        metavar="N|MIN:MAX",
        help="expected Rust complete count in the after snapshot",
    )
    parser.add_argument(
        "--expect-changed",
        type=parse_range,
        metavar="N|MIN:MAX",
        help="expected number of inputs with changes in compared Rust fields",
    )
    args = parser.parse_args()
    if not math.isfinite(args.abs_tol) or args.abs_tol < 0:
        parser.error("--abs-tol must be finite and non-negative")
    if not math.isfinite(args.rel_tol) or args.rel_tol < 0:
        parser.error("--rel-tol must be finite and non-negative")

    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    before_rows = before["rows"]
    after_rows = after["rows"]
    before_inputs = set(before_rows)
    after_inputs = set(after_rows)
    if before_inputs != after_inputs:
        missing = sorted(before_inputs - after_inputs)
        added = sorted(after_inputs - before_inputs)
        print("ERROR: snapshot input sets differ", file=sys.stderr)
        if missing:
            print("  missing from after: " + "; ".join(missing), file=sys.stderr)
        if added:
            print("  added in after: " + "; ".join(added), file=sys.stderr)
        return 2

    legacy_success = sum(bool(row["legacy"].get("ok")) for row in after_rows.values())
    rust_complete = sum(row["rust"].get("state") == "complete" for row in after_rows.values())
    changed_rows = 0
    print(
        f"Snapshots: {args.before} -> {args.after}\n"
        f"Inputs: {len(after_rows)} (sets identical)\n"
        f"After counts: legacy_success={legacy_success}, rust_complete={rust_complete}\n"
        f"Tolerances: abs={args.abs_tol:g}, rel={args.rel_tol:g}"
    )

    for prompt in before_rows:
        old_rust = before_rows[prompt]["rust"]
        new_rust = after_rows[prompt]["rust"]
        deltas = []
        for field, numeric in COMPARE_FIELDS:
            old_value, new_value = old_rust.get(field), new_rust.get(field)
            same = (
                numeric_equal(old_value, new_value, args.abs_tol, args.rel_tol)
                if numeric
                else old_value == new_value
            )
            if not same:
                deltas.append(f"{field}: {format_value(old_value)} -> {format_value(new_value)}")
        if deltas:
            changed_rows += 1
            print(f"CHANGE {prompt}")
            for delta in deltas:
                print(f"  {delta}")

    print(f"Changed inputs: {changed_rows}")
    expectations_ok = all(
        (
            in_range("legacy_success", legacy_success, args.expect_legacy_success),
            in_range("rust_complete", rust_complete, args.expect_rust_complete),
            in_range("changed_inputs", changed_rows, args.expect_changed),
        )
    )
    return 0 if expectations_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
