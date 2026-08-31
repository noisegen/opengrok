#!/usr/bin/env bash
# Run BEFORE node starts sand-host/host-main.cjs (after boot-fetch swap).
# Idempotent. Does NOT bounce the host. Does NOT Update Computer.
#
# Wire this into whatever starts the host after a bundle swap, e.g.:
#   ~/sand-data/host-prestart-ensure.sh && node ~/sand-host/host-main.cjs
#
# After "Update Grok Bot's Computer" recover, boot-fetch replaces sand-host with
# stock. Running this before node starts re-applies the tiny fail-closed wrap
# without a forceNow upgrade bounce (which has taken the fleet down twice).
set -euo pipefail
SAND="${SAND_DATA:-${HOME}/sand-data}"
if [[ ! -d "$SAND" && -d "${HOME}/agent-data" ]]; then
  SAND="${HOME}/agent-data"
fi
HOST_DIR="${SAND_HOST:-${HOME}/sand-host}"
TOOLS="${BRAIN_TOOLS:-$SAND}"
exec python3 "$SAND/ensure-brain-overlay.py" \
  --host-dir "$HOST_DIR" \
  --sand "$SAND" \
  --tools "$TOOLS"
