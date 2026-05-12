#!/usr/bin/env bash
set -euo pipefail

# Load .env if it exists
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "Error: GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

PORT="${PORT:-9090}"

echo "Starting Claude Code Gemini Router on port $PORT"
python -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --log-level info
