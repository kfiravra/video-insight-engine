"""Spot cards for the tips and picks the spot keys cannot read (output-quality R6, pC review).

info_grid never stands alone (R6), so the planner routes ``food.tips``, ``fitness.tips``
and ``gaming.loadout`` to spot_explorer — the registry lists it for all three. The spot
normalizer reads a card from a name key and a description key, and neither shape has
both: a tip is ``{type, text}``, a pick ``{item, category, note}``. Every card was
refused, and the tab walked the demote ladder down to the very info_grid R6 rules out
(S06 "Chef Tips", S09 "Meta & Deck Talk", S10 "Form & Tips").

The grid's row normalizer already reads both shapes — a tip's headline and body cut
from its text with its ``type`` as a badge, a pick's note as its value — so a card the
spot keys refuse, or leave without a body, is read as that row (:func:`spot_card`):
key → name, value → description, the badge (or the item's own short ``category``) →
category, any other evidence as prose after the description. The item's own extras
ride as on any spot (its seek point, quote, stated facts). A row with neither a value
nor a badge (a bare headline) is still no card.
"""

from __future__ import annotations

from .info_grid_items import _category_badge, _normalize_info_grid_item
from .normalizers import _normalize_to_spot, _spot_extras
from .stamp_marker import lift_stamp_marker


def _with_evidence(spot: dict, evidence: str) -> dict:
    """``evidence`` after the description — unless it is the card's own quote line."""
    if not evidence or evidence == spot.get("quote"):
        return spot
    description = spot["description"]
    spot["description"] = f"{description} — {evidence}" if description else evidence
    return spot


def _spot_from_row(item: dict) -> dict | None:
    """``item`` as the info_grid row normalizer reads it, carded; None for a bare headline."""
    row = _normalize_info_grid_item(item)
    if row is None:
        return None
    category = row.get("category") or _category_badge(item.get("category"))
    value = str(row.get("value") or "").strip()
    if not value and not category:
        return None
    spot = {"name": row["key"], "description": value, **_spot_extras(item)}
    if category:
        spot["category"] = category
    return _with_evidence(spot, str(row.get("evidence") or "").strip())


def _same_card_with_body(spot: dict, row_spot: dict) -> bool:
    """Whether the row reading heads ``spot``'s card and gives it a body."""
    return row_spot["name"] == spot["name"] and bool(row_spot["description"])


def spot_card(item: dict) -> dict | None:
    """One item as a spot card: by the spot keys, else as its info_grid row (module
    docstring) — a seek point written into its text lifted first (``stamp_marker``).
    A spot-key card with no description takes the row's body when the row heads the
    same card: a pick's name with its seek point and its note lost otherwise (S09)."""
    item = lift_stamp_marker(item)
    spot = _normalize_to_spot(item)
    if spot is not None and spot["description"]:
        return spot
    row_spot = _spot_from_row(item)
    if spot is None:
        return row_spot
    return row_spot if row_spot is not None and _same_card_with_body(spot, row_spot) else spot
