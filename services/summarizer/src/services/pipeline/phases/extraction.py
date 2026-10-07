"""Phase 4: Extraction — adaptive structured extraction with count validation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.models.schemas import ErrorCode, ProcessingStatus
from src.services.pipeline.extraction_quality import (
    build_synthesis_fed_retry_prompt,
    check_extraction_quality,
    decide_extraction_retry,
    merge_retry_fields,
)
from src.services.pipeline.extractor import extract
from src.services.pipeline.pipeline_helpers import (
    normalize_segments,
    sse_event,
    truncate_json_safely,
)
from src.services.pipeline.post_processor import (
    COVERAGE_CRITICAL_RATIO,
    COVERAGE_GATE_RATIO,
    compute_extraction_coverage,
    validate_extraction_counts,
)
from src.services.pipeline.prompt_builder import format_gallery_frames_for_extraction
from src.services.pipeline.scene_frames import inject_visual_context
from src.services.pipeline.synthesis import synthesize
from src.services.transcript.render import render_transcript

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def build_prompt_transcript(ctx: PipelineContext) -> str:
    """The transcript the extraction prompt reads: ``[m:ss]``-marked, from segments.

    Markers live only in the prompt — ``ctx.clean_text`` (Qdrant, faithfulness)
    and the S3 blob stay unmarked. Metadata-only transcripts carry no segments,
    so they fall back to ``ctx.clean_text``.
    """
    segments = ctx.transcript_data.segments if ctx.transcript_data else []
    marked = render_transcript(segments)
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


async def _attempt_synthesis_fed_retry(
    ctx: PipelineContext,
    plan_tabs: list[dict],
    quality: Any,
    video_info: dict,
    chapters: Any,
    retry_fields: list[str] | None = None,
) -> None:
    """Run synthesis early, then re-extract with evidence-based guidance.

    Mutates ctx.extraction_data (if retry improves quality) and ctx.synthesis_dict.
    When ``retry_fields`` is provided, it replaces ``quality.empty_fields`` in
    the retry prompt — useful when the caller has merged in hard-miss
    annotations from count validation.
    """
    if ctx.video_data is None or ctx.triage is None:
        logger.warning("[pipeline] Cannot retry extraction: missing video_data or triage")
        return

    extraction_summary = truncate_json_safely(ctx.extraction_data, 4000)
    synthesis_result = await synthesize(
        ctx.llm_service,
        title=ctx.video_data.title,
        channel=ctx.video_data.channel,
        duration=ctx.video_data.duration,
        output_type=ctx.triage.primary_tag,
        extraction_summary=extraction_summary,
        video_context=ctx.video_dna_compact,
    )
    ctx.synthesis_dict = synthesis_result.model_dump(by_alias=True)

    fields_for_prompt = retry_fields if retry_fields else quality.empty_fields
    retry_prompt = build_synthesis_fed_retry_prompt(
        fields_for_prompt,
        ctx.synthesis_dict,
    )

    retry_data = None
    # Synthesis-fed retry always escalates to the primary model — even when
    # EXTRACTION_USE_FAST_FIRST is on. The first pass already proved the fast
    # model under-extracted; doubling down on it just burns tokens.
    async for evt in extract(
        ctx.llm_service,
        ctx.triage,
        build_prompt_transcript(ctx),
        video_info,
        chapters=chapters,
        video_context=ctx.video_dna_compact,
        extra_instruction=retry_prompt,
        force_primary_model=True,
    ):
        if evt["event"] == "extraction_complete":
            retry_data = evt.get("data")

    if retry_data:
        retry_quality = check_extraction_quality(plan_tabs, retry_data)
        if retry_quality.score > quality.score:
            ctx.extraction_data = retry_data
            logger.info(
                "[pipeline] Extraction retry improved quality: %.2f → %.2f",
                quality.score,
                retry_quality.score,
            )
        else:
            logger.info(
                "[pipeline] Extraction retry did not improve quality (%.2f vs %.2f), keeping original",
                retry_quality.score,
                quality.score,
            )


async def run_phase_extraction(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run adaptive extraction and optional count-validation retry."""
    llm_feature_var.set("summarize:extraction")
    assert ctx.triage is not None
    assert ctx.video_data is not None

    video_info = {
        "title": ctx.video_data.title,
        "channel": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "chapters": getattr(ctx.video_data, "chapters", None),
    }

    prompt_transcript = build_prompt_transcript(ctx)

    # Chapter splitting for long videos (>30 min)
    chapters = None
    duration = ctx.video_data.duration or 0
    if duration > settings.CHUNKED_EXTRACTION_THRESHOLD and ctx.transcript_data:
        try:
            from src.services.transcription.transcript_chunker import split_transcript_into_chapters

            raw_segments = (
                ctx.transcript_data.segments if hasattr(ctx.transcript_data, "segments") else []
            )
            # Convert TranscriptSegment objects to dicts, then normalize to startMs/endMs
            seg_as_dicts = []
            for seg in raw_segments:
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
            seg_dicts = normalize_segments(seg_as_dicts)

            # Tier 2 of chapter detection: author-listed timestamps from the
            # video description (used when YouTube has no native chapters).
            description_chapters = None
            da = ctx.description_analysis
            if da is not None and getattr(da, "timestamps", None):
                description_chapters = [
                    {"seconds": t.seconds, "label": t.label} for t in da.timestamps
                ]

            chapters = await split_transcript_into_chapters(
                video_data=video_info,
                segments=seg_dicts,
                transcript=prompt_transcript,
                llm_service=ctx.llm_service,
                description_chapters=description_chapters,
            )
            ctx.chapters = chapters
            logger.info("Prepared %d chapters for chunked extraction", len(chapters))
        except Exception as e:
            logger.warning(
                "Chapter splitting failed (non-critical): %s — falling back to standard extraction",
                e,
            )
            chapters = None

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
            video_context=ctx.video_dna_compact,
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

    # Quality check + conditional synthesis-fed retry.
    # Count validation runs alongside the quality check so that a hard miss
    # on a manifest-planned field (e.g. plan said 6 tips, extraction returned 0)
    # can trigger a retry even when the overall score is above threshold.
    if ctx.extraction_data and ctx.triage is not None and ctx.plan_result is not None:
        plan_tabs = ctx.plan_result.tabs
        quality = check_extraction_quality(plan_tabs, ctx.extraction_data)
        count_warnings = validate_extraction_counts(
            ctx.plan_result,
            ctx.extraction_data,
            content_tags=ctx.plan_result.content_tags,
        )
        retry_decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_tags=ctx.plan_result.content_tags,
            content_format=ctx.content_format,
            content_traits=ctx.content_traits,
        )

        logger.info(
            "pipeline.extraction_quality",
            extra={
                "video_id": ctx.video_summary_id,
                "score": quality.score,
                "populated": quality.populated,
                "total": quality.total,
                "empty_fields": quality.empty_fields,
                "count_warnings": count_warnings,
                "hard_miss_fields": retry_decision.hard_miss_fields,
            },
        )

        if retry_decision.should_retry:
            logger.warning(
                "[pipeline] Extraction retry triggered (%s) for video_id=%s — attempting synthesis-fed retry",
                retry_decision.reason,
                ctx.video_summary_id,
            )
            retry_fields = merge_retry_fields(
                quality.empty_fields,
                retry_decision.hard_miss_fields,
                count_warnings,
            )
            try:
                await _attempt_synthesis_fed_retry(
                    ctx,
                    plan_tabs,
                    quality,
                    video_info,
                    chapters,
                    retry_fields=retry_fields,
                )
            except Exception as e:
                logger.warning("[pipeline] Extraction retry failed (non-critical): %s", e)
        elif count_warnings:
            logger.warning(
                "[pipeline] Extraction count mismatch (not retried) for video_id=%s: %s",
                ctx.video_summary_id,
                count_warnings,
            )
