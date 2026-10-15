"""Post-promotion dedupe (output-quality 0b.5 — R5, L26).

Reconcile dedupes planned tabs by (dataSource, component) before extraction;
promotion (flash_deck → concept_canvas) and the sibling fallback can still make
two tabs ship the same items. The assembled props decide: a tab goes when at
least ``DUPLICATE_OVERLAP`` of its items ship on a tab that stays. A tab's
items are its component's own list (``component_lists.item_list_keys`` — a
canvas's group labels never dilute the overlap); every list prop only for a
component the table does not know. ``core`` re-exports the public names.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from .component_lists import item_list_keys

logger = logging.getLogger(__name__)

DUPLICATE_OVERLAP = 0.5
DUPLICATE_TAB = "duplicate_tab"
# An item's identity is its first text field in this order. A code snippet is its
# code (one file evolving through a tutorial keeps its filename) and a budget row
# its category — both last, so no other item changes identity.
_ITEM_TEXT_KEYS: tuple[str, ...] = (
    "label",
    "title",
    "name",
    "term",
    "question",
    "claim",
    "text",
    "front",
    "step",
    "instruction",
    "key",
    "feature",
    "item",
    "code",
    "filename",
    "category",
)

# A tab that stays, with its item fingerprints.
_Kept = tuple[dict, frozenset[str]]


def _item_text(item: Mapping[str, Any]) -> str:
    """The item's first non-blank text field (``_ITEM_TEXT_KEYS`` order), else ""."""
    for key in _ITEM_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _item_fingerprint(item: Any) -> str | None:
    """A list item's identity: its first text field, case/space-folded."""
    if isinstance(item, str):
        text = item
    elif isinstance(item, dict):
        text = _item_text(item)
    else:
        return None
    folded = " ".join(text.split()).casefold()
    return folded or None


def _item_lists(component: str, props: Mapping[str, Any]) -> list[list]:
    """The lists holding a tab's items: its component's own, else every list prop."""
    keys = item_list_keys(component)
    values = [props.get(key) for key in keys] if keys else list(props.values())
    return [value for value in values if isinstance(value, list)]


def tab_fingerprints(tab: Mapping[str, Any]) -> frozenset[str]:
    """The identities of the items a tab ships."""
    props = tab.get("props")
    if not isinstance(props, dict):
        return frozenset()
    found: set[str] = set()
    for items in _item_lists(str(tab.get("component", "")), props):
        found.update(f for f in map(_item_fingerprint, items) if f is not None)
    return frozenset(found)


def _overlap(own: frozenset[str], theirs: frozenset[str]) -> float:
    """The share of ``own`` that ``theirs`` also ships (0 for a tab without items)."""
    return len(own & theirs) / len(own) if own else 0.0


def _twins(own: frozenset[str], kept: list[_Kept]) -> list[int]:
    """Indices of the kept tabs already shipping ≥ ``DUPLICATE_OVERLAP`` of ``own``."""
    return [i for i, (_, theirs) in enumerate(kept) if _overlap(own, theirs) >= DUPLICATE_OVERLAP]


def _meets_requirement_alone(
    tab: Mapping[str, Any], twins: list[_Kept], required: Collection[str]
) -> bool:
    """The later twin renders a required component and none of its earlier twins does."""
    return tab.get("component") in required and all(
        twin.get("component") not in required for twin, _ in twins
    )


def _record_duplicate(
    loser: _Kept, survivor: _Kept, dropped_sink: list[dict], sources: Mapping[str, str]
) -> None:
    """Log the drop and list it in ``droppedTabs`` with its planned dataSource."""
    tab, of_id = loser[0], str(survivor[0].get("id", ""))
    share = _overlap(loser[1], survivor[1])
    logger.warning(
        "TAB DROPPED: id=%r, component=%r — %.0f%% of its items ship on tab %r",
        tab.get("id"),
        tab.get("component"),
        share * 100,
        of_id,
    )
    dropped_sink.append(
        {
            "id": tab.get("id", ""),
            "component": tab.get("component", ""),
            "dataSource": sources.get(str(tab.get("id", "")), ""),
            "reason": DUPLICATE_TAB,
            "detail": f"{share:.0%} of its items ship on {of_id}",
        }
    )


def _dedupe_tabs(
    tabs: list[dict],
    dropped_sink: list[dict],
    required: Collection[str] = (),
    sources: Mapping[str, str] | None = None,
) -> None:
    """Drop every tab whose items ship ≥ ``DUPLICATE_OVERLAP`` on a tab that stays.

    Runs after promotion and the requirement backfill, on the assembled props.
    Of two twins the earlier stays — unless only the later renders a ``required``
    component (the domain's, equivalents included): then the later stays and each
    earlier twin whose own items ship on it at that share goes, so the dedupe
    never undoes the requirement the backfill just met. The overview is its own
    hero and never takes part. ``sources`` (tab id → planned dataSource) labels
    each drop record.
    """
    sources = sources or {}
    kept: list[_Kept] = []
    for tab in [t for t in tabs if t.get("component") != "overview"]:
        entry = (tab, tab_fingerprints(tab))
        twins = _twins(entry[1], kept)
        if twins and not _meets_requirement_alone(tab, [kept[i] for i in twins], required):
            tabs.remove(tab)
            _record_duplicate(entry, kept[twins[0]], dropped_sink, sources)
            continue
        losers = [i for i in twins if _overlap(kept[i][1], entry[1]) >= DUPLICATE_OVERLAP]
        for index in losers:
            tabs.remove(kept[index][0])
            _record_duplicate(kept[index], entry, dropped_sink, sources)
        kept = [k for i, k in enumerate(kept) if i not in losers] + [entry]
