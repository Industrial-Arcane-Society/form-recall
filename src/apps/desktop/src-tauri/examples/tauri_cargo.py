"""Helpers for compiling Tauri Rust CLI examples without staged app resources."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence


def cargo_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment that disables bundle resources for CLI builds.

    Tauri's build script validates configured bundle resources even when Cargo
    is only compiling a Rust example. Preserve any caller-supplied TAURI_CONFIG
    fields and replace only ``bundle.resources`` in the child process.
    """

    environment = dict(os.environ if source is None else source)
    raw_config = environment.get("TAURI_CONFIG")
    if raw_config:
        config = json.loads(raw_config)
        if not isinstance(config, dict):
            raise ValueError("TAURI_CONFIG must contain a JSON object")
    else:
        config = {}

    bundle = config.get("bundle", {})
    if not isinstance(bundle, dict):
        raise ValueError("TAURI_CONFIG.bundle must contain a JSON object")
    bundle["resources"] = []
    config["bundle"] = bundle
    environment["TAURI_CONFIG"] = json.dumps(config, separators=(",", ":"))
    return environment


def run_cargo(
    arguments: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    environment: Mapping[str, str] | None = None,
    executable: str = "cargo",
    **subprocess_options,
) -> subprocess.CompletedProcess:
    """Run a Cargo command with bundle resources disabled in its child env."""

    return subprocess.run(
        [executable, *arguments],
        cwd=cwd,
        env=cargo_environment(environment),
        **subprocess_options,
    )
