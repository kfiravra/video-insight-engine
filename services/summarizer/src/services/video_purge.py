"""Global video purge — remove one video's artifacts from every summarizer-owned store.

The API's video cascade calls ``POST /internal/videos/{youtube_id}/purge`` before
it deletes the Mongo rows. Every store is attempted independently and failures
are reported as warnings: a broken Redis must not leave S3 objects behind, and
the caller decides what a partial purge means.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from src.config import settings
from src.services.cache.pipeline_event_stream import pipeline_event_stream
from src.services.cache.response_cache import response_cache
from src.services.media.s3_client import S3Client, s3_client
from src.services.override_state import clear_override
from src.services.vector.store import _get_vector_service

logger = logging.getLogger(__name__)


@dataclass
class PurgeOutcome:
    """Per-store counts plus one warning per store that failed."""

    qdrant_points: int = 0
    s3_objects: int = 0
    redis_keys: int = 0
    warnings: list[str] = field(default_factory=list)


async def purge_video(youtube_id: str, video_summary_ids: list[str]) -> PurgeOutcome:
    """Delete vectors, S3 objects, Redis keys and in-memory state for one video."""
    outcome = PurgeOutcome()
    await _purge_qdrant(youtube_id, outcome)
    await _purge_s3(youtube_id, outcome)
    await _purge_redis(youtube_id, video_summary_ids, outcome)
    for video_summary_id in video_summary_ids:
        clear_override(video_summary_id)
    logger.info(
        "video_purged youtube_id=%s qdrant=%d s3=%d redis=%d warnings=%d",
        youtube_id,
        outcome.qdrant_points,
        outcome.s3_objects,
        outcome.redis_keys,
        len(outcome.warnings),
    )
    return outcome


async def _purge_qdrant(youtube_id: str, outcome: PurgeOutcome) -> None:
    if not settings.QDRANT_ENABLED:
        return
    try:
        service = _get_vector_service()
        count = await asyncio.to_thread(service.count_points, youtube_id)
        if not await asyncio.to_thread(service.delete_video, youtube_id):
            raise RuntimeError("delete_video reported failure")
        outcome.qdrant_points = count
    except Exception as exc:  # noqa: BLE001 — best-effort per store; surfaced via warnings
        logger.warning("purge_store_failed store=qdrant youtube_id=%s", youtube_id, exc_info=True)
        outcome.warnings.append(f"qdrant: {exc}")


async def _purge_s3(youtube_id: str, outcome: PurgeOutcome) -> None:
    if not S3Client.is_available():
        return
    try:
        outcome.s3_objects = await s3_client.delete_prefix(f"videos/{youtube_id}/")
    except Exception as exc:  # noqa: BLE001 — best-effort per store; surfaced via warnings
        logger.warning("purge_store_failed store=s3 youtube_id=%s", youtube_id, exc_info=True)
        outcome.warnings.append(f"s3: {exc}")


async def _purge_redis(
    youtube_id: str, video_summary_ids: list[str], outcome: PurgeOutcome
) -> None:
    if not settings.REDIS_ENABLED:
        return
    try:
        # Every pipeline version's response key, not just the current one.
        outcome.redis_keys += await response_cache.purge_all_versions(youtube_id)
        for video_summary_id in video_summary_ids:
            outcome.redis_keys += await pipeline_event_stream.purge(video_summary_id)
    except Exception as exc:  # noqa: BLE001 — best-effort per store; surfaced via warnings
        logger.warning("purge_store_failed store=redis youtube_id=%s", youtube_id, exc_info=True)
        outcome.warnings.append(f"redis: {exc}")
