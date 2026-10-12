"""Frames-done — the ``<visual_annotations>`` block, then the deferred code tabs (1c.2, 3.2, 3.4).

Both paths run the same step once frames are done: render the annotations
from the vision captions + OCR, then let 3.2's ``frames_done_events`` decide
the code tabs reconcile deferred (a fresh ``meta`` when that changed the tabs).
Today's path runs it at the orchestrator's ``visual_inject`` step, after the
whole phase-2 group. The split path (``EXTRACTION_PARALLEL``) runs it inside
the group, right after the frames phase, and then sets ``ctx.frames_done`` —
the visual group's signal — so it is cut from the decided tabs.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

from src.services.pipeline.phases.triage import frames_done_events
from src.services.pipeline.pipeline_timing import timed_step
from src.services.pipeline.visual_annotations import annotation_entries, render_visual_annotations

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

# The pipeline.timing step name both paths record (comparable with the baseline).
VISUAL_INJECT_STEP = "visual_inject"


def render_frame_annotations(ctx: PipelineContext) -> None:
    """Frame captions + OCR → ``ctx.visual_annotations`` once frames are done (1c.2).

    Never written into ``clean_text``: the probe, plan and memory read speech
    only, and so do the Qdrant transcript points and the faithfulness judge's
    transcript part.
    """
    ctx.visual_annotations = render_visual_annotations(ctx.frame_descriptions, ctx.scene_frames_all)
    entries = len(annotation_entries(ctx.visual_annotations))
    if entries:
        logger.info("[pipeline] Visual annotations: %d entries", entries)


def frames_done_step(ctx: PipelineContext) -> list[str]:
    """The annotations, then the deferred code tabs decided on them (events: a fresh ``meta``)."""
    render_frame_annotations(ctx)
    return frames_done_events(ctx)


def then_frames_done(phase: PhaseFn) -> PhaseFn:
    """``phase`` (frames), then the frames-done step, then ``ctx.frames_done``.

    Frames that end before plan-done (scene extraction off, no frames, a
    cached-frame re-run) wait for it: the deferred code tabs only exist once
    reconcile ran. The event is set in a ``finally``: every early return of
    the frames phase still reaches the step, and a failure still releases
    whoever waits (the group is being cancelled then anyway). Keeps the
    phase's name for ``pipeline.timing`` — its "frames" wall now includes that
    wait and the ``visual_inject`` step (recorded on its own as well).
    """

    @functools.wraps(phase)
    async def wrapped(ctx: PipelineContext) -> AsyncGenerator[str, None]:
        events: list[str] = []
        try:
            async for event in phase(ctx):
                yield event
            await ctx.plan_done.wait()
            with timed_step(VISUAL_INJECT_STEP):
                events = frames_done_step(ctx)
        finally:
            ctx.frames_done.set()
        for event in events:
            yield event

    return wrapped
