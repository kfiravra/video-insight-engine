"""Quiz v2's key ideas (output-quality 2.1b, split out at 3.0): which extraction list a
domain's questions test, and how its items read in the prompt.

:data:`KEY_IDEA_SOURCES` names the registered dataSources a domain's key ideas live in
(the first non-empty one wins). Each item renders as one line — ``- [m:ss] (speaker)
statement — detail``; a stretch's call reads only the items stamped in it
(``quiz_v2_prompt``, G2-4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...shared_config.domain_config import data_source
from .memory import format_clock, truncate_words
from .pipeline_helpers import sanitize_for_prompt

KEY_IDEA_TEXT_MAX_CHARS = 300
NO_KEY_IDEAS = "Not available — test the claims the transcript argues."

# Where a domain's key ideas live: registered dataSources, the first non-empty one
# wins. learning.keyIdeas (D10) first everywhere — talks, lectures, podcasts and
# tech explainers all carry it; then each quiz domain's nearest equivalent.
KEY_IDEA_SOURCES: dict[str, tuple[str, ...]] = {
    "learning": ("learning.keyIdeas", "learning.keyPoints", "learning.takeaways"),
    "science": ("learning.keyIdeas", "science.keyFacts", "science.concepts"),
    "tech": ("learning.keyIdeas", "tech.topics", "tech.patterns"),
    "language": ("learning.keyIdeas", "language.rules", "language.phrases"),
    "podcast": ("learning.keyIdeas", "podcast.claims", "podcast.qa"),
    "news": ("learning.keyIdeas", "news.claims", "news.storyTimeline"),
}
_DEFAULT_SOURCES = ("learning.keyIdeas",)
# An item's line: its statement, the reason / detail behind it, and its stamp.
_MAIN_KEYS = ("claim", "title", "name", "label", "phrase", "question", "text", "statement")
_DETAIL_KEYS = ("why", "detail", "description", "definition", "explanation", "answer")
_STAMP_KEYS = ("timestamp", "seconds", "startSeconds")


# ─── Key ideas ───


def _resolve(extraction: Mapping[str, Any], source: str) -> object:
    """The extraction value at a registered dataSource (``domain`` + dotted ``field``)."""
    spec = data_source(source)
    if spec is None:
        return None
    node: object = extraction.get(spec["domain"])
    for key in spec["field"].split("."):
        node = node.get(key) if isinstance(node, Mapping) else None
    return node


def key_idea_items(primary_tag: str, extraction: Mapping[str, Any]) -> list[object]:
    """The first non-empty key-idea list of the domain's sources; ``[]`` when none."""
    for source in KEY_IDEA_SOURCES.get(primary_tag, _DEFAULT_SOURCES):
        items = _resolve(extraction, source)
        if isinstance(items, list) and items:
            return items
    return []


def _first_text(item: Mapping[str, Any], keys: Sequence[str], skip: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != skip:
            return value.strip()
    return ""


def item_seconds(item: object) -> int | None:
    if not isinstance(item, Mapping):
        return None
    for key in _STAMP_KEYS:
        value = item.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return None


def _clip(text: str) -> str:
    return truncate_words(sanitize_for_prompt(text, len(text)), KEY_IDEA_TEXT_MAX_CHARS)


def _item_parts(item: object) -> tuple[str, str, str]:
    """(statement, detail, speaker) of a key-idea item; a plain string is its statement."""
    if isinstance(item, str):
        return item.strip(), "", ""
    if not isinstance(item, Mapping):
        return "", "", ""
    main = _first_text(item, _MAIN_KEYS)
    return main, _first_text(item, _DETAIL_KEYS, skip=main), _first_text(item, ("speaker",))


def _item_line(item: object) -> str:
    """``- [m:ss] (speaker) statement — detail``; ``""`` for an item with no statement."""
    main, detail, speaker = _item_parts(item)
    if not main:
        return ""
    text = _clip(main) + (f" — {_clip(detail)}" if detail else "")
    if speaker:
        text = f"({_clip(speaker)}) {text}"
    seconds = item_seconds(item)
    return f"- [{format_clock(seconds)}] {text}" if seconds is not None else f"- {text}"


def render_key_ideas(items: Sequence[object]) -> str:
    lines = [line for line in (_item_line(item) for item in items) if line]
    return "\n".join(lines) or NO_KEY_IDEAS
