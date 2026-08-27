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

    async def _event(
        self,
        session,
        run: WorkflowRun,
        step: str,
        progress: int,
        message: str,
        details: dict | None = None,
    ) -> None:
        run.status = "running"
        run.step = step
        run.progress = progress
        if run.started_at is None:
            run.started_at = utcnow()
        payload = {"progress": progress, "step": step}
        if details:
            payload.update(details)
        session.add(WorkflowEvent(run_id=run.id, event_type="progress", message=message, payload=payload))
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
                await self._event(
                    session,
                    run,
                    "understanding",
                    12,
                    "已收到主题，正在拆解调研目标",
                    {
                        "trace_code": "scope_started",
                        "detail": "识别事件主体、时间边界、关键判断和需要核验的表述。",
                        "subject": project.brief[:240],
                    },
                )
                await self._event(
                    session,
                    run,
                    "understanding",
                    20,
                    "调研边界已经确定",
                    {
                        "trace_code": "scope_ready",
                        "detail": "先查权威原始材料，再用主流媒体交叉验证；事实、推断和未知信息分别标注。",
                        "checks": ["标题与时间", "人物与事件", "数字与法律定性", "传播价值"],
                    },
                )
                await self._event(
                    session,
                    run,
                    "researching",
                    32,
                    "已向联网检索服务提交核验请求",
                    {
                        "trace_code": "research_submitted",
                        "detail": "正在等待搜索和引用结果返回。耗时取决于联网服务，本页会持续保留真实状态。",
                        "provider": "Sub2API Web Search",
                    },
                )
                research = await self.researcher.search(project.brief)
                publishers = list(dict.fromkeys(source.publisher for source in research.sources if source.publisher))
                await self._event(
                    session,
                    run,
                    "researching",
                    52,
                    f"联网核验返回 {len(research.sources)} 条可追溯来源",
                    {
                        "trace_code": "research_returned",
                        "detail": "已提取引用并去除重复链接，接下来只基于这些证据形成判断。",
                        "source_count": len(research.sources),
                        "publishers": publishers[:8],
                        "source_titles": [source.title for source in research.sources[:8]],
                        "is_demo": research.is_demo,
                    },
                )
                await self._event(
                    session,
                    run,
                    "synthesizing",
                    70,
                    "正在基于证据比较传播角度",
                    {
                        "trace_code": "angles_comparing",
                        "detail": "分别评估认知反转、利益相关和情绪／人性三种叙事张力。",
                        "dimensions": ["认知反转", "利益相关", "情绪／人性"],
                    },
                )
                discovery = await self.director.discover(project.brief, research)

                await self._event(
                    session,
                    run,
                    "synthesizing",
                    90,
                    "事实结论与三个候选方向已经形成",
                    {
                        "trace_code": "angles_ready",
                        "detail": discovery.fact_note[:800],
                        "corrected_title": discovery.corrected_title,
                        "option_titles": [option.title for option in discovery.options],
                    },
                )

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
                session.add(WorkflowEvent(
                    run_id=run.id,
                    event_type="completed",
                    message="三个视频方向已经准备好",
                    payload={
                        "progress": 100,
                        "step": "ready_for_selection",
                        "trace_code": "completed",
                        "detail": "调研快照与选题结果已保存到本地项目目录。",
                        "source_count": len(discovery.sources),
                        "option_titles": [option.title for option in discovery.options],
                    },
                ))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                run = await session.get(WorkflowRun, run_id)
                if run:
                    run.status = "failed"
                    run.step = "failed"
                    run.error = str(exc)[:2000]
                    run.finished_at = utcnow()
                    session.add(WorkflowEvent(
                        run_id=run.id,
                        event_type="failed",
                        message="调研暂时没有完成",
                        payload={
                            "error": run.error,
                            "step": "failed",
                            "progress": run.progress,
                            "trace_code": "failed",
                            "detail": "失败位置和错误已保留，可据此检查联网服务或模型配置。",
                        },
                    ))
                    await session.commit()
