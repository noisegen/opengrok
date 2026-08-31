#!/usr/bin/env python3
"""ensure-brain-overlay — re-install the brain hop after a Grok Bot host update/recover.

THE PRODUCTION INCIDENT: per-Bot assignments live in sand-data (brain-bindings.json)
and survive Computer recover. sand-host does not — host-main.cjs comes back stock and
brain-router.cjs is gone. Bindings / sidebar labels still say DeepSeek while the live
host no longer consumes them. A half-applied overlay (hook require without the router
file, or a broken hop) can take down every Bot, not just hopped ones.

This tool is the fail-closed re-apply path:

  1. Keep a durable brain-router.cjs (+ this script / patch-brain-hook) under sand-data.
  2. Copy the router into sand-host (vendor rewrite wipes that tree).
  3. Re-apply patch-brain-hook.py (idempotent when already healthy).
  4. Verify hook + router load. On any post-backup failure: restore host-main, exit 1.
  5. Never touch API keys; never invent a hop when the overlay cannot be proven healthy.

Run on the box (or with --host-dir/--sand pointed at a fixture):

    python3 ensure-brain-overlay.py
    python3 doctor.py --fix          # calls this when desynced

See docs/CLOUD-HOST.md (brain overlay survival) and docs/FAILURE-MODES.md F19.
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


def node_check(path: str) -> None:
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"node --check {path} failed:\n{r.stderr}")


def router_loads(path: str) -> None:
    """Prove brain-router.cjs parses and exports the fail-closed entry points."""
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


def overlay_healthy(host_text: str, router_path: str) -> bool:
    if not os.path.isfile(router_path):
        return False
    if "sand-brain pass-through" not in host_text:
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
        f"{durable} (or keep it next to this script) before ensure"
    )
    return ""  # unreachable


def sync_durable(tools_dir: str, sand: str) -> None:
    """Keep sand-data copies that survive sand-host rewrite."""
    os.makedirs(sand, exist_ok=True)
    for name in ("brain-router.cjs", "patch-brain-hook.py", "ensure-brain-overlay.py"):
        src = os.path.join(tools_dir, name)
        dest = os.path.join(sand, name)
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
            print(f"  durable: {dest}")


def ensure(
    host_dir: str,
    sand: str,
    tools_dir: str,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    host_main = os.path.join(host_dir, "host-main.cjs")
    router_dest = os.path.join(host_dir, "brain-router.cjs")
    bindings = os.path.join(sand, "brain-bindings.json")
    patch_py = os.path.join(sand, "patch-brain-hook.py")
    if not os.path.isfile(patch_py):
        patch_py = os.path.join(tools_dir, "patch-brain-hook.py")
    if not os.path.isfile(patch_py):
        die(f"missing patch-brain-hook.py ({patch_py})")

    if not os.path.isfile(host_main):
        die(f"host-main missing: {host_main} — run on the Grok Bot computer after recover")

    print("== ensure-brain-overlay ==")
    sync_durable(tools_dir, sand)
    router_src = pick_router_src(sand, tools_dir)

    host_text = read(host_main)
    router_same = False
    if os.path.isfile(router_dest):
        router_same = open(router_src, "rb").read() == open(router_dest, "rb").read()
    healthy = overlay_healthy(host_text, router_dest) and router_same

    if healthy and not force:
        print("  no changes needed (overlay healthy)")
        if has_hop_intent(bindings):
            print("  bindings: hop intent present; consumer installed")
        return "noop"

    if "createXaiPromptSession" not in host_text:
        die(
            "createXaiPromptSession missing from host-main — upstream bundle "
            "is not a patchable grok-bot-setup host (refusing to half-patch)"
        )

    if dry_run:
        print("== dry-run ==")
        print(f"  host:   {'HEALTHY' if overlay_healthy(host_text, router_dest) else 'NEEDS PATCH'}")
        print(f"  router: {'in sync' if router_same else 'MISSING/DRIFT'}")
        print(f"  would copy {router_src} -> {router_dest}")
        print(f"  would run {patch_py}")
        return "dry-run"

    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    bk_dir = os.path.join(sand, f"brain-overlay-backups-{stamp}")
    os.makedirs(bk_dir, exist_ok=True)
    shutil.copy2(host_main, os.path.join(bk_dir, "host-main.cjs.bak"))
    if os.path.isfile(router_dest):
        shutil.copy2(router_dest, os.path.join(bk_dir, "brain-router.cjs.bak"))
    print(f"  backups -> {bk_dir}")

    wrote_host = False

    def restore() -> None:
        shutil.copy2(os.path.join(bk_dir, "host-main.cjs.bak"), host_main)
        bak_r = os.path.join(bk_dir, "brain-router.cjs.bak")
        if os.path.isfile(bak_r):
            shutil.copy2(bak_r, router_dest)
        print(f"  RESTORED host-main from {bk_dir}", file=sys.stderr)

    try:
        shutil.copy2(router_src, router_dest)
        print(f"  [router] {router_dest}")
        node_check(router_dest)
        router_loads(router_dest)

        mod = load_patch_mod(patch_py)
        mod.HOST = host_main
        mod.ROUTER = router_dest
        before = read(host_main)
        after = mod.patch(before)
        if after != before:
            write(host_main, after)
            wrote_host = True
            print(f"  [host]   {host_main} patched")
        else:
            print("  [host]   already patched")

        node_check(host_main)
        body = read(host_main)
        if not overlay_healthy(body, router_dest):
            die("post-check: overlay still unhealthy — upstream drift; refusing half-state")
        router_loads(router_dest)
    except SystemExit:
        if wrote_host or os.path.isfile(router_dest):
            restore()
        raise
    except Exception as exc:
        restore()
        die(f"ensure failed, restored backup: {exc!r}")

    print("DONE. Quit Grok Bot fully and reopen (do not ./adapters restart-host).")
    print("Hop failure fail-closes to native Grok; unassigned Bots never touch the hop.")
    return "applied"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Re-install brain hop overlay after host update/recover (fail-closed)."
    )
    ap.add_argument("--host-dir", default=os.path.expanduser("~/sand-host"))
    ap.add_argument("--sand", default=os.path.expanduser("~/sand-data"))
    ap.add_argument(
        "--tools",
        default=HERE,
        help="repo tools/ dir (source of durable overlay files)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-copy/re-patch even if healthy")
    args = ap.parse_args()
    ensure(args.host_dir, args.sand, args.tools, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
