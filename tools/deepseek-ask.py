#!/usr/bin/env python3
"""Ask DeepSeek V4 Flash. Key never printed.

Windows:  python %USERPROFILE%\\.grokbot\\deepseek-ask.py "your prompt"
Linux box: python3 /home/box/sand-data/deepseek-ask.py "your prompt"
Stdin if no args.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

UPSTREAM = os.environ.get("DEEPSEEK_HOP_UPSTREAM", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
ENV_CANDIDATES = [
    os.path.join(os.environ.get("USERPROFILE", ""), ".grokbot", "deepseek.env"),
    os.path.join(os.environ.get("HOME", ""), ".grokbot", "deepseek.env"),
    "/home/box/sand-data/deepseek.env",
]


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    for path in ENV_CANDIDATES:
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            continue
    raise SystemExit("no DEEPSEEK_API_KEY in env or deepseek.env")


def ask(prompt: str, max_tokens: int = 512) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }).encode()
    req = urllib.request.Request(
        UPSTREAM + "/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + load_key(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise SystemExit("DeepSeek HTTP %s" % exc.code) from None
    except Exception as exc:
        raise SystemExit("%s" % type(exc).__name__) from None
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise SystemExit("unexpected DeepSeek response") from None


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
    if not prompt:
        raise SystemExit("usage: deepseek-ask.py <prompt>")
    sys.stdout.write(ask(prompt) + "\n")


if __name__ == "__main__":
    main()
