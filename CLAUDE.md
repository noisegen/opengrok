# CLAUDE.md

opengrok is a zero-dependency toolkit that makes Grok Bot agents speak a foreign model's native wire (xAI, GLM, Claude plans, Gemini, DeepSeek, local llama.cpp), keep keys off the binding file, and survive silent host updates. This file is for a **headless, one-shot Claude Opus escalation agent**: orient here, then open the cited file. Do not rediscover the tree. This checkout's remote is `noisegen/opengrok` (MIT). There is no prior `CLAUDE.md`, `AGENTS.md`, or `.cursor` ruleset.

## Layout

- `setup.py` — interactive detect → plan → wire bindings/`services.json`/`doctor --init` → picker on `:8766`
- `tools/provider-maps.cjs` — **Contract A**: `applyProviderReasoningControls(body, ctx)` (what the box hop calls after the host patch)
- `tools/provider-maps-hop.cjs` — **Contract B**: `applyHarnessControls(input)` → `{body, route, applied, unknownIds}`
- `tools/test-provider-maps.cjs` / `tools/test-provider-maps-hop.cjs` — Contract A (23) / B (6)
- `tools/qa.py` — py_compile + `node --check` on `*.cjs` + JSON parse + md cross-refs + leak scan + **Contract A only**
- `tools/doctor.py` — services / bindings / SHA drift / grok cache / Windows VBS persistence (`services.json` + `baseline.json`)
- `tools/model-picker.py` — local picker UI (`127.0.0.1:8766`); `/api/test` probes the hop from this machine
- `tools/wire-probe.py` — 7-probe ladder (P0 models + P1–P7 thinking/effort)
- `tools/apply-box-patch.py` — surgical host+hop binding consumer (stock `sand-host` ignores bindings)
- `tools/file-relay.py` — box `BOX_RELAY_URL` (`POST /push/<name>`, `GET /pull/<name>`, `GET /health`)
- `tools/hop-server.py` — Hermes hop `:18790` (injects `API_SERVER_KEY`; **not** the file relay)
- `tools/deepseek-hop.py` — DeepSeek hop `:18791` (`DEEPSEEK_API_KEY` or `~/.grokbot/deepseek.env`)
- `tools/brain-router.cjs`, `tools/patch-brain-hook.py`, `tools/brain-assign.py` — per-Bot DeepSeek overlay; operator path is `tools/BRAIN-SETUP.txt`
- `tools/test-brain-router.cjs`, `tools/test-brain-install.py`, `tools/test-brain-assign.py` — overlay tests (**not in CI**)
- `wire-captures/` — shipped evidence (`glm-5.3-flash/` today)
- `examples/` — `model-bindings.example.json`, `brain-bindings.example.json`, hop-health shape
- `docs/` — `MODEL-GUIDELINES.md`, `TESTING.md`, `CLOUD-HOST.md`, `FAILURE-MODES.md`, `BYOK-DECISION.md`, `ROADMAP.md`
- `voice/` — self-contained realtime stack (ears / captain / mouth). Not hooked into `setup.py`
- `.github/workflows/verify.yml` — CI: A + B + `qa.py` (Node 24, Python 3.11)
- `.contracts/validation-contract.md` — voice sanitization + V-gates

## Commands (verified on this Linux agent: Python 3.12.3, Node v22.14.0)

`python` is **not** on PATH here. Every command below was run as written.

```
node tools/test-provider-maps.cjs                 # 23/23 pass
node tools/test-provider-maps-hop.cjs             # 6/6 hop-pass
node tools/test-brain-router.cjs                  # prints ok
python3 tools/test-brain-install.py               # 19 tests OK
python3 tools/test-brain-assign.py                # 2 tests OK
python3 tools/qa.py                               # QA: 0 fail (does not run hop or brain suites)
python3 tools/doctor.py                           # runnable; exit 2 here (hop down, no bindings)
python3 tools/doctor.py --quiet                   # silent only when clean; same exit codes (0/1/2)
node voice/doctor.js                              # runnable; exit 1 without voice/.env
node voice/scripts/leak-scan.js                   # CLEAN
node --check voice/*.js voice/*.cjs voice/lib/*.cjs voice/panel/*.js voice/scripts/*.js
python3 tools/model-picker.py --port 8767 --bindings <tmp-json>   # GET / → 200; GET /api/state → JSON
python3 tools/file-relay.py --dir /tmp/relay --port 8798          # /health /push /pull work
VOICE_GW_PORT=18794 node voice/supervisor.cjs     # /health ok; kids spawn; elevenlabs:false without key
python3 tools/apply-box-patch.py --help
python3 tools/wire-probe.py --help
```

**Not run / could not verify**

- `python setup.py` / `python3 setup.py` — interactive; writes bindings, overwrites `services.json`, writes `baseline.json`, opens picker
- Live `tools/wire-probe.py --base …` — needs a provider key; 7 metered calls
- `tools/apply-box-patch.py` against a real host — defaults `/home/box/sand-host/host-main.cjs` (absent here)
- `tools/install-on-box.sh`, `tools/install-brain-router.py`, `tools/patch-brain-hook.py` — box filesystem
- `voice/scripts/start-voice.ps1`, `tools/start-deepseek-hop.ps1` — Windows PowerShell
- Live hop / picker Test / Grok Bot chat turn
- No `package.json`, `requirements.txt`, linter, or build. Clone + `python3` + `node` is the whole toolchain.

