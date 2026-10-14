"""Structured logging configuration using structlog.

Provides:
- JSON logging in production (LOG_FORMAT=json)
- Human-readable console logging in development (default)
- Request ID binding for tracing
- Contextual logging with automatic timestamps — structlog's own lines and every
  standard-library line (the code's ``logging.getLogger``, uvicorn, libraries)
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from src.config import settings

try:
    from llm_common.middleware import HealthCheckFilter as _HealthCheckFilter
except ImportError:

    class _HealthCheckFilter(logging.Filter):  # type: ignore[no-redef]
        """Fallback filter when llm_common is not installed."""

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return "/health" not in msg


def get_log_level() -> int:
    """Get log level from settings."""
    level = settings.LOG_LEVEL.upper()
    return getattr(logging, level, logging.INFO)


def _add_service_processor(service_name: str) -> Processor:
    """Return a structlog processor that stamps every log line with `service`.

    Keeps the JSON shape consistent across services so a log aggregator can
    filter / group by `service` without each log line having to mention it
    explicitly.
    """
    from structlog.types import EventDict

    def _processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service_name)
        return event_dict

    return _processor


# Marks the root handler this module installs, so a reconfiguration replaces it
# instead of stacking a second one.
_HANDLER_MARK = "_vie_structlog_handler"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# Libraries quieted to WARNING. filelock/huggingface: HF model loading
# (SentenceTransformer) logs every lock acquire/release at DEBUG.
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "litellm",
    "LiteLLM",
    "pymongo",
    "botocore",
    "boto3",
    "s3transfer",
    "aiobotocore",
    "yt_dlp",
    "urllib3",
    "openai",
    "openai._base_client",
    "google",
    "google.auth",
    "google.auth.transport",
    "filelock",
    "huggingface_hub",
    "transformers",
    "sentence_transformers",
)


def _install_stdlib_handler(formatter: logging.Formatter, level: int) -> None:
    """Route root-logger records to stdout through ``formatter`` (replaces our earlier one)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)


def _format_uvicorn_handlers(formatter: logging.Formatter) -> None:
    """Give uvicorn's own handlers (configured before the app imports) the same format."""
    for name in _UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)


def _renderer(json_format: bool) -> Processor:
    """JSON (prod) or colored console (dev) — the last processor of both chains."""
    if json_format:
        return structlog.processors.JSONRenderer(ensure_ascii=False)
    # Plain tracebacks: the rich default prints every frame's locals, and a
    # local can hold a credential (YOUTUBE_PROXY_URL carries the password).
    return structlog.dev.ConsoleRenderer(
        colors=True, exception_formatter=structlog.dev.plain_traceback
    )


def configure_structlog(json_format: bool = False, service_name: str = "vie-summarizer") -> None:
    """
    Configure structlog for the application.

    Args:
        json_format: If True, use JSON output. Otherwise, use colored console output.
        service_name: Stamped on every log line as ``service``. Override to
            ``vie-summarizer-worker`` from the worker entrypoint so log
            consumers can tell the SSE-driving FastAPI process apart from the
            queue-driven worker.
    """
    # Shared processors for all formats
    shared_processors: list[Processor] = [
        _add_service_processor(service_name),
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer = _renderer(json_format)
    tail: list[Processor] = [structlog.processors.format_exc_info] if json_format else []

    structlog.configure(
        processors=[*shared_processors, *tail, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(get_log_level()),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Standard-library records — most of the code logs through
    # ``logging.getLogger(__name__)``, and so do uvicorn and the libraries —
    # render through the same chain: timestamp, level, service and the bound
    # request context on every line (hotfix 4.1: they used to print the bare
    # message, so a worker log had no time on most lines).
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared_processors, structlog.stdlib.add_logger_name],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *tail, renderer],
    )
    _install_stdlib_handler(formatter, get_log_level())
    _format_uvicorn_handlers(formatter)

    # Suppress uvicorn access logs for health checks
    logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger.

    Args:
        name: Logger name. If None, uses the caller's module name.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """
    Bind context variables for the current request.

    Common usage:
        bind_request_context(request_id="abc123", user_id="user456")

    These values will be included in all subsequent log messages
    for the current async context.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
