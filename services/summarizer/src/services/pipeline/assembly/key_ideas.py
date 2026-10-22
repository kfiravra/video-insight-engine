"""Key ideas as spot cards (output-quality 1.1, D10).

A validated ``learning.keyIdeas`` list (every item carries ``claim`` and ``why``)
ships on spot_explorer as key-idea cards: claim → name, why → description, the
quote and seek point as the spot normalizer reads them, the kind as the category
badge, the speaker as the card's attribution, grouped by the items' ``section``.
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


def key_idea_props(data: list) -> dict:
    """spot_explorer props for a key-idea list: ``{spots, sections?}``, source order kept.

    An idea without a claim or without anything to show beside it is dropped by
    the spot normalizer; the caller applies spot_explorer's minimum.
    """
    spots: list[dict] = []
    labels: list[str] = []
    for item in (i for i in data if isinstance(i, dict)):
        spot = _key_idea_spot(item)
        if spot is None:
            continue
        spots.append(spot)
        section = item.get("section")
        labels.append(section.strip() if isinstance(section, str) else "")
    props: dict[str, Any] = {"spots": spots}
    sections = _key_idea_sections(labels)
    if sections:
        props["sections"] = sections
    return props
