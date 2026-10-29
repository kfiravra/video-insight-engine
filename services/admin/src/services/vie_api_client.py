"""vie-admin's calls to vie-api's ``/api/admin/*`` routes: headers, bodies, errors.

Every call carries the shared machine key (``X-Admin-Key``). A call made for a
named admin also carries ``X-Admin-Actor: <userId>.<unixTs>.<hex hmac>`` so
vie-api knows who acted and never has to trust a body field:

    hmac = HMAC-SHA256(ADMIN_SESSION_SECRET,
        f"actor|{METHOD}|{request-target}|{userId}|{unixTs}|{sha256hex(body)}")

The signature binds the HTTP method, the request target exactly as sent (path
plus encoded query) and the body bytes, so a header cannot be replayed against
another route, user or payload; the ``actor|`` prefix keeps it apart from the
``session|`` messages that sign login tokens. vie-api accepts it for five
minutes, accepts a non-GET signature once, and re-checks that the user still
holds the permission. The format is Contract 3 of the billing-roles work;
``api/src/utils/admin-actor.ts`` verifies it, and both sides are held to the
vectors in ``packages/types/test-vectors/admin-auth.json``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import structlog
from fastapi import HTTPException

from src.auth import AdminActor
from src.config import settings

logger = structlog.get_logger(__name__)

ACTOR_HEADER = "X-Admin-Actor"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REQUEST_ID_HEADER = "X-Request-ID"
# Upper bound for any single admin minutes amount, mirrored from vie-api's BillingService.
MAX_ADMIN_MINUTES = 100_000
# Shapes vie-api validates again; checked here so a bad request fails fast with a 422.
OBJECT_ID_PATTERN = r"^[0-9a-fA-F]{24}$"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=2.0)


def encode_json_body(payload: Mapping[str, object]) -> bytes:
    """Compact JSON as bytes; send these exact bytes so the signature covers them."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def sign_actor_header(
    secret: str,
    user_id: str,
    body: bytes,
    *,
    method: str,
    target: str,
    now: int,
) -> str:
    """The ``X-Admin-Actor`` value for ``user_id`` calling ``method target`` with ``body``.

    ``target`` is the request target exactly as it goes on the wire, path plus
    encoded query (``/api/admin/audit?targetId=…``); ``now`` is unix seconds.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"actor|{method.upper()}|{target}|{user_id}|{now}|{body_hash}".encode()
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{user_id}.{now}.{signature}"


def admin_headers(
    actor: AdminActor,
    body: bytes = b"",
    *,
    method: str = "GET",
    target: str = "/",
    now: int | None = None,
) -> dict[str, str]:
    """Headers for one vie-api admin call; the actor header only for a named admin."""
    headers = {"X-Admin-Key": settings.ADMIN_API_KEY, "Accept": "application/json"}
    if actor.kind == "user" and actor.user_id is not None:
        issued_at = int(time.time()) if now is None else now
        headers[ACTOR_HEADER] = sign_actor_header(
            settings.ADMIN_SESSION_SECRET,
            actor.user_id,
            body,
            method=method,
            target=target,
            now=issued_at,
        )
    return headers


# ─── Calling vie-api ───


def upstream_detail(resp: httpx.Response) -> dict | str:
    """vie-api's error envelope when the body is a JSON object, else a status line.

    Kept structured (``{error, message[, replayed]}``) so a mid-drain
    ``REPLAY_FAILED`` keeps its partial ``replayed`` count. Raw non-JSON bodies
    are never forwarded: they could be proxy or HTML error pages.
    """
    try:
        parsed = json.loads(resp.text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return f"vie-api error: {resp.status_code}"


def translate_upstream(resp: httpx.Response) -> dict:
    """The JSON body of a 2xx answer, or the HTTPException the operator should see."""
    if resp.status_code == 401:
        # vie-api rejected our key or the signed actor (ADMIN_API_KEY or
        # ADMIN_SESSION_SECRET differs between the services). The user already
        # proved admin to us, so the misconfig is server-side.
        raise HTTPException(status_code=500, detail="vie-api rejected the admin credentials")
    if resp.status_code == 503:
        # vie-api's own "dependency down" answer: a real state, passed through.
        raise HTTPException(status_code=503, detail=upstream_detail(resp))
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail=upstream_detail(resp))
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=upstream_detail(resp))
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="vie-api returned a non-JSON body") from exc


def _request_url(path: str, params: Mapping[str, Any] | None) -> httpx.URL:
    """The URL vie-api is called on, with the query encoded here so its bytes are known.

    httpx sends ``url.raw_path`` as the request target, and that same string is
    what the actor signature covers.
    """
    url = f"{settings.VIE_API_URL.rstrip('/')}/api/admin/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urlencode(params, quote_via=quote)}"
    return httpx.URL(url)


async def call_vie_api(
    method: str,
    path: str,
    actor: AdminActor,
    *,
    body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> dict:
    """One call to ``/api/admin/<path>`` on behalf of ``actor``; returns vie-api's JSON.

    The signature covers the HTTP method, the request target exactly as sent
    (path plus encoded query) and the body bytes, so those are what goes on the
    wire. ``idempotency_key`` (one per user action) makes a retried mutation
    apply once on vie-api. Every call carries a fresh ``X-Request-ID`` that is
    also logged here, so an ``adminAudit`` row's request id can be found in
    vie-admin's log. A timeout on anything but a read may mean the change was
    applied, so it is reported as such instead of as a plain failure.
    """
    url = _request_url(path, params)
    content = encode_json_body(body) if body is not None else b""
    request_id = uuid.uuid4().hex
    headers = admin_headers(actor, content, method=method, target=url.raw_path.decode("ascii"))
    headers[REQUEST_ID_HEADER] = request_id
    if body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers[IDEMPOTENCY_HEADER] = idempotency_key
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, content=content or None)
    except httpx.TimeoutException as exc:
        logger.warning("vie_api_timeout", method=method, path=path, request_id=request_id)
        raise HTTPException(status_code=504, detail=_timeout_detail(method)) from exc
    except httpx.RequestError as exc:
        logger.warning(
            "vie_api_unreachable", method=method, path=path, request_id=request_id, error=str(exc)
        )
        raise HTTPException(status_code=502, detail="vie-api unreachable") from exc
    logger.info(
        "vie_api_call",
        method=method,
        path=path,
        request_id=request_id,
        user_id=actor.user_id,
        status=resp.status_code,
    )
    return translate_upstream(resp)


def _timeout_detail(method: str) -> str:
    if method.upper() == "GET":
        return "vie-api timed out"
    return "vie-api timed out; the change may have been applied — reload before retrying"
