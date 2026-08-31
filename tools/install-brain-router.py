#!/usr/bin/env python3
"""Install the per-Bot DeepSeek toggle onto a Grok Bot cloud computer.

Prefer the small overlay files, not this installer:

  python3 patch-brain-hook.py
  (see tools/BRAIN-SETUP.txt)

Stay on ./adapters use cursor. Never ./adapters use deepseek or recover.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LONG_RUN = "71b408bd-0c94-494b-8a45-754bc0ef2d73"

OLD_HOOK = """ if (inferenceProvider !== "cursor") {
 try {
 const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
 return createXaiPromptSession({
 requestedModel,
 onRequestId,
 sessionOptions
 });
 } catch (xaiErr) {
 console.error("[sand-xai] failed to create xAI session, falling back to Cursor:", xaiErr);
 }
 }"""

BOOL_HOOK = """ const { shouldUseDeepseek } = require("./brain-router.cjs");
 if (shouldUseDeepseek(sessionOptions, requestedModel)) {
 const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
 return createXaiPromptSession({
 requestedModel,
 onRequestId,
 sessionOptions
 });
 }"""

NEW_HOOK = """ if (inferenceProvider !== "cursor") {
 try {
 const { resolveBrain, createBrandedSession } = require("./brain-router.cjs");
 const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
 let convId = "";
 try {
 if (typeof host !== "undefined" && host && typeof host.getConversationId === "function") convId = String(host.getConversationId() || "");
 } catch (e) {}
 const so = Object.assign({}, sessionOptions || {}, convId ? { conversationId: convId } : {});
 console.error("[sand-brain] conv=" + convId);
 const brain = resolveBrain(so, requestedModel);
 if (brain && brain.kind !== "native") {
 return createBrandedSession({
 requestedModel,
 onRequestId,
 sessionOptions: so,
 brain
 });
 }
 } catch (xaiErr) {
 console.error("[sand-xai] failed to create xAI session, falling back to Cursor:", xaiErr);
 }
 }"""

BRAND_INNER = """ const { resolveBrain, createBrandedSession } = require("./brain-router.cjs");
 const { createXaiPromptSession } = require("./xai-prompt-session.cjs");
 const brain = resolveBrain(sessionOptions, requestedModel);
 if (brain && brain.kind !== "native") {
 return createBrandedSession({
 requestedModel,
 onRequestId,
 sessionOptions,
 brain
 });
 }"""

STOCK_PROVIDERS = {
    "grok": {"kind": "native", "label": "Grok 4.6", "model": "grok-4.6"},
    "deepseek": {
        "kind": "openai",
        "label": "DeepSeek",
        "model": "deepseek-v4-flash",
        "baseUrl": "https://api.deepseek.com/v1",
        "keyEnv": "DEEPSEEK_API_KEY",
    },
    "openrouter": {
        "kind": "openai",
        "label": "OpenRouter",
        "model": "",
        "baseUrl": "https://openrouter.ai/api/v1",
        "keyEnv": "OPENROUTER_API_KEY",
    },
    "glm": {
        "kind": "openai",
        "label": "GLM",
        "model": "glm-5.3-flash",
        "baseUrl": "",
        "keyEnv": "ZAI_API_KEY",
    },
    "kimi": {
        "kind": "openai",
        "label": "Kimi",
        "model": "kimi-k2.5",
        "baseUrl": "https://api.moonshot.ai/v1",
        "keyEnv": "MOONSHOT_API_KEY",
    },
}

SKILL_TEXT = """Toggle this Bot between Grok 4.6 (default) and DeepSeek.

Stay on ./adapters use cursor. Never adapters use deepseek.

UUID from lazy conv= in /tmp/sand-host-manual.log.

  python3 /home/box/sand-data/brain-assign.py list
  python3 /home/box/sand-data/brain-assign.py on <UUID> --name "My Bot"
  python3 /home/box/sand-data/brain-assign.py off <UUID>

Key file: ~/sand-data/deepseek.env with DEEPSEEK_API_KEY=...

New Bots stay Grok. Clones need on again.

