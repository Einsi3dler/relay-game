#!/usr/bin/env bash
# Start The Relay dev server: ./run.sh  (extra args go to uvicorn,
# e.g. ./run.sh --port 9000)
set -euo pipefail
cd "$(dirname "$0")"

# Local secrets, if there are any. Gitignored, so nothing in it reaches the
# repo — which is the point: a password committed to a public repo is not one.
if [ -f .env.local ]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! command -v uvicorn >/dev/null; then
  echo "uvicorn not found — run: pip install -e '.[test]'" >&2
  exit 1
fi

exec uvicorn backend.main:app --reload "$@"
