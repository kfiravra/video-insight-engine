"""vie-admin's log lines (P-13): JSON with LOG_FORMAT=json, console otherwise, through stdlib."""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout

import pytest
import structlog

from src import logging_config
from src.config import settings


@pytest.fixture(autouse=True)
def _isolate_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers, root.level = saved_handlers, saved_level


def _output(monkeypatch: pytest.MonkeyPatch, fmt: str, level: str = "INFO") -> str:
    monkeypatch.setattr(settings, "LOG_FORMAT", fmt)
    monkeypatch.setattr(settings, "LOG_LEVEL", level)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        logging_config.configure_admin_logging()
        structlog.get_logger("src.test").info("health_poll_done", services=5)
        structlog.get_logger("src.test").debug("hidden_at_info")
    return buffer.getvalue()


def test_should_write_json_lines_when_the_format_is_json(monkeypatch: pytest.MonkeyPatch):
    lines = [json.loads(line) for line in _output(monkeypatch, "json").splitlines() if line]

    payload = lines[-1]
    assert (payload["event"], payload["service"], payload["level"], payload["services"]) == (
        "health_poll_done",
        "vie-admin",
        "info",
        5,
    )


def test_should_honour_the_log_level(monkeypatch: pytest.MonkeyPatch):
    assert "hidden_at_info" not in _output(monkeypatch, "json", level="INFO")
    assert "hidden_at_info" in _output(monkeypatch, "json", level="DEBUG")


def test_should_write_console_lines_otherwise(monkeypatch: pytest.MonkeyPatch):
    out = _output(monkeypatch, "console")

    assert "health_poll_done" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.splitlines()[-1])


def test_should_scrub_sentry_events_with_the_vendored_hook():
    from src.sentry_scrub import scrub_event_payload

    event = {"request": {"headers": {"Authorization": "Bearer x"}}, "extra": {"note": "a@b.co"}}

    scrubbed = scrub_event_payload(event, None)

    assert scrubbed["request"]["headers"]["Authorization"] == "[Filtered]"
    assert scrubbed["extra"]["note"] == "[email]"
