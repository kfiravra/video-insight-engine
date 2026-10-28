"""Session tokens that name a user, the live admin check, and the machine-key limits."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Annotated, Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import PyMongoError
from structlog.testing import capture_logs

import src.auth as auth_module
from src.auth import (
    SESSION_ADMIN_TTL_SECONDS,
    SESSION_TOKEN_TTL_SECONDS,
    AdminActor,
    AdminVerdictCache,
    ApiKeyMiddleware,
    create_session_token,
    current_actor,
    current_user_actor,
    verify_session_token,
)
from src.config import settings

USER_ID = "64f1c0a2b3d4e5f607182930"
EMAIL = "admin@example.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _signed(payload: str) -> str:
    """A token over an arbitrary payload, correctly signed with the session secret."""
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = hmac.new(
        settings.ADMIN_SESSION_SECRET.encode(), f"session|{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


class Clock:
    """A settable clock so the 60-second cache is tested without sleeping."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


# ─── Session tokens ───


def test_token_carries_the_user_id_email_and_expiry() -> None:
    token = create_session_token(USER_ID, EMAIL, now=1_000)

    claims = verify_session_token(token, now=1_001)

    assert claims is not None
    assert (claims.user_id, claims.email, claims.expiry) == (
        USER_ID,
        EMAIL,
        1_000 + SESSION_TOKEN_TTL_SECONDS,
    )


def test_token_round_trips_an_email_containing_a_pipe() -> None:
    token = create_session_token(USER_ID, "odd|local@example.com")

    claims = verify_session_token(token)

    assert claims is not None and claims.email == "odd|local@example.com"


def test_token_is_rejected_after_it_expires() -> None:
    token = create_session_token(USER_ID, EMAIL, now=0)

    assert verify_session_token(token, now=SESSION_TOKEN_TTL_SECONDS) is None


def test_old_email_only_token_is_rejected_even_when_correctly_signed() -> None:
    """Tokens issued before sessions named a user must force a fresh login."""
    legacy = _signed("admin@example.com|9999999999")

    assert verify_session_token(legacy) is None


def test_token_whose_user_id_is_not_an_object_id_is_rejected() -> None:
    forged = _signed("not-an-object-id|admin@example.com|9999999999")

    assert verify_session_token(forged) is None


def test_token_with_a_non_numeric_expiry_is_rejected() -> None:
    forged = _signed(f"{USER_ID}|{EMAIL}|soon")

    assert verify_session_token(forged) is None


