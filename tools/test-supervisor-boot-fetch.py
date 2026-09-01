#!/usr/bin/env python3
"""Tests for supervisor-boot-fetch (shouldBootFetchHostBundle mirror)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_mod():
    path = HERE / "supervisor-boot-fetch.py"
    spec = importlib.util.spec_from_file_location("sbf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SupervisorBootFetchTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_mod()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_equal_versions_boot_fetch_armed(self):
        self.assertTrue(
            self.mod.should_boot_fetch_host_bundle("659557c", "659557c")
        )
        self.assertFalse(self.mod.boot_fetch_disarmed("659557c", "659557c"))

    def test_mismatch_disarmed_safe_window(self):
        """Live 2026-09-01: sand-host 659557c, image 1a2167a → no boot-fetch."""
        self.assertFalse(
            self.mod.should_boot_fetch_host_bundle("659557c", "1a2167a")
        )
        self.assertTrue(self.mod.boot_fetch_disarmed("659557c", "1a2167a"))

    def test_empty_inputs_conservative(self):
        self.assertFalse(self.mod.should_boot_fetch_host_bundle("", "1a2167a"))
        self.assertFalse(self.mod.should_boot_fetch_host_bundle("659557c", ""))
        self.assertFalse(self.mod.boot_fetch_disarmed("", ""))

    def test_read_version_file(self):
        vf = self.root / "version"
        vf.write_text("  abc123\n", encoding="utf-8")
        self.assertEqual(self.mod.read_version_file(str(vf)), "abc123")

    def test_describe_boot_fetch_mismatch(self):
        info = self.mod.describe_boot_fetch("659557c", "1a2167a")
        self.assertFalse(info["shouldBootFetchHostBundle"])
        self.assertTrue(info["bootFetchDisarmed"])
        self.assertIn("SAFE", info["note"])

    def test_cli_check_exits_zero_when_disarmed(self):
        script = HERE / "supervisor-boot-fetch.py"
        r = subprocess.run(
            [
                os.environ.get("PYTHON", "python3"),
                str(script),
                "--local",
                "659557c",
                "--image",
                "1a2167a",
                "--check",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("bootFetchDisarmed:         True", r.stdout)

    def test_cli_check_exits_one_when_armed(self):
        script = HERE / "supervisor-boot-fetch.py"
        r = subprocess.run(
            [
                os.environ.get("PYTHON", "python3"),
                str(script),
                "--local",
                "659557c",
                "--image",
                "659557c",
                "--check",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 1, r.stdout)


if __name__ == "__main__":
    unittest.main()
