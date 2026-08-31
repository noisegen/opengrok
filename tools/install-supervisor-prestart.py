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
  Insert a fail-closed ensure call immediately before that spawn. If ensure
  fails, spawn still proceeds (stock Grok lives). Idempotent marker:
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
# Fail-closed: any ensure error is logged; spawn still runs.
PRESTART_BLOCK = r"""%s
  try {
    const { execFileSync } = require("child_process");
    const fs = require("fs");
    const path = require("path");
    const sandRoot =
      process.env.SAND_DATA_ROOT ||
      process.env.SAND_DATA ||
      path.join(process.env.HOME || "/home/box", "sand-data");
    const ensurePy = path.join(sandRoot, "ensure-brain-overlay.py");
    const hostDir =
      typeof HOST_DIR !== "undefined"
        ? HOST_DIR
        : process.env.SAND_HOST || path.join(process.env.HOME || "/home/box", "sand-host");
    if (fs.existsSync(ensurePy)) {
      execFileSync(
        process.env.PYTHON || "python3",
        ["--", ensurePy, "--host-dir", String(hostDir), "--sand", String(sandRoot), "--tools", String(sandRoot)],
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
        return None if len(ordered) > 1 else ordered[0]
    return ordered[0]


def already_installed(text: str) -> bool:
    return MARKER in text and "ensure-brain-overlay.py" in text


def patch_text(text: str) -> str:
    if already_installed(text):
        return text
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
    # Insert a full statement block on its own lines immediately BEFORE the
    # spawn line. Indent = leading whitespace only (not "const child = ").
    line_start = text.rfind("\n", 0, m.start()) + 1
    ws_end = line_start
    while ws_end < m.start() and text[ws_end] in " \t":
        ws_end += 1
    indent = text[line_start:ws_end]
    block_lines = PRESTART_BLOCK.splitlines(True)
    indented = "".join(
        (indent + ln) if ln.strip() else ln for ln in block_lines
    )
    if not indented.endswith("\n"):
        indented += "\n"
    return text[:line_start] + indented + text[line_start:]


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
    print("  note: stock supervisor has NO sand-data prestart hook; this patches launchHost")
    print("  fail-closed: ensure error → still spawn stock host-main")
    print("  Update Computer resets this file from the image — re-run after recover")

    sync_installer_to_sand(sand, tools_dir)
    text = read(supervisor)

    if already_installed(text):
        print("  already installed (marker present)")
        node_check(supervisor)
        return "noop"

    if dry_run:
        m = find_launch_spawn(text)
        print("== dry-run ==")
        print(f"  spawn site: {'FOUND' if m else 'MISSING'}")
        print("  would insert sand-brain supervisor-prestart before HOST_ENTRY spawn")
        print("  would node --check FULL supervisor after write")
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
        if not already_installed(body):
            die("post-check: prestart marker missing after write")
        if find_launch_spawn(body) is None:
            die("post-check: HOST_ENTRY spawn missing after write")
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
    print("  launchHost will run ~/sand-data/ensure-brain-overlay.py before spawn.")
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
