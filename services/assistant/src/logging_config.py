"""Structured logging configuration using structlog.

Provides:
- JSON logging in production (LOG_FORMAT=json)
- Human-readable console logging in development (default)
- Request ID binding for tracing
- Contextual logging with automatic timestamps
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import structlog
from llm_common.logging_setup import configure_logging

from src.config import settings

if TYPE_CHECKING:
    from llm_common.middleware import HealthCheckFilter as _HealthCheckFilter
else:
    try:
        from llm_common.middleware import HealthCheckFilter as _HealthCheckFilter
    except ImportError:

        class _HealthCheckFilter(logging.Filter):
            """Fallback filter when llm_common is not installed."""

            def filter(self, record: logging.LogRecord) -> bool:
                msg = record.getMessage()
                return "/health" not in msg


def get_log_level() -> int:
    """Get log level from settings."""
    level = settings.LOG_LEVEL.upper()
    return getattr(logging, level, logging.INFO)


# Libraries quieted to WARNING.
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "litellm",
    "LiteLLM",
    "pymongo",
    "sentence_transformers",
    "qdrant_client",
    "openai",
    "urllib3",
)


def configure_structlog(json_format: bool = False, service_name: str = "vie-assistant") -> None:
    """Configure structlog and stdlib logging for the process (``llm_common.logging_setup``).

    structlog events go through the standard library (D13): one root handler on
    stdout renders structlog's lines and every stdlib record (uvicorn, the
    libraries) through the same chain, and Sentry's logging integration sees
    structlog ERROR events.

    Args:
        json_format: If True, use JSON output. Otherwise, use colored console output.
        service_name: Stamped on every log line as ``service`` so log
            aggregators can filter cross-service traffic by source.
    """
    configure_logging(
        service_name=service_name,
        json_format=json_format,
        level=get_log_level(),
        quiet_loggers=_NOISY_LOGGERS,
        health_filter=_HealthCheckFilter(),
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger.

    Args:
        name: Logger name. If None, uses the caller's module name.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """Bind context variables for the current request.

    These values will be included in all subsequent log messages
    for the current async context.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
