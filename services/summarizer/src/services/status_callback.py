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
    cache with no run: vie-api releases the run's token hold as cached (I3).
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


# ─── Run cost (token ledger) ───

# The api settles the run's token hold on this; a lost callback is settled by
# its repair job from llm_usage hours later, so a few quick retries are cheap.
_RUN_COST_ATTEMPTS = 3
_RUN_COST_BACKOFF_S = 0.5


async def post_run_cost(
    video_summary_id: str,
    billing_run_id: str | None,
    cost_usd: float,
    llm_calls: int,
) -> bool:
    """Report a finished run's total LLM cost to vie-api (``POST /internal/run-cost``).

    ``cost_usd`` is ``pipeline.timing.costUsd`` (every call of the run, judges
    and transcription included); ``billing_run_id`` the cache row's
    ``billingRunId`` as the run read it, so the api can tell this run's hold
    from a newer dispatch's. Idempotent on the api side; retried on transport
    errors and 5xx. Never raises; returns whether the api accepted it.
    """
    payload: dict[str, object] = {
        "videoSummaryId": video_summary_id,
        "costUsd": cost_usd,
        "llmCalls": llm_calls,
    }
    if billing_run_id:
        payload["billingRunId"] = billing_run_id
    for attempt in range(1, _RUN_COST_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.API_URL}/internal/run-cost",
                    json=payload,
                    headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                    timeout=5.0,
                )
            if response.status_code < 500:
                if response.status_code >= 400:
                    logger.warning(
                        "run-cost callback refused for %s: %s",
                        video_summary_id,
                        response.status_code,
                    )
                return response.status_code < 400
        except httpx.HTTPError as exc:
            logger.warning(
                "run-cost callback failed for %s (attempt %d): %s", video_summary_id, attempt, exc
            )
        if attempt < _RUN_COST_ATTEMPTS:
            await asyncio.sleep(_RUN_COST_BACKOFF_S * attempt)
    return False
