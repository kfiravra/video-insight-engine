"""A reference check for the X-Admin-Actor header, independent of the code under test."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from src.config import settings
from tests.conftest import ADMIN_USER_ID


def expected_signature(
    secret: str, user_id: str, issued_at: str | int, method: str, target: str, body: bytes
) -> str:
    """The hex HMAC vie-api verifies: actor|METHOD|target|userId|ts|sha256(body)."""
    message = (
        f"actor|{method.upper()}|{target}|{user_id}|{issued_at}|{hashlib.sha256(body).hexdigest()}"
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def assert_signed_for(
    call: Any,
    *,
    method: str,
    target: str,
    body: bytes = b"",
    user_id: str = ADMIN_USER_ID,
) -> None:
    """The forwarded httpx call carries an actor header valid for exactly this request.

    `call` is the mock's `call_args` for `client.request(method, url, headers=..., content=...)`.
    """
    sent_method, sent_url = call.args
    assert sent_method.upper() == method.upper()
    assert sent_url.raw_path.decode() == target
    header = call.kwargs["headers"]["X-Admin-Actor"]
    signer, issued_at, signature = header.split(".")
    assert signer == user_id
    expected = expected_signature(
        settings.ADMIN_SESSION_SECRET,
        user_id,
        issued_at,
        method,
        target,
        call.kwargs["content"] or b"",
    )
    assert (call.kwargs["content"] or b"") == body
    assert signature == expected
