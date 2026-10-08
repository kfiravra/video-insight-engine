"""Phase 4: Extraction — adaptive structured extraction, then coverage and quality metrics."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.models.schemas import ErrorCode, ProcessingStatus
from src.services.pipeline.extraction_quality import check_extraction_quality
from src.services.pipeline.extractor import extract
from src.services.pipeline.memory import format_clock
from src.services.pipeline.pipeline_helpers import normalize_segments, sse_event
from src.services.pipeline.post_processor import (
    COVERAGE_CRITICAL_RATIO,
    COVERAGE_GATE_RATIO,
    compute_extraction_coverage,
)
from src.services.pipeline.prompt_builder import format_gallery_frames_for_extraction
from src.services.pipeline.scene_frames import inject_visual_context
from src.services.transcript.render import render_transcript

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def build_prompt_transcript(ctx: PipelineContext) -> str:
    """The transcript the extraction prompt reads: ``[m:ss]``-marked, from segments.

    Markers live only in the prompt — ``ctx.clean_text`` (Qdrant, faithfulness)
    and the S3 blob stay unmarked. The segments are ``ctx.prompt_segments``
    (sponsor reads cut), the ones plan and memory read. Metadata-only
    transcripts carry no segments, so they fall back to ``ctx.clean_text``.
    """
    segments = ctx.prompt_segments
    marked = render_transcript(segments, source_language=ctx.source_language_code)
    if not marked:
        return ctx.clean_text
    # Phase 2.5 annotated clean_text, which this text no longer derives from:
    # same injection here so extraction keeps today's visual facts (1c.2 moves
    # both to a <visual_annotations> block).
    if ctx.frame_descriptions or ctx.scene_frames_all:
        marked = inject_visual_context(
            marked, segments, ctx.frame_descriptions, ctx.scene_frames_all
        )
    return marked


def _record_extraction_coverage(
    ctx: PipelineContext,
    batches_total: int | None,
    batches_succeeded: int | None,
) -> None:
    """Compute + store the extraction coverage metric and warn on under-coverage.

    Detects the failure where a long video's timestamped content stops far
    short of its duration (the 4.5h-video-stops-at-1:34 bug). Stored on the
    context so the assembly phase can surface it into meta.
    """
    duration = float(getattr(ctx.video_data, "duration", 0) or 0)
    coverage = compute_extraction_coverage(ctx.extraction_data, duration)
    if coverage is None:
        return

    if batches_total is not None:
        coverage["batchesTotal"] = batches_total
        coverage["batchesSucceeded"] = batches_succeeded
        coverage["batchesDropped"] = batches_total - (batches_succeeded or 0)

    # Flag critically-low coverage distinctly from a normal long-tail thinning:
    # a ratio this low means the transcript itself was truncated/incomplete
    # (e.g. a Gemini-fallback that only captured the first few minutes), not
    # that extraction merely got sparse toward the end. Surfaced on meta so the
    # FE/admin can show a "transcript incomplete" signal.
    is_critical = coverage["ratio"] < COVERAGE_CRITICAL_RATIO
    coverage["critical"] = is_critical
    ctx.extraction_coverage = coverage

    dropped = coverage.get("batchesDropped", 0)
    if is_critical:
        logger.error(
            "pipeline.extraction_coverage_critical",
            extra={
                "video_id": ctx.video_summary_id,
                "max_timestamp": coverage["maxTimestamp"],
                "duration": coverage["duration"],
                "ratio": coverage["ratio"],
                "tail_missing_seconds": coverage["tailMissingSeconds"],
                "batches_dropped": dropped,
            },
        )
    elif coverage["ratio"] < COVERAGE_GATE_RATIO or dropped:
        logger.warning(
            "pipeline.extraction_coverage",
            extra={
                "video_id": ctx.video_summary_id,
                "max_timestamp": coverage["maxTimestamp"],
                "duration": coverage["duration"],
                "ratio": coverage["ratio"],
                "tail_missing_seconds": coverage["tailMissingSeconds"],
                "batches_dropped": dropped,
            },
        )


def _record_extraction_quality(ctx: PipelineContext) -> None:
    """Log the share of planned dataSources the extraction populated (metric only).

    The synthesis-fed retry this score used to gate is gone (pipeline-1min 1c.5):
    7 firings in 55 runs, 0 of the 3 measurable ones improved. The numbers sit in the
    message because the stdlib log handler drops ``extra``.
    """
    if ctx.plan_result is None or not ctx.extraction_data:
        return
    quality = check_extraction_quality(ctx.plan_result.tabs, ctx.extraction_data)
    logger.info(
        "pipeline.extraction_quality score=%.2f populated=%d/%d empty=%s",
        quality.score,
        quality.populated,
        quality.total,
        quality.empty_fields,
        extra={
            "video_id": ctx.video_summary_id,
            "score": quality.score,
            "populated": quality.populated,
            "total": quality.total,
            "empty_fields": quality.empty_fields,
        },
    )


def _segment_dicts(segments: list[Any]) -> list[dict]:
    """Transcript segments (dicts or ``TranscriptSegment`` objects) → ``startMs``/``endMs`` dicts."""
    seg_as_dicts = []
    for seg in segments:
        if isinstance(seg, dict):
            seg_as_dicts.append(seg)
        elif hasattr(seg, "text"):
            d: dict = {"text": seg.text}
            if hasattr(seg, "startMs"):
                d["startMs"] = seg.startMs
                d["endMs"] = getattr(seg, "endMs", seg.startMs)
            elif hasattr(seg, "start"):
                d["start"] = seg.start
                d["duration"] = getattr(seg, "duration", 0)
            seg_as_dicts.append(d)
    return normalize_segments(seg_as_dicts)


def memory_outline_for_chapters(ctx: PipelineContext) -> list[dict[str, str]] | None:
    """The memory outline in the chunker's shape (``{start: "m:ss", end, title}``).

    ``None`` without a memory outline: chapter_detect then runs only when
    batching needs chapters (1b.6).
    """
    memory = ctx.memory
    if memory is None or not memory.outline:
        return None
    return [
        {"start": format_clock(s.start), "end": format_clock(s.end), "title": s.title}
        for s in memory.outline
    ]


def _description_chapters(ctx: PipelineContext) -> list[dict] | None:
    """Tier 2 of chapter detection: author-listed timestamps from the description."""
    da = ctx.description_analysis
    if da is None or not getattr(da, "timestamps", None):
        return None
    return [{"seconds": t.seconds, "label": t.label} for t in da.timestamps]


async def _split_chapters(
    ctx: PipelineContext, video_info: dict, prompt_transcript: str
) -> list[Any] | None:
    """Chapters for chunked extraction (None = standard extraction).

    Sliced from ``ctx.prompt_segments`` — the segments the marked transcript
    was rendered from — so chapter text and the prompt agree.
    """
    from src.services.transcription.transcript_chunker import split_transcript_into_chapters

    try:
        chapters = await split_transcript_into_chapters(
            video_data=video_info,
            segments=_segment_dicts(ctx.prompt_segments),
            transcript=prompt_transcript,
            llm_service=ctx.llm_service,
            description_chapters=_description_chapters(ctx),
            memory_outline=memory_outline_for_chapters(ctx),
        )
    except Exception as e:
        logger.warning(
            "Chapter splitting failed (non-critical): %s — falling back to standard extraction",
            e,
        )
        return None
    ctx.chapters = chapters
    logger.info("Prepared %d chapters for chunked extraction", len(chapters))
    return chapters


async def run_phase_extraction(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run adaptive extraction, then record its coverage and quality metrics."""
    llm_feature_var.set("summarize:extraction")
    assert ctx.triage is not None
    assert ctx.video_data is not None

    video_info = {
        "title": ctx.video_data.title,
        "channel": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "chapters": getattr(ctx.video_data, "chapters", None),
        # chapter_detect reads it; without the key it ran on an empty description (A9).
        "description": ctx.video_data.description,
    }

    prompt_transcript = build_prompt_transcript(ctx)

    # Chapter splitting for long videos (> CHUNKED_EXTRACTION_THRESHOLD)
    chapters = None
    duration = ctx.video_data.duration or 0
    if duration > settings.CHUNKED_EXTRACTION_THRESHOLD and ctx.transcript_data:
        chapters = await _split_chapters(ctx, video_info, prompt_transcript)

    # Frames (stage 2b) are ready before extraction (stage 4) — fold their
    # captions into the prompt so the LLM can ground visual claims and warrant a
    # filmstrip/diagram. Bounded to 12 captioned frames to cap token cost.
    frame_context = format_gallery_frames_for_extraction(
        ctx.scene_frames_gallery,
        ctx.frame_descriptions,
    )
    batches_total: int | None = None
    batches_succeeded: int | None = None
    try:
        async for evt in extract(
            ctx.llm_service,
            ctx.triage,
            prompt_transcript,
            video_info,
            chapters=chapters,
            video_context=ctx.video_memory,
            frame_context=frame_context,
        ):
            event_name = evt["event"]
            yield sse_event(event_name, {k: v for k, v in evt.items() if k != "event"})
            if event_name == "extraction_complete":
                ctx.extraction_data = evt.get("data")
                batches_total = evt.get("batches_total")
                batches_succeeded = evt.get("batches_succeeded")
    except (ValueError, asyncio.TimeoutError) as e:
        logger.warning(
            "[pipeline] Extraction raised %s for video_id=%s: %s — continuing with empty extraction",
            type(e).__name__,
            ctx.video_summary_id,
            e,
        )

    if not ctx.extraction_data:
        logger.error("[pipeline] Extraction produced no data for video_id=%s", ctx.video_summary_id)
        await asyncio.to_thread(
            ctx.repository.update_status,
            ctx.video_summary_id,
            ProcessingStatus.FAILED,
            "Extraction failed",
            ErrorCode.LLM_ERROR,
        )
        yield sse_event(
            "error",
            {"message": "Extraction failed to produce data", "code": ErrorCode.LLM_ERROR.value},
        )
        return

    logger.info(
        "pipeline.extraction",
        extra={
            "video_id": ctx.video_summary_id,
            "domains_extracted": list(ctx.extraction_data.keys())
            if isinstance(ctx.extraction_data, dict)
            else [],
            "items_per_domain": {
                k: sum(1 for v in d.values() if isinstance(v, list) and len(v) > 0)
                for k, d in ctx.extraction_data.items()
                if isinstance(d, dict)
            }
            if isinstance(ctx.extraction_data, dict)
            else {},
        },
    )

    _record_extraction_coverage(ctx, batches_total, batches_succeeded)
    _record_extraction_quality(ctx)
