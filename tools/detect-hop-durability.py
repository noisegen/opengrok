#!/usr/bin/env python3
"""detect-hop-durability — post-recover / ops checklist (no bounce).

Run from durable sand-data after Update Computer or when hops silently stop.
Does NOT patch anything. Reports facts John/Xian can act on.

  python3 ~/sand-data/detect-hop-durability.py
  python3 ~/sand-data/detect-hop-durability.py --json

Checks:
  - sand-host/version vs /etc/sand-box-image-sha (boot-fetch safe window?)
  - /usr/local/bin/sand-supervisor.mjs prestart marker (or stock)
  - ~/sand-host/host-main.cjs wrap markers
  - durable brain-router.cjs under sand-data/agent-data
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load_boot_fetch():
    import importlib.util

    path = os.path.join(HERE, "supervisor-boot-fetch.py")
    spec = importlib.util.spec_from_file_location("supervisor_boot_fetch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_boot = _load_boot_fetch()
read_version_file = _boot.read_version_file
describe_boot_fetch = _boot.describe_boot_fetch
should_boot_fetch_host_bundle = _boot.should_boot_fetch_host_bundle
boot_fetch_disarmed = _boot.boot_fetch_disarmed

DEFAULT_SUPERVISOR = "/usr/local/bin/sand-supervisor.mjs"
PRESTART_MARKER = "/* sand-brain supervisor-prestart */"
WRAP_MARKERS = ("sand-brain pass-through", "sand-brain durable-router")


def read_text(path: str, limit: int = 500_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def find_durable_router(sand: str) -> str:
    for base in (sand, os.path.expanduser("~/agent-data")):
        p = os.path.join(os.path.abspath(os.path.expanduser(base)), "brain-router.cjs")
        if os.path.isfile(p):
            return p
    return ""


def detect(
    *,
    host_dir: str,
    sand: str,
    supervisor: str,
    image_sha_file: str,
) -> dict[str, object]:
    host_dir = os.path.abspath(os.path.expanduser(host_dir))
    sand = os.path.abspath(os.path.expanduser(sand))
    supervisor = os.path.abspath(supervisor)
    image_sha_file = os.path.abspath(image_sha_file)

    local = read_version_file(os.path.join(host_dir, "version"))
    image = read_version_file(image_sha_file)
    boot = describe_boot_fetch(local, image)

    sup_txt = read_text(supervisor)
    prestart = PRESTART_MARKER in sup_txt and "ensure-brain-overlay.py" in sup_txt

    host_main = os.path.join(host_dir, "host-main.cjs")
    host_txt = read_text(host_main)
    wrapped = all(m in host_txt for m in WRAP_MARKERS)

    router = find_durable_router(sand)

    actions: list[str] = []
    if not router:
        actions.append("copy tools/brain-router.cjs to ~/sand-data/brain-router.cjs")
    if not wrapped:
        actions.append("python3 ~/sand-data/ensure-brain-overlay.py")
    if not prestart:
        actions.append(
            "python3 ~/sand-data/install-supervisor-prestart.py "
            "--sand ~/sand-data --tools ~/sand-data"
        )
    if boot["shouldBootFetchHostBundle"]:
        actions.append(
            "CAUTION: boot-fetch ARMED — supervisor recycle may swap sand-host; "
            "prefer waiting until localVersion !== imageSha"
        )
    elif boot["bootFetchDisarmed"] and not prestart:
        actions.append(
            "SAFE window: supervisor recycle loads prestart from disk without boot-fetch"
        )

    return {
        "hostDir": host_dir,
        "sandData": sand,
        "supervisor": supervisor,
        "bootFetch": boot,
        "supervisorPrestartInstalled": prestart,
        "hostMainWrapped": wrapped,
        "durableRouter": router or None,
        "recommendedActions": actions,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Hop durability facts (no bounce).")
    ap.add_argument("--host-dir", default=os.environ.get("SAND_HOST", "~/sand-host"))
    ap.add_argument("--sand", default=os.environ.get("SAND_DATA", "~/sand-data"))
    ap.add_argument(
        "--supervisor",
        default=os.environ.get("SAND_SUPERVISOR", DEFAULT_SUPERVISOR),
    )
    ap.add_argument("--image-sha-file", default="/etc/sand-box-image-sha")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    info = detect(
        host_dir=args.host_dir,
        sand=args.sand,
        supervisor=args.supervisor,
        image_sha_file=args.image_sha_file,
    )
    if args.json:
        print(json.dumps(info, indent=2))
        return

    boot = info["bootFetch"]
    print("== detect-hop-durability ==")
    print(f"  localVersion: {boot['localVersion']!r}")
    print(f"  imageSha:     {boot['imageSha']!r}")
    print(f"  shouldBootFetchHostBundle: {boot['shouldBootFetchHostBundle']}")
    print(f"  bootFetchDisarmed (safe recycle): {boot['bootFetchDisarmed']}")
    print(f"  supervisor prestart: {'YES' if info['supervisorPrestartInstalled'] else 'STOCK'}")
    print(f"  host-main wrapped:   {'YES' if info['hostMainWrapped'] else 'NO'}")
    print(f"  durable router:      {info['durableRouter'] or 'MISSING'}")
    if info["recommendedActions"]:
        print("  suggested:")
        for a in info["recommendedActions"]:
            print(f"    - {a}")
    else:
        print("  suggested: (none — disk + supervisor look wired; wrap live on next launchHost)")


if __name__ == "__main__":
    main()
