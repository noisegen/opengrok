#!/usr/bin/env python3
"""Tests for install-supervisor-prestart — ESM launchHost wire, fail-closed."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent

# LIVE sand-supervisor.mjs shape (verified): ESM imports + launchHost spawn.
# HOST_ENTRY / HOST_DIR / AGENT_DATA_ROOT are module-level (as on the box).
STOCK_SUPERVISOR_ESM = r"""
import { execFileSync, spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const HOST_DIR = process.env.SAND_HOST || "/home/box/sand-host";
const HOST_ENTRY = join(HOST_DIR, "host-main.cjs");
const AGENT_DATA_ROOT = process.env.SAND_DATA_ROOT || "/home/box/sand-data";

export function launchHost() {
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
"""

# Same imports/shape, but HOST_ENTRY points at a tiny marker script for runtime.
def make_harness_supervisor(fake_host: Path, sand_root: Path) -> str:
    return f"""
import {{ execFileSync, spawn }} from "node:child_process";
import {{ existsSync, writeFileSync }} from "node:fs";
import {{ join }} from "node:path";

const HOST_DIR = {str(fake_host.parent)!r};
const HOST_ENTRY = {str(fake_host)!r};
const AGENT_DATA_ROOT = {str(sand_root)!r};

export function launchHost() {{
  const child = spawn(process.execPath, [HOST_ENTRY], {{
    cwd: HOST_DIR,
    env: {{
      ...process.env,
      SAND_PACKAGED: "1",
      SAND_DATA_ROOT: AGENT_DATA_ROOT,
      SAND_HOST_IN_BOX: "1",
    }},
    stdio: "ignore",
  }});
  return child;
}}
"""

DRIFTED_SUPERVISOR = r"""
import { spawn } from "node:child_process";
export function launchHost() {
  // Different shape — must refuse rather than half-patch.
  spawn("node", ["/home/box/sand-host/host-main.cjs"], { cwd: "/home/box/sand-host" });
}
"""

CJS_LOOKALIKE = r"""
const { spawn } = require("child_process");
const path = require("path");
const HOST_DIR = "/home/box/sand-host";
const HOST_ENTRY = path.join(HOST_DIR, "host-main.cjs");
const AGENT_DATA_ROOT = "/home/box/sand-data";
function launchHost() {
  const child = spawn(process.execPath, [HOST_ENTRY], {
    cwd: HOST_DIR,
    env: { ...process.env, SAND_PACKAGED: "1", SAND_DATA_ROOT: AGENT_DATA_ROOT, SAND_HOST_IN_BOX: "1" },
  });
  return child;
}
module.exports = { launchHost };
"""

STALE_REQUIRE_PRESTART = r"""
import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const HOST_DIR = process.env.SAND_HOST || "/home/box/sand-host";
const HOST_ENTRY = join(HOST_DIR, "host-main.cjs");
const AGENT_DATA_ROOT = process.env.SAND_DATA_ROOT || "/home/box/sand-data";

export function launchHost() {
  /* sand-brain supervisor-prestart */
  try {
    const { execFileSync } = require("child_process");
    const fs = require("fs");
    const path = require("path");
    const ensurePy = path.join(AGENT_DATA_ROOT, "ensure-brain-overlay.py");
    if (fs.existsSync(ensurePy)) {
      execFileSync("python3", [ensurePy], { timeout: 1000 });
    }
  } catch (sandPreErr) {
    console.error("[sand-brain] supervisor prestart failed (continuing stock spawn):", sandPreErr);
  }
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
        self.sup.write_text(STOCK_SUPERVISOR_ESM, encoding="utf-8")

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

    def _prestart_block(self, body: str) -> str:
        marker = body.index("/* sand-brain supervisor-prestart */")
        spawn_at = body.index("spawn(process.execPath, [HOST_ENTRY]")
        return body[marker:spawn_at]

    def test_stock_launchHost_shape_found(self):
        text = self.sup.read_text(encoding="utf-8")
        self.assertIsNotNone(self.mod.find_launch_spawn(text))
        self.assertRegex(
            text,
            r"spawn\s*\(\s*process\.execPath\s*,\s*\[\s*HOST_ENTRY\s*\]\s*,",
        )
        self.assertIn('from "node:child_process"', text)
        self.assertIn('from "node:fs"', text)
        self.assertIn('from "node:path"', text)

    def test_applies_esm_prestart_without_require(self):
        status, out, _ = self._run()
        self.assertEqual(status, "applied", out)
        body = self.sup.read_text(encoding="utf-8")
        block = self._prestart_block(body)
        self.assertIn("/* sand-brain supervisor-prestart */", block)
        self.assertIn("ensure-brain-overlay.py", block)
        self.assertIn("continuing stock spawn", block)
        self.assertIn("existsSync(ensurePy)", block)
        self.assertIn("execFileSync(", block)
        self.assertIn("join(sandRoot", block)
        self.assertIn("hop-fail-logs", block)
        self.assertIn("sand-brain.log", block)
        self.assertNotIn("require(", block)
        self.assertNotIn("createRequire", block)
        self.assertNotRegex(block, r"\bconst\s*\{\s*execFileSync\s*\}")
        self.assertLess(
            body.index("/* sand-brain supervisor-prestart */"),
            body.index("spawn(process.execPath, [HOST_ENTRY]"),
        )
        r = subprocess.run(
            ["node", "--check", str(self.sup)], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.sand / "install-supervisor-prestart.py").is_file())

    def test_runtime_missing_ensure_appends_durable_hop_fail_log(self):
        """Missing ensure.py → console + durable sand-data/hop-fail-logs log."""
        fake_host = self.root / "fake-host2.mjs"
        marker = self.root / "spawned2.flag"
        fake_host.write_text(
            "import { writeFileSync } from 'node:fs';\n"
            f"writeFileSync({str(marker)!r}, 'ok');\n",
            encoding="utf-8",
        )
        empty_sand = self.root / "empty-sand2"
        empty_sand.mkdir()
        self.sup.write_text(
            make_harness_supervisor(fake_host, empty_sand), encoding="utf-8"
        )
        status, out, _ = self._run()
        self.assertEqual(status, "applied", out)
        runner = self.root / "run-launch2.mjs"
        runner.write_text(
            f"import {{ launchHost }} from {self.sup.as_uri()!r};\n"
            "const child = launchHost();\n"
            "await new Promise((r) => child.once('exit', r));\n"
            "if (child.exitCode !== 0) process.exit(child.exitCode || 1);\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SAND_DATA_ROOT"] = str(empty_sand)
        env.pop("BRAIN_LOG", None)
        proc = subprocess.run(
            ["node", str(runner)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(marker.is_file())
        durable = empty_sand / "hop-fail-logs" / "sand-brain.log"
        self.assertTrue(durable.is_file(), "expected durable hop-fail log")
        self.assertIn("missing", durable.read_text(encoding="utf-8"))
        self.assertIn("missing", proc.stderr + proc.stdout)

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
        self.assertIn("require(", out)  # messaging about must NOT contain

    def test_drift_refuses(self):
        self.sup.write_text(DRIFTED_SUPERVISOR, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.sup.read_text(encoding="utf-8"), DRIFTED_SUPERVISOR)

    def test_cjs_lookalike_refuses(self):
        self.sup.write_text(CJS_LOOKALIKE, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.sup.read_text(encoding="utf-8"), CJS_LOOKALIKE)

    def test_upgrades_stale_require_prestart_to_esm(self):
        self.sup.write_text(STALE_REQUIRE_PRESTART, encoding="utf-8")
        status, out, _ = self._run()
        self.assertEqual(status, "applied", out)
        body = self.sup.read_text(encoding="utf-8")
        block = self._prestart_block(body)
        self.assertNotIn("require(", block)
        self.assertEqual(body.count("/* sand-brain supervisor-prestart */"), 1)
        r = subprocess.run(
            ["node", "--check", str(self.sup)], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ensure_failure_still_spawns_in_generated_code(self):
        self._run()
        body = self.sup.read_text(encoding="utf-8")
        catch_idx = body.index("continuing stock spawn")
        spawn_idx = body.index("spawn(process.execPath, [HOST_ENTRY]")
        self.assertLess(catch_idx, spawn_idx)

    def test_runtime_missing_ensure_still_reaches_spawn(self):
        """ESM harness: missing ensure.py → prestart logs, spawn still runs."""
        fake_host = self.root / "fake-host.mjs"
        marker = self.root / "spawned.flag"
        fake_host.write_text(
            "import { writeFileSync } from 'node:fs';\n"
            f"writeFileSync({str(marker)!r}, 'ok');\n",
            encoding="utf-8",
        )
        # Empty sand-data: no ensure-brain-overlay.py → prestart "missing" path.
        empty_sand = self.root / "empty-sand"
        empty_sand.mkdir()
        self.sup.write_text(
            make_harness_supervisor(fake_host, empty_sand), encoding="utf-8"
        )
        status, out, _ = self._run()
        self.assertEqual(status, "applied", out)
        body = self.sup.read_text(encoding="utf-8")
        self.assertNotIn("require(", self._prestart_block(body))
        r = subprocess.run(
            ["node", "--check", str(self.sup)], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)

        runner = self.root / "run-launch.mjs"
        runner.write_text(
            f"import {{ launchHost }} from {self.sup.as_uri()!r};\n"
            "const child = launchHost();\n"
            "await new Promise((r) => child.once('exit', r));\n"
            "if (child.exitCode !== 0) process.exit(child.exitCode || 1);\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SAND_DATA_ROOT"] = str(empty_sand)
        proc = subprocess.run(
            ["node", str(runner)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(marker.is_file(), "spawn did not reach fake host")
        self.assertIn("missing", proc.stderr + proc.stdout)

    def test_messaging_forbids_bounce_and_states_update_computer_limit(self):
        status, out, err = self._run()
        self.assertEqual(status, "applied", out)
        blob = out + err
        self.assertIn("Update Computer", blob)
        self.assertIn("AUTO-restore", blob)
        self.assertIn("Desktop Quit", blob)
        self.assertIn("ESM", blob)
        self.assertRegex(blob, r"(?i)do not.*(forceNow|restart-host|Update Computer)")

    def test_backup_lands_under_sand_not_sibling_when_dir_readonly(self):
        """Simulate /usr/local/bin: dir not writable → bak cannot be a sibling."""
        bin_dir = self.root / "usr-local-bin"
        bin_dir.mkdir()
        sup = bin_dir / "sand-supervisor.mjs"
        sup.write_text(STOCK_SUPERVISOR_ESM, encoding="utf-8")
        os.chmod(sup, 0o644)
        # Directory not writable: cannot create sibling .bak (live /usr/local/bin).
        os.chmod(bin_dir, 0o555)
        try:
            sibling_probe = bin_dir / "probe-sibling.bak"
            with self.assertRaises(OSError):
                sibling_probe.write_text("x", encoding="utf-8")

            buf = StringIO()
            err = StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                status = self.mod.install(
                    str(sup),
                    str(self.sand),
                    str(HERE),
                    allow_elevate=False,
                )
            self.assertEqual(status, "applied", buf.getvalue() + err.getvalue())
            out = buf.getvalue()
            self.assertIn(str(self.sand), out)
            self.assertIn("brain-overlay-backups-", out)
            baks = list(self.sand.glob("brain-overlay-backups-*/sand-supervisor.mjs.bak"))
            self.assertTrue(baks, "expected backup under sand-data")
            self.assertTrue(baks[0].is_file())
            rolling = self.sand / "sand-supervisor.mjs.sand-brain-prestart.bak"
            self.assertTrue(rolling.is_file())
            siblings = list(bin_dir.glob("sand-supervisor.mjs*bak*"))
            self.assertEqual(siblings, [], siblings)
            body = sup.read_text(encoding="utf-8")
            self.assertIn("/* sand-brain supervisor-prestart */", body)
            self.assertNotIn("require(", self._prestart_block(body))
        finally:
            os.chmod(bin_dir, 0o755)

    def test_unwritable_supervisor_dies_with_permission_fact_without_sudo(self):
        os.chmod(self.sup, 0o444)
        try:
            buf = StringIO()
            err = StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    self.mod.install(
                        str(self.sup),
                        str(self.sand),
                        str(HERE),
                        allow_elevate=False,
                    )
            self.assertEqual(cm.exception.code, 1)
            blob = buf.getvalue() + err.getvalue()
            self.assertIn("not writable", blob)
            self.assertIn("HOME=/root", blob)
            self.assertIn("/usr/local/bin", blob)
            self.assertIn("sibling .bak", blob)
            self.assertNotIn(
                "/* sand-brain supervisor-prestart */", self.sup.read_text()
            )
        finally:
            os.chmod(self.sup, 0o644)

    def test_sudo_reexec_argv_uses_absolute_sand_tools(self):
        cmd = self.mod.sudo_reexec_argv(
            str(HERE / "install-supervisor-prestart.py"),
            "/usr/local/bin/sand-supervisor.mjs",
            "~/sand-data",
            "~/sand-data",
            dry_run=False,
        )
        self.assertEqual(cmd[0], "sudo")
        self.assertEqual(cmd[1], "-n")
        self.assertIn("--sand", cmd)
        sand_val = cmd[cmd.index("--sand") + 1]
        tools_val = cmd[cmd.index("--tools") + 1]
        self.assertTrue(os.path.isabs(sand_val), sand_val)
        self.assertTrue(os.path.isabs(tools_val), tools_val)
        self.assertNotIn("~", sand_val)
        self.assertFalse(sand_val.startswith("/root/"))


if __name__ == "__main__":
    unittest.main()
