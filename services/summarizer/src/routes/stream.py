"""SSE streaming + regeneration HTTP endpoints.

Thin route layer — all pipeline orchestration lives in
:mod:`src.routes.pipeline_runner`. This file owns request validation, the
producer/consumer dispatch decision, and response shaping.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

import redis.exceptions as redis_exceptions
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config import settings
from src.dependencies import create_llm_provider, get_llm_service, get_video_repository
from src.models.schemas import ProcessingStatus, ProviderConfig
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.cached_response import (
    build_frontend_response,  # noqa: F401 — re-exported for backward compat
)
from src.routes.cached_response import (
    stream_cached_structured as _stream_cached_structured,
)
from src.routes.pipeline_broker import (
    acquire_owner_id,
    consume_from_broker,
    direct_stream_fallback,
    is_dev_override,
    spawn_producer_task,
)
from src.routes.pipeline_runner import (
    stream_summarization,  # noqa: F401 — re-exported for backward compat
)
from src.services.cache.pipeline_event_stream import pipeline_event_stream
from src.services.cache.response_cache import response_cache
from src.services.llm import LLMService
from src.services.media.s3_client import s3_client

logger = logging.getLogger(__name__)


router = APIRouter()


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ─────────────────────────────────────────────────────────────────────────────
# Streaming Route
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/summarize/stream/{video_summary_id}")
async def stream_summary(
    video_summary_id: str,
    repository: Annotated[MongoDBVideoRepository, Depends(get_video_repository)],
    llm_service: Annotated[LLMService, Depends(get_llm_service)],
):
    """
    Stream video summarization via Server-Sent Events.

    The run itself is ``stream_summarization`` (:mod:`src.routes.pipeline_runner`);
    its phases and their order live in :mod:`src.routes.pipeline_orchestration`.

    Concurrency model: a Redis lock makes the first connection the
    pipeline producer; every subsequent connection (StrictMode reconnect,
    additional tab, processing-manager hook) attaches as a consumer of
    the shared event stream. There is at most one pipeline run per
    ``video_summary_id`` no matter how many SSE clients are open.

    Dev-tools override: when the entry carries a ``providerConfig`` (set by
    the admin UI's "Generate with custom provider" flow), the broker and
    response cache are both bypassed so the new run completely overwrites
    whatever the default-provider run produced.
    """
    # Validate ObjectId format
    try:
        ObjectId(video_summary_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid video summary ID format")

    entry = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Video summary not found")

    # Apply provider config override (dev tools) by swapping in a custom LLM service.
    dev_override = is_dev_override(entry)
    if dev_override:
        provider_config = entry["providerConfig"]
        logger.info("Using custom provider config: %s", provider_config)
        providers = ProviderConfig(
            default=provider_config.get("default", "anthropic"),
            fast=provider_config.get("fast"),
            fallback=provider_config.get("fallback"),
        )
        custom_provider = create_llm_provider(providers)
        llm_service = LLMService(custom_provider)

    # Return cached result if already completed — but only for normal users.
    # Dev-tools override deliberately re-runs to validate the new provider.
    if not dev_override and entry.get("status") == ProcessingStatus.COMPLETED.value:
        return StreamingResponse(
            _stream_cached_structured(video_summary_id, entry),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    # Dev-tools override path: skip broker + response_cache so the fresh run
    # with the custom provider completely overwrites the existing record.
    if dev_override:
        return StreamingResponse(
            direct_stream_fallback(video_summary_id, entry, repository, llm_service),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    # Normal flow: claim producer ownership via the broker. Whoever wins runs
    # the pipeline once; everyone else (including this same client on a
    # StrictMode reconnect) just consumes the shared event stream.
    owner = acquire_owner_id()
    try:
        lock_acquired = await pipeline_event_stream.acquire_lock(video_summary_id, owner)
    except (OSError, redis_exceptions.RedisError) as e:
        # Redis unreachable in normal flow is a real outage, not a dev path.
        # Surface 503 so the client knows to retry instead of silently producing
        # duplicate runs (which the broker exists to prevent).
        logger.error(
            "Redis lock acquisition failed for %s: %s",
            video_summary_id,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail="Streaming service temporarily unavailable. Please retry.",
        )

    if lock_acquired:
        spawn_producer_task(video_summary_id, entry, repository, llm_service, owner)
    else:
        logger.info(
            "Attaching as additional consumer for %s — pipeline already in progress",
            video_summary_id,
        )

    return StreamingResponse(
        consume_from_broker(video_summary_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Regeneration Endpoint
# ─────────────────────────────────────────────────────────────────────────────


class RegenerateRequest(BaseModel):
    """Request body for regeneration endpoint."""

    force: bool = False


class RegenerateResponse(BaseModel):
    """Response for regeneration endpoint."""

    status: str
    video_summary_id: str
    message: str
    has_raw_transcript: bool = False
    generation: dict[str, Any] | None = None


@router.post("/regenerate/{video_summary_id}", response_model=RegenerateResponse)
async def regenerate_summary(
    video_summary_id: str,
    request: RegenerateRequest,
    repository: Annotated[MongoDBVideoRepository, Depends(get_video_repository)],
):
    """Trigger regeneration of a video summary."""
    try:
        ObjectId(video_summary_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid video summary ID format")

    entry = await asyncio.to_thread(repository.get_video_summary, video_summary_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Video summary not found")

    has_raw_transcript = False
    raw_transcript_ref = entry.get("rawTranscriptRef")

    if raw_transcript_ref and s3_client.is_available():
        try:
            has_raw_transcript = await s3_client.exists(raw_transcript_ref)
        except Exception as e:
            logger.warning("Failed to check S3 transcript: %s", e)

    if not has_raw_transcript and not request.force:
        return RegenerateResponse(
            status="unavailable",
            video_summary_id=video_summary_id,
            message="Raw transcript not available in S3. Use force=true to re-fetch from YouTube.",
            has_raw_transcript=False,
            generation=entry.get("generation"),
        )

    await asyncio.to_thread(repository.update_status, video_summary_id, ProcessingStatus.PENDING)

    # Invalidate Redis cache for this video
    youtube_id = entry.get("youtubeId") or entry.get("youtube_id")
    if youtube_id and settings.REDIS_ENABLED:
        try:
            await response_cache.invalidate(youtube_id)
            logger.info("Invalidated Redis cache for youtube_id=%s", youtube_id)
        except (OSError, redis_exceptions.ConnectionError) as e:
            logger.debug("Redis cache invalidation failed (non-critical): %s", e)

    return RegenerateResponse(
        status="ready",
        video_summary_id=video_summary_id,
        message="Video summary ready for regeneration. Connect to streaming endpoint to process.",
        has_raw_transcript=has_raw_transcript,
        generation=entry.get("generation"),
    )
