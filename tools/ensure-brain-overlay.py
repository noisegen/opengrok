#!/usr/bin/env python3
"""ensure-brain-overlay — fail-closed host-main wrap (disk only).

PRODUCTION RULES (learned the hard way):
  - NEVER apply a hop by patching host-main then forceNow/supervisor bounce.
    That twice took down the whole Grok Bot fleet; John had to Update Computer
    to recover — and Update Computer boot-fetches a stock sand-host, wiping
    any sand-host-only overlay. The leftover-`}` stale-upgrade bug is what
    full-file node --check is meant to catch; do not invent other bounce
    causes without evidence.
  - Overlay living only in sand-host/host-main.cjs is wiped every recover /
    boot-fetch.
  - brain-router.cjs MUST live under sand-data/agent-data (durable). The tiny
    host-main hook loads it from there; require failure → native (fail-closed).
  - Always `node --check` the FULL patched host-main, never a wrap slice.
  - Desktop Quit Grok Bot does NOT restart host-main (always-on supervisor).
  - host-prestart-ensure.sh is inert unless sand-supervisor launchHost calls
    it — see install-supervisor-prestart.py. Stock supervisor has no hook.

What this tool does:
  1. Sync durable files under sand-data (brain-router, patch, ensure, prestart,
     install-supervisor-prestart).
  2. Patch host-main with a tiny fail-closed wrap (idempotent).
  3. FULL-file node --check; on any failure RESTORES host-main and exits 1.
  4. Never touches API keys. Never claims quit/reopen loads the wrap.

Load path (separate from this disk patch):
  Wrap is live ONLY after a host process START that already has the wrap on
  disk. Wire that via install-supervisor-prestart.py (patches launchHost to
  run ensure after boot-fetch swap, before spawn). Update Computer resets
  supervisor from the image — hop cannot auto-survive that recover until
  the installer is re-run (no durable automatic hook today).

NEVER: Update Grok Bot's Computer / ./adapters restart-host / forceNow upgrade
to "apply" a hop.

See docs/CLOUD-HOST.md and docs/FAILURE-MODES.md F19.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

PRESTART_SH = """#!/usr/bin/env bash
# Disk helper: re-apply host-main wrap. Does NOT bounce. Does NOT Update Computer.
# Stock sand-supervisor.mjs does NOT call this. It is only useful when:
#   - install-supervisor-prestart.py has patched launchHost to exec ensure, or
#   - an operator runs it manually BEFORE the next host process start.
set -euo pipefail
SAND="${SAND_DATA:-${HOME}/sand-data}"
# agent-data is a symlink to sand-data on the live box
if [[ ! -d "$SAND" && -d "${HOME}/agent-data" ]]; then
  SAND="${HOME}/agent-data"
fi
HOST_DIR="${SAND_HOST:-${HOME}/sand-host}"
TOOLS="${BRAIN_TOOLS:-$SAND}"
exec python3 "$SAND/ensure-brain-overlay.py" \\
  --host-dir "$HOST_DIR" \\
  --sand "$SAND" \\
  --tools "$TOOLS"
