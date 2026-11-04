"""Logging for vie-admin: structlog through stdlib, the chain the other services use (P-13).

JSON lines when ``LOG_FORMAT=json`` (prod), the colored console otherwise, at
``LOG_LEVEL``. Every line carries ``service="vie-admin"``, ``level`` and an ISO
``timestamp``; structlog ERROR events reach Sentry's logging integration.
"""

from __future__ import annotations

import logging

from src.config import settings
from src.logging_bridge import configure_logging

# Libraries quieted to WARNING.
_NOISY_LOGGERS = ("httpx", "httpcore", "pymongo", "urllib3")


class _HealthCheckFilter(logging.Filter):
    """Drop uvicorn's access lines for ``GET /health`` (the compose healthcheck polls it)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return '"GET /health' not in record.getMessage()


def log_level() -> int:
    """``LOG_LEVEL`` as a stdlib level (INFO when unknown)."""
    return getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)


def configure_admin_logging() -> None:
    """Configure structlog and the root logger for the admin process."""
    configure_logging(
        service_name="vie-admin",
        json_format=settings.LOG_FORMAT.lower() == "json",
        level=log_level(),
        quiet_loggers=_NOISY_LOGGERS,
        health_filter=_HealthCheckFilter(),
        # Uncached: the suite captures log events with structlog.testing.capture_logs;
        # the admin logs too little for the cache to matter.
        cache_loggers=False,
    )
