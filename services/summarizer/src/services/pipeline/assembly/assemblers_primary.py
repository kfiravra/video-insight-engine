"""Assemblers — primary family.

spot_explorer, moment_track, code_explorer, comparison, info_grid,
checklist, step_player. The info_grid item normalizers live in
``info_grid_items`` and are re-exported here.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.quote_verify import mark_translated_quote

from .derived_guard import anchor_seconds
from .info_grid_items import _INFO_EVIDENCE_FIELDS as _INFO_EVIDENCE_FIELDS
from .info_grid_items import _INFO_KEY_FIELDS as _INFO_KEY_FIELDS
from .info_grid_items import _INFO_STRING_MAX as _INFO_STRING_MAX
from .info_grid_items import _INFO_VALUE_FIELDS as _INFO_VALUE_FIELDS
from .info_grid_items import _category_badge as _category_badge
from .info_grid_items import _normalize_info_grid_dict as _normalize_info_grid_dict
from .info_grid_items import _normalize_info_grid_item as _normalize_info_grid_item
from .info_grid_items import _split_long_sentence as _split_long_sentence
from .info_grid_items import _split_string_info_item as _split_string_info_item
from .info_grid_items import _text_only_info_item as _text_only_info_item
from .info_grid_items import _with_category as _with_category
from .ingredients import ingredient_row
from .key_ideas import is_key_idea_list, key_idea_props, podcast_guests
from .moment_normalizers import quote_fields
from .normalizers import (
    _normalize_code_snippet,
    _normalize_comparison,
    _normalize_step,
    _normalize_verdict,
    normalize_moment_items,
)
from .quote_spots import is_quote_list, quote_spots
from .tip_spots import spot_card

# ─────────────────────────────────────────────────────
# Assembler Functions
# ─────────────────────────────────────────────────────


# SpotExplorer is a search/filter/browse UI — a single spot renders as a
# degenerate explorer (no filters useful, no comparisons). When a tab can only
# produce one valid spot, dropping it lets `build_fallback_candidates` in
# core.py surface the content via overview/info_grid instead, which is the
# better UX for sparse inputs.
_MIN_SPOTS = 2


def _spot_card(item: dict) -> dict | None:
    """One item as a spot card (a tip or pick read as its grid row, ``tip_spots``, R6),
    its translated-quote flag carried (2.0 d, D12)."""
    spot = spot_card(item)
    return mark_translated_quote(spot, item) if spot is not None else None


def assemble_spot_explorer(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Flatten TravelDay[] to SpotItem[] with section groupings.

    Every spot passes through `spot_card` (`_normalize_to_spot`, else the item's
    info_grid row — a tip or a pick, R6), which drops entries lacking
    a non-empty name or any descriptive/passthrough content. Bare-string input
    is rejected — extraction should produce structured `{name, description}`
    dicts; string arrays are almost always a symptom of keyword-list pollution
    via cross-domain fallback. Requires `_MIN_SPOTS` valid spots to ship; below
    that, the tab is dropped and the fallback layer (overview/info_grid) takes
    over so the content still surfaces. A ``learning.keyIdeas`` list ships as
    key-idea cards (`key_idea_props`: claim, why, quote, timestamp, sections, badge),
    a quote list as quote cards (`quote_spots`: speaker, the line, its seek point).
    """
    if not isinstance(data, list) or len(data) == 0:
        return None

    if is_key_idea_list(data):
        props = key_idea_props(data, podcast_guests(extraction))
        return props if len(props["spots"]) >= _MIN_SPOTS else None
    if is_quote_list(data):
        spots = quote_spots(data)
        return {"spots": spots} if len(spots) >= _MIN_SPOTS else None

    first = data[0] if isinstance(data[0], dict) else {}
    if ("day" in first or "city" in first) and "spots" in first:
        all_spots: list = []
        sections: list = []
        for day in data:
            if not isinstance(day, dict):
                continue
            label = f"Day {day.get('day', '?')}"
            if day.get("city"):
                label += f": {day['city']}"
            day_spots_raw = day.get("spots", [])
            if not isinstance(day_spots_raw, list):
                continue
            day_spots_clean = [
                spot
                for spot in (_spot_card(s) for s in day_spots_raw if isinstance(s, dict))
                if spot is not None
            ]
            if not day_spots_clean:
                continue
            start = len(all_spots)
            sections.append(
                {
                    "label": label,
                    "spotIndices": list(range(start, start + len(day_spots_clean))),
                }
            )
            all_spots.extend(day_spots_clean)
        if len(all_spots) < _MIN_SPOTS:
            return None
        return {"spots": all_spots, "sections": sections}

    normalized = [
        spot
        for spot in (_spot_card(item) for item in data if isinstance(item, dict))
        if spot is not None
    ]
    if len(normalized) < _MIN_SPOTS:
        return None
    return {"spots": normalized}


