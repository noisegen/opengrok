#!/usr/bin/env python3
"""install-supervisor-prestart — wire ensure BEFORE sand-supervisor spawn.

LIVE FACTS (Grok Bot computer, verified):
  - Host is always-on cloud Linux under /usr/local/bin/sand-supervisor.mjs.
  - launchHost() hardcodes:
      spawn(process.execPath, [HOST_ENTRY], {
        cwd: HOST_DIR,
        env: { ...process.env, SAND_PACKAGED:1,
               SAND_DATA_ROOT: AGENT_DATA_ROOT, SAND_HOST_IN_BOX:1 }
      })
    HOST_ENTRY = /home/box/sand-host/host-main.cjs
  - No stock prestart, box-script, NODE_OPTIONS, or sand-data env hook.
  - Desktop "Quit Grok Bot" drops the client only — does NOT restart host-main.
  - host-prestart-ensure.sh alone is UNUSED until something calls it.
  - Update Computer resets supervisor from the image → this patch is wiped.

What this does:
  Insert a fail-closed ensure call immediately before that spawn, using the
  supervisor's existing ESM bindings (execFileSync, existsSync, join,
  HOST_DIR, AGENT_DATA_ROOT). Never require() / createRequire — live
  supervisor is ESM and require would ReferenceError inside the try/catch,
  silently no-oping the hop while stock spawn continues. If ensure fails,
  spawn still proceeds (stock Grok lives). Idempotent marker:
  /* sand-brain supervisor-prestart */

Boot-fetch (sand-host swap only, supervisor binary kept):
  Patched launchHost re-runs ensure before spawn → wrap can come back.

Update Computer (image recover):
  Supervisor is stock again. This hop CANNOT auto-restore the wrap until
  someone re-runs this installer from durable ~/sand-data AFTER recover.
  There is no durable automatic hook between image restore and first spawn
  with the current Cursor supervisor.

NEVER: forceNow upgrade, ./adapters restart-host, or Update Computer to apply.
Keys stay out of git. Unassigned bots never hop (ensure/router rules).

  python3 ~/sand-data/install-supervisor-prestart.py
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SUPERVISOR = "/usr/local/bin/sand-supervisor.mjs"
MARKER = "/* sand-brain supervisor-prestart */"

# Inserted immediately before the HOST_ENTRY spawn inside launchHost.
# LIVE supervisor is ESM — top of file already has:
#   import { execFileSync, spawn } from "node:child_process";
#   import { existsSync, ... } from "node:fs";
#   import { join } from "node:path";
# plus HOST_DIR / AGENT_DATA_ROOT in scope. Do NOT require() / createRequire /
# shadow those bindings — require is ReferenceError in ESM and the catch would
# silently no-op the hop while still spawning stock Grok.
# Fail-closed: any ensure error is logged; spawn still runs.
PRESTART_BLOCK = r"""%s
  try {
    const sandRoot =
      process.env.SAND_DATA_ROOT ||
      process.env.SAND_DATA ||
      AGENT_DATA_ROOT ||
      join(process.env.HOME || "/home/box", "sand-data");
    const ensurePy = join(sandRoot, "ensure-brain-overlay.py");
    const hostDir = HOST_DIR || process.env.SAND_HOST || join(process.env.HOME || "/home/box", "sand-host");
    if (existsSync(ensurePy)) {
      execFileSync(
        process.env.PYTHON || "python3",
        [ensurePy, "--host-dir", String(hostDir), "--sand", String(sandRoot), "--tools", String(sandRoot)],
        { timeout: 180000, stdio: ["ignore", "inherit", "inherit"], env: process.env }
      );
    } else {
      console.error("[sand-brain] supervisor prestart: missing", ensurePy);
    }
  } catch (sandPreErr) {
    console.error(
      "[sand-brain] supervisor prestart failed (continuing stock spawn):",
      sandPreErr && sandPreErr.message ? sandPreErr.message : sandPreErr
    );
  }
