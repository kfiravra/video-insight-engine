"""Rank-sampling for capped lists (output-quality 0b.2 / 0b.3).

A capped list used to be head-sliced (the tail of a long video vanished) or
evenly sampled by index (a timeline's headline beats fell between the
strides). Every cap now keeps the items by *score* — salience for moments,
richness for reference lists — and only falls back to even spacing inside one
score tier, so the kept items still span the whole video. The original
(chronological) order is always preserved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

# content-model.md §9 — Moments sized to the video: (upper minute bound, minutes
# per moment, band minimum, band maximum). The last band has no upper bound.
_MOMENT_BANDS: tuple[tuple[float, int, int, int], ...] = (
    (15, 3, 4, 6),
    (45, 4, 8, 12),
    (90, 5, 12, 18),
    (float("inf"), 6, 18, 60),
)
MOMENT_CAP_MAX = _MOMENT_BANDS[-1][3]

_DIGIT = re.compile(r"\d")
# A verbatim quote: double quotes, or a single-quoted run of at least two words.
_QUOTE = re.compile(r"[\"“”]|(?:^|\s)[‘'][^'’]+\s[^'’]+[’'](?=[\s,.;:!?]|$)")

# Fields whose presence makes a list item richer than its siblings — the
# evidence the FE can show (R10) beyond the bare name.
_RICH_FIELDS: tuple[str, ...] = (
    "description",
    "detail",
    "definition",
    "example",
    "analogy",
    "evidence",
    "quote",
    "note",
    "notes",
    "explanation",
    "why",
    "tips",
    "specs",
    "formCues",
    "realWorldExample",
)
_TIMESTAMP_FIELDS: tuple[str, ...] = ("timestamp", "seconds", "startTime", "start_time")
# A text field this long carries an explanation, not a label.
_LONG_TEXT = 120


def even_indices(n: int, cap: int) -> list[int]:
    """``cap`` evenly spaced indices into a list of ``n``, first and last kept.

    Rounding collisions may yield slightly fewer than ``cap``.
    """
    if cap <= 0 or n <= 0:
        return []
    if n <= cap:
        return list(range(n))
    if cap == 1:
        return [0]
    step = (n - 1) / (cap - 1)
    return sorted({round(i * step) for i in range(cap)})


def evenly_sample(items: Sequence[T], cap: int) -> list[T]:
    """``cap`` items at evenly spaced indices, keeping first and last."""
    return [items[i] for i in even_indices(len(items), cap)]


def rank_sample(items: Sequence[T], cap: int, score: Callable[[T], int]) -> list[T]:
    """Keep ``cap`` items by score, in their original order.

    Tiers (equal scores) are taken whole from the highest down; the first tier
    that does not fit is evenly sampled (its first and last kept), so a long
    video's tail survives inside every tier. Items beyond the cap are dropped.
    """
    if cap <= 0:
        return []
    if len(items) <= cap:
        return list(items)
    tiers: dict[int, list[int]] = {}
    for index, item in enumerate(items):
        tiers.setdefault(score(item), []).append(index)
    chosen: list[int] = []
    room = cap
    for tier_score in sorted(tiers, reverse=True):
        tier = tiers[tier_score]
        if len(tier) <= room:
            chosen.extend(tier)
            room -= len(tier)
            continue
        chosen.extend(tier[i] for i in even_indices(len(tier), room))
        break
    return [items[i] for i in sorted(chosen)]


def moment_cap_for_duration(duration_seconds: float | None, default: int) -> int:
    """The Moments cap for a video of this length (content-model §9), else ``default``."""
    if not duration_seconds or duration_seconds <= 0:
        return default
    minutes = duration_seconds / 60
    for upper, per_moment, low, high in _MOMENT_BANDS:
        if minutes <= upper:
            return max(low, min(high, int(minutes // per_moment)))
    return default


def _text(item: Any, key: str) -> str:
    value = item.get(key) if isinstance(item, dict) else None
    return value if isinstance(value, str) else ""


# ─── Moment anchors: what the rest of the extraction points at ───

# A moment another surface also names (a key point stamped there, a takeaway
# that says the same thing) is a headline beat — the talk-trace's own measure
# of salience. Time anchors: ``timestamp`` (+ ``endTimestamp`` span) of other
# items; text anchors: their title-level fields and the hero takeaways.
_ANCHOR_TITLE_FIELDS: tuple[str, ...] = ("title", "name", "claim", "text")
_ANCHOR_WINDOW_SECONDS = 45.0
# The moment list itself (``learning.timestamps``) never anchors its own items.
_MOMENT_LIST_FIELDS = frozenset({"timestamps"})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'’\-]+")
_STOPWORDS = frozenset(
    "the a an and of to in on for with by at from as is are was were be it its this that "
    "these those his her their our your you we they he she not no or but so if than into "
    "over about after before more most very just can will would should could do does did "
    "have has had one two how what why when where which who while also all any some such "
    "only own same other new now out up down off".split()
)
_TEXT_ANCHOR_OVERLAP = 0.6
_MAX_ANCHOR_POINTS = 3


class MomentAnchors:
    """Timestamps/spans and title token sets of every non-moment item in the extraction."""

    __slots__ = ("times", "texts")

    def __init__(
        self, times: Sequence[tuple[float, float | None]], texts: Sequence[frozenset[str]]
    ) -> None:
        self.times = tuple(times)
        self.texts = tuple(texts)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2)


def _anchor_item(
    item: Any, times: list[tuple[float, float | None]], texts: list[frozenset[str]]
) -> None:
    if isinstance(item, str):
        texts.append(_tokens(item))
        return
    if not isinstance(item, dict):
        return
    title = " ".join(_text(item, key) for key in _ANCHOR_TITLE_FIELDS).strip()
    if title:
        texts.append(_tokens(title))
    start, end = item.get("timestamp"), item.get("endTimestamp")
    if isinstance(start, (int, float)) and not isinstance(start, bool):
        times.append((float(start), float(end) if isinstance(end, (int, float)) else None))


def collect_moment_anchors(
    extraction: Mapping[str, Any] | None, synthesis: Mapping[str, Any] | None
) -> MomentAnchors:
    """Every list item of every domain (the moment lists excluded) plus the hero takeaways."""
    times: list[tuple[float, float | None]] = []
    texts: list[frozenset[str]] = []
    for domain_data in (extraction or {}).values():
        if not isinstance(domain_data, dict):
            continue
        for field, value in domain_data.items():
            if field in _MOMENT_LIST_FIELDS or not isinstance(value, list):
                continue
            for item in value:
                _anchor_item(item, times, texts)
    for takeaway in (synthesis or {}).get("keyTakeaways") or []:
        if isinstance(takeaway, str):
            texts.append(_tokens(takeaway))
    return MomentAnchors(times, [t for t in texts if t])


def _anchor_points(item: dict, anchors: MomentAnchors) -> int:
    seconds = item.get("seconds")
    hits = 0
    if isinstance(seconds, (int, float)):
        hits += sum(
            1
            for start, end in anchors.times
            if abs(start - seconds) <= _ANCHOR_WINDOW_SECONDS
            or (end is not None and start <= seconds <= end)
        )
    label = _tokens(_text(item, "label"))
    if label:
        hits += sum(
            1 for text in anchors.texts if len(label & text) / len(label) >= _TEXT_ANCHOR_OVERLAP
        )
    return min(hits, _MAX_ANCHOR_POINTS)


def moment_salience(item: Any, anchors: MomentAnchors | None = None) -> int:
    """R7: a moment with substance ranks first — what was established (description),
    then a verbatim quote, then a number — plus the beats the rest of the extraction
    points at (``anchors``); even spacing decides among equals."""
    if not isinstance(item, dict):
        return 0
    label, description = _text(item, "label"), _text(item, "description")
    score = 2 if description.strip() else 0
    if _QUOTE.search(label) or _QUOTE.search(description):
        score += 1
    if _DIGIT.search(label) or _DIGIT.search(description):
        score += 1
    if anchors is not None:
        score += _anchor_points(item, anchors)
    return score


def _position(item: Any, index: int, duration: float | None) -> float:
    """Where a moment sits: its seconds when the duration is known, else its index."""
    seconds = item.get("seconds") if isinstance(item, dict) else None
    if duration and isinstance(seconds, (int, float)):
        return float(seconds)
    return float(index)


def _pick_per_window(
    items: Sequence[Any], scores: Sequence[int], cap: int, duration: float | None
) -> set[int]:
    """The most salient moment of each of ``cap`` equal strides (ties → nearest the
    stride's centre); strides by time when the duration is known, else by index."""
    span = float(duration) if duration and duration > 0 else float(len(items))
    stride = span / cap
    best: dict[int, tuple[tuple[int, float], int]] = {}
    for index, item in enumerate(items):
        position = _position(item, index, duration)
        window = min(cap - 1, max(0, int(position // stride)))
        key = (scores[index], -abs(position - (window + 0.5) * stride))
        if window not in best or key > best[window][0]:
            best[window] = (key, index)
    return {index for _, index in best.values()}


def sample_moments(
    items: Sequence[Any],
    cap: int,
    duration: float | None,
    anchors: MomentAnchors | None = None,
) -> list[Any]:
    """``cap`` moments in time order: the video is cut into ``cap`` equal strides and
    each stride keeps its most salient moment, so the headline beats survive AND every
    stretch of the video stays represented (R7, R9); strides without a moment hand
    their slot to the best moments left."""
    if cap <= 0:
        return []
    if len(items) <= cap:
        return list(items)
    scores = [moment_salience(item, anchors) for item in items]
    chosen = _pick_per_window(items, scores, cap, duration)
    room = cap - len(chosen)
    if room > 0:
        rest = [i for i in range(len(items)) if i not in chosen]
        chosen.update(rank_sample(rest, room, lambda i: scores[i]))
    return [items[i] for i in sorted(chosen)]


def _populated(value: Any) -> bool:
    return value not in (None, "", [], {})


def item_richness(item: Any) -> int:
    """R9/R10: an item carrying evidence outranks a bare name when a list is capped —
    one point per populated evidence field, one for a timestamp, one for a long text."""
    if not isinstance(item, dict):
        return 0
    score = sum(1 for key in _RICH_FIELDS if _populated(item.get(key)))
    if any(isinstance(item.get(key), (int, float)) for key in _TIMESTAMP_FIELDS):
        score += 1
    longest = max((len(v) for v in item.values() if isinstance(v, str)), default=0)
    if longest >= _LONG_TEXT:
        score += 1
    return score


def concept_richness(concept: Any) -> int:
    """A concept with an example, an analogy, a timestamp and connections outranks a
    name + definition when the canvas is capped."""
    if not isinstance(concept, dict):
        return 0
    score = sum(1 for key in ("example", "analogy", "timestamp") if _populated(concept.get(key)))
    connections = concept.get("connections")
    if isinstance(connections, list):
        score += min(len(connections), 3)
    return score
