import asyncio
import logging
import re

import tenacity
from youtube_transcript_api import Transcript, TranscriptList, YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import (
    ErrorCode,
    TranscriptSegment,
    TranscriptSource,
)
from src.services.media.download_utils import ytdlp_proxy_url

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Rate Limit Detection & Retry Logic
# ─────────────────────────────────────────────────────


def _is_rate_limit_error(exception: BaseException) -> bool:
    """Check if exception is a rate limit (429) error."""
    error_str = str(exception).lower()
    return any(x in error_str for x in ["429", "too many", "rate limit"])


def _log_retry(retry_state: tenacity.RetryCallState) -> None:
    """Log retry attempts for rate limiting."""
    wait_time = retry_state.next_action.sleep if retry_state.next_action else 0
    logger.warning(
        f"Transcript fetch rate limited, attempt {retry_state.attempt_number}/3, "
        f"waiting {wait_time:.1f}s..."
    )


def _select_track(transcript_list: TranscriptList) -> tuple[Transcript | None, str]:
    """Pick a track: manual English -> generated English -> any language.

    Returns ``(transcript, transcript_type)``; ``transcript`` is ``None``
    when the list is empty. The English lookups label the type by which
    finder succeeded; the any-language fallback reads the track's own
    ``is_generated`` flag so a manual non-English track is not mislabelled.
    """
    try:
        return transcript_list.find_manually_created_transcript(["en"]), "manual"
    except (NoTranscriptFound, TranscriptsDisabled):
        pass
    try:
        return transcript_list.find_generated_transcript(["en"]), "auto-generated"
    except (NoTranscriptFound, TranscriptsDisabled):
        pass
    for track in transcript_list:
        is_manual = getattr(track, "is_generated", None) is False
        return track, ("manual" if is_manual else "auto-generated")
    return None, "auto-generated"


def _proxy_config() -> GenericProxyConfig | None:
    """Caption-fetch proxy: YOUTUBE_PROXY_URL (one exit for every YouTube call) wins;
    otherwise the Webshare rotating pool; otherwise direct."""
    proxy_url = ytdlp_proxy_url()
    if proxy_url:
        return GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    if settings.WEBSHARE_PROXY_USERNAME and settings.WEBSHARE_PROXY_PASSWORD:
        return WebshareProxyConfig(
            proxy_username=settings.WEBSHARE_PROXY_USERNAME,
            proxy_password=settings.WEBSHARE_PROXY_PASSWORD,
        )
    return None


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=2, min=4, max=30),
    retry=tenacity.retry_if_exception(_is_rate_limit_error),
    before_sleep=_log_retry,
    reraise=True,
)
def _fetch_transcript_sync(video_id: str) -> tuple[list[dict], str, str, str | None]:
    """
    Fetch transcript from YouTube (synchronous internal function).

    transcript_type is "manual" for a creator-uploaded track and
    "auto-generated" otherwise. The English lookups label it by which
    finder succeeded; the any-language fallback labels it from the
    track's own is_generated flag, so a manual non-English track is
    not mislabelled.

    Returns:
        (segments, full_text, transcript_type, language_code)
    """
    ytt_api = YouTubeTranscriptApi(proxy_config=_proxy_config())

    try:
        # New API: use instance method .list() instead of class method .list_transcripts()
        transcript_list = ytt_api.list(video_id)
        transcript, transcript_type = _select_track(transcript_list)
        if not transcript:
            raise TranscriptError("No transcript available", ErrorCode.NO_TRANSCRIPT)

        # Capture language code from the transcript object
        language_code = getattr(transcript, "language_code", None)

        # Fetch transcript and convert to dict format
        fetched = transcript.fetch()
        # New API returns FetchedTranscript object, convert to raw data
        segments = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)

        # Handle both old dict format and new snippet format
        if segments and hasattr(segments[0], "text"):
            segments = [
                {"text": s.text, "start": s.start, "duration": s.duration} for s in segments
            ]

        full_text = " ".join([s["text"] for s in segments])

        return segments, full_text, transcript_type, language_code

    except TranscriptsDisabled:
        raise TranscriptError("Captions are disabled for this video", ErrorCode.NO_TRANSCRIPT)
    except NoTranscriptFound:
        raise TranscriptError("No English transcript available", ErrorCode.NO_TRANSCRIPT)
    except VideoUnavailable:
        raise TranscriptError("Video is unavailable or private", ErrorCode.VIDEO_UNAVAILABLE)
    except TranscriptError:
        raise
    except Exception as e:
        # Detect rate limiting errors and map to specific error code
        if _is_rate_limit_error(e):
            raise TranscriptError(
                "YouTube rate limited. Please try again later.",
                ErrorCode.RATE_LIMITED,
            )
        raise TranscriptError(f"Failed to fetch transcript: {str(e)}", ErrorCode.UNKNOWN_ERROR)