## Install / apply rules (from code)

**Maps.** No capture, no merge. Gap-fill only (never overwrite a caller-set field). Unknown effort token → leave provider default. No verified off-switch → `fast` is an explicit noop with a reason string. Unverified route: Contract A returns `"none"` and leaves the body untouched; Contract B writes `applied.wire` noops. **200-accepted ≠ honored** — prove knobs via reasoning tokens/content. Cite `// live-verified YYYY-MM-DD (wire-captures/<slug>/)`.

**`tools/apply-box-patch.py` (run on the box).** `node --check` the **full** host and hop files before any write, and again after. Timestamped backup dir beside the hop (`harness-shim-backups-<UTC>`). Each anchor must occur exactly once; changed upstream → die (no half-patch). Idempotent. Then supervisor-safe bounce — not `kill -9`. Picker Test is **not** routing proof; a normal chat turn must hit the hop port.

**Brain overlay (`tools/BRAIN-SETUP.txt` — prefer over `tools/brain-install-bundle.py`).** Stay on `./adapters use cursor`. Never `use deepseek`, `recover`, or `restart-host`. Copy `tools/brain-router.cjs` → `~/sand-host/brain-router.cjs` and `tools/patch-brain-hook.py` + `tools/brain-assign.py` → `~/sand-data/`. Key file `~/sand-data/deepseek.env` (`DEEPSEEK_API_KEY=`, mode 600). `patch-brain-hook.py` writes `host-main.cjs.brain-router.bak` once. Overlay errors **fail closed to native** (`overlay failed, native`); hop no-key / no-fetch / unavailable → native. Fully Quit Grok Bot and reopen. Log line `[sand-brain] … where=none|store` (not `where=stream`). `python3 ~/sand-data/brain-assign.py on|off <uuid>` applies on the next message. Clone = new UUID → assign again. `BRAIN_LIVE` default `deepseek`; other catalog brains store but stay Grok until enabled.

**Secrets / bindings.** Ports and slugs only. `hopBaseUrl` is exactly `http://127.0.0.1:<port>/v1`. `provider` is audit metadata. `parameters` is `[{id,value}]` strings. Shims never log Authorization or bodies.

**Voice.** `node voice/doctor.js` then `node voice/supervisor.cjs` (Windows: the ps1). Missing creds → guided FAIL, not a stack as the first line. Mouth without ElevenLabs still boots (`elevenlabs:false`). `.contracts`: no personal home paths, no baked tokens, leak-scan CLEAN.

## Traps / invariants

- `tools/wire-probe.py` sets `HERE` to **its own directory**, so dumps land next to the script (`wire-captures/<slug>/` under `tools/`), not repo-root `wire-captures/`. Copy into `wire-captures/<slug>/` before the PR.
- `tools/qa.py` does not run Contract B, brain tests, or `node --check` on `voice/*.js`. CI adds B only.
- `services.json` is machine-local; `setup.py` overwrites it. The committed copy may point at a host-specific Windows bindings path. Doctor prefers it when present.
- Header comment in `tools/provider-maps.cjs` still calls GLM a stub — **GLM is implemented**. Qwen / mimo / hermes-agent still return `"none"`.
- `docs/CLOUD-HOST.md` §5 names `tools/hop-server.py` as the file relay — **wrong**. Relay is `tools/file-relay.py`.
- `docs/MODEL-GUIDELINES.md` "17/17" is stale; Contract A is 23/23.
- "Maps hot-reload" is a host-shim comment, not code in this repo. Do not assume no-restart map updates.
- Bound-route errors must not silently fall through to stock Grok (`docs/FAILURE-MODES.md` F12). Brain overlay fails **to native** so a broken hop does not hold a dead session.
- Windows native flags: single slash; verify port/file effect, never exit code alone (F02).
- Live probes burn quota — unit tests first; one approved ladder last (`docs/TESTING.md`). Auth boundary: hop-with-key succeeds **and** keyless is rejected.
- `voice/supervisor.cjs` listens on `0.0.0.0` (default `:18793`). Do not expose without your own auth.

## Tests / fixtures — how to add one

1. Probe: `python3 tools/wire-probe.py --base <url> --model <slug> --key-env <ENV>`. Copy the script-local dump into `wire-captures/<slug>/{capture,verdict}.json`.
2. Write the route in `tools/provider-maps.cjs` and/or `tools/provider-maps-hop.cjs` (follow GLM).
3. Add a `check("name", fn)` in the matching test file: happy path, off-switch or documented noop, silent/minimal-intervention, gap-fill (caller field preserved).
4. Negative control: revert the map, watch the new test FAIL, restore byte-identical.
5. `node tools/test-provider-maps.cjs && node tools/test-provider-maps-hop.cjs && python3 tools/qa.py`
6. Overlay: add asserts in `tools/test-brain-router.cjs` or a `unittest` in `tools/test-brain-install.py` / `tools/test-brain-assign.py`. Run them locally — CI will not.
7. Voice: `node --check` the file; `node voice/scripts/leak-scan.js` must stay CLEAN. Schema is `voice/.env.example`.

See `CONTRIBUTING.md` and `docs/TESTING.md`.
