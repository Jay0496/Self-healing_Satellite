#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo 'Mac .venv missing: activate or create .venv first' >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/demo.py" mac "$@"
