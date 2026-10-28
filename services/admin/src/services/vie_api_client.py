"""Headers and bodies for vie-admin's calls to vie-api's ``/api/admin/*`` routes.

Every call carries the shared machine key (``X-Admin-Key``). A call made for a
named admin also carries ``X-Admin-Actor: <userId>.<unixTs>.<hex hmac>`` so
vie-api knows who acted and never has to trust a body field:

    hmac = HMAC-SHA256(ADMIN_SESSION_SECRET, f"{userId}|{unixTs}|{sha256hex(body)}")

``body`` is the exact request body bytes (empty for a GET), so a header cannot
be replayed against a different payload. vie-api accepts it for five minutes and
re-checks that the user still holds the permission. The format is Contract 3 of
the billing-roles work; ``api/src/utils/admin-actor.ts`` verifies it, and both
sides are held to the vectors in ``packages/types/test-vectors/admin-auth.json``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping

from src.auth import AdminActor
from src.config import settings

ACTOR_HEADER = "X-Admin-Actor"


def encode_json_body(payload: Mapping[str, object]) -> bytes:
    """Compact JSON as bytes; send these exact bytes so the signature covers them."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def sign_actor_header(secret: str, user_id: str, body: bytes, *, now: int) -> str:
    """The ``X-Admin-Actor`` value for ``user_id`` acting at ``now`` (unix seconds) on ``body``."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{user_id}|{now}|{body_hash}".encode()
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{user_id}.{now}.{signature}"


def admin_headers(
    actor: AdminActor, body: bytes = b"", *, now: int | None = None
) -> dict[str, str]:
    """Headers for one vie-api admin call; the actor header only for a named admin."""
    headers = {"X-Admin-Key": settings.ADMIN_API_KEY, "Accept": "application/json"}
    if actor.kind == "user" and actor.user_id is not None:
        issued_at = int(time.time()) if now is None else now
        headers[ACTOR_HEADER] = sign_actor_header(
            settings.ADMIN_SESSION_SECRET, actor.user_id, body, now=issued_at
        )
    return headers
