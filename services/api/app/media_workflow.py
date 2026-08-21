from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from .config import Settings
from .db import SessionLocal
from .media_pipeline import MediaPipeline
from .models import ArtifactVersion, MediaAsset, Project, ScriptVersion, StoryboardVersion, WorkflowEvent, WorkflowRun


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MediaProductionRunner:
    """Durable local media runner for voice, visuals, whiteboard and composition."""

    def __init__(self, settings: Settings):
        self.pipeline = MediaPipeline(settings)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, run_id: str) -> None:
        task = asyncio.create_task(self.run(run_id), name=f"media-production:{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        async with SessionLocal() as session:
            ids = await session.scalars(
                select(WorkflowRun.id).where(
                    WorkflowRun.workflow_type == "media_production",
                    WorkflowRun.status.in_(["queued", "running"]),
                )
            )
            for run_id in ids.all():
                self.submit(run_id)

    async def _event(self, session, run: WorkflowRun, step: str, progress: int, message: str) -> None:
        run.status = "running"
        run.step = step
        run.progress = progress
        if run.started_at is None:
            run.started_at = utcnow()
        session.add(WorkflowEvent(run_id=run.id, event_type="progress", message=message, payload={"progress": progress, "step": step}))
        await session.commit()

    async def _wait_for_script(self, project_id: str, attempts: int = 240) -> ScriptVersion | None:
        for _ in range(attempts):
            async with SessionLocal() as session:
                script = await session.scalar(
                    select(ScriptVersion).where(ScriptVersion.project_id == project_id).order_by(desc(ScriptVersion.version)).limit(1)
                )
                if script is not None:
                    return script
                failed_script_run = await session.scalar(
                    select(WorkflowRun.id).where(
                        WorkflowRun.project_id == project_id,
                        WorkflowRun.workflow_type == "production",
                        WorkflowRun.status == "failed",
                    ).limit(1)
                )
                if failed_script_run:
                    return None
            await asyncio.sleep(1)
        return None

    async def run(self, run_id: str) -> None:
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is None or run.workflow_type != "media_production" or run.status == "completed":
                return
            await self._event(session, run, "waiting_for_script", 5, "等待已校验脚本进入媒体生产")
            project_id = run.project_id

        script = await self._wait_for_script(project_id)
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, run_id)
            project = await session.get(Project, project_id)
            if run is None or project is None:
                return
            if script is None:
                await self._fail(session, run, "脚本没有准备好，媒体生产未启动")
                return
            try:
                await self._event(session, run, "storyboarding", 12, "正在把脚本拆成全程白板绘制分镜")
                current_version = await session.scalar(
                    select(StoryboardVersion.version).where(StoryboardVersion.project_id == project_id).order_by(desc(StoryboardVersion.version)).limit(1)
                )
                storyboard_version = (current_version or 0) + 1
                await self._event(session, run, "generating_media", 30, "正在生成本地 Qwen 配音和 Image2 白板画稿")
                result = await self.pipeline.produce(project_id, script.version, storyboard_version, script.content_json)
                await self._event(session, run, "composing_video", 88, "正在合成字幕、音轨和竖屏场景")

                storyboard_snapshot = self.pipeline.assets.write_json(
                    f"projects/{project_id}/media/v{script.version}/build-{storyboard_version}/storyboard.json",
                    result.storyboard,
                )
                session.add(
                    StoryboardVersion(
                        project_id=project_id,
                        script_version_id=script.id,
                        version=storyboard_version,
                        content_json=result.storyboard,
                    )
                )
                for item in result.assets:
                    session.add(
                        MediaAsset(
                            project_id=project_id,
                            script_version_id=script.id,
                            scene_index=item.scene_index,
                            kind=item.kind,
                            provider=item.provider,
                            model=item.model,
                            relative_path=item.stored.relative_path,
                            content_hash=item.stored.sha256,
                            size_bytes=item.stored.size,
                            metadata_json=item.metadata,
                        )
                    )
                script_hash = hashlib.sha256(json.dumps(script.content_json, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                session.add(
                    ArtifactVersion(
                        project_id=project_id,
                        artifact_type="storyboard",
                        version=storyboard_version,
                        input_hash=script_hash,
                        relative_path=storyboard_snapshot.relative_path,
                        metadata_json={
                            "sha256": storyboard_snapshot.sha256,
                            "scene_count": len(result.storyboard["scenes"]),
                            "voice_provider": result.voice_provider,
                            "image_provider": result.image_provider,
                        },
                    )
                )
                final = next(item for item in result.assets if item.kind == "final_video")
                final_version = await session.scalar(
                    select(ArtifactVersion.version).where(
                        ArtifactVersion.project_id == project_id,
                        ArtifactVersion.artifact_type == "final_video",
                    ).order_by(desc(ArtifactVersion.version)).limit(1)
                )
                session.add(
                    ArtifactVersion(
                        project_id=project_id,
                        artifact_type="final_video",
                        version=(final_version or 0) + 1,
                        input_hash=script_hash,
                        relative_path=final.stored.relative_path,
                        metadata_json={"sha256": final.stored.sha256, **final.metadata},
                    )
                )
                project.stage = "video_ready"
                run.status = "completed"
                run.step = "video_ready"
                run.progress = 100
                run.finished_at = utcnow()
                session.add(WorkflowEvent(run_id=run.id, event_type="completed", message="竖屏预览视频已经生成", payload={"progress": 100, "step": "video_ready"}))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                run = await session.get(WorkflowRun, run_id)
                if run:
                    await self._fail(session, run, str(exc)[:2000])

    @staticmethod
    async def _fail(session, run: WorkflowRun, error: str) -> None:
        run.status = "failed"
        run.step = "failed"
        run.error = error
        run.finished_at = utcnow()
        session.add(WorkflowEvent(run_id=run.id, event_type="failed", message="媒体生产暂时没有完成", payload={"error": error}))
        await session.commit()
