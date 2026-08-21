from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@dataclass
class TopicConfirmationInput:
    project_id: str
    run_id: str


@workflow.defn
class TopicConfirmationWorkflow:
    """Durable confirmation workflow; activities are registered by the worker process."""

    def __init__(self) -> None:
        self._selected_topic_id: str | None = None
        self._cancelled = False

    @workflow.signal
    async def select_topic(self, topic_id: str) -> None:
        self._selected_topic_id = topic_id

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True

    @workflow.run
    async def run(self, payload: TopicConfirmationInput) -> dict[str, str]:
        await workflow.execute_activity(
            "run_topic_discovery",
            {"project_id": payload.project_id, "run_id": payload.run_id},
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=3,
            ),
        )
        await workflow.wait_condition(lambda: self._selected_topic_id is not None or self._cancelled)
        if self._cancelled:
            return {"status": "cancelled", "run_id": payload.run_id}
        return {"status": "selected", "run_id": payload.run_id, "topic_id": self._selected_topic_id or ""}
