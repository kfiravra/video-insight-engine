"""Quotes as spot cards (output-quality 1.3, L11, C11).

``podcast.quotes`` ``{quote, speaker, timestamp}`` and ``narrative.quotes``
``{text, speaker, timestamp, context}`` rendered only as flash cards; a
spot_explorer tab on either now ships one card per quote: the speaker as the
card's name, the line verbatim as its ``quote`` (with its English, ``quoteEn``, on
a non-English run — D12), the context (narrative) as its description and the seek
point. An unattributed quote is named by its own head, never by an invented
speaker.
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.quote_verify import mark_translated_quote

from .moment_normalizers import QUOTE_EN_KEY
from .normalizers import _normalize_to_spot
from .text_utils import truncate_words

# A card name cut from an unattributed quote: the spot title's length.
_QUOTE_NAME_MAX_CHARS = 60


def _quote_text(item: dict) -> str:
    text = item.get("quote") or item.get("text")
    return text.strip() if isinstance(text, str) else ""


def is_quote_list(data: Any) -> bool:
    """A validated quote list: every item carries a line (``quote`` / ``text``) and a
    ``speaker`` key, and no card name of its own — nor a ``question`` / ``answer``
    (an interview exchange, ``podcast.qa``, whose Q and A a quote card would drop)."""
    items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return bool(items) and all(
        ("quote" in item or "text" in item)
        and "speaker" in item
        and "question" not in item
        and "answer" not in item
        and not (item.get("name") or item.get("title"))
        for item in items
    )


def _quote_spot(item: dict) -> dict | None:
    """One quote as a spot card; None without a line."""
    line = _quote_text(item)
    if not line:
        return None
    speaker = item.get("speaker")
    name = speaker.strip() if isinstance(speaker, str) and speaker.strip() else ""
    name = name or truncate_words(line, _QUOTE_NAME_MAX_CHARS)
    spot = _normalize_to_spot(
        {
            "name": name,
            "description": item.get("context") or "",
            "quote": line if line != name else None,
            QUOTE_EN_KEY: item.get(QUOTE_EN_KEY),
            "timestamp": item.get("timestamp"),
        }
    )
    return mark_translated_quote(spot, item) if spot is not None else None


def quote_spots(data: list) -> list[dict]:
    """The quote cards a quote list ships, in source order."""
    spots = (_quote_spot(item) for item in data if isinstance(item, dict))
    return [spot for spot in spots if spot is not None]