If intro fails: do not restart-host or recover. Quit Grok Bot and reopen.
"""

# pack-brain.py fills these in brain-install-bundle.py
EMBED_ROUTER = ""
EMBED_ASSIGN = ""


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _closing_brace(s: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    in_str = None
    escape = False
    while i < len(s):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
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


def _dump_hook(text: str) -> None:
    idx = text.find("createXaiPromptSession")
    if idx < 0:
        print("no createXaiPromptSession in host-main.cjs", file=sys.stderr)
        return
    snippet = text[max(0, idx - 240) : idx + 420]
    print("--- hook snippet ---", file=sys.stderr)
    print(snippet, file=sys.stderr)
    print("--------------------", file=sys.stderr)


def patch_host(text: str) -> str:
    if "createLazyBrainSession" in text:
        return text
    if "createBrandedSession" in text:
        return text
    if "createXaiPromptSession" not in text:
        die("createXaiPromptSession hook missing — run ./adapters patch-host first")
    if BOOL_HOOK in text:
        return text.replace(BOOL_HOOK, BRAND_INNER, 1)

    m = re.search(
        r"const\s*\{\s*shouldUseDeepseek\s*\}\s*=\s*require\(\s*[\"']\./brain-router\.cjs[\"']\s*\)\s*;"
        r"\s*if\s*\(\s*shouldUseDeepseek\s*\(\s*sessionOptions\s*,\s*requestedModel\s*\)\s*\)\s*\{",
        text,
    )
    if m:
        open_idx = m.end() - 1
        close = _closing_brace(text, open_idx)
        if close < 0:
            die("unclosed shouldUseDeepseek block")
        return text[: m.start()] + BRAND_INNER + text[close + 1 :]

    if OLD_HOOK in text:
        return text.replace(OLD_HOOK, NEW_HOOK, 1)

    m = re.search(
        r"if\s*\(\s*inferenceProvider\s*!==\s*[\"']cursor[\"']\s*\)\s*\{",
        text,
    )
    if m:
        open_idx = m.end() - 1
        close = _closing_brace(text, open_idx)
        if close < 0:
            die("unclosed inferenceProvider block")
        block = text[m.start() : close + 1]
        if "createXaiPromptSession" not in block:
            die("inferenceProvider if-block has no createXaiPromptSession")
        return text[: m.start()] + NEW_HOOK.strip() + text[close + 1 :]

    _dump_hook(text)
    die("hook text did not match known grok-bot-setup shape")


def seed_bindings(path: str, agent_id: str, name: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("default", "grok")
    data.setdefault("identity", True)
    data.setdefault("providers", {})
    for key, spec in STOCK_PROVIDERS.items():
        data["providers"].setdefault(key, spec)
    data.setdefault("agents", {})
    ent = data["agents"].get(agent_id) or {}
    ent["brain"] = "deepseek"
    ent["name"] = name
    data["agents"][agent_id] = ent
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"bindings: {path} ({name} -> deepseek)")


def restore_default_model(settings_path: str, model: str) -> None:
    if not os.path.isfile(settings_path):
        print(f"settings: skip (missing {settings_path})")
        return
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
    adm = data.get("agentDefaultModel") or {}
    old = adm.get("modelId")
    adm["modelId"] = model
    if "maxMode" not in adm:
        adm["maxMode"] = True
    data["agentDefaultModel"] = adm
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"settings: agentDefaultModel {old} -> {model}")


def preflight(sand: str, host_dir: str) -> None:
    env_path = os.path.join(sand, "xai-inference.env")
    key_path = os.path.join(sand, "deepseek.env")
    if not os.path.isfile(env_path):
        print(
            "note: missing xai-inference.env — stay on cursor. "
            "Put DEEPSEEK_API_KEY in ~/sand-data/deepseek.env. Never ./adapters use deepseek."
        )
    else:
        blob = open(env_path, encoding="utf-8", errors="replace").read()
        if "SAND_INFERENCE_PROVIDER=cursor" in blob:
            print(
                "note: SAND_INFERENCE_PROVIDER=cursor — that is correct. "
                "Overlay runs on the cursor path. Never ./adapters use deepseek."
            )
        elif "api.deepseek.com" in blob or "SAND_INFERENCE_PROVIDER=xai" in blob:
            print(
                "warning: global xAI/DeepSeek hop is set. "
                "Stay on ./adapters use cursor; assigned Bots hop via deepseek.env."
            )
    if not os.path.isfile(key_path):
        print("note: missing deepseek.env — assigned Bots will stay on Grok until a key is present")
    session = os.path.join(host_dir, "xai-prompt-session.cjs")
    if not os.path.isfile(session):
        die(f"missing {session} — run ./adapters patch-host first")


def write_file(src: str, dest: str, fallback: str | None = None) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if (not src or not os.path.isfile(src)) and dest.endswith("brain-router.cjs") and EMBED_ROUTER:
        fallback = EMBED_ROUTER
        src = ""
    if (not src or not os.path.isfile(src)) and dest.endswith("brain-assign.py") and EMBED_ASSIGN:
        fallback = EMBED_ASSIGN
        src = ""
    if src and os.path.isfile(src):
        shutil.copy2(src, dest)
    elif fallback is not None:
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(fallback)
            if not fallback.endswith("\n"):
                f.write("\n")
    else:
        die(f"missing {src or dest}")
    print(f"wrote {dest}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=HERE)
    ap.add_argument("--host-dir", default=os.path.expanduser("~/sand-host"))
    ap.add_argument("--sand", default=os.path.expanduser("~/sand-data"))
    ap.add_argument("--agent-id", default=LONG_RUN)
    ap.add_argument("--agent-name", default="Long Run")
    ap.add_argument("--default-model", default="grok-4.6")
    args = ap.parse_args()

    if os.name == "nt":
        die("run this on the Grok Bot Linux computer, not Windows")

    preflight(args.sand, args.host_dir)

    router_src = os.path.join(args.src, "brain-router.cjs")
    assign_src = os.path.join(args.src, "brain-assign.py")
    skill_src = os.path.join(args.src, "brain-skill.txt")
    host_main = os.path.join(args.host_dir, "host-main.cjs")
    if not os.path.isfile(host_main):
        die(f"missing {host_main}")

    write_file(router_src, os.path.join(args.host_dir, "brain-router.cjs"))
    write_file(assign_src, os.path.join(args.sand, "brain-assign.py"))
    try:
        os.chmod(os.path.join(args.sand, "brain-assign.py"), 0o755)
    except OSError:
        pass
    skill_body = SKILL_TEXT
    if os.path.isfile(skill_src):
        skill_body = open(skill_src, encoding="utf-8").read()
    write_file("", os.path.join(args.sand, "brain-skill.txt"), fallback=skill_body)

    seed_bindings(os.path.join(args.sand, "brain-bindings.json"), args.agent_id, args.agent_name)

    text = open(host_main, encoding="utf-8", errors="surrogateescape").read()
    new = patch_host(text)
    if new != text:
        bak = host_main + ".brain-router.bak"
        if not os.path.isfile(bak):
            shutil.copy2(host_main, bak)
            print(f"backup {bak}")
        open(host_main, "w", encoding="utf-8", errors="surrogateescape").write(new)
        print(f"patched {host_main}")
    else:
        print("hook: already patched")
    body = open(host_main, encoding="utf-8", errors="surrogateescape").read()
    if "createLazyBrainSession" not in body and "createBrandedSession" not in body:
        die("patch applied but overlay hook not found")

    restore_default_model(os.path.join(args.sand, "settings.json"), args.default_model)

    print()
    print("Installed. One restart only (laptop awake):")
    print("  cd ~/grok-bot-setup && ./adapters restart-host")
    print("Stay on ./adapters use cursor. Never use deepseek or recover.")
    print("Unassigned Bots stay Grok; assigned hop via ~/sand-data/deepseek.env")
    print("Toggle: python3 ~/sand-data/brain-assign.py on|off <UUID>")


if __name__ == "__main__":
    main()
