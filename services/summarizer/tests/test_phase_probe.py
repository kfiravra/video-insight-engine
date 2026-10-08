"""Tier-probe wiring for a run (pipeline-1min 1b.1, ``phases/probe.py``).

The probe task starts with phase 2, makes its call only once the transcript is
ready (clean text final), tags the call as its own LLM feature, and is
cancelled when the run ends first. The text branch that sets the event is
tested in ``test_phase_text.py``. ``run_tier_probe`` (the LLM boundary) is
patched; the wiring runs as production code.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
            ctx.clean_text = "clean speech after sponsorblock"
            ctx.transcript_ready.set()
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
