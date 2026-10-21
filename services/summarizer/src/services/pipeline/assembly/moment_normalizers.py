"""Moment item normalizers (the MomentTrack item contract).

Split out of normalizers.py (output-quality 0b follow-ups): the moment block
— prose-bearing items, one per second, chapters as spans. Every name here
stays importable from ``normalizers`` through its explicit re-exports.
"""

from __future__ import annotations

import math
import re
from typing import Any

from src.utils.data_helpers import parse_timestamp_to_seconds

from .text_utils import truncate_words

_MOMENT_LABEL_KEYS = ("label", "title", "name")
# Prose a moment source may carry under another name: PodcastSegment.summary,
# FoodStep.instruction, PodcastQA.answer. (A chapter, outline section or
# description timestamp carries a title only.)
_MOMENT_DESCRIPTION_KEYS = ("description", "summary", "instruction", "answer")
# An interview exchange (``podcast.qa``, C10): its question is the row's title —
# whole up to _QUESTION_LABEL_CAP characters, else its first sentence, else cut at a
# word — and rides along whole as ``question``.
_MOMENT_QUESTION_KEY = "question"
_QUESTION_LABEL_CAP = 140
_SENTENCE_END = re.compile(r"[?.!](?=\s|$)")
_MOMENT_START_KEYS = ("seconds", "startSeconds", "timestamp", "start_time", "startTime")
# The clock a ``learning.timestamps`` item writes beside its ``seconds`` ("2:10").
_MOMENT_CLOCK_KEY = "time"
_MOMENT_END_KEYS = ("endSeconds", "end_seconds", "endTimestamp", "end_time", "endTime")
_MOMENT_PASSTHROUGH_KEYS = (
    "quote",
    "question",
    "mood",
    "emoji",
    "speaker",
    "thumbnailUrl",
    "s3Key",
)
_MOMENT_LABEL_CAP = 60
# A placeholder label is no text: the track never ships "Moment 3" (R5). Exact form only —
# a lowercase "moment 3" is a source's own words, not the old positional default.
_MOMENT_PLACEHOLDER = re.compile(r"^Moment \d+$")


def _prose(value: Any) -> str | None:
    """A stripped non-empty string, else None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_prose(item: dict, keys: tuple[str, ...]) -> str | None:
    return next((text for key in keys if (text := _prose(item.get(key))) is not None), None)


def _coerce_int(value: Any) -> int | None:
    """Parse int or return None on failure; a NaN or ±Infinity (JSON from the
    model may carry them) is no number."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _first_int(item: dict, keys: tuple[str, ...]) -> int | None:
    return next((value for key in keys if (value := _coerce_int(item.get(key))) is not None), None)


def _seconds_to_time_str(seconds: int) -> str:
    """Convert seconds to M:SS or H:MM:SS time string."""
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"


def _clock_start(item: dict) -> int | None:
    """The second the item's ``time`` clock reads ("2:10" → 130); None without one."""
    clock = item.get(_MOMENT_CLOCK_KEY)
    return parse_timestamp_to_seconds(clock) if isinstance(clock, str) else None


def _moment_start(item: dict) -> int | None:
    """The second a source item starts at; None when it states none.

    The first positive stamp among ``_MOMENT_START_KEYS`` wins. A ``seconds`` of
    0, or none, is no stamp beside a positive sibling ``startSeconds`` /
    ``timestamp`` (close-out review W4) or a ``time`` clock: ``LearningTimestamp``
    defaults a missing ``seconds`` to 0, so a chip reading 2:10 seeked to 0:00
    and the real opening was deduped away (phase-0 review W-B1) — the clock
    decides. A negative is a sentinel, never a second: the clock decides, else
    None. A 0 stated first, without either, is the opening.
    """
    stated = [v for key in _MOMENT_START_KEYS if (v := _coerce_int(item.get(key))) is not None]
    positive = next((v for v in stated if v > 0), None)
    if positive is not None:
        return positive
    clock = _clock_start(item)
    if clock is not None:
        return clock
    return 0 if stated and stated[0] == 0 else None


def _question_label(question: str) -> str:
    """The row title of an interview question: the whole question when it fits,
    else its first sentence when that fits, else a word-boundary cut."""
    if len(question) <= _QUESTION_LABEL_CAP:
        return question
    end = _SENTENCE_END.search(question)
    if end is not None and end.end() <= _QUESTION_LABEL_CAP:
        return question[: end.end()]
    return truncate_words(question, _QUESTION_LABEL_CAP)


def _moment_text(item: dict) -> tuple[str, str | None] | None:
    """(label, description) from whichever fields carry prose; None when none does.

    The label comes from label/title/name, else from an interview question
    (``_question_label``), else from the head of the description; the
    description is dropped when it only repeats the label.
    """
    label = _first_prose(item, _MOMENT_LABEL_KEYS)
    if label is not None and _MOMENT_PLACEHOLDER.match(label):
        label = None
    description = _first_prose(item, _MOMENT_DESCRIPTION_KEYS)
    question = _prose(item.get(_MOMENT_QUESTION_KEY))
    if label is None and question is not None:
        label = _question_label(question)
    if label is None and description is not None:
        label = truncate_words(description, _MOMENT_LABEL_CAP)
    if label is None:
        return None
    return label, (None if description == label else description)


