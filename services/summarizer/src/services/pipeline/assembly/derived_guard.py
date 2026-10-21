"""Nothing derived from nothing (output-quality D13, R10).

A scalar the reader takes as a fact — a score, a tier, a claim status, a rest
time, a duration, a date — ships only when the extraction carries it on an item
that is anchored in the video: a timestamp the reader can seek to and check.
A verdict — a score, a tier or a status, every scalar of a ``rating`` block —
also needs the creator's words that state it (2.0 c, G1-4: S09's S–D tiers were
a rarity bracket the creator never gave): a non-empty ``quote`` on the item,
which by then has survived quote verification (``pipeline/quote_verify`` clears
an unverified one before assembly reads the extraction). An unanchored or
unquoted scalar is dropped, never defaulted; the item keeps its prose and the
number, rank or status goes. A comparison row is a radar axis only when both
sides are numbers. Every assembler passes its scalars through this module, so
the rule lives in one place.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any, TypeGuard

# Where an item says when it is said, in the order the normalizers read them.
ANCHOR_KEYS: tuple[str, ...] = ("timestamp", "seconds", "startSeconds")

# A comparison cell that IS a number (optionally a currency/approx prefix and a
# short unit): "4,422 mAh", "$249", "6.1 in", "48". A sentence that merely
# contains a digit ("Sony WF-1000XM4", "AirPods Pro 2 — placed…") is not an
# axis; scoring it invented radar shapes. Mirrors NUMERIC_CELL in
# apps/web …/interactive/comparison/radar-scoring.ts.
_NUMERIC_CELL = re.compile(r"^[$€£¥₪~≈<>]?\s*[-+]?\d[\d,]*(?:\.\d+)?\s*[A-Za-z%°µ/]{0,8}$")


# The facts D13 names, by the key a domain block carries them under. The
# overview copies a block's scalars without knowing its schema, so it asks by name.
_SCORES = ("score", "maxScore", "rating")
_TIERS = ("tier", "rank")
# A review verdict's badge ("recommended") is the reviewer's status call (2.0 c).
_STATUSES = ("status", "badge")
_DURATIONS = ("duration", "rest", "prepTime", "cookTime", "totalTime", "estimatedTime")
_DATES = ("date", "releaseDate")
DERIVED_FIELDS: frozenset[str] = frozenset((*_SCORES, *_TIERS, *_STATUSES, *_DURATIONS, *_DATES))
# A block whose every scalar is a verdict: a review rating's score, max and label.
DERIVED_BLOCKS: frozenset[str] = frozenset({"rating"})
# The verdicts among them: the creator's call ships only beside the words that make it.
QUOTED_FIELDS: frozenset[str] = frozenset((*_SCORES, *_TIERS, *_STATUSES))


def _is_finite_number(value: Any) -> TypeGuard[int | float]:
    """A number a reader can take as one: not a bool, NaN or ±Infinity (JSON from
    the model may carry NaN / Infinity, which ``json.loads`` accepts)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def anchor_seconds(item: Any) -> int | None:
    """The item's seek point as the extraction stamps it; None when it cannot seek.

    The schema stamps whole seconds as numbers. A string — a clock or a quoted
    number — is not a stamp, nor is a negative or non-finite number; the next
    anchor key is read instead.
    """
    if not isinstance(item, dict):
        return None
    for key in ANCHOR_KEYS:
        value = item.get(key)
        if not _is_finite_number(value) or value < 0:
            continue
        return int(value)
    return None


def is_anchored(item: Any) -> bool:
    return anchor_seconds(item) is not None


def has_quote(item: Any) -> bool:
    """The item carries the words that state its verdict: a non-empty ``quote``."""
    quote = item.get("quote") if isinstance(item, dict) else None
    return isinstance(quote, str) and bool(quote.strip())


def stated_scalars(item: Any, fields: Iterable[str]) -> dict[str, Any]:
    """The named scalars of an anchored item — ``{field: value}`` for the fields
    it carries (not None, not empty); ``{}`` for an unanchored item. A verdict
    field (``QUOTED_FIELDS``) also needs the item's ``quote``."""
    if not is_anchored(item):
        return {}
    quoted = has_quote(item)
    return {
        field: item[field]
        for field in fields
        if item.get(field) not in (None, "") and (quoted or field not in QUOTED_FIELDS)
    }


def stated_number(item: Any, field: str) -> int | float | None:
    """A finite numeric scalar (a score, a maxScore) as ``stated_scalars`` lets it
    through — anchored, and quoted for a verdict; None otherwise."""
    value = stated_scalars(item, (field,)).get(field)
    return value if _is_finite_number(value) else None


def stated_rows(rows: Any, field: str) -> list[dict]:
    """The anchored, quoted rows whose ``field`` is a stated number (a sub-score's score).

    A "9/10" or a NaN fails the FE's number contract, which would remap the
    whole tab to the display_section fallback.
    """
    if not isinstance(rows, list):
        return []
    return [row for row in rows if stated_number(row, field) is not None]


def _is_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, int | float | bool)


def _is_derived(key: str, block_key: str) -> bool:
    return block_key in DERIVED_BLOCKS or key in DERIVED_FIELDS


def _is_verdict(key: str, block_key: str) -> bool:
    return block_key in DERIVED_BLOCKS or key in QUOTED_FIELDS


def stated_block_scalars(block: Any, block_key: str = "") -> dict[str, Any]:
    """A block's non-empty scalars, minus the derived facts it does not state.

    Prose, names and counts always pass; a score, rating, tier, status,
    duration or date (and every scalar of a ``rating`` block) passes only when
    the block itself carries a timestamp, and a verdict — a score, tier or
    status, any scalar of a ``rating`` block — only when it also carries its
    ``quote``. ``block_key`` is the key the block sits under in its parent;
    ``""`` for a domain's top level.
    """
    if not isinstance(block, dict):
        return {}
    anchored = is_anchored(block)
    quoted = anchored and has_quote(block)
    stated: dict[str, Any] = {}
    for key, value in block.items():
        if not _is_scalar(value):
            continue
        if _is_verdict(key, block_key) and not quoted:
            continue
        if anchored or not _is_derived(key, block_key):
            stated[key] = value
    return stated


def _is_numeric_side(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and _NUMERIC_CELL.match(value.strip()) is not None


def is_numeric_axis(row: Any) -> bool:
    """A comparison row scores a radar axis only when both sides are numbers."""
    return (
        isinstance(row, dict)
        and _is_numeric_side(row.get("thisProduct"))
        and _is_numeric_side(row.get("competitor"))
    )
