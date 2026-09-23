"""Shared FastAPI dependencies for the summarizer's HTTP surface."""

import hmac

from fastapi import Header, HTTPException

from src.config import settings


async def require_internal_secret(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
) -> None:
    """Reject service-to-service calls that do not carry the shared secret.

    Constant-time comparison, matching the API gateway's ``timingSafeEqual``.
    """
    provided = (x_internal_secret or "").encode("utf-8")
    expected = settings.INTERNAL_SECRET.encode("utf-8")
    if not x_internal_secret or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing internal secret")
