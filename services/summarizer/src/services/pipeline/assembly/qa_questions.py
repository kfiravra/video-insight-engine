"""An interview's questions in the host's own words (output-quality 4.0 c, G3-4, D12).

A ``podcast.qa`` row is titled by its question, so the question is the host's words:
the schema asks for it copied from the transcript — one run of at most 25 words, or
two joined by "…". ``check_questions`` holds it to that on the copy assembly reads,
right after the quote pass, with the quote matcher's own rules (``verify_quote``,
"…" fragments in order and close together):

- found → the row's ``timestamp`` becomes the second the question is asked (the row
  seeks to the question, never to its answer's quote) and ``questionStatus: verified``;
- not found on an English source → the exchange leaves the list;
- not found on any other source → kept as ``questionStatus: translated``: the
  extraction runs in English, so a Hebrew interview's question is a translation
  (D12, until 4.2);
- fewer than ``MIN_QUESTIONS`` left → the list empties.

A Q&A tab with no exchange left drops before any fallback (``drop_emptied_qa``): no
outline section, chapter or other list ships under "Questions Asked".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.services.pipeline.quote_verify import TRANSLATED, VERIFIED, QuoteIndex, verify_quote
from src.utils.data_helpers import is_empty_data

from .derived_guard import anchor_seconds

logger = logging.getLogger(__name__)

QA_SOURCE = "podcast.qa"
# Written on every exchange the check keeps, on the copy assembly reads.
QUESTION_STATUS_KEY = "questionStatus"
# Fewer exchanges than this is no Q&A tab (moment_track's minimum, content-model §4).
MIN_QUESTIONS = 3
# The drop record's reason is a data reason, so a failed extraction group still
# claims the drop (``group_failures``); the detail names the rule.
_DROP_REASON = "validation_empty_list"
_DROP_DETAIL = "no question the transcript says — the outline never stands in"


@dataclass
class QaTally:
    """What the question check found: exchanges checked, kept by status, dropped."""

    checked: int = 0
    verified: int = 0
    translated: int = 0
    dropped: int = 0
    emptied: bool = False


def _qa_list(extraction: dict[str, Any] | None) -> list[Any] | None:
    podcast = extraction.get("podcast") if isinstance(extraction, dict) else None
    qa = podcast.get("qa") if isinstance(podcast, dict) else None
    return qa if isinstance(qa, list) else None


def _checked_exchange(item: Any, index: QuoteIndex, tally: QaTally) -> dict[str, Any] | None:
    """The exchange with its question checked; None when it leaves the list."""
    tally.checked += 1
    question = item.get("question") if isinstance(item, dict) else None
    if not isinstance(item, dict) or not isinstance(question, str) or not question.strip():
        tally.dropped += 1
        return None
    verdict = verify_quote(question, index, anchor_seconds(item))
    if verdict.status == VERIFIED:
        tally.verified += 1
        stamp = item.get("timestamp") if verdict.seconds is None else verdict.seconds
        return {**item, "timestamp": stamp, QUESTION_STATUS_KEY: VERIFIED}
    if verdict.status == TRANSLATED:
        tally.translated += 1
        return {**item, QUESTION_STATUS_KEY: TRANSLATED}
    tally.dropped += 1
    return None


def check_questions(
    extraction: dict[str, Any] | None, index: QuoteIndex
) -> tuple[dict[str, Any] | None, QaTally]:
    """``extraction`` with its ``podcast.qa`` questions checked (module docstring) — a
    copy holding the checked list; the same object when it has no exchange."""
    tally = QaTally()
    qa = _qa_list(extraction)
    if not qa or extraction is None:
        return extraction, tally
    kept = [ex for ex in (_checked_exchange(item, index, tally) for item in qa) if ex is not None]
    if len(kept) < MIN_QUESTIONS:
        tally.emptied, kept = True, []
    emptied = f" — fewer than {MIN_QUESTIONS} left, list emptied" if tally.emptied else ""
    logger.info(
        "qa questions: %d checked, %d verified, %d translated, %d dropped%s",
        tally.checked, tally.verified, tally.translated, tally.dropped, emptied,
    )  # fmt: skip
    return {**extraction, "podcast": {**extraction["podcast"], "qa": kept}}, tally


def drop_emptied_qa(tab: dict, component: str, data: Any, dropped_sink: list[dict]) -> bool:
    """Whether ``tab`` is a Q&A tab with no exchange to show — then recorded in
    ``dropped_sink``. It drops before any fallback, so nothing stands in for it."""
    if tab.get("dataSource") != QA_SOURCE or not is_empty_data(data):
        return False
    logger.warning(
        "TAB DROPPED: id=%r, component=%r, dataSource=%r — %s",
        tab.get("id"), component, QA_SOURCE, _DROP_DETAIL,
    )  # fmt: skip
    dropped_sink.append(
        {
            "id": tab.get("id", ""),
            "component": component,
            "dataSource": QA_SOURCE,
            "reason": _DROP_REASON,
            "detail": _DROP_DETAIL,
        }
    )
    return True
