"""The plan phase hands the planner the FULL marked transcript (pipeline-1min 1b.2).

Rendered from the raw segments — never the 3,000-char ``clean_text`` preview,
never the visual annotations injected into ``clean_text`` — with a fallback to
``clean_text`` when the transcript has no segments (metadata-only source).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline.phases import triage as phase

_ANNOTATED = "Hi, I'm Chris. [VISUAL: a pot on the stove] " + "Today, lasagna. " * 400


def _ctx(segments: list[dict]) -> SimpleNamespace:
    video_data = SimpleNamespace(
        title="BA's Best Lasagna",
        channel="Bon Appétit",
        description="",
        duration=1001,
        context=SimpleNamespace(category="Howto & Style", tags=[]),
    )
    return SimpleNamespace(
        video_data=video_data,
        transcript_data=SimpleNamespace(segments=segments),
        clean_text=_ANNOTATED,
        source_language_code=None,
        video_summary_id="vsid",
        llm_service=MagicMock(),
        override=None,
        category_hint=None,
        content_format=None,
        content_traits=None,
        language="en",
        is_rtl=False,
    )


async def _planned_transcript(segments: list[dict]) -> str:
    run_plan = AsyncMock(return_value=PlanResult.model_validate({"confidence": 0.9}))
    with (
        patch.object(phase, "check_override", return_value=None),
        patch.object(phase, "classify_domain_format", AsyncMock(return_value=None)),
        patch.object(phase, "run_plan", run_plan),
    ):
        _ = [event async for event in phase.run_phase_plan(_ctx(segments))]  # type: ignore[arg-type]
    return run_plan.call_args.kwargs["transcript"]


async def test_should_pass_marked_segments_when_transcript_has_segments() -> None:
    segments = [
        {"text": "Hi, I'm Chris.", "start": 0.0, "duration": 3.0},
        {"text": "Today, lasagna.", "start": 21.5, "duration": 4.0},
    ]

    transcript = await _planned_transcript(segments)

    assert transcript == "[0:00] Hi, I'm Chris.\n[0:21] Today, lasagna."


async def test_should_never_pass_injected_visual_annotations_when_segments_exist() -> None:
    transcript = await _planned_transcript([{"text": "Hi.", "start": 0.0, "duration": 1.0}])

    assert "[VISUAL" not in transcript


async def test_should_fall_back_to_whole_clean_text_when_there_are_no_segments() -> None:
    transcript = await _planned_transcript([])

    assert transcript == _ANNOTATED