def assemble_moment_track(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Unified MomentTrack assembler for points and spans.

    Replaces legacy assemble_timeline (points) and assemble_clip_player (spans).
    Items keep endSeconds only when the LLM marked the moment as a replayable
    highlight worth re-watching. Textless items and a second item at the same
    second are refused by `normalize_moment_items` (0b.1).
    """
    if not isinstance(data, list) or len(data) < 1:
        return None
    items = normalize_moment_items(data)
    return {"items": items} if items else None


def assemble_code_explorer(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    snippets = [s for s in (_normalize_code_snippet(item) for item in data) if s is not None]
    if not snippets:
        return None
    return {"snippets": snippets}


def _has_pair_side(value: Any) -> bool:
    """A comparison side is "present" when it carries any non-empty content.

    Numeric zero is legitimate content (a free-tier product priced at $0, a
    "0 GB" storage row), so test for ``None`` and empty-after-strip explicitly
    rather than relying on Python truthiness. The prior ``or ""`` coercion
    dropped rows whose numeric side was 0/False — exactly the kind of
    quantitative comparison this component is supposed to surface.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, bool):
        # Booleans are valid (e.g. "supports HDR: True vs False"). True/False
        # both count as present.
        return True
    if isinstance(value, (int, float)):
        # Any numeric value is present — including 0 and 0.0.
        return True
    # Lists / dicts: present if non-empty.
    if isinstance(value, (list, dict)):
        return len(value) > 0
    # Unknown shape — coerce to string and check.
    return str(value).strip() != ""


def _is_real_comparison_pair(row: dict) -> bool:
    """A row counts only if BOTH sides carry content. Single-side rows are
    just info_grid in disguise and degrade the comparison UX — they render
    as half-empty rows next to the real pairs."""
    return _has_pair_side(row.get("thisProduct")) and _has_pair_side(row.get("competitor"))


# Comparison tabs need at least two real pairs to justify the side-by-side
# layout. A single pair renders as an info_grid in disguise and was the
# audited "comparison with one row" failure mode — fall through to None so
# the caller can route the data into info_grid / flash_deck instead.
_MIN_COMPARISON_ROWS = 2
# The review block a comparison tab reads its pros / cons from when planned on any
# of its paths (``review.comparisons``, ``review.pros`` …) — a side read
# (``field_requests._REVIEW_COMPARISON``).
_REVIEW_DOMAIN = "review"
# A review plan with a rows tab and a Pros & cons tab ships each half once (4.3): the
# rows on the rows tab, the pros and cons on the other — whatever the tab order, so the
# dedupe never drops one as the other's twin. Assembly hands every planned tab the
# plan's comparison tabs, ``(id, dataSource)`` in plan order, under this key
# (``planned_comparison_sources``).
PLANNED_COMPARISONS_KEY = "_plannedComparisons"
_ROWS_SOURCE = "review.comparisons"
_PROS_CONS_SOURCES = frozenset({_REVIEW_DOMAIN, "review.pros", "review.cons"})
_COMPARISON_COMPONENTS = frozenset({"comparison", "comparison_radar"})

# One planned comparison tab: (tab id, dataSource).
_Planned = tuple[tuple[str, str], ...]


def planned_comparison_sources(tabs: Any) -> _Planned:
    """The plan's comparison tabs as ``(id, dataSource)``, in plan order."""
    planned = tabs if isinstance(tabs, list) else []
    return tuple(
        (str(t.get("id") or ""), str(t.get("dataSource") or ""))
        for t in planned
        if isinstance(t, dict) and t.get("component") in _COMPARISON_COMPONENTS
    )


def _planned(tab: dict) -> _Planned:
    return tab.get(PLANNED_COMPARISONS_KEY) or ()


def _halves_elsewhere(tab: dict) -> tuple[bool, bool]:
    """(the rows ride another planned tab, the pros / cons ride another planned tab)."""
    sources = {source for _, source in _planned(tab)}
    source = str(tab.get("dataSource") or "")
    rows_elsewhere = source in _PROS_CONS_SOURCES and _ROWS_SOURCE in sources
    return rows_elsewhere, source == _ROWS_SOURCE and bool(sources & _PROS_CONS_SOURCES)


