"""Post-promotion dedupe (output-quality 0b.5 — R5, L26; phase-0 review W-A2).

Reconcile dedupes planned tabs by (dataSource, component) before extraction;
promotion (flash_deck → concept_canvas) and the sibling fallback can still make
two tabs ship the same items. The assembled props decide: a tab whose items
ship at least ``DUPLICATE_OVERLAP`` on a tab that stays is its twin. An item
one tab ships is never lost: a twin goes only when every item it ships also
ships elsewhere; a twin with items of its own sheds the shared ones and stays
(``duplicate_items``) — a procedure (``SEQUENTIAL_COMPONENTS``) ships whole
instead, and so does a twin a trim would leave below its component's own
minimum (``_LIST_MINIMUMS``). A tab's items are its component's own list
(``component_lists.item_list_keys`` — a canvas's group labels never dilute the
overlap); every list prop only for a component the table does not know. The
asks tab outranks Key Ideas, whose ask cards it ships (``_settle_asks_view``).
``core`` re-exports the public names.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from .assemblers_overhaul import _CONNECT_MIN_PAIRS
from .assemblers_primary import _MIN_COMPARISON_ROWS, _MIN_SPOTS
from .assemblers_secondary import _CLAIMS_MIN, _FORMATION_MIN, _TIER_LIST_MIN
from .component_lists import COMPONENT_LIST_KEYS, SEQUENTIAL_COMPONENTS, item_list_keys
from .promotion import RADAR_AXIS_THRESHOLD
from .talk_asks import KEY_IDEAS_SOURCE, is_asks_view

logger = logging.getLogger(__name__)

DUPLICATE_OVERLAP = 0.5
DUPLICATE_TAB = "duplicate_tab"
# A twin that sheds its shared items: listed in ``droppedTabs`` for the audit
# trail, but the tab ships — no drop (``core.count_dropped_tabs``).
DUPLICATE_ITEMS = "duplicate_items"
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


# ─── Trimming a twin down to its own items ───


def _list_keys(component: str, props: Mapping[str, Any]) -> list[str]:
    """The props keys of the lists ``_item_lists`` reads."""
    keys = item_list_keys(component) or tuple(props)
    return [key for key in keys if isinstance(props.get(key), list)]


def _item_count(tab: Mapping[str, Any]) -> int:
    props = tab.get("props")
    if not isinstance(props, dict):
        return 0
    return sum(len(items) for items in _item_lists(str(tab.get("component", "")), props))


def _remap_sections(props: dict, kept: list[int]) -> None:
    """Point ``sections[].spotIndices`` at the trimmed spots; a section left empty goes."""
    sections = props.get("sections")
    if not isinstance(sections, list):
        return
    new_index = {old: new for new, old in enumerate(kept)}
    remapped: list[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        indices = section.get("spotIndices") or []
        mapped = [new_index[i] for i in indices if isinstance(i, int) and i in new_index]
        if mapped:
            remapped.append({**section, "spotIndices": mapped})
    props["sections"] = remapped


def _linked_to(concept: dict, names: set[str]) -> dict:
    """The concept without its connections to a concept no longer on the canvas."""
    links = concept.get("connections")
    if not isinstance(links, list):
        return concept
    kept = [
        link
        for link in links
        if isinstance(link, dict) and str(link.get("to", "")).strip().lower() in names
    ]
    return {**concept, "connections": kept}


def _prune_concept_graph(props: dict) -> None:
    """A trimmed canvas keeps no edge to a trimmed concept and no empty group."""
    concepts = [c for c in props.get("concepts") or [] if isinstance(c, dict)]
    names = {str(c.get("name", "")).strip().lower() for c in concepts}
    props["concepts"] = [_linked_to(c, names) for c in concepts]
    if isinstance(props.get("groups"), list):
        props["groups"] = list(dict.fromkeys(c["group"] for c in concepts if c.get("group")))


# The fewest items a component's validated list (``COMPONENT_LIST_KEYS``) ships —
# the assemblers' own thresholds, so a trim never leaves what the assembler would
# have refused (phase-0 close-out review W1). Unlisted: non-empty.
_LIST_MINIMUMS: dict[str, int] = {
    "comparison": _MIN_COMPARISON_ROWS,
    "comparison_radar": RADAR_AXIS_THRESHOLD,
    "spot_explorer": _MIN_SPOTS,
    "connect_canvas": _CONNECT_MIN_PAIRS,
    "claims_tracker": _CLAIMS_MIN,
    "tier_list": _TIER_LIST_MIN,
    "formation_diagram": _FORMATION_MIN,
}


def _trim_breaks_minimum(tab: Mapping[str, Any], elsewhere: frozenset[str]) -> bool:
    """A trim would leave the list the tab was validated on (``COMPONENT_LIST_KEYS``)
    below its component's minimum (``_LIST_MINIMUMS``, else empty) — a comparison
    whose own items are pros, not rows, or one left with a single row."""
    component = str(tab.get("component", ""))
    key = COMPONENT_LIST_KEYS.get(component)
    items = tab["props"].get(key) if key else None
    if not isinstance(items, list):
        return False
    left = sum(1 for item in items if _item_fingerprint(item) not in elsewhere)
    return left < _LIST_MINIMUMS.get(component, 1)


def _trim(tab: dict, elsewhere: frozenset[str]) -> int:
    """Remove from the tab's own lists every item ``elsewhere`` ships; the count removed."""
    props = tab["props"]
    component = str(tab.get("component", ""))
    removed = 0
    for key in _list_keys(component, props):
        items = props[key]
        kept = [i for i, item in enumerate(items) if _item_fingerprint(item) not in elsewhere]
        removed += len(items) - len(kept)
        if key == "spots":
            _remap_sections(props, kept)
        props[key] = [items[i] for i in kept]
    if component == "concept_canvas":
        _prune_concept_graph(props)
    return removed


