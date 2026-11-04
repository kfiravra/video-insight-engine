"""Pipeline entry for the summarizer service.

Serves the Redis response-cache fast path, opens the Langfuse run trace,
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
from llm_common.context import bind_usage_ids

from src.config import settings
from src.models.schemas import ProcessingStatus
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.cached_response import stream_cached_structured as _stream_cached_structured
from src.routes.pipeline_failures import classify_run_failure
from src.routes.pipeline_orchestration import run_pipeline_phases
from src.routes.run_events import log_run_finished, log_run_started
from src.services.cache.response_cache import response_cache
from src.services.llm import LLMService
from src.services.media.hires_prefetch import LocalHiresSource, LocalLowresSource
from src.services.pipeline.run_registry import (
    RunDispatch,
    RunIdentity,
    entries_of,
    fresh_run,
    resolve_run,
    run_entry,
    run_tags,
)
from src.services.observability import run_trace
from src.services.override_state import clear_override
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.pipeline_helpers import PipelineTimer, sse_event
from src.services.run_context import bind_run_id
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


async def _complete_from_cache(
    repository: MongoDBVideoRepository, video_summary_id: str, cached: dict[str, Any]
) -> None:
    """Save a Redis-served payload on the row, then report it completed.

    The status carries ``cache_hit`` so vie-api gives the run's token hold
    back as a cached serve instead of settling a run that never ran (I3).
    Sent only after the save, so the API's served-version promote sees the
    completed row.
    """
    await asyncio.to_thread(_persist_cache_hit, repository, video_summary_id, cached)
    await send_video_status(
        video_summary_id, None, ProcessingStatus.COMPLETED.value, cache_hit=True
    )


def _valid_cached_payload(cached: Any, youtube_id: str) -> bool:
    """A Redis payload is served only when it is a completed, intact response."""
    if not isinstance(cached, dict):
        return False
    cached_meta = cached.get("meta", {})
    tabs = cached.get("tabs")
    return (
        cached.get("status") == ProcessingStatus.COMPLETED.value
        and cached.get("youtubeId") == youtube_id  # Verify cache integrity
        and isinstance(tabs, list)  # Required field present
        and len(tabs) > 0  # Reject empty tab lists
        and isinstance(tabs[0], dict)  # Spot-check first tab
        and isinstance(cached_meta, dict)
        and len(cached_meta) > 0  # Reject empty meta
    )


def _spawn_cache_completion(
    repository: MongoDBVideoRepository, video_summary_id: str, cached: dict[str, Any]
) -> None:
    """Fire-and-forget DB save + completed status, so the fast path never waits on it."""
    task = asyncio.create_task(_complete_from_cache(repository, video_summary_id, cached))
    task.add_done_callback(
        lambda t, vid=video_summary_id: (
            logger.error("Cache-hit DB save failed for %s: %s", vid, t.exception())
            if t.exception()
            else None
        )
    )


def _run_trace_args(
    entry: dict[str, Any],
    *,
    youtube_id: str,
    video_summary_id: str,
    user_id: str | None,
    request_id: str | None,
    run_path: str,
    run: RunIdentity,
    bypass: bool,
) -> dict[str, Any]:
    """The Langfuse trace of one attempt: id, name, session, tags and metadata (D4).

    The trace id is seeded from the run id (``trace_id_for_run``), so vie-api and
    the admin can link to it without asking Langfuse. One session per video row
    (``pipeline-<videoSummaryId>``) groups its attempts (first run, bypass,
    regenerate, retries). ``requestId`` is only added when present, so ``null``
    never pollutes dashboards; the user id goes through the
    ``LANGFUSE_USER_ID_MODE`` policy and is never copied into metadata.
    """
    pipeline_version = settings.PIPELINE_VERSION
    tags = [
        f"youtubeId:{youtube_id}",
        f"videoSummaryId:{video_summary_id}",
        f"path:{run_path}",
        *run_tags(run, pipeline_version=pipeline_version, bypass=bypass),
    ]
    metadata: dict[str, Any] = {
        "youtubeId": youtube_id,
        "videoSummaryId": video_summary_id,
        "runId": run.run_id,
        "attempt": run.attempt,
        "pipelineVersion": pipeline_version,
        "language": entry.get("language"),
        "path": run_path,
    }
    if request_id:
        tags.append(f"requestId:{request_id}")
        metadata["requestId"] = request_id
    # D25: the API stamps eval-user rows; their results stay private.
    if entry.get("evalRun"):
        metadata["evalRun"] = True
    # 1a.7: the API stamps admin/eval benchmark rows that must measure cold media.
    if entry.get("coldMedia"):
        metadata["coldMedia"] = True
    return {
        "trace_id": run.trace_id,
        "name": f"pipeline:{youtube_id}",
        "session_id": f"pipeline-{video_summary_id}",
        "user_id": user_id,
        "tags": tags,
        "metadata": metadata,
        "input": {"youtubeId": youtube_id, "videoSummaryId": video_summary_id, "runId": run.run_id},
    }


async def _register_run(
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    entry: dict[str, Any],
    run: RunIdentity,
    *,
    request_id: str | None,
    user_id: str | None,
    run_path: str,
    bypass: bool,
) -> RunIdentity:
    """Mark the attempt's ``runs[]`` entry running (P-2); the identity it ended up with.

    When the api's ``dispatched`` entry was taken by another attempt in between,
    this one gets a fresh id instead (one runId = one trace). Best-effort: a
    write failure is logged and the run goes on under ``run``.
    """

    def _doc(identity: RunIdentity) -> dict[str, Any]:
        return run_entry(
            identity,
            request_id=request_id,
            user_id=user_id,
            path=run_path,
            bypass=bypass,
            pipeline_version=settings.PIPELINE_VERSION,
        )

    try:
        taken = await asyncio.to_thread(
            repository.start_run,
            video_summary_id,
            _doc(run),
            take_over=run.dispatched_entry,
            superseded=run.superseded,
        )
        if not taken:
            run = fresh_run(entries_of(entry), run.attempt, run.chat_session_id)
            await asyncio.to_thread(
                repository.start_run, video_summary_id, _doc(run), take_over=False
            )
    except Exception as exc:  # noqa: BLE001 — observability bookkeeping only
        logger.warning("[pipeline] run registration failed for %s: %s", video_summary_id, exc)
    return run


async def _redis_payload(youtube_id: str) -> dict[str, Any] | None:
    """The completed Redis response for ``youtube_id``, or ``None`` (miss, invalid, Redis down)."""
    try:
        cached = await response_cache.get_response(youtube_id)
    except (OSError, redis_exceptions.ConnectionError) as e:
        logger.debug("Redis cache check failed (non-critical): %s", e)
        return None
    return cached if cached is not None and _valid_cached_payload(cached, youtube_id) else None


def _bind_usage_context(
    entry: dict[str, Any], youtube_id: str, video_summary_id: str
) -> tuple[str | None, str | None]:
    """Attribute this run's ``llm_usage`` rows (user, video, request); return ``(user, request)``.

    Bound BEFORE the cache lookup so every row this run writes, the rare
    cache-hit-path LLM call included, is attributable: these are the keys per-user
    reconciliation and run grouping match on. ``requestId`` comes from the worker's
    contextvars on the queue path and from the dispatching ``POST /summarize``
    (stamped on the row) on the SSE path, so Sentry events, log lines and Langfuse
    traces share the api's correlation id.
    """
    user_id, request_id = _resolve_run_attribution(entry, structlog.contextvars.get_contextvars())
    # Set even when None: a context reused across runs must not keep the last owner.
    bind_usage_ids(
        video_id=youtube_id,
        video_summary_id=video_summary_id,
        user_id=user_id,
        request_id=request_id,
    )
    return user_id, request_id


def _bind_run(run: RunIdentity | None, *, bypass: bool = False) -> None:
    """Bind the attempt's ids for its status callbacks, ledger rows and log lines (D9, D11).

    ``None`` clears the previous attempt's. Bound without reset tokens: the runner
    is an async generator whose ``aclose()`` may run in another context; the worker
    restores the job's ids itself (``usage_scope``).
    """
    bind_run_id(run.run_id if run else None)
    bind_usage_ids(
        run_id=run.run_id if run else None,
        trace_id=run.trace_id if run else None,
        attempt=run.attempt if run else None,
        session_id=run.chat_session_id if run else None,
        pipeline_version=settings.PIPELINE_VERSION if run else None,
        bypass=bypass if run else None,
    )
    structlog.contextvars.unbind_contextvars("run_id", "session_id")
    if run:
        structlog.contextvars.bind_contextvars(run_id=run.run_id)
        if run.chat_session_id:
            structlog.contextvars.bind_contextvars(session_id=run.chat_session_id)


async def _mark_processing(
    repository: MongoDBVideoRepository, video_summary_id: str, youtube_id: str
) -> None:
    """Flip the row to PROCESSING, clear the previous run's provenance, tell vie-api.

    Re-runs (regenerate, failed retry, stall re-dispatch) reuse the same _id, so a
    transcriptMeta block left by an earlier failed run must not survive into this
    one; the transcript phase re-records it. Best-effort: an observability-only
    field must never abort a run. The status mirrors onto userVideos through the
    API's /internal/status endpoint (best-effort; also fans out over WS).
    """
    await asyncio.to_thread(repository.update_status, video_summary_id, ProcessingStatus.PROCESSING)
    try:
        await asyncio.to_thread(repository.clear_transcript_meta, video_summary_id)
        await asyncio.to_thread(repository.clear_degraded, video_summary_id)
    except Exception as exc:
        logger.warning("[pipeline] transcriptMeta clear failed for %s: %s", video_summary_id, exc)
    await send_video_status(video_summary_id, None, "processing")
    logger.info("[pipeline] START video_id=%s youtube_id=%s", video_summary_id, youtube_id)


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
    run_path: str = "sse",
    dispatch: RunDispatch | None = None,
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

    ``run_path`` names the entry path on the trace: ``worker`` (queue),
    ``sse`` (the stream's own producer) or ``dev`` (dev-tools override).
    ``dispatch`` is what the queue message says about the attempt (its run id);
    the SSE and dev paths read the row's ``currentRunId`` (``resolve_run``).
    """
    timer = PipelineTimer()
    force_refresh = force_refresh or bool(entry.get("forceRefresh"))
    # A context reused across runs must never report the previous run's ids.
    _bind_run(None)
    youtube_id: str | None = entry.get("youtubeId") or entry.get("youtube_id")
    # The run event's outcome (None until a cache hit or a registered attempt) and
    # the attempt it names: kept here, since the generator may be closed elsewhere.
    outcome: str | None = None
    error_code: str | None = None
    event_run_id: str | None = None

    try:
        if not youtube_id:
            yield sse_event("error", {"message": "YouTube ID not found"})
            return

        user_id, request_id = _bind_usage_context(entry, youtube_id, video_summary_id)

        # The attempt's run id, before the cache check: a cache hit's status
        # callback closes the api's entry too (as cache_hit). Registered below,
        # only when the pipeline runs.
        run = resolve_run(entry, dispatch)
        _bind_run(run, bypass=force_refresh)
        event_run_id = run.run_id

        # Redis fast path (same YouTube video = instant serve). It runs BEFORE
        # the trace opens: a cache hit is no pipeline run and opens no trace.
        use_cache = settings.REDIS_ENABLED and not force_refresh
        cached = await _redis_payload(youtube_id) if use_cache else None
        if cached is not None:
            logger.info("[pipeline] Redis cache HIT for youtube_id=%s", youtube_id)
            if entry.get("status") != ProcessingStatus.COMPLETED.value:
                _spawn_cache_completion(repository, video_summary_id, cached)
            outcome = "cache_hit"
            async for event in _stream_cached_structured(video_summary_id, cached):
                yield event
            return

        eval_run = bool(entry.get("evalRun"))
        cold_media = bool(entry.get("coldMedia"))
        run = await _register_run(
            repository,
            video_summary_id,
            entry,
            run,
            request_id=request_id,
            user_id=user_id,
            run_path=run_path,
            bypass=force_refresh,
        )
        _bind_run(run, bypass=force_refresh)
        event_run_id = run.run_id
        log_run_started(
            run,
            video_summary_id=video_summary_id,
            youtube_id=youtube_id,
            run_path=run_path,
            bypass=force_refresh,
        )
        outcome = "interrupted"  # until the phases finish or raise
        trace_args = _run_trace_args(
            entry,
            youtube_id=youtube_id,
            video_summary_id=video_summary_id,
            user_id=user_id,
            request_id=request_id,
            run_path=run_path,
            run=run,
            bypass=force_refresh,
        )
        async with run_trace(**trace_args):
            await _mark_processing(repository, video_summary_id, youtube_id)

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
        outcome = "completed"

    except Exception as e:
        failure = classify_run_failure(e, video_summary_id, timer.elapsed())
        if outcome is not None:
            outcome, error_code = "failed", failure.code.value
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
        if outcome is not None:
            log_run_finished(
                outcome,
                run_id=event_run_id,
                video_summary_id=video_summary_id,
                youtube_id=youtube_id,
                duration_s=timer.elapsed(),
                run_path=run_path,
                error_code=error_code,
            )