def _normalize_moment_item(item: Any, index: int) -> dict | None:
    """Normalize a moment/clip item to {label, time, seconds, endSeconds?, description?, ...}.

    Unified shape for MomentTrack: points (no endSeconds) and spans (with endSeconds).
    Accepts the legacy field names from TimelineExplorer.entries and ClipPlayerInteractive.clips.
    An item without prose is refused (None) — a moment is never invented from
    its position; ``index`` stays for the call contract and names nothing. A
    timeless item is shaped at 0:00 here; the track (``normalize_moment_items``)
    refuses it. The ``time`` chip is always the clock of the ``seconds`` shipped.
    """
    if isinstance(item, str):
        stripped = item.strip()
        return {"label": stripped, "time": "0:00", "seconds": 0} if stripped else None
    if not isinstance(item, dict):
        return None
    text = _moment_text(item)
    if text is None:
        return None
    label, description = text
    start = _moment_start(item)
    seconds = start if start is not None else 0
    end_seconds = _first_int(item, _MOMENT_END_KEYS)
    normalized: dict[str, Any] = {
        "label": label,
        "time": _seconds_to_time_str(seconds),
        "seconds": seconds,
    }
    if end_seconds is not None and end_seconds > seconds + 1:
        normalized["endSeconds"] = end_seconds
    if description is not None:
        normalized["description"] = description
    for key in _MOMENT_PASSTHROUGH_KEYS:
        value = item.get(key)
        if value is not None and value != "":
            normalized[key] = value
    tags = item.get("tags")
    if isinstance(tags, list) and tags:
        normalized["tags"] = [str(t) for t in tags if t is not None]
    return normalized


def _stated_start(raw: Any) -> int | None:
    """The second a source item says it starts at; None for a string or a timeless item."""
    return _moment_start(raw) if isinstance(raw, dict) else None


def normalize_moment_items(data: Any) -> list[dict]:
    """Moment items a track can ship: prose-bearing, timed, one per ``seconds`` (first wins).

    A moment is a seek point. An item that states no time is refused, not
    parked at 0:00, where it took the real opening's slot and turned an
    all-timeless list into one item at a made-up 0:00; with nothing left the
    track falls back to the outline's real sections. A second item at the same
    second cannot be seeked apart from the first and reads as padding (S11
    shipped nine itinerary days at 0:00), so it is refused too.
    """
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    seen: set[int] = set()
    for index, raw in enumerate(data):
        if _stated_start(raw) is None:
            continue
        item = _normalize_moment_item(raw, index)
        if item is None or item["seconds"] in seen:
            continue
        seen.add(item["seconds"])
        items.append(item)
    return items


def _timed_chapters(chapters: list[dict]) -> list[tuple[int, dict]]:
    """``(start, chapter)`` for every chapter that states a start, in time order.

    A start-less chapter is skipped like an untitled one: parked at 0 it took
    the opening slot from the real 0:00 chapter. The sort is stable, so of two
    chapters at one start the earlier in the source stays first.
    """
    timed: list[tuple[int, dict]] = []
    for chapter in chapters:
        start = _coerce_int(chapter.get("start_time"))
        if start is not None:
            timed.append((start, chapter))
    return sorted(timed, key=lambda pair: pair[0])


def _chapter_end(starts: list[int], index: int, video_duration: int | None) -> int | None:
    """The span end: the next later start minus 1; the video end for the last one.

    ``starts`` is in time order (``_timed_chapters``).
    """
    start = starts[index]
    next_start = int(video_duration) if video_duration is not None else None
    for later in starts[index + 1 :]:
        if later > start:
            next_start = later
            break
    return next_start - 1 if next_start is not None and next_start > start + 1 else None


def _chapter_moment(chapter: dict, start: int, end_seconds: int | None) -> dict | None:
    """A chapter span in MomentTrack item shape; None for a chapter without a title."""
    text = _moment_text(chapter)
    if text is None:
        return None
    label, description = text
    entry: dict[str, Any] = {
        "time": _seconds_to_time_str(start),
        "seconds": start,
        "label": label,
        "mood": "chapter",
    }
    if end_seconds is not None:
        entry["endSeconds"] = end_seconds
    if description is not None:
        entry["description"] = description
    return entry


def _chapters_to_moments(chapters: list[dict], video_duration: int | None = None) -> list[dict]:
    """Convert yt-dlp chapters (or outline / description entries in that shape) to spans.

    Chapters are read in time order. Each ends where the next later chapter
    starts; the final one at video_duration - 1 when known. A chapter without a
    title or a start, or at a start an earlier chapter already holds, is
    skipped; a ``summary`` / ``description`` rides along as the moment's
    description.
    """
    timed = _timed_chapters(chapters)
    starts = [start for start, _ in timed]
    items: list[dict] = []
    seen: set[int] = set()
    for i, (start, ch) in enumerate(timed):
        if start in seen:
            continue
        entry = _chapter_moment(ch, start, _chapter_end(starts, i, video_duration))
        if entry is None:
            continue
        seen.add(start)
        items.append(entry)
    return items
