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
    repository.save_structured_result(video_summary_id, cached)


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
                lowres_video=LocalLowresSource(youtube_id),
                hires_video=LocalHiresSource(youtube_id),
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
