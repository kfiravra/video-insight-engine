"""Post-promotion dedupe (output-quality 0b.5 — R5, L26).

The tab fingerprints, the overlap rule and the drop record, moved out of
``core`` (which re-exports them).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# Reconcile dedupes planned tabs by (dataSource, component) before extraction;
# promotion (flash_deck → concept_canvas) and the sibling fallback can still
# make two tabs ship the same items. The assembled props decide: a tab whose
# items overlap an earlier tab's by at least this share is a duplicate.
DUPLICATE_OVERLAP = 0.5
DUPLICATE_TAB = "duplicate_tab"
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
)


def _item_fingerprint(item: Any) -> str | None:
    """A list item's identity: its first text field, case/space-folded."""
    if isinstance(item, str):
        text = item
    elif isinstance(item, dict):
        text = next(
            (v for k in _ITEM_TEXT_KEYS if isinstance((v := item.get(k)), str) and v.strip()), ""
        )
    else:
        return None
    folded = " ".join(text.split()).casefold()
    return folded or None


def tab_fingerprints(tab: Mapping[str, Any]) -> frozenset[str]:
    """The identities of every item in every list prop of a tab."""
    props = tab.get("props")
    if not isinstance(props, dict):
        return frozenset()
    found: set[str] = set()
    for value in props.values():
        if not isinstance(value, list):
            continue
        found.update(f for f in map(_item_fingerprint, value) if f is not None)
    return frozenset(found)


def _duplicate_of(
    tab: Mapping[str, Any], earlier: list[tuple[str, frozenset[str]]]
) -> tuple[str, float] | None:
    """``(earlier tab id, overlap share)`` when ``tab`` repeats an earlier tab's items."""
    own = tab_fingerprints(tab)
    if not own:
        return None
    for tab_id, theirs in earlier:
        share = len(own & theirs) / len(own)
        if share >= DUPLICATE_OVERLAP:
            return tab_id, share
    return None


def _dedupe_tabs(tabs: list[dict], dropped_sink: list[dict]) -> None:
    """Drop every tab whose items overlap an earlier tab's by ≥ ``DUPLICATE_OVERLAP``.

    Runs after promotion and the requirement backfill, on the assembled props
    (so the S03 flash_deck promoted onto the same concepts as the canvas, or a
    moment tab built from the exercises the workout room shows, go). The
    overview is its own hero and never takes part.
    """
    earlier: list[tuple[str, frozenset[str]]] = []
    for tab in list(tabs):
        if tab.get("component") == "overview":
            continue
        duplicate = _duplicate_of(tab, earlier)
        if duplicate is None:
            earlier.append((str(tab.get("id", "")), tab_fingerprints(tab)))
            continue
        of_id, share = duplicate
        tabs.remove(tab)
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
                "dataSource": "",
                "reason": DUPLICATE_TAB,
                "detail": f"{share:.0%} of its items ship on {of_id}",
            }
        )
