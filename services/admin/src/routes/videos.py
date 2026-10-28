"""Video administration — proxy to vie-api's global video delete.

vie-api owns the cascade (Mongo references, the summarizer purge of Qdrant,
S3 and Redis, and the audit rows). The admin service forwards the call so the
UI keeps a single base URL and auth model, naming the admin who made it in a
signed ``X-Admin-Actor`` header (see ``vie_api_client``); there is no
self-attested admin id in the request. The proxy helpers are a second copy of
the ones in queue.py — extract on the third.
"""

from __future__ import annotations

import json
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from src.auth import AdminActor, current_user_actor
from src.config import settings
from src.routes.usage import invalidate_video_cache
from src.services.vie_api_client import admin_headers, encode_json_body

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])

# S3 listing plus batched deletes for a long video can take a while.
_PURGE_TIMEOUT = httpx.Timeout(90.0, connect=2.0)
YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


class PurgeVideoRequest(BaseModel):
    """What the operator tells the audit row: why. Who is the session's admin."""

    reason: str | None = Field(default=None, min_length=1, max_length=500)


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
        # vie-api rejected our key or the signed actor (ADMIN_API_KEY or
        # ADMIN_SESSION_SECRET differs between the services) — the user already
        # proved admin to us, so the misconfig is server-side.
        raise HTTPException(status_code=500, detail="vie-api rejected the admin credentials")
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
    actor: Annotated[AdminActor, Depends(current_user_actor)],
    request: PurgeVideoRequest | None = None,
    youtube_id: str = Path(..., pattern=YOUTUBE_ID_PATTERN),
) -> dict:
    """Delete a video from every store for every user (vie-api runs the cascade)."""
    body = request or PurgeVideoRequest()
    logger.info("video_purge_requested", youtube_id=youtube_id, user_id=actor.user_id)
    # The signature covers these exact bytes, so they are what goes on the wire.
    content = encode_json_body({"reason": body.reason} if body.reason else {})
    headers = {**admin_headers(actor, content), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_PURGE_TIMEOUT) as client:
            resp = await client.request(
                "DELETE", _upstream_url(youtube_id), headers=headers, content=content
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
