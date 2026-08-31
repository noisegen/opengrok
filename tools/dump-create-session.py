#!/usr/bin/env python3
"""Dump the real adapter wrapper + conversationIdKey / ALS around the hook.

Run on the box if lazy conv= is still empty after a real chat message:

  python3 /home/box/sand-data/dump-create-session.py
"""
from __future__ import annotations

import os
import re

HOST = os.path.expanduser("~/sand-host/host-main.cjs")

FN_RE = re.compile(
    r"(function\s+\w+\s*\([^)]{0,200}\)"
    r"|\w+\s*=\s*(?:async\s*)?function\s*\([^)]{0,200}\)"
    r"|(?:async\s*)?\([^)]{0,200}\)\s*=>"
    r"|function\s*\([^)]{0,200}\)"
    r"|(?:async\s+)?create\w+\s*\([^)]{0,200}\))",
    re.M,
)


def snippet(text: str, idx: int, before: int, after: int) -> str:
    if idx < 0:
        return "missing"
    return text[max(0, idx - before) : idx + after]


def main() -> None:
    t = open(HOST, encoding="utf-8", errors="replace").read()

    for needle in (
        "createLazyBrainSession",
        "createBrandedSession",
        "inferenceProvider",
    ):
        j = t.find(needle)
        print("\n=== %s @%s ===" % (needle, j))
        if j < 0:
            print("missing")
            continue
        print("--- 1500 chars before ---")
        print(snippet(t, j, 1500, 80))
        window = t[max(0, j - 4000) : j]
        matches = list(FN_RE.finditer(window))
        print("--- enclosing candidates (last 8) ---")
        if not matches:
            print("(none)")
        for m in matches[-8:]:
            start = max(0, j - 4000) + m.start()
            preview = window[m.start() : m.start() + 140].replace("\n", " ")
            print("@%s %s" % (start, preview))

    j = t.find("conversationIdKey")
    print("\n=== first conversationIdKey @%s ===" % j)
    print(snippet(t, j, 400, 500) if j >= 0 else "missing")

    for needle in ("AsyncLocalStorage", "new AsyncLocalStorage", ".getStore("):
        j = t.find(needle)
        print("\n=== %s @%s ===" % (needle, j))
        print(snippet(t, j, 300, 400) if j >= 0 else "missing")


if __name__ == "__main__":
    main()
