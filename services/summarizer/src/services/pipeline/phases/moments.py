"""The Moments tab at plan-done (pipeline-1min 2.1).

A Moments tab always exists (Kfir 2026-10-12): the plan's moment_track tab
when it designed one, else the tab assembly adds from the fallback chain
(``assembly/moment_fallback.always_moments_tab``). The chain — memory outline
→ YouTube chapters → description timestamps — needs no extraction, so a
provisional copy streams as soon as the plan and memory are done: the id,
label and position estimate of the tab that replaces it by id later, each
outline moment described by what its section establishes (D5 / R7, 1.9).
The event's ``provisional`` flag is what the web marks "Preview" in the strip
until the replacement lands. No source with an entry → no tab; a plan whose
steps are the timeline (D6) → no tab. The copy is a best-effort preview: a
failure to build it logs a warning and streams nothing, never failing the run.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any

from src.models.sse_events import SSEEventValidationError
from src.services.pipeline.assembly.core import post_process_tabs
from src.services.pipeline.assembly.moment_fallback import provisional_moments_tab
from src.services.video.description_analyzer import DescriptionAnalysis
from src.shared_config.domain_config import effective_requirements

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

# What a malformed source (an outline section, a chapter, a plan tab) can raise
# while the preview is built.
_PREVIEW_ERRORS = (AssertionError, AttributeError, KeyError, TypeError, ValueError)


def video_meta(ctx: PipelineContext) -> dict[str, Any]:
    """The video facts assembly reads, the moment fallback chain's sources included."""
    assert ctx.video_data is not None
    return {
        "videoId": ctx.video_summary_id,
        "title": ctx.video_data.title,
        "channel": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "chapters": [
            {"start_time": ch.start_time, "end_time": ch.end_time, "title": ch.title}
            for ch in (ctx.video_data.chapters or [])
        ],
        # moment_track fallback, first rung (outline → chapters → description timestamps).
        "outline": [s.model_dump() for s in (ctx.memory.outline if ctx.memory else [])],
    }


def _landed_description(ctx: PipelineContext) -> dict[str, Any] | None:
    """The description analysis if it already landed — plan-done never waits for it."""
    analysis = ctx.description_analysis
    if isinstance(analysis, DescriptionAnalysis) and analysis.has_content:
        return analysis.to_dict()
    return None


def provisional_moments_event(ctx: PipelineContext) -> str | None:
    """The provisional Moments ``tab_ready``, or None when no fallback source has an entry."""
    assert ctx.triage is not None
    assert ctx.video_data is not None
    primary = ctx.triage.primary_tag
    requirements = effective_requirements(primary, ctx.content_format)
    built = provisional_moments_tab(
        ctx.triage.tabs, video_meta(ctx), _landed_description(ctx), primary, requirements
    )
    if built is None:
        return None
    tab, position = built
    # Assembly's post-processing (emoji strip, item cap, label count) on the
    # fallback moments: a label that states a count states the preview's, so the
    # replacement may restate it with the count the real tab ships (D7).
    post_process_tabs([tab], ctx.video_data.duration)
    logger.info(
        "pipeline.moments_provisional",
        extra={"video_id": ctx.video_summary_id, "tab_id": tab["id"], "position": position},
    )
    return ctx.tab_ledger.event(tab, position, provisional=True)


def _safe_provisional_moments_event(ctx: PipelineContext) -> str | None:
    """:func:`provisional_moments_event`, or None when building the preview failed.

    An SSE contract violation still raises (it fails dev runs and CI on purpose).
    """
    try:
        return provisional_moments_event(ctx)
    except SSEEventValidationError:
        raise
    except _PREVIEW_ERRORS as exc:
        logger.warning(
            "Provisional Moments tab skipped — the real one still comes from assembly: %s: %s",
            type(exc).__name__,
            exc,
            extra={"video_id": ctx.video_summary_id},
        )
        return None


def then_provisional_moments(phase: PhaseFn) -> PhaseFn:
    """``phase`` (the text branch: plan ∥ memory), then the provisional Moments tab.

    Keeps the phase's name for ``pipeline.timing``.
    """

    @functools.wraps(phase)
    async def wrapped(ctx: PipelineContext) -> AsyncGenerator[str, None]:
        async for event in phase(ctx):
            yield event
        event = _safe_provisional_moments_event(ctx)
        if event is not None:
            yield event

    return wrapped
