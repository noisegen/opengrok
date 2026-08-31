#!/usr/bin/env bash
# Run this ON the Grok Bot cloud computer (the bot you want DeepSeek to drive).
# It does not run in Cursor. Stock Grok Bot ignores local model-bindings.json
# until this patch is on the box.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAND="${SAND:-/home/box/sand-data}"
HOST="${HOST:-/home/box/sand-host/host-main.cjs}"
HOP_SESSION="${HOP_SESSION:-$SAND/openai-hop-session.cjs}"
AGENT_ID="${AGENT_ID:-71b408bd-0c94-494b-8a45-754bc0ef2d73}"
AGENT_NAME="${AGENT_NAME:-Long Run}"

echo "== preflight =="
for p in "$HOST" "$HOP_SESSION"; do
  if [[ ! -f "$p" ]]; then
    echo "MISSING $p"
    echo "This script must run on the Grok Bot cloud computer, not Windows."
    echo "Open Long Run in Grok Bot, start its Computer, then run this there."
    exit 1
  fi
  echo "ok $p"
done

if [[ ! -f "$SAND/deepseek.env" ]]; then
  echo "MISSING $SAND/deepseek.env"
  echo "Create it on the box (do not paste the key into a Grok Bot chat):"
  echo "  printf 'DEEPSEEK_API_KEY=sk-...' > $SAND/deepseek.env"
  echo "  chmod 600 $SAND/deepseek.env"
  exit 1
fi
if ! grep -qE '^DEEPSEEK_API_KEY=\S' "$SAND/deepseek.env"; then
  echo "DEEPSEEK_API_KEY is empty in $SAND/deepseek.env"
  exit 1
fi

mkdir -p "$SAND"
cp -f "$ROOT/tools/provider-maps.cjs" "$SAND/provider-maps.cjs"
cp -f "$ROOT/tools/deepseek-hop.py" "$SAND/deepseek-hop.py"
cp -f "$ROOT/tools/apply-box-patch.py" "$SAND/apply-box-patch.py"

python3 - <<PY
import json
p = "$SAND/model-bindings.json"
try:
    data = json.load(open(p, encoding="utf-8"))
except Exception:
    data = {"agents": {}}
data.setdefault("agents", {})
data["agents"]["$AGENT_ID"] = {
    "name": "$AGENT_NAME",
    "modelId": "deepseek-v4-flash",
    "provider": "deepseek",
    "hopBaseUrl": "http://127.0.0.1:18791/v1",
    "parameters": [],
}
data["_comment"] = "box install: $AGENT_NAME -> deepseek-v4-flash"
open(p, "w", encoding="utf-8").write(json.dumps(data, indent=2) + "\n")
print("bindings ->", p)
PY

if ! ss -ltn 2>/dev/null | grep -q ':18791 ' && ! netstat -ltn 2>/dev/null | grep -q ':18791 '; then
  echo "== starting deepseek-hop :18791 =="
  nohup python3 "$SAND/deepseek-hop.py" >"$SAND/deepseek-hop.log" 2>&1 &
  sleep 1
fi
python3 - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:18791/healthz", timeout=4).read().decode())
PY

echo "== applying host patch =="
python3 "$SAND/apply-box-patch.py" \
  --host "$HOST" \
  --hop "$HOP_SESSION" \
  --bindings "$SAND/model-bindings.json" \
  --maps "$SAND/provider-maps.cjs"

echo
echo "NEXT: bounce this Computer from the Grok Bot UI (stop/start, not kill -9)."
echo "Then send a normal message in the Long Run bot. Picker Test is not proof."
