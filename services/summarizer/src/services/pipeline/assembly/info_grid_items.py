"""info_grid item normalizers (the InfoGridItem contract).

Split out of assemblers_primary.py (output-quality 0b follow-ups): bare
strings split into key/value pairs (0b.4), ``{type, text}`` tips headlined by
their text with ``type`` as a category badge (0b.8), and the key / value /
evidence field priorities. Every name here stays importable from
``assemblers_primary`` through its explicit re-exports.
"""

from __future__ import annotations

import re
from typing import Any

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
