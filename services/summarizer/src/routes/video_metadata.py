"""Internal pre-flight metadata endpoint — the summarizer half of the API's billing quote.

yt-dlp metadata only: no captions, no LLM, no pipeline state. It goes through
``extract_video_data``, so the proxy exit rotation is the one every other
YouTube call uses.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from yt_dlp.utils import ExtractorError  # type: ignore[import-untyped]

from src.exceptions import TranscriptError
from src.logging_config import get_logger
from src.models.schemas import ErrorCode, VideoMetadataResponse
from src.routes.deps import require_internal_secret
from src.routes.purge import YOUTUBE_ID_PATTERN
from src.services.media.download_utils import is_exit_blocked
from src.services.video.youtube import extract_video_data

router = APIRouter(tags=["video"])
logger = get_logger(__name__)

_YOUTUBE_ID_RE = re.compile(YOUTUBE_ID_PATTERN)


def _video_is_gone(cause: BaseException | None) -> bool:
    """True when the video itself is the problem: private, removed, age-gated, unknown.

    yt-dlp marks those ExtractorErrors ``expected`` and wraps them in a
    DownloadError that keeps the original in ``exc_info``. No cause at all is
    an empty extraction. Anything else (transport failures, extractor bugs) says
    nothing about the video.
    """
    if cause is None:
        return True
    exc_info = getattr(cause, "exc_info", None)
    original = exc_info[1] if exc_info and exc_info[1] is not None else cause
    return isinstance(original, ExtractorError) and original.expected


def _failure(youtube_id: str, error: TranscriptError) -> HTTPException:
    """Map an extraction failure to a status; only the video being gone is a 404."""
    cause = error.__cause__
    if error.code == ErrorCode.LIVE_STREAM:
        status, detail = 422, "Live streams are not supported"
    elif cause is not None and is_exit_blocked(cause):
        status, detail = 503, "YouTube is rate-limiting the lookup, retry shortly"
    elif _video_is_gone(cause):
        status, detail = 404, "Video not found or unavailable"
    else:
        status, detail = 503, "Metadata lookup failed, retry shortly"
    # Class name only: yt-dlp messages can carry request URLs.
    logger.warning(
        "video_metadata_failed",
        youtube_id=youtube_id,
        status=status,
        cause=type(cause).__name__ if cause is not None else None,
    )
    return HTTPException(status_code=status, detail=detail)


@router.get(
    "/video/metadata",
    response_model=VideoMetadataResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def video_metadata(
    youtube_id: str = Query(..., alias="youtubeId"),
) -> VideoMetadataResponse:
    """Title, channel, duration (seconds) and thumbnail of one video.

    400 for a malformed id, 404 when the video is unknown or unavailable, 422 for
    a live stream or a video with no duration yet, 503 when YouTube could not be
    asked (rate limit, transport). There is no route-level timeout, like
    ``/playlist/extract``: yt-dlp's socket timeout and the exit-rotation budget
    bound the call, and the API client gives up after its own 15 s.
    """
    if not _YOUTUBE_ID_RE.fullmatch(youtube_id):
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID")

    try:
        video = await extract_video_data(youtube_id)
    except TranscriptError as e:
        raise _failure(youtube_id, e) from e

    # 0 means yt-dlp had no duration (a premiere, a video still processing): a
    # quote from it would price the video at the minimum.
    if video.duration <= 0:
        raise HTTPException(status_code=422, detail="Video has no duration yet")

    return VideoMetadataResponse(
        youtubeId=youtube_id,
        title=video.title,
        channel=video.channel,
        duration=video.duration,
        thumbnailUrl=video.thumbnail_url,
    )
