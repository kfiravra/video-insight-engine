"""Active stall sweeper — turns dead pipeline runs into ``failed`` rows + alerts.

Before this, a stuck ``processing`` row was only noticed lazily: the API
re-dispatched when the *same* video was resubmitted after 30 minutes
(``video.service.ts``). A job nobody resubmitted stayed ``processing``
forever, trapping SSE attachers and hiding the outage.

The sweep runs every ``STALL_SWEEP_INTERVAL_SECONDS`` from the summarizer's
HTTP-process lifespan (never the worker — one sweeper per deployment). A row
is stalled when BOTH hold:

1. ``updatedAt`` is older than ``STALL_THRESHOLD_MINUTES`` — no status
   transition or result save in that window.
2. Nobody holds the Redis producer lock. The lock is heart-beaten by a live
   producer (``pipeline_broker._heartbeat_lock``) and auto-expires
   ``PIPELINE_LOCK_TTL_SECONDS`` after a crash, so "unheld" means the
   producer is gone. ``lock_held`` fails *open* on Redis errors, so an
   outage pauses sweeping rather than mass-failing live runs.

For each stalled row the sweeper mirrors what a pipeline failure does:
a compare-and-set ``failed`` write on the cache row (``mark_stalled_failed``
— only if it is *still* ``processing`` with the stale ``updatedAt``, so a run
that resumed or was re-dispatched between find and write is left alone), the
``video.status`` callback to vie-api (flips ``userVideos``, broadcasts over
WS, releases the dispatch guard so a retry can re-dispatch immediately), then
an ``llm_alerts`` row + webhook so the operator hears about it. No cooldown
is needed — the status flip is what stops the same row from alerting twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.collection import Collection

from src.models.schemas import ErrorCode
from src.repositories.mongodb_repository import MongoDBVideoRepository

logger = logging.getLogger(__name__)

ALERT_TYPE = "pipeline_stalled"
# Rows drained per sweep — bounds one cycle's work during a mass outage.
SWEEP_BATCH_LIMIT = 50

LockProbe = Callable[[str], Awaitable[bool]]
StatusNotifier = Callable[..., Awaitable[None]]
AlertDeliverer = Callable[[dict], bool]


def build_stall_alert(row: dict[str, Any], stalled_for: timedelta, now: datetime) -> dict:
    """The ``llm_alerts`` document for one stalled row.

    ``severity`` is explicit so the admin Alerts page files it under
    *critical* instead of falling back to a regex over ``type``.
    """
    return {
        "type": ALERT_TYPE,
        "severity": "critical",
        "video_summary_id": str(row["_id"]),
        "youtube_id": row.get("youtubeId"),
        "stalled_minutes": int(stalled_for.total_seconds() // 60),
        "service": "summarizer-sweeper",
        "timestamp": now,
    }


def stall_error_message(stalled_for: timedelta) -> str:
    minutes = int(stalled_for.total_seconds() // 60)
    return f"Pipeline stalled: no progress for {minutes} min (marked failed by the stall sweeper)"


class StallSweeper:
    """One sweep = find candidates → confirm the producer is gone → fail + alert."""

    def __init__(
        self,
        repository: MongoDBVideoRepository,
        alerts_collection: Collection,
        lock_held: LockProbe,
        notify_status: StatusNotifier,
        deliver_alert: AlertDeliverer,
        threshold: timedelta,
    ) -> None:
        self._repository = repository
        self._alerts = alerts_collection
        self._lock_held = lock_held
        self._notify_status = notify_status
        self._deliver_alert = deliver_alert
        self._threshold = threshold

    async def sweep_once(self, now: datetime | None = None) -> list[dict]:
        """Fail every confirmed-stalled row; return the alerts written."""
        now = now or datetime.now(UTC)
        older_than = now - self._threshold
        candidates = await asyncio.to_thread(
            self._repository.find_stalled_processing, older_than, SWEEP_BATCH_LIMIT
        )
        written: list[dict] = []
        for row in candidates:
            video_summary_id = str(row["_id"])
            if await self._lock_held(video_summary_id):
                logger.info("stall_sweep_skipped_live_producer video=%s", video_summary_id)
                continue
            alert = await self._fail_row(row, older_than, now)
            if alert is not None:
                written.append(alert)
        if candidates:
            logger.info("stall_sweep_done candidates=%d failed=%d", len(candidates), len(written))
        return written

    async def _fail_row(
        self, row: dict[str, Any], older_than: datetime, now: datetime
    ) -> dict | None:
        """Flip one row; ``None`` when it was revived between find and write."""
        video_summary_id = str(row["_id"])
        updated_at = _as_utc(row.get("updatedAt")) or now
        stalled_for = now - updated_at
        message = stall_error_message(stalled_for)
        flipped = await asyncio.to_thread(
            self._repository.mark_stalled_failed,
            video_summary_id,
            older_than,
            message,
            ErrorCode.UNKNOWN_ERROR,
        )
        if not flipped:
            logger.info("stall_sweep_skipped_row_revived video=%s", video_summary_id)
            return None
        logger.error(
            "pipeline_stalled video=%s youtube=%s stalled_minutes=%d",
            video_summary_id,
            row.get("youtubeId"),
            int(stalled_for.total_seconds() // 60),
        )
        # Best-effort side channels: the row flip above is the source of truth.
        await self._notify_status(
            video_summary_id, None, "failed", error=message, run_id=row.get("currentRunId")
        )
        alert = build_stall_alert(row, stalled_for, now)
        try:
            # insert_one mutates its argument (adds ``_id``); keep the webhook body clean.
            await asyncio.to_thread(self._alerts.insert_one, dict(alert))
        except Exception as e:  # noqa: BLE001 — alert bookkeeping must not abort the sweep
            logger.warning("stall_alert_write_failed video=%s error=%s", video_summary_id, e)
        # deliver_alert is blocking urllib and never raises — keep it off the loop.
        await asyncio.to_thread(self._deliver_alert, alert)
        return alert


def _as_utc(value: Any) -> datetime | None:
    """pymongo returns naive UTC datetimes unless tz_aware=True; normalize."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def stall_sweeper_loop(sweeper: StallSweeper, interval_seconds: int) -> None:
    """Run sweeps forever; start via ``asyncio.create_task`` in the lifespan."""
    logger.info("stall_sweeper_started interval=%d", interval_seconds)
    while True:
        try:
            await sweeper.sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — one bad cycle must not kill the loop
            logger.error("stall_sweep_failed error=%s", e, exc_info=True)
        await asyncio.sleep(interval_seconds)


def build_default_sweeper(
    repository: MongoDBVideoRepository, threshold_minutes: int
) -> StallSweeper:
    """Wire the production collaborators (Redis lock, API callback, webhook)."""
    from llm_common.alerts import deliver_alert

    from src.services.cache.pipeline_event_stream import pipeline_event_stream
    from src.services.status_callback import send_video_status

    return StallSweeper(
        repository=repository,
        alerts_collection=repository.alerts_collection,
        lock_held=pipeline_event_stream.lock_held,
        notify_status=send_video_status,
        deliver_alert=deliver_alert,
        threshold=timedelta(minutes=threshold_minutes),
    )
