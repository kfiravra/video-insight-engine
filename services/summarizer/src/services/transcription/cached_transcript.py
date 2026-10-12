"""Priority 0 of the transcript chain: the S3 blob at ``videos/<id>/transcript.json``.

Read before every YouTube layer and keyed by video id only. A caption-origin
blob that holds a different track than the one this run's caption picker
chose is stale: the chain takes the fresh captions instead and assembly
re-stores the blob (``transcript_store.store`` overwrites the key).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.transcription.transcript_store import (
    RawTranscript,
    caption_ref,
    transcript_store,
)
from src.utils.language_utils import detect_language_by_script, detect_language_from_text

if TYPE_CHECKING:
    from src.services.video.youtube import VideoData

logger = logging.getLogger(__name__)


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


def _is_stale_caption_blob(cached: RawTranscript, video_data: VideoData) -> bool:
    """A caption-origin blob for a track other than the one this run picked.

    Judged only when this run's caption fetch produced subtitles: a failed
    fetch has nothing fresher to offer, so the blob is used as before. Blobs
    stored before the 2.4 picker record no track — stale once, then re-stored
    by assembly with this run's track.
    """
    if not video_data.subtitles:
        return False
    picked = caption_ref(video_data.caption_lang, video_data.caption_track)
    return cached.holds_other_caption_track(picked)


async def read_cached_transcript(
    youtube_id: str, video_data: VideoData, trail: TranscriptTrail
) -> TranscriptData | None:
    """The S3 blob as TranscriptData; None on a miss, a failed lookup or a stale blob.

    A failed lookup is recorded as ``s3`` in ``trail.attempted``, a stale blob
    as ``s3-stale``; either way the chain moves on to the next layer. A hit
    records the blob's source as ``trail.origin``.
    """
    try:
        cached = await transcript_store.get(youtube_id)
        if cached is None:
            return None
        if _is_stale_caption_blob(cached, video_data):
            trail.attempted.append("s3-stale")
            logger.info(
                "S3 transcript for %s holds caption %s; this run picked %s/%s — "
                "skipping it for this run's captions (re-stored by assembly)",
                youtube_id,
                f"{cached.caption.kind}/{cached.caption.lang}" if cached.caption else "unknown",
                video_data.caption_track,
                video_data.caption_lang,
            )
            return None
        data = _cached_transcript_data(cached)
    except Exception as e:
        trail.attempted.append("s3")
        logger.warning("S3 transcript retrieval failed, continuing with fallback chain: %s", e)
        return None
    logger.info("Using S3 cached transcript: %d segments", len(cached.segments))
    trail.origin = cached.source
    data.trail = trail
    return data
