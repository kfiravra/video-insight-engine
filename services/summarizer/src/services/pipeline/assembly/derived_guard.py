"""Nothing derived from nothing (output-quality D13, R10).

A scalar the reader takes as a fact — a score, a tier, a claim status, a rest
time, a duration, a date — ships only when the extraction carries it on an item
that is anchored in the video: a timestamp the reader can seek to and check.
An unanchored scalar is dropped, never defaulted; the item keeps its prose and
the number, rank or status goes. A comparison row is a radar axis only when
both sides are numbers. Every assembler passes its scalars through this module,
so the rule lives in one place.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Where an item says when it is said, in the order the normalizers read them.
ANCHOR_KEYS: tuple[str, ...] = ("timestamp", "seconds", "startSeconds")

# A comparison cell that IS a number (optionally a currency/approx prefix and a
# short unit): "4,422 mAh", "$249", "6.1 in", "48". A sentence that merely
# contains a digit ("Sony WF-1000XM4", "AirPods Pro 2 — placed…") is not an
# axis; scoring it invented radar shapes. Mirrors NUMERIC_CELL in
# apps/web …/interactive/comparison/radar-scoring.ts.
_NUMERIC_CELL = re.compile(r"^[$€£¥₪~≈<>]?\s*[-+]?\d[\d,]*(?:\.\d+)?\s*[A-Za-z%°µ/]{0,8}$")


def anchor_seconds(item: Any) -> int | None:
    """The item's seek point as the extraction stamps it; None when it cannot seek."""
    if not isinstance(item, dict):
        return None
    for key in ANCHOR_KEYS:
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            continue
        return int(value)
    return None


def is_anchored(item: Any) -> bool:
    return anchor_seconds(item) is not None


def stated_scalars(item: Any, fields: Iterable[str]) -> dict[str, Any]:
    """The named scalars of an anchored item — ``{field: value}`` for the fields
    it carries (not None, not empty); ``{}`` for an unanchored item."""
    if not is_anchored(item):
        return {}
    return {field: item[field] for field in fields if item.get(field) not in (None, "")}


def stated_number(item: Any, field: str) -> int | float | None:
    """A numeric scalar (a score, a maxScore) of an anchored item; None otherwise."""
    value = stated_scalars(item, (field,)).get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def stated_rows(rows: Any, field: str) -> list[dict]:
    """The rows that are anchored and carry ``field`` (sub-scores, ranked items)."""
    if not isinstance(rows, list):
        return []
    return [row for row in rows if field in stated_scalars(row, (field,))]


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
