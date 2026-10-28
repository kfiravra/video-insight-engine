"""Authentication for the admin service.

Two credentials are accepted on protected routes:
- the raw ``ADMIN_API_KEY`` (the machine key: health tooling, curl). It acts as
  ``AdminActor(kind="machine")`` and may only read: any method other than GET
  is a 403. It is also what vie-admin sends vie-api as ``X-Admin-Key``.
- an HMAC-signed session token issued by ``POST /auth/login`` after an
  email/password check against the ``users`` collection. The token names the
  user (``userId|email|expiry``), and the middleware re-checks on every
  request that this user is still an active admin, so a demoted or deleted
  admin loses access within ``SESSION_ADMIN_TTL_SECONDS``, not at token expiry.

Session tokens are signed with ``ADMIN_SESSION_SECRET``, never with
``ADMIN_API_KEY``: holding the machine key must not let anyone mint a session
for an arbitrary user. Rotating ``ADMIN_SESSION_SECRET`` invalidates every
outstanding session (logs every admin out); rotating ``ADMIN_API_KEY`` does
not touch sessions.

The authenticated actor is placed on ``request.state.actor`` and read by routes
through ``current_actor`` / ``current_user_actor``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import structlog
from bson import ObjectId
from fastapi import Depends, HTTPException
from pymongo.errors import PyMongoError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config import settings
from src.dependencies import get_database
from src.roles import is_active_admin

logger = structlog.get_logger(__name__)

# Paths that never require authentication
EXEMPT_PATHS = {"/health", "/auth/login"}

# Static SPA paths served without auth (exact match or prefix)
SPA_EXACT_PATHS = {"/", "/index.html"}
SPA_PREFIX_PATHS = ("/assets/",)

SESSION_TOKEN_TTL_SECONDS = 12 * 60 * 60

# How long a "this user is still an admin" verdict is reused. A demotion or a
# soft delete reaches live sessions within this window.
SESSION_ADMIN_TTL_SECONDS = 60.0
_MAX_CACHED_VERDICTS = 256

# The machine key reads; it never mutates.
MACHINE_KEY_METHODS = frozenset({"GET"})

_USER_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")


# ─── Actor ───


@dataclass(frozen=True, slots=True)
class AdminActor:
    """Who is calling: a named admin (``user``) or the shared machine key (``machine``)."""

    kind: Literal["user", "machine"]
    user_id: str | None = None


MACHINE_ACTOR = AdminActor(kind="machine")


def current_actor(request: Request) -> AdminActor:
    """Dependency: the actor the middleware authenticated for this request."""
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, AdminActor):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return actor


def current_user_actor(actor: Annotated[AdminActor, Depends(current_actor)]) -> AdminActor:
    """Dependency for mutations: a named admin, never the shared machine key."""
    if actor.kind != "user" or actor.user_id is None:
        raise HTTPException(status_code=403, detail="A named admin session is required")
    return actor


# ─── Session tokens ───


@dataclass(frozen=True, slots=True)
class SessionClaims:
    """The signed contents of a session token."""

    user_id: str
    email: str
    expiry: int


def _sign(payload: str) -> str:
    secret = settings.ADMIN_SESSION_SECRET.encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(user_id: str, email: str, *, now: float | None = None) -> str:
    """Issue a signed token: base64url("userId|email|expiry") + "." + hmac."""
    issued_at = time.time() if now is None else now
    expiry = int(issued_at + SESSION_TOKEN_TTL_SECONDS)
    payload = f"{user_id}|{email}|{expiry}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{encoded}.{_sign(payload)}"


def _parse_claims(payload: str) -> SessionClaims | None:
    """Split ``userId|email|expiry``; the email may itself contain ``|``."""
    user_id, first_sep, rest = payload.partition("|")
    email, last_sep, expiry_str = rest.rpartition("|")
    if not first_sep or not last_sep or not _USER_ID_PATTERN.match(user_id):
        return None
    try:
        return SessionClaims(user_id=user_id, email=email, expiry=int(expiry_str))
    except ValueError:
        return None


def verify_session_token(token: str, *, now: float | None = None) -> SessionClaims | None:
    """The claims of a well-formed, correctly signed, unexpired token, else None."""
    encoded, sep, signature = token.partition(".")
    if not sep:
        return None
    try:
        payload = base64.urlsafe_b64decode(encoded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII
    # input, and the signature arrives attacker-controlled via the
    # latin-1-decoded Authorization header.
    if not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return None
    claims = _parse_claims(payload)
    if claims is None or (time.time() if now is None else now) >= claims.expiry:
        return None
    return claims


# ─── Live admin check ───

UserLoader = Callable[[str], Awaitable[dict[str, object] | None]]


async def _load_user_roles(user_id: str) -> dict[str, object] | None:
    """Only the fields the admin rule reads; no hash, no profile data."""
    return await get_database().users.find_one(
        {"_id": ObjectId(user_id)}, {"roles": 1, "role": 1, "deletedAt": 1}
    )


class AdminVerdictCache:
    """Answers "is this user still an active admin?" and reuses the answer briefly.

    Caches the boolean verdict, never the user document, keyed by user id, so a
    busy panel does not read ``users`` on every request. The clock is injected
    so tests move time without sleeping.
    """

    def __init__(
        self,
        loader: UserLoader,
        *,
        ttl_seconds: float = SESSION_ADMIN_TTL_SECONDS,
        max_entries: int = _MAX_CACHED_VERDICTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._verdicts: dict[str, tuple[float, bool]] = {}

    async def is_active_admin(self, user_id: str) -> bool:
        now = self._clock()
        cached = self._verdicts.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]
        verdict = is_active_admin(await self._loader(user_id))
        self._remember(user_id, verdict, now)
        return verdict

    def _remember(self, user_id: str, verdict: bool, now: float) -> None:
        if len(self._verdicts) >= self._max_entries:
            self._verdicts = {k: v for k, v in self._verdicts.items() if v[0] > now}
        if len(self._verdicts) >= self._max_entries:
            self._verdicts.pop(next(iter(self._verdicts)))
        self._verdicts[user_id] = (now + self._ttl_seconds, verdict)


session_admins = AdminVerdictCache(_load_user_roles)


# ─── Middleware ───


def _reject(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _is_open_path(path: str) -> bool:
    return path in EXEMPT_PATHS or path in SPA_EXACT_PATHS or path.startswith(SPA_PREFIX_PATHS)


async def _authenticate(request: Request, token: str) -> Response | None:
    """Set ``request.state.actor`` and return None, or return the rejection."""
    if hmac.compare_digest(token.encode(), settings.ADMIN_API_KEY.encode()):
        if request.method not in MACHINE_KEY_METHODS:
            return _reject(403, "The machine key is read-only")
        request.state.actor = MACHINE_ACTOR
        return None

    claims = verify_session_token(token)
    if claims is None:
        return _reject(401, "Invalid credentials")
    try:
        active = await session_admins.is_active_admin(claims.user_id)
    except PyMongoError:
        # Fail closed: without the users collection nobody can be re-validated.
        logger.error("admin_session_check_failed", user_id=claims.user_id)
        return _reject(503, "Authentication is temporarily unavailable")
    if not active:
        logger.info("admin_session_rejected", user_id=claims.user_id)
        return _reject(401, "Invalid credentials")
    request.state.actor = AdminActor(kind="user", user_id=claims.user_id)
    return None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Authenticates every non-exempt route as the machine key or a live admin session."""

    async def dispatch(self, request: Request, call_next):
        if _is_open_path(request.url.path):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _reject(401, "Missing Authorization header")

        denial = await _authenticate(request, auth[7:])
        if denial is not None:
            return denial
        return await call_next(request)
