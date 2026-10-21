"""The judge's hero + quiz sample starts once per run, after the late group (output-quality 2.4).

``_launch_hero_quiz_check`` reads the hero and the quiz the late group wrote, so the
orchestration must call it after assembly — never before the hero exists — and once.
It judges the hero only when the hero rewrite wrote it and the quiz only when quiz v2
made it: a run with both flags off samples nothing (no extra judge call).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from src.routes import pipeline_faithfulness, pipeline_orchestration
from src.services.pipeline.context import PipelineContext
from tests.replay.cassette import Cassette
from tests.replay.conftest import REFERENCE_VIDEO
from tests.replay.driver import run_replay

# The replay driver pins the judge off (rate 0); these runs turn it on to see what it would sample.
_JUDGE_ON = {"LANGFUSE_FAITHFULNESS_SAMPLE_RATE": 0.2}
_LATE_CALLS_OFF = {"HERO_REWRITE_ENABLED": False, "QUIZ_V2_ENABLED": False}


@pytest.fixture(scope="module")
def launches(cassettes: dict[str, Cassette]) -> list[dict]:
    seen: list[dict] = []

    def launch(ctx: PipelineContext) -> None:
        meta = ctx.assembled_meta or {}
        seen.append({"brief": meta.get("brief"), "takeaways": len(meta.get("keyTakeaways") or [])})

    with patch.object(pipeline_orchestration, "_launch_hero_quiz_check", launch):
        asyncio.run(run_replay(cassettes[REFERENCE_VIDEO], speed=0))
    return seen


def _sampled_calls(cassette: Cassette, overrides: dict[str, Any]) -> list[list[str]]:
    """Per launch, the judge calls the run would make (``[]`` = no sample at all)."""
    seen: list[list[str]] = []

    def launch(ctx: PipelineContext) -> None:
        samples = pipeline_faithfulness._hero_quiz_samples(ctx)
        seen.append([] if samples is None else [s.score_name for s in samples.calls()])

    with (
        patch.object(pipeline_orchestration, "_launch_hero_quiz_check", launch),
        patch.object(pipeline_orchestration, "_launch_faithfulness_check", lambda ctx: None),
    ):
        asyncio.run(run_replay(cassette, speed=0, settings_overrides=overrides))
    return seen


class TestHeroQuizJudgeHook:
    def test_should_start_the_hero_and_quiz_sample_once_per_run(self, launches: list[dict]):
        assert len(launches) == 1

    def test_should_start_it_after_the_late_group_wrote_the_hero(self, launches: list[dict]):
        assert launches[0]["brief"] and launches[0]["takeaways"] > 0


class TestFlagOffRun:
    def test_should_sample_the_hero_and_the_quiz_when_both_late_calls_ran(
        self, cassettes: dict[str, Cassette]
    ):
        calls = _sampled_calls(cassettes[REFERENCE_VIDEO], _JUDGE_ON)

        assert calls == [["faithfulness_hero", "faithfulness_quiz"]]

    def test_should_sample_nothing_when_the_hero_rewrite_and_quiz_v2_are_off(
        self, cassettes: dict[str, Cassette]
    ):
        calls = _sampled_calls(cassettes[REFERENCE_VIDEO], {**_JUDGE_ON, **_LATE_CALLS_OFF})

        assert calls == [[]]
