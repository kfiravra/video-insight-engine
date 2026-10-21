"""Assemblers — primary family.

spot_explorer, moment_track, code_explorer, comparison, info_grid,
checklist, step_player. The info_grid item normalizers live in
``info_grid_items`` and are re-exported here.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

import re
from typing import Any

from .assemblers_learning import is_key_idea_list, key_idea_props
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
from .normalizers import (
    _normalize_code_snippet,
    _normalize_comparison,
    _normalize_step,
    _normalize_to_spot,
    _normalize_verdict,
    normalize_moment_items,
)
from .quote_spots import is_quote_list, quote_spots

# ─────────────────────────────────────────────────────
# Assembler Functions
# ─────────────────────────────────────────────────────


# SpotExplorer is a search/filter/browse UI — a single spot renders as a
# degenerate explorer (no filters useful, no comparisons). When a tab can only
# produce one valid spot, dropping it lets `build_fallback_candidates` in
# core.py surface the content via overview/info_grid instead, which is the
# better UX for sparse inputs.
_MIN_SPOTS = 2


def assemble_spot_explorer(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Flatten TravelDay[] to SpotItem[] with section groupings.

    Every spot passes through `_normalize_to_spot`, which drops entries lacking
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
        props = key_idea_props(data)
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
                for spot in (_normalize_to_spot(s) for s in day_spots_raw if isinstance(s, dict))
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
        for spot in (_normalize_to_spot(item) for item in data if isinstance(item, dict))
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
# A competitor is "named" in its cell when one of its identifying words (≥ this
# long — skips "vs", "II" — and not a filler word) appears there as a whole
# word, any case; a name with no such word ("LG", "Mi 11", "Mini Pro") must
# appear whole. Otherwise the cell is prefixed.
_COMPETITOR_WORD_MIN = 3
# Words that ride along in product names without saying whose a cell is: "the"
# of "The Galaxy S24" and "Pro" of "iPhone 15 Pro" match cells about any product.
_COMPETITOR_FILLER = frozenset({"the", "and", "pro", "max", "plus", "mini", "ultra", "lite", "new"})


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


def _competitor_patterns(name: str) -> list[str]:
    """What identifies the competitor in a cell: its long non-filler words, else
    its whole name."""
    words = re.findall(r"\w+", name)
    identifying = [
        re.escape(w)
        for w in words
        if len(w) >= _COMPETITOR_WORD_MIN and w.casefold() not in _COMPETITOR_FILLER
    ]
    if identifying or not words:
        return identifying
    return [r"\W+".join(re.escape(w) for w in words)]


def _cell_names_competitor(cell: str, name: str) -> bool:
    """Whether the cell already says whose it is (whole words, any case).

    A name with no word characters has nothing to look for, so it never
    prefixes a cell.
    """
    patterns = _competitor_patterns(name)
    if not patterns:
        return True
    return any(re.search(rf"\b{p}\b", cell, re.IGNORECASE) for p in patterns)


def _name_competitor_cells(rows: list[dict], names: list[str]) -> list[dict]:
    """With several competitors, each competitor cell says whose it is.

    The two-column renderer heads the right column with every competitor, so
    a cell that does not mention its own competitor is prefixed with the
    extraction's `competitorName` (never a label the assembler made up).
    """
    if len(names) < 2:
        return rows
    named: list[dict] = []
    for row in rows:
        name = str(row.get("competitorName") or "").strip()
        cell = row.get("competitor")
        if name and isinstance(cell, str) and not _cell_names_competitor(cell, name):
            row = {**row, "competitor": f"{name} — {cell}"}
        named.append(row)
    return named


def assemble_comparison(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build comparison props with strict pair requirements.

    Requires at least 2 rows where BOTH sides have content — otherwise the
    output is the audited "video title as column header / empty third column"
    failure mode. When the LLM produced single-sided items (concepts with no
    competitor), this routes through to None so the data can flow into
    info_grid or flash_deck via the fallback layer instead.

    The leftLabel/rightLabel are only set when the data is actually a product
    review with an explicit competitor — never from the video title. The right
    column keeps one name per competitor, and pros/cons come only from the
    review's own `pros`/`cons` — never derived from `winner` (0b.9, R10).
    """
    review_data = extraction.get("review") if isinstance(extraction, dict) else None
    product_name: str | None = None
    if isinstance(review_data, dict):
        candidate = review_data.get("product")
        if isinstance(candidate, str) and candidate.strip():
            product_name = candidate.strip()

    pros: list[str] = []
    cons: list[str] = []
    rows: list[dict] = []

    if isinstance(data, dict):
        pros = list(data.get("pros") or [])
        cons = list(data.get("cons") or [])
        raw_rows = data.get("comparisons") or []
        rows = [_normalize_comparison(c) for c in raw_rows if isinstance(c, dict)]
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if "feature" in item and ("thisProduct" in item or "competitor" in item):
                rows.append(_normalize_comparison(item))

    rows = [r for r in rows if str(r.get("feature") or "").strip() and _is_real_comparison_pair(r)]

    # Enforce the documented contract: a comparison tab needs either ≥2 real
    # pairs OR a populated pros/cons set. A lone row with no pros/cons is the
    # "info_grid in disguise" failure mode the refactor was supposed to close.
    if len(rows) < _MIN_COMPARISON_ROWS and not pros and not cons:
        return None

    names = _competitor_names(rows)

    props: dict[str, Any] = {
        "pros": pros,
        "cons": cons,
        "comparisons": _name_competitor_cells(rows, names),
        "leftLabel": product_name or "",
        "rightLabel": " · ".join(names),
    }

    # Verdict header data is folded into ComparisonInteractive — when the
    # review extraction includes a verdict block, attach it so ReviewSummary
    # renders above the table. Standalone VerdictInteractive was retired in
    # the video-to-action overhaul; the comparison renderer absorbs its role.
    if isinstance(review_data, dict):
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


