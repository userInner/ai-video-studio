#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$PROJECT_DIR/vendor/srt-whiteboard-animation/scripts/render_stream_whiteboard.py" ]]; then
  git -C "$PROJECT_DIR" submodule update --init --recursive
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg is required. Install it first, then run this script again." >&2
  exit 1
fi

python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/services/api/requirements.txt"
"$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR/packages/whiteboard_engine"
npm ci --prefix "$PROJECT_DIR/apps/web"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "Created .env. Add provider API keys before generating production media."
fi

echo "Setup complete. Run ./scripts/dev.sh"
