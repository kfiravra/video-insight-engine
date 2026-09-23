"""Webhook delivery for llm_alerts.

Every alert written to the ``llm_alerts`` collection is also POSTed as JSON
to ``ALERT_WEBHOOK_URL`` (Slack-compatible generic webhook, ntfy, or any
HTTP catcher). A Telegram Bot API URL
(``https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>``) gets the
``{chat_id, text}`` shape that endpoint requires instead of the raw document.
Unset/empty URL → no-op, so dev and CI need no receiver.

Delivery is strictly best-effort: failures are logged and swallowed —
alerting must never break the LLM call it is reporting on. Uses stdlib
``urllib`` so llm-common gains no HTTP dependency; callers on an event loop
should wrap :func:`deliver_alert` in ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import structlog

logger = structlog.get_logger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 3.0
TELEGRAM_HOST = "api.telegram.org"
# Telegram rejects messages over 4096 characters.
TELEGRAM_TEXT_LIMIT = 3500
_TEXT_SKIP_FIELDS = frozenset({"type", "severity", "_id", "delivered"})


def format_alert_text(alert: dict) -> str:
    """Readable multi-line summary for chat receivers (Telegram)."""
    head = f"VIE {alert.get('severity', 'alert')}: {alert.get('type', 'unknown')}"
    lines = [f"{k}: {v}" for k, v in alert.items() if k not in _TEXT_SKIP_FIELDS]
    return "\n".join([head, *lines])[:TELEGRAM_TEXT_LIMIT]


def build_webhook_body(url: str, alert: dict) -> bytes:
    """Serialize the alert for the receiver behind ``url``.

    Telegram's ``sendMessage`` needs ``{chat_id, text}`` (chat_id taken from the
    URL query so the env var stays a single URL); every other receiver gets the
    raw alert document. Mirrored in services/admin alert_evaluator.py, which
    does not depend on llm-common.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname == TELEGRAM_HOST:
        chat_id = urllib.parse.parse_qs(parsed.query).get("chat_id", [""])[0]
        if not chat_id:
            # Never log the URL itself: it carries the bot token.
            logger.warning("alert_webhook_telegram_missing_chat_id")
        payload = {"chat_id": chat_id, "text": format_alert_text(alert)}
        return json.dumps(payload, default=str).encode("utf-8")
    # default=str keeps datetimes/ObjectIds from crashing serialization.
    return json.dumps(alert, default=str).encode("utf-8")


def get_webhook_url() -> str:
    """Read the webhook target from the environment (empty → disabled)."""
    return os.environ.get("ALERT_WEBHOOK_URL", "").strip()


def deliver_alert(alert: dict) -> bool:
    """POST the alert document to ALERT_WEBHOOK_URL.

    Returns ``True`` only on a 2xx response. ``False`` for disabled webhook,
    non-2xx, or any transport failure — never raises.
    """
    url = get_webhook_url()
    if not url:
        return False
    # urllib.urlopen happily follows file:// and ftp:// — a misconfigured env
    # var must not turn the alerter into a local-file reader.
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        logger.warning("alert_webhook_invalid_scheme", scheme=scheme)
        return False
    try:
        body = build_webhook_body(url, alert)
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            ok = 200 <= response.status < 300
        if ok:
            logger.info("alert_webhook_delivered", alert_type=alert.get("type"))
        else:
            logger.warning(
                "alert_webhook_rejected",
                alert_type=alert.get("type"),
                status=response.status,
            )
        return ok
    except Exception as e:  # noqa: BLE001 — alerting must never break the caller
        logger.warning("alert_webhook_delivery_failed", error=str(e))
        return False
