"""Argued-idea items (output-quality phase 1): what a talk, lecture or podcast claims.

``LearningKeyIdea`` (D10) is the argument tab's item: the claim, why the speaker
holds it, the line that carries it, where it is said, the part of the video it
belongs to and what kind of idea it is. ``PodcastQA`` is an interview's question
and the answer it got (C10); ``TechTopic`` a concept a tutorial explains, with
where it explains it (L25); ``Claim`` a checkable claim a news piece, talk,
podcast or tutorial makes and what the video does with it (D11). Each mirrors its TS interface
(``packages/types/src/vie-response.ts``; ``tests/test_ts_pydantic_parity.py``).
Validation is lenient on everything but the heading field: a malformed stamp or
an unknown kind becomes ``None``, so one bad value never drops the item.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator

# The six kinds of argued idea (D10) — the spot card's category badge.
KEY_IDEA_KINDS: tuple[str, ...] = ("thesis", "argument", "announcement", "number", "story", "ask")
# What the video does with a claim (D11): states it, supports it with evidence, or
# contests it. Never a verdict of ours — "verified" and "disputed" are not words here.
CLAIM_STATUSES: tuple[str, ...] = ("stated", "supported", "contested")


def seconds_or_none(value: Any) -> int | None:
    """Seconds into the video: a non-negative whole number, else None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
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

    @field_validator("quote", "section", "speaker", mode="before")
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
    answer: str = Field(default="", validation_alias=AliasChoices("answer", "description"))
    # Who answers — the guest, usually.
    speaker: str | None = None
    timestamp: int | None = None
    quote: str | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("speaker", "quote", mode="before")
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
    ``timestamp`` it can be checked at (D13).
    """

    model_config = {"populate_by_name": True}

    claim: str
    # None when the video attributes the claim to no one — never an invented "Reporter".
    source: str | None = None
    status: str | None = None
    source_citation: str | None = Field(None, alias="sourceCitation")
    timestamp: int | None = None

    @field_validator("status", mode="before")
    @classmethod
    def check_status(cls, v: Any) -> str | None:
        status = (text_or_none(v) or "").lower()
        return status if status in CLAIM_STATUSES else None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("source", "source_citation", mode="before")
    @classmethod
    def coerce_optional_text(cls, v: Any) -> str | None:
        return text_or_none(v)