def clean_transcript(text: str) -> str:
    """Clean and normalize transcript text."""
    # Remove common artifacts
    text = re.sub(r"\[Music\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Applause\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Laughter\]", "", text, flags=re.IGNORECASE)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def format_transcript_with_timestamps(segments: list[dict], interval_seconds: int = 30) -> str:
    """Format transcript with timestamps at regular intervals.

    Groups segments into chunks to reduce character bloat while still
    providing timestamp context for the LLM. This is more efficient than
    per-segment timestamps which can bloat the text by 2-3x.

    Args:
        segments: List of transcript segments with 'text' and 'start' keys
        interval_seconds: Group segments into this interval (default 30s)

    Returns:
        Formatted string like:
        [0:00] Hello everyone, welcome to the video. Today we're going to talk about...
        [0:30] The first concept is dependency injection. It's a design pattern...
        [1:00] Let me show you an example of how this works in practice.
    """
    if not segments:
        return ""

    lines = []
    current_interval = -1
    current_texts = []

    for segment in segments:
        start_seconds = int(segment.get("start", 0))
        interval = start_seconds // interval_seconds

        if interval != current_interval:
            # Flush previous interval
            if current_texts:
                interval_start = current_interval * interval_seconds
                mins = interval_start // 60
                secs = interval_start % 60
                timestamp = f"[{mins}:{secs:02d}]"
                lines.append(f"{timestamp} {' '.join(current_texts)}")
            current_texts = []
            current_interval = interval

        text = segment.get("text", "").strip()
        if text:
            current_texts.append(text)

    # Flush final interval
    if current_texts:
        interval_start = current_interval * interval_seconds
        mins = interval_start // 60
        secs = interval_start % 60
        timestamp = f"[{mins}:{secs:02d}]"
        lines.append(f"{timestamp} {' '.join(current_texts)}")

    return "\n".join(lines)


def normalize_segments(
    segments: list[dict],
    source: TranscriptSource,
) -> list[TranscriptSegment]:
    """
    Convert any segment format to normalized milliseconds.

    Handles different input formats:
    - Browser: already has startMs/endMs (milliseconds)
    - yt-dlp/API: start (seconds) + duration (seconds)
    - Whisper: start (seconds) + end (seconds)

    Args:
        segments: Raw segments in any format
        source: Source of the transcript for format detection

    Returns:
        List of normalized TranscriptSegment objects
    """
    normalized = []
    for seg in segments:
        # Handle different input formats
        if "startMs" in seg:
            # Already normalized (Whisper format)
            normalized.append(
                TranscriptSegment(
                    text=seg["text"],
                    startMs=int(seg["startMs"]),
                    endMs=int(seg["endMs"]),
                )
            )
        elif "end" in seg:
            # Whisper format: start + end (seconds)
            normalized.append(
                TranscriptSegment(
                    text=seg["text"],
                    startMs=int(seg["start"] * 1000),
                    endMs=int(seg["end"] * 1000),
                )
            )
        else:
            # youtube-transcript-api / yt-dlp format: start + duration (seconds)
            start_s = seg.get("start", 0)
            duration_s = seg.get("duration", 0)
            normalized.append(
                TranscriptSegment(
                    text=seg["text"],
                    startMs=int(start_s * 1000),
                    endMs=int((start_s + duration_s) * 1000),
                )
            )
    return normalized


async def get_transcript(video_id: str) -> tuple[list[dict], str, str, str | None]:
    """
    Fetch transcript from YouTube (async wrapper).

    Runs the blocking YouTube API call in a thread pool to avoid
    blocking the event loop.

    Args:
        video_id: YouTube video ID

    Returns:
        (segments, full_text, transcript_type, language_code)

    Raises:
        TranscriptError: If transcript cannot be fetched
    """
    return await asyncio.to_thread(_fetch_transcript_sync, video_id)
