from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .config import Settings
from .db import SessionLocal
from .director import CodexDirector
from .models import ArtifactVersion, Project, Source, TopicOption, WorkflowEvent, WorkflowRun
from .research import Sub2APIResearcher
from .storage import LocalAssetStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TopicConfirmationRunner:
    """Development runner with durable state; Temporal is the production adapter."""

    def __init__(self, settings: Settings):
        self.director = CodexDirector(settings)
        self.researcher = Sub2APIResearcher(settings)
        self.assets = LocalAssetStore(settings.asset_root)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, run_id: str) -> None:
        task = asyncio.create_task(self.run(run_id), name=f"topic-confirmation:{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        async with SessionLocal() as session:
            result = await session.scalars(
                select(WorkflowRun.id).where(
                    WorkflowRun.workflow_type == "topic_confirmation",
                    WorkflowRun.status.in_(["queued", "running"]),
                )
            )
            for run_id in result.all():
                self.submit(run_id)

    async def _event(self, session, run: WorkflowRun, step: str, progress: int, message: str) -> None:
        run.status = "running"
        run.step = step
        run.progress = progress
        if run.started_at is None:
            run.started_at = utcnow()
        session.add(WorkflowEvent(run_id=run.id, event_type="progress", message=message, payload={"progress": progress, "step": step}))
        await session.commit()

    async def run(self, run_id: str) -> None:
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is None or run.status == "completed":
                return
            project = await session.get(Project, run.project_id)
            if project is None:
                return
            try:
                await self._event(session, run, "understanding", 12, "正在理解你想讲的核心问题")
                await self._event(session, run, "researching", 32, "正在联网核对事实并寻找可靠来源")
                research = await self.researcher.search(project.brief)
                await self._event(session, run, "synthesizing", 70, "Codex 正在基于证据比较传播角度")
                discovery = await self.director.discover(project.brief, research)

                await session.execute(delete(Source).where(Source.project_id == project.id))
                await session.execute(delete(TopicOption).where(TopicOption.project_id == project.id))
                for source in discovery.sources:
                    session.add(Source(
                        project_id=project.id,
                        title=source.title,
                        url=str(source.url),
                        publisher=source.publisher,
                        published_at=source.published_at,
                        credibility=source.credibility,
                        summary=source.summary,
                    ))
                for rank, option in enumerate(discovery.options, start=1):
                    session.add(TopicOption(project_id=project.id, rank=rank, **option.model_dump()))

                project.title = discovery.corrected_title
                project.stage = "topic_selection"
                research_snapshot = self.assets.write_json(
                    f"projects/{project.id}/research/research-pack-v1.json",
                    research.as_dict(),
                )
                snapshot = self.assets.write_json(
                    f"projects/{project.id}/research/discovery-v1.json",
                    discovery.model_dump(mode="json"),
                )
                session.add(ArtifactVersion(
                    project_id=project.id,
                    artifact_type="research_pack",
                    input_hash=hashlib.sha256(project.brief.encode("utf-8")).hexdigest(),
                    relative_path=research_snapshot.relative_path,
                    metadata_json={"sha256": research_snapshot.sha256, "source_count": len(research.sources), "is_demo": research.is_demo},
                ))
                session.add(ArtifactVersion(
                    project_id=project.id,
                    artifact_type="discovery",
                    input_hash=hashlib.sha256(project.brief.encode("utf-8")).hexdigest(),
                    relative_path=snapshot.relative_path,
                    metadata_json={"sha256": snapshot.sha256, "fact_note": discovery.fact_note},
                ))
                run.status = "completed"
                run.step = "ready_for_selection"
                run.progress = 100
                run.finished_at = utcnow()
                session.add(WorkflowEvent(run_id=run.id, event_type="completed", message="三个视频方向已经准备好", payload={"progress": 100}))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                run = await session.get(WorkflowRun, run_id)
                if run:
                    run.status = "failed"
                    run.step = "failed"
                    run.error = str(exc)[:2000]
                    run.finished_at = utcnow()
                    session.add(WorkflowEvent(run_id=run.id, event_type="failed", message="调研暂时没有完成", payload={"error": run.error}))
                    await session.commit()
