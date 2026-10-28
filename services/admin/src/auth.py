"""Authentication for the admin service.

Two credentials are accepted on protected routes:
- the raw ``ADMIN_API_KEY`` (the machine key: programmatic access, curl,
  health tooling — also what vie-admin sends vie-api as ``X-Admin-Key``), or
- an HMAC-signed session token issued by ``POST /auth/login`` after an
  email/password check against the ``users`` collection (``role: "admin"``).

Session tokens are signed with ``ADMIN_SESSION_SECRET``, never with
``ADMIN_API_KEY``: holding the machine key must not let anyone mint a session
for an arbitrary email. Rotating ``ADMIN_SESSION_SECRET`` invalidates every
outstanding session (logs every admin out); rotating ``ADMIN_API_KEY`` does
not touch sessions.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings

# Paths that never require authentication
EXEMPT_PATHS = {"/health", "/auth/login"}

# Static SPA paths served without auth (exact match or prefix)
SPA_EXACT_PATHS = {"/", "/index.html"}
SPA_PREFIX_PATHS = ("/assets/",)

SESSION_TOKEN_TTL_SECONDS = 12 * 60 * 60


def _sign(payload: str) -> str:
    secret = settings.ADMIN_SESSION_SECRET.encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def create_session_token(email: str, *, now: float | None = None) -> str:
    """Issue a signed session token: base64url("email|expiry") + "." + hmac."""
    issued_at = time.time() if now is None else now
    expiry = int(issued_at + SESSION_TOKEN_TTL_SECONDS)
    payload = f"{email}|{expiry}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{encoded}.{_sign(payload)}"


def verify_session_token(token: str, *, now: float | None = None) -> bool:
    """True only for a well-formed, correctly signed, unexpired token."""
    encoded, sep, signature = token.partition(".")
    if not sep:
        return False
    try:
        payload = base64.urlsafe_b64decode(encoded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    # Compare as bytes: str compare_digest raises TypeError on non-ASCII
    # input, and the signature arrives attacker-controlled via the
    # latin-1-decoded Authorization header.
    if not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return False
    _, pipe, expiry_str = payload.rpartition("|")
    if not pipe:
        return False
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    return (time.time() if now is None else now) < expiry


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Requires the API key or a valid session token on all non-exempt routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow health check, login, and static SPA assets without auth
        if path in EXEMPT_PATHS or path in SPA_EXACT_PATHS:
            return await call_next(request)
        if any(path.startswith(p) for p in SPA_PREFIX_PATHS):
            return await call_next(request)

        # All other paths require Bearer token
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "Missing Authorization header"}, status_code=401)

        token = auth[7:]
        is_api_key = hmac.compare_digest(token.encode(), settings.ADMIN_API_KEY.encode())
        if not is_api_key and not verify_session_token(token):
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)

        return await call_next(request)
