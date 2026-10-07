"""Prompt-only transcript rendering with ``[m:ss]`` time markers.

Markers exist only in LLM prompts (plan, memory, extraction). ``ctx.clean_text``,
the Qdrant transcript chunks and the S3 transcript blob keep the unmarked text.

Format — one line per block, ``[m:ss] text``:

* A block opens at the first segment whose start crosses the next ``every``-second
  boundary (the first spoken segment always opens one). A segment that spans
  several boundaries opens one block, so no marker ever sits on an empty line.
* The marker shows that segment's own start, floored to the second — the moment
  the text really begins, not the boundary it crossed.
* Times are absolute video time, so rendering a slice of the segment list (one
  chunk of a long video) gives the same times as rendering the whole list.
* Below one hour the marker is ``[m:ss]``; from one hour on it is ``[h:mm:ss]``,
  the same clock the ``=== CHAPTER …`` headers of chunked batches use.

Each block's text goes through the basic cleaning that builds ``clean_text``
(``clean_transcript``), so the blocks joined without their markers read the same
as ``clean_text`` built from the same segments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.services.transcription.transcript import clean_transcript

# A rendered marker: ``[m:ss]`` or ``[h:mm:ss]`` → groups (hours?, minutes, seconds).
MARKER_PATTERN = re.compile(r"\[(?:(\d+):)?(\d{1,2}):(\d{2})\]")


@dataclass
class _Block:
    """Segments that share one marker: the opening segment's start + raw texts."""

    start: float
    raw_texts: list[str] = field(default_factory=list)


def format_marker(seconds: float) -> str:
    """``[m:ss]`` below one hour, ``[h:mm:ss]`` from one hour on (floored)."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"[{hours}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes}:{secs:02d}]"


def marker_seconds(text: str) -> list[int]:
    """Absolute seconds of every marker in ``text``, in order of appearance."""
    return [
        int(hours or 0) * 3600 + int(minutes) * 60 + int(secs)
        for hours, minutes, secs in MARKER_PATTERN.findall(text)
    ]


def _segment_start(segment: Mapping[str, Any]) -> float:
    """Start in seconds. Raw ``start`` (seconds) is the pipeline shape; the
    ``startMs`` shape (``normalize_segments`` output) is what the chapter
    chunker slices, so both render identically."""
    if "startMs" in segment:
        return float(segment["startMs"]) / 1000.0
    return float(segment.get("start") or 0)


def _group_blocks(segments: Sequence[Mapping[str, Any]], every: int) -> list[_Block]:
    """Group segments into marker blocks (see the module docstring)."""
    blocks: list[_Block] = []
    last_bucket = -1
    for segment in segments:
        raw = str(segment.get("text") or "")
        if not clean_transcript(raw):
            # A "[Music]"-only segment never carries a marker; it still joins
            # the open block so block cleaning sees the text clean_text sees.
            if blocks:
                blocks[-1].raw_texts.append(raw)
            continue
        start = _segment_start(segment)
        bucket = int(start // every)
        # ``>`` (not ``!=``): an out-of-order caption never moves time backwards.
        if not blocks or bucket > last_bucket:
            blocks.append(_Block(start=start))
            last_bucket = max(last_bucket, bucket)
        blocks[-1].raw_texts.append(raw)
    return blocks


def render_transcript(segments: Sequence[Mapping[str, Any]], every: int = 20) -> str:
    """Render segments as prompt text with an absolute ``[m:ss]`` marker per block.

    Args:
        segments: ``{text, start, duration}`` dicts (seconds) as on
            ``ctx.transcript_data.segments``; ``{text, startMs, endMs}`` dicts
            are accepted too.
        every: Block spacing in seconds — a new marker at the first segment
            that crosses each multiple of ``every``.

    Returns:
        ``"[0:00] text\\n[0:21] text…"``; ``""`` when no segment has text.
    """
    if every <= 0:
        raise ValueError(f"every must be a positive number of seconds, got {every}")
    lines: list[str] = []
    for block in _group_blocks(segments, every):
        text = clean_transcript(" ".join(block.raw_texts))
        if text:
            lines.append(f"{format_marker(block.start)} {text}")
    return "\n".join(lines)
