"""Assemblers — primary family.

spot_explorer, moment_track, code_explorer, comparison, info_grid,
checklist, step_player.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

from typing import Any

from .normalizers import (
    _first_competitor_name,
    _normalize_code_snippet,
    _normalize_comparison,
    _normalize_moment_item,
    _normalize_step,
    _normalize_to_spot,
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
    highlight worth re-watching.
    """
    if not isinstance(data, list) or len(data) < 1:
        return None
    items = [
        m for m in (_normalize_moment_item(item, i) for i, item in enumerate(data)) if m is not None
    ]
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
    review with an explicit competitor — never from the video title.
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

    # When the LLM tagged winners on real-pair rows but didn't emit pros/cons
    # explicitly, derive them from the winner field. Keeps the "Go for it /
    # Skip it" recommendation section populated on review tabs that focus on
    # the table rather than separately listing pros and cons.
    if rows and not pros and not cons:
        for row in rows:
            feature = str(row.get("feature") or "").strip()
            if not feature:
                continue
            winner = str(row.get("winner") or "").strip().lower()
            if winner == "left":
                pros.append(feature)
            elif winner == "right":
                cons.append(feature)

    # Enforce the documented contract: a comparison tab needs either ≥2 real
    # pairs OR a populated pros/cons set. A lone row with no pros/cons is the
    # "info_grid in disguise" failure mode the refactor was supposed to close.
    if len(rows) < _MIN_COMPARISON_ROWS and not pros and not cons:
        return None

    competitor_name = _first_competitor_name(rows) if rows else ""

    props: dict[str, Any] = {
        "pros": pros,
        "cons": cons,
        "comparisons": rows,
        "leftLabel": product_name or "",
        "rightLabel": competitor_name,
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
    "type",
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
# not a reference pair — it was the wall-of-empty-grid bug (a 150-char sentence
# dumped into `key` with an empty `value`). Drop it so the orchestrator routes
# the data elsewhere (spot_explorer via promotion, or the fallback layer).
_INFO_STRING_MAX = 120


def _split_string_info_item(text: str) -> dict | None:
    """Turn a bare string into an info_grid pair, or drop it.

    A natural ` — ` / ` – ` / `: ` delimiter splits cleanly into key/value.
    A short standalone label survives as a key-only chip; a long delimiterless
    paragraph is not a reference pair and is dropped.
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
        return None
    return {"key": text, "value": ""}


def _normalize_info_grid_item(item: Any) -> dict | None:
    """Coerce diverse LLM-emit shapes into {key, value, evidence?, emoji?}.

    Returns None when the item lacks either key OR value — those would render
    as empty cards and are the root cause of the wall-of-empty-grid bug.
    """
    if isinstance(item, str):
        return _split_string_info_item(item)
    if not isinstance(item, dict):
        return None

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
        # the frontend renders them; only a paragraph-length key is dropped.
        if not value_clean and len(key_clean) > _INFO_STRING_MAX:
            return None
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
        return None

    value_raw = ""
    for field in _INFO_VALUE_FIELDS:
        candidate = item.get(field)
        if isinstance(candidate, str) and candidate.strip() and candidate.strip() != key_raw:
            value_raw = candidate.strip()
            break
    # No value field → key-only headline chip (frontend renders these);
    # paragraph-length keys are still dropped as not-a-reference-pair.
    if not value_raw and len(key_raw) > _INFO_STRING_MAX:
        return None

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
