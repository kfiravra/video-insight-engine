"""Pipeline orchestration for the summarizer service.

Drives the phase-by-phase pipeline, opens the Langfuse parent trace, and
spawns the fire-and-forget faithfulness judge. The broker fan-out (which
republishes the SSE stream through Redis so concurrent SSE consumers
dedupe to a single pipeline run) lives in :mod:`src.routes.pipeline_broker` —
keeping that out of here lets this file stay focused on the pipeline itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncGenerator

import redis.exceptions as redis_exceptions
import structlog
from litellm.exceptions import (
    APIError as LitellmAPIError,
)
from litellm.exceptions import (
    RateLimitError,
)
from litellm.exceptions import (
    Timeout as LitellmTimeout,
)
from llm_common.context import (  # noqa: F401 — llm_feature_var used in phases
    llm_feature_var,
    llm_request_id_var,
    llm_user_id_var,
    llm_video_id_var,
    llm_video_summary_id_var,
)
from llm_common.sentry_init import capture_exception_with_context

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode, ProcessingStatus
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.cached_response import stream_cached_structured as _stream_cached_structured
from src.services.cache.response_cache import response_cache
from src.services.llm import LLMService
from src.services.observability import pipeline_trace, update_trace_metadata
from src.services.override_state import clear_override
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
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.status_callback import send_video_status
from src.services.transcription.transcript_meta import build_transcript_meta

logger = logging.getLogger(__name__)


# Re-imported so tests can keep calling/patching these via this module; the
# implementations (and the strong-ref task registry) live in
# ``pipeline_faithfulness.py``.
from src.routes.pipeline_faithfulness import (  # noqa: E402
    _drain_faithfulness,
    _launch_faithfulness_check,
)

# ─────────────────────────────────────────────────────────────────────────────
# Failure reporting
# ─────────────────────────────────────────────────────────────────────────────


def _report_unexpected_failure(exc: Exception, video_summary_id: str, elapsed: float) -> str:
    """Log + Sentry-capture an unclassified pipeline exception; return its ref.

    The classified branches (transcript, rate-limit, timeout, provider error)
    are operational and stay log-only. This branch is where real bugs land —
    and until now it was the one place they were swallowed into an SSE
    ``error`` frame with no exception reaching Sentry. The SSE-direct path
    (dev override / SSE client winning the producer lock) has no other
    capture point; under the worker the DLQ capture in ``runner.py`` only
    sees a synthetic RuntimeError, so this is also where the real traceback
    comes from. ``attempt`` is bound by the worker only, so its presence
    tells the two paths apart in Sentry.
    """
    error_ref = str(uuid.uuid4())[:8]
    logger.error(
        "[pipeline] FAILED video_id=%s error=%s ref=%s total=%.1fs",
        video_summary_id,
        type(exc).__name__,
        error_ref,
        elapsed,
        exc_info=True,
    )
    bound = structlog.contextvars.get_contextvars()
    attempt = bound.get("attempt") if isinstance(bound, dict) else None
    capture_exception_with_context(
        exc,
        videoSummaryId=video_summary_id,
        errorRef=error_ref,
        outcome="pipeline_failed",
        path="worker" if attempt is not None else "sse-direct",
        attempt=str(attempt) if attempt is not None else None,
    )
    return error_ref


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


def _persist_cache_hit(
    repository: MongoDBVideoRepository, video_summary_id: str, cached: dict[str, Any]
) -> None:
    """Complete a pending row from a Redis-served payload (thread target).

    Such a row never ran the transcript phase in this run, so any
    ``transcriptMeta`` already on it belongs to a previous — possibly failed —
    run and is cleared first. Redis-completed rows are identified downstream
    by "no transcriptMeta AND no pipelineVersion"; a stale block would break
    that discriminator.
    """
    repository.clear_transcript_meta(video_summary_id)
    repository.save_structured_result(video_summary_id, cached)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline phase orchestration
# ─────────────────────────────────────────────────────────────────────────────


async def _run_pipeline_phases(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timer: PipelineTimer,
) -> AsyncGenerator[str, None]:
    """Run every pipeline phase in order, streaming SSE chunks to the caller.

    Lives inside the Langfuse ``pipeline_trace`` context so every LLM span
    attaches to the parent trace. Phase timings are recorded on ``ctx`` for
    the final summary log. Any faithfulness tasks spawned mid-pipeline are
    drained before this coroutine returns so their scores reach Langfuse
    before the trace is flushed.
    """
    youtube_id = ctx.youtube_id
    spawned_faithfulness: list[asyncio.Task[None]] = []

    try:
        # Phase 1: Metadata (sequential — sets video_data needed by everything)
        phase_start = time.monotonic()
        async for event in run_phase_metadata(ctx):
            yield event
        ctx.phase_times["metadata"] = round(time.monotonic() - phase_start, 1)

        # Phase 2: Transcript + Frames (parallel — both only need youtube_id + video_data)
        # ``finally`` so transcriptMeta is recorded whether the phases succeed
        # or raise (cancellation included): a TranscriptError from the
        # fallback chain must still leave outcome="failed" + attempted +
        # errorCode on the row (the phase's own finally already stamped
        # ctx.transcript_trail by the time it propagates here).
        phase_start = time.monotonic()
        try:
            async for event in run_parallel_phases([run_phase_transcript, run_phase_frames], ctx):
                yield event
        finally:
            ctx.phase_times["transcript_frames"] = round(time.monotonic() - phase_start, 1)
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
        ctx.phase_times["visual_inject"] = round(time.monotonic() - phase_start, 1)

        # Phase 3-4: Plan -> Extraction (sequential — each depends on the previous)
        for phase in [run_phase_plan, run_phase_extraction]:
            phase_start = time.monotonic()
            async for event in phase(ctx):
                yield event
            ctx.phase_times[phase.__name__.replace("run_phase_", "")] = round(
                time.monotonic() - phase_start, 1
            )
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
        ctx.phase_times["synthesis_enrichment"] = round(time.monotonic() - phase_start, 1)

        # Phase 6: Assembly (needs synthesis + enrichment results)
        phase_start = time.monotonic()
        async for event in run_phase_assembly(ctx):
            yield event
        ctx.phase_times["assembly"] = round(time.monotonic() - phase_start, 1)

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
            ctx.phase_times["translation"] = round(time.monotonic() - phase_start, 1)

        # One-line pipeline summary with ALL phase timings
        pt = ctx.phase_times
        plan_ok = "ok" if ctx.plan_result is not None else "FAIL"
        enrich_ok = "ok" if ctx.enrichment_data else "FAIL"
        logger.info(
            "[pipeline] DONE youtube_id=%s in %.0fs | "
            "metadata=%.1fs transcript_frames=%.1fs visual_inject=%.1fs "
            "plan=%.1fs(%s) extraction=%.1fs synthesis_enrichment=%.1fs(%s) "
            "assembly=%.1fs | tabs=%d",
            youtube_id,
            timer.elapsed(),
            pt.get("metadata", 0),
            pt.get("transcript_frames", 0),
            pt.get("visual_inject", 0),
            pt.get("plan", 0),
            plan_ok,
            pt.get("extraction", 0),
            pt.get("synthesis_enrichment", 0),
            enrich_ok,
            pt.get("assembly", 0),
            len(ctx.triage.tabs) if ctx.triage else 0,
        )
    finally:
        # Drain in-flight faithfulness tasks BEFORE the surrounding
        # ``pipeline_trace`` exits and flushes — otherwise the judge's
        # ``log_score`` lands in the SDK buffer after explicit flush and
        # may be lost on container stop.
        await _drain_faithfulness(spawned_faithfulness)


# ─────────────────────────────────────────────────────────────────────────────
# Main Streaming Generator (Triage-Driven Pipeline)
# ─────────────────────────────────────────────────────────────────────────────


async def stream_summarization(
    video_summary_id: str,
    entry: dict[str, Any],
    repository: MongoDBVideoRepository,
    llm_service: LLMService,
    force_refresh: bool = False,
) -> AsyncGenerator[str, None]:
    """Triage-driven pipeline: Triage -> Extract -> Enrich -> Synthesize (4-7 LLM calls).

    ``force_refresh=True`` skips the response_cache fast path so a fresh run
    always executes — used by the dev-tools override flow that wants to
    test alternate provider configs against a video that was previously
    cached under the default provider, and by bypassCache version bumps
    (via the worker payload OR the ``forceRefresh`` flag the API stamps on
    the fresh cache row — the row flag covers the case where an SSE client
    wins the producer lock before the worker).
    """
    timer = PipelineTimer()
    force_refresh = force_refresh or bool(entry.get("forceRefresh"))

    try:
        youtube_id = entry.get("youtubeId") or entry.get("youtube_id")
        if not youtube_id:
            yield sse_event("error", {"message": "YouTube ID not found"})
            return

        # Open the Langfuse parent trace BEFORE the cache lookup so cache
        # hits are observable too. Without this, the Redis-hit fast path
        # produced zero spans and dashboards under-counted the actual
        # request volume. ``cacheHit`` is set via ``update_trace_metadata``
        # once the cache lookup resolves.
        # ``requestId`` is pulled from structlog contextvars (the worker binds
        # it from the queue payload) so Sentry events, log lines, and Langfuse
        # traces share the same correlation id. Only added when present —
        # the SSE-direct path (e.g. dev override) doesn't bind one and we don't
        # want ``null`` polluting Langfuse dashboards.
        # ``request_id`` and ``user_id`` come from structlog contextvars, which
        # the worker binds from the queue payload (see worker/runner.py). The
        # ``entry`` row is the cross-user ``videoSummaryCache`` doc — it is
        # content-addressed and shared across users, so it carries no per-run
        # owner; ``entry.get("userId")`` is a best-effort fallback for any
        # SSE-direct path that sets it on the doc.
        ctxvars = structlog.contextvars.get_contextvars()
        request_id = ctxvars.get("request_id")
        user_id = entry.get("userId") or ctxvars.get("user_id")

        # Set LLM cost-tracking context vars BEFORE the cache lookup so every
        # ``llm_usage`` row this run writes — including the rare cache-hit-path
        # LLM call — is attributable to its user, video, run, and request. These
        # are the keys per-user reconciliation and run grouping match on.
        llm_video_id_var.set(youtube_id)
        llm_video_summary_id_var.set(video_summary_id)
        if user_id:
            llm_user_id_var.set(user_id)
        if request_id:
            llm_request_id_var.set(request_id)

        trace_tags = [f"youtubeId:{youtube_id}", f"videoSummaryId:{video_summary_id}"]
        trace_metadata: dict[str, Any] = {
            "youtubeId": youtube_id,
            "videoSummaryId": video_summary_id,
            "userId": user_id,
            "language": entry.get("language"),
        }
        if request_id:
            trace_tags.append(f"requestId:{request_id}")
            trace_metadata["requestId"] = request_id
        async with pipeline_trace(
            video_summary_id,
            tags=trace_tags,
            metadata=trace_metadata,
            user_id=user_id,
        ):
            # Check Redis cache first (same YouTube video = instant serve)
            if settings.REDIS_ENABLED and not force_refresh:
                try:
                    cached = await response_cache.get_response(youtube_id)
                    cached_meta = cached.get("meta", {}) if isinstance(cached, dict) else {}
                    if (
                        cached
                        and isinstance(cached, dict)
                        and cached.get("status") == ProcessingStatus.COMPLETED.value
                        and cached.get("youtubeId") == youtube_id  # Verify cache integrity
                        and isinstance(cached.get("tabs"), list)  # Required field present
                        and len(cached["tabs"]) > 0  # Reject empty tab lists
                        and isinstance(cached["tabs"][0], dict)  # Spot-check first tab
                        and isinstance(cached_meta, dict)
                        and len(cached_meta) > 0  # Reject empty meta
                    ):
                        logger.info("[pipeline] Redis cache HIT for youtube_id=%s", youtube_id)
                        update_trace_metadata({"cacheHit": True, "cacheSource": "redis"})
                        # Fire-and-forget DB save — don't block the cache-hit fast path
                        if entry.get("status") != ProcessingStatus.COMPLETED.value:
                            _vid_id = video_summary_id  # capture for lambda
                            task = asyncio.create_task(
                                asyncio.to_thread(
                                    _persist_cache_hit, repository, video_summary_id, cached
                                )
                            )
                            task.add_done_callback(
                                lambda t, vid=_vid_id: (
                                    logger.error(
                                        "Cache-hit DB save failed for %s: %s",
                                        vid,
                                        t.exception(),
                                    )
                                    if t.exception()
                                    else None
                                )
                            )
                        async for event in _stream_cached_structured(video_summary_id, cached):
                            yield event
                        return
                except (OSError, redis_exceptions.ConnectionError) as e:
                    logger.debug("Redis cache check failed (non-critical): %s", e)

            update_trace_metadata({"cacheHit": False})

            await asyncio.to_thread(
                repository.update_status, video_summary_id, ProcessingStatus.PROCESSING
            )
            # Re-runs (regenerate, failed retry, stall re-dispatch) reuse the
            # same _id, so a transcriptMeta block left by an earlier failed run
            # must not survive into this one; the transcript phase re-records it.
            # Best-effort: an observability-only field must never abort a run.
            try:
                await asyncio.to_thread(repository.clear_transcript_meta, video_summary_id)
            except Exception as exc:
                logger.warning(
                    "[pipeline] transcriptMeta clear failed for %s: %s", video_summary_id, exc
                )
            # Mirror the cache-doc status onto userVideos via the API's
            # /internal/status endpoint (best-effort; also fans out over WS).
            await send_video_status(video_summary_id, None, "processing")
            logger.info("[pipeline] START video_id=%s youtube_id=%s", video_summary_id, youtube_id)

            ctx = PipelineContext(
                video_summary_id=video_summary_id,
                youtube_id=youtube_id,
                entry=entry,
                repository=repository,
                llm_service=llm_service,
                timer=timer,
            )

            async for event in _run_pipeline_phases(ctx, repository, video_summary_id, timer):
                yield event

    except TranscriptError as e:
        logger.info(
            "[pipeline] FAILED video_id=%s error=TranscriptError total=%.1fs",
            video_summary_id,
            timer.elapsed(),
        )
        await asyncio.to_thread(
            repository.update_status, video_summary_id, ProcessingStatus.FAILED, str(e), e.code
        )
        await send_video_status(video_summary_id, None, "failed", error=str(e))
        yield sse_event("error", {"message": str(e), "code": e.code.value})

    except RateLimitError as e:
        logger.warning(
            "[pipeline] FAILED video_id=%s error=RateLimitError total=%.1fs",
            video_summary_id,
            timer.elapsed(),
        )
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.FAILED,
            str(e),
            ErrorCode.RATE_LIMITED,
        )
        # User-facing channels (WS status + SSE) get the same sanitized text;
        # raw LiteLLM strings leak provider/model/endpoint internals.
        safe_msg = "AI service rate limited. Please try again in a moment."
        await send_video_status(video_summary_id, None, "failed", error=safe_msg)
        yield sse_event(
            "error",
            {"message": safe_msg, "code": ErrorCode.RATE_LIMITED.value},
        )

    except LitellmTimeout as e:
        logger.warning(
            "[pipeline] FAILED video_id=%s error=Timeout total=%.1fs",
            video_summary_id,
            timer.elapsed(),
        )
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.FAILED,
            str(e),
            ErrorCode.LLM_ERROR,
        )
        safe_msg = "Request took too long. Please try again."
        await send_video_status(video_summary_id, None, "failed", error=safe_msg)
        yield sse_event(
            "error",
            {"message": safe_msg, "code": ErrorCode.LLM_ERROR.value},
        )

    except LitellmAPIError as e:
        logger.error(
            "[pipeline] FAILED video_id=%s error=APIError total=%.1fs",
            video_summary_id,
            timer.elapsed(),
        )
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.FAILED,
            str(e),
            ErrorCode.LLM_ERROR,
        )
        safe_msg = "AI service error. Please try again."
        await send_video_status(video_summary_id, None, "failed", error=safe_msg)
        yield sse_event(
            "error",
            {"message": safe_msg, "code": ErrorCode.LLM_ERROR.value},
        )

    except Exception as e:
        error_ref = _report_unexpected_failure(e, video_summary_id, timer.elapsed())
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.FAILED,
            str(e),
            ErrorCode.UNKNOWN_ERROR,
        )
        safe_msg = f"An unexpected error occurred (ref: {error_ref})."
        await send_video_status(video_summary_id, None, "failed", error=safe_msg)
        yield sse_event(
            "error",
            {
                "message": safe_msg,
                "code": ErrorCode.UNKNOWN_ERROR.value,
            },
        )
    finally:
        await asyncio.to_thread(clear_override, video_summary_id)
