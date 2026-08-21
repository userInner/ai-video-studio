#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$PROJECT_DIR/.venv/bin/uvicorn" ]]; then
  python3 -m venv "$PROJECT_DIR/.venv"
  "$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/services/api/requirements.txt"
fi

if [[ ! -d "$PROJECT_DIR/apps/web/node_modules" ]]; then
  npm install --prefix "$PROJECT_DIR/apps/web"
fi

"$PROJECT_DIR/.venv/bin/uvicorn" app.main:app \
  --app-dir "$PROJECT_DIR/services/api" \
  --host 127.0.0.1 \
  --port 8010 &
API_PID=$!

npm run dev --prefix "$PROJECT_DIR/apps/web" &
WEB_PID=$!

cleanup() {
  kill "$API_PID" "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait "$API_PID" "$WEB_PID"
