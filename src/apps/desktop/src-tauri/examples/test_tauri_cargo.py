"""Regression tests for subprocess configuration used by Tauri CLI builds."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from tauri_cargo import cargo_environment, run_cargo


class TauriCargoEnvironmentTests(unittest.TestCase):
    def test_override_changes_only_bundle_resources_and_not_source_environment(self):
        source = {
            "TAURI_CONFIG": json.dumps(
                {
                    "productName": "custom app",
                    "build": {"devUrl": "http://127.0.0.1:1420"},
                    "bundle": {"active": False, "resources": ["staged/app"]},
                }
            ),
            "KEEP_ME": "original",
        }

        child = cargo_environment(source)

        self.assertEqual(
            json.loads(child["TAURI_CONFIG"]),
            {
                "productName": "custom app",
                "build": {"devUrl": "http://127.0.0.1:1420"},
                "bundle": {"active": False, "resources": []},
            },
        )
        self.assertEqual(child["KEEP_ME"], "original")
        self.assertEqual(
            json.loads(source["TAURI_CONFIG"])["bundle"]["resources"],
            ["staged/app"],
        )
        self.assertEqual(source["KEEP_ME"], "original")

    def test_run_cargo_passes_override_to_child_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "cargo_probe.py"
            probe.write_text(
                "import json, os, sys\n"
                "print(json.dumps({'argv': sys.argv[1:], 'config': json.loads(os.environ['TAURI_CONFIG'])}))\n",
                encoding="utf-8",
            )
            source = {
                "TAURI_CONFIG": '{"bundle":{"active":true,"resources":["release-only"]},"custom":{"x":1}}',
                "SENTINEL": "preserved",
            }
            arguments = [
                str(probe),
                "build",
                "--offline",
                "--manifest-path",
                "Cargo.toml",
                "--example",
                "native_parts_catalog_cli",
            ]

            result = run_cargo(
                arguments,
                cwd=directory,
                environment=source,
                executable=sys.executable,
                check=True,
                capture_output=True,
                text=True,
            )

        observed = json.loads(result.stdout)
        self.assertEqual(observed["argv"], arguments[1:])
        self.assertEqual(
            observed["config"],
            {"bundle": {"active": True, "resources": []}, "custom": {"x": 1}},
        )
        self.assertEqual(
            source["TAURI_CONFIG"],
            '{"bundle":{"active":true,"resources":["release-only"]},"custom":{"x":1}}',
        )
        self.assertEqual(source["SENTINEL"], "preserved")

    def test_rejects_non_object_caller_override(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            cargo_environment({"TAURI_CONFIG": "[]"})

    def test_rejects_non_object_bundle_override(self):
        with self.assertRaisesRegex(ValueError, "TAURI_CONFIG.bundle"):
            cargo_environment({"TAURI_CONFIG": '{"bundle":[]}'})


if __name__ == "__main__":
    unittest.main()