def test_a_token_signed_without_the_session_prefix_is_rejected() -> None:
    """Sessions are domain-separated from the actor| messages signed with the same secret."""
    payload = f"{USER_ID}|{EMAIL}|9999999999"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    bare = hmac.new(
        settings.ADMIN_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    assert verify_session_token(f"{encoded}.{bare}") is None


def test_an_actor_signature_cannot_be_presented_as_a_session_token() -> None:
    payload = f"{USER_ID}|{EMAIL}|9999999999"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    actor_style = hmac.new(
        settings.ADMIN_SESSION_SECRET.encode(), f"actor|{payload}".encode(), hashlib.sha256
    ).hexdigest()

    assert verify_session_token(f"{encoded}.{actor_style}") is None


def test_token_with_a_changed_user_id_fails_the_signature() -> None:
    token = create_session_token(USER_ID, EMAIL)
    encoded, _, signature = token.partition(".")
    payload = base64.urlsafe_b64decode(encoded).decode().replace(USER_ID, "0" * 24)
    swapped = f"{base64.urlsafe_b64encode(payload.encode()).decode()}.{signature}"

    assert verify_session_token(swapped) is None


# ─── The live admin check and its 60-second cache ───


def _verdicts(user: dict[str, Any] | None, clock: Clock) -> tuple[AdminVerdictCache, AsyncMock]:
    loader = AsyncMock(return_value=user)
    return AdminVerdictCache(loader, clock=clock), loader


@pytest.mark.anyio
async def test_an_admin_is_recognised() -> None:
    cache, _ = _verdicts({"roles": ["admin"]}, Clock())

    assert await cache.is_active_admin(USER_ID) is True


@pytest.mark.anyio
async def test_the_user_is_read_once_per_window() -> None:
    cache, loader = _verdicts({"roles": ["admin"]}, Clock())

    await cache.is_active_admin(USER_ID)
    await cache.is_active_admin(USER_ID)

    assert loader.await_count == 1


@pytest.mark.anyio
async def test_a_demotion_is_not_seen_inside_the_window() -> None:
    clock = Clock()
    cache, loader = _verdicts({"roles": ["admin"]}, clock)
    await cache.is_active_admin(USER_ID)
    loader.return_value = {"roles": []}
    clock.now += SESSION_ADMIN_TTL_SECONDS - 1

    assert await cache.is_active_admin(USER_ID) is True


@pytest.mark.anyio
async def test_a_demotion_is_seen_once_the_window_expires() -> None:
    clock = Clock()
    cache, loader = _verdicts({"roles": ["admin"]}, clock)
    await cache.is_active_admin(USER_ID)
    loader.return_value = {"roles": []}
    clock.now += SESSION_ADMIN_TTL_SECONDS + 1

    assert await cache.is_active_admin(USER_ID) is False


@pytest.mark.anyio
async def test_a_soft_deleted_admin_is_not_an_admin() -> None:
    cache, _ = _verdicts({"roles": ["admin"], "deletedAt": "2026-05-01T00:00:00Z"}, Clock())

    assert await cache.is_active_admin(USER_ID) is False


@pytest.mark.anyio
async def test_a_user_who_no_longer_exists_is_not_an_admin() -> None:
    cache, _ = _verdicts(None, Clock())

    assert await cache.is_active_admin(USER_ID) is False


@pytest.mark.anyio
async def test_a_failed_lookup_is_raised_and_not_remembered() -> None:
    cache, loader = _verdicts({"roles": ["admin"]}, Clock())
    loader.side_effect = [PyMongoError("down"), {"roles": ["admin"]}]

    with pytest.raises(PyMongoError):
        await cache.is_active_admin(USER_ID)

    assert await cache.is_active_admin(USER_ID) is True


@pytest.mark.anyio
async def test_the_cache_stays_within_its_size_bound() -> None:
    cache = AdminVerdictCache(
        AsyncMock(return_value={"roles": ["admin"]}), max_entries=2, clock=Clock()
    )

    for n in range(5):
        await cache.is_active_admin(f"{n:024x}")

    assert len(cache._verdicts) <= 2


# ─── Middleware ───


def _probe_app() -> FastAPI:
    """A tiny app behind the real middleware, to observe the actor routes see."""
    probe = FastAPI()
    probe.add_middleware(ApiKeyMiddleware)

    @probe.api_route("/probe", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def read_actor(
        actor: Annotated[AdminActor, Depends(current_actor)],
    ) -> dict[str, str | None]:
        return {"kind": actor.kind, "user_id": actor.user_id}

    @probe.get("/named-admin-only")
    async def named_admin_only(
        actor: Annotated[AdminActor, Depends(current_user_actor)],
    ) -> dict[str, str | None]:
        return {"user_id": actor.user_id}

    return probe


async def _call(method: str, path: str = "/probe", token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(
        transport=ASGITransport(app=_probe_app()), base_url="http://test"
    ) as client:
        return await client.request(method, path, headers=headers)


@pytest.fixture
def live_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, Clock]:
    """An admin the users collection still vouches for, with a clock the test moves."""
    clock = Clock()
    loader = AsyncMock(return_value={"roles": ["admin"]})
    monkeypatch.setattr(auth_module, "session_admins", AdminVerdictCache(loader, clock=clock))
    return loader, clock


@pytest.mark.anyio
async def test_a_session_gives_routes_a_user_actor(live_admin) -> None:
    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL))

    assert resp.json() == {"kind": "user", "user_id": USER_ID}


@pytest.mark.anyio
async def test_a_session_may_mutate(live_admin) -> None:
    resp = await _call("POST", token=create_session_token(USER_ID, EMAIL))

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_the_machine_key_gives_routes_a_machine_actor() -> None:
    resp = await _call("GET", token=settings.ADMIN_API_KEY)

    assert resp.json() == {"kind": "machine", "user_id": None}


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_the_machine_key_cannot_mutate(method: str) -> None:
    resp = await _call(method, token=settings.ADMIN_API_KEY)

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_a_machine_key_refusal_does_not_reach_the_route() -> None:
    resp = await _call("POST", token=settings.ADMIN_API_KEY)

    assert resp.json() == {"error": "The machine key is read-only"}


