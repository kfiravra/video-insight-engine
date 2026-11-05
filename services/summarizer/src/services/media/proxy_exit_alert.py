"""One alert when more than half of the proxy exits are benched.

``download_utils._ExitMemory`` benches an exit for 15 min after a bot check, a
429, a session block, an unavailable answer or (downloads) a 403 on the video
data, and reports the block that tips its pool past half (at most once per
cooldown, per process). This module
turns that into the stall sweeper's alert shape: an ERROR line (one Sentry
event), a POST to ALERT_WEBHOOK_URL (``llm_common.alerts.deliver_alert``) and
an ``llm_alerts`` row for the admin Alerts page.

The block is recorded from worker threads and from the event loop
(``local_video``), and ``deliver_alert`` blocks for up to 3 s, so delivery
runs on a daemon thread. Only sticky-session labels (``session -N``) ever
reach the alert, never a proxy URL.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from llm_common.alerts import deliver_alert
from pymongo.collection import Collection

from src.logging_config import configured_service_name

logger = logging.getLogger(__name__)

ALERT_TYPE = "proxy_exits_benched"


def build_benched_alert(
    labels: Sequence[str], total: int, *, window_minutes: int, service: str, now: datetime
) -> dict[str, Any]:
    """The ``llm_alerts`` document; Telegram shows one ``key: value`` line per field."""
    return {
        "type": ALERT_TYPE,
        "severity": "critical",
        "service": service,
        "benched": f"{len(labels)}/{total}",
        "exits": ", ".join(labels),
        "window_minutes": window_minutes,
        "message": (
            "More than half of the proxy exits got a bot check, 429, session block, "
            f"unavailable answer or video-data 403 in the last {window_minutes} min; YouTube "
            "calls from this process are failing or slow"
        ),
        "timestamp": now,
    }


def _alerts_collection() -> Collection:
    # Lazy: src.dependencies pulls in the LLM stack; both processes have it loaded by now.
    from src.dependencies import get_mongo_client
    from src.repositories.mongodb_repository import MongoDBVideoRepository

    return MongoDBVideoRepository(get_mongo_client().get_default_database()).alerts_collection


def _post(alert: dict[str, Any]) -> None:
    """Webhook first (it pages; at most 3 s, never raises), then the best-effort Mongo row."""
    deliver_alert(alert)
    try:
        # insert_one adds ``_id`` to its argument; keep the caller's dict clean.
        _alerts_collection().insert_one(dict(alert))
    except Exception as e:  # noqa: BLE001 — alert bookkeeping must never break a YouTube call
        logger.warning("proxy_exit_alert_write_failed error=%s", type(e).__name__)


def _deliver(alert: dict[str, Any]) -> threading.Thread:
    """Hand the alert to a daemon thread; returned so tests can join it."""
    thread = threading.Thread(target=_post, args=(alert,), name="proxy-exit-alert", daemon=True)
    thread.start()
    return thread


def alert_benched_exits(labels: Sequence[str], total: int, *, window_minutes: int) -> None:
    """Log and send one proxy_exits_benched alert; never raises into the YouTube call."""
    try:
        service = configured_service_name()
        alert = build_benched_alert(
            labels, total, window_minutes=window_minutes, service=service, now=datetime.now(UTC)
        )
        logger.error(
            "proxy_exits_benched service=%s benched=%s exits=%s",
            service,
            alert["benched"],
            alert["exits"],
        )
        _deliver(alert)
    except Exception as e:  # noqa: BLE001 — alerting must never break the caller
        logger.warning("proxy_exit_alert_failed error=%s", type(e).__name__)
