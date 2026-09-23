"""Internal purge endpoint — the summarizer half of the API's global video delete.

HTTP layer only; the store fan-out lives in services/video_purge.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field, StringConstraints

from src.routes.deps import require_internal_secret
from src.services.video_purge import purge_video

router = APIRouter(tags=["purge"])

YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
SummaryId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{24}$")]


class PurgeRequest(BaseModel):
    """Summary ids of every version of the video; their per-run Redis keys go too."""

    videoSummaryIds: list[SummaryId] = Field(default_factory=list, max_length=50)


class PurgeResponse(BaseModel):
    """Per-store counts; warnings name the stores that failed."""

    youtubeId: str
    qdrantPoints: int
    s3Objects: int
    redisKeys: int
    warnings: list[str]


@router.post(
    "/internal/videos/{youtube_id}/purge",
    response_model=PurgeResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def purge_video_route(
    request: PurgeRequest,
    youtube_id: str = Path(..., pattern=YOUTUBE_ID_PATTERN),
) -> PurgeResponse:
    """Remove the video's vectors, S3 objects and Redis keys. Never fails on one store."""
    outcome = await purge_video(youtube_id, request.videoSummaryIds)
    return PurgeResponse(
        youtubeId=youtube_id,
        qdrantPoints=outcome.qdrant_points,
        s3Objects=outcome.s3_objects,
        redisKeys=outcome.redis_keys,
        warnings=outcome.warnings,
    )