# ─── One pass ───


@dataclass
class _Pass:
    """One dedupe pass: the tab list it edits, its records and the tabs that stay."""

    tabs: list[dict]
    dropped_sink: list[dict]
    sources: Mapping[str, str]
    kept: list[_Kept] = field(default_factory=list)

    def shipped_besides(self, tab: Mapping[str, Any]) -> frozenset[str]:
        """Every item a staying tab other than ``tab`` ships."""
        return frozenset().union(*(own for other, own in self.kept if other is not tab))

    def remove(self, tab: Mapping[str, Any]) -> None:
        """Remove ``tab`` itself — by identity, never an equal tab before it."""
        index = next((i for i, other in enumerate(self.tabs) if other is tab), None)
        if index is not None:
            del self.tabs[index]

    def record(self, tab: Mapping[str, Any], reason: str, detail: str) -> None:
        """List the decision in ``droppedTabs`` with the tab's planned dataSource."""
        self.dropped_sink.append(
            {
                "id": tab.get("id", ""),
                "component": tab.get("component", ""),
                "dataSource": self.sources.get(str(tab.get("id", "")), ""),
                "reason": reason,
                "detail": detail,
            }
        )


def _record_duplicate(run: _Pass, loser: _Kept, survivor: _Kept) -> None:
    """Log the drop and list it in ``droppedTabs``."""
    tab, of_id = loser[0], str(survivor[0].get("id", ""))
    share = _overlap(loser[1], survivor[1])
    logger.warning(
        "TAB DROPPED: id=%r, component=%r — %.0f%% of its items ship on tab %r",
        tab.get("id"),
        tab.get("component"),
        share * 100,
        of_id,
    )
    run.record(tab, DUPLICATE_TAB, f"{share:.0%} of its items ship on {of_id}")


def _record_trim(
    run: _Pass, tab: Mapping[str, Any], survivor: _Kept, removed: int, total: int
) -> None:
    """Log the trim and list it in ``droppedTabs`` (``DUPLICATE_ITEMS`` — the tab ships)."""
    of_id = str(survivor[0].get("id", ""))
    logger.info(
        "TAB TRIMMED: id=%r, component=%r — %d of its %d items ship elsewhere (twin of %r)",
        tab.get("id"),
        tab.get("component"),
        removed,
        total,
        of_id,
    )
    run.record(tab, DUPLICATE_ITEMS, f"{removed} of its {total} items ship elsewhere ({of_id})")


