"""Tests for POST /auth/login and session-token auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import bcrypt
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

import src.routes.auth as auth_routes
from src.auth import (
    SESSION_TOKEN_TTL_SECONDS,
    create_session_token,
    verify_session_token,
)
from src.config import settings
from src.main import app

USER_ID = "64f1c0a2b3d4e5f607182930"
PASSWORD = "Admin123"
PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """Rate-limit state is module-global; isolate it per test."""
    auth_routes._login_attempts.clear()
    yield
    auth_routes._login_attempts.clear()


def _mock_db(user_doc: dict[str, Any] | None) -> MagicMock:
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value=user_doc)
    return db


def _admin_user(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": ObjectId(USER_ID),
        "email": "admin@admin.com",
        "passwordHash": PASSWORD_HASH,
        "roles": ["admin"],
    }
    doc.update(overrides)
    return doc


async def _post_login(payload: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/auth/login", json=payload)


# ─── Session token unit tests ───


def test_session_token_round_trip():
    token = create_session_token(USER_ID, "admin@admin.com")
    assert verify_session_token(token)


def test_session_token_expired():
    token = create_session_token(USER_ID, "admin@admin.com", now=0)
    assert not verify_session_token(token, now=SESSION_TOKEN_TTL_SECONDS + 1)


def test_session_token_tampered_signature():
    token = create_session_token(USER_ID, "admin@admin.com")
    encoded, _, sig = token.partition(".")
    tampered = f"{encoded}.{'0' * len(sig)}"
    assert not verify_session_token(tampered)


def test_session_token_garbage_inputs():
    for bad in ("", "no-dot", "a.b", "!!!.???", create_session_token(USER_ID, "x")[:-5]):
        assert not verify_session_token(bad)


# ─── Signing secret separation ───
# The machine key (ADMIN_API_KEY) must not be able to mint admin sessions.

API_KEY = "machine-key-" + "a" * 32
SESSION_SECRET = "session-secret-" + "b" * 32


@pytest.fixture
def distinct_secrets(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", SESSION_SECRET)


def _token_signed_with(secret: str, email: str = "admin@admin.com") -> str:
    """Build a session token by hand, HMAC-keyed on an arbitrary secret."""
    payload = f"{USER_ID}|{email}|{int(time.time()) + 3600}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def test_token_signed_with_session_secret_accepted(distinct_secrets):
    assert verify_session_token(_token_signed_with(SESSION_SECRET))


def test_token_signed_with_api_key_rejected(distinct_secrets):
    """Regression: tokens used to be HMAC-keyed on ADMIN_API_KEY, so the
    machine key could forge a session for any email."""
    assert not verify_session_token(_token_signed_with(API_KEY))


def test_rotating_session_secret_invalidates_outstanding_tokens(distinct_secrets, monkeypatch):
    token = create_session_token(USER_ID, "admin@admin.com")
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", "rotated-" + "c" * 32)

    assert not verify_session_token(token)


def test_rotating_api_key_keeps_sessions_valid(distinct_secrets, monkeypatch):
    token = create_session_token(USER_ID, "admin@admin.com")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "rotated-" + "d" * 32)

    assert verify_session_token(token)


# ─── Login route tests ───


@pytest.mark.anyio
async def test_login_success_returns_session_token(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user()))

    resp = await _post_login({"email": "Admin@Admin.com", "password": PASSWORD})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@admin.com"
    assert body["expires_in"] == SESSION_TOKEN_TTL_SECONDS
    claims = verify_session_token(body["token"])
    assert claims is not None and claims.user_id == USER_ID


@pytest.mark.anyio
async def test_login_wrong_password_rejected(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user()))

    resp = await _post_login({"email": "admin@admin.com", "password": "nope"})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_unknown_email_rejected(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(None))

    resp = await _post_login({"email": "ghost@example.com", "password": PASSWORD})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_non_admin_user_rejected(monkeypatch):
    """A valid password is NOT enough — the user must hold the admin role."""
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user(roles=[])))

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_soft_deleted_admin_rejected(monkeypatch):
    monkeypatch.setattr(
        auth_routes,
        "get_database",
        lambda: _mock_db(_admin_user(deletedAt="2026-01-01T00:00:00Z")),
    )

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_admin_without_password_hash_rejected(monkeypatch):
    """Regression: an admin doc missing passwordHash must NOT be loggable-in
    with the source-code dummy password used for timing equalization."""
    user = _admin_user()
    del user["passwordHash"]
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(user))

    resp = await _post_login({"email": "admin@admin.com", "password": "vie-admin-dummy-password"})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_validates_body():
    resp = await _post_login({"email": "a@b.c"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_login_accepts_a_legacy_admin_without_a_roles_array(monkeypatch):
    legacy = _admin_user(role="admin")
    del legacy["roles"]
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(legacy))

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_login_rejects_a_demoted_admin_whose_legacy_role_is_still_stored(monkeypatch):
    """`roles: []` is authoritative: the leftover `role: admin` must not revive access."""
    monkeypatch.setattr(
        auth_routes, "get_database", lambda: _mock_db(_admin_user(roles=[], role="admin"))
    )

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_logs_the_user_id_and_never_the_email(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user()))

    with capture_logs() as logs:
        await _post_login({"email": "admin@admin.com", "password": PASSWORD})
        await _post_login({"email": "admin@admin.com", "password": "wrong"})

    assert {entry["event"] for entry in logs} >= {"admin_login_success", "admin_login_rejected"}
    assert all(entry.get("user_id") == USER_ID for entry in logs)
    assert "admin@admin.com" not in repr(logs)


@pytest.mark.anyio
async def test_login_does_not_log_the_email_of_an_unknown_account(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(None))

    with capture_logs() as logs:
        await _post_login({"email": "ghost@example.com", "password": PASSWORD})

    assert "ghost@example.com" not in repr(logs)


# ─── Middleware integration ───


@pytest.mark.anyio
async def test_session_token_accepted_by_middleware(admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            resp = await client.get("/usage/stats", headers=admin_session)
            # Auth passed — may be 500 (no DB in tests) but never 401
            assert resp.status_code != 401
        except RuntimeError as e:
            assert "MongoDB" in str(e)


@pytest.mark.anyio
async def test_api_key_signed_token_rejected_by_middleware(distinct_secrets):
    token = _token_signed_with(API_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/stats", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_raw_api_key_still_accepted_by_middleware(distinct_secrets):
    """The machine key keeps working as a bearer credential (only sessions moved)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            resp = await client.get("/usage/stats", headers={"Authorization": f"Bearer {API_KEY}"})
            assert resp.status_code != 401
        except RuntimeError as e:
            assert "MongoDB" in str(e)


