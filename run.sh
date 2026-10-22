#!/usr/bin/env bash
# Start the Agentorium server. Usage: ./run.sh [--reload]
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

if [ ! -f .env ]; then
  echo "No .env found — copying .env.example (mock provider, no API keys needed)."
  cp .env.example .env
fi

HOST="${OFFICE_HOST:-$(grep -E '^OFFICE_HOST=' .env | cut -d= -f2)}"
PORT="${OFFICE_PORT:-$(grep -E '^OFFICE_PORT=' .env | cut -d= -f2)}"
exec "$PY" -m uvicorn agentorium.server:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
