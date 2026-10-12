"""moment_track fallback chain (pipeline-1min phase-1 close, Kfir's decision on g1).

A moment_track tab is never dropped for lack of data. When its planned
dataSource cannot feed it (no data, an inactive domain, or every moment past
the video's end), the moments come from the first source that has entries:

1. the memory outline (``video_meta["outline"]``: start/end/title sections),
2. the YouTube chapters (``video_meta["chapters"]``),
3. the description's timestamps (``description_analysis["timestamps"]``).

Every source is converted to the chapter shape ``_chapters_to_moments`` turns
into MomentTrack spans, so the three read the same in the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .assemblers_primary import assemble_moment_track
from .normalizers import _chapters_to_moments, _coerce_int

logger = logging.getLogger(__name__)


def _outline_entries(video_meta: Mapping[str, Any]) -> list[dict]:
    return [
        {"start_time": s.get("start"), "title": s.get("title", "")}
        for s in video_meta.get("outline") or []
        if isinstance(s, dict)
    ]


def _chapter_entries(video_meta: Mapping[str, Any]) -> list[dict]:
    return [ch for ch in video_meta.get("chapters") or [] if isinstance(ch, dict)]


def _description_entries(description_analysis: Mapping[str, Any] | None) -> list[dict]:
    timestamps = (description_analysis or {}).get("timestamps") or []
    return [
        {"start_time": t.get("seconds"), "title": t.get("label", "")}
        for t in timestamps
        if isinstance(t, dict)
    ]


def _usable(entries: list[dict], duration: int | None) -> list[dict]:
    """Entries with a start inside the video, in time order."""
    kept = [
        e
        for e in entries
        if (start := _coerce_int(e.get("start_time"))) is not None
        and start >= 0
        and (duration is None or start < duration)
        and str(e.get("title") or "").strip()
    ]
    return sorted(kept, key=lambda e: _coerce_int(e["start_time"]) or 0)


_SOURCES: tuple[
    tuple[str, Callable[[Mapping[str, Any], Mapping[str, Any] | None], list[dict]]], ...
] = (
    ("memory_outline", lambda meta, _desc: _outline_entries(meta)),
    ("youtube_chapters", lambda meta, _desc: _chapter_entries(meta)),
    ("description_timestamps", lambda _meta, desc: _description_entries(desc)),
)


def fallback_moments(
    video_meta: Mapping[str, Any] | None, description_analysis: Mapping[str, Any] | None
) -> list[dict] | None:
    """Moment data from the first fallback source with entries; None when none has any."""
    meta = video_meta or {}
    duration = _coerce_int(meta.get("duration"))
    for name, entries_of in _SOURCES:
        entries = _usable(entries_of(meta, description_analysis), duration)
        if entries:
            logger.info("moment_track fallback: %d moments from %s", len(entries), name)
            return _chapters_to_moments(entries, duration)
    return None


def fallback_moment_props(
    video_meta: Mapping[str, Any] | None, description_analysis: Mapping[str, Any] | None
) -> dict | None:
    """Assembled moment_track props built from the fallback chain."""
    moments = fallback_moments(video_meta, description_analysis)
    return assemble_moment_track({}, moments, {}, None) if moments else None


def cannot_feed_moments(data: Any) -> bool:
    """True when resolved moment data is missing or empty."""
    return data is None or (isinstance(data, list) and len(data) == 0)
