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

A chunk's jobs expect its share of each planned count (output-quality 3.3, L19):
``ExtractionChunk.share`` = the window over the video's clock (S01's 0–39:21 and
39:21–1:03:07: 0.62 and 0.38), so N chunks no longer each aim at the whole video's
count; the caps stay per call, and its ``<batch_partial_context>`` says so. One chunk
has share 1.0 and today's bytes.

The window also bounds what a chunk's call may stamp (output-quality 0b.10, R10 /
L21): ``window_stamps`` keeps an item's ``timestamp`` / ``seconds`` only inside the
chunk's window (a marker-interval of grace at an inner edge, none past the video's
end). A stamp outside it is the model reading the chunk's ``[h:mm:ss]`` markers
without the hour (S02 chunk 3 stamped 1:13:41 as 13:41 = 821 s), so the hour is put
back when that lands the stamp inside the window — once, deterministically; a stamp
≤ 0 (a 0 is the "no stamp" a model writes, a negative a sentinel) is never
hour-restored. A stamp nothing fits is cleared (an annotated item stays, and so does
a stamped dict reached as a value) or, when the stamp is a list item (``seconds``,
``learning.timestamps``), the item is dropped; a range end left before its start, or
without it, is cleared too. Every move is
counted per field path (``StampReport``) for the validation report — gate 1's
timestamp-drift reading (D17). One chunk is open at both ends: nothing is checked
and the data is returned as is (the ≤ 40-min replay stays byte-identical). Inside the
window, ``stamp_anchor`` then ties each stamp to its item's verified quote and to its
list's order (output-quality 4.0, G3-3), counted in the same report.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard

