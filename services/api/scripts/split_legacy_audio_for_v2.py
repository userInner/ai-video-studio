#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import desc, select

from app.config import Settings
from app.db import SessionLocal
from app.media_pipeline import build_storyboard
from app.models import ScriptVersion


def duration(path: Path, ffprobe: str) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


async def run(project_id: str, source_build: int, target_build: int) -> None:
    settings = Settings()
    async with SessionLocal() as session:
        script = await session.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
            .order_by(desc(ScriptVersion.version))
            .limit(1)
        )
    if script is None:
        raise RuntimeError("project has no script")

    storyboard = build_storyboard(script.content_json)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")

    media_root = settings.asset_root / f"projects/{project_id}/media/v{script.version}"
    source_audio = media_root / f"build-{source_build}/audio"
    target_audio = media_root / f"build-{target_build}/audio"
    target_audio.mkdir(parents=True, exist_ok=True)

    sections: dict[int, list[dict]] = {}
    for scene in storyboard["scenes"]:
        sections.setdefault(int(scene["source_section_index"]), []).append(scene)

    for section_index, scenes in sections.items():
        source = source_audio / f"scene-{section_index + 1:02d}.wav"
        if not source.is_file():
            raise FileNotFoundError(source)
        source_duration = duration(source, ffprobe)
        weights = [max(1, len(scene["narration"])) for scene in scenes]
        total_weight = sum(weights)
        cursor = 0.0
        for scene, weight in zip(scenes, weights):
            span = source_duration * weight / total_weight
            output = target_audio / f"scene-{scene['index'] + 1:02d}.wav"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-af",
                    f"atrim=start={cursor:.6f}:duration={span:.6f},asetpts=PTS-STARTPTS",
                    "-ar",
                    "32000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                check=True,
            )
            cursor += span
    print(f"AUDIO_SCENES={len(storyboard['scenes'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a continuous legacy Qwen track for a V2 preview")
    parser.add_argument("project_id")
    parser.add_argument("--source-build", type=int, default=1)
    parser.add_argument("--target-build", type=int, default=2)
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.source_build, args.target_build))


if __name__ == "__main__":
    main()
