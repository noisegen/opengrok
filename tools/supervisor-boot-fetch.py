#!/usr/bin/env python3
"""supervisor-boot-fetch — mirror upstream shouldBootFetchHostBundle (not vendored).

Live sand-supervisor.mjs (stock) decides whether to boot-fetch sand-host on
supervisor start using logic equivalent to:

    shouldBootFetchHostBundle = (localVersion === imageSha)

Where:
  - localVersion  = contents of ~/sand-host/version (current on-disk bundle)
  - imageSha      = contents of /etc/sand-box-image-sha (baked image id)

When equal → boot-fetch is ARMED (next supervisor start may swap sand-host).
When NOT equal → boot-fetch is DISARMED — the safe supervisor-recycle window:
  install install-supervisor-prestart.py to disk, recycle supervisor process,
  patched launchHost enters memory, launchHost ensure re-applies wrap — without
  a Cursor bundle fetch wiping sand-host mid-recycle.

This module is for docs, tests, and ops helpers only. It does not patch the
live supervisor binary.

  python3 ~/sand-data/supervisor-boot-fetch.py --check
  python3 ~/sand-data/supervisor-boot-fetch.py --local 659557c --image 1a2167a
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_HOST_DIR = os.path.expanduser("~/sand-host")
DEFAULT_IMAGE_SHA = "/etc/sand-box-image-sha"


def read_version_file(path: str) -> str:
    """Read a single-line version/sha file; return stripped text or ''."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def should_boot_fetch_host_bundle(local_version: str, image_sha: str) -> bool:
    """True when upstream would boot-fetch (localVersion === imageSha)."""
    lv = (local_version or "").strip()
    is_ = (image_sha or "").strip()
    if not lv or not is_:
        return False
    return lv == is_


def boot_fetch_disarmed(local_version: str, image_sha: str) -> bool:
    """True in the safe recycle window (local !== baked image sha)."""
    lv = (local_version or "").strip()
    is_ = (image_sha or "").strip()
    if not lv or not is_:
        return False
    return lv != is_


def describe_boot_fetch(local_version: str, image_sha: str) -> dict[str, object]:
    armed = should_boot_fetch_host_bundle(local_version, image_sha)
    disarmed = boot_fetch_disarmed(local_version, image_sha)
    if armed:
        note = (
            "boot-fetch ARMED on supervisor start (localVersion === imageSha) — "
            "sand-host may swap from image"
        )
    elif disarmed:
        note = (
            "boot-fetch DISARMED (localVersion !== imageSha) — "
            "SAFE supervisor-recycle window for install-supervisor-prestart.py"
        )
    else:
        note = "unknown (missing localVersion or imageSha)"
    return {
        "localVersion": local_version,
        "imageSha": image_sha,
        "shouldBootFetchHostBundle": armed,
        "bootFetchDisarmed": disarmed,
        "note": note,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Report whether sand-supervisor would boot-fetch sand-host "
            "(localVersion === imageSha)."
        )
    )
    ap.add_argument(
        "--host-dir",
        default=os.environ.get("SAND_HOST", DEFAULT_HOST_DIR),
        help="sand-host dir containing version file (default: ~/sand-host)",
    )
    ap.add_argument(
        "--image-sha-file",
        default=DEFAULT_IMAGE_SHA,
        help="baked image sha file (default: /etc/sand-box-image-sha)",
    )
    ap.add_argument("--local", help="override localVersion (else read host-dir/version)")
    ap.add_argument("--image", help="override imageSha (else read image-sha-file)")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 0 only when boot-fetch is disarmed (safe recycle window)",
    )
    args = ap.parse_args()

    host_dir = os.path.abspath(os.path.expanduser(args.host_dir))
    local = args.local if args.local is not None else read_version_file(
        os.path.join(host_dir, "version")
    )
    image = args.image if args.image is not None else read_version_file(args.image_sha_file)
    info = describe_boot_fetch(local, image)

    print(f"localVersion: {info['localVersion']!r}")
    print(f"imageSha:     {info['imageSha']!r}")
    print(f"shouldBootFetchHostBundle: {info['shouldBootFetchHostBundle']}")
    print(f"bootFetchDisarmed:         {info['bootFetchDisarmed']}")
    print(f"note: {info['note']}")

    if args.check:
        if info["bootFetchDisarmed"]:
            sys.exit(0)
        sys.exit(1)


if __name__ == "__main__":
    main()
