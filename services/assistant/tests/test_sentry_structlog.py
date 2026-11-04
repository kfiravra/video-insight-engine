"""D13: a structlog ERROR in the assistant reaches Sentry (in-process transport, no DSN needed)."""

from __future__ import annotations

import io
import logging
from contextlib import redirect_stdout

import pytest
import sentry_sdk
import structlog
from llm_common import sentry_init
from sentry_sdk.transport import Transport

from src.logging_config import configure_structlog, get_logger


class _CaptureTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope) -> None:  # noqa: ANN001 — sentry's Envelope
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def sentry_events(monkeypatch: pytest.MonkeyPatch):
    """The real `init_sentry` (scrub included), its envelopes kept in memory."""
    transport = _CaptureTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(
        sentry_init.sentry_sdk, "init", lambda **kw: real_init(transport=transport, **kw)
    )
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    structlog.contextvars.clear_contextvars()
    assert sentry_init.init_sentry(
        dsn="https://public@o0.ingest.sentry.io/0", environment="test", service="vie-assistant"
    )
    yield transport.events
    sentry_sdk.get_client().close()
    real_init()  # back to a disabled client
    structlog.contextvars.clear_contextvars()
    root.handlers, root.level = saved_handlers, saved_level


def test_should_send_a_structlog_error_to_sentry_named_by_its_event(sentry_events):
    with redirect_stdout(io.StringIO()):
        configure_structlog(json_format=True, service_name="vie-assistant")
        structlog.contextvars.bind_contextvars(request_id="req-12345678", session_id="chat-sess-1")
        get_logger("src.services.assistant").error("assistant_library_chat_llm_error", error="boom")
    sentry_sdk.flush()

    event = next(e for e in sentry_events if e.get("level") == "error")
    assert event["logentry"]["message"] == "assistant_library_chat_llm_error"
    assert (event["tags"]["requestId"], event["tags"]["sessionId"]) == (
        "req-12345678",
        "chat-sess-1",
    )


def test_should_send_the_stack_of_a_logged_exception(sentry_events):
    with redirect_stdout(io.StringIO()):
        configure_structlog(json_format=True, service_name="vie-assistant")
        try:
            raise RuntimeError("provider down")
        except RuntimeError:
            get_logger("src.services.assistant").exception("assistant_chat_failed")
    sentry_sdk.flush()

    event = next(e for e in sentry_events if e.get("level") == "error")
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"


def test_should_keep_the_event_fields_out_of_sentry(sentry_events):
    with redirect_stdout(io.StringIO()):
        configure_structlog(json_format=True, service_name="vie-assistant")
        get_logger("src.tools.navigator").error(
            "navigator_search_failed", query="my private search"
        )
    sentry_sdk.flush()

    event = next(e for e in sentry_events if e.get("level") == "error")
    assert "my private search" not in repr(event)


def test_should_leave_the_unhandled_error_line_to_the_asgi_integration(sentry_events):
    with redirect_stdout(io.StringIO()):
        configure_structlog(json_format=True, service_name="vie-assistant")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            get_logger(sentry_init.UNHANDLED_ERROR_LOGGER).exception("unhandled_error")
    sentry_sdk.flush()

    assert [e for e in sentry_events if e.get("level") == "error"] == []
