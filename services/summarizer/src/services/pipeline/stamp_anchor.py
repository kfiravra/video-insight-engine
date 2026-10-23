"""The stamp anchor (output-quality 4.0, G3-3): a chunk call's stamps tied to the item's
own words and to its list's order.

The window check (``extraction_chunks.window_stamps``, 0b.10) bounds a stamp by its
chunk's window, 31 minutes on S15's second chunk. There the model wrote a later marker's
clock for one 60-s passage in two calls: key idea "New bitcoins are created by the
network…" at 56:20 with its own Hebrew quote said at 43:16, concepts "Bitcoin mining"
(53:20) and "Fixed supply" (1:03:41) explained at 43:01–44:01. Every stamp sat inside the
window and in its right place in its list. Two passes per call, right after the window
check, so every reader of the call (previews, quiz, hero, merge_rank, the final merge)
reads the result:

1. **Quote anchor.** An item whose ``quote`` the run's ``QuoteIndex`` VERIFIES at a
   second inside the call's window (its grace included) is stamped where it is said:
   - a point (a key idea, a claim, a quote, a rating, a verdict …) when it is said more
     than ``STAMP_AGREEMENT_S`` away from its stamp;
   - a range (a ``moment_track`` list: moments, segments, Q&A — or an item with an end)
     only when it is said outside [stamp − 30 s, end + 30 s], the end being its own, else
     the list's next stamp, else the window's end. An end left before the new start is
     cleared; a moment's ``time`` clock is rewritten.
   A translated or unverified quote moves nothing, nor does one said outside the window.
2. **Order guard**, on the lists the model writes in the video's order (``learning.*``,
   ``podcast.segments``; never a rankings list, never ``podcast.qa``, whose stamps the
   question check owns: a verified question is re-stamped where it is asked, a translated
   one keeps its row — Kfir: keep every question). The stamps off the list's longest
   non-decreasing run are bracketed by the run's stamps before and after them (the
   window's edges at the list's ends). The guard is one-sided (phase-4 review A-C1): only
   a stamp more than ``ORDER_GAP_S`` LATE past its bracket moves, since every miss found
   was the model writing a later marker; one listed after later items but stamped early
   is left alone (S15's closing claim, said at 56:24 and listed after 1:08:41), and so is
   any item whose verified quote holds its stamp after pass 1. A late stamp goes back to
   the bracket's earlier stamp when the bracket is at most ``ORDER_GAP_S`` wide, else it
   is cleared. On a timeline list (moments, segments) the stamp is the item's place: it
   goes to the first second from there that no other item of the list holds (assembly
   keeps one item per second), and one left unstamped is dropped. A range that moves
   loses its end (read on the same wrong clock). A 0 is the model's "no stamp" and is
   left out of the order.

Every move is counted per field path in the window's ``StampReport`` (``anchored`` /
``reordered``, and ``dropped`` for an item the guard drops). Pure: the input is never
mutated, and the same object comes back when nothing moved — always for an open window
(a one-chunk run: Kfir's scope, 2026-10-22).
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeGuard

from ...shared_config.domain_config import data_sources
from .extraction_chunks import ExtractionChunk, StampReport, holds_stamp, window_open
from .extractor import _format_time
from .quote_verify import VERIFIED, QuoteIndex

# A stamp this close to where its quote is said agrees with it (= ``quiz_v2``'s).
STAMP_AGREEMENT_S = 30
# A stamp this far outside its list's order is a misread marker, not a digression.
ORDER_GAP_S = 300
# Shorter lists have no order to read.
_MIN_ORDERED = 3
_STAMP_KEYS = ("seconds", "timestamp")
_ITEM_IS_STAMP = "seconds"
_END_KEYS = ("endSeconds", "endTimestamp")
_CLOCK_KEY = "time"
_QUOTE_KEY = "quote"
_CHRONOLOGICAL_DOMAINS = ("learning.",)
_CHRONOLOGICAL_LISTS = frozenset({"podcast.segments"})


@dataclass(frozen=True)
class _Pass:
    """One call's anchor: its window, the index (None = no quote anchor), whether its
    lists' order is guarded, and the report the moves are counted in."""

    chunk: ExtractionChunk
    index: QuoteIndex | None
    ordered: bool
    report: StampReport


