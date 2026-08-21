from __future__ import annotations

from pathlib import Path


def locate_legacy_renderer() -> Path:
    """Resolve the renderer vendored as the upstream Git submodule."""
    candidate = Path(__file__).resolve().parents[3] / "vendor" / "srt-whiteboard-animation" / "scripts" / "stream_render.py"
    if not candidate.is_file():
        raise FileNotFoundError("whiteboard renderer submodule is missing; run git submodule update --init --recursive")
    return candidate
