#!/usr/bin/env python3
"""Print hook + getConversationId call sites from host-main.cjs. Run on the box."""
from __future__ import annotations

import os

HOST = os.path.expanduser("~/sand-host/host-main.cjs")
LOG = "/tmp/sand-host-manual.log"


def main() -> None:
    t = open(HOST, encoding="utf-8", errors="replace").read()
    print("getConversationId count:", t.count("getConversationId"))
    print("createBrandedSession count:", t.count("createBrandedSession"))
    print("options2keys in hook:", "options2keys=" in t)
    i = 0
    n = 0
    while n < 8:
        j = t.find("getConversationId", i)
        if j < 0:
            break
        print(f"\n--- getConversationId @{j} ---")
        print(t[max(0, j - 80) : j + len("getConversationId") + 80])
        i = j + 1
        n += 1
    k = t.find("createBrandedSession")
    print("\n--- createBrandedSession context ---")
    print(t[max(0, k - 700) : k + 900] if k >= 0 else "missing")
    if os.path.isfile(LOG):
        print("\n--- sand-brain lines from host log ---")
        for line in open(LOG, encoding="utf-8", errors="replace"):
            if "[sand-brain]" in line:
                print(line.rstrip())


if __name__ == "__main__":
    main()
