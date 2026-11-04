"""The assistant's log lines go through stdlib (observability D13) with the shared JSON shape."""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout

import pytest
import structlog

from src.logging_config import configure_structlog, get_logger


@pytest.fixture(autouse=True)
def _isolate_logging():
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    structlog.contextvars.clear_contextvars()
    root.handlers, root.level = saved_handlers, saved_level


def _lines(emit) -> list[dict]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        configure_structlog(json_format=True, service_name="vie-assistant")
        emit()
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_structlog_line_carries_service_level_timestamp_and_session():
    structlog.contextvars.bind_contextvars(request_id="req-1", session_id="chat-1")

    payload = _lines(lambda: get_logger("src.test").info("assistant_chat_start", history_len=2))[-1]

    assert (payload["event"], payload["service"], payload["level"]) == (
        "assistant_chat_start",
        "vie-assistant",
        "info",
    )
    assert (payload["request_id"], payload["session_id"], payload["history_len"]) == (
        "req-1",
        "chat-1",
        2,
    )
    assert "T" in payload["timestamp"]


def test_stdlib_line_renders_through_the_same_chain():
    payload = _lines(lambda: logging.getLogger("httpx.fake").warning("retrying %s", "x"))[-1]

    assert (payload["event"], payload["service"], payload["logger"]) == (
        "retrying x",
        "vie-assistant",
        "httpx.fake",
    )


def test_structlog_error_reaches_the_root_logger_as_its_event_name():
    seen: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    def emit() -> None:
        logging.getLogger().addHandler(_Collect())
        get_logger("src.test").error("assistant_library_chat_llm_error", error="boom")

    _lines(emit)

    assert [r.getMessage() for r in seen if r.levelno == logging.ERROR] == [
        "assistant_library_chat_llm_error"
    ]
