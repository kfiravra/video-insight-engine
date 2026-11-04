"""llm_common.logging_setup: structlog through stdlib (observability D13)."""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout

import pytest
import structlog

from llm_common.logging_setup import EVENT_DICT_ATTR, configure_logging, to_stdlib


@pytest.fixture(autouse=True)
def _isolate_logging():
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    structlog.contextvars.clear_contextvars()
    root.handlers, root.level = saved_handlers, saved_level
    structlog.reset_defaults()


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _configure(json_format: bool = True, level: int = logging.INFO) -> None:
    configure_logging(service_name="svc-test", json_format=json_format, level=level)


def test_should_hand_stdlib_the_event_name_as_message_and_the_event_dict_on_the_record():
    args, kwargs = to_stdlib(None, "error", {"event": "job_failed", "exc_info": True, "job": 1})

    assert args == ("job_failed",)
    assert kwargs["exc_info"] is True
    assert kwargs["extra"][EVENT_DICT_ATTR]["job"] == 1


def test_should_leave_exc_info_off_the_record_when_the_event_has_none():
    _, kwargs = to_stdlib(None, "info", {"event": "ok"})

    assert "exc_info" not in kwargs


def test_should_render_one_json_line_with_the_bound_context():
    structlog.contextvars.bind_contextvars(run_id="run-1")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _configure()
        structlog.get_logger("a.b").info("pipeline_run_started", attempt=1)

    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assert len(lines) == 1
    assert (lines[0]["event"], lines[0]["service"], lines[0]["run_id"], lines[0]["attempt"]) == (
        "pipeline_run_started",
        "svc-test",
        "run-1",
        1,
    )


def test_should_give_the_root_logger_an_error_record_with_its_exception():
    collect = _Collect()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _configure()
        logging.getLogger().addHandler(collect)
        try:
            raise KeyError("missing")
        except KeyError:
            structlog.get_logger("a.b").exception("lookup_failed")

    record = collect.records[-1]
    assert (record.getMessage(), record.levelname) == ("lookup_failed", "ERROR")
    assert record.exc_info is not None and record.exc_info[0] is KeyError
    assert "KeyError" in json.loads(buffer.getvalue().splitlines()[-1])["exception"]


def test_should_drop_events_below_the_level():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _configure(level=logging.WARNING)
        structlog.get_logger("a.b").info("quiet")
        structlog.get_logger("a.b").warning("loud")

    assert "quiet" not in buffer.getvalue()
    assert "loud" in buffer.getvalue()


def test_should_quiet_the_named_loggers():
    configure_logging(
        service_name="s", json_format=True, level=logging.INFO, quiet_loggers=["noisy.lib"]
    )

    assert logging.getLogger("noisy.lib").level == logging.WARNING
