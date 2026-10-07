"""Tier-probe wiring for one run (pipeline-1min 1b.1).

The probe task starts with phase 2 and makes its one call as soon as
``ctx.transcript_ready`` is set — by ``marks_transcript_ready`` around the
transcript phase, once ``clean_text`` is final (SponsorBlock included). It
reads clean transcript windows only, never frame annotations.

Two readers, each with its own wait:

* the frames branch, at most ``PROBE_WAIT_CAP_SECONDS`` just before Step 6b
  (``media/visual_tier.resolve_tier``; the metadata rule decides past that);
* the plan, for the playbook and its ``Hint:`` line (``probe_for_plan``).

The task never raises (``run_tier_probe`` returns ``None`` on any failure);
the run's cleanup cancels it when the run ends before it does.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

from llm_common.context import llm_feature_var

from src.models.probe_types import TierProbe
from src.services.media.visual_tier import await_probe
from src.services.pipeline.tier_probe import PROBE_TIMEOUT_SECONDS, TierProbeInput, run_tier_probe

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

TIER_PROBE_FEATURE = "summarize:tier_probe"
# The plan wants the probe's answer, not a race: it waits out the probe's own
# single-attempt budget (+ prompt render) and only then plans without it.
PLAN_PROBE_WAIT_SECONDS = PROBE_TIMEOUT_SECONDS + 1.0


async def _probe_at_transcript_ready(ctx: PipelineContext) -> TierProbe | None:
    await ctx.transcript_ready.wait()
    if ctx.video_data is None:
        return None
    probe_input = TierProbeInput.from_video(ctx.video_data, ctx.clean_text)
    return await run_tier_probe(probe_input, ctx.llm_service)


def start_tier_probe(ctx: PipelineContext) -> None:
    """Start the run's probe task; it waits for ``ctx.transcript_ready``.

    The task copies the context at creation, so its LLM call carries the
    probe's own ``llm_feature_var`` (cost rows, replay key) whatever phase
    happens to be running when the transcript lands.
    """
    token = llm_feature_var.set(TIER_PROBE_FEATURE)
    try:
        ctx.tier_probe_task = asyncio.create_task(_probe_at_transcript_ready(ctx))
    finally:
        llm_feature_var.reset(token)


def marks_transcript_ready(phase: PhaseFn) -> PhaseFn:
    """``phase`` (the transcript phase) then ``ctx.transcript_ready.set()``.

    Keeps the phase's name for ``pipeline.timing``. A raising phase never sets
    the event: the run fails and its cleanup cancels the waiting probe.
    """

    @functools.wraps(phase)
    async def wrapped(ctx: PipelineContext) -> AsyncGenerator[str, None]:
        async for event in phase(ctx):
            yield event
        ctx.transcript_ready.set()

    return wrapped


async def probe_for_plan(ctx: PipelineContext) -> TierProbe | None:
    """The probe's answer for the plan, stored on ``ctx.probe`` (None = none)."""
    ctx.probe = await await_probe(ctx.tier_probe_task, PLAN_PROBE_WAIT_SECONDS)
    return ctx.probe


def cancel_tier_probe(ctx: PipelineContext) -> None:
    """Cancel a probe the run no longer needs; retrieve a finished task's error."""
    task = getattr(ctx, "tier_probe_task", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    elif not task.cancelled() and task.exception() is not None:
        logger.warning("Tier probe task failed: %s", task.exception())
