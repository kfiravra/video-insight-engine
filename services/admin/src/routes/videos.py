"""Video administration — proxy to vie-api's global video delete.

vie-api owns the cascade (Mongo references, the summarizer purge of Qdrant,
S3 and Redis, and the audit rows). The admin service forwards the call so the
UI keeps a single base URL and auth model, naming the admin who made it in a
signed ``X-Admin-Actor`` header (see ``vie_api_client``); there is no
self-attested admin id in the request.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from src.auth import AdminActor, current_user_actor
from src.routes.usage import invalidate_video_cache
from src.services.vie_api_client import call_vie_api

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])

# S3 listing plus batched deletes for a long video can take a while.
_PURGE_TIMEOUT = httpx.Timeout(90.0, connect=2.0)
YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


class PurgeVideoRequest(BaseModel):
    """What the operator tells the audit row: why. Who is the session's admin."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, min_length=1, max_length=500)


@router.delete("/{youtube_id}")
async def purge_video(
    actor: Annotated[AdminActor, Depends(current_user_actor)],
    request: PurgeVideoRequest | None = None,
    youtube_id: str = Path(..., pattern=YOUTUBE_ID_PATTERN),
) -> dict:
    """Delete a video from every store for every user (vie-api runs the cascade).

    A timeout is a 504 saying the purge may still be running: reload before
    retrying instead of firing a second cascade blindly.
    """
    body = request or PurgeVideoRequest()
    logger.info("video_purge_requested", youtube_id=youtube_id, user_id=actor.user_id)
    result = await call_vie_api(
        "DELETE",
        f"videos/{youtube_id}",
        actor,
        body={"reason": body.reason} if body.reason else {},
        timeout=_PURGE_TIMEOUT,
    )
    invalidate_video_cache(youtube_id)
    return result
