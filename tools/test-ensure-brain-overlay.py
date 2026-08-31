#!/usr/bin/env python3
"""Tests for ensure-brain-overlay — stock recover → re-apply, noop, drift, fail-closed."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE


def load_ensure():
    path = HERE / "ensure-brain-overlay.py"
    spec = importlib.util.spec_from_file_location("ensure_brain", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STOCK_HOOK = (
    'const inferenceProvider=(process.env.SAND_INFERENCE_PROVIDER||"xai").toLowerCase();\n'
    'if (inferenceProvider !== "cursor") {\n'
    " try {\n"
    '  const { createXaiPromptSession } = require("./xai-prompt-session.cjs");\n'
    "  return createXaiPromptSession({ requestedModel, onRequestId, sessionOptions });\n"
    " } catch (xaiErr) {\n"
    '  console.error("[sand-xai] failed to create xAI session, falling back to Cursor:", xaiErr);\n'
    " }\n"
    "}\n"
    "const session = createCursorInferencePromptSession({\n"
)

# Recovered production host (version 112ba04): Cursor-native only — no xAI branch.
CURSOR_NATIVE_HOOK = """
function resolveSandRequestedModel(opts) { return (opts && opts.modelId) || "grok-4.6"; }
function createCursorInferencePromptSession(opts) { return { kind: "cursor", opts: opts }; }
function createSession(options2, sessionOptions, onRequestId) {
      const requestedModel = resolveSandRequestedModel({
        modelId: "grok-4.6",
        sessionOptions
      });
      const session = createCursorInferencePromptSession({
        getAccessToken: options2.getAccessToken,
        getTeamId: options2.getTeamId,
        getMachineId: options2.getMachineId,
        requestedModel,
        inferenceReason: options2.isGeminiVideoDeveloperApiEnabled?.() === true ? sessionOptions?.inferenceReason : void 0,
        onRequestId,
        ...sessionOptions?.lineage != null ? { lineage: sessionOptions.lineage } : {}
      });
      return session;
}
module.exports = { createSession };
"""


def write_stock_host(host_dir: Path) -> Path:
    host_dir.mkdir(parents=True, exist_ok=True)
    host = host_dir / "host-main.cjs"
    host.write_text(
        "// stock host after recover\n" + STOCK_HOOK + "  requestedModel\n});\n",
        encoding="utf-8",
    )
    (host_dir / "xai-prompt-session.cjs").write_text(
        "module.exports={createXaiPromptSession(){return{};}};\n",
        encoding="utf-8",
    )
    return host


def write_cursor_native_host(host_dir: Path) -> Path:
    host_dir.mkdir(parents=True, exist_ok=True)
    host = host_dir / "host-main.cjs"
    host.write_text(
        "// recovered Cursor-native host (xAI branch absent)\n"
        + CURSOR_NATIVE_HOOK,
        encoding="utf-8",
    )
    # Deliberately no xai-prompt-session.cjs — matches live recover.
    return host


def load_patch():
    path = HERE / "patch-brain-hook.py"
    spec = importlib.util.spec_from_file_location("pbh_cursor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EnsureBrainOverlayTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_ensure()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.host_dir = self.root / "sand-host"
        self.sand = self.root / "sand-data"
        self.sand.mkdir()
        write_stock_host(self.host_dir)
        # Durable bindings survive recover; host does not.
        (self.sand / "brain-bindings.json").write_text(
            json.dumps(
                {
                    "default": "grok",
                    "agents": {
                        "aaaaaaaa-0000-4000-8000-000000000001": {
                            "brain": "deepseek",
                            "name": "Hopped",
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.td.cleanup()

    def _ensure(self, **kwargs):
        buf = StringIO()
        err = StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            status = self.mod.ensure(
                str(self.host_dir),
                str(self.sand),
                str(TOOLS),
                **kwargs,
            )
        return status, buf.getvalue(), err.getvalue()

    def test_stock_host_applies(self):
        status, out, _ = self._ensure()
        self.assertEqual(status, "applied")
        host = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        self.assertIn("createLazyBrainSession", host)
        self.assertIn("overlay failed, native", host)
        self.assertIn("sand-brain pass-through", host)
        self.assertTrue((self.host_dir / "brain-router.cjs").is_file())
        self.assertTrue((self.sand / "brain-router.cjs").is_file())
        self.assertIn("no changes needed", self._ensure()[1])

    def test_already_patched_is_noop(self):
        self._ensure()
        status, out, _ = self._ensure()
        self.assertEqual(status, "noop")
        self.assertIn("no changes needed", out)

    def test_host_rewrite_missing_router_reapplies(self):
        self._ensure()
        # Simulate recover: stock host again, bindings + durable router remain.
        write_stock_host(self.host_dir)
        router = self.host_dir / "brain-router.cjs"
        if router.exists():
            router.unlink()
        self.assertFalse(router.exists())
        status, out, _ = self._ensure()
        self.assertEqual(status, "applied")
        self.assertTrue(router.is_file())
        host = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        self.assertIn("createLazyBrainSession", host)

    def test_half_patch_missing_router_healed(self):
        self._ensure()
        # Hook still present, router wiped (classic desync that bricks require()).
        (self.host_dir / "brain-router.cjs").unlink()
        status, out, _ = self._ensure()
        self.assertIn(status, ("applied", "noop"))
        self.assertTrue((self.host_dir / "brain-router.cjs").is_file())

    def test_upstream_drift_loud_fail_restores(self):
        # Host without any createXaiPromptSession anchor.
        host = self.host_dir / "host-main.cjs"
        original = "// totally different upstream\nmodule.exports = {};\n"
        host.write_text(original, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self._ensure()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(host.read_text(encoding="utf-8"), original)

    def test_dry_run_does_not_write(self):
        before = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        status, out, _ = self._ensure(dry_run=True)
        self.assertEqual(status, "dry-run")
        self.assertEqual(
            (self.host_dir / "host-main.cjs").read_text(encoding="utf-8"), before
        )
        self.assertFalse((self.host_dir / "brain-router.cjs").exists())
        self.assertIn("would copy", out)


class ApplyBoxPatchTests(unittest.TestCase):
    """apply-box-patch: stock → apply, re-run noop, drift loud, restore on fail."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.host = self.root / "host-main.cjs"
        self.hop = self.root / "openai-hop-session.cjs"
        self.bindings = self.root / "model-bindings.json"
        self.maps = TOOLS / "provider-maps.cjs"
        # Minimal anchors matching apply-box-patch.py expectations.
        self.host.write_text(
            "function resolve() {\n"
            "        let resolvedTopLevelModelId = host.subagentModelId;\n"
            "        let resolvedOpenaiBaseUrl = void 0;\n"
            "        resolvedTopLevelModelId = __entry.modelId;\n"
            "        const sessionOptions = {\n"
            "          ...resolvedOpenaiBaseUrl != null ? { openaiBaseUrl: resolvedOpenaiBaseUrl, provenanceAgentId: host.getConversationId(), skipLabeling: true } : {},\n"
            "          isSummarizationSession: false,\n"
            "        };\n"
            "        const sum = {\n"
            "          ...resolvedOpenaiBaseUrl != null ? { openaiBaseUrl: resolvedOpenaiBaseUrl, provenanceAgentId: host.getConversationId(), skipLabeling: true } : {},\n"
            "          isSummarizationSession: true,\n"
            "        };\n"
            "        createOpenAiHopSession({\n"
            '          requestKind: sessionOptions.isSummarizationSession ? "summarization" : "main"\n'
            "          });\n"
            "}\n",
            encoding="utf-8",
        )
        self.hop.write_text(
            'const fs = require("fs");\n'
            "function createOpenAiHopSession(opts) {\n"
            "  const requestKind = opts && opts.requestKind;\n"
            "  return { getExecutor(selfOpts) { return new Executor(selfOpts); } };\n"
            "}\n"
            "function Executor(opts) {\n"
            "  this.baseUrl = (opts && opts.baseUrl) || '';\n"
            "  this.allowTestVisibleRecovery = opts.allowTestVisibleRecovery === true;\n"
            "  this.stream = function (body, modelId, localQwen) {\n"
            "      const self = this;\n"
            "      const url = completionsUrl(self.baseUrl);\n"
            "      return url;\n"
            "  };\n"
            "}\n"
            "function completionsUrl(u) { return u + '/chat/completions'; }\n"
            "module.exports = { createOpenAiHopSession };\n",
            encoding="utf-8",
        )
        self.bindings.write_text('{"agents":{}}\n', encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def _run(self, extra=None):
        cmd = [
            sys.executable,
            str(TOOLS / "apply-box-patch.py"),
            "--host",
            str(self.host),
            "--hop",
            str(self.hop),
            "--bindings",
            str(self.bindings),
            "--maps",
            str(self.maps),
        ]
        if extra:
            cmd.extend(extra)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_stock_applies_then_noop(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("patched", r.stdout)
        ht = self.host.read_text(encoding="utf-8")
        self.assertIn("resolvedTopLevelMaxMode", ht)
        self.assertIn("maxMode: sessionOptions.maxMode === true", ht)
        hp = self.hop.read_text(encoding="utf-8")
        self.assertIn("applyProviderReasoningControls", hp)
        r2 = self._run()
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        self.assertIn("no changes needed", r2.stdout)

    def test_drift_fails_loudly(self):
        self.host.write_text("// no anchors\nmodule.exports={};\n", encoding="utf-8")
        r = self._run()
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(
            "anchor" in (r.stderr + r.stdout).lower()
            or "expected" in (r.stderr + r.stdout).lower()
            or "count=" in (r.stderr + r.stdout)
        )


class DoctorBrainOverlayTests(unittest.TestCase):
    def test_desync_detected(self):
        # Import doctor helpers against a temp sand-host via env.
        td = tempfile.TemporaryDirectory()
        try:
            root = Path(td.name)
            host_dir = root / "sand-host"
            sand = root / "sand-data"
            sand.mkdir()
            write_stock_host(host_dir)
            (sand / "brain-bindings.json").write_text(
                json.dumps(
                    {
                        "agents": {
                            "bbbbbbbb-0000-4000-8000-000000000002": {
                                "brain": "deepseek",
                                "name": "X",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["SAND_HOST"] = str(host_dir)
            env["SAND_DATA"] = str(sand)
            env["BRAIN_BINDINGS"] = str(sand / "brain-bindings.json")
            # Run only the overlay check via a tiny driver.
            driver = root / "drive_doctor_brain.py"
            driver.write_text(
                "import os, sys\n"
                f"sys.path.insert(0, {str(TOOLS)!r})\n"
                "import doctor\n"
                "from pathlib import Path\n"
                "doctor.results.clear()\n"
                "doctor.HOST_DIR = Path(os.environ['SAND_HOST'])\n"
                "doctor.SAND_DATA = Path(os.environ['SAND_DATA'])\n"
                "doctor.BRAIN_BINDINGS = Path(os.environ['BRAIN_BINDINGS'])\n"
                "doctor.check_brain_overlay()\n"
                "fails=[r for r in doctor.results if r[0]=='FAIL']\n"
                "print('FAILS', len(fails))\n"
                "for r in fails: print(r[1], r[2][:60])\n"
                "sys.exit(2 if fails else 0)\n",
                encoding="utf-8",
            )
            r = subprocess.run(
                [sys.executable, str(driver)], capture_output=True, text=True, env=env
            )
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("brain:desync", r.stdout)
        finally:
            td.cleanup()

    def test_desync_detected_on_cursor_native_stock(self):
        td = tempfile.TemporaryDirectory()
        try:
            root = Path(td.name)
            host_dir = root / "sand-host"
            sand = root / "sand-data"
            sand.mkdir()
            write_cursor_native_host(host_dir)
            (sand / "brain-bindings.json").write_text(
                json.dumps(
                    {
                        "agents": {
                            "cccccccc-0000-4000-8000-000000000003": {
                                "brain": "deepseek",
                                "name": "Long Run",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["SAND_HOST"] = str(host_dir)
            env["SAND_DATA"] = str(sand)
            env["BRAIN_BINDINGS"] = str(sand / "brain-bindings.json")
            driver = root / "drive_doctor_brain.py"
            driver.write_text(
                "import os, sys\n"
                f"sys.path.insert(0, {str(TOOLS)!r})\n"
                "import doctor\n"
                "from pathlib import Path\n"
                "doctor.results.clear()\n"
                "doctor.HOST_DIR = Path(os.environ['SAND_HOST'])\n"
                "doctor.SAND_DATA = Path(os.environ['SAND_DATA'])\n"
                "doctor.BRAIN_BINDINGS = Path(os.environ['BRAIN_BINDINGS'])\n"
                "doctor.check_brain_overlay()\n"
                "fails=[r for r in doctor.results if r[0]=='FAIL']\n"
                "print('FAILS', len(fails))\n"
                "for r in fails: print(r[1])\n"
                "sys.exit(2 if fails else 0)\n",
                encoding="utf-8",
            )
            r = subprocess.run(
                [sys.executable, str(driver)], capture_output=True, text=True, env=env
            )
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("brain:desync", r.stdout)
        finally:
            td.cleanup()


class CursorNativePatchTests(unittest.TestCase):
    """Recovered Cursor-only host (no createXaiPromptSession) — production shape."""

    def setUp(self):
        self.mod = load_ensure()
        self.pbh = load_patch()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.host_dir = self.root / "sand-host"
        self.sand = self.root / "sand-data"
        self.sand.mkdir()
        write_cursor_native_host(self.host_dir)
        (self.sand / "brain-bindings.json").write_text(
            json.dumps(
                {
                    "default": "grok",
                    "agents": {
                        "71b408bd-0c94-494b-8a45-754bc0ef2d73": {
                            "brain": "deepseek",
                            "name": "Long Run",
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.td.cleanup()

    def _ensure(self, **kwargs):
        buf = StringIO()
        err = StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            status = self.mod.ensure(
                str(self.host_dir),
                str(self.sand),
                str(TOOLS),
                **kwargs,
            )
        return status, buf.getvalue(), err.getvalue()

    def test_detect_shape_cursor_native(self):
        text = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        self.assertEqual(self.pbh.detect_shape(text), "cursor-native")
        self.assertNotIn("createXaiPromptSession", text)
        self.assertNotIn('inferenceProvider !== "cursor"', text)
        self.assertFalse((self.host_dir / "xai-prompt-session.cjs").exists())

    def test_stock_cursor_native_applies(self):
        status, out, _ = self._ensure()
        self.assertEqual(status, "applied", out)
        host = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        self.assertIn("sand-brain pass-through", host)
        self.assertIn("createLazyBrainSession", host)
        self.assertIn("overlay failed, native", host)
        self.assertIn("nativeFactory", host)
        self.assertIn("getAccessToken: options2.getAccessToken", host)
        self.assertIn("pickSandBrainIds", host)
        self.assertIn("options2:", host)
        # Fail-closed stock path preserved inside catch.
        self.assertIn("createCursorInferencePromptSession", host)
        self.assertNotIn("createXaiPromptSession", host)
        self.assertNotIn('inferenceProvider !== "cursor"', host)
        self.assertTrue((self.host_dir / "brain-router.cjs").is_file())
        # Syntax-valid.
        r = subprocess.run(
            ["node", "--check", str(self.host_dir / "host-main.cjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # Patch itself is idempotent on the bytes.
        self.assertEqual(host, self.pbh.patch(host))

    def test_cursor_native_idempotent_noop(self):
        self._ensure()
        status, out, _ = self._ensure()
        self.assertEqual(status, "noop")
        self.assertIn("no changes needed", out)

    def test_cursor_native_rewrite_reapplies(self):
        self._ensure()
        write_cursor_native_host(self.host_dir)
        router = self.host_dir / "brain-router.cjs"
        if router.exists():
            router.unlink()
        status, out, _ = self._ensure()
        self.assertEqual(status, "applied", out)
        host = (self.host_dir / "host-main.cjs").read_text(encoding="utf-8")
        self.assertIn("createLazyBrainSession", host)
        self.assertTrue(router.is_file())

    def test_cursor_native_dry_run_reports_shape(self):
        status, out, _ = self._ensure(dry_run=True)
        self.assertEqual(status, "dry-run")
        self.assertIn("cursor-native", out)
        self.assertFalse((self.host_dir / "brain-router.cjs").exists())

    def test_cursor_native_upgrades_stale_overlay_without_id_bag(self):
        # LIVE recovered host shape: createCursorSandInference returns an object
        # with createSession (stale overlay) + sibling shorthand methods.
        # The only const session = createCursorInferencePromptSession is INSIDE
        # the catch. Upgrading must replace the whole try/catch — not leave an
        # extra } before recordPostTurnLabeling (that caused Unexpected '{').
        stale = r"""
function createCursorInferencePromptSession(opts) { return { kind: "cursor", opts }; }
function recordSandPostTurnLabeling() {}
function getLabelingClient() { return {}; }
function createCursorSandInference(options2) {
  return {
    createSession(onRequestId, sessionOptions) {
      const requestedModel = "grok-4.6";
      try {
       /* sand-brain pass-through */
       const { createLazyBrainSession } = require("./brain-router.cjs");
       let cidKey;
       let getCid;
       try { cidKey = conversationIdKey; } catch (e) {}
       try { getCid = getConversationId; } catch (e) {}
       return createLazyBrainSession({
        requestedModel,
        onRequestId,
        sessionOptions,
        conversationIdKey: cidKey,
        getConversationId: getCid,
        nativeFactory: function (so, rid) {
         const cb = typeof rid === "function" ? rid : onRequestId;
         const sessionOptions = so;
         return createCursorInferencePromptSession({
          getAccessToken: options2.getAccessToken,
          getTeamId: options2.getTeamId,
          getMachineId: options2.getMachineId,
          requestedModel,
          onRequestId: cb,
          ...sessionOptions?.lineage != null ? { lineage: sessionOptions.lineage } : {}
         });
        }
       });
      } catch (sandErr) {
       console.error("[sand-brain] overlay failed, native:", sandErr);
       const session = createCursorInferencePromptSession({
        getAccessToken: options2.getAccessToken,
        getTeamId: options2.getTeamId,
        getMachineId: options2.getMachineId,
        requestedModel,
        onRequestId,
        ...sessionOptions?.lineage != null ? { lineage: sessionOptions.lineage } : {}
       });
       return session;
      }
    },
    recordPostTurnLabeling(args) {
      recordSandPostTurnLabeling(getLabelingClient(), args);
    },
    recordFollowupLabeling(args) {
      recordSandPostTurnLabeling(getLabelingClient(), args);
    }
  };
}
module.exports = { createCursorSandInference };
"""
        out = self.pbh.patch(stale)
        self.assertIn("pickSandBrainIds", out)
        self.assertIn("options2:", out)
        # No leftover brace between catch-end and the sibling method.
        self.assertRegex(
            out,
            r"return session;\s*\n\s*\}\s*\n\s*\},\s*\n\s*recordPostTurnLabeling\(args\)\s*\{",
        )
        self.assertNotRegex(
            out,
            r"return session;\s*\n\s*\}\s*\n\s*\}\s*\n\s*\},\s*\n\s*recordPostTurnLabeling",
        )
        # Must be valid JS (the live failure mode).
        with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False) as tf:
            tf.write(out)
            tf_path = tf.name
        try:
            r = subprocess.run(
                ["node", "--check", tf_path], capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(tf_path)
        self.assertEqual(out, self.pbh.patch(out))

    def test_patch_prefers_xai_when_both_present(self):
        # xAI shape also contains a createCursorInferencePromptSession call;
        # detect_shape must prefer xai.
        text = (
            'if (inferenceProvider !== "cursor") {\n'
            " try {\n"
            '  const { createXaiPromptSession } = require("./xai-prompt-session.cjs");\n'
            "  return createXaiPromptSession({ requestedModel, onRequestId, sessionOptions });\n"
            " } catch (e) {}\n"
            "}\n"
            "const session = createCursorInferencePromptSession({\n"
            "  getAccessToken: options2.getAccessToken,\n"
            "  requestedModel,\n"
            "  onRequestId,\n"
            "});\n"
            "return session;\n"
        )
        self.assertEqual(self.pbh.detect_shape(text), "xai")
        out = self.pbh.patch(text)
        self.assertIn("createXaiPromptSession", out)
        self.assertIn("createLazyBrainSession", out)
        self.assertEqual(out, self.pbh.patch(out))


if __name__ == "__main__":
    unittest.main()
