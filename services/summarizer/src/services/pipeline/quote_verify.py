"""Quote verification (output-quality 2.0 d, R10, D12): is a quote in the transcript read?

A ``quote`` field promises the speaker's own words. ``verify_quote`` looks a quote up
in a ``QuoteIndex`` built once per transcript (``run_quote_index`` caches it on the
pipeline context); how it matches — normalized, exact first, else fuzzy, a "…"-cut
quote fragment by fragment in order and close together — is ``quote_index``'s
docstring.

Status: ``verified`` (found) · ``unverified`` (an English source, not found: the
quote is cleared) · ``translated`` (any other source, not found: the extraction runs
in English, so a Hebrew talk's quotes are translations — kept as prose per D12 until
4.2, never cleared; a quote in the source language still passes).

``verify_extraction_quotes`` applies it to every quote-bearing field of an extraction
and to the quiz items of an enrichment — on a copy: only the containers on the way
to a checked item are copied, so the persisted extraction is never mutated. Verdicts
are memoized on the index, so the provisional re-assemblies of a run pay once.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# The matcher's names stay importable from here (split out, output-quality p2 review).
from .quote_index import TRANSLATED as TRANSLATED
from .quote_index import UNVERIFIED as UNVERIFIED
from .quote_index import VERIFIED as VERIFIED
from .quote_index import QuoteIndex as QuoteIndex
from .quote_index import QuoteStatus as QuoteStatus
from .quote_index import QuoteVerdict as QuoteVerdict
from .quote_index import _is_number
from .quote_index import normalize_words as normalize_words

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

# Written on every checked item of the copy assembly reads.
QUOTE_STATUS_KEY = "quoteStatus"
# A translated quote's flag, carried onto the card that shows it (rendered as prose).
# A boolean: the English→source translation walk translates every string leaf, so a
# "translated" status string would arrive in the source-language tabs translated too.
QUOTE_TRANSLATED_KEY = "quoteTranslated"

_ANCHOR_KEYS = ("timestamp", "seconds", "startSeconds")
# A quote list's item IS its quote: an unverified one leaves the list.
_QUOTE_LIST_KEY = "quotes"


def verify_quote(quote: str, index: QuoteIndex, near_seconds: int | None = None) -> QuoteVerdict:
    """Whether ``quote`` is in the indexed transcript — ``{status, seconds, score}``."""
    return index.verify(quote, near_seconds)


# ─── The extraction pre-pass ─────────────────────────────────────────────────


@dataclass
class QuoteTally:
    """Quotes checked by status, and the ones that failed; the unverified share is
    over English-source quotes."""

    english_source: bool = True
    checked: int = 0
    verified: int = 0
    unverified: int = 0
    translated: int = 0
    failed: list[tuple[str, QuoteVerdict]] = field(default_factory=list)

    def add(self, quote: str, verdict: QuoteVerdict) -> None:
        self.checked += 1
        setattr(self, verdict.status, getattr(self, verdict.status) + 1)
        if verdict.status != VERIFIED:
            self.failed.append((quote, verdict))

    @property
    def unverified_share(self) -> float | None:
        if not self.english_source or not self.checked:
            return None
        return self.unverified / self.checked


@dataclass(frozen=True)
class QuotePass:
    """The checked copies assembly reads, and what the check found."""

    extraction: dict[str, Any] | None
    enrichment: dict[str, Any] | None
    tally: QuoteTally


def _near_seconds(item: Mapping[str, Any]) -> int | None:
    for key in _ANCHOR_KEYS:
        value = item.get(key)
        if _is_number(value) and math.isfinite(value):
            return int(value)
    return None


def _is_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _line_key(item: Mapping[str, Any], parent_key: str) -> str | None:
    """The key holding the item's quote: ``quote``, or a quote list item's ``text``."""
    if _is_line(item.get("quote")):
        return "quote"
    if parent_key == _QUOTE_LIST_KEY and "quote" not in item and _is_line(item.get("text")):
        return "text"
    return None


def _checked_item(item: dict, line_key: str, index: QuoteIndex, tally: QuoteTally) -> dict:
    verdict = index.verify(item[line_key], _near_seconds(item))
    tally.add(item[line_key], verdict)
    checked = {**item, QUOTE_STATUS_KEY: verdict.status}
    if verdict.status == UNVERIFIED:
        checked[line_key] = None
    elif verdict.status == TRANSLATED:
        checked[QUOTE_TRANSLATED_KEY] = True
    return checked


def _walk(value: Any, index: QuoteIndex, tally: QuoteTally, parent_key: str) -> Any:
    """``value`` with every quote checked — the same object when nothing in it changed."""
    if isinstance(value, list):
        return _walk_list(value, index, tally, parent_key)
    if not isinstance(value, dict):
        return value
    changed = {}
    for key, child in value.items():
        checked = _walk(child, index, tally, key)
        if checked is not child:
            changed[key] = checked
    item = {**value, **changed} if changed else value
    line_key = _line_key(item, parent_key)
    return item if line_key is None else _checked_item(item, line_key, index, tally)


def _is_cleared(item: Any) -> bool:
    return isinstance(item, dict) and item.get(QUOTE_STATUS_KEY) == UNVERIFIED


def _walk_list(value: list, index: QuoteIndex, tally: QuoteTally, parent_key: str) -> list:
    items = []
    for item in value:
        checked = _walk(item, index, tally, parent_key)
        if parent_key == _QUOTE_LIST_KEY and _is_cleared(checked):
            continue
        items.append(checked)
    same = len(items) == len(value) and all(new is old for new, old in zip(items, value))
    return value if same else items


def verify_extraction_quotes(
    extraction: dict[str, Any] | None, enrichment: dict[str, Any] | None, index: QuoteIndex
) -> QuotePass:
    """Every quote of ``extraction`` (any ``quote`` field, a quote list's ``text``) and of
    the enrichment's quiz, checked on a copy (module docstring)."""
    tally = QuoteTally(english_source=index.english_source)
    checked = _walk(extraction, index, tally, "") if extraction is not None else None
    quiz = enrichment.get("quiz") if isinstance(enrichment, dict) else None
    if isinstance(quiz, list) and enrichment is not None:
        checked_quiz = _walk(quiz, index, tally, "quiz")
        if checked_quiz is not quiz:
            enrichment = {**enrichment, "quiz": checked_quiz}
    logger.debug(
        "quotes: %d checked, %d verified, %d unverified, %d translated",
        tally.checked, tally.verified, tally.unverified, tally.translated,
    )  # fmt: skip
    return QuotePass(checked, enrichment, tally)


def mark_translated_quote(card: dict, item: Mapping[str, Any]) -> dict:
    """Carry the item's translated-quote flag onto a card that shows its quote."""
    if card.get("quote") and item.get(QUOTE_TRANSLATED_KEY) is True:
        card[QUOTE_TRANSLATED_KEY] = True
    return card


# ─── The run's index ─────────────────────────────────────────────────────────


def _run_segments(ctx: PipelineContext) -> list[Any]:
    """The transcript the pipeline read: the fetched segments, else the prompt segments."""
    transcript = getattr(ctx, "transcript_data", None)
    for segments in (getattr(transcript, "segments", None), getattr(ctx, "prompt_segments", None)):
        if isinstance(segments, list) and segments:
            return segments
    return []


def run_quote_index(ctx: PipelineContext) -> QuoteIndex | None:
    """The run's quote index, built on first use and kept on the context; None when the
    run has no timed transcript (a metadata-only run checks no quote)."""
    cached = getattr(ctx, "quote_index", None)
    if isinstance(cached, QuoteIndex):
        return cached
    segments = _run_segments(ctx)
    if not segments:
        return None
    language = getattr(ctx, "source_language_code", None)
    index = QuoteIndex.build(segments, language if isinstance(language, str) else None)
    ctx.quote_index = index
    return index
