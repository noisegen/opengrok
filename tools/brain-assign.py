#!/usr/bin/env python3
"""Toggle a Grok Bot conversation between Grok 4.6 (default) and DeepSeek.

  python3 /home/box/sand-data/brain-assign.py list
  python3 /home/box/sand-data/brain-assign.py on 71b408bd-0c94-494b-8a45-754bc0ef2d73 --name "Long Run"
  python3 /home/box/sand-data/brain-assign.py off <UUID>
  python3 /home/box/sand-data/brain-assign.py status

Takes effect on the next message (no host restart).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BINDINGS = os.environ.get(
    "BRAIN_BINDINGS", os.path.expanduser("~/sand-data/brain-bindings.json")
)
LOG = os.environ.get("BRAIN_LOG", "/tmp/sand-brain.log")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
NATIVE = {"grok", "cursor", "stock"}
LIVE = "deepseek"


def load() -> dict:
    try:
        with open(BINDINGS, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as e:
        sys.exit(f"bad JSON in {BINDINGS}: {e}")
    data.setdefault("default", "grok")
    data.setdefault("providers", {})
    data.setdefault("agents", {})
    return data


def save(data: dict) -> None:
    os.makedirs(os.path.dirname(BINDINGS) or ".", exist_ok=True)
    tmp = BINDINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, BINDINGS)


def cmd_list(_args: argparse.Namespace) -> None:
    data = load()
    print(f"file: {BINDINGS}")
    print(f"default: {data.get('default', 'grok')}")
    print(f"live hop: {LIVE}")
    agents = data.get("agents") or {}
    if not agents:
        print("deepseek bots: (none)")
        return
    print("agents:")
    for aid, ent in agents.items():
        ent = ent or {}
        name = ent.get("name") or ""
        brain = ent.get("brain") or "?"
        extra = f"  {name}" if name else ""
        print(f"  {aid}  {brain}{extra}")


def cmd_status(_args: argparse.Namespace) -> None:
    cmd_list(_args)
    if not os.path.isfile(LOG):
        print(f"log: missing {LOG} (send one message after install)")
        return
    print(f"log (last 12): {LOG}")
    lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
    for line in lines[-12:]:
        print(" ", line)


def assign(aid: str, brain: str, name: str = "") -> None:
    aid = aid.strip()
    brain = brain.lower()
    data = load()
    if brain in NATIVE:
        for k in list(data["agents"]):
            if k.lower() == aid.lower() or k == aid:
                data["agents"].pop(k, None)
        save(data)
        print(f"off {aid} -> grok")
        return
    if brain != LIVE:
        sys.exit(f"only '{LIVE}' is live. use: on <uuid>   or   off <uuid>")
    entry = data["agents"].get(aid) or {}
    entry["brain"] = LIVE
    if name:
        entry["name"] = name
    data["agents"][aid] = entry
    save(data)
    print(f"on {aid} -> {LIVE}")


def guess_self_id() -> str:
    for key in (
        "SAND_CONVERSATION_ID",
        "CONVERSATION_ID",
        "GROK_CONVERSATION_ID",
        "CURSOR_CONVERSATION_ID",
        "SAND_AGENT_ID",
    ):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    if os.path.isfile(LOG):
        try:
            lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            lines = []
        for line in reversed(lines[-40:]):
            found = UUID_RE.findall(line)
            if found:
                return found[-1]
    sys.exit(
        "could not guess this Bot id. Copy the UUID from View conversation details, then:\n"
        "  python3 /home/box/sand-data/brain-assign.py on <UUID> --name \"My Bot\""
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Toggle DeepSeek vs Grok per Bot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show DeepSeek assignments")
    sub.add_parser("status", help="list plus recent router log")

    onp = sub.add_parser("on", help="this Bot uses DeepSeek")
    onp.add_argument("id", help="conversation / Bot UUID")
    onp.add_argument("--name", default="", help="optional label")

    offp = sub.add_parser("off", help="this Bot uses Grok 4.6")
    offp.add_argument("id", help="conversation / Bot UUID")

    ons = sub.add_parser("on-self", help="DeepSeek for the current conversation if the id is known")
    ons.add_argument("--name", default="")

    args = p.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "on":
        assign(args.id, LIVE, args.name)
    elif args.cmd == "off":
        assign(args.id, "grok")
    elif args.cmd == "on-self":
        assign(guess_self_id(), LIVE, args.name)


if __name__ == "__main__":
    main()
