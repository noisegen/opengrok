#!/usr/bin/env python3
"""Patch host-main.cjs so per-Bot DeepSeek runs even when provider is cursor.

Stay on ./adapters use cursor. Never adapters use deepseek or recover.
Unassigned Bots get a raw Cursor session (no Proxy). Assigned hop via
~/sand-data/deepseek.env.

  python3 /home/box/sand-data/patch-brain-hook.py
  Fully Quit Grok Bot and reopen. Do not ./adapters restart-host.
  grep -F '[sand-brain]' /tmp/sand-host-manual.log | tail -n 8
"""
from __future__ import annotations

import os
import re
import shutil
import sys

HOST = os.path.expanduser("~/sand-host/host-main.cjs")
ROUTER = os.path.expanduser("~/sand-host/brain-router.cjs")

NEW = """try {
 /* sand-brain pass-through */
 const { createLazyBrainSession } = require("./brain-router.cjs");
 const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
 let cidKey;
 let getCid;
 try { cidKey = conversationIdKey; } catch (e) {}
 try { getCid = getConversationId; } catch (e) {}
 return createLazyBrainSession({
  requestedModel,
  onRequestId,
  sessionOptions,
  conversationIdKey: cidKey,
  getConversationId: getCid,
  nativeFactory: function (so, rid) {
   const cb = typeof rid === "function" ? rid : onRequestId;
   if (typeof createCursorInferencePromptSession === "function") {
    return createCursorInferencePromptSession({ requestedModel, onRequestId: cb, sessionOptions: so });
   }
   throw new Error("sand-brain: no native factory");
  }
 });
} catch (sandErr) {
 console.error("[sand-brain] overlay failed, native:", sandErr);
 if (typeof createCursorInferencePromptSession === "function") {
  return createCursorInferencePromptSession({ requestedModel, onRequestId, sessionOptions });
 }
}
if (inferenceProvider !== "cursor") {
 try {
  const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
  return createXaiPromptSession({ requestedModel, onRequestId, sessionOptions });
 } catch (xaiErr) {
  console.error("[sand-xai] failed to create xAI session, falling back to native:", xaiErr);
 }
}"""


def die(msg: str) -> None:
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)


def closing_brace(s: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    in_str = None
    esc = False
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
        elif c in "\"'`":
            in_str = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _is_current(block: str) -> bool:
    if "innerFactory" in block or "factoryFn" in block:
        return False
    if "falling back to Cursor" in block:
        return False
    if "wrappedOnRequestId" in block:
        return False
    if "return createXaiPromptSession({ requestedModel, onRequestId: cb" in block:
        return False
    return (
        "sand-brain pass-through" in block
        and "createLazyBrainSession" in block
        and "overlay failed, native" in block
        and "createXaiPromptSession" in block
        and "createCursorInferencePromptSession" in block
    )


def patch(text: str) -> str:
    if "createXaiPromptSession" not in text:
        die("createXaiPromptSession missing — run ./adapters patch-host")
    m = re.search(
        r"if\s*\(\s*inferenceProvider\s*!==\s*[\"']cursor[\"']\s*\)\s*\{",
        text,
    )
    m_try = re.search(
        r"try\s*\{\s*(?:/\*[\s\S]*?\*/\s*)?const\s*\{\s*createLazyBrainSession",
        text,
    )
    # Already-healthy (or stale) lazy overlay sits BEFORE the inferenceProvider if.
    if m_try and m and m_try.start() < m.start():
        close = closing_brace(text, m.end() - 1)
        if close < 0:
            die("unclosed if-block")
        block = text[m_try.start() : close + 1]
        if _is_current(block):
            return text  # idempotent no-op
        return text[: m_try.start()] + NEW + text[close + 1 :]
    if "sand-brain pass-through" in text and "createLazyBrainSession" in text:
        # Marker present but we could not locate a replaceable try+if pair — drift.
        die(
            "sand-brain markers present but hook shape unmatched — "
            "upstream host drift; refusing to half-patch"
        )
    if not m:
        idx = text.find("createXaiPromptSession")
        print(text[max(0, idx - 200) : idx + 400] if idx >= 0 else "no hook")
        die("could not find inferenceProvider !== cursor")
    close = closing_brace(text, m.end() - 1)
    if close < 0:
        die("unclosed if-block")
    block = text[m.start() : close + 1]
    if "createXaiPromptSession" not in block and "createBrandedSession" not in block:
        die("that if-block is not the xAI hook")
    return text[: m.start()] + NEW + text[close + 1 :]


def main() -> None:
    if not os.path.isfile(ROUTER):
        die("missing " + ROUTER)
    if not os.path.isfile(HOST):
        die("missing " + HOST)
    text = open(HOST, encoding="utf-8", errors="surrogateescape").read()
    new = patch(text)
    if new == text:
        print("already patched with lazy brain session")
    else:
        bak = HOST + ".brain-router.bak"
        if not os.path.isfile(bak):
            shutil.copy2(HOST, bak)
            print("backup", bak)
        open(HOST, "w", encoding="utf-8", errors="surrogateescape").write(new)
        print("patched", HOST)
    body = open(HOST, encoding="utf-8", errors="surrogateescape").read()
    if "createLazyBrainSession" not in body:
        die("createLazyBrainSession still missing")
    if "createXaiPromptSession" not in body:
        die("createXaiPromptSession still missing")
    print("ok")
    print("next: fully Quit Grok Bot and reopen. Do not ./adapters restart-host")
    print("then: grep -F '[sand-brain]' /tmp/sand-host-manual.log | tail -n 8")
    print("want where=none or where=store — not where=stream")


if __name__ == "__main__":
    main()
