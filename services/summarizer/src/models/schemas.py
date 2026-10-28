from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel

# Valid LLM provider names
Provider = Literal["anthropic", "openai", "gemini"]

# Transcript source types for tracking fetch method
TranscriptSource = Literal["ytdlp", "api", "proxy", "whisper", "gemini", "metadata", "s3"]


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCode(str, Enum):
    NO_TRANSCRIPT = "NO_TRANSCRIPT"
    VIDEO_TOO_LONG = "VIDEO_TOO_LONG"
    VIDEO_TOO_SHORT = "VIDEO_TOO_SHORT"
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    VIDEO_RESTRICTED = "VIDEO_RESTRICTED"
    LIVE_STREAM = "LIVE_STREAM"
    LLM_ERROR = "LLM_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


# ─────────────────────────────────────────────────────
# Normalized Transcript Types
# ─────────────────────────────────────────────────────


class TranscriptSegment(BaseModel):
    """Normalized transcript segment with millisecond timestamps."""

    text: str
    startMs: int
    endMs: int


class NormalizedTranscript(BaseModel):
    """Unified transcript format from any source."""

    text: str
    segments: list[TranscriptSegment]
    source: TranscriptSource
    language: str | None = None  # ISO 639-1 code detected by transcriber


class ProviderConfig(BaseModel):
    """LLM provider configuration for dev tools override."""

    default: Provider
    fast: Provider | None = None
    fallback: Provider | None = None


class SummarizeRequest(BaseModel):
    videoSummaryId: str
    youtubeId: str
    url: str
    userId: str | None = None
    providers: ProviderConfig | None = None


class SummarizeResponse(BaseModel):
    status: str
    videoSummaryId: str


class Chapter(BaseModel):
    """A chapter in the video summary (formerly Section)."""
    id: str
    timestamp: str
    start_seconds: int
    end_seconds: int
    title: str
    summary: str
    bullets: list[str]
    content: list[dict] = []  # Dynamic content blocks with blockId


# Backwards compatibility alias
Section = Chapter


class Concept(BaseModel):
    id: str
    name: str
    definition: Optional[str] = None
    timestamp: Optional[str] = None
    aliases: list[str] = []
    chapter_index: int | None = None


class VideoSummary(BaseModel):
    tldr: str
    key_takeaways: list[str]
    chapters: list[Chapter]
    concepts: list[Concept]
    master_summary: Optional[str] = None


class ProcessingResult(BaseModel):
    title: str
    channel: Optional[str] = None
    duration: Optional[int] = None
    thumbnail_url: Optional[str] = None
    transcript: str
    transcript_type: str
    summary: VideoSummary
    token_usage: dict


# ─────────────────────────────────────────────────────
# Playlist Extraction Types
# ─────────────────────────────────────────────────────


class PlaylistExtractRequest(BaseModel):
    """Request to extract playlist metadata."""
    playlist_id: str
    max_videos: int = 100


class PlaylistVideoInfo(BaseModel):
    """Information about a single video in a playlist."""
    video_id: str
    title: str
    position: int
    duration: int | None = None
    thumbnail_url: str | None = None


class PlaylistExtractResponse(BaseModel):
    """Response from playlist extraction."""
    playlist_id: str
    title: str
    channel: str | None = None
    thumbnail_url: str | None = None
    total_videos: int
    videos: list[PlaylistVideoInfo]


# ─────────────────────────────────────────────────────
# Pre-flight Video Metadata (GET /video/metadata)
# ─────────────────────────────────────────────────────


class VideoMetadataResponse(BaseModel):
    """What the API needs to quote a video before submitting it: no captions, no LLM."""
    youtubeId: str
    title: str
    channel: str
    duration: int  # seconds, always > 0
    thumbnailUrl: str | None = None
