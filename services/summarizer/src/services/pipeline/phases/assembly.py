"""Phase 7: Assembly — assemble tabs, save to DB, cache, and emit done."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncGenerator

from src.config import settings
from src.services.cache.response_cache import response_cache
from src.services.media.s3_client import S3Client
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.moment_frame_fill import fill_moment_frames
from src.services.pipeline.pipeline_helpers import (
    normalize_segments,
    run_task_with_heartbeat,
    sse_event,
)
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.status_callback import send_video_status_background
from src.services.transcription.whisper_transcriber import translate_audio_to_english
from src.services.vector.store import store_default_output_chunks, store_transcript_chunks
from src.services.video.description_analyzer import DescriptionAnalysis
from src.utils.language_utils import get_language_name

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


async def run_phase_assembly(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Assemble response, emit tab_ready events, save to DB, and emit done."""
    assert ctx.video_data is not None
    assert ctx.triage is not None

    video_meta = {
        "videoId": ctx.video_summary_id,
        "title": ctx.video_data.title,
        "channel": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "chapters": [
            {"start_time": ch.start_time, "end_time": ch.end_time, "title": ch.title}
            for ch in (ctx.video_data.chapters or [])
        ],
    }
    desc_analysis_dict = (
        ctx.description_analysis.to_dict()
        if isinstance(ctx.description_analysis, DescriptionAnalysis)
        and ctx.description_analysis.has_content
        else None
    )
    assembled = assemble_response(
        triage=ctx.triage_dict,
        extraction=ctx.extraction_data,
        enrichment=ctx.enrichment_data,
        synthesis=ctx.synthesis_dict or None,
        video_meta=video_meta,
        description_analysis=desc_analysis_dict,
        frames=ctx.scene_frames_for_assembly,
        gallery_frames=ctx.scene_frames_gallery,
        all_frames=ctx.scene_frames_all,
        frame_descriptions=ctx.frame_descriptions or None,
    )

    # Store assembled output on ctx for translation phase
    ctx.assembled_tabs = assembled.get("tabs", [])
    ctx.assembled_meta = assembled.get("meta", {})

    # Surface the extraction coverage metric (how far the timestamped output
    # reaches vs. duration, + dropped-batch counts) for per-doc monitoring.
    coverage = getattr(ctx, "extraction_coverage", None)
    if coverage and ctx.assembled_meta is not None:
        ctx.assembled_meta["extractionCoverage"] = coverage

    # Degraded run: dropped extraction batches or critically-low coverage mean
    # the output is a partial result. Flag it on meta (served to the FE, which
    # renders the "partial result — retry" affordance) — set only when True so
    # clean runs carry no extra field.
    degraded = coverage_is_degraded(coverage)
    if degraded and ctx.assembled_meta is not None:
        ctx.assembled_meta["degraded"] = True

    # Emit tab_ready events (progressive rendering). moment_track tabs are
    # held back until the exact-timestamp frame fill completes so they stream
    # WITH their guaranteed images — every other tab renders immediately. Each
    # event carries its index in the persisted order (`position`) so the
    # client can slot a late tab where the DB doc will have it, instead of
    # appending it last and reshuffling once the doc becomes authoritative.
    # The payload is a copy — the persisted tab dict never gains `position`.
    all_tabs = assembled.get("tabs", [])
    moment_positions = [i for i, t in enumerate(all_tabs) if t.get("component") == "moment_track"]
    for position, tab in enumerate(all_tabs):
        if position in moment_positions:
            continue
        yield sse_event("tab_ready", {**tab, "position": position})

    if moment_positions:
        # Best-effort image guarantee for the value-moment gallery: extract a
        # frame at each still-frameless moment's own timestamp (never raises).
        # Heartbeats keep the SSE hop alive — the fill can run for minutes
        # (stream-URL pass + local-download fallback) and this phase is not
        # under run_parallel_phases' keepalive.
        fill_task = asyncio.ensure_future(fill_moment_frames(all_tabs, ctx.youtube_id))
        async for keepalive in run_task_with_heartbeat(fill_task):
            yield keepalive
        await fill_task
        for position in moment_positions:
            yield sse_event("tab_ready", {**all_tabs[position], "position": position})

    # Drops = the plan's registry check (unregistered dataSource, no sibling)
    # + the assembler's per-tab accounting — assembly also
    # ADDS tabs (overview, backfill, filmstrip, fallbacks), so the old
    # designed-minus-assembled subtraction masked drops and could go negative.
    plan_dropped = ctx.plan_result.dropped_tabs if ctx.plan_result else []
    dropped_tabs = [*plan_dropped, *assembled.get("dropped", [])]
    # Designed = what the planner designed. When plan validation left nothing
    # and the domain defaults stand in, only the drops were the planner's.
    plan_fallback = bool(ctx.plan_result and ctx.plan_result.plan_fallback)
    tabs_designed = len(plan_dropped) + (0 if plan_fallback else len(ctx.triage.tabs))
    logger.info(
        "pipeline.assembly",
        extra={
            "video_id": ctx.video_summary_id,
            "tabs_designed": tabs_designed,
            "tabs_assembled": len(assembled.get("tabs", [])),
            "tabs_dropped": len(dropped_tabs),
            "dropped_detail": dropped_tabs,
            "components_used": [t["component"] for t in assembled.get("tabs", [])],
        },
    )

    # Emit complete event
    processing_time = int(ctx.timer.elapsed() * 1000)
    yield sse_event(
        "complete",
        {
            "tabCount": len(assembled.get("tabs", [])),
            "processingTimeMs": processing_time,
            "degraded": degraded,
        },
    )

    # Save result. Non-English videos stay "processing" until the translation
    # phase persists the sourceLanguage block and owns the "completed"
    # transition — so an interrupted translation leaves a retriable doc rather
    # than a fake-completed English-only one.
    result: dict = {
        "status": "processing" if ctx.source_language_code else "completed",
        "youtubeId": ctx.youtube_id,
        "title": ctx.video_data.title,
        "creator": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "thumbnailUrl": ctx.video_data.thumbnail_url,
        "meta": assembled.get("meta", {}),
        "tabs": assembled.get("tabs", []),
        "language": ctx.language,
        "isRTL": ctx.is_rtl,
        # Version stamp — the api's serve path regens docs whose stored
        # version differs from the canonical pipeline-version.json; docs
        # WITHOUT the field predate stamping and are served as-is.
        "pipelineVersion": settings.PIPELINE_VERSION,
        "pipeline": {
            "triage": ctx.triage_dict,
            "extraction": ctx.extraction_data,
            "enrichment": ctx.enrichment_data,
            "synthesis": ctx.synthesis_dict,
            "assembly": {
                "tabsDesigned": tabs_designed,
                "tabsAssembled": len(assembled.get("tabs", [])),
                "tabsDropped": len(dropped_tabs),
                "droppedTabs": dropped_tabs,
                **({"planFallback": True} if plan_fallback else {}),
            },
        },
        "processedAt": datetime.now(timezone.utc),
        "processingTimeMs": processing_time,
    }
    if degraded:
        # Top-level mirror of meta.degraded — queryable by the admin run badge
        # without unpacking meta. Only written when True; absence means clean
        # (or pre-feature doc).
        result["degraded"] = True

    saved = await asyncio.to_thread(
        ctx.repository.save_structured_result, ctx.video_summary_id, result
    )
    if not saved:
        # A global purge deleted the row mid-run; re-creating its Redis, Qdrant
        # and S3 artifacts would orphan them again. Drop the result.
        ctx.row_deleted = True
        logger.warning("pipeline_row_deleted_mid_run video_summary_id=%s", ctx.video_summary_id)
        return
    # Mirror onto userVideos + WS fan-out (best-effort). Non-English videos
    # stay "processing" here — the translation phase owns their "completed".
    # Fire-and-forget: a slow API gateway must not stall the SSE stream.
    send_video_status_background(ctx.video_summary_id, None, result["status"])

    # Cache in Redis (non-blocking, best-effort).
    # English-source videos: cache the assembled (English) payload now — there
    # is no translation phase to wait for. Non-English: skip; the translation
    # phase runs next, builds the source-language artifact, and writes the final
    # payload (English primary + sourceLanguage) to Redis. Caching here for
    # non-English would freeze a sourceLanguage-less payload into Redis for the
    # whole TTL and hide the FE language toggle on every cache hit.
    if settings.REDIS_ENABLED and not ctx.source_language_code:
        from src.routes.cached_response import build_frontend_response

        frontend_response = build_frontend_response(result)
        try:
            await response_cache.set_response(ctx.youtube_id, frontend_response)
        except (OSError, ConnectionError) as e:
            logger.debug("Redis cache failed (non-critical): %s", e)

    # Store transcript chunks in Qdrant (background, non-blocking)
    if settings.QDRANT_ENABLED:

        def _log_qdrant_error(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error("Qdrant store failed: %s", exc)

        # The transcript is still in the source language even though generated
        # content is English. For non-English source, translate it to English so
        # embeddings live in the model's strongest language; keep the original
        # for display.
        english_text = ctx.clean_text
        original_text = None
        if ctx.source_language_code:
            # Try Whisper translate for English text
            try:
                translated = await translate_audio_to_english(
                    ctx.youtube_id, cached_audio_path=ctx.audio_path
                )
                if translated and len(translated) > len(ctx.clean_text) * 0.1:
                    english_text = translated
                    original_text = ctx.clean_text
                    logger.info(
                        "Using Whisper-translated English text for Qdrant "
                        "(%d chars, original %s: %d chars)",
                        len(english_text),
                        get_language_name(ctx.source_language_code),
                        len(original_text),
                    )
                else:
                    # Whisper translate failed or produced garbage — use LLM fallback
                    logger.warning(
                        "Whisper translate too short or failed, using original text for Qdrant"
                    )
            except Exception as e:
                logger.warning("Whisper translate for Qdrant failed: %s — using original text", e)

        task = asyncio.create_task(
            store_transcript_chunks(
                ctx.youtube_id,
                english_text,
                language=ctx.source_language_code or "en",
                transcript_original=original_text,
                segments=ctx.transcript_data.segments if ctx.transcript_data else None,
            )
        )
        task.add_done_callback(_log_qdrant_error)

        # Index assembled output content (tabs + props) alongside transcript.
        # Generation is English-canonical, so the assembled tabs are always
        # English and can be indexed here for every video — the translation
        # phase only adds the source-language artifact, it does not change the
        # English tabs.
        output_task = asyncio.create_task(
            store_default_output_chunks(
                ctx.youtube_id,
                ctx.assembled_tabs or [],
                language="en",
            ),
            name=f"store_output_{ctx.youtube_id}",
        )
        output_task.add_done_callback(_log_qdrant_error)

    # Store raw transcript to S3 (background, non-blocking, best-effort)
    if (
        S3Client.is_available()
        and ctx.transcript_data
        # An S3-hit run would re-store the blob it just read with source="s3",
        # decaying the recorded origin (which layer originally produced the
        # transcript) to "s3" after one regen. The blob is already in S3, so
        # skipping is lossless.
        and ctx.transcript_data.source != "s3"
    ):

        async def _store_transcript() -> None:
            try:
                from src.services.transcription.transcript_store import transcript_store

                normalized = normalize_segments(ctx.transcript_data.segments)
                s3_key = await transcript_store.store(
                    youtube_id=ctx.youtube_id,
                    segments=normalized,
                    source=ctx.transcript_data.source,
                    language=ctx.source_language_code or ctx.transcript_data.language,
                )
                await asyncio.to_thread(
                    ctx.repository._collection.update_one,
                    {"_id": ctx.video_summary_id},
                    {"$set": {"rawTranscriptRef": s3_key}},
                )
                logger.info("Stored transcript in S3: %s", s3_key)
            except Exception as e:
                logger.warning("Transcript S3 storage failed (non-critical): %s", e)

        def _log_transcript_error(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.warning("Transcript S3 background task failed: %s", exc)

        transcript_task = asyncio.create_task(_store_transcript())
        transcript_task.add_done_callback(_log_transcript_error)

    # English-source videos are final here. Non-English videos still have the
    # translation phase ahead of them, which owns the terminal event (emitted by
    # the pipeline runner after translation persists the sourceLanguage block)
    # so the FE refetch on `done` sees a "completed" doc with the toggle.
    if not ctx.source_language_code:
        yield sse_event(
            "done",
            {
                "videoSummaryId": ctx.video_summary_id,
                "processingTimeMs": processing_time,
                "degraded": degraded,
            },
        )
        yield "data: [DONE]\n\n"
