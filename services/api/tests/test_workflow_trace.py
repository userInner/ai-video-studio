from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import Base, Project, WorkflowEvent, WorkflowRun


@pytest.mark.asyncio
async def test_trace_endpoint_returns_ordered_structured_events(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as session:
        project = Project(title="测试主题", brief="测试主题")
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, status="running", step="researching", progress=32)
        session.add(run)
        await session.flush()
        session.add_all([
            WorkflowEvent(
                run_id=run.id,
                event_type="progress",
                message="已收到主题",
                payload={"trace_code": "scope_started", "step": "understanding", "progress": 12},
            ),
            WorkflowEvent(
                run_id=run.id,
                event_type="progress",
                message="已提交核验请求",
                payload={
                    "trace_code": "research_submitted",
                    "step": "researching",
                    "progress": 32,
                    "provider": "Sub2API Web Search",
                },
            ),
        ])
        await session.commit()
        run_id = run.id

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/v1/runs/{run_id}/trace")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "running"
        assert [event["trace_code"] for event in payload["events"]] == [
            "scope_started",
            "research_submitted",
        ]
        assert payload["events"][1]["provider"] == "Sub2API Web Search"
        assert payload["events"][0]["created_at"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
