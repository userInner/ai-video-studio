#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio

from app.config import Settings
from app.db import SessionLocal, init_db
from app.media_workflow import MediaProductionRunner
from app.models import WorkflowRun


async def run(project_id: str) -> int:
    await init_db()
    async with SessionLocal() as session:
        workflow = WorkflowRun(
            project_id=project_id,
            workflow_type="media_production",
            step="waiting_for_script",
        )
        session.add(workflow)
        await session.commit()
        run_id = workflow.id
        print(f"RUN_ID={run_id}", flush=True)

    runner = MediaProductionRunner(Settings())
    await runner.run(run_id)

    async with SessionLocal() as session:
        workflow = await session.get(WorkflowRun, run_id)
        if workflow is None:
            print("STATUS=missing", flush=True)
            return 1
        print(
            f"STATUS={workflow.status} STEP={workflow.step} ERROR={workflow.error or ''}",
            flush=True,
        )
        return 0 if workflow.status == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one media build with the current local code")
    parser.add_argument("project_id")
    args = parser.parse_args()
    return asyncio.run(run(args.project_id))


if __name__ == "__main__":
    raise SystemExit(main())
