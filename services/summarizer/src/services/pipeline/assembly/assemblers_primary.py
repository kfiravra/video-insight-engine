"""Assemblers — primary family.

spot_explorer, moment_track, code_explorer, comparison, info_grid,
checklist, step_player.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

import re
from typing import Any

from .normalizers import (
    _normalize_code_snippet,
    _normalize_comparison,
    _normalize_step,
    _normalize_to_spot,
    normalize_moment_items,
)

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
    over so the content still surfaces.
    """
    if not isinstance(data, list) or len(data) == 0:
        return None

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
# A competitor is "named" in its cell when one of its words (≥ this long —
# skips "the", "vs", "II") appears there; otherwise the cell is prefixed.
_COMPETITOR_WORD_MIN = 3


def _competitor_names(rows: list[dict]) -> list[str]:
    """Distinct non-empty `competitorName`s in row order."""
    names: list[str] = []
    for row in rows:
        name = str(row.get("competitorName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _cell_names_competitor(cell: str, name: str) -> bool:
    words = [w for w in re.findall(r"\w+", name) if len(w) >= _COMPETITOR_WORD_MIN]
    return not words or any(w.lower() in cell.lower() for w in words)


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
            verdict_props: dict[str, Any] = {
                "badge": verdict.get("badge", "neutral"),
                "bottomLine": verdict.get("bottomLine", ""),
                "bestFor": verdict.get("bestFor") or [],
                "notFor": verdict.get("notFor") or [],
            }
            sub_scores = verdict.get("subScores")
            if isinstance(sub_scores, list) and sub_scores:
                verdict_props["subScores"] = sub_scores
            rating = (
                review_data.get("rating") if isinstance(review_data.get("rating"), dict) else None
            )
            if isinstance(rating, dict):
                score = rating.get("score")
                if isinstance(score, (int, float)):
                    verdict_props["score"] = score
                    max_score = rating.get("maxScore")
                    if isinstance(max_score, (int, float)) and max_score > 0:
                        verdict_props["maxScore"] = max_score
            props["verdict"] = verdict_props

    return props


_INFO_KEY_FIELDS: tuple[str, ...] = (
    "key",
    "name",
    "title",
    "label",
    "term",
    "word",
    "phrase",
    "aspect",
    "role",
    "fact",
    "concept",
    "topic",
    "item",
    "place",
    "spot",
    "feature",
    "claim",
    "step",
    "question",
)
_INFO_VALUE_FIELDS: tuple[str, ...] = (
    "value",
    "definition",
    "description",
    "detail",
    "explanation",
    "text",
    "translation",
    "answer",
    "meaning",
    "summary",
    "reason",
    "why",
    "note",
    "tip",
    "instruction",
)
# `type` is a category token (`chef_tip`, `safety`, `form`), never the key —
# mapped into the key it named every promoted spot "chef_tip" (0b.8, R5). It
# ships as `category`, a badge the renderer may show next to the key.
_CATEGORY_TOKEN_MAX_CHARS = 32
_CATEGORY_TOKEN_MAX_WORDS = 3
_INFO_EVIDENCE_FIELDS: tuple[str, ...] = (
    "example",
    "source",
    "context",
    "pronunciation",
    "note",
    "analogy",
    "quote",
)

# A bare string longer than this with no key/value delimiter is a paragraph,
# not a reference pair. Dropping it lost content (S12 shipped 3 of 7 takeaways),
# so it now splits at its first sentence boundary into a headline + body card
# that the grid renders in full and the promotion can turn into a spot; a single
# long sentence stays a headline chip (output-quality 0b.4, R5/R10).
_INFO_STRING_MAX = 120
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")
# The headline must be at least this long — guards against "No. 1 …" / "e.g. …"
# being read as a sentence end.
_SENTENCE_LEAD_MIN = 20


def _split_long_sentence(text: str) -> dict:
    """Paragraph-length bare string: first sentence -> key, the rest -> value."""
    match = _SENTENCE_BOUNDARY.search(text, _SENTENCE_LEAD_MIN)
    if match is None:
        return {"key": text, "value": ""}
    return {"key": text[: match.start()].rstrip("."), "value": text[match.end() :]}


def _split_string_info_item(text: str) -> dict | None:
    """Turn a bare string into an info_grid pair.

    A natural ` — ` / ` – ` / `: ` delimiter splits cleanly into key/value.
    A short standalone label survives as a key-only chip; a long delimiterless
    paragraph splits at its first sentence boundary instead of being dropped.
    """
    text = text.strip()
    if not text:
        return None
    for sep in (" — ", " – ", ": "):
        if sep in text:
            key, _, value = text.partition(sep)
            key, value = key.strip(), value.strip()
            if key and value:
                return {"key": key, "value": value}
            break
    if len(text) > _INFO_STRING_MAX:
        return _split_long_sentence(text)
    return {"key": text, "value": ""}


def _category_badge(raw: Any) -> str | None:
    """A short `type` token ("chef_tip") as badge text ("Chef tip"); None otherwise."""
    if not isinstance(raw, str):
        return None
    text = raw.replace("_", " ").replace("-", " ").strip()
    if not text or len(text) > _CATEGORY_TOKEN_MAX_CHARS:
        return None
    if len(text.split()) > _CATEGORY_TOKEN_MAX_WORDS or any(c in text for c in ".!?:"):
        return None
    return text[0].upper() + text[1:]


def _with_category(result: dict | None, item: dict) -> dict | None:
    """Attach the item's `type` as a `category` badge to a normalized row."""
    category = _category_badge(item.get("type")) if result is not None else None
    if category:
        result["category"] = category
    return result


def _normalize_info_grid_item(item: Any) -> dict | None:
    """Coerce diverse LLM-emit shapes into {key, value, evidence?, emoji?, category?}.

    Returns None when the item lacks a key — those would render as empty cards
    and were the root cause of the wall-of-empty-grid bug.
    """
    if isinstance(item, str):
        return _split_string_info_item(item)
    if not isinstance(item, dict):
        return None
    return _with_category(_normalize_info_grid_dict(item), item)


def _normalize_info_grid_dict(item: dict) -> dict | None:
    """The dict half of `_normalize_info_grid_item` (category attached by the caller)."""

    # Credits-style: {role, name} — role labels the position (key), name
    # identifies who fills it (value). Handle explicitly because both fields
    # fall in the priority key-field list and would otherwise collide.
    role = str(item.get("role") or "").strip()
    name = str(item.get("name") or "").strip()
    if role and name and not any(item.get(f) for f in _INFO_VALUE_FIELDS):
        result: dict[str, Any] = {"key": role, "value": name}
        if item.get("emoji"):
            result["emoji"] = item["emoji"]
        return result

    if "key" in item and "value" in item:
        key_clean = str(item["key"]).strip()
        value_clean = str(item.get("value") or "").strip()
        if not key_clean:
            return None
        # Key-only items survive as headline chips (terms-only glossaries) —
        # the frontend renders any key length in full, so none is dropped.
        result: dict[str, Any] = {"key": key_clean, "value": value_clean}
        if item.get("emoji"):
            result["emoji"] = item["emoji"]
        for ev_field in _INFO_EVIDENCE_FIELDS:
            ev = item.get(ev_field)
            if isinstance(ev, str) and ev.strip():
                result["evidence"] = ev.strip()
                break
        return result

    key_raw = ""
    for field in _INFO_KEY_FIELDS:
        candidate = item.get(field)
        if isinstance(candidate, str) and candidate.strip():
            key_raw = candidate.strip()
            break
    if not key_raw:
        return _text_only_info_item(item)

    value_raw = ""
    for field in _INFO_VALUE_FIELDS:
        candidate = item.get(field)
        if isinstance(candidate, str) and candidate.strip() and candidate.strip() != key_raw:
            value_raw = candidate.strip()
            break
    # No value field → key-only headline chip (frontend renders these in full).
    result = {"key": key_raw, "value": value_raw}
    if item.get("emoji"):
        result["emoji"] = item["emoji"]

    for ev_field in _INFO_EVIDENCE_FIELDS:
        ev = item.get(ev_field)
        if isinstance(ev, str) and ev.strip() and ev.strip() != value_raw:
            result["evidence"] = ev.strip()
            break

    if item.get("timestamp") is not None:
        result["timestamp"] = item["timestamp"]

    return result


def _text_only_info_item(item: dict) -> dict | None:
    """A keyless `{type, text}` tip: the `text` is the whole card.

    It splits into key/value exactly like a bare string (delimiter, then
    sentence boundary), so the key is the tip's own headline and never its
    category token. Other keyless fragments (a lone `definition`) still drop.
    """
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    result = _split_string_info_item(text)
    if result is not None and item.get("emoji"):
        result["emoji"] = item["emoji"]
    return result


def assemble_info_grid(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Normalize diverse list/dict shapes into InfoGridItem[].

    Drops items that can't produce both a key and a value — they'd render as
    empty cards. If every item is dropped, returns None so the orchestrator
    can route the data through a fallback layer instead of shipping an empty
    grid (the audited "8 empty cards" failure mode).
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
                da = str(item.get("displayAmount", "")).strip()
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
