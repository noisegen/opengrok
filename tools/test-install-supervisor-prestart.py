#!/usr/bin/env python3
"""Tests for install-supervisor-prestart — launchHost wire, fail-closed, honesty."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Minimal fixture matching the verified live launchHost spawn shape.
STOCK_SUPERVISOR = r"""
const { spawn } = require("child_process");
const path = require("path");
const HOST_DIR = process.env.SAND_HOST || "/home/box/sand-host";
const HOST_ENTRY = path.join(HOST_DIR, "host-main.cjs");
const AGENT_DATA_ROOT = process.env.SAND_DATA_ROOT || "/home/box/sand-data";

function launchHost() {
  console.log("[sand-supervisor] launching", HOST_ENTRY);
  const child = spawn(process.execPath, [HOST_ENTRY], {
    cwd: HOST_DIR,
    env: {
      ...process.env,
      SAND_PACKAGED: "1",
      SAND_DATA_ROOT: AGENT_DATA_ROOT,
      SAND_HOST_IN_BOX: "1",
    },
  });
  return child;
}

module.exports = { launchHost, HOST_ENTRY, HOST_DIR };
"""

DRIFTED_SUPERVISOR = r"""
const { spawn } = require("child_process");
function launchHost() {
  // Different shape — must refuse rather than half-patch.
  spawn("node", ["/home/box/sand-host/host-main.cjs"], { cwd: "/home/box/sand-host" });
}
module.exports = { launchHost };
"""


def load_mod():
    path = HERE / "install-supervisor-prestart.py"
    spec = importlib.util.spec_from_file_location("isp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InstallSupervisorPrestartTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_mod()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.sand = self.root / "sand-data"
        self.sand.mkdir()
        self.sup = self.root / "sand-supervisor.mjs"
        self.sup.write_text(STOCK_SUPERVISOR, encoding="utf-8")
        # Seed durable ensure so sync has a source.
        for name in (
            "install-supervisor-prestart.py",
            "ensure-brain-overlay.py",
            "host-prestart-ensure.sh",
            "patch-brain-hook.py",
            "brain-router.cjs",
        ):
            src = HERE / name
            if src.is_file():
                # Will be copied by install; nothing required here.
                pass

    def tearDown(self):
        self.td.cleanup()

    def _run(self, **kwargs):
        buf = StringIO()
        err = StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            status = self.mod.install(
                str(self.sup),
                str(self.sand),
                str(HERE),
                **kwargs,
            )
        return status, buf.getvalue(), err.getvalue()

    def test_stock_launchHost_shape_found(self):
        text = self.sup.read_text(encoding="utf-8")
        self.assertIsNotNone(self.mod.find_launch_spawn(text))
        self.assertIn("spawn(process.execPath, [HOST_ENTRY],", text.replace("\n", " ").replace("  ", " ") or text)
        # Exact multiline presence:
        self.assertRegex(
            text,
            r"spawn\s*\(\s*process\.execPath\s*,\s*\[\s*HOST_ENTRY\s*\]\s*,",
        )

    def test_applies_prestart_before_spawn(self):
        status, out, _ = self._run()
        self.assertEqual(status, "applied", out)
        body = self.sup.read_text(encoding="utf-8")
        self.assertIn("/* sand-brain supervisor-prestart */", body)
        self.assertIn("ensure-brain-overlay.py", body)
        self.assertIn("continuing stock spawn", body)
        # Prestart appears BEFORE the spawn call.
        self.assertLess(
            body.index("/* sand-brain supervisor-prestart */"),
            body.index("spawn(process.execPath, [HOST_ENTRY]"),
        )
        r = subprocess.run(
            ["node", "--check", str(self.sup)], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # Durable copy of installer under sand-data.
        self.assertTrue((self.sand / "install-supervisor-prestart.py").is_file())

    def test_idempotent_noop(self):
        self._run()
        before = self.sup.read_text(encoding="utf-8")
        status, out, _ = self._run()
        self.assertEqual(status, "noop", out)
        self.assertEqual(before, self.sup.read_text(encoding="utf-8"))

    def test_dry_run_does_not_write(self):
        before = self.sup.read_text(encoding="utf-8")
        status, out, _ = self._run(dry_run=True)
        self.assertEqual(status, "dry-run")
        self.assertEqual(before, self.sup.read_text(encoding="utf-8"))
        self.assertIn("FOUND", out)

    def test_drift_refuses(self):
        self.sup.write_text(DRIFTED_SUPERVISOR, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertEqual(cm.exception.code, 1)
        # Unchanged.
        self.assertEqual(self.sup.read_text(encoding="utf-8"), DRIFTED_SUPERVISOR)

    def test_ensure_failure_still_spawns_in_generated_code(self):
        """Fail-closed contract: catch continues to spawn — verified structurally."""
        self._run()
        body = self.sup.read_text(encoding="utf-8")
        # catch logs then falls through; spawn remains after the try/catch.
        catch_idx = body.index("continuing stock spawn")
        spawn_idx = body.index("spawn(process.execPath, [HOST_ENTRY]")
        self.assertLess(catch_idx, spawn_idx)

    def test_messaging_forbids_bounce_and_states_update_computer_limit(self):
        status, out, err = self._run()
        self.assertEqual(status, "applied", out)
        blob = out + err
        self.assertIn("Update Computer", blob)
        self.assertIn("AUTO-restore", blob)
        self.assertIn("Desktop Quit", blob)
        self.assertRegex(blob, r"(?i)do not.*(forceNow|restart-host|Update Computer)")


if __name__ == "__main__":
    unittest.main()
