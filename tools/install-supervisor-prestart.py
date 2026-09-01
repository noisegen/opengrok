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
  - sand-supervisor.mjs is -rw-r--r-- root root; /usr/local/bin is root-owned.
    User `box` cannot write the file or create a sibling .bak there. box has
    passwordless sudo (`sudo -n true`). sudo sets HOME=/root USER=root, so
    ~/sand-data would become /root/sand-data unless --sand/--tools are absolute.

What this does:
  Insert a fail-closed ensure call immediately before that spawn, using the
  supervisor's existing ESM bindings (execFileSync, existsSync, join,
  HOST_DIR, AGENT_DATA_ROOT). Never require() / createRequire — live
  supervisor is ESM and require would ReferenceError inside the try/catch,
  silently no-oping the hop while stock spawn continues. If ensure fails,
  spawn still proceeds (stock Grok lives). Idempotent marker:
  /* sand-brain supervisor-prestart */

  Backups always under --sand (brain-overlay-backups-*), never siblings in
  /usr/local/bin. If supervisor is not writable, re-exec via `sudo -n` with
  absolute --sand/--tools (do not rely on HOME). If sudo unavailable, die
  with that permission fact — never half-patch.

Boot-fetch (sand-host swap only, supervisor binary kept):
  Patched launchHost re-runs ensure before spawn → wrap can come back.

Supervisor recycle when localVersion !== imageSha (shouldBootFetchHostBundle false):
  Boot-fetch is DISARMED — safe window to load prestart into memory without
  sand-host swap. Check: python3 ~/sand-data/supervisor-boot-fetch.py --check

Update Computer (image recover):
  Supervisor is stock again. This hop CANNOT auto-restore the wrap until
  someone re-runs this installer from durable ~/sand-data AFTER recover.
  There is no durable automatic hook between image restore and first spawn
  with the current Cursor supervisor.

