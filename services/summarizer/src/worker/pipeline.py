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

import redis.exceptions as redis_exceptions

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


async def _is_completed(repository: MongoDBVideoRepository, video_summary_id: str) -> bool:
    """True when the row is already completed — another producer finished the job.

    The API resets a row to ``pending`` whenever it dispatches work for it
    (new submission, retry, regenerate), so ``completed`` here means this
    message is stale: e.g. an SSE-started run finished it while the message
    waited in the queue (A4: a full duplicate run, $0.16, on prod 2026-10-05).
    """
    try:
        entry = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
    except OSError as e:
        # The pre-lock read already passed; a hiccup here must not fail the job.
        logger.warning("worker_status_recheck_failed videoSummaryId=%s: %s", video_summary_id, e)
        return False
    return entry is not None and entry.get("status") == ProcessingStatus.COMPLETED.value


async def drive_pipeline(payload: VideoJobPayload) -> None:
    """Acquire the per-video lock and run the SSE pipeline producer.

    If the lock is already held (e.g. an SSE producer started first), this job
    is a no-op — the existing producer will finish it. So is a job whose row is
    already completed, checked before the lock and again once it is held (the
    other producer may have finished in between).
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
    if entry.get("status") == ProcessingStatus.COMPLETED.value:
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

    if await _is_completed(repository, video_summary_id):
        logger.info("worker_skip_completed videoSummaryId=%s (after lock)", video_summary_id)
        await pipeline_event_stream.release_lock(video_summary_id, owner)
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