# ─── Items ───


def _is_stamp(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _stamp_key(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    return next((key for key in _STAMP_KEYS if _is_stamp(item.get(key))), None)


def _stamp(item: Any) -> int | None:
    key = _stamp_key(item)
    return item[key] if key is not None else None


def _window(chunk: ExtractionChunk) -> tuple[float, float]:
    """The window's start and end; 0 and infinity at an open side."""
    end = chunk.end_s if chunk.end_s is not None else chunk.video_end_s
    return chunk.start_s or 0.0, end if end is not None else math.inf


def _restamped(item: dict[str, Any], key: str, seconds: int | None) -> dict[str, Any]:
    """``item`` stamped at ``seconds``: a moment's clock follows; an end before the new
    start, or with no start left, is cleared."""
    fixed = {**item, key: seconds}
    if key == _ITEM_IS_STAMP and seconds is not None and isinstance(item.get(_CLOCK_KEY), str):
        fixed[_CLOCK_KEY] = _format_time(seconds)
    for end in _END_KEYS:
        value = fixed.get(end)
        if _is_stamp(value) and (seconds is None or value < seconds):
            fixed[end] = None
    return fixed


def _count(run: _Pass, path: str, kind: str, size: int) -> None:
    """One move at ``path``; a path the window never counted (an open window) gets its size."""
    if path not in run.report.counts:
        run.report.tally(path, "total", size)
    run.report.tally(path, kind)


# ─── 1. The quote anchor ───


@lru_cache(maxsize=1)
def _timeline_paths() -> frozenset[str]:
    """Every list the registry renders first as a timeline: its items are ranges."""
    return frozenset(
        path for path, spec in data_sources().items() if spec["components"][:1] == ["moment_track"]
    )


def _said(item: dict[str, Any], key: str, run: _Pass) -> int | None:
    """Where the item's quote is said, when it is VERIFIED inside the call's window."""
    quote = item.get(_QUOTE_KEY)
    if run.index is None or not isinstance(quote, str) or not quote.strip():
        return None
    verdict = run.index.verify(quote, item[key])
    if verdict.status != VERIFIED or verdict.seconds is None:
        return None
    return verdict.seconds if holds_stamp(verdict.seconds, run.chunk) else None


def _is_range(item: dict[str, Any], path: str) -> bool:
    return path in _timeline_paths() or any(_is_stamp(item.get(end)) for end in _END_KEYS)


def _range_end(
    item: dict[str, Any], stamp: int, starts: Sequence[int], chunk: ExtractionChunk
) -> float:
    """The item's own end, else the list's next stamp, else the window's end."""
    own = next((item[key] for key in _END_KEYS if _is_stamp(item.get(key))), None)
    if own is not None:
        return own
    later = bisect.bisect_right(starts, stamp)
    return starts[later] if later < len(starts) else _window(chunk)[1]


def _span(
    item: dict[str, Any], stamp: int, path: str, starts: Sequence[int], chunk: ExtractionChunk
) -> tuple[float, float]:
    """Where a quote may be said and still agree with the item's stamp: its span for a
    range, ``STAMP_AGREEMENT_S`` around a point."""
    end = _range_end(item, stamp, starts, chunk) if _is_range(item, path) else stamp
    return stamp - STAMP_AGREEMENT_S, end + STAMP_AGREEMENT_S


def _anchor_item(item: Any, path: str, run: _Pass, starts: Sequence[int], size: int) -> Any:
    """``item`` stamped where its verified quote is said, when that lies outside its span."""
    key = _stamp_key(item)
    said = _said(item, key, run) if key is not None else None
    if key is None or said is None:
        return item
    low, high = _span(item, item[key], path, starts, run.chunk)
    if low <= said <= high:
        return item
    _count(run, path, "anchored", size)
    return _restamped(item, key, said)


# ─── 2. The order guard ───


def _chronological(path: str) -> bool:
    return path.startswith(_CHRONOLOGICAL_DOMAINS) or path in _CHRONOLOGICAL_LISTS


def _order_stamp(item: Any) -> int | None:
    """The stamp the list's order reads: a 0 is the model's "no stamp"."""
    stamp = _stamp(item)
    return stamp if stamp is not None and stamp > 0 else None


def _walk_back(before: Sequence[int], last: int) -> frozenset[int]:
    """The positions of the run ending at ``last``, read back through ``before``."""
    run: set[int] = set()
    while last != -1:
        run.add(last)
        last = before[last]
    return frozenset(run)


def _longest_run(stamps: Sequence[int]) -> frozenset[int]:
    """The positions of one longest non-decreasing run of ``stamps`` (patience sorting)."""
    tails: list[tuple[int, int]] = []  # per run length: (its smallest last stamp, position)
    before: list[int] = []  # per position: the run's previous position (-1 = none)
    for position, stamp in enumerate(stamps):
        length = bisect.bisect_right(tails, stamp, key=lambda tail: tail[0])
        before.append(tails[length - 1][1] if length else -1)
        tails[length : length + 1] = [(stamp, position)]
    return _walk_back(before, tails[-1][1] if tails else -1)


def _bracket(
    stamps: Sequence[int], run: frozenset[int], position: int, chunk: ExtractionChunk
) -> tuple[float, float]:
    """The run's stamps just before and after ``position``; the window's edges past its ends."""
    start, end = _window(chunk)
    low = max((stamps[i] for i in run if i < position), default=start)
    high = min((stamps[i] for i in run if i > position), default=end)
    return low, high


def _late(stamps: Sequence[int], chunk: ExtractionChunk) -> dict[int, int | None]:
    """``{position: new stamp}`` for every stamp more than ``ORDER_GAP_S`` past its
    bracket's later stamp: the bracket's earlier stamp, or None when the bracket is wider
    than that. A stamp before its bracket is never moved (module docstring, one-sided)."""
    run = _longest_run(stamps)
    off_run = {p: _bracket(stamps, run, p, chunk) for p in range(len(stamps)) if p not in run}
    return {
        position: int(low) if high - low <= ORDER_GAP_S else None
        for position, (low, high) in off_run.items()
        if stamps[position] > high + ORDER_GAP_S
    }


def _held_by_quote(item: Any, run: _Pass) -> bool:
    """Whether the item's quote is VERIFIED inside the window: after pass 1 its stamp is
    where (a range: within the span of where) the quote is said, so the order is wrong,
    not the stamp."""
    key = _stamp_key(item)
    return key is not None and _said(item, key, run) is not None


def _free_second(seconds: int, taken: set[int]) -> int:
    """The first second from ``seconds`` on that ``taken`` does not hold."""
    while seconds in taken:
        seconds += 1
    return seconds


def _placed(items: list[Any], moves: dict[int, int | None], path: str) -> dict[int, int | None]:
    """``moves`` with each timeline item's new second its own: assembly keeps one moment per
    second (``normalize_moment_items``), so one pulled onto its neighbour's is lost."""
    if path not in _timeline_paths():
        return moves
    taken = {
        s for i, item in enumerate(items) if i not in moves and (s := _stamp(item)) is not None
    }
    placed: dict[int, int | None] = {}
    for position, seconds in sorted(moves.items()):
        if seconds is not None:
            seconds = _free_second(seconds, taken)
            taken.add(seconds)
        placed[position] = seconds
    return placed


def _reordered(item: dict[str, Any], seconds: int | None, path: str) -> dict[str, Any] | None:
    """``item`` at ``seconds`` without its end; None (dropped) for a moment or a timeline
    item left unstamped — its stamp is its place (a moment's ``time`` clock would
    otherwise revive the wrong one)."""
    key = _stamp_key(item)
    assert key is not None  # only stamped items are ordered
    if seconds is None and (key == _ITEM_IS_STAMP or path in _timeline_paths()):
        return None
    moved = {**item, **{end: None for end in _END_KEYS if _is_stamp(item.get(end))}}
    return _restamped(moved, key, seconds)


def _rebuilt(items: list[Any], moves: dict[int, int | None], path: str, run: _Pass) -> list[Any]:
    """``items`` with each moved one at its new stamp, a dropped one left out; each move
    counted ``reordered``, each drop ``dropped``."""
    placed = _placed(items, moves, path)
    rebuilt = [_reordered(it, placed[i], path) if i in placed else it for i, it in enumerate(items)]
    for position in placed:
        _count(run, path, "dropped" if rebuilt[position] is None else "reordered", len(items))
    return [new for new, old in zip(rebuilt, items) if new is not None or old is None]


def _ordered(items: list[Any], path: str, run: _Pass) -> list[Any]:
    """The list with every stamp far past its order pulled back, cleared or dropped."""
    places = [i for i, item in enumerate(items) if _order_stamp(item) is not None]
    if len(places) < _MIN_ORDERED:
        return items
    late = _late([_order_stamp(items[i]) or 0 for i in places], run.chunk)
    moves = {places[p]: s for p, s in late.items() if not _held_by_quote(items[places[p]], run)}
    if not moves:
        return items
    return _rebuilt(items, moves, path, run)


# ─── The walk ───


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _inner(item: dict[str, Any], path: str, run: _Pass) -> dict[str, Any]:
    """``item`` with every value below it anchored; its own stamp is its list's business."""
    placed = {key: _value(value, _join(path, key), run) for key, value in item.items()}
    return placed if any(placed[key] is not item[key] for key in item) else item


def _list(items: list[Any], path: str, run: _Pass) -> list[Any]:
    """The list's nested values, then its items anchored, then its order guarded."""
    inner = [_inner(item, path, run) if isinstance(item, dict) else item for item in items]
    starts = sorted(s for s in map(_stamp, inner) if s is not None)
    anchored = [_anchor_item(item, path, run, starts, len(items)) for item in inner]
    if run.ordered and _chronological(path):
        anchored = _ordered(anchored, path, run)
    same = len(anchored) == len(items) and all(a is b for a, b in zip(anchored, items))
    return items if same else anchored


def _value(value: Any, path: str, run: _Pass) -> Any:
    """A list item by item, then as a list; a dict reached as a value (a review's verdict)
    as a point."""
    if isinstance(value, list):
        return _list(value, path, run)
    if not isinstance(value, dict):
        return value
    return _anchor_item(_inner(value, path, run), path, run, (), 1)


def _anchors_quotes(chunk: ExtractionChunk) -> bool:
    """Whether the quote anchor reads this call: a closed window only, so a one-chunk run's
    prompts and replay stay byte-identical (Kfir's scope, 2026-10-22). Anchoring one-chunk
    runs too is this one condition (``return True``); the order guard stays chunk-only."""
    return not window_open(chunk)


def anchor_stamps(
    data: dict[str, Any], chunk: ExtractionChunk, index: QuoteIndex | None, report: StampReport
) -> dict[str, Any]:
    """One call's windowed, domain-keyed data with its stamps anchored (module docstring);
    each move is tallied into ``report``. The same object when nothing moved."""
    run = _Pass(chunk, index if _anchors_quotes(chunk) else None, not window_open(chunk), report)
    if run.index is None and not run.ordered:
        return data
    return _inner(data, "", run)
