"""Extraction chunks — the transcript slices the split extraction fires its groups over.

Every group call of a chunk shares that chunk's cached head block (``<video>``
+ the chunk's transcript + ``<video_memory>``), so a chunk is also the unit of
the warm-up: its own text1 call writes the head, the rest read it.

A video one extraction batch covers (``needs_chapter_batching`` false: at
most ``MAX_MINUTES_PER_BATCH`` and ``MAX_TOKENS_PER_BATCH``) is ONE chunk —
the whole marked transcript, no batch context, every annotation — with no
chapter work and no wait for the description. A longer video is cut the way
today's chunked extraction cuts it (pipeline-1min 3.7, A13): today's chapter
chain (creator chapters → description timestamps → the memory outline;
chapter_detect only without a usable outline) merged by ``batch_chapters``.
Each batch is a chunk with today's chapter-headed transcript, its
``<batch_partial_context>`` and the annotation window today's batches read —
from its first marker to the next chunk's, open at both video ends. When the
chain gives no more than one batch (it raised, or there are no segments to
cut) the transcript is force-split by sentences into enough parts for both
limits, one chunk per part — today's force-split path; one chunk only when
that split gives fewer than two parts. At frames-done each chunk's key frames
are formatted from the gallery frames in its window (``key_frames_by_chunk``).

The window also bounds what a chunk's call may stamp (output-quality 0b.10, R10 /
L21): ``window_stamps`` keeps an item's ``timestamp`` / ``seconds`` only inside the
chunk's window (a marker-interval of grace at both edges). A stamp outside it is
the model reading the chunk's ``[h:mm:ss]`` markers without the hour (S02 chunk 3
stamped 1:13:41 as 13:41 = 821 s), so the hour is put back when that lands the
stamp inside the window — once, deterministically; a stamp no hour fits is
cleared (an annotated item stays) or, when the stamp is the item (``seconds``,
``learning.timestamps``), the item is dropped. One chunk is open at both ends:
nothing is checked and the data is returned as is (the ≤ 40-min replay stays
byte-identical).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...config import settings
from ..transcript.render import format_marker
from ..transcription.transcript_chunker import (
    _estimate_tokens,
    force_split_by_sentences,
    needs_chapter_batching,
)
from .extraction_prompt import slice_visual_annotations
from .extractor import (
    _batch_start_seconds,
    _build_batch_context,
    _build_batch_transcript,
    batch_chapters,
)
from .prompt_builder import format_gallery_frames_for_extraction

if TYPE_CHECKING:
    from ..transcription.transcript_chunker import ChapterChunk
    from .context import PipelineContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionChunk:
    """One transcript slice: its text, its ``<batch_partial_context>`` line, its annotation window.

    ``start_s`` / ``end_s`` bound the ``<visual_annotations>`` entries and the
    key frames the chunk's visual group reads (``None`` = open at that side);
    ``video_end_s`` (the last chapter's end) caps what the open-ended last
    chunk may stamp.
    """

    index: int
    transcript: str
    batch_context: str = ""
    start_s: float | None = None
    end_s: float | None = None
    video_end_s: float | None = None


# ─── Planning ───


def _duration(ctx: PipelineContext) -> float:
    return float(getattr(ctx.video_data, "duration", 0) or 0)


def _needs_chunks(ctx: PipelineContext) -> bool:
    """Whether one extraction batch cannot cover the video (chapters need a transcript)."""
    return bool(ctx.transcript_data) and needs_chapter_batching(
        _duration(ctx), ctx.prompt_transcript
    )


async def _chapter_batches(ctx: PipelineContext) -> list[list[ChapterChunk]]:
    """Today's chapter chain (sets ``ctx.chapters``), merged into extraction batches."""
    # The phase sits above this module (and imports it): bound on the long-video path only.
    from .phases.extraction import _split_chapters, extraction_video_info

    chapters = await _split_chapters(ctx, extraction_video_info(ctx), ctx.prompt_transcript)
    return batch_chapters(chapters) if chapters else []


def _forced_batches(transcript: str, duration: float) -> list[list[ChapterChunk]]:
    """Today's force-split: sentence-bounded parts, enough for both batch limits, one per batch."""
    parts = max(
        2,
        math.ceil(duration / (settings.MAX_MINUTES_PER_BATCH * 60)),
        math.ceil(_estimate_tokens(transcript) / settings.MAX_TOKENS_PER_BATCH),
    )
    return [[part] for part in force_split_by_sentences(transcript, duration, target_chunks=parts)]


def _batch_chunks(batches: list[list[ChapterChunk]]) -> list[ExtractionChunk]:
    """One chunk per batch: today's batch transcript, context and annotation window."""
    full_duration = max(ch.end_seconds for batch in batches for ch in batch)
    opens = [_batch_start_seconds(batch) for batch in batches]
    last = len(batches) - 1
    return [
        ExtractionChunk(
            index=k,
            transcript=_build_batch_transcript(batch),
            batch_context=_build_batch_context(k, len(batches), batch, full_duration),
            start_s=opens[k] if k else None,
            end_s=opens[k + 1] if k < last else None,
            video_end_s=full_duration,
        )
        for k, batch in enumerate(batches)
    ]


async def plan_chunks(ctx: PipelineContext) -> list[ExtractionChunk]:
    """The run's chunks: one when a single batch covers the video, else one per chapter batch."""
    whole = [ExtractionChunk(index=0, transcript=ctx.prompt_transcript)]
    if not _needs_chunks(ctx):
        return whole
    batches = await _chapter_batches(ctx)
    if len(batches) <= 1:
        batches = _forced_batches(ctx.prompt_transcript, _duration(ctx))
    if len(batches) <= 1:
        logger.warning(
            "Split extraction: a long video neither chapters nor force-splits — one chunk "
            "(video_id=%s)",
            ctx.video_summary_id,
        )
        return whole
    chunks = _batch_chunks(batches)
    logger.info(
        "Split extraction: %d chapters (%s) in %d chunks (video_id=%s)",
        sum(len(batch) for batch in batches),
        batches[0][0].source,
        len(chunks),
        ctx.video_summary_id,
    )
    return chunks


# ─── Per-chunk frame inputs ───


def _in_window(seconds: float, chunk: ExtractionChunk) -> bool:
    return (chunk.start_s is None or seconds >= chunk.start_s) and (
        chunk.end_s is None or seconds < chunk.end_s
    )


def chunk_annotations(block: str, chunk: ExtractionChunk) -> str:
    """The chunk's slice of a ``<visual_annotations>`` block; the whole block for an open window."""
    if chunk.start_s is None and chunk.end_s is None:
        return block
    return slice_visual_annotations(block, chunk.start_s, chunk.end_s)


def key_frames_by_chunk(
    gallery: list[dict], descriptions: list[dict] | None, chunks: Sequence[ExtractionChunk]
) -> dict[int, str]:
    """Each chunk's ``<key_frames>`` lines, formatted from the gallery frames in its window
    (the same cap per chunk as the whole video gets); ``{}`` for one chunk — it reads the
    whole video's."""
    if len(chunks) <= 1:
        return {}
    return {
        chunk.index: format_gallery_frames_for_extraction(
            [f for f in gallery if _in_window(float(f.get("timestamp", 0) or 0), chunk)],
            descriptions,
        )
        for chunk in chunks
    }


# ─── Per-chunk timestamp range (0b.10) ───

# Keys an extracted item is stamped under. ``seconds`` is the stamp of an item that IS
# a timestamp (``learning.timestamps``); the others annotate an item that stands alone.
_STAMP_KEYS = ("timestamp", "endTimestamp", "seconds", "endSeconds")
_ITEM_IS_STAMP = "seconds"
_CLOCK_KEY = "time"
# A window opens at its first marker (one every 20 s) and a chapter edge may sit a few
# seconds before it: a stamp this close outside the window is at the edge, not off-clock.
WINDOW_GRACE_S = 30.0
_HOUR_S = 3600
_MAX_HOURS = 24
_CLOCK_PATTERN = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")


@dataclass
class StampReport:
    """What ``window_stamps`` did to one call's data."""

    restamped: int = 0
    cleared: int = 0
    # Per field path (``learning.timestamps``): the items dropped and the list's size,
    # the shape ``ValidationReport.dropped`` holds.
    dropped: dict[str, dict[str, int]] = field(default_factory=dict)

    def drop(self, path: str, lost: int, total: int) -> None:
        counts = self.dropped.setdefault(path, {"dropped": 0, "total": 0})
        counts["dropped"] += lost
        counts["total"] += total

    @property
    def changed(self) -> bool:
        return bool(self.restamped or self.cleared or self.dropped)


def _window_open(chunk: ExtractionChunk) -> bool:
    return chunk.start_s is None and chunk.end_s is None


def _holds(seconds: int, chunk: ExtractionChunk) -> bool:
    """Whether ``seconds`` lies in the chunk's window, a grace interval at both edges."""
    after_start = chunk.start_s is None or seconds >= chunk.start_s - WINDOW_GRACE_S
    end = chunk.end_s if chunk.end_s is not None else chunk.video_end_s
    return after_start and (end is None or seconds < end + WINDOW_GRACE_S)


def restamp(seconds: int, chunk: ExtractionChunk) -> int | None:
    """``seconds`` inside the window; else the hour-restored clock that lands inside it;
    else None (no marker of the chunk reads that way)."""
    if _holds(seconds, chunk):
        return seconds
    for hours in range(1, _MAX_HOURS + 1):
        candidate = seconds + hours * _HOUR_S
        if _holds(candidate, chunk):
            return candidate
    return None


def _clock_seconds(text: Any) -> int | None:
    """The seconds a ``m:ss`` / ``h:mm:ss`` clock string reads; None for anything else."""
    found = _CLOCK_PATTERN.match(text) if isinstance(text, str) else None
    if found is None:
        return None
    hours, minutes, secs = found.groups()
    return int(hours or 0) * _HOUR_S + int(minutes) * 60 + int(secs)


def _is_stamp(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _restamp_item(
    item: dict[str, Any], chunk: ExtractionChunk, report: StampReport
) -> dict[str, Any] | None:
    """The item with every stamp inside the window: same object when nothing moved, a copy
    when one did, None when the item's own stamp (``seconds``) fits nowhere."""
    fixed = item
    for key in _STAMP_KEYS:
        value = item.get(key)
        if not _is_stamp(value):
            continue
        placed = restamp(value, chunk)
        if placed is None and key == _ITEM_IS_STAMP:
            placed = _clock_placed(item, chunk)
        if placed == value:
            continue
        if placed is None and key == _ITEM_IS_STAMP:
            return None
        fixed = _with_stamp(fixed, key, placed, report)
    return fixed


def _clock_placed(item: dict[str, Any], chunk: ExtractionChunk) -> int | None:
    """A ``seconds`` no hour fits may still have its ``time`` clock inside the window."""
    clock = _clock_seconds(item.get(_CLOCK_KEY))
    return clock if clock is not None and _holds(clock, chunk) else None


def _with_stamp(
    item: dict[str, Any], key: str, placed: int | None, report: StampReport
) -> dict[str, Any]:
    fixed = {**item, key: placed}
    if placed is None:
        report.cleared += 1
        return fixed
    report.restamped += 1
    if key == _ITEM_IS_STAMP and isinstance(item.get(_CLOCK_KEY), str):
        fixed[_CLOCK_KEY] = format_marker(placed)[1:-1]
    return fixed


def _restamp_value(value: Any, chunk: ExtractionChunk, path: str, report: StampReport) -> Any:
    """``value`` with every stamped item below it placed; the same object when nothing moved."""
    if isinstance(value, dict):
        if any(key in value for key in _STAMP_KEYS):
            return _restamp_item(value, chunk, report)
        placed = {
            k: _restamp_value(v, chunk, f"{path}.{k}" if path else k, report)
            for k, v in value.items()
        }
        return placed if any(placed[k] is not value[k] for k in value) else value
    if isinstance(value, list):
        return _restamp_list(value, chunk, path, report)
    return value


def _restamp_list(
    items: list[Any], chunk: ExtractionChunk, path: str, report: StampReport
) -> list[Any]:
    """The list's items placed; an item placing dropped is left out and counted."""
    placed = [_restamp_value(item, chunk, path, report) for item in items]
    kept = [new for new, old in zip(placed, items) if new is not None or old is None]
    if len(kept) < len(items):
        report.drop(path, len(items) - len(kept), len(items))
    if len(kept) == len(items) and all(new is old for new, old in zip(kept, items)):
        return items
    return kept


def window_stamps(
    data: dict[str, Any], chunk: ExtractionChunk
) -> tuple[dict[str, Any], StampReport]:
    """One call's validated, domain-keyed data with every stamp inside the chunk's window.

    Deterministic and pure: the input is never mutated, and the same object comes
    back when nothing moved — always for the one open chunk of a video a single
    batch covers.
    """
    report = StampReport()
    if _window_open(chunk):
        return data, report
    placed = _restamp_value(data, chunk, "", report)
    return placed, report
