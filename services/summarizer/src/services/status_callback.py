"""Status callback service for WebSocket broadcasts."""

import asyncio
import logging

import httpx

from src.config import settings
from src.services.run_context import current_run_id

logger = logging.getLogger(__name__)

# Keep strong references so fire-and-forget status tasks aren't GC'd mid-POST
# (same pattern as _PRODUCER_TASKS / _FAITHFULNESS_TASKS).
_STATUS_TASKS: set[asyncio.Task] = set()


async def send_video_status(
    video_summary_id: str,
    user_id: str | None,
    status: str,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
    cache_hit: bool = False,
    run_id: str | None = None,
):
    """Send video status update to API for WebSocket broadcast.

    ``cache_hit`` marks a ``completed`` that was served from the response
    cache with no run: vie-api releases the run's minutes hold as cached (I3).
    ``run_id`` (default: the attempt bound in this context) lets vie-api close
    that attempt's ``runs[]`` entry on a terminal status (P0b).
    """
    run_id = run_id or current_run_id()
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "type": "video.status",
                "payload": {
                    "videoSummaryId": video_summary_id,
                    "status": status,
                },
            }

            if user_id:
                payload["payload"]["userId"] = user_id
            if progress is not None:
                payload["payload"]["progress"] = progress
            if message:
                payload["payload"]["message"] = message
            if error:
                payload["payload"]["error"] = error
            if cache_hit:
                payload["payload"]["cacheHit"] = True
            if run_id:
                payload["payload"]["runId"] = run_id

            await client.post(
                f"{settings.API_URL}/internal/status",
                json=payload,
                headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                timeout=5.0,
            )
    except Exception as e:
        # Log but don't fail - status updates are best-effort
        logger.warning(f"Failed to send status callback: {e}")


def send_video_status_background(
    video_summary_id: str,
    user_id: str | None,
    status: str,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
) -> None:
    """Fire-and-forget wrapper for callers on a latency-sensitive path.

    ``send_video_status`` opens a fresh client and POSTs with a 5s timeout —
    awaiting it inline in the SSE generator adds up to 5s of dead air to the
    user-facing stream for a best-effort side-channel notification.
    """
    task = asyncio.create_task(
        send_video_status(video_summary_id, user_id, status, progress, message, error)
    )
    _STATUS_TASKS.add(task)
    task.add_done_callback(_STATUS_TASKS.discard)
