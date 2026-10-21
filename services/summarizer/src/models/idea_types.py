"""Argued-idea items (output-quality phase 1): what a talk, lecture or podcast claims.

``LearningKeyIdea`` (D10) is the argument tab's item: the claim, why the speaker
holds it, the line that carries it, where it is said, the part of the video it
belongs to and what kind of idea it is. Mirrors TS ``KeyIdeaItem``
(``packages/types/src/vie-response.ts``; ``tests/test_ts_pydantic_parity.py``).
Validation is lenient on everything but the claim: a malformed stamp or an
unknown kind becomes ``None``, so one bad value never drops the idea.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

# The six kinds of argued idea (D10) — the spot card's category badge.
KEY_IDEA_KINDS: tuple[str, ...] = ("thesis", "argument", "announcement", "number", "story", "ask")


def seconds_or_none(value: Any) -> int | None:
    """Seconds into the video: a non-negative whole number, else None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        seconds = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return seconds if seconds >= 0 else None


def _text_or_none(value: Any) -> str | None:
    """Stripped text, None when blank or not text."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


class LearningKeyIdea(BaseModel):
    """One argued idea (D10): ``claim`` → the card's name, ``why`` → its description."""

    claim: str
    why: str = ""
    # The speaker's own words that carry the claim, verbatim (R10).
    quote: str | None = None
    # Where the claim is said, in seconds — the seek chip (R2).
    timestamp: int | None = None
    # The part of the video the idea belongs to — the card's section.
    section: str | None = None
    kind: str | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("quote", "section", mode="before")
    @classmethod
    def coerce_optional_text(cls, v: Any) -> str | None:
        return _text_or_none(v)

    @field_validator("kind", mode="before")
    @classmethod
    def coerce_kind(cls, v: Any) -> str | None:
        """One of ``KEY_IDEA_KINDS`` (any case), else None — never a guessed kind."""
        kind = _text_or_none(v)
        return kind.lower() if kind and kind.lower() in KEY_IDEA_KINDS else None
