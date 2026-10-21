"""The judge's hero + quiz sample starts once per run, after the late group (output-quality 2.4).

``_launch_hero_quiz_check`` reads the hero and the quiz the late group wrote, so the
orchestration must call it after assembly — never before the hero exists — and once.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.routes import pipeline_orchestration
from src.services.pipeline.context import PipelineContext
from tests.replay.cassette import Cassette
from tests.replay.conftest import REFERENCE_VIDEO
from tests.replay.driver import run_replay


@pytest.fixture(scope="module")
def launches(cassettes: dict[str, Cassette]) -> list[dict]:
    seen: list[dict] = []

    def launch(ctx: PipelineContext) -> None:
        meta = ctx.assembled_meta or {}
        seen.append({"brief": meta.get("brief"), "takeaways": len(meta.get("keyTakeaways") or [])})

    with patch.object(pipeline_orchestration, "_launch_hero_quiz_check", launch):
        asyncio.run(run_replay(cassettes[REFERENCE_VIDEO], speed=0))
    return seen


class TestHeroQuizJudgeHook:
    def test_should_start_the_hero_and_quiz_sample_once_per_run(self, launches: list[dict]):
        assert len(launches) == 1

    def test_should_start_it_after_the_late_group_wrote_the_hero(self, launches: list[dict]):
        assert launches[0]["brief"] and launches[0]["takeaways"] > 0
