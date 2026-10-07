"""Phase 5: Synthesis — TLDR, takeaways, master summary.

Emits the run's second ``synthesis_complete``: the full four-field superset of
the memory-done one (``phases/memory.py``). Memory fills a field synthesis left
empty, so the stored meta keeps what the hero already showed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.pipeline_helpers import sse_event, truncate_json_safely
from src.services.pipeline.synthesis import synthesize

if TYPE_CHECKING:
    from src.models.memory_types import MemoryResult
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


_TEXT_FIELDS = ("tldr", "masterSummary", "seoDescription")


def fill_from_memory(synthesis: dict[str, Any], memory: MemoryResult | None) -> dict[str, Any]:
    """``synthesis`` with memory's tldr/takeaways where synthesis has none.

    The memory-done event already showed them (the hero); a failed or thin
    synthesis must not leave the stored meta without what the viewer saw.
    """
    if memory is None:
        return synthesis
    filled = dict(synthesis)
    if not filled.get("tldr") and memory.tldr:
        filled["tldr"] = memory.tldr
    if not filled.get("keyTakeaways") and memory.takeaways:
        filled["keyTakeaways"] = list(memory.takeaways)
    return filled


def synthesis_event(synthesis: dict[str, Any]) -> dict[str, object]:
    """The synthesis-done ``synthesis_complete`` payload: all four fields (the superset)."""
    payload: dict[str, object] = {key: synthesis.get(key) or "" for key in _TEXT_FIELDS}
    payload["keyTakeaways"] = list(synthesis.get("keyTakeaways") or [])
    return payload


async def _synthesize(ctx: PipelineContext) -> dict[str, Any]:
    """One synthesis call → its dict; ``{}`` when it failed (non-critical)."""
    assert ctx.video_data is not None
    assert ctx.triage is not None
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
    except Exception as e:
        logger.warning("[pipeline] Synthesis failed (non-critical): %s", e)
        return {}
    return synthesis_result.model_dump(by_alias=True)


async def run_phase_synthesis(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Synthesize TLDR, takeaways and master summary; emit the full ``synthesis_complete``."""
    llm_feature_var.set("summarize:synthesis")
    if ctx.synthesis_dict:
        # Already populated by the extraction retry — re-emit, don't re-run.
        logger.info("[pipeline] Synthesis already populated (from extraction retry), skipping")
        synthesis = ctx.synthesis_dict
    else:
        synthesis = await _synthesize(ctx)
    ctx.synthesis_dict = fill_from_memory(synthesis, getattr(ctx, "memory", None))
    yield sse_event("synthesis_complete", synthesis_event(ctx.synthesis_dict))

    logger.info(
        "pipeline.synthesis",
        extra={
            "video_id": ctx.video_summary_id,
            "has_tldr": bool(ctx.synthesis_dict.get("tldr")),
            "takeaway_count": len(ctx.synthesis_dict.get("keyTakeaways", [])),
        },
    )
