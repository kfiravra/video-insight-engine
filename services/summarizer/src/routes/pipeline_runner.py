"""Pipeline entry for the summarizer service.

Opens the Langfuse parent trace, serves the Redis response-cache fast path,
builds the run's context and hands it to the phase orchestration
(:mod:`src.routes.pipeline_orchestration`, which also spawns the
fire-and-forget faithfulness judge). The broker fan-out (which republishes
the SSE stream through Redis so concurrent SSE consumers dedupe to a single
pipeline run) lives in :mod:`src.routes.pipeline_broker`. Run timing (phase
stamps, ``pipeline.timing`` persistence) lives in
:mod:`src.routes.run_timing`; failure classification in
:mod:`src.routes.pipeline_failures`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

import redis.exceptions as redis_exceptions
import structlog
from llm_common.context import (  # noqa: F401 — llm_feature_var used in phases
    llm_feature_var,
    llm_request_id_var,
    llm_user_id_var,
    llm_video_id_var,
    llm_video_summary_id_var,
)

from src.config import settings
from src.models.schemas import ProcessingStatus
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.cached_response import stream_cached_structured as _stream_cached_structured
from src.routes.pipeline_failures import classify_run_failure
from src.routes.pipeline_orchestration import run_pipeline_phases
from src.services.cache.response_cache import response_cache
from src.services.llm import LLMService
from src.services.media.hires_prefetch import LocalHiresSource, LocalLowresSource
from src.services.observability import pipeline_trace, update_trace_metadata
from src.services.override_state import clear_override
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.pipeline_helpers import PipelineTimer, sse_event
from src.services.status_callback import send_video_status

logger = logging.getLogger(__name__)


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
    repository.clear_degraded(video_summary_id)
    repository.save_structured_result(video_summary_id, cached)


def _resolve_run_attribution(
    entry: dict[str, Any], ctxvars: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Pick the ``(user_id, request_id)`` this run's ``llm_usage`` rows carry.

    Contextvars win, the cache row is the fallback. The queue worker binds the
    true owner and request of THIS run in contextvars (worker/runner.py); the
    ``entry`` row is the shared ``videoSummaryCache`` doc, whose ``userId`` /
    ``requestId`` are whatever the last ``POST /summarize`` stamped and may
    belong to an earlier HTTP dispatch. The SSE producer binds no user, so
    there the row is the only owner.

    The request id has one exception: the request-context middleware always
    binds one on an HTTP request (``path`` is set only by it), and the browser's
    SSE GET carries no ``X-Request-ID`` — so that is a fresh UUID that would
    shadow the dispatching request's id the row holds. On an HTTP-bound
    context the row's id therefore comes first.
    """
    user_id = ctxvars.get("user_id") or entry.get("userId")
    ctx_request_id = ctxvars.get("request_id")
    row_request_id = entry.get("requestId")
    if "path" in ctxvars:
        return user_id, row_request_id or ctx_request_id
    return user_id, ctx_request_id or row_request_id


# ─────────────────────────────────────────────────────────────────────────────
# Main Streaming Generator
# ─────────────────────────────────────────────────────────────────────────────


async def stream_summarization(
    video_summary_id: str,
    entry: dict[str, Any],
    repository: MongoDBVideoRepository,
    llm_service: LLMService,
    force_refresh: bool = False,
) -> AsyncGenerator[str, None]:
    """One pipeline run: metadata -> text branch (transcript -> tier probe -> plan ∥
    memory) ∥ frames [∥ field groups] -> extraction -> quiz ∥ synthesis -> assembly ->
    translation (non-English sources); the phases run in
    :mod:`src.routes.pipeline_orchestration`. The extraction call count varies per video:
    field groups × transcript chunks, or one call per batch with
    ``EXTRACTION_PARALLEL=false``.

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
        # ``requestId`` comes from the worker's contextvars on the queue path and
        # from the dispatching ``POST /summarize`` (stamped on the row) on the
        # SSE path, so Sentry events, log lines, and Langfuse traces share the
        # api's correlation id. Only added when present — we don't want
        # ``null`` polluting Langfuse dashboards.
        user_id, request_id = _resolve_run_attribution(
            entry, structlog.contextvars.get_contextvars()
        )

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
        # D25: the API stamps eval-user rows; their results stay private.
        eval_run = bool(entry.get("evalRun"))
        if eval_run:
            trace_metadata["evalRun"] = True
        # 1a.7: the API stamps admin/eval benchmark rows that must measure cold media.
        cold_media = bool(entry.get("coldMedia"))
        if cold_media:
            trace_metadata["coldMedia"] = True
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
                await asyncio.to_thread(repository.clear_degraded, video_summary_id)
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
                lowres_video=LocalLowresSource(youtube_id),
                hires_video=LocalHiresSource(youtube_id),
                eval_run=eval_run,
                cold_media=cold_media,
            )

            async for event in run_pipeline_phases(ctx, repository, video_summary_id, timer):
                yield event

    except Exception as e:
        failure = classify_run_failure(e, video_summary_id, timer.elapsed())
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.FAILED,
            str(e),
            failure.code,
        )
        # WS status + SSE get the same user-safe text (see pipeline_failures).
        await send_video_status(video_summary_id, None, "failed", error=failure.user_message)
        yield sse_event("error", {"message": failure.user_message, "code": failure.code.value})
    finally:
        await asyncio.to_thread(clear_override, video_summary_id)
