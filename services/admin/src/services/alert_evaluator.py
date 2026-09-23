"""Periodic evaluation of aggregate alert thresholds (spike, failures, backups).

The per-call cost threshold fires inline in llm-common's MongoDBUsageCallback.
The other two thresholds configured via POST /alerts/config — daily spend
spike and failure rate — are aggregate properties of the ledger and can only
be judged over a window, so this evaluator polls ``llm_usage`` on an interval
(started from the app lifespan, mirroring ``health_poller_loop``).

The same loop doubles as a dead-man's switch for ``scripts/backup.sh``: when
the newest manifest under ``BACKUP_DIR`` is older than ``BACKUP_MAX_AGE_HOURS``
(or no backup exists at all), a ``backup_stale`` alert fires. A missing/
unmounted ``BACKUP_DIR`` disables the check (dev boxes without the mount).

Alerts are written to ``llm_alerts`` (rendered by the admin UI) and also
POSTed to ``ALERT_WEBHOOK_URL`` when configured. A per-type cooldown stops a
sustained condition from re-alerting on every cycle.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.config import settings
from src.dependencies import get_database

logger = structlog.get_logger(__name__)

EVAL_INTERVAL_SECONDS = 300  # 5 minutes
WEBHOOK_TIMEOUT_SECONDS = 3.0

# Failure rate: judged over the trailing hour, and only once there is a
# meaningful sample — 2 failures out of 3 calls is noise, not an incident.
FAILURE_WINDOW_MINUTES = 60
MIN_FAILURE_SAMPLE = 10

# Daily spike: today's spend vs the average of the previous N full days.
# A cold baseline (barely any historical spend) can't meaningfully "spike".
SPIKE_BASELINE_DAYS = 7
MIN_BASELINE_DAILY_USD = 0.50
# Spike proration floor: never compare against less than one hour's worth of
# baseline — just after midnight the elapsed fraction tends to 0 and any
# nonzero spend would otherwise register as a "spike".
MIN_PRORATE_FRACTION = 1.0 / 24.0

# Backup manifest timestamps are written by scripts/backup.sh as UTC.
BACKUP_MANIFEST_TS_FORMAT = "%Y%m%dT%H%M%SZ"

# Re-alert suppression per alert type.
COOLDOWNS = {
    "daily_spend_spike": timedelta(hours=6),
    "high_failure_rate": timedelta(hours=1),
    "backup_stale": timedelta(hours=6),
}

# ``severity`` is set explicitly on every alert (warning|critical) so the admin
# Alerts page files it correctly instead of regex-guessing from ``type``.
# Mirrors the defaults served by GET /alerts/config when no doc exists.
DEFAULT_CONFIG = {
    "cost_threshold_usd": 0.50,
    "daily_spike_multiplier": 2.0,
    "failure_rate_threshold": 0.20,
}


# ─── Pure decision logic ───


def build_spike_alert(
    today_usd: float,
    baseline_daily_usd: float,
    multiplier: float,
    now: datetime,
    day_fraction: float = 1.0,
) -> dict | None:
    """Alert when partial-day spend exceeds multiplier x the prorated baseline."""
    # Cold-start guard applies PRE-proration, to the full-day baseline: it asks
    # "is there enough spend history for a spike to be meaningful?", which is a
    # property of the history, not of how far into today we are. Guarding the
    # prorated value instead would silence the check for the first hours of
    # every day even with a warm baseline.
    if baseline_daily_usd < MIN_BASELINE_DAILY_USD:
        return None
    # Pro-rate by fraction-of-day-elapsed so 09:00 spend is compared against
    # nine hours' worth of typical spend — the full-day baseline under-alerts
    # all morning. Clamp to [MIN_PRORATE_FRACTION, 1.0] (midnight guard; also
    # neutralizes bogus <=0 / >1 inputs).
    fraction = min(max(day_fraction, MIN_PRORATE_FRACTION), 1.0)
    if today_usd <= multiplier * baseline_daily_usd * fraction:
        return None
    return {
        "type": "daily_spend_spike",
        "severity": "warning",
        "cost_usd": round(today_usd, 4),
        "baseline_daily_usd": round(baseline_daily_usd, 4),
        "day_fraction": round(fraction, 3),
        "threshold": multiplier,
        "service": "admin-evaluator",
        "timestamp": now,
    }


def build_failure_alert(failures: int, total: int, threshold: float, now: datetime) -> dict | None:
    """Alert when the failure rate over the window exceeds the threshold."""
    if total < MIN_FAILURE_SAMPLE:
        return None
    rate = failures / total
    if rate <= threshold:
        return None
    return {
        "type": "high_failure_rate",
        "severity": "critical",
        "failure_rate": round(rate, 3),
        "sample_size": total,
        "window_minutes": FAILURE_WINDOW_MINUTES,
        "threshold": threshold,
        "service": "admin-evaluator",
        "timestamp": now,
    }


def build_backup_stale_alert(
    latest_backup: datetime | None, max_age_hours: float, now: datetime
) -> dict | None:
    """Alert when the newest backup is older than max_age_hours.

    ``latest_backup is None`` means the backups dir exists but contains no
    manifest at all — for a dead-man's switch that is the worst case, so it
    alerts instead of staying silent.
    """
    age_hours: float | None = None
    if latest_backup is not None:
        age_hours = (now - latest_backup).total_seconds() / 3600
        if age_hours <= max_age_hours:
            return None
    return {
        "type": "backup_stale",
        "severity": "critical",
        "latest_backup": latest_backup,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "max_age_hours": max_age_hours,
        "service": "admin-evaluator",
        "timestamp": now,
    }


# ─── Backup manifest scan ───


def _latest_backup_time(backup_root: Path) -> datetime | None:
    """Newest backup time across ``<backup_root>/<ts>/manifest.json`` files.

    Prefers the manifest's own ``timestamp`` field (written by
    scripts/backup.sh); falls back to the file's mtime when it can't be
    parsed. Sync I/O is acceptable here: a handful of sub-KB reads once per
    EVAL_INTERVAL_SECONDS.
    """
    latest: datetime | None = None
    for manifest in backup_root.glob("*/manifest.json"):
        try:
            stamp = json.loads(manifest.read_text())["timestamp"]
            taken = datetime.strptime(stamp, BACKUP_MANIFEST_TS_FORMAT).replace(tzinfo=UTC)
        except (OSError, ValueError, KeyError, TypeError):
            try:
                taken = datetime.fromtimestamp(manifest.stat().st_mtime, tz=UTC)
            except OSError:
                continue
        if latest is None or taken > latest:
            latest = taken
    return latest


def _backup_stale_candidate(now: datetime) -> dict | None:
    """Run the dead-man's switch; skipped when BACKUP_DIR isn't mounted."""
    backup_root = Path(settings.BACKUP_DIR)
    if not backup_root.is_dir():
        # Dev boxes without the compose mount: monitoring not configured —
        # debug (not warning) so a 5-min loop doesn't spam the logs.
        logger.debug("backup_dir_missing_skipping_stale_check", path=str(backup_root))
        return None
    return build_backup_stale_alert(
        _latest_backup_time(backup_root), settings.BACKUP_MAX_AGE_HOURS, now
    )


# ─── Ledger queries ───


async def _load_config(db: AsyncIOMotorDatabase) -> dict:
    doc = await db.llm_alert_config.find_one({"_id": "default"})
    return {**DEFAULT_CONFIG, **(doc or {})}


async def _sum_cost(db: AsyncIOMotorDatabase, start: datetime, end: datetime) -> float:
    pipeline = [
        {"$match": {"timestamp": {"$gte": start, "$lt": end}}},
        {"$group": {"_id": None, "total": {"$sum": "$cost_usd"}}},
    ]
    rows = await db.llm_usage.aggregate(pipeline).to_list(1)
    return float(rows[0]["total"]) if rows else 0.0


async def _failure_counts(db: AsyncIOMotorDatabase, since: datetime) -> tuple[int, int]:
    """Return (failures, total) over llm_usage rows since the given time."""
    pipeline = [
        {"$match": {"timestamp": {"$gte": since}}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "failures": {"$sum": {"$cond": [{"$eq": ["$success", False]}, 1, 0]}},
            }
        },
    ]
    rows = await db.llm_usage.aggregate(pipeline).to_list(1)
    if not rows:
        return 0, 0
    return int(rows[0]["failures"]), int(rows[0]["total"])


async def _in_cooldown(db: AsyncIOMotorDatabase, alert_type: str, now: datetime) -> bool:
    cooldown = COOLDOWNS.get(alert_type, timedelta(hours=1))
    recent = await db.llm_alerts.find_one(
        {"type": alert_type, "timestamp": {"$gte": now - cooldown}}
    )
    return recent is not None


# ─── Delivery ───


TELEGRAM_HOST = "api.telegram.org"
TELEGRAM_TEXT_LIMIT = 3500  # Telegram rejects messages over 4096 characters
_TEXT_SKIP_FIELDS = frozenset({"type", "severity", "_id", "delivered"})


def _format_alert_text(alert: dict) -> str:
    """Readable multi-line summary for chat receivers (Telegram)."""
    head = f"VIE {alert.get('severity', 'alert')}: {alert.get('type', 'unknown')}"
    lines = [f"{k}: {v}" for k, v in alert.items() if k not in _TEXT_SKIP_FIELDS]
    return "\n".join([head, *lines])[:TELEGRAM_TEXT_LIMIT]


def _webhook_body(url: str, alert: dict) -> bytes:
    """Telegram's sendMessage needs {chat_id, text}; other receivers get the raw alert.

    Same rule as llm_common.alerts.build_webhook_body (admin has no llm-common dependency).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname == TELEGRAM_HOST:
        chat_id = urllib.parse.parse_qs(parsed.query).get("chat_id", [""])[0]
        if not chat_id:
            # Never log the URL itself: it carries the bot token.
            logger.warning("alert_webhook_telegram_missing_chat_id")
        payload = {"chat_id": chat_id, "text": _format_alert_text(alert)}
        return json.dumps(payload, default=str).encode("utf-8")
    # default=str keeps datetimes/ObjectIds from crashing serialization.
    return json.dumps(alert, default=str).encode("utf-8")