"""


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_patch_mod(path: str):
    spec = importlib.util.spec_from_file_location("patch_brain_hook", path)
    if spec is None or spec.loader is None:
        die(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


def node_check(path: str, label: str = "") -> None:
    """FULL-file syntax check. Slice-only checks hid fleet-killing corruption."""
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode != 0:
        tag = label or path
        die(f"node --check {tag} failed (FULL file):\n{r.stderr}")


def router_loads(path: str) -> None:
    script = (
        "const m=require(process.argv[1]);"
        "if(typeof m.createLazyBrainSession!=='function')process.exit(2);"
        "if(typeof m.resolveBrain!=='function')process.exit(3);"
        "console.log('ok');"
    )
    r = subprocess.run(
        ["node", "-e", script, path],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(path) or ".",
    )
    if r.returncode != 0:
        die(f"brain-router load failed ({path}):\n{r.stderr or r.stdout}")


def overlay_healthy(host_text: str, sand_router: str) -> bool:
    if not os.path.isfile(sand_router):
        return False
    if "sand-brain pass-through" not in host_text:
        return False
    if "sand-brain durable-router" not in host_text:
        return False
    if "createLazyBrainSession" not in host_text:
        return False
    if "overlay failed, native" not in host_text:
        return False
    if "createCursorInferencePromptSession" not in host_text:
        return False
    return True


def has_hop_intent(bindings_path: str) -> bool:
    if not os.path.isfile(bindings_path):
        return False
    try:
        data = json.loads(read(bindings_path))
    except Exception:
        return False
    agents = data.get("agents") or {}
    native = {"grok", "cursor", "stock", ""}
    for ent in agents.values():
        if not isinstance(ent, dict):
            continue
        brain = str(ent.get("brain") or "").lower()
        if brain and brain not in native:
            return True
    return False


def pick_router_src(sand: str, tools_dir: str) -> str:
    durable = os.path.join(sand, "brain-router.cjs")
    repo = os.path.join(tools_dir, "brain-router.cjs")
    if os.path.isfile(durable):
        return durable
    if os.path.isfile(repo):
        return repo
    die(
        "no durable brain-router.cjs — copy tools/brain-router.cjs to "
        f"{durable} before ensure"
    )
    return ""


def sync_durable(tools_dir: str, sand: str) -> None:
    os.makedirs(sand, exist_ok=True)
    for name in (
        "brain-router.cjs",
        "patch-brain-hook.py",
        "ensure-brain-overlay.py",
        "host-prestart-ensure.sh",
        "install-supervisor-prestart.py",
    ):
        src = os.path.join(tools_dir, name)
        dest = os.path.join(sand, name)
        if name == "host-prestart-ensure.sh" and not os.path.isfile(src):
            write(dest, PRESTART_SH)
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass
            print(f"  durable: {dest}")
            continue
        if not os.path.isfile(src):
            continue
        if os.path.isfile(dest):
            try:
                if os.path.samefile(src, dest):
                    continue
            except OSError:
                pass
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copy2(src, dest)
            if name.endswith(".sh"):
                try:
                    os.chmod(dest, 0o755)
                except OSError:
                    pass
            print(f"  durable: {dest}")


def _print_load_path(sand: str) -> None:
    print("  LOAD PATH (honest):")
    print("    - This wrote/verified host-main on disk only.")
    print("    - Desktop Quit Grok Bot does NOT restart host-main.")
    print("    - Wrap is live only after a host process START with wrap already on disk.")
    print("    - Stock supervisor launchHost has no prestart — wire it with:")
    print(f"        python3 {os.path.join(sand, 'install-supervisor-prestart.py')}")
    print("    - Boot-fetch (host swap, supervisor kept): patched launchHost re-ensures.")
    print("    - Update Computer recover: supervisor resets from image — CANNOT auto-restore")
    print("      the wrap; re-run install-supervisor-prestart + ensure after recover.")
    print("    - NEVER forceNow / adapters restart-host / Update Computer to apply.")


def ensure(
    host_dir: str,
    sand: str,
    tools_dir: str,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    host_main = os.path.join(host_dir, "host-main.cjs")
    sand_router = os.path.join(sand, "brain-router.cjs")
    bindings = os.path.join(sand, "brain-bindings.json")
    patch_py = os.path.join(sand, "patch-brain-hook.py")
    if not os.path.isfile(patch_py):
        patch_py = os.path.join(tools_dir, "patch-brain-hook.py")
    if not os.path.isfile(patch_py):
        die(f"missing patch-brain-hook.py ({patch_py})")

    if not os.path.isfile(host_main):
        die(f"host-main missing: {host_main} — run on the Grok Bot computer after recover")

    print("== ensure-brain-overlay ==")
    print("  disk patch only — does not restart host-main; NEVER forceNow bounce")
    sync_durable(tools_dir, sand)
    router_src = pick_router_src(sand, tools_dir)

    # Always keep durable router bytes current under sand-data.
    if os.path.abspath(router_src) != os.path.abspath(sand_router):
        shutil.copy2(router_src, sand_router)
        print(f"  durable router: {sand_router}")

    host_text = read(host_main)
    healthy = overlay_healthy(host_text, sand_router)

    if healthy and not force:
        print("  no changes needed (overlay healthy; durable-router loader present)")
        if has_hop_intent(bindings):
            print("  bindings: hop intent present; consumer installed on disk")
        print(f"  prestart helper: {os.path.join(sand, 'host-prestart-ensure.sh')} (unused unless supervisor wired)")
        _print_load_path(sand)
        return "noop"

    mod = load_patch_mod(patch_py)
    shape = None
    if hasattr(mod, "detect_shape"):
        shape = mod.detect_shape(host_text)
    elif "createXaiPromptSession" in host_text:
        shape = "xai"
    if shape is None:
        die(
            "unrecognized host-main shape — need grok-bot-setup "
            "(createXaiPromptSession + inferenceProvider) or recovered "
            "Cursor-native (const session = createCursorInferencePromptSession; "
            "return session). Refusing to half-patch"
        )

    if dry_run:
        print("== dry-run ==")
        print(f"  shape:  {shape}")
        print(f"  host:   {'HEALTHY' if healthy else 'NEEDS PATCH'}")
        print(f"  router: {sand_router} ({'present' if os.path.isfile(sand_router) else 'MISSING'})")
        print(f"  would run {patch_py}")
        print("  would node --check FULL host-main (not a wrap slice)")
        print("  would NOT restart host; wrap live only after next host process start")
        print("  NEVER forceNow bounce")
        return "dry-run"

    # Pre-flight: stock host must already parse before we touch it.
    node_check(host_main, "host-main (pre-patch FULL)")

    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    bk_dir = os.path.join(sand, f"brain-overlay-backups-{stamp}")
    os.makedirs(bk_dir, exist_ok=True)
    shutil.copy2(host_main, os.path.join(bk_dir, "host-main.cjs.bak"))
    print(f"  backups -> {bk_dir}")
    print(f"  shape: {shape}")

    wrote_host = False

    def restore() -> None:
        shutil.copy2(os.path.join(bk_dir, "host-main.cjs.bak"), host_main)
        print(f"  RESTORED host-main from {bk_dir}", file=sys.stderr)

    try:
        node_check(sand_router, "sand-data/brain-router.cjs")
        router_loads(sand_router)

        mod.HOST = host_main
        mod.ROUTER = sand_router
        before = read(host_main)
        after = mod.patch(before)
        if after != before:
            write(host_main, after)
            wrote_host = True
            print(f"  [host]   {host_main} patched ({shape})")
        else:
            print("  [host]   already patched")

        # FULL file — this is the gate that must pass before any host start.
        node_check(host_main, "host-main (post-patch FULL)")
        body = read(host_main)
        if not overlay_healthy(body, sand_router):
            die("post-check: overlay still unhealthy — upstream drift; refusing half-state")
        router_loads(sand_router)
    except SystemExit:
        if wrote_host:
            restore()
        raise
    except Exception as exc:
        if wrote_host:
            restore()
        die(f"ensure failed, restored backup: {exc!r}")

    print("DONE (disk).")
    print("  Durable router:", sand_router)
    print("  Hop failure fail-closes to native; unassigned Bots never enter the hop.")
    _print_load_path(sand)
    return "applied"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fail-closed brain hop ensure (durable sand-data; disk patch only)."
    )
    ap.add_argument("--host-dir", default=os.path.expanduser("~/sand-host"))
    ap.add_argument("--sand", default=os.path.expanduser("~/sand-data"))
    ap.add_argument("--tools", default=HERE, help="repo tools/ dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    # Prefer agent-data when it exists and sand-data does not (live box symlink world).
    sand = args.sand
    if not os.path.isdir(sand):
        agent = os.path.expanduser("~/agent-data")
        if os.path.isdir(agent):
            sand = agent
    ensure(args.host_dir, sand, args.tools, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