# A display amount that only says the amount is unknown ("amount not stated")
# is not an amount: the ingredient ships by its name alone (R10).
_UNSTATED_AMOUNT = re.compile(
    r"^(?:amount\s+)?(?:not\s+(?:stated|given|specified|mentioned|shown)"
    r"|unspecified|unknown|n/?a)\.?$",
    re.IGNORECASE,
)


def _display_amount(item: dict) -> str:
    """The item's displayAmount, or '' when it only says the amount is unknown."""
    shown = str(item.get("displayAmount", "")).strip()
    return "" if _UNSTATED_AMOUNT.match(shown) else shown


def assemble_checklist(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None

    items: list[dict] = []
    for item in data:
        if isinstance(item, str):
            items.append({"label": item})
        elif isinstance(item, dict):
            if "name" in item and ("amount" in item or "unit" in item or "displayAmount" in item):
                da = _display_amount(item)
                if da:
                    items.append({"label": f"{da} {item['name']}", "note": item.get("notes")})
                else:
                    parts: list[str] = []
                    if item.get("amount") and item["amount"] != 0:
                        parts.append(str(item["amount"]))
                    if item.get("unit"):
                        parts.append(str(item["unit"]))
                    parts.append(str(item["name"]))
                    items.append({"label": " ".join(parts), "note": item.get("notes")})
            elif "item" in item:
                items.append(
                    {
                        "label": str(item["item"]),
                        "note": item.get("category"),
                        "emoji": "⚠️" if item.get("essential") else None,
                    }
                )
            elif "text" in item:
                items.append({"label": str(item["text"]), "note": item.get("notes")})
            elif "title" in item:
                items.append(
                    {"label": str(item["title"]), "note": item.get("detail") or item.get("notes")}
                )
            elif "label" in item:
                items.append(
                    {"label": str(item["label"]), "note": item.get("note") or item.get("notes")}
                )
            elif "name" in item:
                items.append({"label": str(item["name"]), "note": item.get("notes")})
            # A dict with no text field (e.g. an itinerary day a fallback routed here) has no
            # checklist label; str() of it leaked a Python repr into the UI, so it is dropped.
        else:
            items.append({"label": str(item)})

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