from ...config import settings
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
    _format_time,
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

    @property
    def share(self) -> float:
        """The window's share of the video's clock, ``(end − start) / video end`` — what
        the chunk's jobs scale each expected count by (output-quality 3.3; the windows of a
        run's chunks add up to 1). 1.0 for the open chunk of a video one batch covers, and
        without a video end to divide by."""
        if window_open(self) or not self.video_end_s:
            return 1.0
        end = self.video_end_s if self.end_s is None else self.end_s
        return min(1.0, max(0.0, (end - (self.start_s or 0)) / self.video_end_s))


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
    """One chunk per batch: today's batch transcript and annotation window; its context
    says the job's counts are the chunk's share (``ExtractionChunk.share``)."""
    full_duration = max(ch.end_seconds for batch in batches for ch in batch)
    opens = [_batch_start_seconds(batch) for batch in batches]
    last = len(batches) - 1
    return [
        ExtractionChunk(
            index=k,
            transcript=_build_batch_transcript(batch),
            batch_context=_build_batch_context(
                k, len(batches), batch, full_duration, counts_scaled=True
            ),
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
# a timestamp (``learning.timestamps``) — first, so a dropped item counts no other move;
# the others annotate an item that stands alone.
_STAMP_KEYS = ("seconds", "timestamp", "endSeconds", "endTimestamp")
_ITEM_IS_STAMP = "seconds"
# (start, end) of a range: a PodcastSegment ends its ``timestamp`` with ``endSeconds``.
_RANGE_KEYS = (
    ("timestamp", "endTimestamp"),
    ("timestamp", "endSeconds"),
    ("seconds", "endSeconds"),
)
_CLOCK_KEY = "time"
# A window opens at its first marker (one every 20 s) and a chapter edge may sit a few
# seconds before it: a stamp this close outside the window is at the edge, not off-clock.
# There is no grace past the video's end — no marker reads later than that.
WINDOW_GRACE_S = 30.0
_HOUR_S = 3600
_MAX_HOURS = 24
_CLOCK_PATTERN = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")
_MOVES = ("dropped", "restamped", "cleared")
# The stamp anchor's moves (``stamp_anchor``, output-quality 4.0): an entry carries them
# only where they happened, so a window-only entry keeps its four keys.
ANCHOR_MOVES = ("anchored", "reordered")
STAMP_MOVES = (*_MOVES, *ANCHOR_MOVES)


@dataclass
class StampReport:
    """What ``window_stamps`` (and then ``stamp_anchor.anchor_stamps``) did to one call's
    data, per field path (``learning.keyPoints``, the label ``ValidationReport.dropped``
    uses): the items dropped, the stamps restamped (the hour put back, or a ``seconds``
    read from its clock) and cleared, out of the ``total`` items at that path — plus the
    stamps anchored to their verified quote and those reordered by the list's order."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def tally(self, path: str, kind: str, n: int = 1) -> None:
        entry = self.counts.setdefault(path, {**dict.fromkeys(_MOVES, 0), "total": 0})
        entry[kind] = entry.get(kind, 0) + n

    @property
    def paths(self) -> dict[str, dict[str, int]]:
        """``{path: {dropped, restamped, cleared, total}}`` (``anchored`` / ``reordered``
        where they happened) for every path a stamp moved on."""
        return {p: dict(c) for p, c in self.counts.items() if any(c.get(k) for k in STAMP_MOVES)}

    def _sum(self, kind: str) -> int:
        return sum(c.get(kind, 0) for c in self.counts.values())

    @property
    def dropped(self) -> int:
        return self._sum("dropped")

    @property
    def restamped(self) -> int:
        return self._sum("restamped")

    @property
    def cleared(self) -> int:
        return self._sum("cleared")

    @property
    def anchored(self) -> int:
        return self._sum("anchored")

    @property
    def reordered(self) -> int:
        return self._sum("reordered")

    @property
    def changed(self) -> bool:
        return bool(self.paths)


def window_open(chunk: ExtractionChunk) -> bool:
    """Whether the chunk is a one-chunk run's whole video (no window to hold stamps to)."""
    return chunk.start_s is None and chunk.end_s is None


def window_label(chunk: ExtractionChunk) -> str:
    """The chunk's window on the transcript's clock (``1:11:11–1:22:58``), for logs."""
    end = chunk.end_s if chunk.end_s is not None else chunk.video_end_s
    return f"{_format_time(chunk.start_s or 0)}–{_format_time(end) if end is not None else 'end'}"


def holds_stamp(seconds: int, chunk: ExtractionChunk) -> bool:
    """Whether ``seconds`` lies in the chunk's window: a grace interval at an inner edge,
    none past the video's end."""
    after_start = chunk.start_s is None or seconds >= chunk.start_s - WINDOW_GRACE_S
    before_end = chunk.end_s is None or seconds < chunk.end_s + WINDOW_GRACE_S
    in_video = chunk.video_end_s is None or seconds <= chunk.video_end_s
    return after_start and before_end and in_video


def restamp(seconds: int, chunk: ExtractionChunk) -> int | None:
    """``seconds`` inside the window; else the hour-restored clock that lands inside it;
    else None (no marker of the chunk reads that way). A negative is no second of the
    video, and neither it nor a 0 is ever hour-restored: + 1 h would fabricate a stamp."""
    if seconds < 0:
        return None
    if holds_stamp(seconds, chunk):
        return seconds
    if seconds == 0:
        return None
    for hours in range(1, _MAX_HOURS + 1):
        candidate = seconds + hours * _HOUR_S
        if holds_stamp(candidate, chunk):
            return candidate
    return None


def _clock_seconds(text: Any) -> int | None:
    """The seconds a ``m:ss`` / ``h:mm:ss`` clock string reads; None for anything else."""
    found = _CLOCK_PATTERN.match(text) if isinstance(text, str) else None
    if found is None:
        return None
    hours, minutes, secs = found.groups()
    return int(hours or 0) * _HOUR_S + int(minutes) * 60 + int(secs)


def _is_stamp(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _stamped(value: dict[str, Any]) -> bool:
    return any(key in value for key in _STAMP_KEYS)


def _clock_placed(item: dict[str, Any], chunk: ExtractionChunk) -> int | None:
    """The item's ``time`` clock (the video clock of a ``seconds`` item) when it lies inside
    the window."""
    clock = _clock_seconds(item.get(_CLOCK_KEY))
    return clock if clock is not None and holds_stamp(clock, chunk) else None


def _place(item: dict[str, Any], key: str, value: int, chunk: ExtractionChunk) -> int | None:
    """Where the stamp under ``key`` lands: itself inside the window, else the hour put
    back, else (``seconds``) the item's own clock; None when nothing fits. A 0 is the
    model's "no stamp" (``LearningTimestamp.seconds`` defaults to it) and a negative a
    sentinel: both give way to a fitting clock and are never hour-restored
    (``restamp``) — a 0 stays only inside the window, a negative never."""
    clock = _clock_placed(item, chunk) if key == _ITEM_IS_STAMP else None
    if value <= 0 and clock is not None:
        return clock
    placed = restamp(value, chunk)
    return placed if placed is not None else clock


def _with_stamp(
    item: dict[str, Any], key: str, placed: int | None, path: str, report: StampReport
) -> dict[str, Any]:
    fixed = {**item, key: placed}
    if placed is None:
        report.tally(path, "cleared")
        return fixed
    report.tally(path, "restamped")
    if key == _ITEM_IS_STAMP and isinstance(item.get(_CLOCK_KEY), str):
        fixed[_CLOCK_KEY] = _format_time(placed)
    return fixed


def _ordered_range(
    fixed: dict[str, Any], item: dict[str, Any], path: str, report: StampReport
) -> dict[str, Any]:
    """``fixed`` without a range end before its start or without its start — what placing
    one side of a range and not the other leaves."""
    for start_key, end_key in _RANGE_KEYS:
        start, end = fixed.get(start_key), fixed.get(end_key)
        if start_key not in fixed or not _is_stamp(end):
            continue
        start_cleared = start is None and _is_stamp(item.get(start_key))
        if start_cleared or (_is_stamp(start) and end < start):
            fixed = _with_stamp(fixed, end_key, None, path, report)
    return fixed


def _restamp_item(
    item: dict[str, Any],
    chunk: ExtractionChunk,
    path: str,
    report: StampReport,
    droppable: bool,
) -> dict[str, Any] | None:
    """The item with every stamp inside the window: same object when nothing moved, a copy
    when one did. A ``seconds`` nothing places drops a list item (None) and is cleared on
    a dict reached as a value — a value is never replaced by None."""
    fixed = item
    for key in _STAMP_KEYS:
        value = item.get(key)
        if not _is_stamp(value):
            continue
        placed = _place(item, key, value, chunk)
        if placed == value:
            continue
        if placed is None and key == _ITEM_IS_STAMP and droppable:
            return None
        fixed = _with_stamp(fixed, key, placed, path, report)
    return fixed if fixed is item else _ordered_range(fixed, item, path, report)


def _restamp_value(value: Any, chunk: ExtractionChunk, path: str, report: StampReport) -> Any:
    """``value`` with every stamped item below it placed; the same object when nothing moved."""
    if isinstance(value, dict):
        if _stamped(value):
            report.tally(path, "total")
            return _restamp_item(value, chunk, path, report, droppable=False)
        placed = {
            k: _restamp_value(v, chunk, f"{path}.{k}" if path else k, report)
            for k, v in value.items()
        }
        return placed if any(placed[k] is not value[k] for k in value) else value
    if isinstance(value, list):
        return _restamp_list(value, chunk, path, report)
    return value


def _restamp_entry(item: Any, chunk: ExtractionChunk, path: str, report: StampReport) -> Any:
    """One list item placed — a stamped item whose own stamp fits nowhere is None (dropped)."""
    if isinstance(item, dict) and _stamped(item):
        return _restamp_item(item, chunk, path, report, droppable=True)
    return _restamp_value(item, chunk, path, report)


def _restamp_list(
    items: list[Any], chunk: ExtractionChunk, path: str, report: StampReport
) -> list[Any]:
    """The list's items placed; an item placing dropped is left out and counted."""
    report.tally(path, "total", len(items))
    placed = [_restamp_entry(item, chunk, path, report) for item in items]
    kept = [new for new, old in zip(placed, items) if new is not None or old is None]
    if len(kept) < len(items):
        report.tally(path, "dropped", len(items) - len(kept))
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
    if window_open(chunk):
        return data, report
    placed = _restamp_value(data, chunk, "", report)
    return placed, report
