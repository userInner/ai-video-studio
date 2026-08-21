from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from .config import Settings
from .db import SessionLocal
from .models import ArtifactVersion, ProductionCard, Project, ScriptVersion, WorkflowEvent, WorkflowRun
from .research import ResearchPack
from .schemas import SourceContract
from .script_writer import CodexScriptWriter
from .storage import LocalAssetStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductionRunner:
    """Durable local production runner; Temporal is the production adapter."""

    def __init__(self, settings: Settings):
        self.writer = CodexScriptWriter(settings)
        self.assets = LocalAssetStore(settings.asset_root)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, run_id: str) -> None:
        task = asyncio.create_task(self.run(run_id), name=f"production:{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        async with SessionLocal() as session:
            result = await session.scalars(
                select(WorkflowRun.id).where(
                    WorkflowRun.workflow_type == "production",
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
        session.add(
            WorkflowEvent(
                run_id=run.id,
                event_type="progress",
                message=message,
                payload={"progress": progress, "step": step},
            )
        )
        await session.commit()

    async def run(self, run_id: str) -> None:
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is None or run.workflow_type != "production" or run.status == "completed":
                return
            project = await session.get(Project, run.project_id)
            card = await session.scalar(
                select(ProductionCard)
                .where(ProductionCard.project_id == run.project_id, ProductionCard.status == "confirmed")
                .order_by(desc(ProductionCard.version))
                .limit(1)
            )
            research_artifact = await session.scalar(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.project_id == run.project_id,
                    ArtifactVersion.artifact_type == "research_pack",
                )
                .order_by(desc(ArtifactVersion.version))
                .limit(1)
            )
            if project is None or card is None or research_artifact is None or not research_artifact.relative_path:
                await self._fail(session, run, "制作所需的选题卡或研究底稿不存在")
                return

            try:
                await self._event(session, run, "preparing_script", 10, "正在整理制作卡和事实底稿")
                research_payload = json.loads(self.assets.read_bytes(research_artifact.relative_path))
                research = ResearchPack(
                    memo=research_payload["memo"],
                    sources=[SourceContract.model_validate(item) for item in research_payload["sources"]],
                    is_demo=bool(research_payload.get("is_demo", False)),
                )
                card_payload = {
                    "title": card.title,
                    "promise": card.promise,
                    "audience": card.audience,
                    "duration_seconds": card.duration_seconds,
                    "visual_style": card.visual_style,
                    "tone": card.tone,
                    "structure": card.structure,
                }
                await self._event(session, run, "writing_script", 45, "Codex 正在把证据写成完整口播脚本")
                script = await self.writer.generate(card_payload, research)
                await self._event(session, run, "validating_script", 80, "正在核对引用、结构和视频时长")

                current_version = await session.scalar(
                    select(ScriptVersion.version)
                    .where(ScriptVersion.project_id == project.id)
                    .order_by(desc(ScriptVersion.version))
                    .limit(1)
                )
                version = (current_version or 0) + 1
                script_payload = script.model_dump(mode="json")
                snapshot = self.assets.write_json(
                    f"projects/{project.id}/scripts/script-v{version}.json",
                    script_payload,
                )
                input_hash = hashlib.sha256(
                    json.dumps(card_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                session.add(
                    ScriptVersion(
                        project_id=project.id,
                        production_card_id=card.id,
                        version=version,
                        title=script.title,
                        estimated_duration_seconds=script.estimated_duration_seconds,
                        content_json=script_payload,
                    )
                )
                session.add(
                    ArtifactVersion(
                        project_id=project.id,
                        artifact_type="script",
                        version=version,
                        input_hash=input_hash,
                        relative_path=snapshot.relative_path,
                        metadata_json={
                            "sha256": snapshot.sha256,
                            "section_count": len(script.sections),
                            "estimated_duration_seconds": script.estimated_duration_seconds,
                        },
                    )
                )
                project.stage = "script_ready"
                run.status = "completed"
                run.step = "script_ready"
                run.progress = 100
                run.finished_at = utcnow()
                session.add(
                    WorkflowEvent(
                        run_id=run.id,
                        event_type="completed",
                        message="完整脚本已经生成",
                        payload={"progress": 100, "step": "script_ready"},
                    )
                )
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
        session.add(
            WorkflowEvent(
                run_id=run.id,
                event_type="failed",
                message="脚本生成暂时没有完成",
                payload={"error": error},
            )
        )
        await session.commit()
