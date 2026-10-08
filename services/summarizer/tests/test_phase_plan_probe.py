"""The plan phase reads the tier probe (pipeline-1min 1b.1).

A confident probe picks the playbook domain, its format refines the playbook,
and its whole answer becomes the plan's ``Hint:`` line. No probe (failed,
late, never started) or a dev-tools override means the metadata category and
no hint — the classifier this replaced is gone.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.models.probe_types import TierProbe
from src.services.pipeline.phases import probe as probe_phase
from src.services.pipeline.phases import triage as phase


def _probe(confidence: float = 0.9) -> TierProbe:
    return TierProbe(domain="food", format="tutorial", has_visual_demo=True, confidence=confidence)


def _done_task(result: TierProbe | None) -> asyncio.Future[TierProbe | None]:
    future: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    future.set_result(result)
    return future


def _ctx(
    probe: TierProbe | None, task: asyncio.Future[TierProbe | None] | None = None
) -> SimpleNamespace:
    video_data = SimpleNamespace(
        title="BA's Best Lasagna",
        channel="Bon Appétit",
        description="",
        duration=1001,
        context=SimpleNamespace(category="Howto & Style", tags=[]),
    )
    return SimpleNamespace(
        video_data=video_data,
        transcript_data=SimpleNamespace(segments=[]),
        clean_text="Today, lasagna.",
        prompt_transcript="[0:00] Today, lasagna.",
        source_language_code=None,
        video_summary_id="vsid",
        llm_service=MagicMock(),
        override=None,
        category_hint=None,
        content_format=None,
        tier_probe_task=task,
        probe=probe,
        language="en",
        is_rtl=False,
    )


async def _plan_kwargs(ctx: SimpleNamespace, override: dict | None = None) -> dict[str, Any]:
    run_plan = AsyncMock(return_value=PlanResult.model_validate({"confidence": 0.9}))
    with (
        patch.object(phase, "check_override", return_value=override),
        patch.object(phase, "run_plan", run_plan),
    ):
        _ = [event async for event in phase.run_phase_plan(ctx)]  # type: ignore[arg-type]
    return run_plan.call_args.kwargs


async def test_should_hand_the_plan_the_probe_answer_as_one_hint_line() -> None:
    kwargs = await _plan_kwargs(_ctx(_probe()))

    assert kwargs["probe_hint"] == (
        "domain=food format=tutorial has_visual_demo=true confidence=0.90"
    )


async def test_should_pick_the_playbook_from_a_confident_probe() -> None:
    kwargs = await _plan_kwargs(_ctx(_probe()))

    assert (kwargs["category_hint"], kwargs["content_format"]) == ("food", "tutorial")


async def test_should_keep_the_metadata_category_when_the_probe_is_unsure() -> None:
    kwargs = await _plan_kwargs(_ctx(_probe(confidence=0.4)))

    assert (kwargs["category_hint"], kwargs["content_format"]) == ("Howto & Style", "tutorial")


async def test_should_plan_from_metadata_without_hint_when_the_probe_returned_none() -> None:
    kwargs = await _plan_kwargs(_ctx(None))

    assert (kwargs["category_hint"], kwargs["content_format"], kwargs["probe_hint"]) == (
        "Howto & Style",
        None,
        None,
    )


async def test_should_plan_without_hint_when_no_probe_was_started() -> None:
    kwargs = await _plan_kwargs(_ctx(None))

    assert kwargs["probe_hint"] is None


async def test_should_ignore_the_probe_when_an_override_is_set() -> None:
    kwargs = await _plan_kwargs(_ctx(_probe()), override={"category": "gaming"})

    assert (kwargs["category_hint"], kwargs["content_format"], kwargs["probe_hint"]) == (
        "gaming",
        None,
        None,
    )


async def test_should_not_wait_for_the_probe_task_again() -> None:
    """The text branch already awaited it (``probe_for_plan``); the plan reads ``ctx.probe``."""
    stalled: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()

    kwargs = await asyncio.wait_for(_plan_kwargs(_ctx(_probe(), task=stalled)), timeout=1.0)

    assert kwargs["probe_hint"] is not None


async def test_should_record_the_probe_format_in_the_triage_dict() -> None:
    ctx = _ctx(_probe())

    await _plan_kwargs(ctx)

    assert ctx.triage_dict["contentFormat"] == "tutorial"


async def test_should_stop_waiting_for_a_stalled_probe_after_its_budget() -> None:
    stalled: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
    ctx = _ctx(None, task=stalled)

    with patch.object(probe_phase, "PLAN_PROBE_WAIT_SECONDS", 0.01):
        probe = await probe_phase.probe_for_plan(ctx)  # type: ignore[arg-type]

    assert (probe, ctx.probe) == (None, None)


async def test_should_store_the_probe_answer_on_the_context() -> None:
    ctx = _ctx(None, task=_done_task(_probe()))

    await probe_phase.probe_for_plan(ctx)  # type: ignore[arg-type]

    assert ctx.probe == _probe()


async def test_should_carry_the_plans_evidence_and_terms_in_the_triage_dict() -> None:
    ctx = _ctx(None)
    plan = PlanResult.model_validate(
        {"confidence": 0.9, "evidence": {"has_steps": True}, "terms": ["guanciale"]}
    )
    with (
        patch.object(phase, "check_override", return_value=None),
        patch.object(phase, "run_plan", AsyncMock(return_value=plan)),
    ):
        _ = [event async for event in phase.run_phase_plan(ctx)]  # type: ignore[arg-type]

    assert (ctx.triage_dict["evidence"], ctx.triage_dict["terms"]) == (
        {"has_steps": True},
        ["guanciale"],
    )
