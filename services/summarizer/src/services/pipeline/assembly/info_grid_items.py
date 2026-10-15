"""info_grid item normalizers (the InfoGridItem contract).

Split out of assemblers_primary.py (output-quality 0b follow-ups): bare
strings split into key/value pairs (0b.4), ``{type, text}`` tips headlined by
their text with ``type`` as a category badge (0b.8), and the key / value /
evidence field priorities. Whatever its shape, a row carries its item's emoji,
evidence line and seek timestamp (0b review). Every name here stays importable
from ``assemblers_primary`` through its explicit re-exports.
"""

from __future__ import annotations

import re
from typing import Any

from .derived_guard import anchor_seconds

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
# A keyless tip's card text: the schema's `text`, then `tip` (the usual drift),
# then any value field — a `{type, tip|description|definition|detail}` item
# degrades to a card headlined by its own prose instead of dropping (0b review).
_TIP_TEXT_FIELDS: tuple[str, ...] = tuple(dict.fromkeys(("text", "tip", *_INFO_VALUE_FIELDS)))

# A bare string longer than this with no key/value delimiter is a paragraph,
# not a reference pair. Dropping it lost content (S12 shipped 3 of 7 takeaways),
# so it now splits at its first sentence boundary into a headline + body card
# that the grid renders in full and the promotion can turn into a spot; a single
# long sentence stays a headline chip (output-quality 0b.4, R5/R10).
_INFO_STRING_MAX = 120
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")
# The headline is at least this long, so an abbreviation near the start
# ("No. 1 rule …", "Dr. Smith …") is not read as a sentence end. One past it
# ("…, e.g. LangGraph") still splits the text there.
_SENTENCE_LEAD_MIN = 20


def _drop_closing_period(headline: str) -> str:
    """One closing period goes; an ellipsis ("Wait for it...") stays whole."""
    if headline.endswith(".") and not headline.endswith(".."):
        return headline[:-1]
    return headline


def _split_long_sentence(text: str) -> dict:
    """Paragraph-length bare string: first sentence -> key, the rest -> value.

    Text with no `.`/`!`/`?` + whitespace boundary — unpunctuated, or CJK
    whose "。" has no space after it — stays one headline chip, uncut.
    """
    match = _SENTENCE_BOUNDARY.search(text, _SENTENCE_LEAD_MIN)
    if match is None:
        return {"key": text, "value": ""}
    return {"key": _drop_closing_period(text[: match.start()]), "value": text[match.end() :]}


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


def _first_text(item: dict, fields: tuple[str, ...], skip: str = "") -> tuple[str, str]:
    """The first of `fields` holding non-empty text other than `skip` -> (field, text)."""
    for field in fields:
        candidate = item.get(field)
        if isinstance(candidate, str) and candidate.strip() and candidate.strip() != skip:
            return field, candidate.strip()
    return "", ""


def _attach_extras(result: dict, item: dict, text_field: str = "") -> dict:
    """Carry the item's emoji, first evidence line and seek timestamp onto its row.

    Evidence never repeats the row's value nor the field the row was cut from
    (`text_field`). The timestamp is the item's anchor as `derived_guard`
    reads it: whole seconds, 0 included; a non-numeric stamp cannot seek.
    """
    if item.get("emoji"):
        result["emoji"] = item["emoji"]
    evidence_fields = tuple(f for f in _INFO_EVIDENCE_FIELDS if f != text_field)
    _, evidence = _first_text(item, evidence_fields, skip=result["value"])
    if evidence:
        result["evidence"] = evidence
    seconds = anchor_seconds(item)
    if seconds is not None:
        result["timestamp"] = seconds
    return result


def _credit_pair(item: dict) -> dict | None:
    """Credits-style `{role, name}`: the role labels the position (key), the name fills it.

    Both are key fields and would otherwise collide, so this shape is checked
    first — and only when the item carries no value field.
    """
    role = str(item.get("role") or "").strip()
    name = str(item.get("name") or "").strip()
    if role and name and not any(item.get(f) for f in _INFO_VALUE_FIELDS):
        return {"key": role, "value": name}
    return None


def _explicit_pair(item: dict) -> dict | None:
    """An explicit `{key, value}` row; with an empty value it is a headline chip.

    The frontend renders any key length in full, so no key-only row is dropped.
    """
    if "key" not in item or "value" not in item:
        return None
    key = str(item["key"]).strip()
    if not key:
        return None
    return {"key": key, "value": str(item.get("value") or "").strip()}


def _keyed_pair(item: dict) -> dict | None:
    """First key field -> key; first value field that is not the key -> value.

    No value field -> a key-only headline chip (frontend renders these in full).
    """
    _, key = _first_text(item, _INFO_KEY_FIELDS)
    if not key:
        return None
    _, value = _first_text(item, _INFO_VALUE_FIELDS, skip=key)
    return {"key": key, "value": value}


def _is_typed(item: dict) -> bool:
    category = item.get("type")
    return isinstance(category, str) and bool(category.strip())


def _text_only_info_item(item: dict) -> dict | None:
    """A keyless tip: its prose is the whole card.

    The card text is `text` — or, on a typed item, its first prose field
    (`_TIP_TEXT_FIELDS`). It splits like a bare string (delimiter, then
    sentence boundary), so the key is the tip's own headline and never its
    category token. A keyless fragment with neither `text` nor `type` (a
    lone `definition`) still drops: there is no tip to headline.
    """
    field, text = _first_text(item, _TIP_TEXT_FIELDS if _is_typed(item) else ("text",))
    result = _split_string_info_item(text) if text else None
    if result is None:
        return None
    return _attach_extras(result, item, text_field=field)


def _normalize_info_grid_dict(item: dict) -> dict | None:
    """The dict half of `_normalize_info_grid_item` (category attached by the caller).

    First shape that fits wins: credits `{role, name}`, an explicit
    `{key, value}` row, any key field with its value field, then a keyless tip.
    """
    pair = _credit_pair(item) or _explicit_pair(item) or _keyed_pair(item)
    if pair is None:
        return _text_only_info_item(item)
    return _attach_extras(pair, item)


def _normalize_info_grid_item(item: Any) -> dict | None:
    """Coerce diverse LLM-emit shapes into {key, value, evidence?, emoji?, timestamp?, category?}.

    Returns None when nothing can headline the card (no key field, no tip
    text) — such rows would render as empty cards, the root cause of the
    wall-of-empty-grid bug.
    """
    if isinstance(item, str):
        return _split_string_info_item(item)
    if not isinstance(item, dict):
        return None
    return _with_category(_normalize_info_grid_dict(item), item)
