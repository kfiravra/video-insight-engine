"""Tests for startup secret validation — dev defaults refused outside development."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI

from src import config
from src.config import settings, validate_secrets
from src.main import lifespan

REAL_API_KEY = "a1" * 32
REAL_SESSION_SECRET = "b2" * 32


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _safe_production_settings(monkeypatch):
    """Baseline: production with two real, distinct secrets — each test breaks one thing."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", REAL_API_KEY)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", REAL_SESSION_SECRET)


# ─── Production refuses unsafe secrets ───


def test_should_raise_when_api_key_is_dev_default_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", config._DEFAULT_ADMIN_API_KEY)

    with pytest.raises(ValueError, match="ADMIN_API_KEY"):
        validate_secrets()


def test_should_raise_when_session_secret_is_dev_default_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", config._DEFAULT_ADMIN_SESSION_SECRET)

    with pytest.raises(ValueError, match="ADMIN_SESSION_SECRET"):
        validate_secrets()


def test_should_raise_when_session_secret_is_empty_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", "")

    with pytest.raises(ValueError, match="ADMIN_SESSION_SECRET"):
        validate_secrets()


def test_should_raise_when_session_secret_equals_api_key_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", REAL_API_KEY)

    with pytest.raises(ValueError, match="equals ADMIN_API_KEY"):
        validate_secrets()


def test_should_never_echo_secret_value_in_error(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", REAL_API_KEY)

    with pytest.raises(ValueError) as exc_info:
        validate_secrets()

    assert REAL_API_KEY not in str(exc_info.value)


def test_should_treat_unknown_environment_as_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "some-cloud")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", config._DEFAULT_ADMIN_API_KEY)

    with pytest.raises(ValueError, match="some-cloud"):
        validate_secrets()


def test_should_match_environment_name_case_insensitively(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "PRODUCTION")
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", config._DEFAULT_ADMIN_SESSION_SECRET)

    with pytest.raises(ValueError):
        validate_secrets()


def test_should_pass_when_real_distinct_secrets_in_production():
    validate_secrets()


# ─── Development tolerates defaults ───


@pytest.mark.parametrize("env_name", ["", "development", "dev", "test", "local"])
def test_should_not_raise_on_dev_defaults_in_dev_environments(monkeypatch, env_name):
    monkeypatch.setattr(settings, "ENVIRONMENT", env_name)
    monkeypatch.setattr(settings, "ADMIN_API_KEY", config._DEFAULT_ADMIN_API_KEY)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", config._DEFAULT_ADMIN_SESSION_SECRET)

    validate_secrets()


def test_should_warn_on_dev_defaults_in_development(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", config._DEFAULT_ADMIN_SESSION_SECRET)

    with caplog.at_level(logging.WARNING, logger="src.config"):
        validate_secrets()

    assert any("ADMIN_SESSION_SECRET" in r.getMessage() for r in caplog.records)


# ─── Wiring: the app lifespan runs the check first ───


@pytest.mark.anyio
async def test_should_refuse_to_start_app_on_dev_default_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", config._DEFAULT_ADMIN_API_KEY)

    with pytest.raises(ValueError, match="ADMIN_API_KEY"):
        async with lifespan(FastAPI()):
            pass
