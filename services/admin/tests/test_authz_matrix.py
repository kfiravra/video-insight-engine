"""Authorization matrix for every vie-admin route.

The routes are not listed here: they are read from the app, so a router added
without going through the auth middleware (or a new path added to the open
list) fails these tests the day it lands. They come from the app's OpenAPI
document (`app.openapi()`, the stable public way to enumerate included
routers; the HTTP docs endpoints stay disabled), so a route declared with
`include_in_schema=False` would not be seen: do not use that flag on a route.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import src.auth as auth_module
from src.auth import (
    EXEMPT_PATHS,
    SPA_EXACT_PATHS,
    SPA_PREFIX_PATHS,
    AdminVerdictCache,
    create_session_token,
)
from src.config import settings
from src.main import app

PLACEHOLDER = "a" * 24
NON_ADMIN_ID = "64f1c0a2b3d4e5f607182931"
# HEAD/OPTIONS are never registered by the routers; CORS handles preflight.
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def _documented_routes() -> list[tuple[str, str]]:
    """(method, registered path) for every route in the app, whatever router it came from."""
    paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    return [
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() not in _IGNORED_METHODS
    ]


def _protected_routes() -> list[tuple[str, str, str]]:
    """(method, registered path, concrete path) for every route behind the middleware."""
    found: list[tuple[str, str, str]] = []
    for method, path in _documented_routes():
        if path in EXEMPT_PATHS or path in SPA_EXACT_PATHS:
            continue
        found.append((method, path, re.sub(r"\{[^}]+\}", PLACEHOLDER, path)))
    return sorted(found)


ROUTES = _protected_routes()
MUTATIONS = [route for route in ROUTES if route[0] != "GET"]
IDS = [f"{method} {path}" for method, path, _ in ROUTES]
MUTATION_IDS = [f"{method} {path}" for method, path, _ in MUTATIONS]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _request(method: str, path: str, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers)


def test_the_matrix_finds_the_routers_it_is_meant_to_guard() -> None:
    prefixes = (
        "/alerts",
        "/billing",
        "/health/",
        "/queue",
        "/shares",
        "/tiers",
        "/usage",
        "/users",
        "/videos",
    )

    missing = [p for p in prefixes if not any(path.startswith(p) for _, path, _ in ROUTES)]

    assert missing == []


def test_the_open_paths_are_only_health_login_and_the_spa_shell() -> None:
    """Widening the exemption list is a security change; this makes it a visible one."""
    assert EXEMPT_PATHS == {"/health", "/auth/login"}
    assert SPA_EXACT_PATHS == {"/", "/index.html"}
    assert SPA_PREFIX_PATHS == ("/assets/",)


def test_the_only_open_mutation_is_login() -> None:
    open_mutations = [
        (method, path)
        for method, path in _documented_routes()
        if path in EXEMPT_PATHS and method != "GET"
    ]

    assert open_mutations == [("POST", "/auth/login")]


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "_path", "url"), ROUTES, ids=IDS)
async def test_rejects_a_request_without_credentials(method: str, _path: str, url: str) -> None:
    resp = await _request(method, url)

    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "_path", "url"), ROUTES, ids=IDS)
async def test_rejects_a_wrong_key(method: str, _path: str, url: str) -> None:
    resp = await _request(method, url, token="not-the-key")

    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "_path", "url"), ROUTES, ids=IDS)
async def test_rejects_the_session_of_a_non_admin(
    method: str, _path: str, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correctly signed session for a user without the admin role is still a 401."""
    non_admins = AdminVerdictCache(AsyncMock(return_value={"roles": []}))
    monkeypatch.setattr(auth_module, "session_admins", non_admins)

    resp = await _request(method, url, token=create_session_token(NON_ADMIN_ID, "user@example.com"))

    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "_path", "url"), ROUTES, ids=IDS)
async def test_rejects_the_session_of_a_deleted_admin(
    method: str, _path: str, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted = AdminVerdictCache(
        AsyncMock(return_value={"roles": ["admin"], "deletedAt": "2026-05-01T00:00:00Z"})
    )
    monkeypatch.setattr(auth_module, "session_admins", deleted)

    resp = await _request(method, url, token=create_session_token(NON_ADMIN_ID, "gone@example.com"))

    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "_path", "url"), MUTATIONS, ids=MUTATION_IDS)
async def test_refuses_the_machine_key_on_every_mutation(method: str, _path: str, url: str) -> None:
    resp = await _request(method, url, token=settings.ADMIN_API_KEY)

    assert resp.status_code == 403
