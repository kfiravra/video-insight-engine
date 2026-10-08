"""The enrichment phase runs the quiz only when the plan can show it (pipeline-1min 1d.1)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import EnrichmentData, PlanResult, QuizQuestion
from src.services.pipeline.phases import enrichment as enrichment_phase

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