def _settle(run: _Pass, twin: _Kept, survivor: _Kept, elsewhere: frozenset[str]) -> _Kept | None:
    """``twin`` against what ``elsewhere`` ships: dropped (None) when it has no item of its
    own; else trimmed to its own items. The entry that stays.

    A procedure ships whole, and so does a twin a trim would leave without the list
    it was validated on, or below that list's minimum — its own items would not ship.
    """
    tab, own = twin
    unique = own - elsewhere
    if not unique:
        run.remove(tab)
        _record_duplicate(run, twin, survivor)
        return None
    if tab.get("component") in SEQUENTIAL_COMPONENTS or _trim_breaks_minimum(tab, elsewhere):
        return twin
    total = _item_count(tab)
    _record_trim(run, tab, survivor, _trim(tab, elsewhere), total)
    return tab, unique


def _settle_earlier_twins(run: _Pass, entry: _Kept, twins: list[int]) -> None:
    """Each earlier twin whose own items ship ≥ ``DUPLICATE_OVERLAP`` on ``entry`` (the
    required tab that stays) keeps only what ships nowhere else."""
    losers = [
        run.kept[i][0] for i in twins if _overlap(run.kept[i][1], entry[1]) >= DUPLICATE_OVERLAP
    ]
    for loser in losers:
        index = next(i for i, (tab, _) in enumerate(run.kept) if tab is loser)
        elsewhere = run.shipped_besides(loser) | entry[1]
        settled = _settle(run, run.kept[index], entry, elsewhere)
        if settled is None:
            del run.kept[index]
        else:
            run.kept[index] = settled


def _source(run: _Pass, tab: Mapping[str, Any]) -> str | None:
    return run.sources.get(str(tab.get("id", "")))


def _settle_asks_view(run: _Pass) -> None:
    """Each ask shows once (4.0 d, G3-2): the key-ideas tab sheds the cards the asks tab
    (``talk_asks``) ships — unless shedding would leave it under its minimum: then the
    cards stay and the asks tab goes. Before the twin rule, whatever the tab order."""
    view = next((t for t in run.tabs if is_asks_view(t.get("component"), _source(run, t))), None)
    asks = tab_fingerprints(view) if view is not None else frozenset()
    holders = [
        t for t in run.tabs if _source(run, t) == KEY_IDEAS_SOURCE and tab_fingerprints(t) & asks
    ]
    if view is None or not holders:
        return
    if any(_trim_breaks_minimum(holder, asks) for holder in holders):
        run.remove(view)
        _record_duplicate(run, (view, asks), (holders[0], tab_fingerprints(holders[0])))
        return
    for holder in holders:
        total = _item_count(holder)
        _record_trim(run, holder, (view, asks), _trim(holder, asks), total)


def _dedupe_tabs(
    tabs: list[dict],
    dropped_sink: list[dict],
    required: Collection[str] = (),
    sources: Mapping[str, str] | None = None,
) -> None:
    """Settle every tab whose items ship ≥ ``DUPLICATE_OVERLAP`` on a tab that stays.

    Runs after promotion and the requirement backfill, on the assembled props.
    Of two twins the earlier stays whole and the later sheds what ships elsewhere
    (``_settle``: dropped when nothing of its own is left) — unless only the later
    renders a ``required`` component (the domain's, equivalents included): then
    the later stays whole and each earlier twin whose own items ship on it at that
    share is settled instead, so the dedupe never undoes the requirement the
    backfill just met. The overview is its own hero and never takes part.
    ``sources`` (tab id → planned dataSource) labels each record.
    """
    run = _Pass(tabs, dropped_sink, sources or {})
    _settle_asks_view(run)
    for tab in [t for t in tabs if t.get("component") != "overview"]:
        entry = (tab, tab_fingerprints(tab))
        twins = _twins(entry[1], run.kept)
        if twins and not _meets_requirement_alone(tab, [run.kept[i] for i in twins], required):
            settled = _settle(run, entry, run.kept[twins[0]], run.shipped_besides(tab))
            run.kept.extend([settled] if settled is not None else [])
            continue
        _settle_earlier_twins(run, entry, twins)
        run.kept.append(entry)
