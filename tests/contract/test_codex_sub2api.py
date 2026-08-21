from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.director import CodexDirector
from app.research import Sub2APIResearcher


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_PROVIDER_TESTS") != "1", reason="live provider contract test")
async def test_codex_sub2api_structured_discovery() -> None:
    settings = Settings(allow_demo_fallback=False, director_timeout_seconds=120)
    research = await Sub2APIResearcher(settings).search("为什么越来越多年轻人开始反向消费？")
    result = await CodexDirector(settings).discover("为什么越来越多年轻人开始反向消费？", research)
    assert len(result.options) == 3
    assert len(result.sources) >= 2
