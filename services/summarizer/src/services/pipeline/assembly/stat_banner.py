"""The answer tab's numbers strip — stat_banner (C8, output-quality 0b.11).

A key point whose title holds a figure and whose item carries a timestamp
becomes a tile: ``{label, value, emoji?, meaning, timestamp}``. ``value`` is the
figure exactly as the title states it, ``label`` a short phrase from the title
around it, ``meaning`` the whole title. ``attachments`` decides which tab
carries the strip and re-exports these names.
"""

from __future__ import annotations

import re
from typing import Any

from .derived_guard import anchor_seconds
from .text_utils import truncate_words

# stat_banner (C8): the answer tab carries a numbers strip when the key points
# hold at least this many figures with a timestamp; at most _STAT_BANNER_MAX
# tiles so it stays a strip, not a wall.
_STAT_BANNER_MIN = 5
_STAT_BANNER_MAX = 8
# The tile label is the FE's small-caps line above the value: a short phrase
# from the title — the clause the figure closes ("Rust backend: 99%"), else the
# one it opens ("2 trillion photos a year, …") — at most this many words, minus
# the hedges and connectives a cut leaves dangling. The full title travels in
# ``meaning``.
_STAT_LABEL_MAX_CHARS = 56
_STAT_LABEL_MAX_WORDS = 4
_LABEL_DANGLING = frozenset(
    "a an the of to in on at for from by with vs vs. and or but so then now as about over "
    "under nearly around roughly almost just only up than is are was were within".split()
)
_CLAUSE_BREAK = re.compile(r"[,:;()—–]|\s-\s")
_CLAUSE_EDGE = " ,:;()—–-"
# A figure as a speaker says one: "150,000", "$20 million", "99%", "1,000x",
# "0.5 MB" / "0.5MB", "3:33", "20 months". Thousands separators group exactly
# three digits, so a trailing comma ("2023,") is never part of the figure. A
# letter, digit, point or comma glued to either end ("1950s", "v2.5", "x86",
# the "5" of "1,5") is not a figure.
_NUMBER_RE = re.compile(
    r"(?<![\w.,])"
    r"(?P<figure>[$€£]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?::\d{2})?"
    r"(?:\s?(?:%|x|×|percent|thousand|million|billion|trillion"
    r"|MB|GB|KB|TB|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?|lines?|apps?))?)"
    r"(?![\w])",
    re.IGNORECASE,
)
# A bare year or a bare single digit names something ("in 2023", "Rails 8",
# "Python 3") rather than measuring it: never a stat.
_YEAR = re.compile(r"(?:19|20)\d{2}")


def _is_figure(figure: str) -> bool:
    """False for a bare year or a bare single digit (a name, not a stat)."""
    if not figure.isdigit():
        return True
    return len(figure) > 1 and _YEAR.fullmatch(figure) is None


def _stat_figure(title: str) -> re.Match[str] | None:
    """The headline figure in a key-point title: a figure with a unit, suffix,
    currency or separator beats a bare number, a multi-digit number beats a
    single digit, and the earliest wins a tie. None when the title holds no
    figure."""
    best: tuple[int, re.Match[str]] | None = None
    for match in _NUMBER_RE.finditer(title):
        figure = match.group("figure")
        if not _is_figure(figure):
            continue
        digits = sum(ch.isdigit() for ch in figure)
        score = (2 if not figure.isdigit() else 0) + (1 if digits >= 2 else 0)
        if best is None or score > best[0]:
            best = (score, match)
    return best[1] if best else None


def _stat_value(title: str) -> str | None:
    """The headline figure's text (``_stat_figure``); None without one."""
    match = _stat_figure(title)
    return match.group("figure") if match else None


def _phrase(text: str) -> str:
    """At most _STAT_LABEL_MAX_WORDS words of ``text``, dangling connectives trimmed."""
    words = text.split()[:_STAT_LABEL_MAX_WORDS]
    while words and words[-1].casefold() in _LABEL_DANGLING:
        words.pop()
    return " ".join(words)


def _stat_label(title: str, figure: re.Match[str]) -> str:
    """The tile's caption: the clause before the figure, else the clause after
    it; the capped title when neither leaves a word."""
    before = _CLAUSE_BREAK.split(title[: figure.start()].rstrip(_CLAUSE_EDGE))[-1]
    label = _phrase(before)
    if not label:
        label = _phrase(_CLAUSE_BREAK.split(title[figure.end() :].lstrip(_CLAUSE_EDGE))[0])
    return truncate_words(label or title, _STAT_LABEL_MAX_CHARS)


def _key_points(extraction: dict | None, domain: str) -> list[dict]:
    """The first ``keyPoints`` list in the extraction, primary domain first."""
    if not isinstance(extraction, dict):
        return []
    ordered = [extraction.get(domain), *(v for k, v in extraction.items() if k != domain)]
    for domain_data in ordered:
        points = domain_data.get("keyPoints") if isinstance(domain_data, dict) else None
        if isinstance(points, list) and points:
            return [kp for kp in points if isinstance(kp, dict)]
    return []


def _stat_tile(point: dict) -> dict[str, Any] | None:
    """One key point as a tile; None without a figure or a timestamp."""
    title = str(point.get("title") or "").strip()
    figure = _stat_figure(title) if title else None
    timestamp = anchor_seconds(point)
    if figure is None or timestamp is None:
        return None
    tile: dict[str, Any] = {
        "label": _stat_label(title, figure),
        "value": figure.group("figure"),
        "meaning": title,
        "timestamp": timestamp,
    }
    if isinstance(point.get("emoji"), str) and point["emoji"].strip():
        tile["emoji"] = point["emoji"].strip()
    return tile


def _stat_banner_attachment(extraction: dict | None, domain: str) -> dict | None:
    """Build the answer tab's numbers strip (C8) from the numeric key points:
    ``{label, value, emoji?, meaning, timestamp}`` per tile, where ``label`` +
    ``value`` are what StatBanner renders and ``meaning`` + ``timestamp`` ride
    along (the FE schema passes extra keys through). Built here, not via the
    registered assembler, because that one keeps only label/value/emoji and
    the timestamp is the point (R2). None below _STAT_BANNER_MIN figures."""
    from .core import _validate_assembled_props  # local import avoids cycle

    tiles = (_stat_tile(point) for point in _key_points(extraction, domain))
    stats = [tile for tile in tiles if tile is not None]
    if len(stats) < _STAT_BANNER_MIN:
        return None
    props = {"stats": stats[:_STAT_BANNER_MAX]}
    if not _validate_assembled_props("stat_banner", props):
        return None
    return {"slot": "bottom", "component": "stat_banner", "props": props, "size": "strip"}
