"""The plan phase hands the planner the FULL marked transcript (pipeline-1min 1b.2/1b.3).

The text branch renders it once (``ctx.prompt_transcript``, see
``test_phase_text.py``) and the plan passes that same string on — no
re-render, no 3,000-char preview, no ``clean_text``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline.phases import triage as phase

_RENDERED = "[0:00] Hi, I'm Chris.\n[0:21] Today, lasagna."


def _ctx() -> SimpleNamespace:
    video_data = SimpleNamespace(
        title="BA's Best Lasagna",
        channel="Bon Appétit",
        description="",
        duration=1001,
        context=SimpleNamespace(category="Howto & Style", tags=[]),
    )
    return SimpleNamespace(
        video_data=video_data,
        prompt_transcript=_RENDERED,
        clean_text="Hi, I'm Chris. [VISUAL: a pot on the stove] Today, lasagna.",
        source_language_code=None,
        video_summary_id="vsid",
        llm_service=MagicMock(),
        override=None,
        category_hint=None,
        content_format=None,
        tier_probe_task=None,
        probe=None,
        language="en",
        is_rtl=False,
    )


async def test_should_pass_the_rendered_prompt_transcript_verbatim() -> None:
    run_plan = AsyncMock(return_value=PlanResult.model_validate({"confidence": 0.9}))
    with (
        patch.object(phase, "check_override", return_value=None),
        patch.object(phase, "run_plan", run_plan),
    ):
        _ = [event async for event in phase.run_phase_plan(_ctx())]  # type: ignore[arg-type]

    assert run_plan.call_args.kwargs["transcript"] is _RENDERED
