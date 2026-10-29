"""Email/password login for the admin panel.

Validates credentials against the shared ``users`` collection and requires an
active admin: the same rule the session middleware re-applies on every
request (``src.roles.is_active_admin``). Successful logins receive an
HMAC-signed session token naming the user, accepted by ``ApiKeyMiddleware``.
Logs carry the user id, never the email.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import bcrypt
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth import SESSION_TOKEN_TTL_SECONDS, create_session_token
from src.dependencies import get_database
from src.roles import is_active_admin

logger = structlog.get_logger(__name__)

router = APIRouter()

# Compared against when the user does not exist so response timing does not
# reveal whether an email is registered.
_DUMMY_HASH = bcrypt.hashpw(b"vie-admin-dummy-password", bcrypt.gensalt(rounds=10))

# In-process sliding-window rate limit for login attempts. Each attempt burns
# a bcrypt verify, so an unthrottled endpoint is both a brute-force and a CPU
# DoS vector. Single-process service → in-memory state is sufficient.
LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60.0
_MAX_TRACKED_CLIENTS = 1024

_login_attempts: dict[str, deque[float]] = {}


def _login_rate_limited(client_ip: str, *, now: float | None = None) -> bool:
    """True when the client exceeded LOGIN_RATE_LIMIT_ATTEMPTS in the window."""
    ts = time.monotonic() if now is None else now
    window_start = ts - LOGIN_RATE_LIMIT_WINDOW_SECONDS
    if len(_login_attempts) > _MAX_TRACKED_CLIENTS:
        for ip in [ip for ip, w in _login_attempts.items() if not w or w[-1] < window_start]:
            del _login_attempts[ip]
    attempts = _login_attempts.setdefault(client_ip, deque())
    while attempts and attempts[0] < window_start:
        attempts.popleft()
    if len(attempts) >= LOGIN_RATE_LIMIT_ATTEMPTS:
        return True
    attempts.append(ts)
    return False


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    token: str
    email: str
    expires_in: int


def _password_matches(password: str, stored_hash: object) -> bool:
    """False unless ``stored_hash`` is a real bcrypt hash matching ``password``.

    Accounts without a string ``passwordHash`` (OAuth-only, migrations) still
    burn a bcrypt verify against ``_DUMMY_HASH`` for timing parity, but can
    NEVER authenticate — returning the dummy comparison directly would make
    the dummy password in this file a working credential for such accounts.
    """
    is_real_hash = isinstance(stored_hash, str)
    hash_bytes = stored_hash.encode() if is_real_hash else _DUMMY_HASH
    try:
        return bcrypt.checkpw(password.encode(), hash_bytes) and is_real_hash
    except ValueError:
        return False


def _user_id(user: dict[str, object] | None) -> str:
    """The user's ObjectId as hex, or ``""`` when there is no such user."""
    return str(user["_id"]) if user is not None else ""


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    client_ip = request.client.host if request.client else "unknown"
    if _login_rate_limited(client_ip):
        logger.warning("admin_login_rate_limited", client_ip=client_ip)
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again shortly.")

    email = body.email.strip().lower()
    db = get_database()
    user = await db.users.find_one({"email": email})

    stored_hash = user.get("passwordHash") if user else None
    valid = await asyncio.to_thread(_password_matches, body.password, stored_hash)

    if user is None or not valid or not is_active_admin(user):
        logger.info("admin_login_rejected", user_id=_user_id(user))
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user_id = _user_id(user)
    logger.info("admin_login_success", user_id=user_id)
    return LoginResponse(
        token=create_session_token(user_id, email),
        email=email,
        expires_in=SESSION_TOKEN_TTL_SECONDS,
    )