def _competitor_names(rows: list[dict]) -> list[str]:
    """Distinct non-empty `competitorName`s in row order; "Sony" and "sony" are one."""
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("competitorName") or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def _comparison_rows(data: Any) -> list[dict]:
    """The real pairs a comparison ships: a review dict's ``comparisons`` or a row
    list's feature rows, each with a feature and both sides."""
    if isinstance(data, dict):
        raw = data.get("comparisons") or []
        rows = [_normalize_comparison(c) for c in raw if isinstance(c, dict)]
    elif isinstance(data, list):
        rows = [
            _normalize_comparison(item)
            for item in data
            if isinstance(item, dict)
            and "feature" in item
            and ("thisProduct" in item or "competitor" in item)
        ]
    else:
        rows = []
    return [r for r in rows if str(r.get("feature") or "").strip() and _is_real_comparison_pair(r)]


def _stated_lines(value: Any) -> list[str]:
    """The non-blank strings of a pros / cons list, in order."""
    if not isinstance(value, list):
        return []
    return [line for line in value if isinstance(line, str) and line.strip()]


def _pros_cons(tab: dict, data: Any, review_data: Any) -> tuple[list[str], list[str]]:
    """The reviewer's own pros and cons — never derived from a row's `winner` (0b.9).

    A tab planned on the review block itself reads its lists; a tab planned on any
    other review path (``review.comparisons``, ``review.pros``) reads the review
    block's (4.3: S07 planned ``review.comparisons`` and shipped none of its 14 pros
    and 7 cons; a tab on ``review.pros`` shipped nothing at all).
    """
    source = data if isinstance(data, dict) else None
    if source is None and str(tab.get("dataSource") or "").split(".")[0] == _REVIEW_DOMAIN:
        source = review_data if isinstance(review_data, dict) else None
    if source is None:
        return [], []
    return _stated_lines(source.get("pros")), _stated_lines(source.get("cons"))


def _verdict_tab(planned: _Planned, states_pros_or_cons: bool) -> str:
    """The id of the one planned tab that carries a review's verdict: the Pros & cons tab
    when one is planned and the review states pros or cons (it ships them), else the
    first comparison tab — a rows tab before a Pros & cons tab that ships nothing."""
    pros_cons = [tab_id for tab_id, source in planned if source in _PROS_CONS_SOURCES]
    if pros_cons and states_pros_or_cons:
        return pros_cons[0]
    rows = [tab_id for tab_id, source in planned if source not in _PROS_CONS_SOURCES]
    return (rows or [tab_id for tab_id, _ in planned])[0]


def _carries_verdict(tab: dict, review_data: dict) -> bool:
    """Whether this tab carries the review's verdict (C-C1): one tab only, so the hero
    that lifts it is its only place. A tab assembled outside a plan keeps it."""
    planned = _planned(tab)
    if not planned:
        return True
    states = bool(_stated_lines(review_data.get("pros")) or _stated_lines(review_data.get("cons")))
    return str(tab.get("id") or "") == _verdict_tab(planned, states)


