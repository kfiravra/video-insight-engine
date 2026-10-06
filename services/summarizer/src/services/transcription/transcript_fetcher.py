"""Transcript fetching with multi-source fallback chain.

Provides the async generator ``fetch_transcript`` which tries each
transcript source in priority order:
  S3 cache -> yt-dlp subtitles -> youtube-transcript-api -> Whisper -> Gemini -> metadata

Extracted from ``src.routes.stream`` for maintainability.
"""

import asyncio
import logging
import time
from typing import AsyncGenerator

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.cache.caption_negative_cache import caption_negative_cache
from src.services.media.download_utils import ytdlp_proxy_exit_urls, ytdlp_proxy_url
from src.services.media.s3_client import S3Client
from src.services.pipeline.pipeline_helpers import (
    TranscriptData,
    TranscriptTrail,
    build_metadata_text,
    normalized_segments_to_pipeline,
    sse_event,
)
from src.services.transcription.gemini_transcriber import transcribe_with_gemini
from src.services.transcription.transcript import get_transcript
from src.services.transcription.transcript_store import RawTranscript, transcript_store
from src.services.transcription.whisper_transcriber import transcribe_with_whisper
from src.services.video.youtube import VideoData
from src.utils.language_utils import (
    detect_language_by_script,
    detect_language_from_text,
    normalize_language_code,
)

logger = logging.getLogger(__name__)

# Caption-fetch errors that must NOT fall back to audio transcription: genuine
# video-access failures where the audio download would also fail. Every other
# caption error (rate-limit 429, generic fetch errors) falls back to Whisper,
# since audio download is a different YouTube endpoint than the caption API.
_NO_AUDIO_FALLBACK = frozenset(
    {
        ErrorCode.VIDEO_UNAVAILABLE,
        ErrorCode.VIDEO_RESTRICTED,
        ErrorCode.LIVE_STREAM,
    }
)


def _audio_fallback_gates(duration: int) -> tuple[bool, bool]:
    """(whisper_allowed, gemini_allowed) for the audio fallback branch.

    The duration cap is branch-wide — it bounds audio-transcription *cost*,
    which applies to Gemini as much as Whisper. But WHISPER_ENABLED gates only
    Whisper: disabling it must not silently kill the Gemini path too.
    """
    over_cap = duration > settings.WHISPER_MAX_DURATION_MINUTES * 60
    whisper_allowed = settings.WHISPER_ENABLED and not over_cap
    gemini_allowed = bool(settings.GEMINI_API_KEY) and not over_cap
    return whisper_allowed, gemini_allowed


def _api_label() -> str:
    """Source label for the youtube-transcript-api layer: "proxy" or "api".

    ``get_transcript`` routes through YOUTUBE_PROXY_URL when it is set — the
    label uses the same predicate, so ``source``/``attempted`` never claim a
    proxy that never ran.
    """
    return "proxy" if ytdlp_proxy_url() else "api"


async def _handle_timedtext_429(video_data: VideoData) -> bool:
    """React to a metadata-phase timedtext 429; True = retry captions on the next exit.

    A 429 is IP-scoped. With more than one proxy exit configured the caption
    API layer gets its retry on the next exit (the marker is written only if
    that 429s too); with a single exit the marker is set right away so this
    run and the next ones go straight to audio.
    """
    if not getattr(video_data, "captions_rate_limited", False):
        return False
    if len(ytdlp_proxy_exit_urls()) > 1:
        logger.info("Timedtext 429 on the primary proxy exit — retrying captions on the next exit")
        return True
    await caption_negative_cache.mark()
    return False


def _cached_transcript_data(cached: RawTranscript) -> TranscriptData:
    """Convert an S3-cached transcript blob (startMs/endMs) to pipeline TranscriptData."""
    segments = []
    for seg in cached.segments:
        start_ms = seg.get("startMs", 0)
        end_ms = seg.get("endMs", start_ms)
        segments.append(
            {
                "text": seg.get("text", ""),
                "start": start_ms / 1000.0,
                "duration": (end_ms - start_ms) / 1000.0,
            }
        )
    raw_text = " ".join(seg["text"] for seg in segments)

    # Detect language from cached transcript if not stored
    cached_language = cached.language
    if not cached_language and raw_text:
        cached_language = detect_language_from_text(raw_text)
        if not cached_language:
            cached_language = detect_language_by_script(raw_text)
        if cached_language:
            logger.info("Detected language from S3 cache text: %s", cached_language)

    return TranscriptData(
        segments=segments,
        raw_text=raw_text,
        transcript_type=f"cached-{cached.source}",
        source="s3",
        language=cached_language,
    )