async def _deliver_webhook(alert: dict) -> bool:
    """POST the alert to ALERT_WEBHOOK_URL. Best-effort; False when disabled."""
    url = settings.ALERT_WEBHOOK_URL.strip()
    if not url:
        return False
    content = _webhook_body(url, alert)
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url, content=content, headers={"Content-Type": "application/json"}
        )
    ok = 200 <= response.status_code < 300
    if ok:
        logger.info("alert_webhook_delivered", alert_type=alert.get("type"))
    else:
        logger.warning(
            "alert_webhook_rejected", alert_type=alert.get("type"), status=response.status_code
        )
    return ok


# ─── Orchestration ───


async def evaluate_once(db: AsyncIOMotorDatabase, now: datetime | None = None) -> list[dict]:
    """Evaluate the aggregate thresholds; write + deliver any tripped alerts."""
    now = now or datetime.now(UTC)
    config = await _load_config(db)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    baseline_start = today_start - timedelta(days=SPIKE_BASELINE_DAYS)
    baseline_total, today_usd, (failures, total) = await asyncio.gather(
        _sum_cost(db, baseline_start, today_start),
        _sum_cost(db, today_start, now),
        _failure_counts(db, now - timedelta(minutes=FAILURE_WINDOW_MINUTES)),
    )
    # Fraction of today elapsed, for spike proration. Only ever used as a
    # (clamped) multiplier — no division by it anywhere.
    day_fraction = (now - today_start).total_seconds() / 86400.0

    candidates = [
        build_spike_alert(
            today_usd,
            baseline_total / SPIKE_BASELINE_DAYS,
            float(config["daily_spike_multiplier"]),
            now,
            day_fraction=day_fraction,
        ),
        build_failure_alert(failures, total, float(config["failure_rate_threshold"]), now),
        _backup_stale_candidate(now),
    ]

    written: list[dict] = []
    for alert in candidates:
        if alert is None:
            continue
        if await _in_cooldown(db, alert["type"], now):
            continue
        logger.warning(
            "aggregate_alert_tripped", **{k: v for k, v in alert.items() if k != "timestamp"}
        )
        await db.llm_alerts.insert_one(alert)
        written.append(alert)
        try:
            await _deliver_webhook(alert)
        except Exception as e:  # noqa: BLE001 — delivery must not lose the Mongo write
            logger.warning("alert_webhook_delivery_failed", error=str(e))
    return written


async def alert_evaluator_loop() -> None:
    """Run threshold evaluation in background. Start via asyncio.create_task in lifespan."""
    logger.info("alert_evaluator_started", interval=EVAL_INTERVAL_SECONDS)
    while True:
        try:
            await evaluate_once(get_database())
        except Exception as e:
            logger.error("alert_evaluation_failed", error=str(e))
        await asyncio.sleep(EVAL_INTERVAL_SECONDS)