def assemble_comparison(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build comparison props with strict pair requirements.

    Requires at least 2 rows where BOTH sides have content, or the reviewer's own
    pros / cons — otherwise the output is the audited "video title as column header
    / empty third column" failure mode, and the data flows into info_grid or
    flash_deck via the fallback layer instead.

    The leftLabel is only set from the review's product — never from the video
    title. Each row keeps its `competitorName`, which heads that competitor's own
    table (4.3, Q5); `rightLabel` names the competitor only when the rows name one.
    Pros/cons come only from the review's own `pros` / `cons` (``_pros_cons``); a plan
    with both a rows tab and a Pros & cons tab ships each half once (``_halves_elsewhere``)
    and the review's verdict once (``_carries_verdict``).
    """
    review_data = extraction.get(_REVIEW_DOMAIN) if isinstance(extraction, dict) else None
    product_name: str | None = None
    if isinstance(review_data, dict):
        candidate = review_data.get("product")
        if isinstance(candidate, str) and candidate.strip():
            product_name = candidate.strip()

    rows_elsewhere, pros_cons_elsewhere = _halves_elsewhere(tab)
    rows = [] if rows_elsewhere else _comparison_rows(data)
    pros, cons = ([], []) if pros_cons_elsewhere else _pros_cons(tab, data, review_data)
    # Enforce the documented contract: a comparison tab needs either ≥2 real
    # pairs OR a populated pros/cons set. A lone row with no pros/cons is the
    # "info_grid in disguise" failure mode the refactor was supposed to close.
    if len(rows) < _MIN_COMPARISON_ROWS and not pros and not cons:
        return None

    names = _competitor_names(rows)
    props: dict[str, Any] = {
        "pros": pros,
        "cons": cons,
        "comparisons": rows,
        "leftLabel": product_name or "",
        "rightLabel": names[0] if len(names) == 1 else "",
    }

    # Verdict header data is folded into ComparisonInteractive — when the
    # review extraction includes a verdict block, attach it to one planned tab (the
    # web lifts it into the hero on a review, 4.3). Standalone VerdictInteractive was
    # retired in the video-to-action overhaul; the comparison renderer absorbs its role.
    if isinstance(review_data, dict) and _carries_verdict(tab, review_data):
        verdict = review_data.get("verdict")
        if isinstance(verdict, dict) and verdict.get("bottomLine"):
            props["verdict"] = _normalize_verdict(verdict, review_data.get("rating"))

    return props


def assemble_info_grid(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Normalize diverse list/dict shapes into InfoGridItem[] (`info_grid_items`).

    Every row needs a key — the card's headline. A key-only row ships as a
    headline chip, a long bare string splits at its first sentence, and a
    keyless tip is headlined by its own text with its `type` as a category
    badge; each row keeps its emoji, evidence and timestamp. Only a row with
    nothing to headline drops; when every row does, returns None so the
    orchestrator can route the data through a fallback layer instead of
    shipping an empty grid (the audited "8 empty cards" failure mode).
    """
    if isinstance(data, list) and len(data) >= 1:
        pairs = [p for p in (_normalize_info_grid_item(item) for item in data) if p is not None]
        return {"items": pairs} if pairs else None
    if isinstance(data, dict) and data:
        pairs = [
            {"key": str(k), "value": str(v)}
            for k, v in data.items()
            if isinstance(v, (str, int, float, bool)) and str(v).strip()
        ]
        return {"items": pairs} if pairs else None
    return None


def _checklist_dict_row(item: dict) -> dict | None:
    """One dict item as a checklist row, by the field that carries its text.

    A dict with no text field (e.g. an itinerary day a fallback routed here) has no
    checklist label; str() of it leaked a Python repr into the UI, so it is dropped.
    """
    if "name" in item and ("amount" in item or "unit" in item or "displayAmount" in item):
        return ingredient_row(item)
    if "item" in item:
        return {
            "label": str(item["item"]),
            "note": item.get("category"),
            "emoji": "⚠️" if item.get("essential") else None,
        }
    if "text" in item:
        return {"label": str(item["text"]), "note": item.get("notes")}
    if "title" in item:
        return {"label": str(item["title"]), "note": item.get("detail") or item.get("notes")}
    if "label" in item:
        return {"label": str(item["label"]), "note": item.get("note") or item.get("notes")}
    if "name" in item:
        return {"label": str(item["name"]), "note": item.get("notes")}
    return None


def _checklist_row(item: Any) -> dict | None:
    """One checklist row; a dict item's row keeps the item's seek point (4.1 — a talk's
    asks rows, ``talk_asks.ask_rows``, lost theirs here) and its kept quote with the
    quote's flag and English (``quote_fields``: an ask's ``quoteEn`` stays, and the
    translation walk knows its “note” for the speaker's own line — D12)."""
    if not isinstance(item, dict):
        return {"label": str(item)}
    row = _checklist_dict_row(item)
    if row is None:
        return None
    timestamp = anchor_seconds(item)
    stamp = {} if timestamp is None or "timestamp" in row else {"timestamp": timestamp}
    return {**row, **stamp, **quote_fields(item)}


def assemble_checklist(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    items = [row for row in map(_checklist_row, data) if row is not None]
    return {"items": items} if items else None


def assemble_step_player(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    # Bare-string steps are legitimate (e.g. tech.setup.commands is list[str])
    # — coerce instead of silently filtering them into an empty tab.
    steps = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            steps.append(_normalize_step(item, i))
        elif isinstance(item, str) and item.strip():
            steps.append(_normalize_step({"instruction": item.strip()}, i))
    if not steps:
        return None
    return {"steps": steps}
