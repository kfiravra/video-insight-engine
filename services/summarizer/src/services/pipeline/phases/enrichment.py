"""Phase 6: Enrichment — quiz/flashcards/scenarios."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.enrichment import _has_meaningful_data, enrich
from src.services.pipeline.pipeline_helpers import sse_event

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


async def run_phase_enrichment(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run enrichment to generate quiz, flashcards, and scenarios."""
    llm_feature_var.set("summarize:enrichment")
    assert ctx.triage is not None

    # Build tab goals for enrichment context
    tab_goals_text = ""
    if ctx.triage.tabs:
        tab_goals_text = "\n".join(
            f'Tab: "{t.get("label", t.get("id", ""))}" ({t.get("component", "overview")}) — {t.get("goal", "")}'
            for t in ctx.triage.tabs
        )

    enrichment_result = await enrich(
        ctx.llm_service,
        ctx.triage.primary_tag,
        ctx.extraction_data or {},
        ctx.video_data.title if ctx.video_data else "",
        content_tags=ctx.triage.content_tags,
        synthesis_data=ctx.synthesis_dict,
        video_context=ctx.video_memory,
        tab_goals=tab_goals_text,
        content_format=ctx.content_format,
    )
    if enrichment_result:
        ctx.enrichment_data = enrichment_result.model_dump(by_alias=True)
        yield sse_event("enrichment_complete", ctx.enrichment_data)

    logger.info(
        "pipeline.enrichment",
        extra={
            "video_id": ctx.video_summary_id,
            "quiz_count": len(ctx.enrichment_data.get("quiz") or []) if ctx.enrichment_data else 0,
            "flashcard_count": len(ctx.enrichment_data.get("flashcards") or [])
            if ctx.enrichment_data
            else 0,
            "scenario_count": len(ctx.enrichment_data.get("scenarios") or [])
            if ctx.enrichment_data
            else 0,
            "used_synthesis_fallback": not (
                ctx.extraction_data and _has_meaningful_data(ctx.extraction_data)
            ),
        },
    )
