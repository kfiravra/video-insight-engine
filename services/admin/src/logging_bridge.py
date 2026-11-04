# Vendored verbatim from packages/llm-common/src/llm_common/logging_setup.py
# (the admin image does not install llm-common, plan A3); tests/test_vendored_copies.py
# fails on any drift. Edit llm-common's file and copy it here in the same commit.
"""structlog through the standard library for the Python services (observability D13, P-14).

Every line — structlog's own and every ``logging.getLogger`` record (the code's,
uvicorn's, the libraries') — goes to one root handler on stdout and renders
through one chain: JSON in prod (``LOG_FORMAT=json``), the colored console
otherwise, with ``service``, ``level``, an ISO ``timestamp`` and the bound
contextvars (``request_id``, ``run_id``, ``session_id`` …) on each line.

Routing structlog through stdlib (instead of ``PrintLoggerFactory``, which
wrote straight to stdout) is what lets Sentry's default ``LoggingIntegration``
see structlog ERROR events. :func:`to_stdlib` hands the record the event name
as its message, so Sentry groups an error by its event (not by a rendered line
whose timestamp differs every time), and puts the exception on the record so
Sentry keeps its stack. The rendered line is unchanged: the formatter renders
the event dict the record carries.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# The record attribute that carries structlog's event dict to the formatter.
# Public on purpose: Sentry's logging integration copies non-underscore record
# attributes into the event's ``extra``, so an error keeps its fields there.
EVENT_DICT_ATTR = "vie_event"
# Marks the root handler this module installs, so a reconfiguration replaces it
# instead of stacking a second one.
_HANDLER_MARK = "_vie_structlog_handler"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def service_processor(service_name: str) -> Processor:
    """Stamp every line with ``service`` (a caller's own ``service=`` wins)."""

    def _processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service_name)
        return event_dict

    return _processor


def shared_processors(service_name: str) -> list[Processor]:
    """The processors both structlog events and stdlib records run through."""
    return [
        service_processor(service_name),
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def to_stdlib(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> tuple[tuple[str], dict[str, Any]]:
    """Last structlog processor: one stdlib record per event (see the module docstring).

    The record's message is the event name; the event dict rides on the record
    (:data:`EVENT_DICT_ATTR`) for :class:`EventDictFormatter`, with the
    ``_logger`` / ``_name`` pair ``ProcessorFormatter`` uses to tell a structlog
    record from a foreign one. ``exc_info`` goes on the record as well.
    """
    kwargs: dict[str, Any] = {
        "extra": {"_logger": logger, "_name": method_name, EVENT_DICT_ATTR: event_dict}
    }
    exc_info = event_dict.get("exc_info")
    if exc_info:
        kwargs["exc_info"] = exc_info
    return (str(event_dict.get("event", "")),), kwargs


class EventDictFormatter(structlog.stdlib.ProcessorFormatter):
    """``ProcessorFormatter`` that renders the event dict :func:`to_stdlib` put on the record."""

    def format(self, record: logging.LogRecord) -> str:
        event_dict = getattr(record, EVENT_DICT_ATTR, None)
        if isinstance(event_dict, dict):
            record = logging.makeLogRecord(record.__dict__)
            record.msg = event_dict
        return super().format(record)


def renderer(json_format: bool) -> Processor:
    """JSON (prod) or the colored console (dev), with plain tracebacks.

    The rich default traceback prints every frame's locals, and a local can hold
    a credential (YOUTUBE_PROXY_URL carries the password).
    """
    if json_format:
        return structlog.processors.JSONRenderer(ensure_ascii=False)
    return structlog.dev.ConsoleRenderer(
        colors=True, exception_formatter=structlog.dev.plain_traceback
    )


def _install_root_handler(formatter: logging.Formatter, level: int) -> None:
    """Route root-logger records to stdout through ``formatter`` (replaces our earlier one)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)


def configure_logging(
    *,
    service_name: str,
    json_format: bool,
    level: int,
    quiet_loggers: Iterable[str] = (),
    health_filter: logging.Filter | None = None,
    cache_loggers: bool = True,
) -> None:
    """Configure structlog and the root logger for one process.

    ``quiet_loggers`` are set to WARNING; ``health_filter`` is added to
    ``uvicorn.access`` (health-check lines). Uvicorn's own handlers, configured
    before the app imports, get the same formatter; they do not propagate, so
    no line is written twice. ``cache_loggers=False`` keeps loggers rebindable
    (``structlog.testing.capture_logs`` cannot patch a cached logger).
    """
    shared = shared_processors(service_name)
    tail: list[Processor] = [structlog.processors.format_exc_info] if json_format else []

    structlog.configure(
        processors=[*shared, to_stdlib],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=cache_loggers,
    )

    formatter = EventDictFormatter(
        foreign_pre_chain=[*shared, structlog.stdlib.add_logger_name],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *tail,
            renderer(json_format),
        ],
    )
    _install_root_handler(formatter, level)
    for name in UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)

    if health_filter is not None:
        logging.getLogger("uvicorn.access").addFilter(health_filter)
    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
