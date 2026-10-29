"""The X-Admin-Actor header vie-admin signs for vie-api, held to the shared golden vectors."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from src.auth import MACHINE_ACTOR, AdminActor
from src.config import settings
from src.services.vie_api_client import (
    ACTOR_HEADER,
    REQUEST_ID_HEADER,
    admin_headers,
    call_vie_api,
    encode_json_body,
    sign_actor_header,
)
from tests._actor import assert_signed_for, expected_signature
from tests._vectors import VECTORS

SECRET: str = VECTORS["actorHeader"]["secret"]
ACTOR_VECTORS: list[dict[str, Any]] = VECTORS["actorHeader"]["vectors"]
USER_ID = "64f1c0a2b3d4e5f607182930"
USER = AdminActor(kind="user", user_id=USER_ID)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _sign(vector: dict[str, Any], **overrides: Any) -> str:
    args = {
        "method": vector["method"],
        "target": vector["target"],
        "body": vector["body"].encode("utf-8"),
        "user_id": vector["userId"],
        "now": vector["ts"],
        **overrides,
    }
    return sign_actor_header(
        SECRET,
        args["user_id"],
        args["body"],
        method=args["method"],
        target=args["target"],
        now=args["now"],
    )


@pytest.mark.parametrize("vector", ACTOR_VECTORS, ids=[v["name"] for v in ACTOR_VECTORS])
def test_signs_to_the_golden_header(vector: dict[str, Any]) -> None:
    assert _sign(vector) == vector["header"]


@pytest.mark.parametrize("vector", ACTOR_VECTORS, ids=[v["name"] for v in ACTOR_VECTORS])
def test_hashes_the_body_to_the_golden_digest(vector: dict[str, Any]) -> None:
    import hashlib

    assert hashlib.sha256(vector["body"].encode("utf-8")).hexdigest() == vector["bodySha256"]


# ─── What the signature binds ───

POST = ACTOR_VECTORS[2]


def test_a_different_method_changes_the_signature() -> None:
    assert _sign(POST, method="DELETE") != POST["header"]


def test_a_different_path_changes_the_signature() -> None:
    assert _sign(POST, target=POST["target"].replace("credits", "adjust")) != POST["header"]


def test_a_different_query_changes_the_signature() -> None:
    assert _sign(POST, target=f"{POST['target']}?immediate=true") != POST["header"]


def test_a_different_user_changes_the_signature() -> None:
    assert _sign(POST, user_id="0a1b2c3d4e5f60718293a4b5") != POST["header"]


def test_a_different_body_changes_the_signature() -> None:
    assert _sign(POST, body=b'{"reason":"x","minutes":99999}') != POST["header"]


def test_a_different_second_changes_the_signature() -> None:
    assert _sign(POST, now=POST["ts"] + 1) != POST["header"]


def test_the_method_is_signed_in_upper_case_whatever_the_caller_passes() -> None:
    assert _sign(POST, method="post") == POST["header"]


def test_an_actor_signature_is_not_a_session_signature_over_the_same_text() -> None:
    """The actor| and session| prefixes keep the two uses of one secret apart."""
    import hashlib
    import hmac

    session_style = hmac.new(
        SECRET.encode(),
        f"session|{USER_ID}|1790000000|{hashlib.sha256(b'').hexdigest()}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert session_style != expected_signature(SECRET, USER_ID, 1790000000, "GET", "/", b"")


# ─── Headers for one call ───


def test_a_named_admin_call_carries_the_machine_key_and_a_signed_actor(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", SECRET)
    body = encode_json_body({"reason": "duplicate upload"})

    headers = admin_headers(USER, body, method="POST", target="/api/admin/x", now=1_790_000_000)

    assert headers["X-Admin-Key"] == settings.ADMIN_API_KEY
    assert headers[ACTOR_HEADER] == f"{USER_ID}.1790000000." + expected_signature(
        SECRET, USER_ID, 1_790_000_000, "POST", "/api/admin/x", body
    )


def test_the_timestamp_defaults_to_the_current_time(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", SECRET)

    headers = admin_headers(USER, method="GET", target="/api/admin/x")

    assert abs(int(headers[ACTOR_HEADER].split(".")[1]) - time.time()) < 5


def test_the_machine_key_alone_carries_no_actor() -> None:
    assert ACTOR_HEADER not in admin_headers(MACHINE_ACTOR)


def test_the_actor_header_is_signed_with_the_session_secret_not_the_machine_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "machine-key-" + "a" * 32)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", "session-secret-" + "b" * 32)

    header = admin_headers(USER, method="GET", target="/t", now=1_790_000_000)[ACTOR_HEADER]

    assert header == sign_actor_header(
        settings.ADMIN_SESSION_SECRET, USER_ID, b"", method="GET", target="/t", now=1_790_000_000
    )


def test_the_json_body_is_compact_and_ascii_escaped() -> None:
    assert encode_json_body({"reason": "dupliqué"}) == b'{"reason":"dupliqu\\u00e9"}'


# ─── call_vie_api: what actually goes on the wire ───


def _client(response: MagicMock | None = None, error: Exception | None = None):
    instance = MagicMock()
    instance.request = AsyncMock(return_value=response, side_effect=error)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), instance


def _ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={})
    return resp


async def _call(method: str, path: str, **kwargs: Any):
    factory, instance = _client(_ok())
    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await call_vie_api(method, path, USER, **kwargs)
    return instance.request.call_args


@pytest.mark.anyio
async def test_a_get_is_signed_for_its_path() -> None:
    call = await _call("GET", "queue/stats")

    assert_signed_for(call, method="GET", target="/api/admin/queue/stats")


@pytest.mark.anyio
async def test_a_get_is_signed_for_the_encoded_query_it_sends() -> None:
    params = {"targetId": "a" * 24, "before": "2026-11-01T10:00:00Z", "limit": 25}

    call = await _call("GET", "audit", params=params)

    target = f"/api/admin/audit?targetId={'a' * 24}&before=2026-11-01T10%3A00%3A00Z&limit=25"
    assert_signed_for(call, method="GET", target=target)


@pytest.mark.anyio
async def test_a_mutation_is_signed_for_its_method_path_and_body() -> None:
    call = await _call("DELETE", f"videos/{'x' * 11}", body={"reason": "dup"})

    assert_signed_for(
        call, method="DELETE", target=f"/api/admin/videos/{'x' * 11}", body=b'{"reason":"dup"}'
    )


@pytest.mark.anyio
async def test_a_header_made_for_a_get_does_not_verify_as_a_delete() -> None:
    """The replay the review found: the same header must not authorise another method or path."""
    get_call = await _call("GET", "queue/stats")
    header = get_call.kwargs["headers"][ACTOR_HEADER]
    _, issued_at, signature = header.split(".")

    delete_signature = expected_signature(
        settings.ADMIN_SESSION_SECRET,
        USER_ID,
        issued_at,
        "DELETE",
        "/api/admin/users/x?immediate=true",
        b"",
    )

    assert signature != delete_signature


@pytest.mark.anyio
async def test_every_call_carries_its_own_request_id() -> None:
    first = await _call("GET", "queue/stats")
    second = await _call("GET", "queue/stats")

    ids = [first.kwargs["headers"][REQUEST_ID_HEADER], second.kwargs["headers"][REQUEST_ID_HEADER]]
    assert len(set(ids)) == 2 and all(len(i) >= 8 for i in ids)


@pytest.mark.anyio
async def test_a_timeout_on_a_mutation_says_it_may_have_applied() -> None:
    factory, _ = _client(error=httpx.ReadTimeout("slow"))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        with pytest.raises(HTTPException) as caught:
            await call_vie_api("POST", "queue/replay", USER, body={"max": 5})

    assert caught.value.status_code == 504
    assert "may have been applied" in caught.value.detail


@pytest.mark.anyio
async def test_a_timeout_on_a_read_does_not_claim_a_change() -> None:
    factory, _ = _client(error=httpx.ReadTimeout("slow"))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        with pytest.raises(HTTPException) as caught:
            await call_vie_api("GET", "queue/stats", USER)

    assert (caught.value.status_code, caught.value.detail) == (504, "vie-api timed out")


@pytest.mark.anyio
async def test_the_signed_body_is_the_body_on_the_wire() -> None:
    call = await _call(
        "POST", "billing/accounts/x/plan", body={"reason": "dupliqué", "planId": "pro"}
    )

    assert json.loads(call.kwargs["content"]) == {"reason": "dupliqué", "planId": "pro"}
    assert_signed_for(
        call,
        method="POST",
        target="/api/admin/billing/accounts/x/plan",
        body=call.kwargs["content"],
    )
