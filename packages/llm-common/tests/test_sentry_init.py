"""Tests for the shared Sentry init helper.

Covers:
- Empty DSN no-ops cleanly.
- ``scrub_event_payload`` strips sensitive headers, drops user emails, redacts
  emails inside arbitrary payload structures, and tolerates ``None`` events.
- ``scrub_event_payload`` promotes the structlog ``request_id`` contextvar to
  a Sentry tag — that's the link that lets a Sentry event jump straight to
  a log line or Langfuse trace.
- ``capture_exception_with_context`` is a clean no-op when the SDK isn't
  initialized.
"""

from __future__ import annotations

import structlog

from llm_common import sentry_init


def _clear_contextvars():
    structlog.contextvars.clear_contextvars()


def test_init_returns_false_when_dsn_empty():
    _clear_contextvars()
    assert sentry_init.init_sentry(dsn="", environment="test", service="vie-test") is False


class _FakeSettings:
    """Minimal duck-typed settings stand-in for ``init_sentry_from_settings``."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_init_from_settings_forwards_all_sentry_fields(monkeypatch):
    """Every SENTRY_* field on the settings object reaches ``init_sentry``."""
    _clear_contextvars()
    captured: dict = {}

    def fake_init(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(sentry_init, "init_sentry", fake_init)
    settings = _FakeSettings(
        SENTRY_DSN="https://x@sentry.io/123",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="sha-abc",
        SENTRY_TRACES_SAMPLE_RATE=0.5,
    )

    result = sentry_init.init_sentry_from_settings(settings, service="vie-test")

    assert result is True
    assert captured["dsn"] == "https://x@sentry.io/123"
    assert captured["environment"] == "staging"
    assert captured["release"] == "sha-abc"
    assert captured["service"] == "vie-test"
    assert captured["traces_sample_rate"] == 0.5
    assert captured["integrate_fastapi"] is True


def test_init_from_settings_falls_back_to_environment_field(monkeypatch):
    """When ``SENTRY_ENVIRONMENT`` is blank, use the generic ``ENVIRONMENT``."""
    _clear_contextvars()
    captured: dict = {}
    monkeypatch.setattr(sentry_init, "init_sentry", lambda **kw: captured.update(kw) or True)

    settings = _FakeSettings(
        SENTRY_DSN="https://x@sentry.io/1",
        SENTRY_ENVIRONMENT=None,
        SENTRY_RELEASE=None,
        SENTRY_TRACES_SAMPLE_RATE=0.0,
        ENVIRONMENT="production",
    )
    sentry_init.init_sentry_from_settings(settings, service="vie-test")

    assert captured["environment"] == "production"


def test_init_from_settings_defaults_environment_when_unset(monkeypatch):
    """No SENTRY_ENVIRONMENT and no ENVIRONMENT → ``default_environment``."""
    _clear_contextvars()
    captured: dict = {}
    monkeypatch.setattr(sentry_init, "init_sentry", lambda **kw: captured.update(kw) or True)

    settings = _FakeSettings(
        SENTRY_DSN="https://x@sentry.io/1",
        SENTRY_ENVIRONMENT=None,
        SENTRY_RELEASE=None,
        SENTRY_TRACES_SAMPLE_RATE=0.0,
    )
    sentry_init.init_sentry_from_settings(settings, service="vie-test")

    assert captured["environment"] == "development"


def test_init_from_settings_honors_explicit_default_environment(monkeypatch):
    _clear_contextvars()
    captured: dict = {}
    monkeypatch.setattr(sentry_init, "init_sentry", lambda **kw: captured.update(kw) or True)

    settings = _FakeSettings(
        SENTRY_DSN="https://x@sentry.io/1",
        SENTRY_ENVIRONMENT=None,
        SENTRY_RELEASE=None,
        SENTRY_TRACES_SAMPLE_RATE=0.0,
    )
    sentry_init.init_sentry_from_settings(
        settings,
        service="vie-test",
        default_environment="ci",
    )

    assert captured["environment"] == "ci"


def test_init_from_settings_propagates_integrate_fastapi_false(monkeypatch):
    """Worker entrypoint disables FastAPI integration."""
    _clear_contextvars()
    captured: dict = {}
    monkeypatch.setattr(sentry_init, "init_sentry", lambda **kw: captured.update(kw) or True)

    settings = _FakeSettings(
        SENTRY_DSN="https://x@sentry.io/1",
        SENTRY_ENVIRONMENT="test",
        SENTRY_RELEASE=None,
        SENTRY_TRACES_SAMPLE_RATE=0.0,
    )
    sentry_init.init_sentry_from_settings(
        settings,
        service="vie-test-worker",
        integrate_fastapi=False,
    )

    assert captured["integrate_fastapi"] is False


def test_init_from_settings_tolerates_missing_optional_fields(monkeypatch):
    """A minimal settings object with only ``SENTRY_DSN`` must not blow up."""
    _clear_contextvars()
    captured: dict = {}
    monkeypatch.setattr(sentry_init, "init_sentry", lambda **kw: captured.update(kw) or True)

    settings = _FakeSettings(SENTRY_DSN="https://x@sentry.io/1")
    sentry_init.init_sentry_from_settings(settings, service="vie-test")

    assert captured["dsn"] == "https://x@sentry.io/1"
    assert captured["release"] is None
    assert captured["traces_sample_rate"] == 0.0
    assert captured["environment"] == "development"


def test_init_from_settings_empty_dsn_short_circuits():
    """Empty DSN bottoms out at ``init_sentry`` which returns False — no SDK call."""
    _clear_contextvars()
    settings = _FakeSettings(SENTRY_DSN="")
    assert sentry_init.init_sentry_from_settings(settings, service="vie-test") is False


def test_scrub_returns_none_for_empty_event():
    _clear_contextvars()
    assert sentry_init.scrub_event_payload({}, None) is None


def test_scrub_redacts_sensitive_headers():
    _clear_contextvars()
    event = {
        "request": {
            "headers": {
                "authorization": "Bearer secret",
                "Cookie": "session=abcdef",
                "X-Internal-Secret": "shh",
                "user-agent": "vie-test/1.0",
            },
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    headers = out["request"]["headers"]
    assert headers["authorization"] == "[Filtered]"
    assert headers["Cookie"] == "[Filtered]"
    assert headers["X-Internal-Secret"] == "[Filtered]"
    # Non-sensitive header survives untouched
    assert headers["user-agent"] == "vie-test/1.0"


def test_scrub_drops_user_email():
    _clear_contextvars()
    event = {"user": {"id": "user-1", "email": "alice@example.com"}}
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert "email" not in out["user"]
    assert out["user"]["id"] == "user-1"


def test_scrub_redacts_emails_inside_payload():
    _clear_contextvars()
    event = {
        "request": {
            "headers": {},
            "data": {
                "note": "reach me at bob@example.com",
                "list": ["carl@example.com", "ok"],
            },
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    data = out["request"]["data"]
    assert "[email]" in data["note"]
    assert "[email]" in data["list"][0]
    assert data["list"][1] == "ok"


def test_scrub_promotes_request_id_contextvar_to_tag():
    _clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id="req-trace-abc12345",
        video_summary_id="vid-678",
    )
    try:
        event: dict = {"message": "kaboom"}
        out = sentry_init.scrub_event_payload(event, None)
        assert out is not None
        assert out["tags"]["requestId"] == "req-trace-abc12345"
        assert out["tags"]["videoSummaryId"] == "vid-678"
    finally:
        _clear_contextvars()


def test_capture_exception_noop_when_sdk_missing(monkeypatch):
    _clear_contextvars()
    monkeypatch.setattr(sentry_init, "_SDK_AVAILABLE", False)
    monkeypatch.setattr(sentry_init, "sentry_sdk", None, raising=False)
    # Must not raise, even though we never installed the SDK.
    sentry_init.capture_exception_with_context(RuntimeError("boom"), service="vie-test")


def test_pipeline_stage_transaction_noop_when_sdk_missing(monkeypatch):
    """The transaction context manager is callable even without an SDK."""
    _clear_contextvars()
    monkeypatch.setattr(sentry_init, "_SDK_AVAILABLE", False)
    monkeypatch.setattr(sentry_init, "sentry_sdk", None, raising=False)

    with sentry_init.pipeline_stage_transaction("extraction", videoId="vid-1") as txn:
        # set_tag/set_data must be safe to call on the null transaction.
        txn.set_tag("custom", "value")
        txn.set_data("payload_size", 1024)


def test_scrub_strips_tokens_from_request_url():
    """WebSocket auth puts the JWT in ?token= — must never reach Sentry."""
    _clear_contextvars()
    event = {
        "request": {
            "headers": {},
            "url": "wss://example.com/ws/123?token=eyJhbGc.payload.sig&foo=bar",
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert out["request"]["url"] == "wss://example.com/ws/123?token=[Filtered]&foo=bar"


def test_scrub_strips_credentials_from_query_string():
    _clear_contextvars()
    event = {"request": {"headers": {}, "query_string": "access_token=secret&page=2"}}
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert out["request"]["query_string"] == "access_token=[Filtered]&page=2"


def test_scrub_drops_user_id_when_it_looks_like_email():
    """`user.id` bypasses scrub_value — guard the email pattern explicitly."""
    _clear_contextvars()
    event = {"user": {"id": "eve@example.com", "username": "eve"}}
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert "id" not in out["user"]
    assert out["user"]["username"] == "eve"


def test_scrub_redacts_emails_in_extra_and_breadcrumbs():
    _clear_contextvars()
    event = {
        "extra": {"note": "forward to ops@example.com"},
        "breadcrumbs": [{"data": {"recipient": "alice@example.com"}}],
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert out["extra"]["note"] == "forward to [email]"
    assert out["breadcrumbs"][0]["data"]["recipient"] == "[email]"


def test_scrub_redacts_emails_and_tokens_in_exception_messages():
    _clear_contextvars()
    event = {
        "exception": {
            "values": [
                {"value": "auth failed at /ws?token=eyJfoo for dan@example.com"},
            ],
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert (
        out["exception"]["values"][0]["value"] == "auth failed at /ws?token=[Filtered] for [email]"
    )


def test_scrub_strips_url_credentials_from_subprocess_breadcrumbs():
    """Sentry records a subprocess's full argv as the breadcrumb message; a
    proxy URL with an IP host slips past the email pattern entirely."""
    _clear_contextvars()
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "subprocess",
                    "message": "yt-dlp --proxy http://user:pass@203.0.113.7:8080 https://youtu.be/x",
                },
            ],
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert (
        out["breadcrumbs"]["values"][0]["message"]
        == "yt-dlp --proxy http://[Filtered]@203.0.113.7:8080 https://youtu.be/x"
    )


def test_scrub_strips_url_username_when_host_is_a_domain():
    """The email pattern alone would redact `pass@host.tld` and keep `user`."""
    _clear_contextvars()
    event = {"extra": {"cmd": "--proxy http://user:pass@proxy.example.com:8080"}}
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert out["extra"]["cmd"] == "--proxy http://[Filtered]@proxy.example.com:8080"


def test_scrub_strips_url_credentials_in_exception_messages():
    _clear_contextvars()
    event = {
        "exception": {
            "values": [{"value": "connect failed: amqp://vie:s3cret@rabbit:5672/ for job 7"}],
        },
    }
    out = sentry_init.scrub_event_payload(event, None)
    assert out is not None
    assert (
        out["exception"]["values"][0]["value"]
        == "connect failed: amqp://[Filtered]@rabbit:5672/ for job 7"
    )


def test_scrub_keeps_urls_without_credentials():
    _clear_contextvars()
    note = "see https://example.com/watch?v=abc&t=10 at 10:30"
    out = sentry_init.scrub_event_payload({"extra": {"note": note}}, None)
    assert out is not None
    assert out["extra"]["note"] == note
