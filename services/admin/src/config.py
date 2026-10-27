"""Configuration settings for vie-admin service."""

from __future__ import annotations

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Well-known dev values (dev compose fallbacks + .env templates). Production
# must never run on them — anyone who has read this repo knows them.
_DEFAULT_ADMIN_API_KEY = "dev-admin-key-change-me"
_DEFAULT_ADMIN_SESSION_SECRET = "dev-admin-session-secret-change-me"

# Environments where dev defaults are tolerated (warn only). Any other value —
# production, staging, or an unrecognized name — fails startup. Same set as
# the assistant's validate_internal_secret.
_DEV_ENVS = frozenset({"", "development", "dev", "test", "local"})


class Settings(BaseSettings):
    """Admin service settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env")

    ENVIRONMENT: str = ""  # development, test, staging, production
    MONGODB_URI: str = "mongodb://vie-mongodb:27017/video-insight-engine"
    # Machine credential: vie-api X-Admin-Key + raw Bearer key on vie-admin.
    ADMIN_API_KEY: str = Field(repr=False)
    # HMAC key for admin session tokens only. Kept apart from ADMIN_API_KEY so
    # a holder of the machine key cannot mint a session for an arbitrary
    # email; rotating it logs every admin out without touching vie-api.
    ADMIN_SESSION_SECRET: str = Field(default=_DEFAULT_ADMIN_SESSION_SECRET, repr=False)

    # Service URLs for health polling
    VIE_API_URL: str = "http://vie-api:3000"
    VIE_SUMMARIZER_URL: str = "http://vie-summarizer:8000"
    VIE_ASSISTANT_URL: str = "http://vie-assistant:8001"
    # Qdrant readiness probe target (GET /readyz). Redis/RabbitMQ are observed
    # through vie-api's /ready (what matters is whether the API can reach them).
    QDRANT_URL: str = "http://vie-qdrant:6333"

    # Alert thresholds
    ALERT_COST_THRESHOLD_USD: float = 0.50
    # Global spend alarm: daily_global_cost fires once per UTC day when the
    # total across all users reaches this many USD. Alarm only — nothing is
    # blocked. <= 0 disables it.
    ALERT_DAILY_COST_THRESHOLD_USD: float = 20.0

    # Webhook receiving llm_alerts JSON POSTs (empty → delivery disabled).
    # Same env var the summarizer/assistant/worker read via llm-common.
    ALERT_WEBHOOK_URL: str = ""

    # Backup dead-man's switch (alert_evaluator.py) — directory holding
    # scripts/backup.sh output (compose mounts the host backups/ dir here,
    # read-only). Missing/unmounted dir → the staleness check is skipped.
    BACKUP_DIR: str = "./backups"
    # 26h = daily cron (03:30) + generous slack for a slow run before alerting.
    BACKUP_MAX_AGE_HOURS: float = 26.0

    # Langfuse — used only to build "Open in Langfuse" deep-links for pipeline
    # runs. The project id is resolved from the keys at runtime when left blank.
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PROJECT_ID: str = ""

    # Sentry — empty DSN → init no-ops (dev/CI run without a Sentry project).
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str | None = None
    SENTRY_RELEASE: str | None = None  # Usually the deploy SHA.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    # Logging
    LOG_LEVEL: str = "INFO"


settings = Settings()


def _secret_problems() -> list[str]:
    """Describe each unsafe admin secret without ever including its value."""
    problems: list[str] = []
    if settings.ADMIN_API_KEY in ("", _DEFAULT_ADMIN_API_KEY):
        problems.append("ADMIN_API_KEY is empty or the well-known dev default")
    if settings.ADMIN_SESSION_SECRET in ("", _DEFAULT_ADMIN_SESSION_SECRET):
        problems.append("ADMIN_SESSION_SECRET is empty or the well-known dev default")
    if settings.ADMIN_SESSION_SECRET == settings.ADMIN_API_KEY:
        problems.append(
            "ADMIN_SESSION_SECRET equals ADMIN_API_KEY — the machine key could mint admin sessions"
        )
    return problems


def validate_secrets() -> None:
    """Fail startup on unsafe admin secrets outside development.

    Called from the application lifespan (not import time) so test imports
    keep working. Unknown ``ENVIRONMENT`` names count as production — a typo
    must fail closed, not silently run on dev defaults.

    Raises:
        ValueError: An unsafe secret with a non-development ``ENVIRONMENT``.
    """
    problems = _secret_problems()
    if not problems:
        return

    env_name = settings.ENVIRONMENT.lower()
    if env_name not in _DEV_ENVS:
        raise ValueError(
            f"vie-admin refuses to start in ENVIRONMENT={env_name!r}: "
            f"{'; '.join(problems)}. Generate each with `openssl rand -hex 32`."
        )
    for problem in problems:
        logger.warning("%s — acceptable in development only", problem)