@pytest.mark.anyio
async def test_a_route_that_needs_a_named_admin_refuses_the_machine_key() -> None:
    resp = await _call("GET", "/named-admin-only", token=settings.ADMIN_API_KEY)

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_a_route_that_needs_a_named_admin_accepts_a_session(live_admin) -> None:
    resp = await _call("GET", "/named-admin-only", token=create_session_token(USER_ID, EMAIL))

    assert resp.json() == {"user_id": USER_ID}


@pytest.mark.anyio
async def test_a_demoted_admins_live_session_stops_working_once_the_cache_expires(
    live_admin,
) -> None:
    loader, clock = live_admin
    token = create_session_token(USER_ID, EMAIL)
    assert (await _call("GET", token=token)).status_code == 200
    loader.return_value = {"roles": []}
    clock.now += SESSION_ADMIN_TTL_SECONDS + 1

    resp = await _call("GET", token=token)

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_demoted_admins_session_still_works_inside_the_cache_window(live_admin) -> None:
    loader, clock = live_admin
    token = create_session_token(USER_ID, EMAIL)
    await _call("GET", token=token)
    loader.return_value = {"roles": []}
    clock.now += SESSION_ADMIN_TTL_SECONDS - 1

    assert (await _call("GET", token=token)).status_code == 200


@pytest.mark.anyio
async def test_a_legacy_admin_whose_roles_were_emptied_loses_access(live_admin) -> None:
    """`roles: []` wins over a leftover `role: "admin"`."""
    loader, clock = live_admin
    token = create_session_token(USER_ID, EMAIL)
    loader.return_value = {"roles": [], "role": "admin"}

    assert (await _call("GET", token=token)).status_code == 401


@pytest.mark.anyio
async def test_a_legacy_admin_without_a_roles_array_keeps_access(live_admin) -> None:
    loader, _ = live_admin
    loader.return_value = {"role": "admin"}

    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL))

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_a_soft_deleted_admin_is_rejected(live_admin) -> None:
    loader, _ = live_admin
    loader.return_value = {"roles": ["admin"], "deletedAt": "2026-05-01T00:00:00Z"}

    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL))

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_user_who_no_longer_exists_is_rejected(live_admin) -> None:
    loader, _ = live_admin
    loader.return_value = None

    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL))

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_rejected_session_says_nothing_about_why(live_admin) -> None:
    """Demoted, deleted and forged all read the same to the caller."""
    loader, _ = live_admin
    loader.return_value = {"roles": []}

    demoted = await _call("GET", token=create_session_token(USER_ID, EMAIL))
    forged = await _call("GET", token="garbage")

    assert demoted.json() == forged.json()


@pytest.mark.anyio
async def test_the_check_fails_closed_when_the_users_collection_is_unreachable(live_admin) -> None:
    loader, _ = live_admin
    loader.side_effect = PyMongoError("down")

    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL))

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_an_expired_session_is_rejected(live_admin) -> None:
    resp = await _call("GET", token=create_session_token(USER_ID, EMAIL, now=0))

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_token_signed_with_the_machine_key_is_rejected(live_admin, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "machine-key-" + "a" * 32)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", "session-secret-" + "b" * 32)
    payload = f"{USER_ID}|{EMAIL}|9999999999"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = hmac.new(
        settings.ADMIN_API_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    resp = await _call("GET", token=f"{encoded}.{signature}")

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_request_without_credentials_is_rejected() -> None:
    assert (await _call("GET")).status_code == 401


@pytest.mark.anyio
async def test_the_health_path_stays_open() -> None:
    probe = _probe_app()

    @probe.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    async with AsyncClient(transport=ASGITransport(app=probe), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200


# ─── Logging ───


@pytest.mark.anyio
async def test_a_rejected_session_is_logged_by_user_id_never_by_email(live_admin) -> None:
    loader, _ = live_admin
    loader.return_value = {"roles": []}

    with capture_logs() as logs:
        await _call("GET", token=create_session_token(USER_ID, EMAIL))

    rejection = next(entry for entry in logs if entry["event"] == "admin_session_rejected")
    assert rejection["user_id"] == USER_ID
    assert EMAIL not in repr(logs)
