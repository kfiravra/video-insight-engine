"""Phase 5: Synthesis — TLDR, takeaways, master summary."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.pipeline_helpers import sse_event, truncate_json_safely
from src.services.pipeline.synthesis import synthesize

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def _build_hierarchical_synthesis_input(ctx: PipelineContext) -> str:
    """Build synthesis input from chapter summaries + extraction data for long videos.

    Keeps total input under ~8K tokens by summarizing chapter structure
    and truncating extraction data.
    """
    assert ctx.video_data is not None

    lines: list[str] = []
    lines.append(
        f"Video: {ctx.video_data.title} ({round((ctx.video_data.duration or 0) / 60)} min)"
    )

    if ctx.chapters:
        lines.append(f"\nChapters ({len(ctx.chapters)}):")
        for ch in ctx.chapters:
            # Format time range
            start_min = round(ch.start_seconds / 60)
            end_min = round(ch.end_seconds / 60)
            lines.append(f"  - {ch.title} ({start_min}-{end_min} min)")

    lines.append("\nExtracted content summary:")
    extraction_summary = (
        truncate_json_safely(ctx.extraction_data, 6000) if ctx.extraction_data else ""
    )
    lines.append(extraction_summary)

    return "\n".join(lines)


async def run_phase_synthesis(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Synthesize TLDR, takeaways, and master summary from extraction data."""
    llm_feature_var.set("summarize:synthesis")
    assert ctx.video_data is not None
    assert ctx.triage is not None

    # Skip if synthesis was already populated during extraction retry
    if ctx.synthesis_dict:
        logger.info("[pipeline] Synthesis already populated (from extraction retry), skipping")
        yield sse_event(
            "synthesis_complete",
            {
                "tldr": ctx.synthesis_dict.get("tldr", ""),
                "keyTakeaways": ctx.synthesis_dict.get("keyTakeaways", []),
                "masterSummary": ctx.synthesis_dict.get("masterSummary", ""),
                "seoDescription": ctx.synthesis_dict.get("seoDescription", ""),
            },
        )
        return

    # Hierarchical mode for long videos with chapters
    if ctx.chapters and len(ctx.chapters) > 5:
        extraction_summary = _build_hierarchical_synthesis_input(ctx)
    else:
        extraction_summary = (
            truncate_json_safely(ctx.extraction_data, 4000) if ctx.extraction_data else ""
        )

    try:
        synthesis_result = await synthesize(
            ctx.llm_service,
            title=ctx.video_data.title,
            channel=ctx.video_data.channel,
            duration=ctx.video_data.duration,
            output_type=ctx.triage.primary_tag,
            extraction_summary=extraction_summary,
            video_context=ctx.video_memory,
        )
        ctx.synthesis_dict = synthesis_result.model_dump(by_alias=True)
        yield sse_event(
            "synthesis_complete",
            {
                "tldr": synthesis_result.tldr,
                "keyTakeaways": synthesis_result.key_takeaways,
                "masterSummary": synthesis_result.master_summary,
                "seoDescription": synthesis_result.seo_description,
            },
        )
    except Exception as e:
        logger.warning("[pipeline] Synthesis failed (non-critical): %s", e)
        ctx.synthesis_dict = {}
        yield sse_event(
            "synthesis_complete",
            {
                "tldr": "",
                "keyTakeaways": [],
                "masterSummary": "",
                "seoDescription": "",
            },
        )

    logger.info(
        "pipeline.synthesis",
        extra={
            "video_id": ctx.video_summary_id,
            "has_tldr": bool(ctx.synthesis_dict.get("tldr")),
            "takeaway_count": len(ctx.synthesis_dict.get("keyTakeaways", [])),
        },
    )
