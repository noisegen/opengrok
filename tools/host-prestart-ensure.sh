#!/usr/bin/env bash
# Disk helper: re-apply host-main wrap from durable sand-data.
# Does NOT bounce the host. Does NOT Update Computer.
#
# Stock /usr/local/bin/sand-supervisor.mjs does NOT call this. Live launchHost()
# hardcodes spawn(process.execPath, [HOST_ENTRY], …) with no prestart.
# Wire it with: python3 ~/sand-data/install-supervisor-prestart.py
#
# Until that installer has patched launchHost (and after Update Computer, which
# resets supervisor from the image), running this alone only updates files on
# disk — the running host-main process is unchanged. Wrap is live only after a
# host process START that already has the wrap on disk.
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
