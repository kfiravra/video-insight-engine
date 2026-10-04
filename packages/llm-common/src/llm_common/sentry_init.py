"""Shared Sentry SDK initialization for VIE Python services.

Design contract (mirrors the API's plugin):

- Empty DSN -> ``init_sentry`` no-ops. Lets dev/CI environments run without
  provisioning a Sentry project.
- All payloads pass through :func:`scrub_event_payload` before transmission to
  Sentry SaaS. Authorization headers, cookies, internal-service secrets, and
  user emails are redacted; structlog ``request_id`` contextvar values are
  promoted to event tags so cross-system lookups stay cheap.
- Helpers must NEVER raise. Observability outages must never affect the
  pipeline.

The SDK is an optional runtime dep (CI images can be trimmed) — the module
imports it lazily and treats ``ImportError`` as "Sentry disabled".
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import structlog

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised at runtime, not unit tests
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    _SDK_AVAILABLE = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]
    FastApiIntegration = None  # type: ignore[assignment,misc]
    StarletteIntegration = None  # type: ignore[assignment,misc]
    _SDK_AVAILABLE = False


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
    except Exception as exc:  # noqa: BLE001 - never crash the SDK pipeline
        logger.debug("sentry_before_send_scrub_failed: %s", exc)

    return event


def init_sentry(
    *,
    dsn: str,
    environment: str,
    service: str,
    release: str | None = None,
    traces_sample_rate: float = 0.0,
    integrate_fastapi: bool = True,
) -> bool:
    """Boot the Sentry SDK. Returns ``True`` when actually initialized.

    No-ops when ``dsn`` is empty OR when the SDK is not installed — so CI
    images that don't ship sentry-sdk import cleanly.
    """
    if not dsn:
        return False
    if not _SDK_AVAILABLE:
        logger.info("sentry_sdk not installed - observability disabled")
        return False

    integrations: list[Any] = []
    if integrate_fastapi and FastApiIntegration is not None and StarletteIntegration is not None:
        integrations.extend([StarletteIntegration(), FastApiIntegration()])

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            before_send=scrub_event_payload,
            integrations=integrations,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", service)
        logger.info(
            "sentry_initialized environment=%s release=%s service=%s",
            environment,
            release,
            service,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - boot must not depend on observability
        logger.warning("sentry_init_failed: %s", exc)
        return False


def init_sentry_from_settings(
    settings: Any,
    *,
    service: str,
    integrate_fastapi: bool = True,
    default_environment: str = "development",
) -> bool:
    """Boot Sentry from a Pydantic ``Settings`` object.

    Centralizes the DSN/environment/release/sample-rate plumbing so the three
    callers (vie-summarizer FastAPI, vie-summarizer worker, vie-assistant) all
    agree on the env-var contract. Tolerant to settings-shape variance:
    summarizer carries a generic ``ENVIRONMENT`` field that the assistant
    doesn't, so we fall through ``SENTRY_ENVIRONMENT`` → ``ENVIRONMENT`` →
    ``default_environment``.

    Returns the underlying :func:`init_sentry` result — ``True`` only when the
    SDK was actually started (empty DSN or missing sentry-sdk → ``False``).
    """
    environment = (
        getattr(settings, "SENTRY_ENVIRONMENT", None)
        or getattr(settings, "ENVIRONMENT", None)
        or default_environment
    )
    return init_sentry(
        dsn=getattr(settings, "SENTRY_DSN", "") or "",
        environment=environment,
        release=getattr(settings, "SENTRY_RELEASE", None),
        service=service,
        traces_sample_rate=getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0) or 0.0,
        integrate_fastapi=integrate_fastapi,
    )


class StageTransaction(Protocol):
    """Structural type for the pipeline-stage transaction context manager.

    Satisfied by both ``_NullTransaction`` (used when the SDK is absent) and
    the real ``sentry_sdk.tracing.Transaction``. Lets callers annotate
    ``with pipeline_stage_transaction(...) as txn:`` without importing
    sentry-sdk types from a dependency that may not be installed.

    Parameters are positional-only so the two implementations are free to
    use different argument names (``_key``/``_value`` vs ``key``/``value``)
    without breaking structural conformance.
    """

    def __enter__(self) -> StageTransaction: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
        /,
    ) -> bool | None: ...
    def set_tag(self, key: str, value: Any, /) -> None: ...
    def set_data(self, key: str, value: Any, /) -> None: ...


class _NullTransaction:
    """No-op stand-in returned when Sentry is unavailable.

    Lets callers write ``with pipeline_stage_transaction(...):`` unconditionally
    — no DSN, no SDK, no problem. Set methods become cheap pass-throughs.
    """

    def __enter__(self) -> _NullTransaction:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        return False

    def set_tag(self, _key: str, _value: Any) -> None:
        pass

    def set_data(self, _key: str, _value: Any) -> None:
        pass


def pipeline_stage_transaction(stage: str, **tags: str | None) -> StageTransaction:
    """Open a Sentry performance transaction for a pipeline stage.

    Use as a context manager around a pipeline phase:

        with pipeline_stage_transaction("extraction", videoSummaryId=vid):
            await run_extraction(ctx)

    Sampling is controlled by ``SENTRY_TRACES_SAMPLE_RATE`` on the init call.
    When the SDK is unavailable the helper returns a no-op so callers don't
    have to special-case observability.
    """
    if not _SDK_AVAILABLE or sentry_sdk is None:
        return _NullTransaction()
    try:
        transaction = sentry_sdk.start_transaction(op="pipeline.stage", name=stage)
        for key, value in tags.items():
            if value is not None:
                transaction.set_tag(key, value)
        # Mirror the structlog request_id contextvar so the transaction can be
        # filtered alongside Sentry events emitted during the same job.
        bound = structlog.contextvars.get_contextvars()
        if isinstance(bound, dict):
            rid = bound.get("request_id")
            if isinstance(rid, str):
                transaction.set_tag("requestId", rid)
        return transaction
    except Exception as exc:  # noqa: BLE001
        logger.debug("sentry_start_transaction_failed: %s", exc)
        return _NullTransaction()


def capture_exception_with_context(exc: BaseException, **tags: str | None) -> None:
    """Capture an exception with extra tags. No-op when SDK is unavailable."""
    if not _SDK_AVAILABLE or sentry_sdk is None:
        return
    try:
        with sentry_sdk.push_scope() as scope:
            for key, value in tags.items():
                if value is not None:
                    scope.set_tag(key, value)
            # Mirror structlog contextvars onto the scope so the event carries
            # request_id/videoSummaryId without callers having to thread them.
            bound = structlog.contextvars.get_contextvars()
            if isinstance(bound, dict):
                rid = bound.get("request_id")
                if isinstance(rid, str):
                    scope.set_tag("requestId", rid)
            sentry_sdk.capture_exception(exc)
    except Exception as scope_exc:  # noqa: BLE001
        logger.debug("sentry_capture_failed: %s", scope_exc)
