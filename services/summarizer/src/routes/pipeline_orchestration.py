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
from collections.abc import AsyncGenerator, Callable
from typing import Literal

from src.config import settings
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.pipeline_faithfulness import (
    _drain_faithfulness,
    _launch_faithfulness_check,
    _launch_hero_quiz_check,
)
from src.routes.run_timing import log_run_summary, mark_phase, persist_run_timing
from src.services.observability import update_trace_metadata
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.phases import (
    run_phase_assembly,
    run_phase_extraction,
    run_phase_frames,
    run_phase_metadata,
    run_phase_text,
)
from src.services.pipeline.phases.frames_done import frames_done_step
from src.services.pipeline.phases.qdrant_index import wait_for_rag_transcript
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
    once the phases finish or fail. A run that finished — ``done`` already
    out — gives a non-English RAG transcript task and a deferred hi-res pass a
    bounded wait (side by side), so their calls and steps are in the document;
    a failing run never waits (its error event follows this).
    """
    timing = start_run_timing()
    finished = False
    try:
        async for event in _run_phases_in_order(ctx, repository, video_summary_id, timer, timing):
            timing.observe_sse(event)
            yield event
        finished = True
    finally:
        try:
            await _end_run_work(ctx, finished)
        finally:
            await persist_run_timing(ctx, repository, video_summary_id, timing)


async def _end_run_work(ctx: PipelineContext, finished: bool) -> None:
    """Release the run's media; a finished run also gives its RAG transcript task a wait.

    The two bounded waits (the deferred hi-res pass, the RAG transcript) run side
    by side so ``done`` is never followed by both back to back.
    """
    if not finished:
        await _release_run_media(ctx, finished)
        return
    results = await asyncio.gather(
        _release_run_media(ctx, finished), wait_for_rag_transcript(ctx), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _release_run_media(ctx: PipelineContext, finished: bool) -> None:
    """Stop and delete whatever the t=0 group left running.

    Failed or cancelled runs never reach the phases that close the run's
    downloads; nothing may outlive the run (a cancelled download still lands
    in the timing, so this runs before it is persisted). A deferred hi-res
    pass reads the 720p file: a finished run lets it end (bounded), a failed
    one cancels it, before the file is closed.
    """
    from src.services.pipeline.phases.frames import finish_hires_upgrade
    from src.services.pipeline.phases.metadata import release_background_work
    from src.services.pipeline.phases.probe import cancel_tier_probe

    release_background_work(ctx)
    cancel_tier_probe(ctx)
    lowres_video = getattr(ctx, "lowres_video", None)
    hires_video = getattr(ctx, "hires_video", None)
    try:
        await finish_hires_upgrade(ctx, wait=finished)
    finally:
        try:
            if lowres_video is not None:
                await lowres_video.close()
        finally:
            if hires_video is not None:
                await hires_video.close()


PhaseFn = Callable[[PipelineContext], AsyncGenerator[str, None]]


def _phase_two_members(split: bool) -> list[PhaseFn]:
    """Text ∥ frames; the split path adds the frames-done hook and the field groups.

    Split (``EXTRACTION_PARALLEL``, 3.4): the frames phase ends with the
    frames-done step (annotations + deferred code tabs) inside the group, and
    the extraction member fires its text groups at plan-done and its visual
    group at frames-done — extraction no longer waits for the whole group.
    """
    from src.services.pipeline.phases.moments import then_provisional_moments

    text = then_provisional_moments(run_phase_text)
    if not split:
        return [text, run_phase_frames]
    from src.services.pipeline.phases.frames_done import then_frames_done
    from src.services.pipeline.phases.split_extraction import run_phase_split_extraction

    return [text, then_frames_done(run_phase_frames), run_phase_split_extraction]


async def _run_phase_two(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timing: PipelineTimingRecorder,
    split: bool = False,
) -> AsyncGenerator[str, None]:
    """Text branch ∥ frames (∥ the field groups when ``split``), the tier probe alongside.

    The text branch runs transcript → probe → plan ∥ memory, so the readers
    work while frames + vision still run; frames reads the probe at Step 6b.
    At plan-done (plan and memory both in) it streams the provisional Moments
    tab (2.1), long before extraction can start.
    The description analysis (started at metadata-end) is not a member: no
    reader in this group needs it, and extraction must not wait for its LLM
    call — its readers (chunked chapters, assembly) await it with a cap.
    ``finally`` so transcriptMeta is recorded whether the phases succeed or
    raise (cancellation included): a TranscriptError from the fallback chain
    must still leave outcome="failed" + attempted + errorCode on the row (the
    phase's own finally already stamped ctx.transcript_trail by the time it
    propagates here).
    """
    from src.services.pipeline.phases.probe import start_tier_probe

    phase_start = time.monotonic()
    start_tier_probe(ctx)
    try:
        async for event in run_parallel_phases(_phase_two_members(split), ctx):
            yield event
    finally:
        # On the split path this wall spans the field groups too (SERVICE-SUMMARIZER.md, 4b).
        mark_phase(ctx, timing, "transcript_frames", phase_start)
        await _record_transcript_outcome(ctx, repository, video_summary_id)


async def _frames_done_then_extraction(
    ctx: PipelineContext, timing: PipelineTimingRecorder
) -> AsyncGenerator[str, None]:
    """Today's path after the whole phase-2 group: the frames-done step, then ONE extraction.

    Frames-done: the ``<visual_annotations>`` block, then the code tabs
    reconcile deferred at plan-done decided on what the frames show (3.2; a
    fresh meta when that changed the tabs). Timing name kept so
    pipeline.timing stays comparable with the baseline and cassettes.
    Extraction goes through the parallel runner for its heartbeats: one
    extraction call can be silent for minutes, past the API gateway's idle
    timeout. The runner also records the "extraction" step in pipeline.timing.
    """
    phase_start = time.monotonic()
    for event in frames_done_step(ctx):
        yield event
    mark_phase(ctx, timing, "visual_inject", phase_start)
    async for event in run_parallel_phases([run_phase_extraction], ctx):
        yield event


JudgeStage = Literal["extraction", "assembly"]


def _launch_judges(
    ctx: PipelineContext, spawned: list[asyncio.Task[None]], stage: JudgeStage
) -> None:
    """Start the fire-and-forget faithfulness judge due at ``stage``; track it in ``spawned``.

    ``extraction``: once extraction has data, the extraction sample. ``assembly``: the
    judge's hero + quiz sample (output-quality 2.4), which needs the late group's hero
    and quiz and judges each only if its call wrote it. The task copies the current
    ContextVar state so the Langfuse trace is still attached; the run drains the tasks
    before exiting the trace.
    """
    if stage == "extraction":
        task = _launch_faithfulness_check(ctx) if ctx.extraction_data else None
    else:
        task = _launch_hero_quiz_check(ctx)
    if task is not None:
        spawned.append(task)


async def _run_translation_step(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timer: PipelineTimer,
    timing: PipelineTimingRecorder,
) -> AsyncGenerator[str, None]:
    """Translate the English output into the source language and attach it as
    ``sourceLanguage`` for the FE toggle (no-op for English).

    A row purged mid-run gets no translation (paid LLM calls) and no `done`.
    """
    if not ctx.source_language_code or ctx.row_deleted:
        return
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
                "degraded": coverage_is_degraded(getattr(ctx, "extraction_coverage", None)),
            },
        )
        yield "data: [DONE]\n\n"
    except Exception as e:
        # Translation raised — leave the doc "processing" (retriable) and
        # emit no terminal event; the FE handles the stream close.
        logger.warning("[pipeline] Translation failed (non-critical): %s", e)
    mark_phase(ctx, timing, "translation", phase_start)


async def _run_early_phases(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timing: PipelineTimingRecorder,
) -> AsyncGenerator[str, None]:
    """Phase 1 (metadata) and phase 2 (text ∥ frames, then extraction on the unsplit path)."""
    # Phase 1: Metadata — one extract_info + validate_duration; it then
    # starts the t=0 group in the background (captions, description
    # analysis, low-res + 720p downloads) and returns.
    phase_start = time.monotonic()
    async for event in run_phase_metadata(ctx):
        yield event
    mark_phase(ctx, timing, "metadata", phase_start)

    # Phase 2: text branch (transcript → tier probe → plan ∥ memory) ∥
    # frames. EXTRACTION_PARALLEL (3.4): the field groups run inside the
    # group (text at plan-done, visual at frames-done); false: today's
    # frames-done step, then ONE extraction after the whole group.
    split = settings.EXTRACTION_PARALLEL
    async for event in _run_phase_two(ctx, repository, video_summary_id, timing, split):
        yield event
    if not split:
        async for event in _frames_done_then_extraction(ctx, timing):
            yield event


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
        async for event in _run_early_phases(ctx, repository, video_summary_id, timing):
            yield event
        _launch_judges(ctx, spawned_faithfulness, "extraction")

        # Phase 6: Assembly — tabs first, then synthesis ∥ moment fill ∥ the
        # quiz (1d.1/1d.3: the quiz never blocks tabs; its tab joins last),
        # then the save.
        phase_start = time.monotonic()
        async for event in run_phase_assembly(ctx):
            yield event
        mark_phase(ctx, timing, "assembly", phase_start)
        _launch_judges(ctx, spawned_faithfulness, "assembly")

        async for event in _run_translation_step(ctx, repository, video_summary_id, timer, timing):
            yield event

        # The orchestrator's logger: the replay driver + log searches key on it.
        log_run_summary(ctx, timer, timing, logger)
    finally:
        # Drain in-flight faithfulness tasks BEFORE the surrounding
        # ``pipeline_trace`` exits and flushes — otherwise the judge's
        # ``log_score`` lands in the SDK buffer after explicit flush and
        # may be lost on container stop.
        await _drain_faithfulness(spawned_faithfulness)
