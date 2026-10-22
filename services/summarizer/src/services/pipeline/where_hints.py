"""The where part of a brief line — on a chunked run, an argument list's windows are hints.

A planned tab's brief line (``extraction_prompt._tab_line``) names the plan's
``where`` windows: ``where 5:00–15:27, 24:21–33:00`` — read as the whole video's
places, and on a one-chunk run that is what they are. On a chunked run the argument
list's windows read as a limit: S15's re-run (71 min, chunks 0–40:00 and
40:00–1:11:22, 2026-10-22) had a key-ideas brief whose windows ended at 45:23, and
its chunk-1 call kept 7 key ideas, all ≤ 45:01 (gate 3, whose windows ran to 1:09:20,
kept 15 up to 1:09). Key ideas are the argument and are never sampled away (Kfir
2026-10-21), so on a chunked run the line of an argument list (``ARGUMENT_PATHS``:
``learning.keyIdeas`` — a talk's Key Ideas and an interview's Positions both read it)
asks for every one in the chunk's part and names the plan's windows that touch the
part only as where the planner expected most of them. Its count stays the chunk's
share of the video by duration (``ExtractionChunk.share``), never the windows' share.

Every other list, and every line of a one-chunk run (no ``PartWindow``), keeps
today's ``where`` part byte for byte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...utils.data_helpers import parse_timestamp_to_seconds
from .plan_reconcile import ARGUMENT_PATHS

_EVERY_ONE = "every one in this part"


@dataclass(frozen=True)
class PartWindow:
    """A chunk's window on the video's clock in seconds; None = open at that side."""

    start_s: float | None
    end_s: float | None


def part_window(start_s: float | None, end_s: float | None) -> PartWindow | None:
    """The chunk's window; None for a window open at both ends (a one-chunk run)."""
    return None if start_s is None and end_s is None else PartWindow(start_s, end_s)


def _bounds(time_range: str) -> tuple[int, int] | None:
    """``(start, end)`` seconds of a normalized ``m:ss-m:ss`` range (a lone time is both)."""
    start, _, end = time_range.partition("-")
    first = parse_timestamp_to_seconds(start)
    last = parse_timestamp_to_seconds(end) if end else first
    return None if first is None or last is None else (first, last)


def _touches(time_range: str, part: PartWindow) -> bool:
    bounds = _bounds(time_range)
    if bounds is None:
        return False
    first, last = bounds
    before_end = part.end_s is None or first < part.end_s
    return before_end and (part.start_s is None or last >= part.start_s)


def _shown(ranges: Sequence[str]) -> str:
    return ", ".join(r.replace("-", "–") for r in ranges)


def where_part(source: str, where: Sequence[str], part: PartWindow | None = None) -> str:
    """The brief line's where part ("" without windows): today's ``where …`` — or, for an
    argument list in a chunk's ``part``, every one in the part, the plan's windows that
    touch it named as where the planner expected most."""
    if not where:
        return ""
    if part is None or source not in ARGUMENT_PATHS:
        return f"where {_shown(where)}"
    hints = [r for r in where if _touches(r, part)]
    return f"{_EVERY_ONE} (the plan expected most at {_shown(hints)})" if hints else _EVERY_ONE
