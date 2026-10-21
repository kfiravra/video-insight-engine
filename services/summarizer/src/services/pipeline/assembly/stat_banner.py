"""The answer tab's numbers strip — stat_banner (C8, output-quality 0b.11, 1.8).

Only the figures the model stated become tiles (Kfir 2026-10-21): a key idea it
labelled ``kind: number`` with the ``figure`` ``{value, label}`` it gave and a
timestamp — ``{label, value, meaning, timestamp}``, the claim the meaning.
Nothing is mined from text: a key point's title, or a claim, holding a digit is
no tile (version numbers, fragments of a clock or a compound duration were). Below
_STAT_BANNER_MIN such ideas there is no strip. ``attachments`` decides which tab
carries it and re-exports these names.
"""

from __future__ import annotations

from typing import Any

from .derived_guard import anchor_seconds

# stat_banner (C8): the answer tab carries a numbers strip when the key ideas
# state at least this many figures with a timestamp; at most _STAT_BANNER_MAX
# tiles so it stays a strip, not a wall.
_STAT_BANNER_MIN = 5
_STAT_BANNER_MAX = 8


def _domain_blocks(extraction: dict | None, domain: str) -> list[dict]:
    """The extraction's domain blocks, primary first."""
    if not isinstance(extraction, dict):
        return []
    ordered = [extraction.get(domain), *(v for k, v in extraction.items() if k != domain)]
    return [d for d in ordered if isinstance(d, dict)]


def _key_ideas(extraction: dict | None, domain: str) -> list[dict]:
    """The first ``keyIdeas`` list in the extraction, primary domain first."""
    for domain_data in _domain_blocks(extraction, domain):
        ideas = domain_data.get("keyIdeas")
        if isinstance(ideas, list) and ideas:
            return [idea for idea in ideas if isinstance(idea, dict)]
    return []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _key_idea_tile(idea: dict) -> dict[str, Any] | None:
    """One key idea as a tile — only as the model stated it (Kfir 2026-10-21): a
    ``kind: number`` idea with its ``figure`` ``{value, label}`` and a timestamp;
    nothing is mined from the claim's text. ``meaning`` is the claim."""
    raw = idea.get("figure")
    figure: dict = raw if isinstance(raw, dict) else {}
    value, label = _text(figure.get("value")), _text(figure.get("label"))
    timestamp = anchor_seconds(idea)
    if idea.get("kind") != "number" or not value or not label or timestamp is None:
        return None
    return {
        "label": label,
        "value": value,
        "meaning": _text(idea.get("claim")),
        "timestamp": timestamp,
    }


def _stat_tiles(extraction: dict | None, domain: str) -> list[dict[str, Any]]:
    """The figures the model gave its number key ideas, in source order."""
    return [t for t in map(_key_idea_tile, _key_ideas(extraction, domain)) if t is not None]


def _stat_banner_attachment(extraction: dict | None, domain: str) -> dict | None:
    """Build the answer tab's numbers strip (C8) from the number key ideas:
    ``{label, value, meaning, timestamp}`` per tile, where ``label`` + ``value``
    are what StatBanner renders and ``meaning`` + ``timestamp`` ride along (the
    FE schema passes extra keys through). Built here, not via the registered
    assembler, because that one keeps only label/value/emoji and the timestamp
    is the point (R2). None below _STAT_BANNER_MIN figures."""
    from .core import _validate_assembled_props  # local import avoids cycle

    stats = _stat_tiles(extraction, domain)
    if len(stats) < _STAT_BANNER_MIN:
        return None
    props = {"stats": stats[:_STAT_BANNER_MAX]}
    if not _validate_assembled_props("stat_banner", props):
        return None
    return {"slot": "bottom", "component": "stat_banner", "props": props, "size": "strip"}
