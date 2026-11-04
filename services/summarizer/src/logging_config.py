"""Structured logging configuration using structlog (through stdlib, ``llm_common.logging_setup``).

Provides:
- JSON logging in production (LOG_FORMAT=json)
- Human-readable console logging in development (default)
- Request ID binding for tracing
- Contextual logging with automatic timestamps — structlog's own lines and every
  standard-library line (the code's ``logging.getLogger``, uvicorn, libraries)
"""

import logging
from typing import Any

import structlog
from llm_common.logging_setup import configure_logging

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


# The ``service`` every log line of this process carries; set by configure_structlog.
_SERVICE_NAME = "vie-summarizer"


def configured_service_name() -> str:
    """The process's log service name: ``vie-summarizer`` or ``vie-summarizer-worker``."""
    return _SERVICE_NAME


def configure_structlog(json_format: bool = False, service_name: str = "vie-summarizer") -> None:
    """
    Configure structlog and stdlib logging for the process (``llm_common.logging_setup``).

    structlog events go through the standard library (D13), so Sentry's logging
    integration sees ERROR events, and every line — structlog's, the code's
    ``logging.getLogger`` records, uvicorn's and the libraries' — renders through
    one chain: timestamp, level, service and the bound request context (hotfix
    4.1: stdlib lines used to print the bare message).

    Args:
        json_format: If True, use JSON output. Otherwise, use colored console output.
        service_name: Stamped on every log line as ``service``. Override to
            ``vie-summarizer-worker`` from the worker entrypoint so log
            consumers can tell the SSE-driving FastAPI process apart from the
            queue-driven worker.
    """
    global _SERVICE_NAME
    _SERVICE_NAME = service_name
    configure_logging(
        service_name=service_name,
        json_format=json_format,
        level=get_log_level(),
        quiet_loggers=_NOISY_LOGGERS,
        health_filter=_HealthCheckFilter(),
    )


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
