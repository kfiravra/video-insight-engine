"""Video administration — proxy to vie-api's global video delete.

vie-api owns the cascade (Mongo references, the summarizer purge of Qdrant,
S3 and Redis, and the audit row). The admin service forwards the call so the
UI keeps a single base URL and auth model. The proxy helpers are a second
copy of the ones in queue.py — extract on the third.
"""

from __future__ import annotations

import json

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from src.config import settings
from src.routes.usage import invalidate_video_cache

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])

# S3 listing plus batched deletes for a long video can take a while.
_PURGE_TIMEOUT = httpx.Timeout(90.0, connect=2.0)
YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


class PurgeVideoRequest(BaseModel):
    """Operator context forwarded to vie-api's audit row (self-attested)."""

    reason: str | None = Field(default=None, min_length=1, max_length=500)
    adminId: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )


def _admin_headers(admin_id: str | None) -> dict[str, str]:
    headers = {
        "X-Admin-Key": settings.ADMIN_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if admin_id:
        headers["X-Admin-Id"] = admin_id
    return headers


def _upstream_url(youtube_id: str) -> str:
    return f"{settings.VIE_API_URL.rstrip('/')}/api/admin/videos/{youtube_id}"


def _upstream_detail(resp: httpx.Response) -> dict | str:
    """vie-api's error envelope when the body is a JSON object, else a status line."""
    try:
        parsed = json.loads(resp.text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return f"vie-api error: {resp.status_code}"


def _translate_upstream(resp: httpx.Response) -> dict:
    if resp.status_code == 401:
        # vie-api rejected our admin key — the user already proved admin to
        # us, so the misconfig is server-side.
        raise HTTPException(status_code=500, detail="vie-api rejected admin key")
    if resp.status_code == 503:
        # vie-api's own "dependency down" answer — a real state, passed through.
        raise HTTPException(status_code=503, detail=_upstream_detail(resp))
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail=_upstream_detail(resp))
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_upstream_detail(resp))
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="vie-api returned a non-JSON body") from exc


@router.delete("/{youtube_id}")
async def purge_video(
    request: PurgeVideoRequest | None = None,
    youtube_id: str = Path(..., pattern=YOUTUBE_ID_PATTERN),
) -> dict:
    """Delete a video from every store for every user (vie-api runs the cascade)."""
    body = request or PurgeVideoRequest()
    logger.info("video_purge_requested", youtube_id=youtube_id, admin_id=body.adminId)
    payload = {"reason": body.reason} if body.reason else {}
    try:
        async with httpx.AsyncClient(timeout=_PURGE_TIMEOUT) as client:
            resp = await client.request(
                "DELETE",
                _upstream_url(youtube_id),
                headers=_admin_headers(body.adminId),
                content=json.dumps(payload),
            )
    except httpx.TimeoutException as exc:
        # The purge may still be running on vie-api: tell the operator to
        # reload before retrying instead of firing a second cascade blindly.
        logger.warning("video_purge_timeout", youtube_id=youtube_id)
        raise HTTPException(
            status_code=504,
            detail="vie-api timed out; the purge may still be running — reload before retrying",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("video_purge_unreachable", youtube_id=youtube_id, error=str(exc))
        raise HTTPException(status_code=502, detail="vie-api unreachable") from exc
    result = _translate_upstream(resp)
    invalidate_video_cache(youtube_id)
    return result
