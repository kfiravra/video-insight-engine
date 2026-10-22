"""A talk's asks — the requests it puts to the audience, in its words (output-quality 4.0 d).

The talk playbook's "What they asked" tab is a checklist on ``learning.takeaways``:
strings with no quote and no stamp, where the speaker's own practices passed for asks
(G3-4: S01 shipped six of DHH's and lost his one real ask to merge_rank). When the run
has key ideas, that tab is a view of them instead — the numbers strip's precedent
(``stat_banner``): ``ask_rows`` returns the ideas of kind ``ask`` whose quote survived
the quote pass, ``{label: claim, note: “quote”, timestamp}``; fewer than the registry's
checklist minimum is an empty list, so the tab drops and nothing refills it. A run
without key ideas (an explainer keeps keyPoints, D10) keeps its takeaways checklist.

An ask is the words that ask: one whose quote did not survive is badged as an
argument on the key-ideas tab (``shown_kind``). Each ask shows once (G3-2): when the
asks tab ships, the key-ideas tab sheds its ask cards (``dedupe``, ``is_asks_view``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.services.pipeline.quote_verify import QUOTE_TRANSLATED_KEY
from src.shared_config.domain_config import density_gates

from .derived_guard import anchor_seconds

ASKS_SOURCE = "learning.takeaways"
ASKS_COMPONENT = "checklist"
KEY_IDEAS_SOURCE = "learning.keyIdeas"
ASK_KIND = "ask"
_ARGUMENT_KIND = "argument"
_LEADING_DIGITS = re.compile(r"\d+")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_ask(idea: Mapping[str, Any]) -> bool:
    return _text(idea.get("kind")).lower() == ASK_KIND


def shown_kind(idea: Mapping[str, Any]) -> Any:
    """The kind a key idea's badge shows: an ask with no quote left is an argument."""
    if _is_ask(idea) and not _text(idea.get("quote")):
        return _ARGUMENT_KIND
    return idea.get("kind")


def _checklist_minimum() -> int:
    """The leading number of checklist's ``densityGates`` minimum ("3" → 3)."""
    found = _LEADING_DIGITS.match(str(density_gates().get(ASKS_COMPONENT, {}).get("min", "")))
    return int(found.group()) if found else 1


def _key_ideas(extraction: dict[str, Any] | None) -> list[dict[str, Any]]:
    learning = extraction.get("learning") if isinstance(extraction, dict) else None
    ideas = learning.get("keyIdeas") if isinstance(learning, dict) else None
    return [idea for idea in ideas if isinstance(idea, dict)] if isinstance(ideas, list) else []


def _ask_row(idea: dict[str, Any]) -> dict[str, Any] | None:
    """One quoted ask as a checklist row; a translated quote is prose, unquoted (D12)."""
    claim, quote = _text(idea.get("claim")), _text(idea.get("quote"))
    if not _is_ask(idea) or not claim or not quote:
        return None
    note = quote if idea.get(QUOTE_TRANSLATED_KEY) is True else f"“{quote}”"
    row: dict[str, Any] = {"label": claim, "note": note}
    timestamp = anchor_seconds(idea)
    if timestamp is not None:
        row["timestamp"] = timestamp
    return row


def ask_rows(extraction: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The quoted asks of the run's key ideas, in source order ([] below the checklist
    minimum); None when the run has no key ideas."""
    ideas = _key_ideas(extraction)
    if not ideas:
        return None
    rows = [row for row in map(_ask_row, ideas) if row is not None]
    return rows if len(rows) >= _checklist_minimum() else []


def is_asks_view(component: Any, source: Any) -> bool:
    """Whether a tab shipping ``component`` from ``source`` is the asks checklist."""
    return component == ASKS_COMPONENT and source == ASKS_SOURCE


def asks_data(
    component: str, data_source: str, extraction: dict[str, Any] | None, data: Any
) -> Any:
    """What a tab assembles from: a takeaways checklist on a run with key ideas gets its
    ask rows (an empty list drops the tab); every other tab keeps ``data``."""
    if not is_asks_view(component, data_source):
        return data
    rows = ask_rows(extraction)
    return data if rows is None else rows
