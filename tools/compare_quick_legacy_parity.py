#!/usr/bin/env python3
"""Compare legacy Python Quick outputs with Rust Quick outputs in one audit snapshot.

This is a semantic audit, not an assertion that mesh volume equals OCCT B-Rep
volume. It compares feature signatures and every legacy parameter present in
the Rust CADPlan, then reports bounds and volume deltas separately.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


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


def equivalent(left: Any, right: Any, abs_tol: float) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        a, b = float(left), float(right)
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= abs_tol
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            equivalent(a, b, abs_tol) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            equivalent(left[key], right[key], abs_tol) for key in left
        )
    return left == right


DESCRIPTION_PARAMETER_KEYS = {"explain", "std_warnings"}
TESSELLATION_PARAMETER_KEYS = {"segments", "rings", "seg_per_turn", "wire_seg"}


def feature_differences(
    legacy_features: Any, rust_features: Any, abs_tol: float
) -> tuple[list[str], list[str], list[str], list[str]]:
    structural: list[str] = []
    parameters: list[str] = []
    tessellation: list[str] = []
    descriptions: list[str] = []
    if not isinstance(legacy_features, list) or not isinstance(rust_features, list):
        return ["feature list is missing or invalid"], parameters, tessellation, descriptions
    if len(legacy_features) != len(rust_features):
        structural.append(f"feature count: legacy={len(legacy_features)} rust={len(rust_features)}")

    for index, (legacy, rust) in enumerate(zip(legacy_features, rust_features), 1):
        if not isinstance(legacy, dict) or not isinstance(rust, dict):
            structural.append(f"feature {index}: invalid feature object")
            continue
        for field in ("kind", "operation"):
            left, right = legacy.get(field), rust.get(field)
            if left != right:
                structural.append(f"feature {index} {field}: {left!r} -> {right!r}")
        legacy_params = legacy.get("parameters") or {}
        rust_params = rust.get("parameters") or {}
        if not isinstance(legacy_params, dict) or not isinstance(rust_params, dict):
            parameters.append(f"feature {index}: invalid parameter object")
            continue
        for key, expected in legacy_params.items():
            actual = rust_params.get(key, None)
            # Omitted and null are equivalent only when Rust also leaves the
            # value empty. A concrete Rust value can change shape semantics
            # (for example radius2=null versus radius2=0 for a cylinder/cone).
            if expected is None:
                if actual is None:
                    continue
                parameters.append(
                    f"feature {index} {key}: null -> "
                    f"{json.dumps(actual, ensure_ascii=False) if key in rust_params else '<missing>'}"
                )
                continue
            if key in DESCRIPTION_PARAMETER_KEYS:
                if key not in rust_params or not equivalent(expected, actual, abs_tol):
                    descriptions.append(f"feature {index} {key}: legacy annotation not carried over")
                continue
            if key in TESSELLATION_PARAMETER_KEYS:
                if key not in rust_params or not equivalent(expected, actual, abs_tol):
                    tessellation.append(
                        f"feature {index} {key}: {json.dumps(expected, ensure_ascii=False)} -> "
                        f"{json.dumps(actual, ensure_ascii=False) if key in rust_params else '<missing>'}"
                    )
                continue
            if key not in rust_params or not equivalent(expected, actual, abs_tol):
                parameters.append(
                    f"feature {index} {key}: {json.dumps(expected, ensure_ascii=False)} -> "
                    f"{json.dumps(actual, ensure_ascii=False) if key in rust_params else '<missing>'}"
                )
    return structural, parameters, tessellation, descriptions


def dimension_error_mm(legacy: Any, rust: Any) -> float | None:
    if not isinstance(legacy, list) or not isinstance(rust, list) or len(legacy) != len(rust):
        return None
    try:
        values = [abs(float(left) - float(right)) for left, right in zip(legacy, rust)]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return max(values, default=0.0)


def volume_error_percent(legacy: Any, rust: Any) -> float | None:
    try:
        left, right = float(legacy), float(rust)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(left) or not math.isfinite(right):
        return None
    return abs(left - right) / max(abs(left), abs(right), 1e-12) * 100.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="audit output from audit_quick_legacy_samples.py")
    parser.add_argument(
        "--parameter-abs-tol",
        type=float,
        default=1e-9,
        help="absolute tolerance for numeric legacy/Rust feature parameters (default: 1e-9)",
    )
    parser.add_argument(
        "--dimension-tol-mm",
        type=float,
        default=0.02,
        help="maximum per-axis bounding-box difference to count as matching (default: 0.02)",
    )
    parser.add_argument(
        "--max-state-mismatches",
        type=int,
        help="fail if legacy success versus Rust complete differs on more than N inputs",
    )
    parser.add_argument(
        "--max-feature-signature-mismatches",
        type=int,
        help="fail if feature count/kind/operation differs on more than N successful pairs",
    )
    parser.add_argument(
        "--max-parameter-mismatches",
        type=int,
        help="fail if legacy parameters differ on more than N successful pairs",
    )
    parser.add_argument(
        "--show-volume-over",
        type=float,
        default=1.0,
        metavar="PERCENT",
        help="show successful pairs with a larger relative volume delta (default: 1%%)",
    )
    args = parser.parse_args()
    for label, value in (
        ("--parameter-abs-tol", args.parameter_abs_tol),
        ("--dimension-tol-mm", args.dimension_tol_mm),
        ("--show-volume-over", args.show_volume_over),
    ):
        if not math.isfinite(value) or value < 0:
            parser.error(f"{label} must be finite and non-negative")
    for label, value in (
        ("--max-state-mismatches", args.max_state_mismatches),
        ("--max-feature-signature-mismatches", args.max_feature_signature_mismatches),
        ("--max-parameter-mismatches", args.max_parameter_mismatches),
    ):
        if value is not None and value < 0:
            parser.error(f"{label} must be non-negative")

    try:
        snapshot = load_snapshot(args.snapshot)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    state_mismatches = 0
    successful_pairs = 0
    feature_mismatch_rows = 0
    parameter_mismatch_rows = 0
    tessellation_mismatch_rows = 0
    description_mismatch_rows = 0
    dimension_mismatch_rows = 0
    dimension_errors: list[float] = []
    volume_errors: list[tuple[float, str]] = []
    details: list[
        tuple[
            str,
            list[str],
            list[str],
            list[str],
            list[str],
            float | None,
            float | None,
        ]
    ] = []

    for prompt, row in snapshot["rows"].items():
        legacy, rust = row["legacy"], row["rust"]
        legacy_ok = bool(legacy.get("ok"))
        rust_complete = rust.get("state") == "complete"
        if legacy_ok != rust_complete:
            state_mismatches += 1
        if not (legacy_ok and rust_complete):
            continue
        successful_pairs += 1

        structural, parameters, tessellation, descriptions = feature_differences(
            legacy.get("features"), rust.get("features"), args.parameter_abs_tol
        )
        if structural:
            feature_mismatch_rows += 1
        if parameters:
            parameter_mismatch_rows += 1
        if tessellation:
            tessellation_mismatch_rows += 1
        if descriptions:
            description_mismatch_rows += 1
        dim_error = dimension_error_mm(legacy.get("dimensions_mm"), rust.get("dimensions_mm"))
        if dim_error is not None:
            dimension_errors.append(dim_error)
            if dim_error > args.dimension_tol_mm:
                dimension_mismatch_rows += 1
        volume_error = volume_error_percent(legacy.get("volume_mm3"), rust.get("volume_mm3"))
        if volume_error is not None:
            volume_errors.append((volume_error, prompt))
        details.append(
            (prompt, structural, parameters, tessellation, descriptions, dim_error, volume_error)
        )

    print(
        f"Snapshot: {args.snapshot}\n"
        f"Inputs: {len(snapshot['rows'])}; successful legacy/Rust pairs: {successful_pairs}\n"
        f"Mismatches: state={state_mismatches}, feature_signature_rows={feature_mismatch_rows}, "
        f"parameter_rows={parameter_mismatch_rows}, tessellation_rows={tessellation_mismatch_rows}, "
        f"description_rows={description_mismatch_rows}, bounds_over_{args.dimension_tol_mm:g}mm="
        f"{dimension_mismatch_rows}"
    )
    if dimension_errors:
        print(f"Maximum bounds delta: {max(dimension_errors):.9g} mm")
    if volume_errors:
        ordered = sorted(error for error, _prompt in volume_errors)
        median = statistics.median(ordered)
        print(
            f"Volume delta (legacy mesh stats vs Rust geometry report): "
            f"median={median:.6g}%, max={ordered[-1]:.6g}%, "
            f">{args.show_volume_over:g}%="
            f"{sum(error > args.show_volume_over for error in ordered)}"
        )
    print("VOLUME DELTAS ARE DIAGNOSTIC ONLY; mesh and B-Rep volume algorithms may differ.")

    shown = 0
    for prompt, structural, parameters, tessellation, descriptions, dim_error, volume_error in details:
        if (
            not structural
            and not parameters
            and not tessellation
            and not descriptions
            and (dim_error is None or dim_error <= args.dimension_tol_mm)
            and (volume_error is None or volume_error <= args.show_volume_over)
        ):
            continue
        print(f"DIFF {prompt}")
        for item in structural:
            print(f"  STRUCTURE {item}")
        for item in parameters:
            print(f"  PARAMETER {item}")
        for item in tessellation:
            print(f"  TESSELLATION {item}")
        for item in descriptions:
            print(f"  ANNOTATION {item}")
        if dim_error is not None and dim_error > args.dimension_tol_mm:
            print(f"  BOUNDS max_delta_mm={dim_error:.9g}")
        if volume_error is not None and volume_error > args.show_volume_over:
            print(f"  VOLUME relative_delta={volume_error:.6g}%")
        shown += 1
    print(f"Detailed differences shown: {shown}")

    gates = [
        ("state_mismatches", state_mismatches, args.max_state_mismatches),
        ("feature_signature_mismatches", feature_mismatch_rows, args.max_feature_signature_mismatches),
        ("parameter_mismatch_rows", parameter_mismatch_rows, args.max_parameter_mismatches),
    ]
    failed = False
    for label, actual, maximum in gates:
        if maximum is None:
            continue
        passed = actual <= maximum
        print(f"EXPECT {label}: {actual} <= {maximum} {'PASS' if passed else 'FAIL'}")
        failed = failed or not passed
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
