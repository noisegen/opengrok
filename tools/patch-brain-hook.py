#!/usr/bin/env python3
"""Patch host-main.cjs so per-Bot DeepSeek runs on cursor (both host shapes).

Stay on ./adapters use cursor. Never adapters use deepseek or recover.
Unassigned Bots get a raw Cursor session (no Proxy). Assigned hop via
~/sand-data/deepseek.env.

Supports TWO stock host shapes (prefer xAI locator when present):

  A) grok-bot-setup — has `if (inferenceProvider !== "cursor")` + createXaiPromptSession
  B) recovered Cursor-native (post Computer recover) — only
     `const session = createCursorInferencePromptSession({...}); return session;`
     No xAI branch, no xai-prompt-session.cjs. Wrap that call/return only.

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

# Shape A — grok-bot-setup (xAI branch present). Inserted in place of the
# inferenceProvider !== "cursor" block; keeps a fail-closed xAI fallback after.
NEW_XAI = """try {
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

# Alias for older tests / importers that expect NEW.
NEW = NEW_XAI


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


def detect_shape(text: str) -> str | None:
    """Return 'xai', 'cursor-native', or None if unpatchable."""
    if (
        "createXaiPromptSession" in text
        and re.search(
            r"if\s*\(\s*inferenceProvider\s*!==\s*[\"']cursor[\"']\s*\)\s*\{",
            text,
        )
    ):
        return "xai"
    if find_cursor_native_call(text) is not None:
        return "cursor-native"
    return None


def _is_current_xai(block: str) -> bool:
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


def _is_current_cursor(block: str) -> bool:
    if "wrappedOnRequestId" in block or "innerFactory" in block:
        return False
    return (
        "sand-brain pass-through" in block
        and "createLazyBrainSession" in block
        and "overlay failed, native" in block
        and "createCursorInferencePromptSession" in block
        and "nativeFactory" in block
        and "pickSandBrainIds" in block
        and "options2:" in block
    )


# Back-compat name used by older call sites / mental model.
def _is_current(block: str) -> bool:
    return _is_current_xai(block) or _is_current_cursor(block)


def find_cursor_native_call(text: str):
    """Locate the createCursorInferencePromptSession *call* (not the definition).

    Production recovered host (version 112ba04) has exactly one wrap site:
      const session = createCursorInferencePromptSession({ getAccessToken: options2...});
      return session;
    """
    pattern = re.compile(r"const\s+session\s*=\s*createCursorInferencePromptSession\s*\(")
    candidates = []
    for m in pattern.finditer(text):
        # Skip if this is already inside a sand-brain overlay (look back a bit).
        lookback = text[max(0, m.start() - 400) : m.start()]
        # Definition forms never match `const session = createCursor...`.
        window = text[m.start() : m.start() + 1200]
        if "return session" not in window:
            continue
        # Prefer the options2.getAccessToken production shape; accept any single hit.
        score = 0
        if "getAccessToken" in window:
            score += 2
        if "options2" in window:
            score += 2
        if "requestedModel" in window:
            score += 1
        candidates.append((score, m))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    best_score = candidates[0][0]
    top = [m for s, m in candidates if s == best_score]
    if len(top) != 1:
        return None  # ambiguous — caller dies loudly
    return top[0]


def _cursor_call_span(text: str, m: re.Match) -> tuple[int, int, str]:
    """Return (start, end_exclusive, object_literal_including_braces) for the call."""
    # m ends at '(' after createCursorInferencePromptSession
    paren = m.end() - 1  # index of '('
    # skip whitespace to '{'
    i = paren + 1
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        die("cursor-native call: expected object literal after createCursorInferencePromptSession(")
    obj_end = closing_brace(text, i)
    if obj_end < 0:
        die("cursor-native call: unclosed object literal")
    # expect ); then return session;
    j = obj_end + 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j >= len(text) or text[j] != ")":
        die("cursor-native call: expected ) after object literal")
    j += 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j >= len(text) or text[j] != ";":
        die("cursor-native call: expected ; after )")
    j += 1
    rest = text[j : j + 80]
    rm = re.match(r"\s*return\s+session\s*;", rest)
    if not rm:
        die("cursor-native call: expected return session; after createCursorInferencePromptSession")
    end = j + rm.end()
    obj_lit = text[i : obj_end + 1]
    return m.start(), end, obj_lit


def _native_factory_args(obj_lit: str) -> str:
    """Reuse stock object literal inside nativeFactory; wire onRequestId to cb.

    Shadows sessionOptions = so so lineage/inferenceReason pick up the lazy so.
    """
    # Shorthand `onRequestId,` or bare `onRequestId` before closing brace
    out = re.sub(
        r"(\n\s*)onRequestId(\s*,)",
        r"\1onRequestId: cb\2",
        obj_lit,
        count=1,
    )
    if out == obj_lit:
        out = re.sub(
            r"(\n\s*)onRequestId(\s*\n\s*\})",
            r"\1onRequestId: cb\2",
            obj_lit,
            count=1,
        )
    if out == obj_lit:
        # Already `onRequestId: something` — force cb
        out2 = re.sub(
            r"(\n\s*)onRequestId\s*:\s*[^,\n]+",
            r"\1onRequestId: cb",
            obj_lit,
            count=1,
        )
        if out2 == obj_lit:
            die("cursor-native call: could not rewrite onRequestId for nativeFactory")
        out = out2
    return out


def _build_cursor_overlay(obj_lit: str, indent: str) -> str:
    factory_args = _native_factory_args(obj_lit)
    # Keep indentation of the surrounding block (production uses 6 spaces).
    # Pass every reachable bot id into createLazyBrainSession. conversationIdKey /
    # getConversationId are often OUT OF SCOPE on recovered Cursor-native hosts;
    # options2 + sessionOptions + zero-arg getters are what isolation actually has.
    lines = [
        "try {",
        " /* sand-brain pass-through */",
        ' const { createLazyBrainSession } = require("./brain-router.cjs");',
        " let cidKey;",
        " let getCid;",
        " try { cidKey = conversationIdKey; } catch (e) {}",
        " try { getCid = getConversationId; } catch (e) {}",
        " const __sandBag = (function pickSandBrainIds() {",
        "  const bag = {};",
        "  const srcs = [];",
        '  try { if (typeof sessionOptions !== "undefined" && sessionOptions) srcs.push(sessionOptions); } catch (e) {}',
        '  try { if (typeof options2 !== "undefined" && options2) srcs.push(options2); } catch (e) {}',
        "  for (const src of srcs) {",
        "   if (!src || typeof src !== \"object\") continue;",
        '   for (const k of ["agentId","agent_id","conversationId","conversation_id","bcId","bc_id","botId","bot_id","provenanceAgentId"]) {',
        "    try { if (!bag[k] && typeof src[k] === \"string\" && src[k]) bag[k] = src[k]; } catch (e) {}",
        "   }",
        '   for (const g of ["getAgentId","getBotId","getConversationId","getBcId","getAgentBCId"]) {',
        "    try {",
        "     if (typeof src[g] === \"function\" && src[g].length === 0) {",
        "      const v = src[g]();",
        '      if (typeof v === "string" && v) {',
        '       if (!bag.agentId && /agent/i.test(g)) bag.agentId = v;',
        '       else if (!bag.conversationId && /conversation/i.test(g)) bag.conversationId = v;',
        '       else if (!bag.bcId && /bc/i.test(g)) bag.bcId = v;',
        '       else if (!bag.botId && /bot/i.test(g)) bag.botId = v;',
        "       else if (!bag.agentId) bag.agentId = v;",
        "      }",
        "     }",
        "    } catch (e) {}",
        "   }",
        "  }",
        "  return bag;",
        " })();",
        " return createLazyBrainSession({",
        "  requestedModel,",
        "  onRequestId,",
        "  sessionOptions: Object.assign({}, typeof sessionOptions !== \"undefined\" && sessionOptions ? sessionOptions : {}, __sandBag),",
        '  options2: typeof options2 !== "undefined" ? options2 : void 0,',
        "  conversationIdKey: cidKey,",
        "  getConversationId: getCid,",
        "  getBotId: function (ctx) {",
        "   try { if (typeof options2 !== \"undefined\" && options2 && typeof options2.getAgentId === \"function\") { const v = options2.getAgentId(ctx); if (v) return String(v); } } catch (e) {}",
        "   try { if (typeof options2 !== \"undefined\" && options2 && typeof options2.getBotId === \"function\") { const v = options2.getBotId(ctx); if (v) return String(v); } } catch (e) {}",
        "   try { if (__sandBag.agentId) return __sandBag.agentId; } catch (e) {}",
        '   return "";',
        "  },",
        "  nativeFactory: function (so, rid) {",
        '   const cb = typeof rid === "function" ? rid : onRequestId;',
        "   const sessionOptions = so;",
        f"   return createCursorInferencePromptSession({factory_args});",
        "  }",
        " });",
        "} catch (sandErr) {",
        ' console.error("[sand-brain] overlay failed, native:", sandErr);',
        f" const session = createCursorInferencePromptSession({obj_lit});",
        " return session;",
        "}",
    ]
    # Prefix each line with indent; first line uses indent as-is.
    return "\n".join(indent + ln if ln else indent for ln in lines)


def patch_cursor_native(text: str) -> str:
    # Fast idempotent path: a current cursor overlay already owns the wrap site.
    for m_try in re.finditer(
        r"try\s*\{\s*/\*\s*sand-brain pass-through\s*\*/",
        text,
    ):
        window = text[m_try.start() : m_try.start() + 4000]
        if _is_current_cursor(window) and "const session = createCursorInferencePromptSession" in window:
            return text

    m_call = find_cursor_native_call(text)
    if m_call is None:
        if "sand-brain pass-through" in text and "createLazyBrainSession" in text:
            die(
                "sand-brain markers present but cursor-native hook shape unmatched — "
                "upstream host drift; refusing to half-patch"
            )
        die("could not find cursor-native createCursorInferencePromptSession call site")

    # Stale sand-brain try immediately before this call → replace from that try.
    lookback_start = max(0, m_call.start() - 4000)
    lookback = text[lookback_start : m_call.start()]
    abs_try = None
    for m in re.finditer(
        r"try\s*\{\s*/\*\s*sand-brain pass-through\s*\*/",
        lookback,
    ):
        abs_try = lookback_start + m.start()

    start, end, obj_lit = _cursor_call_span(text, m_call)
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    overlay = _build_cursor_overlay(obj_lit, indent)

    if abs_try is not None:
        # Replace stale overlay (from its try through the stock call in catch).
        return text[:abs_try] + overlay + text[end:]

    # Fresh stock call: replace from the start of the const-session line.
    return text[:line_start] + overlay + text[end:]


def patch_xai(text: str) -> str:
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
        if _is_current_xai(block):
            return text  # idempotent no-op
        return text[: m_try.start()] + NEW_XAI + text[close + 1 :]
    if "sand-brain pass-through" in text and "createLazyBrainSession" in text:
        die(
            "sand-brain markers present but xAI hook shape unmatched — "
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
    return text[: m.start()] + NEW_XAI + text[close + 1 :]


def patch(text: str) -> str:
    shape = detect_shape(text)
    # Prefer xAI locator when present (even if cursor call also exists).
    if shape == "xai":
        return patch_xai(text)
    if shape == "cursor-native":
        return patch_cursor_native(text)
    # Markers without a recognizable shape = drift.
    if "sand-brain pass-through" in text or "createLazyBrainSession" in text:
        die(
            "sand-brain markers present but host shape unmatched — "
            "upstream host drift; refusing to half-patch"
        )
    die(
        "unrecognized host-main shape — need either createXaiPromptSession+"
        "inferenceProvider (grok-bot-setup) or const session = "
        "createCursorInferencePromptSession(...); return session "
        "(recovered Cursor-native). Refusing to half-patch"
    )


def main() -> None:
    if not os.path.isfile(ROUTER):
        die("missing " + ROUTER)
    if not os.path.isfile(HOST):
        die("missing " + HOST)
    text = open(HOST, encoding="utf-8", errors="surrogateescape").read()
    shape = detect_shape(text)
    if shape:
        print(f"host shape: {shape}")
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
    if "overlay failed, native" not in body:
        die("fail-closed native catch still missing")
    if "createCursorInferencePromptSession" not in body:
        die("createCursorInferencePromptSession still missing")
    print("ok")
    print("next: fully Quit Grok Bot and reopen. Do not ./adapters restart-host")
    print("then: grep -F '[sand-brain]' /tmp/sand-host-manual.log | tail -n 8")
    print("want where=none or where=store — or where=stream if id arrives late")


if __name__ == "__main__":
    main()
