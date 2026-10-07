"""Phase orchestration for one pipeline run.

Split out from :mod:`src.routes.pipeline_runner` to keep that file under the
project's 500-line cap. The runner (``stream_summarization``) owns the entry,
the Redis response-cache fast path, the Langfuse trace and the failure
handling; this module runs the phases in order inside that trace, records the
transcript provenance, releases the run's downloads, and persists
``pipeline.timing`` when the phases finish or fail.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.pipeline_faithfulness import _drain_faithfulness, _launch_faithfulness_check
from src.routes.run_timing import log_run_summary, mark_phase, persist_run_timing
from src.services.observability import update_trace_metadata
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.enrichment import _has_meaningful_data
from src.services.pipeline.phases import (
    run_phase_assembly,
    run_phase_enrichment,
    run_phase_extraction,
    run_phase_frames,
    run_phase_metadata,
    run_phase_plan,
    run_phase_synthesis,
    run_phase_transcript,
)
from src.services.pipeline.pipeline_helpers import (
    PipelineTimer,
    run_parallel_phases,
    sse_event,
)
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.transcription.transcript_meta import build_transcript_meta

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Transcript provenance
# ─────────────────────────────────────────────────────────────────────────────


async def _record_transcript_outcome(
    ctx: PipelineContext, repository: MongoDBVideoRepository, video_summary_id: str
) -> None:
    """Persist this run's ``transcriptMeta`` — the single write point.

    Called from the ``finally`` around the transcript+frames phase so that
    successful AND failed fetches are recorded: a TranscriptError from the
    fallback chain still leaves ``outcome="failed"`` + ``attempted`` +
    ``errorCode`` on the row, because the phase's own ``finally`` stamped
    ``ctx.transcript_trail`` before re-raising. It has to live INSIDE the
    Langfuse ``pipeline_trace``: the ``except TranscriptError`` in
    ``stream_summarization`` sits outside it, so trace metadata written
    there would silently no-op. Best-effort — it must never raise, or it
    would mask the pipeline's own exception in that ``finally``.
    """
    transcript_data = getattr(ctx, "transcript_data", None)
    trail = getattr(ctx, "transcript_trail", None)
    if transcript_data is None and trail is None:
        # The transcript phase never ran (e.g. metadata failed first).
        return
    try:
        meta = build_transcript_meta(transcript_data, getattr(ctx, "video_data", None), trail)
        update_trace_metadata(
            {
                "transcriptSource": meta["source"],
                "transcriptType": meta["type"],
                "transcriptAttempted": meta["attempted"],
                "transcriptOutcome": meta["outcome"],
            }
        )
        await asyncio.to_thread(repository.set_transcript_meta, video_summary_id, meta)
        logger.info(
            "[pipeline] transcriptMeta youtube_id=%s source=%s outcome=%s attempted=%s",
            ctx.youtube_id,
            meta["source"],
            meta["outcome"],
            meta["attempted"],
        )
    except Exception as exc:
        logger.warning("[pipeline] transcriptMeta record failed for %s: %s", video_summary_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline phase orchestration
# ─────────────────────────────────────────────────────────────────────────────


async def run_pipeline_phases(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timer: PipelineTimer,
) -> AsyncGenerator[str, None]:
    """Run the pipeline with a timing recorder bound for this run.

    Every outgoing SSE chunk stamps the recorder's milestones (first
    ``tab_ready``, ``complete``, ``done``); the timing document is persisted
    once the phases finish or fail.
    """
    timing = start_run_timing()
    try:
        async for event in _run_phases_in_order(ctx, repository, video_summary_id, timer, timing):
            timing.observe_sse(event)
            yield event
    finally:
        try:
            await _release_run_media(ctx)
        finally:
            await persist_run_timing(ctx, repository, video_summary_id, timing)


async def _release_run_media(ctx: PipelineContext) -> None:
    """Stop and delete whatever the t=0 group left running.

    Failed or cancelled runs never reach the phases that close the run's
    downloads; nothing may outlive the run (a cancelled download still lands
    in the timing, so this runs before it is persisted).
    """
    from src.services.pipeline.phases.metadata import release_background_work

    release_background_work(ctx)
    lowres_video = getattr(ctx, "lowres_video", None)
    hires_video = getattr(ctx, "hires_video", None)
    try:
        if lowres_video is not None:
            await lowres_video.close()
    finally:
        if hires_video is not None:
            await hires_video.close()


async def _run_phases_in_order(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timer: PipelineTimer,
    timing: PipelineTimingRecorder,
) -> AsyncGenerator[str, None]:
    """Run every pipeline phase in order, streaming SSE chunks to the caller.

    Lives inside the Langfuse ``pipeline_trace`` context so every LLM span
    attaches to the parent trace. Phase timings are recorded on ``ctx`` for
    the final summary log. Any faithfulness tasks spawned mid-pipeline are
    drained before this coroutine returns so their scores reach Langfuse
    before the trace is flushed.
    """
    spawned_faithfulness: list[asyncio.Task[None]] = []

    try:
        # Phase 1: Metadata — one extract_info + validate_duration; it then
        # starts the t=0 group in the background (captions, description
        # analysis, low-res + 720p downloads) and returns.
        phase_start = time.monotonic()
        async for event in run_phase_metadata(ctx):
            yield event
        mark_phase(ctx, timing, "metadata", phase_start)

        # Phase 2: Transcript + Frames + description (parallel). Transcript
        # waits for the caption fetch, frames for the low-res download, the
        # description member emits its SSE once the analysis lands.
        # ``finally`` so transcriptMeta is recorded whether the phases succeed
        # or raise (cancellation included): a TranscriptError from the
        # fallback chain must still leave outcome="failed" + attempted +
        # errorCode on the row (the phase's own finally already stamped
        # ctx.transcript_trail by the time it propagates here).
        from src.services.pipeline.phases.metadata import after_captions, run_phase_description

        phase_start = time.monotonic()
        group = [after_captions(run_phase_transcript), run_phase_frames, run_phase_description]
        try:
            async for event in run_parallel_phases(group, ctx):
                yield event
        finally:
            mark_phase(ctx, timing, "transcript_frames", phase_start)
            await _record_transcript_outcome(ctx, repository, video_summary_id)

        # Phase 2.5: Inject visual context into transcript (after both phases complete)
        phase_start = time.monotonic()
        if ctx.clean_text and (ctx.frame_descriptions or ctx.scene_frames_all):
            from src.services.pipeline.scene_frames import inject_visual_context

            segments = ctx.transcript_data.segments if ctx.transcript_data else None
            ctx.clean_text = inject_visual_context(
                ctx.clean_text,
                segments,
                ctx.frame_descriptions,
                ctx.scene_frames_all,
            )
            annotation_count = ctx.clean_text.count("[VISUAL at") + ctx.clean_text.count(
                "[ON-SCREEN TEXT at"
            )
            if annotation_count:
                logger.info(
                    "[pipeline] Injected %d visual annotations into transcript", annotation_count
                )
        mark_phase(ctx, timing, "visual_inject", phase_start)

        # Phase 3-4: Plan -> Extraction (sequential — each depends on the previous)
        for phase in [run_phase_plan, run_phase_extraction]:
            phase_start = time.monotonic()
            async for event in phase(ctx):
                yield event
            mark_phase(ctx, timing, phase.__name__.replace("run_phase_", ""), phase_start)
            # Fire-and-forget faithfulness judge once extraction has data. The
            # task copies the current ContextVar state so the Langfuse trace is
            # still attached. We track the task so we can drain it before
            # exiting the trace.
            if phase is run_phase_extraction and ctx.extraction_data:
                spawned = _launch_faithfulness_check(ctx)
                if spawned is not None:
                    spawned_faithfulness.append(spawned)

        # Phase 5: Synthesis + Enrichment. Both read only the extraction output,
        # so they run in parallel — EXCEPT when extraction came back empty:
        # enrichment then falls back to the synthesis output as its context
        # (see enrich()), which forces the sequential order.
        phase_start = time.monotonic()
        if ctx.extraction_data and _has_meaningful_data(ctx.extraction_data):
            async for event in run_parallel_phases(
                [run_phase_synthesis, run_phase_enrichment], ctx
            ):
                yield event
        else:
            for phase in [run_phase_synthesis, run_phase_enrichment]:
                async for event in phase(ctx):
                    yield event
        mark_phase(ctx, timing, "synthesis_enrichment", phase_start)

        # Phase 6: Assembly (needs synthesis + enrichment results)
        phase_start = time.monotonic()
        async for event in run_phase_assembly(ctx):
            yield event
        mark_phase(ctx, timing, "assembly", phase_start)

        # Translation step — translate the English output into the source
        # language and attach it as ``sourceLanguage`` for the FE toggle.
        # A row purged mid-run gets no translation (paid LLM calls) and no `done`.
        if ctx.source_language_code and not ctx.row_deleted:
            phase_start = time.monotonic()
            try:
                from src.services.pipeline.phases.translation import run_phase_translation

                async for event in run_phase_translation(ctx, repository, video_summary_id):
                    yield event
                # Assembly deferred the terminal event for non-English videos so
                # `done` fires only after the source-language surface is final.
                # Reaching here means translation ran to completion (success or a
                # deliberate English-only no-op) and the doc is now "completed".
                yield sse_event(
                    "done",
                    {
                        "videoSummaryId": video_summary_id,
                        "processingTimeMs": int(timer.elapsed() * 1000),
                        # Mirrors the assembly-phase terminal event for the
                        # non-English path — same partial-result signal.
                        "degraded": coverage_is_degraded(
                            getattr(ctx, "extraction_coverage", None),
                        ),
                    },
                )
                yield "data: [DONE]\n\n"
            except Exception as e:
                # Translation raised — leave the doc "processing" (retriable) and
                # emit no terminal event; the FE handles the stream close.
                logger.warning("[pipeline] Translation failed (non-critical): %s", e)
            mark_phase(ctx, timing, "translation", phase_start)

        # The orchestrator's logger: the replay driver + log searches key on it.
        log_run_summary(ctx, timer, timing, logger)
    finally:
        # Drain in-flight faithfulness tasks BEFORE the surrounding
        # ``pipeline_trace`` exits and flushes — otherwise the judge's
        # ``log_score`` lands in the SDK buffer after explicit flush and
        # may be lost on container stop.
        await _drain_faithfulness(spawned_faithfulness)
