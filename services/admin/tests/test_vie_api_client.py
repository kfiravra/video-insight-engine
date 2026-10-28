"""The X-Admin-Actor header vie-admin signs for vie-api, held to the shared golden vectors."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import pytest

from src.auth import MACHINE_ACTOR, AdminActor
from src.config import settings
from src.services.vie_api_client import (
    ACTOR_HEADER,
    admin_headers,
    encode_json_body,
    sign_actor_header,
)
from tests._vectors import VECTORS

SECRET: str = VECTORS["actorHeader"]["secret"]
ACTOR_VECTORS: list[dict[str, Any]] = VECTORS["actorHeader"]["vectors"]
USER_ID = "64f1c0a2b3d4e5f607182930"


@pytest.mark.parametrize("vector", ACTOR_VECTORS, ids=[v["name"] for v in ACTOR_VECTORS])
def test_signs_to_the_golden_header(vector: dict[str, Any]) -> None:
    header = sign_actor_header(
        SECRET, vector["userId"], vector["body"].encode("utf-8"), now=vector["ts"]
    )

    assert header == vector["header"]


@pytest.mark.parametrize("vector", ACTOR_VECTORS, ids=[v["name"] for v in ACTOR_VECTORS])
def test_hashes_the_body_to_the_golden_digest(vector: dict[str, Any]) -> None:
    assert hashlib.sha256(vector["body"].encode("utf-8")).hexdigest() == vector["bodySha256"]


@pytest.fixture
def golden_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", SECRET)


def _verify(header: str, body: bytes) -> tuple[str, int]:
    """Check a header with an independent HMAC; returns (user id, timestamp)."""
    user_id, ts, signature = header.split(".")
    message = f"{user_id}|{ts}|{hashlib.sha256(body).hexdigest()}".encode()
    expected = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    return user_id, int(ts)


def test_a_named_admin_call_carries_the_machine_key_and_a_signed_actor(golden_secret: None) -> None:
    body = encode_json_body({"reason": "duplicate upload"})

    headers = admin_headers(AdminActor(kind="user", user_id=USER_ID), body, now=1_790_000_000)

    assert headers["X-Admin-Key"] == settings.ADMIN_API_KEY
    assert _verify(headers[ACTOR_HEADER], body) == (USER_ID, 1_790_000_000)


def test_the_signature_covers_the_exact_body_bytes(golden_secret: None) -> None:
    actor = AdminActor(kind="user", user_id=USER_ID)

    signed_for_one = admin_headers(actor, b'{"max":5}', now=1_790_000_000)[ACTOR_HEADER]
    signed_for_other = admin_headers(actor, b'{"max":500}', now=1_790_000_000)[ACTOR_HEADER]

    assert signed_for_one != signed_for_other


def test_a_body_less_call_is_signed_over_the_empty_body(golden_secret: None) -> None:
    headers = admin_headers(AdminActor(kind="user", user_id=USER_ID), now=1_790_000_000)

    assert _verify(headers[ACTOR_HEADER], b"") == (USER_ID, 1_790_000_000)


def test_the_timestamp_defaults_to_the_current_time(golden_secret: None) -> None:
    headers = admin_headers(AdminActor(kind="user", user_id=USER_ID))

    _, issued_at = _verify(headers[ACTOR_HEADER], b"")
    assert abs(issued_at - time.time()) < 5


def test_the_machine_key_alone_carries_no_actor() -> None:
    headers = admin_headers(MACHINE_ACTOR)

    assert ACTOR_HEADER not in headers


def test_the_actor_header_is_signed_with_the_session_secret_not_the_machine_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "machine-key-" + "a" * 32)
    monkeypatch.setattr(settings, "ADMIN_SESSION_SECRET", "session-secret-" + "b" * 32)

    header = admin_headers(AdminActor(kind="user", user_id=USER_ID), now=1_790_000_000)[
        ACTOR_HEADER
    ]

    assert header == sign_actor_header(
        settings.ADMIN_SESSION_SECRET, USER_ID, b"", now=1_790_000_000
    )


def test_the_json_body_is_compact_and_ascii_escaped() -> None:
    assert encode_json_body({"reason": "dupliqué"}) == b'{"reason":"dupliqu\\u00e9"}'