NEVER: forceNow upgrade, ./adapters restart-host, or Update Computer to apply.
Keys stay out of git. Unassigned bots never hop (ensure/router rules).

  python3 /home/box/sand-data/install-supervisor-prestart.py \\
    --sand /home/box/sand-data --tools /home/box/sand-data
  # (auto sudo -n when needed; paths must be absolute under sudo)
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
ELEVATED_ENV = "SAND_BRAIN_SUPERVISOR_AS_ROOT"

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
    const appendHopFailLog = (msg) => {
      try {
        const logFile =
          process.env.BRAIN_LOG ||
          join(sandRoot, "hop-fail-logs", "sand-brain.log");
        const line =
          new Date().toISOString() +
          " [sand-brain] " +
          String(msg == null ? "" : msg);
        execFileSync(
          "/bin/sh",
          [
            "-c",
            'mkdir -p "$(dirname "$1")" && printf "%%s\\n" "$2" >> "$1"',
            "sh",
            logFile,
            line,
          ],
          { stdio: "ignore" }
        );
      } catch (_) {
        /* logging must never block spawn */
      }
    };
    if (existsSync(ensurePy)) {
      execFileSync(
        process.env.PYTHON || "python3",
        [ensurePy, "--host-dir", String(hostDir), "--sand", String(sandRoot), "--tools", String(sandRoot)],
        { timeout: 180000, stdio: ["ignore", "inherit", "inherit"], env: process.env }
      );
    } else {
      console.error("[sand-brain] supervisor prestart: missing", ensurePy);
      appendHopFailLog("supervisor prestart: missing " + String(ensurePy));
    }
  } catch (sandPreErr) {
    console.error(
      "[sand-brain] supervisor prestart failed (continuing stock spawn):",
      sandPreErr && sandPreErr.message ? sandPreErr.message : sandPreErr
    );
    try {
      const sandRoot2 =
        process.env.SAND_DATA_ROOT ||
        process.env.SAND_DATA ||
        AGENT_DATA_ROOT ||
        join(process.env.HOME || "/home/box", "sand-data");
      const logFile =
        process.env.BRAIN_LOG ||
        join(sandRoot2, "hop-fail-logs", "sand-brain.log");
      const line =
        new Date().toISOString() +
        " [sand-brain] supervisor prestart failed (continuing stock spawn): " +
        String(
          sandPreErr && sandPreErr.message ? sandPreErr.message : sandPreErr
        );
      execFileSync(
        "/bin/sh",
        [
          "-c",
          'mkdir -p "$(dirname "$1")" && printf "%%s\\n" "$2" >> "$1"',
          "sh",
          logFile,
          line,
        ],
        { stdio: "ignore" }
      );
    } catch (_) {
      /* logging must never block spawn */
    }
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
        "supervisor-boot-fetch.py",
        "detect-hop-durability.py",
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


def abspath(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def supervisor_writable(path: str) -> bool:
    """True if this uid can open the supervisor for write (file must exist)."""
    if not os.path.isfile(path):
        return False
    return os.access(path, os.W_OK)


def can_sudo_n() -> bool:
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def permission_die(supervisor: str, *, sudo_failed: bool = False) -> None:
    sudo_bit = (
        "passwordless sudo unavailable (sudo -n true failed) — refusing to half-patch. "
        if sudo_failed
        else "refusing to half-patch without a writable supervisor or sudo elevation. "
    )
    die(
        f"supervisor not writable by uid {os.getuid()}: {supervisor} "
        f"(live box: -rw-r--r-- 1 root root under /usr/local/bin which box cannot "
        f"write; /usr/local/bin is drwxr-xr-x root root so box also cannot create "
        f"a sibling .bak there). {sudo_bit}"
        f"Re-run with sudo -n and explicit absolute --sand/--tools "
        f"(sudo sets HOME=/root USER=root, so ~/sand-data would be /root/sand-data)."
    )


def sudo_reexec_argv(
    script: str,
    supervisor: str,
    sand: str,
    tools_dir: str,
    dry_run: bool,
) -> list[str]:
    """Build sudo -n argv with absolute paths (never rely on HOME under sudo)."""
    cmd = [
        "sudo",
        "-n",
        sys.executable,
        abspath(script),
        "--supervisor",
        abspath(supervisor),
        "--sand",
        abspath(sand),
        "--tools",
        abspath(tools_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def backup_supervisor_under_sand(supervisor: str, sand: str) -> str:
    """Copy supervisor into sand backups dir — never a sibling under /usr/local/bin."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    bk_dir = os.path.join(sand, f"brain-overlay-backups-{stamp}")
    os.makedirs(bk_dir, exist_ok=True)
    base = os.path.basename(supervisor) or "sand-supervisor.mjs"
    bk = os.path.join(bk_dir, f"{base}.bak")
    shutil.copy2(supervisor, bk)
    # Rolling easy-restore name also under sand (not next to the live binary).
    rolling = os.path.join(sand, f"{base}.sand-brain-prestart.bak")
    shutil.copy2(supervisor, rolling)
    return bk


def install(
    supervisor: str,
    sand: str,
    tools_dir: str,
    dry_run: bool = False,
    allow_elevate: bool = False,
    script_path: str | None = None,
) -> str:
    supervisor = abspath(supervisor)
    sand = abspath(sand)
    tools_dir = abspath(tools_dir)

    if not os.path.isfile(supervisor):
        die(
            f"supervisor missing: {supervisor} — run on the Grok Bot computer "
            "(this tool does not invent a supervisor)"
        )

    print("== install-supervisor-prestart ==")
    print("  target:", supervisor)
    print("  sand:  ", sand)
    print("  note: live supervisor is ESM — prestart reuses execFileSync/existsSync/join")
    print("  note: backups go under --sand (never siblings in /usr/local/bin)")
    print("  fail-closed: ensure error → still spawn stock host-main")
    print("  Update Computer resets this file from the image — re-run after recover")
    if os.environ.get(ELEVATED_ENV) == "1":
        print(f"  elevated: uid={os.getuid()} (sudo HOME=/root ignored; --sand is absolute)")

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
        writable = supervisor_writable(supervisor)
        print("== dry-run ==")
        print(f"  bindings: {bindings}")
        print(f"  spawn site: {'FOUND' if m else 'MISSING'}")
        print(f"  writable: {writable} (uid {os.getuid()})")
        print("  would backup under", os.path.join(sand, "brain-overlay-backups-*"))
        print("  would insert sand-brain supervisor-prestart before HOST_ENTRY spawn")
        print("  would node --check FULL supervisor after write")
        print("  inserted block must NOT contain require(")
        if not writable:
            print("  would re-exec: sudo -n … with absolute --sand/--tools")
        return "dry-run"

    # Live: root-owned supervisor — elevate before any write attempt.
    if not supervisor_writable(supervisor):
        if allow_elevate and os.environ.get(ELEVATED_ENV) != "1":
            if not can_sudo_n():
                permission_die(supervisor, sudo_failed=True)
            script = script_path or os.path.abspath(__file__)
            cmd = sudo_reexec_argv(script, supervisor, sand, tools_dir, dry_run=False)
            print("  supervisor not writable by this uid — elevating via sudo -n")
            print("  ", " ".join(cmd))
            print("  note: sudo sets HOME=/root — --sand/--tools are absolute paths")
            env = os.environ.copy()
            env[ELEVATED_ENV] = "1"
            r = subprocess.run(cmd, env=env)
            sys.exit(r.returncode)
        permission_die(supervisor, sudo_failed=False)

    bk = backup_supervisor_under_sand(supervisor, sand)
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
            try:
                shutil.copy2(bk, supervisor)
                print(f"  RESTORED supervisor from {bk}", file=sys.stderr)
            except OSError as restore_exc:
                die(
                    f"install failed ({exc!r}) and restore also failed ({restore_exc!r}); "
                    f"manual restore from {bk}"
                )
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
        description=(
            "Patch sand-supervisor launchHost to run ensure before spawn (fail-closed). "
            "Backups under --sand. Elevates via sudo -n with absolute --sand/--tools "
            "(sudo HOME=/root)."
        )
    )
    ap.add_argument(
        "--supervisor",
        default=os.environ.get("SAND_SUPERVISOR", DEFAULT_SUPERVISOR),
    )
    ap.add_argument("--sand", default=os.path.expanduser("~/sand-data"))
    ap.add_argument("--tools", default=HERE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sand = abspath(args.sand)
    if not os.path.isdir(sand):
        agent = abspath("~/agent-data")
        if os.path.isdir(agent):
            sand = agent
    tools = abspath(args.tools)
    # If invoked without explicit paths as root (sudo), refuse HOME=/root sand.
    if os.environ.get(ELEVATED_ENV) == "1" and sand.startswith("/root/"):
        die(
            f"--sand resolved under /root ({sand}) — sudo sets HOME=/root. "
            "Pass absolute --sand /home/box/sand-data --tools /home/box/sand-data"
        )
    install(
        abspath(args.supervisor),
        sand,
        tools,
        dry_run=args.dry_run,
        allow_elevate=True,
        script_path=os.path.abspath(__file__),
    )


if __name__ == "__main__":
    main()
