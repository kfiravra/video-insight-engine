"""Broker-side pipeline plumbing: producer, consumer, lock heartbeat.

Split out from :mod:`src.routes.pipeline_runner` to keep that file under the
project's 500-line cap. The orchestrator (``stream_summarization``) lives
there; everything that fans the SSE stream through the per-video Redis
broker lives here.

This module is the only thing that knows about the broker's lock TTL,
producer/consumer pairing, dedup semantics, and the dev-tools direct
bypass. Callers should treat it as the public broker interface.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, AsyncGenerator

import redis.exceptions as redis_exceptions

from src.config import settings
from src.models.schemas import ErrorCode
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.pipeline_runner import stream_summarization
from src.services.cache.pipeline_event_stream import pipeline_event_stream
from src.services.llm import LLMService
from src.services.pipeline.pipeline_helpers import sse_event

logger = logging.getLogger(__name__)


# Strong references for fire-and-forget producer tasks. The event loop
# only keeps weak refs to tasks created via ``asyncio.create_task``, so
# without a module-level set the GC could cancel a producer mid-run.
_PRODUCER_TASKS: set[asyncio.Task[None]] = set()


# ─────────────────────────────────────────────────────────────────────────────
# Lock heartbeat
# ─────────────────────────────────────────────────────────────────────────────


async def _heartbeat_lock(video_summary_id: str, owner: str, stop: asyncio.Event) -> None:
    """Periodically extend the producer lock so long pipelines don't lose it.

    The lock TTL is sized for crash recovery (10 min default); without a
    heartbeat, a >10-min pipeline (chunked extraction on a long video, plus
    translation) would lose the lock and a parallel producer could spin up.
    The same refresh renews the run's event-stream TTL, so a phase silent for
    longer than it cannot let the stream expire under a live producer.
    Heartbeat interval is one-third of the TTL — gives two retries before
    expiry if Redis is briefly unreachable.
    """
    interval = max(30, settings.PIPELINE_LOCK_TTL_SECONDS // 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return  # stop_event set — pipeline finished
        except asyncio.TimeoutError:
            pass
        extended = await pipeline_event_stream.refresh_lock(video_summary_id, owner)
        if not extended:
            logger.warning(
                "Lock refresh for %s reported not-owned; another worker may "
                "have re-claimed it. Continuing to publish but dedup is at risk.",
                video_summary_id,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Producer / consumer
# ─────────────────────────────────────────────────────────────────────────────


async def produce_to_broker(
    video_summary_id: str,
    entry: dict[str, Any],
    repository: MongoDBVideoRepository,
    llm_service: LLMService,
    owner: str,
    force_refresh: bool = False,
) -> None:
    """Run the pipeline and republish each SSE chunk through the broker.

    Lifetime is detached from any single SSE connection — clients can
    abort and reconnect without killing the pipeline. The caller's lock
    claim emptied the stream, so this run's events are the only ones in it.
    The finally block always sends DONE and releases the lock so consumers
    exit cleanly even on producer failure.

    ``force_refresh=True`` (a bypassCache submission) skips the response_cache
    fast path — the whole point of a version bump is a fresh pipeline run, and
    the Redis cache is keyed by youtubeId, not version, so without this the
    new run is instantly re-fed the stale payload.

    ``stream_summarization`` already catches every pipeline-level error and
    yields a structured error event, so the only exception classes that
    can escape into this function are infrastructure failures (Mongo /
    Redis) and ``CancelledError`` (BaseException, not caught here).
    """
    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_lock(video_summary_id, owner, stop_heartbeat),
        name=f"pipeline-lock-heartbeat:{video_summary_id}",
    )
    try:
        # Re-fetch entry for fresh state (the handler's snapshot may be stale).
        try:
            fresh = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
        except OSError as e:
            logger.exception(
                "MongoDB fetch failed for %s during producer startup: %s",
                video_summary_id,
                e,
            )
            fresh = None
        current_entry = fresh if fresh is not None else entry

        async for chunk in stream_summarization(
            video_summary_id,
            current_entry,
            repository,
            llm_service,
            force_refresh=force_refresh,
        ):
            try:
                await pipeline_event_stream.publish(video_summary_id, chunk)
            except (OSError, redis_exceptions.RedisError) as e:
                logger.warning(
                    "Broker publish failed for %s (event dropped): %s",
                    video_summary_id,
                    e,
                )
    except (OSError, redis_exceptions.RedisError) as e:
        logger.exception(
            "Pipeline producer infra failure for %s: %s",
            video_summary_id,
            e,
        )
        try:
            err_chunk = sse_event(
                "error",
                {
                    "message": "An unexpected error occurred during processing.",
                    "code": ErrorCode.UNKNOWN_ERROR.value,
                },
            )
            await pipeline_event_stream.publish(video_summary_id, err_chunk)
        except (OSError, redis_exceptions.RedisError):
            pass
    finally:
        stop_heartbeat.set()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        try:
            await pipeline_event_stream.mark_done(video_summary_id)
        finally:
            await pipeline_event_stream.release_lock(video_summary_id, owner)


async def consume_from_broker(video_summary_id: str) -> AsyncGenerator[str, None]:
    """Subscribe to the per-video stream and yield SSE chunks until DONE."""
    async for event in pipeline_event_stream.subscribe(video_summary_id):
        yield event
    yield "data: [DONE]\n\n"


async def direct_stream_fallback(
    video_summary_id: str,
    entry: dict[str, Any],
    repository: MongoDBVideoRepository,
    llm_service: LLMService,
) -> AsyncGenerator[str, None]:
    """Run the pipeline straight to this connection — no broker, no dedup.

    Reserved for the dev-tools "regenerate with custom provider" path: the
    operator wants to re-run the pipeline against the live LLM provider
    override and overwrite whatever's currently stored. Bypassing the broker
    avoids dedup against a previous (default-provider) run, and bypassing
    the response_cache (``force_refresh=True``) avoids serving the stale
    cached output that was generated under the default provider.
    """
    logger.info(
        "Dev-tools direct stream for %s — broker bypassed, response_cache bypassed",
        video_summary_id,
    )
    async for chunk in stream_summarization(
        video_summary_id,
        entry,
        repository,
        llm_service,
        force_refresh=True,
    ):
        yield chunk
    yield "data: [DONE]\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def is_dev_override(entry: dict[str, Any]) -> bool:
    """A non-empty providerConfig is the dev-tools "regenerate with override" signal."""
    return bool(entry.get("providerConfig"))


def spawn_producer_task(
    video_summary_id: str,
    entry: dict[str, Any],
    repository: MongoDBVideoRepository,
    llm_service: LLMService,
    owner: str,
) -> asyncio.Task[None]:
    """Spawn the pipeline producer with a strong-ref + error-logging callback."""
    producer_task = asyncio.create_task(
        produce_to_broker(video_summary_id, entry, repository, llm_service, owner),
        name=f"pipeline-producer:{video_summary_id}",
    )
    # Hold a strong reference until the task finishes — the event loop only
    # keeps weak refs, so without this the GC could cancel a fire-and-forget
    # producer mid-run.
    _PRODUCER_TASKS.add(producer_task)
    producer_task.add_done_callback(_PRODUCER_TASKS.discard)
    producer_task.add_done_callback(
        lambda t, vid=video_summary_id: (
            logger.error(
                "Producer task for %s ended with exception: %s",
                vid,
                t.exception(),
                exc_info=t.exception(),
            )
            if not t.cancelled() and t.exception() is not None
            else None
        )
    )
    return producer_task


def acquire_owner_id() -> str:
    """Stable per-process lock owner id."""
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
