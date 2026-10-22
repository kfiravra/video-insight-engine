"""The moment fill that waits for the 720p file (output-quality 3.7).

When the late group starts with frameless moments and the run's 720p file still
downloading (``phases.assembly._defer_moment_fill``), the fill must not hold
``complete``. It starts with the late group as a task, so its seeks overlap the quiz
and the hero once the file lands; the moment tabs stay non-final
(``TabLedger.frames_pending``), and assembly joins the task after ``complete``,
with heartbeats while it waits, then sends them final, framed (``land_late_fill``),
and frees the 720p file (``close_hires_video`` — also the late-group fill's last step).

The settle step's filmstrip decision counts the frames the fill has still to add
(``LateMomentFill.still_to_come``): the start-of-group count less what landed
meanwhile, so a landed frame is never counted twice. The saved row records the
frames the fill was to add, the frames it added, and whether a filmstrip went on
frames not landed yet (``record`` → ``pipeline.assembly.momentFill``): a fill that
then falls short leaves the page with neither image surface, since ``complete.tabIds``
and the overview's final already left the filmstrip out.

Split out of ``phases/assembly`` (review p3 D2/D3): the fill phase is passed in.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.services.pipeline.assembly.tab_references import framed_moments, shadowed_on_pending
from src.services.pipeline.phases.frames import hires_upgrade_pending
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import timed_step

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

# The late group times its fill under this name too (``run_parallel_phases``).
MOMENT_FILL_STEP = "moment_fill"


@dataclass
class LateMomentFill:
    """A started deferred fill, and what it was to add (per moment tab id, at its start)."""

    task: asyncio.Task[None]
    pending: dict[str, int]
    framed: dict[str, int]
    # The settle step dropped a filmstrip that only the frames still to come shadowed.
    filmstrip_on_pending: bool = False

    def _landed(self, tabs: list[dict]) -> dict[str, int]:
        now = framed_moments(tabs)
        return {
            tab_id: max(0, now.get(tab_id, before) - before)
            for tab_id, before in self.framed.items()
        }

    def still_to_come(self, tabs: list[dict]) -> dict[str, int]:
        """Per moment tab, the frames the fill has still to add — none once it ended."""
        if self.task.done():
            return {}
        landed = self._landed(tabs)
        left = {tab_id: count - landed.get(tab_id, 0) for tab_id, count in self.pending.items()}
        return {tab_id: count for tab_id, count in left.items() if count > 0}

    def before_settle(self, ctx: PipelineContext) -> None:
        """The settle step counts the frames still to come as framed; note whether a
        filmstrip goes on them alone."""
        tabs = ctx.assembled_tabs or []
        ctx.pending_moment_frames = self.still_to_come(tabs)
        self.filmstrip_on_pending = shadowed_on_pending(tabs, ctx.pending_moment_frames)

    def record(self, tabs: list[dict]) -> dict[str, Any]:
        """``pipeline.assembly.momentFill`` once the fill ended (warns when a filmstrip went
        on frames that never landed)."""
        pending = sum(self.pending.values())
        filled = sum(self._landed(tabs).values())
        if filled < pending and self.filmstrip_on_pending:
            logger.warning(
                "Moment fill after complete framed %d/%d moments — the filmstrip already "
                "went on the frames still to come, so the page ships neither",
                filled,
                pending,
            )
        return {
            "pending": pending,
            "filled": filled,
            "filmstripDroppedOnPending": self.filmstrip_on_pending,
        }

    def cancel(self) -> None:
        """Stop a fill nobody will join (the run failed or was cancelled)."""
        if not self.task.done():
            self.task.cancel()


async def _run_fill(ctx: PipelineContext, fill_phase: PhaseFn) -> None:
    """The fill phase, timed as the late group times it. With the frames pending it
    streams nothing: the moment tabs go out once ``frames_landed``."""
    with timed_step(MOMENT_FILL_STEP):
        streamed = [event async for event in fill_phase(ctx)]
    if streamed:
        logger.error(
            "tab_ready contract: the deferred moment fill streamed %d events", len(streamed)
        )


def start_late_fill(ctx: PipelineContext, fill_phase: PhaseFn) -> LateMomentFill | None:
    """Start the deferred fill with the late group (``ctx.pending_moment_frames`` = what it
    seeks); None when the fill is not deferred."""
    if not ctx.tab_ledger.frames_pending:
        return None
    pending = dict(ctx.pending_moment_frames or {})
    now = framed_moments(ctx.assembled_tabs or [])
    task = asyncio.create_task(
        _run_fill(ctx, fill_phase), name=f"moment_fill_{ctx.video_summary_id}"
    )
    return LateMomentFill(task, pending, {tab_id: now.get(tab_id, 0) for tab_id in pending})


async def _join(fill: LateMomentFill) -> AsyncGenerator[str, None]:
    """Wait for the fill, a heartbeat every ``SSE_HEARTBEAT_SECONDS`` meanwhile (as
    ``run_parallel_phases`` sends them: the proxy keeps the stream open); then raise what
    it raised."""
    while not fill.task.done():
        await asyncio.wait({fill.task}, timeout=settings.SSE_HEARTBEAT_SECONDS)
        if not fill.task.done():
            yield sse_event("heartbeat", {"ts": time.monotonic()})
    fill.task.result()


async def close_hires_video(ctx: PipelineContext) -> None:
    """Moment fill was the 720p file's last reader — free the disk now, unless the
    deferred hi-res pass still reads it (the runner closes it after)."""
    hires_video = getattr(ctx, "hires_video", None)
    if hires_video is not None and not hires_upgrade_pending(ctx):
        await hires_video.close()


async def land_late_fill(ctx: PipelineContext, fill: LateMomentFill) -> AsyncGenerator[str, None]:
    """After ``complete``: the fill joined, the moment tabs final and framed (the ledger's
    second closing re-emit), then the 720p file goes."""
    async for event in _join(fill):
        yield event
    ctx.tab_ledger.frames_landed()
    ctx.pending_moment_frames = {}
    for event in ctx.tab_ledger.closing_events(ctx.assembled_tabs or []):
        yield event
    await close_hires_video(ctx)
