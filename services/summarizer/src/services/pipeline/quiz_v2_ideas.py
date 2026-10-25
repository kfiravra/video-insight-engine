"""Quiz v2's key ideas (output-quality 2.1b, split out at 3.0): which extraction list a
domain's questions test, which of its items a question may target, and how they read
in the prompt.

:data:`KEY_IDEA_SOURCES` names the registered dataSources a domain's key ideas live in
(the first non-empty one wins). Of that list only the items of a kind in
:data:`QUIZ_KINDS` are targets (4.0 b, Kfir 2026-10-22: every question tests a thesis,
an argument or a number — or an interview's disagreement, both sides argued; never an
incidental fact, a story's detail, an announcement); a list whose items carry no kind
(``news.claims``, ``tech.topics`` …) is all targets, a claim being no incidental fact.
Each target gets an id, ``K1`` … in list order, once per quiz, so every call names the
same idea the same way; it renders as one line — ``- K3 [m:ss] argument: (speaker)
statement — detail`` — and a part's call reads only the targets stamped in it
(``quiz_v2_prompt``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
# The key-idea kinds a question may test (schemas/learning.txt, podcast.txt); story, ask
# and announcement are left out. An interview's disagreement (4.1) was filed as an
# argument before it had a kind of its own (review p4 A-W2).
QUIZ_KINDS = ("thesis", "argument", "number", "disagreement")
TARGET_ID_PREFIX = "K"
# An item's line: its statement, the reason / detail behind it, and its stamp.
_MAIN_KEYS = ("claim", "title", "name", "label", "phrase", "question", "text", "statement")
_DETAIL_KEYS = ("why", "detail", "description", "definition", "explanation", "answer")
_STAMP_KEYS = ("timestamp", "seconds", "startSeconds")


@dataclass(frozen=True)
class QuizTarget:
    """One key idea a question may test: its id, its extraction item, its stamp and its
    quote (``""`` without one)."""

    id: str
    item: object
    seconds: int | None
    quote: str


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


def item_kind(item: object) -> str:
    """The item's ``kind``, casefolded; ``""`` when it has none."""
    kind = item.get("kind") if isinstance(item, Mapping) else None
    return kind.strip().casefold() if isinstance(kind, str) else ""


def _item_parts(item: object) -> tuple[str, str, str]:
    """(statement, detail, speaker) of a key-idea item; a plain string is its statement."""
    if isinstance(item, str):
        return item.strip(), "", ""
    if not isinstance(item, Mapping):
        return "", "", ""
    main = _first_text(item, _MAIN_KEYS)
    return main, _first_text(item, _DETAIL_KEYS, skip=main), _first_text(item, ("speaker",))


# ─── Targets ───


def _eligible(items: Sequence[object]) -> list[object]:
    """The items a question may test: those of a quiz kind; every one when no item
    carries a kind — or when none of the kinds is a quiz kind (a list of stories alone
    still gets a targeted quiz)."""
    stated = [item for item in items if _item_parts(item)[0]]
    picked = [item for item in stated if item_kind(item) in QUIZ_KINDS]
    return picked or stated


def _quote(item: object) -> str:
    quote = item.get("quote") if isinstance(item, Mapping) else None
    return quote.strip() if isinstance(quote, str) else ""


def quiz_targets(primary_tag: str, extraction: Mapping[str, Any]) -> list[QuizTarget]:
    """The domain's key ideas a question may test, ``K1`` … in list order (module
    docstring); ``[]`` when the run has none."""
    return [
        QuizTarget(f"{TARGET_ID_PREFIX}{n}", item, item_seconds(item), _quote(item))
        for n, item in enumerate(_eligible(key_idea_items(primary_tag, extraction)), start=1)
    ]


def _clip(text: str) -> str:
    return truncate_words(sanitize_for_prompt(text, len(text)), KEY_IDEA_TEXT_MAX_CHARS)


def _target_line(target: QuizTarget) -> str:
    """``- K3 [m:ss] argument: (speaker) statement — detail``."""
    main, detail, speaker = _item_parts(target.item)
    text = _clip(main) + (f" — {_clip(detail)}" if detail else "")
    if speaker:
        text = f"({_clip(speaker)}) {text}"
    if kind := item_kind(target.item):
        text = f"{_clip(kind)}: {text}"
    clock = f" [{format_clock(target.seconds)}]" if target.seconds is not None else ""
    return f"- {target.id}{clock} {text}"


def render_targets(targets: Sequence[QuizTarget]) -> str:
    return "\n".join(_target_line(target) for target in targets) or NO_KEY_IDEAS
