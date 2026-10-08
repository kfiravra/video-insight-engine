"""Pipeline driver for the worker.

Extracted from ``__main__.py`` so it can be unit-tested without dragging in
``aio_pika`` (which only the consumer loop needs). The function reuses the
exact codepath the SSE handler uses, so frontend SSE consumers attach as
followers regardless of which path started the work.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import redis.exceptions as redis_exceptions
from pymongo.errors import PyMongoError

from src.config import settings
from src.dependencies import (
    create_llm_provider,
    get_llm_provider,
    get_mongo_client,
)
from src.logging_config import get_logger
from src.models.schemas import ProcessingStatus
from src.models.schemas import ProviderConfig as ServiceProviderConfig
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.services.cache.pipeline_event_stream import pipeline_event_stream
from src.services.llm import LLMService
from src.services.override_state import clear_override
from src.worker.payload import VideoJobPayload

logger = get_logger(__name__)


def _llm_service(payload: VideoJobPayload) -> LLMService:
    """The job's LLM service: its own provider config, else the cached default."""
    if payload.providers is None:
        return LLMService(get_llm_provider())
    # The worker payload's ProviderConfig has the same shape as the
    # service-side schema but they're distinct classes; convert through
    # model_dump so create_llm_provider's type contract holds.
    schema_providers = ServiceProviderConfig.model_validate(payload.providers.model_dump())
    return LLMService(create_llm_provider(schema_providers))


def _is_done(entry: dict[str, Any] | None) -> bool:
    """True when the row is completed by the CURRENT pipeline — nothing left to run.

    The API resets a row to ``pending`` when it dispatches new work for it (new
    submission, retry), so such a row means this message is stale: e.g. an
    SSE-started run finished it while the message waited in the queue (A4: a
    full duplicate run, $0.16, on prod 2026-10-05). The exception is the API's
    stale-version regen, which re-dispatches a completed row stamped with an
    older ``pipelineVersion`` WITHOUT resetting its status — that job must run.
    Unstamped rows predate stamping and count as current, the same rule as the
    API's ``isStaleVersion``, so the two sides never disagree on staleness.
    """
    if entry is None or entry.get("status") != ProcessingStatus.COMPLETED.value:
        return False
    stamped = entry.get("pipelineVersion")
    return not isinstance(stamped, str) or stamped == settings.PIPELINE_VERSION


async def _is_completed(repository: MongoDBVideoRepository, video_summary_id: str) -> bool:
    """Post-lock re-check: another producer may have finished the row meanwhile."""
    try:
        entry = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
    except (OSError, PyMongoError) as e:
        # The pre-lock read already passed; a hiccup here (AutoReconnect,
        # NetworkTimeout, ...) must not fail the job — run it.
        logger.warning("worker_status_recheck_failed videoSummaryId=%s: %s", video_summary_id, e)
        return False
    return _is_done(entry)


async def _should_run_locked(
    repository: MongoDBVideoRepository, video_summary_id: str, owner: str
) -> bool:
    """Re-check under the lock; release it on every path that won't run the job.

    ``produce_to_broker`` releases the lock in its own ``finally`` only once it
    runs, so anything raised before that — CancelledError included — would
    otherwise leave the lock held for its full TTL, and the runner's retry would
    then be acked as ``skip_locked``: the job silently lost.
    """
    try:
        done = await _is_completed(repository, video_summary_id)
    except BaseException:
        await pipeline_event_stream.release_lock(video_summary_id, owner)
        raise
    if done:
        logger.info("worker_skip_completed videoSummaryId=%s (after lock)", video_summary_id)
        await pipeline_event_stream.release_lock(video_summary_id, owner)
    return not done


async def drive_pipeline(payload: VideoJobPayload) -> None:
    """Acquire the per-video lock and run the SSE pipeline producer.

    If the lock is already held (e.g. an SSE producer started first), this job
    is a no-op — the existing producer will finish it. So is a job whose row is
    already completed by the current pipeline version, checked before the lock
    and again once it is held (the other producer may have finished in between).
    """
    # Import here to keep cold-start fast and avoid circular imports.
    from src.routes.pipeline_broker import produce_to_broker

    video_summary_id = payload.video_summary_id
    # FastAPI's Depends() doesn't run outside HTTP requests, so build the
    # repository + LLM service directly. Using the cached mongo client /
    # provider keeps us aligned with the HTTP path's behaviour.
    repository = MongoDBVideoRepository(get_mongo_client().get_default_database())
    llm_service = _llm_service(payload)

    entry = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
    if entry is None:
        # Mongo row vanished after publish — surface to DLQ rather than spin
        # forever. Could happen if a user deleted their submission mid-flight.
        raise RuntimeError(f"video_summary not found: {video_summary_id}")
    if _is_done(entry):
        logger.info("worker_skip_completed videoSummaryId=%s", video_summary_id)
        return

    owner = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        acquired = await pipeline_event_stream.acquire_lock(video_summary_id, owner)
    except (OSError, redis_exceptions.RedisError) as e:
        # Redis outage → treat as transient; the runner will republish.
        raise RuntimeError(f"redis_unreachable: {e}") from e

    if not acquired:
        logger.info("worker_skip_locked videoSummaryId=%s", video_summary_id)
        return  # Treated as success — another producer is already running it.

    if not await _should_run_locked(repository, video_summary_id, owner):
        return

    try:
        await produce_to_broker(
            video_summary_id,
            entry,
            repository,
            llm_service,
            owner,
            force_refresh=payload.bypass_cache,
        )
    finally:
        await asyncio.to_thread(clear_override, video_summary_id)
