"""The structured log line shape is a cross-system contract.

Every JSON log line must include ``service``, the optional contextvars that
the worker binds (``request_id``, ``video_summary_id``, ``user_id``), an ISO
``timestamp``, and a ``level``. The Phase 6 ``find-request.sh`` script greps
these fields, so changes here break support tooling — keep the schema honest.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
from contextlib import redirect_stdout

import pytest
import structlog

from src.logging_config import configure_structlog, get_logger


@pytest.fixture(autouse=True)
def _isolate_structlog():
    structlog.contextvars.clear_contextvars()
    # Stash + restore stdlib handlers — configure_structlog installs a root handler.
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    structlog.contextvars.clear_contextvars()
    root.handlers = saved_handlers
    root.level = saved_level


def _emit_and_capture(service_name: str, msg: str, **bound) -> dict:
    configure_structlog(json_format=True, service_name=service_name)
    logger = get_logger("test")

    if bound:
        structlog.contextvars.bind_contextvars(**bound)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        logger.info(msg)
        sys.stdout.flush()
    line = buffer.getvalue().strip().splitlines()[-1]
    return json.loads(line)


def test_log_line_includes_service_field():
    payload = _emit_and_capture("vie-summarizer-test", "ping")
    assert payload["service"] == "vie-summarizer-test"


def test_log_line_includes_request_id_when_bound():
    payload = _emit_and_capture(
        "vie-summarizer-test",
        "ping",
        request_id="req-trace-aaaa1111",
    )
    assert payload["request_id"] == "req-trace-aaaa1111"


def test_log_line_carries_video_summary_id_when_bound():
    payload = _emit_and_capture(
        "vie-summarizer-test",
        "ping",
        request_id="req-1",
        video_summary_id="vid-42",
    )
    assert payload["video_summary_id"] == "vid-42"


def test_log_line_has_iso_timestamp_and_level():
    payload = _emit_and_capture("vie-summarizer-test", "ping")
    assert payload["level"] == "info"
    assert "timestamp" in payload
    # ISO 8601 / RFC 3339 — `2026-05-20T...` shape (no whitespace).
    assert "T" in payload["timestamp"]


def test_log_line_explicit_service_field_is_not_overwritten():
    # If a caller passes `service=...` in the log call, structlog should keep
    # the caller's value — the processor uses setdefault to avoid clobbering.
    configure_structlog(json_format=True, service_name="vie-summarizer-test")
    logger = get_logger("test")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        logger.info("ping", service="custom-service")
        sys.stdout.flush()
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert payload["service"] == "custom-service"


# ─── Standard-library lines (hotfix 4.1 (4): timestamps on every log line) ───


def _stdlib_line(json_format: bool, name: str = "src.services.video.youtube") -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        configure_structlog(json_format=json_format, service_name="vie-summarizer-worker")
        logging.getLogger(name).warning(
            "Metadata extract for %s blocked on proxy exit %d/%d", "vid", 1, 3
        )
    return buffer.getvalue().strip().splitlines()[-1]


def test_stdlib_json_line_carries_timestamp_level_service_and_logger():
    payload = json.loads(_stdlib_line(json_format=True))

    assert (payload["level"], payload["service"], payload["logger"]) == (
        "warning",
        "vie-summarizer-worker",
        "src.services.video.youtube",
    )
    assert payload["event"] == "Metadata extract for vid blocked on proxy exit 1/3"
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", payload["timestamp"])


def test_stdlib_console_line_starts_with_an_iso_timestamp():
    line = _stdlib_line(json_format=False)

    assert re.match(r"^(\x1b\[[0-9;]*m)*\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", line)


def test_stdlib_line_carries_the_bound_request_context():
    structlog.contextvars.bind_contextvars(video_summary_id="vid-42")

    payload = json.loads(_stdlib_line(json_format=True))

    assert payload["video_summary_id"] == "vid-42"


def test_uvicorn_lines_carry_a_timestamp():
    access = logging.getLogger("uvicorn.access")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    access.addHandler(handler)
    try:
        configure_structlog(json_format=True, service_name="vie-summarizer")
        access.info('%s - "%s %s HTTP/%s" %d', "172.19.0.1:4669", "GET", "/docs", "1.1", 200)
    finally:
        access.removeHandler(handler)

    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert (payload["event"], "T" in payload["timestamp"]) == (
        '172.19.0.1:4669 - "GET /docs HTTP/1.1" 200',
        True,
    )


def test_stdlib_exception_in_json_carries_the_traceback():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        configure_structlog(json_format=True, service_name="vie-summarizer")
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("src.test").exception("failed")
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])

    assert "ValueError: boom" in payload["exception"]


def test_console_traceback_never_prints_frame_locals():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        configure_structlog(json_format=False, service_name="vie-summarizer")
        proxy_url = "http://user-2:SECRETPASS@p.webshare.io:80"  # noqa: F841 — a frame local
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("src.test").exception("failed")

    assert "SECRETPASS" not in buffer.getvalue()


def test_reconfiguring_keeps_a_root_handler_it_did_not_install():
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)

    configure_structlog(json_format=True, service_name="vie-summarizer")

    assert foreign in logging.getLogger().handlers


def test_reconfiguring_does_not_stack_root_handlers():
    configure_structlog(json_format=True, service_name="vie-summarizer")
    first = len(logging.getLogger().handlers)

    configure_structlog(json_format=True, service_name="vie-summarizer")

    assert len(logging.getLogger().handlers) == first
