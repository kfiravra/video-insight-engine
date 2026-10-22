"""Key ideas as spot cards (output-quality 1.1, D10).

A validated ``learning.keyIdeas`` list (every item carries ``claim`` and ``why``)
ships on spot_explorer as key-idea cards: claim → name, why → description, the
quote and seek point as the spot normalizer reads them, the kind as the category
badge, the speaker as the card's attribution, grouped by the items' ``section``.

An interview's Positions (4.1, C10): the run's ``podcast.guests`` open the list as
its first section ("Guest"), each card with the role as its badge and the second the
guest is introduced as its seek point; two or more disagreements (kind
``disagreement``) close it as a section of their own, a lone one stays in its
outline section with its badge.
"""

from __future__ import annotations

from typing import Any

from src.models.idea_types import KEY_IDEA_KINDS
from src.services.pipeline.quote_verify import mark_translated_quote

from .normalizers import _normalize_to_spot
from .talk_asks import shown_kind


def is_key_idea_list(data: Any) -> bool:
    """A validated ``learning.keyIdeas`` list: every item carries ``claim`` and ``why``.

    A news claim has a ``claim`` but never a ``why``, so a claims list that walks
    the demote ladder down to spot_explorer is not read as key ideas.
    """
    items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return bool(items) and all("claim" in item and "why" in item for item in items)


def _key_idea_badge(kind: Any) -> str | None:
    """A key idea's kind as its badge text ("Thesis"); None for no stated kind."""
    if not isinstance(kind, str) or kind.strip().lower() not in KEY_IDEA_KINDS:
        return None
    return kind.strip().capitalize()


def _key_idea_spot(item: dict) -> dict | None:
    """One key idea as a spot card: claim → name, why → description; the quote and the
    seek point as the spot normalizer reads them, the kind as the category badge, the
    speaker (a podcast position's holder) as the card's attribution."""
    spot = _normalize_to_spot(
        {
            "name": item.get("claim") or "",
            "description": item.get("why") or "",
            "quote": item.get("quote"),
            "timestamp": item.get("timestamp"),
        }
    )
    if spot is None:
        return None
    badge = _key_idea_badge(shown_kind(item))
    if badge:
        spot["category"] = badge
    speaker = item.get("speaker")
    if isinstance(speaker, str) and speaker.strip():
        spot["speaker"] = speaker.strip()
    return mark_translated_quote(spot, item)


# Where the ideas that name no section go once the others are sectioned.
_UNSECTIONED = "Other"
_GUEST_SECTION = "Guest"
_GUESTS_SECTION = "Guests"
_DISAGREEMENT_KIND = "disagreement"
_DISAGREEMENTS_SECTION = "Disagreements"
# Fewer disagreements than this stay in their outline sections.
_DISAGREEMENTS_MIN = 2


def podcast_guests(extraction: Any) -> Any:
    """The run's ``podcast.guests`` — Positions opens with them; None without."""
    podcast = extraction.get("podcast") if isinstance(extraction, dict) else None
    return podcast.get("guests") if isinstance(podcast, dict) else None


def guest_spots(guests: Any) -> list[dict]:
    """``podcast.guests`` as spot cards: the name, the role as the badge, what the
    video says of them and the second they are introduced (the spot normalizer)."""
    if not isinstance(guests, list):
        return []
    spots = (_normalize_to_spot(guest) for guest in guests if isinstance(guest, dict))
    return [spot for spot in spots if spot is not None]


def _is_disagreement(item: dict) -> bool:
    kind = item.get("kind")
    return isinstance(kind, str) and kind.strip().lower() == _DISAGREEMENT_KIND


def _idea_label(item: dict, disagreements_apart: bool) -> str:
    """The section an idea's card files under: the disagreements' own when they stand
    apart, else the idea's ``section`` ("" for none)."""
    if disagreements_apart and _is_disagreement(item):
        return _DISAGREEMENTS_SECTION
    section = item.get("section")
    return section.strip() if isinstance(section, str) else ""


def _key_idea_sections(labels: list[str]) -> list[dict]:
    """``section`` → spot sections in first-appearance order, the ideas that name
    none in a trailing "Other" (so every section view adds up to the whole list);
    none below two named sections (one section is the whole list, the FE shows no
    nav for it)."""
    indices: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        if label:
            indices.setdefault(label, []).append(index)
    if len(indices) < 2:
        return []
    unsectioned = [index for index, label in enumerate(labels) if not label]
    if unsectioned:
        indices[_UNSECTIONED] = sorted([*indices.get(_UNSECTIONED, []), *unsectioned])
    return [{"label": label, "spotIndices": spots} for label, spots in indices.items()]


def key_idea_props(data: list, guests: Any = None) -> dict:
    """spot_explorer props for a key-idea list: ``{spots, sections?}``, source order kept,
    the run's guests first (module docstring).

    An idea without a claim or without anything to show beside it is dropped by
    the spot normalizer; the caller applies spot_explorer's minimum.
    """
    guest_cards = guest_spots(guests)
    guest_label = _GUESTS_SECTION if len(guest_cards) > 1 else _GUEST_SECTION
    cards = ((_key_idea_spot(item), item) for item in data if isinstance(item, dict))
    ideas = [(spot, item) for spot, item in cards if spot is not None]
    apart = sum(_is_disagreement(item) for _, item in ideas) >= _DISAGREEMENTS_MIN
    labels = [guest_label] * len(guest_cards) + [_idea_label(item, apart) for _, item in ideas]
    props: dict[str, Any] = {"spots": guest_cards + [spot for spot, _ in ideas]}
    # The disagreements' section trails every other (a stable sort moves it last).
    sections = sorted(
        _key_idea_sections(labels), key=lambda s: s["label"] == _DISAGREEMENTS_SECTION
    )
    if sections:
        props["sections"] = sections
    return props