""" % MARKER


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


def node_check(path: str) -> None:
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"node --check {path} failed:\n{r.stderr}")


def assert_esm_supervisor_bindings(text: str) -> None:
    """Live sand-supervisor.mjs is ESM; prestart must reuse its imports."""
    head = text[:4000]
    if "require(" in head and not re.search(r"^\s*import\s+", head, re.M):
        die(
            "supervisor looks like CJS (require without import) — live box is ESM; "
            "refusing to insert a require()-based prestart"
        )
    if not re.search(
        r"import\s*\{[^}]*\bexecFileSync\b[^}]*\}\s*from\s*[\"']node:child_process[\"']",
        text,
    ):
        die(
            "supervisor missing ESM `import { execFileSync, … } from \"node:child_process\"` "
            "— refusing to half-patch"
        )
    if not re.search(
        r"import\s*\{[^}]*\bexistsSync\b[^}]*\}\s*from\s*[\"']node:fs[\"']",
        text,
    ):
        die(
            "supervisor missing ESM `import { existsSync, … } from \"node:fs\"` "
            "— refusing to half-patch"
        )
    if not re.search(
        r"import\s*\{[^}]*\bjoin\b[^}]*\}\s*from\s*[\"']node:path[\"']",
        text,
    ):
        die(
            "supervisor missing ESM `import { join } from \"node:path\"` "
            "— refusing to half-patch"
        )
    if not re.search(r"\bHOST_DIR\b", text):
        die("supervisor missing HOST_DIR binding — refusing to half-patch")
    if not re.search(r"\bAGENT_DATA_ROOT\b", text):
        die("supervisor missing AGENT_DATA_ROOT binding — refusing to half-patch")


def find_launch_spawn(text: str) -> re.Match | None:
    """Locate spawn(process.execPath, [HOST_ENTRY], …) used by launchHost."""
    patterns = [
        # Live shape (verified): spawn(process.execPath, [HOST_ENTRY], { ... })
        re.compile(
            r"spawn\s*\(\s*process\.execPath\s*,\s*\[\s*HOST_ENTRY\s*\]\s*,",
            re.M,
        ),
        # Slightly looser: spawn(process.execPath, [HOST_ENTRY]
        re.compile(
            r"spawn\s*\(\s*process\.execPath\s*,\s*\[\s*HOST_ENTRY\s*\]",
            re.M,
        ),
    ]
    hits: list[re.Match] = []
    for pat in patterns:
        hits.extend(pat.finditer(text))
    if not hits:
        return None
    # Prefer the most specific (first pattern); de-dupe by start.
    by_start = {}
    for m in hits:
        by_start.setdefault(m.start(), m)
    ordered = sorted(by_start.values(), key=lambda m: m.start())
    if len(ordered) != 1:
        # Ambiguous — refuse rather than half-patch.
        return None
    return ordered[0]


def already_installed(text: str) -> bool:
    if MARKER not in text or "ensure-brain-overlay.py" not in text:
        return False
    # Stale CJS installer left require() — treat as not current.
    m = find_launch_spawn(text)
    if m is None:
        return False
    marker_at = text.find(MARKER)
    if marker_at < 0 or marker_at > m.start():
        return False
    block = text[marker_at : m.start()]
    if "require(" in block:
        return False
    if "existsSync(ensurePy)" not in block:
        return False
    if "execFileSync(" not in block:
        return False
    return True


def prestart_block_ok(text: str) -> None:
    """Post-write gate: inserted region must be ESM-safe (no require)."""
    if MARKER not in text:
        die("post-check: prestart marker missing")
    m = find_launch_spawn(text)
    if m is None:
        die("post-check: HOST_ENTRY spawn missing after write")
    marker_at = text.find(MARKER)
    if marker_at < 0 or marker_at > m.start():
        die("post-check: prestart marker not before HOST_ENTRY spawn")
    block = text[marker_at : m.start()]
    if "require(" in block:
        die(
            "post-check: prestart block contains require() — "
            "live supervisor is ESM; that would ReferenceError and no-op the hop"
        )
    if "createRequire" in block:
        die("post-check: prestart must not use createRequire")
    if "existsSync(ensurePy)" not in block or "execFileSync(" not in block:
        die("post-check: prestart missing existsSync/execFileSync use")
    # Must not shadow module-level execFileSync with a local const binding.
    if re.search(r"\bconst\s*\{\s*execFileSync\s*\}", block) or re.search(
        r"\bconst\s+execFileSync\b", block
    ):
        die("post-check: prestart must not shadow module-level execFileSync")


def patch_text(text: str) -> str:
    if already_installed(text):
        return text
    assert_esm_supervisor_bindings(text)
    m = find_launch_spawn(text)
    if m is None:
        if "HOST_ENTRY" in text and "spawn(" in text:
            die(
                "found HOST_ENTRY/spawn but not the verified launchHost shape "
                "(spawn(process.execPath, [HOST_ENTRY], …)) — refusing to half-patch"
            )
        die(
            "could not find launchHost spawn(process.execPath, [HOST_ENTRY], …) — "
            "stock supervisor may have drifted; refusing to half-patch"
        )

    # Stale CJS prestart (require inside marker→spawn): replace that span.
    insert_at = None
    marker_at = text.find(MARKER)
    if marker_at >= 0 and marker_at < m.start():
        insert_at = marker_at
        # Expand to start of that line.
        insert_at = text.rfind("\n", 0, marker_at) + 1
    else:
        insert_at = text.rfind("\n", 0, m.start()) + 1

    # Insert a full statement block on its own lines immediately BEFORE the
    # spawn line (or replacing a stale prestart spanning insert_at→spawn).
    line_start_spawn = text.rfind("\n", 0, m.start()) + 1
    ws_end = line_start_spawn
    while ws_end < m.start() and text[ws_end] in " \t":
        ws_end += 1
    indent = text[line_start_spawn:ws_end]
    block_lines = PRESTART_BLOCK.splitlines(True)
    indented = "".join(
        (indent + ln) if ln.strip() else ln for ln in block_lines
    )
    if not indented.endswith("\n"):
        indented += "\n"
    return text[:insert_at] + indented + text[line_start_spawn:]


def sync_installer_to_sand(sand: str, tools_dir: str) -> None:
    os.makedirs(sand, exist_ok=True)
    for name in (
        "install-supervisor-prestart.py",
        "ensure-brain-overlay.py",
        "host-prestart-ensure.sh",
        "patch-brain-hook.py",
        "brain-router.cjs",
    ):
        src = os.path.join(tools_dir, name)
        dest = os.path.join(sand, name)
        if not os.path.isfile(src):
            continue
        if os.path.abspath(src) == os.path.abspath(dest):
            continue
        try:
            if os.path.isfile(dest) and os.path.samefile(src, dest):
                continue
        except OSError:
            pass
        shutil.copy2(src, dest)
        if name.endswith(".sh"):
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass


def install(
    supervisor: str,
    sand: str,
    tools_dir: str,
    dry_run: bool = False,
) -> str:
    if not os.path.isfile(supervisor):
        die(
            f"supervisor missing: {supervisor} — run on the Grok Bot computer "
            "(this tool does not invent a supervisor)"
        )

    print("== install-supervisor-prestart ==")
    print("  target:", supervisor)
    print("  note: live supervisor is ESM — prestart reuses execFileSync/existsSync/join")
    print("  fail-closed: ensure error → still spawn stock host-main")
    print("  Update Computer resets this file from the image — re-run after recover")

    sync_installer_to_sand(sand, tools_dir)
    text = read(supervisor)

    if already_installed(text):
        print("  already installed (ESM prestart marker present)")
        node_check(supervisor)
        prestart_block_ok(text)
        return "noop"

    if dry_run:
        try:
            assert_esm_supervisor_bindings(text)
            bindings = "ESM ok"
        except SystemExit as exc:
            bindings = f"ESM FAIL (exit {exc.code})"
        m = find_launch_spawn(text)
        print("== dry-run ==")
        print(f"  bindings: {bindings}")
        print(f"  spawn site: {'FOUND' if m else 'MISSING'}")
        print("  would insert sand-brain supervisor-prestart before HOST_ENTRY spawn")
        print("  would node --check FULL supervisor after write")
        print("  inserted block must NOT contain require(")
        return "dry-run"

    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    bk = f"{supervisor}.sand-brain-prestart.bak-{stamp}"
    # Keep one rolling .bak as well for easy restore.
    bak_simple = supervisor + ".sand-brain-prestart.bak"
    shutil.copy2(supervisor, bk)
    if not os.path.isfile(bak_simple):
        shutil.copy2(supervisor, bak_simple)
    print(f"  backup: {bk}")

    wrote = False
    try:
        node_check(supervisor)
        after = patch_text(text)
        if after == text:
            print("  no changes")
            return "noop"
        write(supervisor, after)
        wrote = True
        node_check(supervisor)
        body = read(supervisor)
        prestart_block_ok(body)
        if not already_installed(body):
            die("post-check: prestart not healthy after write")
    except SystemExit:
        if wrote:
            shutil.copy2(bk, supervisor)
            print(f"  RESTORED supervisor from {bk}", file=sys.stderr)
        raise
    except Exception as exc:
        if wrote:
            shutil.copy2(bk, supervisor)
            print(f"  RESTORED supervisor from {bk}", file=sys.stderr)
        die(f"install failed, restored backup: {exc!r}")

    print("DONE.")
    print("  launchHost will run ensure-brain-overlay.py via ESM execFileSync before spawn.")
    print("  Wrap is live only after a host process START that already has the wrap on disk.")
    print("  Desktop Quit Grok Bot does NOT restart host-main.")
    print("  Do NOT forceNow / restart-host / Update Computer to apply.")
    print("  After Update Computer recover: re-run this installer (supervisor is stock again).")
    print("  Honest limit: Update Computer recover cannot AUTO-restore the wrap —")
    print("  there is no durable hook between image restore and first spawn.")
    return "applied"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Patch sand-supervisor launchHost to run ensure before spawn (fail-closed)."
    )
    ap.add_argument(
        "--supervisor",
        default=os.environ.get("SAND_SUPERVISOR", DEFAULT_SUPERVISOR),
    )
    ap.add_argument("--sand", default=os.path.expanduser("~/sand-data"))
    ap.add_argument("--tools", default=HERE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sand = args.sand
    if not os.path.isdir(sand):
        agent = os.path.expanduser("~/agent-data")
        if os.path.isdir(agent):
            sand = agent
    install(args.supervisor, sand, args.tools, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
