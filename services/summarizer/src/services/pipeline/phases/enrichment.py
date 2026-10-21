"""Phase 6: Enrichment — the quiz, only when the plan can use one (pipeline-1min 1d.1).

Today's path makes the call in assembly's late group, over the whole
extraction. The split path (3.6) starts it earlier as ``ctx.quiz_task``, over
the extraction merged once the groups holding its domains' fields landed
(``start_quiz_task``, B.6); the late group then awaits that task instead.
The started call is timed as ``quiz_call`` (``enrichment`` is then the wait).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.enrichment import (
    _has_meaningful_data,
    enrich_quiz,
    needs_quiz,
    render_hero,
)
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import timed_step

if TYPE_CHECKING:
    from src.models.pipeline_types import EnrichmentData
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

# pipeline.timing step of a quiz call the split extraction started.
QUIZ_CALL_STEP = "quiz_call"


def _skip_reason(ctx: PipelineContext, extraction: Mapping[str, Any]) -> str | None:
    """Why this run needs no quiz call over ``extraction``, or None when it does."""
    if not needs_quiz(ctx.plan_result, ctx.content_format):
        return "no_demand"
    if not _has_meaningful_data(dict(extraction)):
        return "empty_extraction"
    return None


async def _quiz(ctx: PipelineContext, extraction: Mapping[str, Any]) -> EnrichmentData | None:
    """The quiz over ``extraction`` (None = skipped / failed), logged as ``pipeline.enrichment``."""
    llm_feature_var.set("summarize:enrichment")
    plan = ctx.plan_result
    skip_reason = _skip_reason(ctx, extraction)  # never None without a plan (no plan → no demand)
    if plan is None or skip_reason is not None:
        logger.info(
            "pipeline.enrichment",
            extra={"video_id": ctx.video_summary_id, "skipped": skip_reason},
        )
        return None

    result = await enrich_quiz(
        ctx.llm_service,
        primary_tag=plan.primary_tag,
        extraction_data=extraction,
        video_memory=ctx.video_memory,
        tabs=plan.tabs,
        # Task 1.13: the length sizes the quiz (3 / 5 / 8) and bounds its stamps; the
        # memory hero is what the page shows, so no question may be answerable from it.
        duration_s=ctx.video_data.duration if ctx.video_data else None,
        hero=render_hero(ctx.video_data.title if ctx.video_data else "", ctx.memory),
    )
    logger.info(
        "pipeline.enrichment",
        extra={
            "video_id": ctx.video_summary_id,
            "quiz_count": len(result.quiz) if result is not None else 0,
        },
    )
    return result


async def _timed_quiz(ctx: PipelineContext, extraction: Mapping[str, Any]) -> EnrichmentData | None:
    with timed_step(QUIZ_CALL_STEP):
        return await _quiz(ctx, extraction)


def start_quiz_task(ctx: PipelineContext, extraction: Mapping[str, Any]) -> bool:
    """Start the quiz over ``extraction`` now (split path); False when no call starts.

    No call: one already started, the plan cannot use a quiz, or the extraction
    is still empty — the late group then calls over the whole extraction.
    """
    if ctx.quiz_task is not None or _skip_reason(ctx, extraction) is not None:
        return False
    ctx.quiz_task = asyncio.create_task(
        _timed_quiz(ctx, extraction), name=f"quiz_{ctx.video_summary_id}"
    )
    return True


def cancel_quiz_task(ctx: PipelineContext) -> None:
    """Stop a started quiz call nobody will await (the run failed, or no late quiz)."""
    task = getattr(ctx, "quiz_task", None)
    if task is not None and not task.done():
        task.cancel()


async def run_phase_enrichment(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Generate the quiz for a plan that can show one; otherwise do nothing.

    Gated here as well as by assembly (``phases/assembly._late_phases``), so the
    phase is safe to schedule unconditionally. It runs in assembly's late group,
    alongside synthesis and the moment fill, after the other tabs went out: no
    synthesis input (the quiz reads the extraction and the run's video_memory),
    and assembly adds the quiz tab last once it lands (``phases/late_quiz``).
    A quiz the split extraction already started is awaited, not called again.
    """
    task = ctx.quiz_task
    result = await task if task is not None else await _quiz(ctx, ctx.extraction_data or {})
    if result is not None:
        ctx.enrichment_data = result.model_dump(by_alias=True)
        yield sse_event("enrichment_complete", ctx.enrichment_data)