@pytest.mark.anyio
async def test_expired_session_token_rejected_by_middleware():
    token = create_session_token(USER_ID, "admin@admin.com", now=0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/usage/stats", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_login_path_exempt_from_auth(monkeypatch):
    """/auth/login must be reachable without a bearer token."""
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(None))

    resp = await _post_login({"email": "x@y.z", "password": "pw"})

    # 401 from bad credentials, not from the middleware's missing-header branch
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


# ─── Login rate limiting ───


def test_rate_limiter_allows_under_limit():
    for i in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS):
        assert not auth_routes._login_rate_limited("1.2.3.4", now=100.0 + i)


def test_rate_limiter_blocks_over_limit():
    for i in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS):
        auth_routes._login_rate_limited("1.2.3.4", now=100.0 + i)
    assert auth_routes._login_rate_limited("1.2.3.4", now=105.0)


def test_rate_limiter_resets_after_window():
    for i in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS):
        auth_routes._login_rate_limited("1.2.3.4", now=100.0 + i)
    later = 100.0 + auth_routes.LOGIN_RATE_LIMIT_WINDOW_SECONDS + 10.0
    assert not auth_routes._login_rate_limited("1.2.3.4", now=later)


def test_rate_limiter_is_per_client():
    for i in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS):
        auth_routes._login_rate_limited("1.2.3.4", now=100.0 + i)
    assert auth_routes._login_rate_limited("1.2.3.4", now=105.0)
    assert not auth_routes._login_rate_limited("5.6.7.8", now=105.0)


def test_rate_limiter_evicts_stale_clients():
    for n in range(auth_routes._MAX_TRACKED_CLIENTS + 1):
        auth_routes._login_rate_limited(f"10.0.0.{n}", now=100.0)
    later = 100.0 + auth_routes.LOGIN_RATE_LIMIT_WINDOW_SECONDS + 10.0
    auth_routes._login_rate_limited("fresh-client", now=later)
    # All stale windows were evicted; only the fresh client remains
    assert len(auth_routes._login_attempts) == 1


@pytest.mark.anyio
async def test_login_route_returns_429_over_limit(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user()))

    for _ in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS):
        resp = await _post_login({"email": "admin@admin.com", "password": "wrong"})
        assert resp.status_code == 401

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})
    assert resp.status_code == 429


@pytest.mark.anyio
async def test_login_route_under_limit_not_throttled(monkeypatch):
    monkeypatch.setattr(auth_routes, "get_database", lambda: _mock_db(_admin_user()))

    for _ in range(auth_routes.LOGIN_RATE_LIMIT_ATTEMPTS - 1):
        resp = await _post_login({"email": "admin@admin.com", "password": "wrong"})
        assert resp.status_code == 401

    resp = await _post_login({"email": "admin@admin.com", "password": PASSWORD})
    assert resp.status_code == 200