def _ytdlp_transcript_data(video_data: VideoData) -> TranscriptData:
    """Build TranscriptData from the caption track yt-dlp fetched in the metadata phase."""
    segments = [
        {"text": seg.text, "start": seg.start, "duration": seg.duration}
        for seg in video_data.subtitles
    ]
    logger.info("Using yt-dlp subtitles: %d segments", len(segments))

    # Detect language from yt-dlp video metadata or subtitle text
    ytdlp_language = normalize_language_code(getattr(video_data, "language", None))
    if not ytdlp_language and video_data.transcript_text:
        ytdlp_language = detect_language_from_text(video_data.transcript_text)
        if not ytdlp_language:
            ytdlp_language = detect_language_by_script(video_data.transcript_text)

    # The picked track kind ("manual" / "auto-generated") is the honest
    # transcript_type; callers that predate the field carry no string here.
    caption_track = getattr(video_data, "caption_track", None)
    transcript_type = caption_track if isinstance(caption_track, str) else "auto-generated"

    return TranscriptData(
        segments=segments,
        raw_text=video_data.transcript_text,
        transcript_type=transcript_type,
        source="ytdlp",
        language=ytdlp_language,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


async def fetch_transcript(
    youtube_id: str,
    video_data: VideoData,
    duration: int,
    is_music: bool = False,
    *,
    trail: TranscriptTrail | None = None,
) -> AsyncGenerator[str | TranscriptData, None]:
    """
    Fetch transcript using the fallback chain:
    S3 cache -> yt-dlp captions -> youtube-transcript-api -> Whisper -> Gemini
    -> metadata (music only).

    Yields SSE events for phase changes, then yields TranscriptData as final item.

    Args:
        youtube_id: YouTube video ID
        video_data: Extracted video data
        duration: Video duration in seconds
        is_music: If True, use music-specific transcription prompts
        trail: Provenance trail to fill in as layers run; attached to the yielded
            TranscriptData and kept by the caller even when every layer fails
    """
    if trail is None:
        trail = TranscriptTrail()

    # Priority 0: S3 cached transcript (avoids all YouTube calls)
    if S3Client.is_available():
        try:
            cached = await transcript_store.get(youtube_id)
            if cached:
                logger.info("Using S3 cached transcript: %d segments", len(cached.segments))
                yield sse_event("phase", {"phase": "transcript_cached"})
                trail.origin = cached.source
                data = _cached_transcript_data(cached)
                data.trail = trail
                yield data
                return
        except Exception as e:
            trail.attempted.append("s3")
            logger.warning("S3 transcript retrieval failed, continuing with fallback chain: %s", e)

    # Priority 1: yt-dlp subtitles
    if video_data.subtitles:
        data = _ytdlp_transcript_data(video_data)
        data.trail = trail
        yield data
        return

    # A caption track was picked but its timedtext fetch yielded nothing: the
    # yt-dlp layer genuinely ran and failed (no track picked is not an attempt).
    if isinstance(getattr(video_data, "caption_track", None), str):
        trail.attempted.append("ytdlp")

    # The metadata phase's timedtext fetch just 429'd. With spare proxy exits
    # the caption API below retries on the next one; otherwise record the 429
    # so this run and the next ones stop hammering the throttled endpoints.
    skip_primary_exit = await _handle_timedtext_429(video_data)

    # Recent caption 429 on record (IP-scoped): the caption API below is
    # doomed, so skip its 30-60s of retries and go straight to audio.
    if await caption_negative_cache.is_marked():
        trail.caption_api_skipped = True
        whisper_allowed, gemini_allowed = _audio_fallback_gates(duration)
        if not whisper_allowed and not gemini_allowed:
            raise TranscriptError(
                "Caption endpoints rate-limited and no audio fallback is available",
                ErrorCode.NO_TRANSCRIPT,
            )
        logger.info(
            "Caption endpoints negative-cached (recent 429), skipping to audio for %s",
            youtube_id,
        )
    else:
        # Priority 3: youtube-transcript-api
        logger.info("yt-dlp subtitles not available, falling back to youtube-transcript-api")
        yield sse_event("phase", {"phase": "transcript"})
        api_label = _api_label()
        try:
            segments, raw_text, transcript_type, api_language = await asyncio.wait_for(
                get_transcript(youtube_id, skip_primary_exit=skip_primary_exit),
                timeout=settings.TRANSCRIPT_FETCH_TIMEOUT,
            )
            data = TranscriptData(
                segments=segments,
                raw_text=raw_text,
                transcript_type=transcript_type,
                source=api_label,
                language=normalize_language_code(api_language),
            )
            data.trail = trail
            yield data
            return
        except asyncio.TimeoutError:
            trail.attempted.append(api_label)
            logger.warning(
                "get_transcript timed out after %ss for %s",
                settings.TRANSCRIPT_FETCH_TIMEOUT,
                youtube_id,
            )
            whisper_allowed, gemini_allowed = _audio_fallback_gates(duration)
            if not whisper_allowed and not gemini_allowed:
                raise TranscriptError(
                    "Transcript fetch timed out and no audio fallback is available "
                    "(Whisper disabled/over-duration, no Gemini key)",
                    ErrorCode.NO_TRANSCRIPT,
                )
            # Fall through to audio transcription
        except TranscriptError as e:
            # Recorded before any re-raise so a video-unavailable abort still
            # shows the API layer ran.
            trail.attempted.append(api_label)
            # Priority 4: audio fallback. Whisper downloads AUDIO — a different
            # YouTube endpoint than the throttled caption API — so transient
            # caption failures (rate-limit 429, generic fetch errors) should
            # still fall back to audio transcription. Only genuine video-access
            # failures, where the audio download would also fail, abort here.
            if e.code is ErrorCode.RATE_LIMITED:
                await caption_negative_cache.mark()
            if e.code in _NO_AUDIO_FALLBACK:
                raise
            whisper_allowed, gemini_allowed = _audio_fallback_gates(duration)
            if not whisper_allowed and not gemini_allowed:
                logger.warning(
                    "Caption fetch failed (%s) and no audio fallback is available",
                    e.code.value,
                )
                raise
            logger.info(
                "Caption fetch failed (%s); falling back to audio transcription", e.code.value
            )

    # Audio transcription fallback chain: Whisper (detects language) -> Gemini (fast)
    # Whisper comes first because it natively returns response.language for detection.
    duration_min = duration // 60
    if duration_min > 180:
        logger.warning(
            "Audio transcription requested for long video %s (%d min). "
            "Estimated cost: ~$%.2f Whisper API.",
            youtube_id,
            duration_min,
            duration_min * 0.006,
        )
    logger.info("No captions for %s, trying audio transcription", youtube_id)
    yield sse_event("phase", {"phase": "audio_transcription"})

    # Try Whisper first -- detects language natively via response.language
    whisper_allowed, gemini_allowed = _audio_fallback_gates(duration)
    whisper_data: TranscriptData | None = None
    whisper_error: TranscriptError | None = None
    if whisper_allowed:
        yield sse_event("phase", {"phase": "whisper_transcription"})
        whisper_data, whisper_error = await _try_whisper_transcription(
            youtube_id, duration, is_music
        )
        if whisper_data is None:
            trail.attempted.append("whisper")
    if whisper_data is not None:
        whisper_data.trail = trail
        yield whisper_data
        return

    # Gemini fallback -- faster/cheaper but no language detection
    gemini_data = (
        await _try_gemini_transcription(youtube_id, duration, is_music) if gemini_allowed else None
    )
    if gemini_allowed and gemini_data is None:
        trail.attempted.append("gemini")
    if gemini_data is not None:
        gemini_data.trail = trail
        yield gemini_data
        return

    # Metadata fallback -- only for music videos when all transcript sources fail.
    # Yields empty segments intentionally: sponsor filtering and AI chapter detection
    # are skipped for metadata-only transcripts; only raw_text is used downstream.
    if is_music:
        logger.info(
            "All transcript sources failed for music video %s, using metadata fallback", youtube_id
        )
        yield sse_event("phase", {"phase": "metadata_fallback"})
        metadata_text = build_metadata_text(video_data)
        data = TranscriptData(
            segments=[],
            raw_text=metadata_text,
            transcript_type="metadata",
            source="metadata",
        )
        data.trail = trail
        yield data
        return

    # Not a music video -- raise the last error from the fallback chain.
    # whisper_error is None when Whisper is disabled and Gemini was the only audio path.
    if whisper_error is not None:
        raise whisper_error
    raise TranscriptError("All transcript sources failed", ErrorCode.NO_TRANSCRIPT)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _try_gemini_transcription(
    youtube_id: str,
    duration: int,
    is_music: bool,
) -> TranscriptData | None:
    """Attempt Gemini audio transcription. Returns TranscriptData on success, None on failure."""
    if not settings.GEMINI_API_KEY:
        return None

    gemini_timeout = min(max(120.0, duration * 0.02 + 60), 300.0)
    logger.info(
        "Trying Gemini transcription (timeout: %ds) for %ds video",
        int(gemini_timeout),
        duration,
    )
    try:
        gemini_result = await asyncio.wait_for(
            transcribe_with_gemini(youtube_id, is_music=is_music),
            timeout=gemini_timeout,
        )
        segments = normalized_segments_to_pipeline(gemini_result.segments)
        # The transcriber now detects and carries the source language (it keeps
        # the transcript in its original script). Honor that first; fall back to
        # detecting from text as a safety net so a non-English video is never
        # silently defaulted to "en" — that would skip the translation phase and
        # hide the FE language toggle.
        gemini_language = (
            gemini_result.language
            or detect_language_from_text(gemini_result.text)
            or detect_language_by_script(gemini_result.text)
        )
        logger.info(
            "Gemini transcription successful: %d segments (language=%s)",
            len(segments),
            gemini_language,
        )
        return TranscriptData(
            segments=segments,
            raw_text=gemini_result.text,
            transcript_type="gemini",
            source="gemini",
            language=gemini_language,
        )
    except (TranscriptError, asyncio.TimeoutError) as e:
        logger.warning("Gemini transcription failed, falling back to Whisper: %s", e)
    except Exception as e:
        logger.error("Unexpected Gemini error: %s", e, exc_info=True)
    return None


async def _try_whisper_transcription(
    youtube_id: str,
    duration: int,
    is_music: bool,
) -> tuple[TranscriptData | None, TranscriptError | None]:
    """Attempt Whisper transcription. Returns (data, None) on success or (None, error) on failure."""
    logger.info("Trying Whisper fallback for %s", youtube_id)
    # Budget scales with duration so long videos get enough time to transcribe
    # every chunk. Minimum 5 min, max 15 min. The budget is enforced as an
    # internal per-chunk *deadline* inside transcribe_with_whisper (it returns
    # the chunks it finished instead of being hard-cancelled), so a long video
    # yields a partial-but-real transcript rather than falling through to the
    # truncating Gemini path. The outer wait_for is only a generous backstop for
    # a single hung API call; keeping it well above the deadline avoids the
    # cancel-mid-thread race that deletes chunk files an orphaned worker is
    # still reading.
    whisper_timeout = min(max(300.0, duration * 0.12 + 120), 900.0)
    deadline = time.monotonic() + whisper_timeout
    backstop = whisper_timeout + 300.0
    logger.info("Whisper budget set to %ds for %ds video", int(whisper_timeout), duration)

    try:
        whisper_result = await asyncio.wait_for(
            transcribe_with_whisper(youtube_id, is_music=is_music, deadline=deadline),
            timeout=backstop,
        )
    except asyncio.TimeoutError:
        return None, TranscriptError(
            f"Whisper transcription exceeded backstop of {int(backstop)}s",
            ErrorCode.UNKNOWN_ERROR,
        )
    except TranscriptError as e:
        return None, e
    except Exception as e:
        logger.error("Whisper transcription failed unexpectedly: %s", e, exc_info=True)
        return None, TranscriptError(
            f"Whisper transcription failed: {e}",
            ErrorCode.UNKNOWN_ERROR,
        )

    segments = normalized_segments_to_pipeline(whisper_result.segments)
    logger.info(
        "Whisper fallback successful: %d segments, language=%s",
        len(segments),
        whisper_result.language,
    )
    return TranscriptData(
        segments=segments,
        raw_text=whisper_result.text,
        transcript_type="whisper",
        source="whisper",
        language=normalize_language_code(whisper_result.language),
    ), None
