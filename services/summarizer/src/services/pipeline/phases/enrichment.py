"""Phase 6: Enrichment — the quiz, only when the plan can use one (pipeline-1min 1d.1).

Today's path makes the call in assembly's late group, over the whole
extraction. The split path (3.6) starts it earlier as ``ctx.quiz_task``, over
the extraction merged once the groups holding its domains' fields landed
(``start_quiz_task``, B.6); the late group then awaits that task instead.
The started call is timed as ``quiz_call`` (``enrichment`` is then the wait).

Which call (output-quality 2.1b): ``QUIZ_V2_ENABLED`` (default) writes quiz v2 on
Haiku 5.5 from the transcript + the key ideas, every question's evidence quote
checked against the transcript (``pipeline/quiz_v2``); false keeps ``enrich_quiz``.
Both bill the ``summarize:enrichment`` ledger feature; the span tells them apart.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.services.pipeline.enrichment import (
    _has_meaningful_data,
    enrich_quiz,
    needs_quiz,
    render_hero,
)
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import timed_step
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2
from src.services.pipeline.quote_verify import run_quote_index

if TYPE_CHECKING:
    from src.models.pipeline_types import EnrichmentData, PlanResult
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


def _duration_s(ctx: PipelineContext) -> float | None:
    return ctx.video_data.duration if ctx.video_data else None


def _hero(ctx: PipelineContext) -> str:
    # The memory hero is what the page shows, so no question may be answerable from it.
    return render_hero(ctx.video_data.title if ctx.video_data else "", ctx.memory)


async def _quiz_v1(
    ctx: PipelineContext, plan: PlanResult, extraction: Mapping[str, Any]
) -> EnrichmentData | None:
    """The flag-off quiz: ``enrich_quiz`` over the extraction, the memory and the tabs."""
    return await enrich_quiz(
        ctx.llm_service,
        primary_tag=plan.primary_tag,
        extraction_data=extraction,
        video_memory=ctx.video_memory,
        tabs=plan.tabs,
        # Task 1.13: the length sizes the quiz (3 / 5 / 8) and bounds its stamps.
        duration_s=_duration_s(ctx),
        hero=_hero(ctx),
    )


async def _quiz_v2(
    ctx: PipelineContext, plan: PlanResult, extraction: Mapping[str, Any]
) -> EnrichmentData | None:
    """Quiz v2: the marked transcript + the key ideas in, every quote checked on the run's index."""
    request = QuizV2Request(
        primary_tag=plan.primary_tag,
        title=ctx.video_data.title if ctx.video_data else "",
        duration_s=_duration_s(ctx),
        transcript=ctx.prompt_transcript,
        extraction=extraction,
        hero=_hero(ctx),
    )
    return await enrich_quiz_v2(ctx.llm_service, request, run_quote_index(ctx))


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

    if settings.QUIZ_V2_ENABLED:
        result = await _quiz_v2(ctx, plan, extraction)
    else:
        result = await _quiz_v1(ctx, plan, extraction)
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
