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
from collections.abc import Callable
from typing import AsyncGenerator

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.pipeline_faithfulness import _drain_faithfulness, _launch_faithfulness_check
from src.routes.run_timing import log_run_summary, mark_phase, persist_run_timing
from src.services.observability import update_trace_metadata
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.enrichment import needs_quiz
from src.services.pipeline.phases import (
    run_phase_assembly,
    run_phase_enrichment,
    run_phase_extraction,
    run_phase_frames,
    run_phase_metadata,
    run_phase_synthesis,
    run_phase_text,
)
from src.services.pipeline.pipeline_helpers import (
    PipelineTimer,
    run_parallel_phases,
    sse_event,
)
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.pipeline.visual_annotations import annotation_entries, render_visual_annotations
from src.services.transcription.transcript_meta import build_transcript_meta

logger = logging.getLogger(__name__)

PhaseFn = Callable[[PipelineContext], AsyncGenerator[str, None]]


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
    from src.services.pipeline.phases.probe import cancel_tier_probe

    release_background_work(ctx)
    cancel_tier_probe(ctx)
    lowres_video = getattr(ctx, "lowres_video", None)
    hires_video = getattr(ctx, "hires_video", None)
    try:
        if lowres_video is not None:
            await lowres_video.close()
    finally:
        if hires_video is not None:
            await hires_video.close()


def _render_visual_annotations(ctx: PipelineContext) -> None:
    """Frame captions + OCR → ``ctx.visual_annotations`` once frames are done (1c.2).

    Never written into ``clean_text``: the probe, plan and memory read speech
    only, and so do the Qdrant transcript points and the faithfulness judge's
    transcript part. Phase 1's single extraction call waits for the whole
    phase-2 group, so rendering after it is frames-done for every reader.
    """
    ctx.visual_annotations = render_visual_annotations(ctx.frame_descriptions, ctx.scene_frames_all)
    entries = len(annotation_entries(ctx.visual_annotations))
    if entries:
        logger.info("[pipeline] Visual annotations: %d entries", entries)


async def _run_phase_two(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timing: PipelineTimingRecorder,
) -> AsyncGenerator[str, None]:
    """Text branch ∥ frames ∥ description, with the tier probe started alongside.

    The text branch runs transcript → probe → plan ∥ memory, so the readers
    work while frames + vision still run; frames reads the probe at Step 6b.
    ``finally`` so transcriptMeta is recorded whether the phases succeed or
    raise (cancellation included): a TranscriptError from the fallback chain
    must still leave outcome="failed" + attempted + errorCode on the row (the
    phase's own finally already stamped ctx.transcript_trail by the time it
    propagates here).
    """
    from src.services.pipeline.phases.metadata import run_phase_description
    from src.services.pipeline.phases.probe import start_tier_probe

    phase_start = time.monotonic()
    start_tier_probe(ctx)
    group = [run_phase_text, run_phase_frames, run_phase_description]
    try:
        async for event in run_parallel_phases(group, ctx):
            yield event
    finally:
        mark_phase(ctx, timing, "transcript_frames", phase_start)
        await _record_transcript_outcome(ctx, repository, video_summary_id)


def _tail_phases(ctx: PipelineContext) -> list[PhaseFn]:
    """Synthesis, plus the quiz when the plan demands one (1d.1)."""
    if needs_quiz(ctx.plan_result, ctx.content_format):
        return [run_phase_synthesis, run_phase_enrichment]
    return [run_phase_synthesis]


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

        # Phase 2: text branch (transcript → tier probe → plan ∥ memory) ∥
        # frames ∥ description; extraction waits for all of it.
        async for event in _run_phase_two(ctx, repository, video_summary_id, timing):
            yield event

        # Phase 2.5: the <visual_annotations> block (timing name kept so
        # pipeline.timing stays comparable with the baseline and cassettes).
        phase_start = time.monotonic()
        _render_visual_annotations(ctx)
        mark_phase(ctx, timing, "visual_inject", phase_start)

        # Phase 4: Extraction (the plan ran inside phase 2). Through the
        # parallel runner for its heartbeats: one extraction call can be
        # silent for minutes, past the API gateway's idle timeout. The runner
        # also records the "extraction" step in pipeline.timing.
        async for event in run_parallel_phases([run_phase_extraction], ctx):
            yield event
        # Fire-and-forget faithfulness judge once extraction has data. The
        # task copies the current ContextVar state so the Langfuse trace is
        # still attached. We track the task so we can drain it before
        # exiting the trace.
        if ctx.extraction_data:
            spawned = _launch_faithfulness_check(ctx)
            if spawned is not None:
                spawned_faithfulness.append(spawned)

        # Phase 5: Synthesis ∥ quiz. The quiz reads the extraction and the
        # video_memory (never the synthesis), and only a plan that can show a
        # quiz asks for one (the phase re-checks the same gate).
        phase_start = time.monotonic()
        async for event in run_parallel_phases(_tail_phases(ctx), ctx):
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
