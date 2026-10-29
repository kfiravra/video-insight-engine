"""Shared fixtures: a live admin session for tests that call protected routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import src.auth as auth_module
from src.auth import AdminVerdictCache, create_session_token

ADMIN_USER_ID = "64f1c0a2b3d4e5f607182930"
ADMIN_EMAIL = "admin@example.com"


@pytest.fixture
def admin_session(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Bearer headers for a signed-in admin whose account is still an active admin."""
    verdicts = AdminVerdictCache(AsyncMock(return_value={"roles": ["admin"]}))
    monkeypatch.setattr(auth_module, "session_admins", verdicts)
    token = create_session_token(ADMIN_USER_ID, ADMIN_EMAIL)
    return {"Authorization": f"Bearer {token}"}
