#!/usr/bin/env bash
# Start The Relay dev server: ./run.sh  (extra args go to uvicorn,
# e.g. ./run.sh --port 9000)
set -euo pipefail
cd "$(dirname "$0")"

if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! command -v uvicorn >/dev/null; then
  echo "uvicorn not found — run: pip install -e '.[test]'" >&2
  exit 1
fi

exec uvicorn backend.main:app --reload "$@"
