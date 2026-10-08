"""Pipeline helper functions for the summarization pipeline.

Contains self-contained utilities:
- SSE event formatting
- Pipeline timer
- TranscriptData dataclass
- Duration validation
- Segment normalization
- Metadata text builder
- JSON truncation
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

if TYPE_CHECKING:
    from .context import PipelineContext

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode, TranscriptSegment
from src.models.sse_events import validate_sse_event
from src.services.pipeline.pipeline_timing import timed_step

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SSE Event Helpers
# ─────────────────────────────────────────────────────────────────────────────


def sse_event(event: str, data: dict[str, Any]) -> str:
    """Format data as an SSE event, validating it against the Pydantic
    contract in ``src/models/sse_events.py`` (raises outside production,
    logs-and-emits in production — see that module's docstring)."""
    validate_sse_event(event, data)
    return f"data: {json.dumps({'event': event, **data})}\n\n"


def truncate_json_safely(data: Any, max_chars: int) -> str:
    """Serialize JSON compactly and truncate at a structural boundary.

    Avoids cutting mid-string which would produce invalid JSON context
    for downstream prompts. Handles both objects and arrays.
    """
    serialized = json.dumps(data, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized
    truncated = serialized[:max_chars]
    last_comma = truncated.rfind(",")
    if last_comma > max_chars * 0.5:
        truncated = truncated[:last_comma]
    closer = "]" if isinstance(data, list) else "}"
    return truncated + closer


def sse_token(phase: str, token: str, **extra: Any) -> str:
    """Format token as SSE event (validated like sse_event)."""
    validate_sse_event("token", {"phase": phase, "token": token, **extra})
    return f"data: {json.dumps({'event': 'token', 'phase': phase, 'token': token, **extra})}\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Timer
# ─────────────────────────────────────────────────────────────────────────────


class PipelineTimer:
    """Lightweight phase timer for pipeline observability."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed(self) -> float:
        """Seconds since pipeline start."""
        return time.monotonic() - self._start

    def elapsed_str(self) -> str:
        """Formatted elapsed time for logging."""
        return f"{self.elapsed():.1f}s"


# ─────────────────────────────────────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TranscriptTrail:
    """Provenance of one transcript-fetch run, independent of its outcome.

    The fetcher appends to ``attempted`` as each layer runs and fails, so the
    list survives a run where every layer failed and no ``TranscriptData``
    ever existed. The transcript phase stamps ``fetch_wall_ms`` and
    ``error_code`` in a ``finally`` and hangs the trail on the context; the
    runner persists it as ``transcriptMeta`` for successful AND failed runs.
    """

    # Layers that RAN and FAILED before the winning source, in order. Values:
    # "s3" (lookup raised — a miss is not an attempt), "ytdlp" (a caption
    # track was picked but the timedtext fetch yielded nothing), "api" /
    # "proxy" (youtube-transcript-api, labelled by whether YOUTUBE_PROXY_URL
    # is set), "whisper", "gemini".
    attempted: list[str] = field(default_factory=list)
    # youtube-transcript-api was skipped by the Redis caption-429 marker.
    caption_api_skipped: bool = False
    # S3-hit runs only: the ``source`` recorded in the cached blob — the layer
    # that originally produced the transcript. ``"s3"`` here means the blob
    # had already decayed before the assembly re-store skip landed.
    origin: str | None = None
    # Wall time of the transcript phase's fetch (frames run in parallel).
    fetch_wall_ms: int | None = None
    # ``ErrorCode.value`` of the TranscriptError that ended the fetch, if any.
    error_code: str | None = None


@dataclass
class TranscriptData:
    """Holds transcript data from any source.

    Note: when ``source="metadata"``, ``segments`` is intentionally empty.
    """

    segments: list[dict[str, Any]]
    raw_text: str
    transcript_type: str
    source: str  # ytdlp, api, proxy, whisper, gemini, metadata, s3
    language: str | None = None  # ISO 639-1 code (e.g., "en", "he")
    # Attached by the fetcher AFTER construction (keeps every existing
    # keyword-only construction site and test-built instance valid).
    trail: TranscriptTrail | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Segment Conversion & Normalization
# ─────────────────────────────────────────────────────────────────────────────


def normalized_segments_to_pipeline(
    segments: list[TranscriptSegment],
) -> list[dict[str, Any]]:
    """Convert NormalizedTranscript segments (startMs/endMs) to pipeline format (start/duration in seconds)."""
    return [
        {
            "text": s.text,
            "start": s.startMs / 1000.0,
            "duration": (s.endMs - s.startMs) / 1000.0,
        }
        for s in segments
    ]


def normalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert segments to normalized format with milliseconds."""
    normalized = []
    for seg in segments:
        if "startMs" in seg:
            start_ms = int(seg["startMs"])
            end_ms = int(seg.get("endMs", start_ms))
        else:
            start_s = seg.get("start", 0)
            duration_s = seg.get("duration", 0)
            start_ms = int(start_s * 1000)
            end_ms = int((start_s + duration_s) * 1000)

        normalized.append(
            {
                "text": seg.get("text", ""),
                "startMs": start_ms,
                "endMs": end_ms,
            }
        )
    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# Duration Validation
# ─────────────────────────────────────────────────────────────────────────────


def validate_duration(duration: int) -> None:
    """Validate video duration against limits."""
    if duration > settings.MAX_VIDEO_DURATION_MINUTES * 60:
        raise TranscriptError(f"Video too long ({duration // 60} min)", ErrorCode.VIDEO_TOO_LONG)
    if duration < settings.MIN_VIDEO_DURATION_SECONDS:
        raise TranscriptError(f"Video too short ({duration} sec)", ErrorCode.VIDEO_TOO_SHORT)


# ─────────────────────────────────────────────────────────────────────────────
# Metadata Text Builder
# ─────────────────────────────────────────────────────────────────────────────


def build_metadata_text(video_data: Any) -> str:
    """Build a text representation from video metadata for fallback summarization."""
    parts: list[str] = []
    parts.append(f"Title: {video_data.title}")
    if video_data.channel:
        parts.append(f"Channel: {video_data.channel}")
    if video_data.description:
        desc = video_data.description[:2000]
        parts.append(f"Description: {desc}")
    if video_data.context and video_data.context.tags:
        parts.append(f"Tags: {', '.join(video_data.context.tags[:20])}")
    if video_data.has_chapters:
        chapter_titles = [ch.title for ch in video_data.chapters]
        parts.append(f"Chapters: {', '.join(chapter_titles)}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Sanitization
# ─────────────────────────────────────────────────────────────────────────────


def sanitize_for_prompt(text: str, max_len: int = 500) -> str:
    """Sanitize user-controlled text before injecting into prompt templates.

    Strips curly braces (template placeholders) and angle brackets (XML-tag
    injection in prompt sections like <transcript>) and truncates to prevent
    prompt bloat. Use for video titles, descriptions, channel names, and any
    other user-controlled metadata injected into LLM prompts.

    Angle brackets are replaced with Unicode look-alikes (‹›) rather than
    stripped so that titles like "React <Suspense>" remain readable in
    prompts while preventing XML-tag injection into prompt sections.
    """
    sanitized = text.replace("{", "").replace("}", "").replace("<", "‹").replace(">", "›")
    return sanitized[:max_len]


# ─────────────────────────────────────────────────────────────────────────────
# Parallel Phase Runner
# ─────────────────────────────────────────────────────────────────────────────

_SENTINEL = object()


async def run_parallel_phases(
    phases: list[Callable[["PipelineContext"], AsyncGenerator[str, None]]],
    ctx: "PipelineContext",
) -> AsyncGenerator[str, None]:
    """Run multiple pipeline phases in parallel, yielding SSE events in arrival order.

    Uses an asyncio.Queue so events stream to the frontend in real-time
    as each phase produces them (no buffering).

    If any phase raises an exception, remaining phases are cancelled
    and the exception is re-raised to the caller.  Any SSE events already
    queued by non-failed phases are discarded — this is intentional since
    the pipeline cannot continue after a phase failure.
    """
    queue: asyncio.Queue[str | BaseException | object] = asyncio.Queue()

    async def _run_phase(phase_fn: Callable) -> None:
        phase_name = getattr(phase_fn, "__name__", str(phase_fn)).removeprefix("run_phase_")
        try:
            with timed_step(phase_name):
                async for event in phase_fn(ctx):
                    await queue.put(event)
        except Exception as exc:
            # Annotate exception with phase name for debuggability
            exc.add_note(f"Failed in parallel phase: {phase_name}")
            await queue.put(exc)
        finally:
            await queue.put(_SENTINEL)

    tasks = [asyncio.create_task(_run_phase(phase)) for phase in phases]

    try:
        completed = 0
        while completed < len(tasks):
            # Emit a keepalive when no phase produces an event within the
            # heartbeat window. A long silent phase (multi-minute Whisper runs
            # entirely inside one asyncio.to_thread) otherwise sends zero bytes,
            # and the API gateway's undici proxy aborts the idle-but-live SSE
            # connection at its 300s bodyTimeout. asyncio.Queue.get is
            # cancellation-safe, so the timed-out get drops no queued item.
            try:
                item = await asyncio.wait_for(queue.get(), timeout=settings.SSE_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield sse_event("heartbeat", {"ts": time.monotonic()})
                continue
            if item is _SENTINEL:
                completed += 1
            elif isinstance(item, BaseException):
                for t in tasks:
                    t.cancel()
                raise item
            else:
                yield item  # type: ignore[misc]
    finally:
        # Ensure all tasks are cleaned up and exceptions consumed
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
