#!/usr/bin/env bash
# Run ON the Grok Bot cloud computer, not Windows.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/install-brain-router.py" --src "$ROOT" "$@"
