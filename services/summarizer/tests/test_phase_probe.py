"""Tier-probe wiring for a run (pipeline-1min 1b.1, ``phases/probe.py``).

The probe task starts with phase 2, makes its call only once the transcript is
ready (clean text final), tags the call as its own LLM feature, and is
cancelled when the run ends first. ``run_tier_probe`` (the LLM boundary) is
patched; the wiring runs as production code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.models.probe_types import TierProbe
from src.services.pipeline.phases import probe as probe_phase
from src.services.pipeline.tier_probe import TierProbeInput

_PROBE = TierProbe(domain="food", format="tutorial", has_visual_demo=True, confidence=0.9)


def _ctx() -> SimpleNamespace:
    video_data = SimpleNamespace(
        title="Lasagna",
        channel="Chef",
        duration=600,
        description="",
        context=None,
    )
    return SimpleNamespace(
        video_data=video_data,
        clean_text="",
        llm_service=MagicMock(),
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        probe=None,
    )


async def _transcript_phase(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    ctx.clean_text = "clean speech after sponsorblock"
    yield "data: transcript_ready\n\n"


async def _failing_transcript(_ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    raise RuntimeError("no transcript")
    yield  # pragma: no cover — makes this an async generator


async def _drain(phase: Any, ctx: SimpleNamespace) -> list[str]:
    return [event async for event in phase(ctx)]


class TestStartTierProbe:
    async def test_should_not_call_the_probe_before_the_transcript_is_ready(self) -> None:
        ctx = _ctx()
        run = AsyncMock(return_value=_PROBE)

        with patch.object(probe_phase, "run_tier_probe", run):
            probe_phase.start_tier_probe(ctx)
            await asyncio.sleep(0)
            probe_phase.cancel_tier_probe(ctx)
            await asyncio.gather(ctx.tier_probe_task, return_exceptions=True)

        run.assert_not_called()

    async def test_should_probe_the_clean_text_once_the_transcript_is_ready(self) -> None:
        ctx = _ctx()
        run = AsyncMock(return_value=_PROBE)

        with patch.object(probe_phase, "run_tier_probe", run):
            probe_phase.start_tier_probe(ctx)
            await _drain(probe_phase.marks_transcript_ready(_transcript_phase), ctx)
            await ctx.tier_probe_task

        probe_input: TierProbeInput = run.call_args.args[0]
        assert probe_input.transcript == "clean speech after sponsorblock"

    async def test_should_tag_the_probe_call_with_its_own_llm_feature(self) -> None:
        ctx = _ctx()
        seen: list[str] = []

        async def _record_feature(*_args: object) -> TierProbe:
            seen.append(llm_feature_var.get())
            return _PROBE

        token = llm_feature_var.set("summarize:frames")
        try:
            with patch.object(probe_phase, "run_tier_probe", _record_feature):
                probe_phase.start_tier_probe(ctx)
                ctx.transcript_ready.set()
                await ctx.tier_probe_task
        finally:
            llm_feature_var.reset(token)

        assert seen == ["summarize:tier_probe"]

    async def test_should_leave_the_caller_feature_untouched(self) -> None:
        token = llm_feature_var.set("summarize:metadata")
        try:
            ctx = _ctx()
            probe_phase.start_tier_probe(ctx)
            probe_phase.cancel_tier_probe(ctx)
            await asyncio.gather(ctx.tier_probe_task, return_exceptions=True)
            assert llm_feature_var.get() == "summarize:metadata"
        finally:
            llm_feature_var.reset(token)


class TestMarksTranscriptReady:
    async def test_should_set_the_event_after_the_phase_finished(self) -> None:
        ctx = _ctx()

        events = await _drain(probe_phase.marks_transcript_ready(_transcript_phase), ctx)

        assert events == ["data: transcript_ready\n\n"] and ctx.transcript_ready.is_set()

    async def test_should_leave_the_event_unset_when_the_phase_raises(self) -> None:
        ctx = _ctx()

        with pytest.raises(RuntimeError):
            await _drain(probe_phase.marks_transcript_ready(_failing_transcript), ctx)

        assert not ctx.transcript_ready.is_set()

    def test_should_keep_the_phase_name_for_timing(self) -> None:
        assert probe_phase.marks_transcript_ready(_transcript_phase).__name__ == (
            "_transcript_phase"
        )


class TestCancelTierProbe:
    async def test_should_cancel_a_probe_still_waiting_for_the_transcript(self) -> None:
        ctx = _ctx()
        with patch.object(probe_phase, "run_tier_probe", AsyncMock(return_value=_PROBE)):
            probe_phase.start_tier_probe(ctx)
            probe_phase.cancel_tier_probe(ctx)
            await asyncio.gather(ctx.tier_probe_task, return_exceptions=True)

        assert ctx.tier_probe_task.cancelled()

    def test_should_do_nothing_when_no_probe_was_started(self) -> None:
        probe_phase.cancel_tier_probe(SimpleNamespace())
