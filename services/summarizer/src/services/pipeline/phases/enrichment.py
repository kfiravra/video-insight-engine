"""Phase 6: Enrichment — the quiz, only when the plan can use one (pipeline-1min 1d.1)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.enrichment import _has_meaningful_data, enrich_quiz, needs_quiz
from src.services.pipeline.pipeline_helpers import sse_event

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def _skip_reason(ctx: PipelineContext) -> str | None:
    """Why this run needs no quiz call, or None when it does."""
    if not needs_quiz(ctx.plan_result, ctx.content_format):
        return "no_demand"
    if not _has_meaningful_data(ctx.extraction_data or {}):
        return "empty_extraction"
    return None


async def run_phase_enrichment(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Generate the quiz for a plan that can show one; otherwise do nothing.

    Gated here as well as by assembly (``phases/assembly._late_phases``), so the
    phase is safe to schedule unconditionally. It runs in assembly's late group,
    alongside synthesis and the moment fill, after the other tabs went out: no
    synthesis input (the quiz reads the extraction and the run's video_memory),
    and assembly adds the quiz tab last once it lands (``phases/late_quiz``).
    """
    llm_feature_var.set("summarize:enrichment")
    plan = ctx.plan_result
    skip_reason = _skip_reason(ctx)  # never None without a plan (no plan → no demand)
    if plan is None or skip_reason is not None:
        logger.info(
            "pipeline.enrichment",
            extra={"video_id": ctx.video_summary_id, "skipped": skip_reason},
        )
        return

    result = await enrich_quiz(
        ctx.llm_service,
        primary_tag=plan.primary_tag,
        extraction_data=ctx.extraction_data or {},
        video_memory=ctx.video_memory,
        tabs=plan.tabs,
    )
    if result is not None:
        ctx.enrichment_data = result.model_dump(by_alias=True)
        yield sse_event("enrichment_complete", ctx.enrichment_data)

    logger.info(
        "pipeline.enrichment",
        extra={
            "video_id": ctx.video_summary_id,
            "quiz_count": len(result.quiz) if result is not None else 0,
        },
    )
