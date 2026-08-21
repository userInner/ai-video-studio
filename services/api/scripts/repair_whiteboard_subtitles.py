#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import desc, select


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.config import Settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.media_pipeline import VideoCompositor  # noqa: E402
from app.models import ArtifactVersion, MediaAsset  # noqa: E402
from app.storage import LocalAssetStore  # noqa: E402


async def update_records(project_id: str, updates: dict[str, tuple[str, int]], final_duration: float) -> None:
    async with SessionLocal() as session:
        assets = (await session.scalars(select(MediaAsset).where(MediaAsset.project_id == project_id))).all()
        for asset in assets:
            update = updates.get(asset.relative_path)
            if update:
                asset.content_hash, asset.size_bytes = update
                if asset.kind == "final_video":
                    asset.metadata_json = {**asset.metadata_json, "duration_seconds": final_duration}
        artifact = await session.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.project_id == project_id,
                ArtifactVersion.artifact_type == "final_video",
            ).order_by(desc(ArtifactVersion.version)).limit(1)
        )
        if artifact and artifact.relative_path in updates:
            digest, _ = updates[artifact.relative_path]
            artifact.metadata_json = {**artifact.metadata_json, "sha256": digest, "duration_seconds": final_duration}
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild whiteboard scenes after subtitle renderer fixes")
    parser.add_argument("project_id")
    parser.add_argument("--script-version", type=int, default=1)
    parser.add_argument("--build-version", type=int, required=True)
    args = parser.parse_args()

    settings = Settings()
    assets = LocalAssetStore(settings.asset_root)
    compositor = VideoCompositor(assets)
    base = f"projects/{args.project_id}/media/v{args.script_version}/build-{args.build_version}"
    storyboard_path = settings.asset_root / base / "storyboard.json"
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    updates: dict[str, tuple[str, int]] = {}

    current_final = settings.asset_root / base / "final" / "video.mp4"
    backup_relative = f"{base}/final/video-before-caption-redesign.mp4"
    backup_path = settings.asset_root / backup_relative
    if current_final.is_file() and not backup_path.exists():
        assets.write_bytes(backup_relative, current_final.read_bytes())

    with tempfile.TemporaryDirectory(prefix="subtitle-repair-") as temp_value:
        temp = Path(temp_value)
        scene_paths = []
        for index, scene in enumerate(storyboard["scenes"]):
            number = index + 1
            frame = settings.asset_root / base / "frames" / f"scene-{number:02d}.png"
            whiteboard = settings.asset_root / base / "whiteboard" / f"scene-{number:02d}.mp4"
            audio = settings.asset_root / base / "audio" / f"scene-{number:02d}.wav"
            output = temp / f"scene-{number:02d}.mp4"
            compositor.scene_video(
                frame,
                audio,
                scene["narration"],
                output,
                str(scene.get("visual_mode", "whiteboard_drawing")),
                whiteboard if whiteboard.is_file() else None,
                scene,
            )
            relative = f"{base}/scenes/scene-{number:02d}.mp4"
            stored = assets.write_bytes(relative, output.read_bytes())
            updates[relative] = (stored.sha256, stored.size)
            scene_paths.append(output)
            print(f"SCENE={number}/{len(storyboard['scenes'])}", flush=True)

        final_temp = temp / "video.mp4"
        compositor.concatenate(scene_paths, final_temp)
        final_relative = f"{base}/final/video.mp4"
        final_stored = assets.write_bytes(final_relative, final_temp.read_bytes())
        updates[final_relative] = (final_stored.sha256, final_stored.size)
        final_duration = compositor.duration(assets.path_for_read(final_relative))

    asyncio.run(update_records(args.project_id, updates, final_duration))
    print(json.dumps({"project_id": args.project_id, "build_version": args.build_version, "duration_seconds": final_duration, "sha256": final_stored.sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
