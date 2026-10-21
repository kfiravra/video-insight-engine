"""The enrichment phase runs the quiz only when the plan can show it (pipeline-1min 1d.1).

Split path (3.6): the split extraction starts the call early (``start_quiz_task``)
and the phase awaits it instead of calling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import EnrichmentData, PlanResult, QuizQuestion
from src.services.pipeline import pipeline_timing
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing

_VIDEO_MEMORY = "<video_memory>\ndomains: tech\n</video_memory>"
_QUIZ_TAB = {
    "id": "quiz",
    "label": "Quiz",
    "component": "quiz_arena",
    "dataSource": "enrichment.quiz",
}
_QUESTION = QuizQuestion.model_validate(
    {
        "question": "Q?",
        "options": ["a", "b", "c", "d"],
        "correctIndex": 1,
        "explanation": "Because b.",
    }
)


def _ctx(primary: str = "tech", extraction: dict | None = None) -> SimpleNamespace:
    plan = PlanResult.model_validate(
        {"primaryTag": primary, "contentTags": [primary], "tabs": [_QUIZ_TAB]}
    )
    return SimpleNamespace(
        video_summary_id="vsid",
        plan_result=plan,
        content_format=None,
        extraction_data={"tech": {"topics": ["dependency injection in FastAPI"]}}
        if extraction is None
        else extraction,
        video_memory=_VIDEO_MEMORY,
        enrichment_data=None,
        llm_service=MagicMock(),
        quiz_task=None,
        video_data=SimpleNamespace(duration=600, title="t"),
        memory=None,
    )


async def _events(ctx: Any) -> list[str]:
    return [event async for event in enrichment_phase.run_phase_enrichment(ctx)]


async def _run(ctx: SimpleNamespace, result: EnrichmentData | None) -> tuple[list[str], Any]:
    enrich_quiz = AsyncMock(return_value=result)
    with patch.object(enrichment_phase, "enrich_quiz", enrich_quiz):
        events = await _events(ctx)
    return events, enrich_quiz


async def test_should_skip_the_quiz_call_when_the_plan_cannot_use_one() -> None:
    ctx = _ctx(primary="food")

    events, enrich_quiz = await _run(ctx, EnrichmentData(quiz=[_QUESTION, _QUESTION]))

    assert events == [] and ctx.enrichment_data is None
    enrich_quiz.assert_not_awaited()


async def test_should_skip_the_quiz_call_when_extraction_is_empty() -> None:
    events, enrich_quiz = await _run(_ctx(extraction={"tech": {"topics": []}}), None)

    assert events == []
    enrich_quiz.assert_not_awaited()


async def test_should_emit_only_the_quiz_when_the_plan_has_a_quiz_tab() -> None:
    ctx = _ctx()

    events, _ = await _run(ctx, EnrichmentData(quiz=[_QUESTION, _QUESTION]))

    assert len(events) == 1 and "enrichment_complete" in events[0]
    assert list(ctx.enrichment_data) == ["quiz"] and len(ctx.enrichment_data["quiz"]) == 2


async def test_should_hand_the_plan_tabs_and_video_memory_to_the_quiz_call() -> None:
    ctx = _ctx()

    _, enrich_quiz = await _run(ctx, None)

    kwargs = enrich_quiz.await_args.kwargs
    assert kwargs["video_memory"] is _VIDEO_MEMORY and kwargs["tabs"] == ctx.plan_result.tabs


async def test_should_emit_nothing_when_the_quiz_call_fails() -> None:
    ctx = _ctx()

    events, _ = await _run(ctx, None)

    assert events == [] and ctx.enrichment_data is None


# ─── Split path: the quiz task ───

_EARLY_EXTRACTION = {"tech": {"topics": ["the extraction merged so far"]}}


async def _started(ctx: Any, result: EnrichmentData | None) -> tuple[bool, Any]:
    enrich_quiz = AsyncMock(return_value=result)
    with patch.object(enrichment_phase, "enrich_quiz", enrich_quiz):
        started = enrichment_phase.start_quiz_task(ctx, _EARLY_EXTRACTION)
        if ctx.quiz_task is not None:
            await ctx.quiz_task
    return started, enrich_quiz


async def test_should_start_the_quiz_over_the_extraction_it_is_given() -> None:
    _, enrich_quiz = await _started(_ctx(), None)

    assert enrich_quiz.await_args.kwargs["extraction_data"] is _EARLY_EXTRACTION


async def test_should_not_start_the_quiz_when_the_extraction_so_far_is_empty() -> None:
    ctx: Any = _ctx()

    started = enrichment_phase.start_quiz_task(ctx, {"tech": {"topics": []}})

    assert (started, ctx.quiz_task) == (False, None)


async def test_should_not_start_the_quiz_when_the_plan_cannot_use_one() -> None:
    started, _ = await _started(_ctx(primary="food"), None)

    assert started is False


async def test_should_await_the_started_quiz_instead_of_calling_again() -> None:
    ctx = _ctx()
    await _started(ctx, EnrichmentData(quiz=[_QUESTION, _QUESTION]))

    events, enrich_quiz = await _run(ctx, None)

    assert (len(events), enrich_quiz.await_count) == (1, 0)


async def test_should_emit_the_started_quizs_questions() -> None:
    ctx = _ctx()
    await _started(ctx, EnrichmentData(quiz=[_QUESTION, _QUESTION]))

    await _run(ctx, None)

    assert len(ctx.enrichment_data["quiz"]) == 2


async def test_should_cancel_a_started_quiz_nobody_awaits() -> None:
    ctx: Any = _ctx()
    ctx.quiz_task = asyncio.create_task(asyncio.sleep(60))

    enrichment_phase.cancel_quiz_task(ctx)
    await asyncio.gather(ctx.quiz_task, return_exceptions=True)

    assert ctx.quiz_task.cancelled()


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


async def test_should_time_the_started_call_as_quiz_call(
    recorder: PipelineTimingRecorder,
) -> None:
    await _started(_ctx(), None)

    assert [phase["name"] for phase in recorder.phases] == [enrichment_phase.QUIZ_CALL_STEP]


async def test_should_size_the_quiz_by_the_video_length_when_the_video_is_known() -> None:
    ctx = _ctx()

    _, enrich_quiz = await _run(ctx, None)

    assert enrich_quiz.await_args.kwargs["duration_s"] == 600


async def test_should_hand_the_hero_text_to_the_quiz_call_when_the_title_is_known() -> None:
    ctx = _ctx()

    _, enrich_quiz = await _run(ctx, None)

    assert "t" in enrich_quiz.await_args.kwargs["hero"]
