#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$PROJECT_DIR/.venv/bin/pytest" -q "$PROJECT_DIR/services/api/tests"
PYTHONPATH="$PROJECT_DIR/packages/whiteboard_engine" \
  "$PROJECT_DIR/.venv/bin/pytest" -q "$PROJECT_DIR/packages/whiteboard_engine/tests"
npm run lint --prefix "$PROJECT_DIR/apps/web"
npm run build --prefix "$PROJECT_DIR/apps/web"
