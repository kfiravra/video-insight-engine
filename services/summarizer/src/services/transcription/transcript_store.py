"""Transcript storage service for S3 operations.

Provides business logic for storing and retrieving raw transcripts,
wrapping the S3 client with transcript-specific operations.

Note: S3 storage is optional - operations will fail gracefully if unavailable.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.models.schemas import TranscriptSegment, TranscriptSource
from src.services.media.s3_client import S3Client, s3_client

logger = logging.getLogger(__name__)

_AUDIO_SOURCES: frozenset[str] = frozenset({"whisper", "gemini"})


class CaptionRef(BaseModel):
    """A yt-dlp caption track by its caption key and bucket."""

    model_config = ConfigDict(frozen=True)

    lang: str  # caption key as yt-dlp lists it, e.g. "iw", "en-US", "en-orig"
    kind: str  # "manual" | "auto-generated"


def caption_ref(caption_lang: str | None, caption_kind: str | None) -> CaptionRef | None:
    """The picked track as a ``CaptionRef``; None when no track was picked.

    Anything but two strings (no pick, or a test double's attribute) is no pick.
    """
    if isinstance(caption_lang, str) and isinstance(caption_kind, str):
        return CaptionRef(lang=caption_lang, kind=caption_kind)
    return None


class RawTranscript(BaseModel):
    """Full transcript stored in S3 for regeneration."""

    youtube_id: str
    fetched_at: str  # ISO format
    source: TranscriptSource
    language: str | None
    segments: list[dict[str, Any]]  # List of {text, startMs, endMs}
    # The yt-dlp caption track the segments came from. None for every other
    # source and for blobs stored before the 2.4 caption picker.
    caption: CaptionRef | None = None

    def holds_other_caption_track(self, picked: CaptionRef | None) -> bool:
        """True when a caption-origin blob is not the track this run picked.

        The blob is keyed by video id only and read before every other layer,
        so a video processed before a picker change would otherwise serve its
        old track forever. Audio transcriptions (Whisper / Gemini) do not
        depend on the picker and never count as another track.
        """
        if self.source in _AUDIO_SOURCES:
            return False
        return self.caption != picked


class TranscriptStoreService:
    """Service for storing and retrieving transcripts from S3."""

    def __init__(self):
        self._s3 = s3_client
        self._bucket_verified = False  # Cache bucket existence check

    def _get_key(self, youtube_id: str) -> str:
        """Generate S3 key for a transcript (new path)."""
        return f"videos/{youtube_id}/transcript.json"

    async def store(
        self,
        youtube_id: str,
        segments: list[TranscriptSegment | dict[str, Any]],
        source: TranscriptSource,
        language: str | None = None,
        caption: CaptionRef | None = None,
    ) -> str:
        """
        Store a transcript in S3, replacing any blob already at the key.

        Args:
            youtube_id: YouTube video ID
            segments: List of transcript segments (Pydantic models or dicts)
            source: Source of the transcript (api, ytdlp, proxy, whisper)
            language: Language code if known
            caption: The yt-dlp caption track the segments came from (ytdlp only)

        Returns:
            S3 key for the stored transcript

        Raises:
            RuntimeError: If S3 storage is not available
        """
        if not S3Client.is_available():
            raise RuntimeError("S3 storage is not available (aiobotocore not installed)")

        key = self._get_key(youtube_id)

        # Convert Pydantic models to dicts if needed
        segment_dicts = []
        for seg in segments:
            if hasattr(seg, "model_dump"):
                segment_dicts.append(seg.model_dump())
            else:
                segment_dicts.append(seg)

        transcript = RawTranscript(
            youtube_id=youtube_id,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            language=language,
            segments=segment_dicts,
            caption=caption,
        )

        try:
            # Only check bucket existence once per service lifetime
            if not self._bucket_verified:
                await self._s3.ensure_bucket_exists()
                self._bucket_verified = True

            await self._s3.put_json(key, transcript.model_dump())
            logger.debug("Stored transcript in S3: %s (%d segments)", key, len(segments))
            return key
        except Exception as e:
            logger.warning("Failed to store transcript in S3: %s", e)
            raise

    async def get(self, youtube_id: str) -> RawTranscript | None:
        """
        Retrieve a transcript from S3.

        Args:
            youtube_id: YouTube video ID

        Returns:
            RawTranscript if found, None otherwise
        """
        key = self._get_key(youtube_id)
        data = await self._s3.get_json(key)

        if data is None:
            return None

        return RawTranscript(**data)

    async def get_by_ref(self, ref: str) -> RawTranscript | None:
        """
        Retrieve a transcript by S3 key reference.

        Args:
            ref: S3 key (e.g., "videos/abc123/transcript.json")

        Returns:
            RawTranscript if found, None otherwise
        """
        data = await self._s3.get_json(ref)

        if data is None:
            return None

        return RawTranscript(**data)

    async def exists(self, youtube_id: str) -> bool:
        """
        Check if a transcript exists in S3.

        Args:
            youtube_id: YouTube video ID

        Returns:
            True if transcript exists
        """
        key = self._get_key(youtube_id)
        return await self._s3.exists(key)

    async def delete(self, youtube_id: str) -> None:
        """
        Delete a transcript from S3.

        Args:
            youtube_id: YouTube video ID
        """
        key = self._get_key(youtube_id)
        await self._s3.delete(key)
        logger.info("Deleted transcript from S3: %s", key)


# Singleton instance
transcript_store = TranscriptStoreService()
