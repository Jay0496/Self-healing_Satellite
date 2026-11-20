#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo 'Pi .venv missing: run python3 -m venv .venv and install dependencies' >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/demo.py" pi "$@"
