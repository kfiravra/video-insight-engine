"""Argued-idea items (output-quality phase 1): what a talk, lecture or podcast claims.

``LearningKeyIdea`` (D10) is the argument tab's item: the claim, why the speaker
holds it, the line that carries it, where it is said, the part of the video it
belongs to and what kind of idea it is. ``PodcastQA`` is an interview's question
and the answer it got (C10); ``TechTopic`` a concept a tutorial explains, with
where it explains it (L25); ``Claim`` a checkable claim a news piece, talk,
podcast or tutorial makes and what the video does with it (D11). Each mirrors its TS interface
(``packages/types/src/vie-response.ts``; ``tests/test_ts_pydantic_parity.py``).
Validation is lenient on everything but the heading field (and an exchange's
answer): a malformed stamp or an unknown kind becomes ``None``, so one bad value
never drops the item.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator

from ..utils.data_helpers import parse_timestamp_to_seconds

logger = logging.getLogger(__name__)

# The kinds of argued idea (D10) — the spot card's category badge; ``disagreement``
# (4.1) is where host and guest differ or one pushes back.
KEY_IDEA_KINDS: tuple[str, ...] = (
    "thesis",
    "argument",
    "announcement",
    "number",
    "story",
    "ask",
    "disagreement",
)
# What the video does with a claim (D11): states it, supports it with evidence, or
# contests it. Never a verdict of ours — "verified" and "disputed" are not words here.
CLAIM_STATUSES: tuple[str, ...] = ("stated", "supported", "contested")
# A whole transcript clock, bracketed or not: "1:35", "1:02:05", "[4:32]" (the
# groups after the first are clock digits, so "12:60" is no clock).
_CLOCK_RE = re.compile(r"\[?(\d{1,3}(?::[0-5]\d){1,2})\]?")


def seconds_or_none(value: Any) -> int | None:
    """Seconds into the video: a non-negative whole number or a transcript clock
    (``"1:35"`` → 95), else None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str) and (clock := _CLOCK_RE.fullmatch(value.strip())):
        return parse_timestamp_to_seconds(clock.group(1))
    try:
        seconds = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return seconds if seconds >= 0 else None


def text_or_none(value: Any) -> str | None:
    """Stripped text, None when blank or not text."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def absent(value: Any) -> bool:
    """``exclude_if`` of a field only a non-English run writes (``quoteEn``,
    ``questionEn``, 4.2): unset, it is left out of the dump, so an English run's
    extraction keeps its bytes."""
    return value is None


class KeyIdeaFigure(BaseModel):
    """The figure a number key idea states, as the speaker says it (Kfir 2026-10-21):
    the numbers strip shows it as given — nothing is mined from the claim's text."""

    value: str
    label: str


def _figure_or_none(value: Any) -> dict | None:
    """``{value, label}`` with both filled (a number value becomes its text); None
    otherwise, so a half-given figure never costs the idea it belongs to."""
    if not isinstance(value, dict):
        return None
    raw = value.get("value")
    shown = str(raw).strip() if isinstance(raw, int | float) and not isinstance(raw, bool) else raw
    figure, label = text_or_none(shown), text_or_none(value.get("label"))
    return {"value": figure, "label": label} if figure and label else None


class LearningKeyIdea(BaseModel):
    """One argued idea (D10): ``claim`` → the card's name, ``why`` → its description."""

    claim: str
    why: str = ""
    # The speaker's own words that carry the claim, verbatim (R10).
    quote: str | None = None
    # The quote's English when the speaker is not speaking English (D12, 4.2).
    quote_en: str | None = Field(None, alias="quoteEn", exclude_if=absent)
    # Where the claim is said, in seconds — the seek chip (R2).
    timestamp: int | None = None
    # The part of the video the idea belongs to — the card's section.
    section: str | None = None
    kind: str | None = None
    # Who holds the idea when more than one person speaks (a podcast's positions).
    speaker: str | None = None
    # A number idea's figure as the speaker says it — the numbers strip's tile.
    figure: KeyIdeaFigure | None = None

    @field_validator("figure", mode="before")
    @classmethod
    def coerce_figure(cls, v: Any) -> dict | None:
        return _figure_or_none(v)

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("quote", "quote_en", "section", "speaker", mode="before")
    @classmethod
    def coerce_optional_text(cls, v: Any) -> str | None:
        return text_or_none(v)

    @field_validator("kind", mode="before")
    @classmethod
    def coerce_kind(cls, v: Any) -> str | None:
        """One of ``KEY_IDEA_KINDS`` (any case), else None — never a guessed kind."""
        kind = text_or_none(v)
        return kind.lower() if kind and kind.lower() in KEY_IDEA_KINDS else None


class PodcastQA(BaseModel):
    """One interview exchange (C10): the question asked and the answer it got.

    Ships as a moment: the question is its label (cut to the track's label
    length, the full question kept as ``question``), the answer its description.
    """

    question: str
    # The question's English when it was asked in another language (D12, 4.2).
    question_en: str | None = Field(None, alias="questionEn", exclude_if=absent)
    # Required: an exchange without its answer is dropped by validation instead of
    # shipping as a label-only moment.
    answer: str = Field(validation_alias=AliasChoices("answer", "description"))
    # Who answers — the guest, usually.
    speaker: str | None = None
    timestamp: int | None = None
    quote: str | None = None
    quote_en: str | None = Field(None, alias="quoteEn", exclude_if=absent)

    @field_validator("answer")
    @classmethod
    def require_answer(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("an exchange needs its answer")
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("speaker", "quote", "quote_en", "question_en", mode="before")
    @classmethod
    def coerce_optional_text(cls, v: Any) -> str | None:
        return text_or_none(v)


class TechTopic(BaseModel):
    """A concept a tutorial explains (L25): the name, the explanation, where it is said."""

    model_config = {"populate_by_name": True}

    name: str = Field(validation_alias=AliasChoices("name", "topic", "label", "title"))
    description: str = ""
    timestamp: int | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)


class Claim(BaseModel):
    """A checkable claim (D11; ``news.claims`` generalised to learning, podcast, tech).

    ``status`` is one of ``CLAIM_STATUSES`` or nothing: any other word ("verified",
    "disputed", the schema's own "stated | supported | contested") is refused —
    the status goes, never the claim (the lenient enum rule of the other domain
    models: a verdict badge, a tier). A status is read only beside the
    ``timestamp`` it can be checked at and the ``quote`` that makes the claim
    (D13, output-quality 2.0 c).
    """

    model_config = {"populate_by_name": True}

    claim: str
    # None when the video attributes the claim to no one — never an invented "Reporter".
    source: str | None = None
    status: str | None = None
    source_citation: str | None = Field(None, alias="sourceCitation")
    quote: str | None = None
    quote_en: str | None = Field(None, alias="quoteEn", exclude_if=absent)
    timestamp: int | None = None

    @field_validator("status", mode="before")
    @classmethod
    def check_status(cls, v: Any) -> str | None:
        status = (text_or_none(v) or "").lower()
        if status in CLAIM_STATUSES:
            return status
        if status:
            logger.warning("Claim status %r refused (not one of %s)", v, ", ".join(CLAIM_STATUSES))
        return None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("source", "source_citation", "quote", "quote_en", mode="before")
    @classmethod
    def coerce_optional_text(cls, v: Any) -> str | None:
        return text_or_none(v)
