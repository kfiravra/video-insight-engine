"""Sentry ``before_send`` scrub for vie-admin, vendored from llm-common (plan A3, P-13).

The admin image does not install llm-common, so the block between the
``scrub`` markers below is a verbatim copy of the one in
``packages/llm-common/src/llm_common/sentry_init.py``;
``tests/test_vendored_copies.py`` compares the two and fails on any drift.
Edit llm-common's copy and paste it here in the same commit.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

logger = logging.getLogger(__name__)

# ─── scrub: BEGIN (vendored verbatim into services/admin/src/sentry_scrub.py; the
# admin's identity test fails on any drift, so change both in one commit) ───
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-internal-secret",
        "x-admin-key",
        "x-csrf-token",
        "x-api-key",
        "proxy-authorization",
    }
)

_EMAIL_PATTERN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.IGNORECASE)

# Query-string parameter names that may carry credentials. The WebSocket auth
# endpoint accepts `?token=<JWT>` so any captured event from that path would
# otherwise mirror a live JWT to Sentry SaaS.
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "code",
        "state",
        "api_key",
        "apikey",
        "key",
        "password",
        "secret",
    }
)

_QUERY_REDACT_PATTERN = re.compile(
    # Value runs until `&`, `#`, whitespace, or quote. Without the whitespace
    # guard the regex would keep eating past the URL into surrounding prose
    # in error messages like `auth failed at /ws?token=X for alice@...`.
    r"(?P<sep>[?&])(?P<key>" + "|".join(_SENSITIVE_QUERY_KEYS) + r")=[^&#\s\"']*",
    re.IGNORECASE,
)


# `scheme://user:pass@host` — proxy, broker and database URLs carry their
# credentials in the userinfo part. Subprocess breadcrumbs (full argv) and
# exception text can embed such a URL; the email pattern only catches it by
# accident, and not at all when the host is an IP.
_URL_USERINFO_PATTERN = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.\-]*://)[^/\s@\"'?#]+@",
    re.IGNORECASE,
)


def _redact_emails(text: str) -> str:
    return _EMAIL_PATTERN.sub("[email]", text)


def _redact_url_userinfo(text: str) -> str:
    """Strip `user:pass@` from any URL embedded in the text."""
    return _URL_USERINFO_PATTERN.sub(lambda m: f"{m.group('scheme')}[Filtered]@", text)


def _redact_url(url: str) -> str:
    """Strip credentials from URL query strings before they ship to Sentry."""
    return _QUERY_REDACT_PATTERN.sub(lambda m: f"{m.group('sep')}{m.group('key')}=[Filtered]", url)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        # Userinfo FIRST: the email pattern would otherwise eat `pass@host.tld`
        # and leave the username behind.
        return _redact_emails(_redact_url_userinfo(value))
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(val) for key, val in value.items()}
    return value


def _scrub_user_identifier(value: Any) -> Any:
    """Drop the value if it embeds an email. Used for user.id / user.username
    fields, which Sentry doesn't pass through the request-data scrubber."""
    if isinstance(value, str) and _EMAIL_PATTERN.search(value):
        return None
    return value


def scrub_event_payload(
    event: dict[str, Any],
    _hint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """``before_send`` hook. Redacts PII and promotes contextvars to tags.

    Returning ``None`` drops the event entirely; returning the (mutated) event
    forwards it to Sentry. The function is total (never raises) so an SDK
    failure inside Sentry's pipeline can't take down the host process.
    """
    if not event:
        return None

    try:
        request = event.get("request")
        if isinstance(request, dict):
            headers = request.get("headers")
            if isinstance(headers, dict):
                for key in list(headers.keys()):
                    if key.lower() in _SENSITIVE_HEADERS:
                        headers[key] = "[Filtered]"

            data = request.get("data")
            if data is not None:
                request["data"] = _scrub_value(data)

            # WebSocket auth uses `?token=<JWT>` — query strings in the
            # captured URL would otherwise mirror live credentials to Sentry.
            url = request.get("url")
            if isinstance(url, str):
                request["url"] = _redact_url(url)
            query_string = request.get("query_string")
            if isinstance(query_string, str):
                request["query_string"] = _redact_url(f"?{query_string}").lstrip("?")

        user = event.get("user")
        if isinstance(user, dict):
            user.pop("email", None)
            # `user.id` and `user.username` skip the request-data scrubber.
            # Drop them if they embed an email (some collections key by it).
            for field in ("id", "username"):
                if field in user:
                    safe = _scrub_user_identifier(user[field])
                    if safe is None:
                        user.pop(field, None)
                    else:
                        user[field] = safe

        # Recurse the scrubber over the rest of the envelope so emails or
        # tokens that landed in extra/contexts/breadcrumbs/exception payloads
        # via a logger call don't leak through.
        for key in ("extra", "contexts", "breadcrumbs"):
            if key in event and event[key] is not None:
                event[key] = _scrub_value(event[key])
        exception = event.get("exception")
        if isinstance(exception, dict):
            values = exception.get("values")
            if isinstance(values, list):
                for entry in values:
                    if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                        # URL-redact FIRST: the token-redaction regex consumes
                        # until `&` or `#`, so a preceding `[email]` token
                        # would be swallowed by the URL match.
                        entry["value"] = _redact_emails(
                            _redact_url(_redact_url_userinfo(entry["value"]))
                        )

        # Promote the structlog request_id contextvar to a Sentry tag so all
        # events tied to a worker job share the cross-system correlation id.
        bound = structlog.contextvars.get_contextvars()
        if isinstance(bound, dict):
            tags = event.setdefault("tags", {})
            if isinstance(tags, dict):
                request_id = bound.get("request_id")
                if isinstance(request_id, str) and request_id:
                    tags.setdefault("requestId", request_id)
                video_summary_id = bound.get("video_summary_id")
                if isinstance(video_summary_id, str) and video_summary_id:
                    tags.setdefault("videoSummaryId", video_summary_id)
                # D13: the pipeline attempt and the chat session, as vie-api tags them.
                run_id = bound.get("run_id")
                if isinstance(run_id, str) and run_id:
                    tags.setdefault("runId", run_id)
                session_id = bound.get("session_id")
                if isinstance(session_id, str) and session_id:
                    tags.setdefault("sessionId", session_id)
    except Exception as exc:  # noqa: BLE001 - never crash the SDK pipeline
        logger.debug("sentry_before_send_scrub_failed: %s", exc)

    return event


# ─── scrub: END ───
