"""Quote verification (output-quality 2.0 d, R10, D12): is a quote in the transcript read?

A ``quote`` field promises the speaker's own words. ``verify_quote`` looks a quote up
in a ``QuoteIndex`` built once per transcript (``run_quote_index`` caches it on the
pipeline context):

- **Normalized** on both sides: NFKC, casefolded, apostrophes dropped ("don't" =
  "dont" = "don’t"), every other non-word character — punctuation, curly or straight
  quote marks, dashes — read as a space, thousands separators joined ("8,000" =
  "8000"), whitespace collapsed. The hesitation words in ``FILLERS`` are dropped on
  both sides, after the prompt render's own filler pass (``remove_fillers``: "um",
  "uh", "basically", "so yeah" …): an English prompt transcript is rendered without
  them, so a quote copied from it lacks what the raw captions keep ("Paper basically
  takes me" was quoted "Paper takes me"), and a hesitation is never what a quote is
  for. Dropping words on both sides only brings two texts closer, so a Portuguese
  "um" ("one") costs nothing.
- **Exact first**: the normalized quote as a whole-word run of the normalized
  transcript — the occurrence nearest ``near_seconds`` when the line recurs.
- **Else fuzzy**: candidate windows anchored by the quote's word trigrams (each hit
  votes for the window it implies; the most-voted windows are scored, ties nearest
  ``near_seconds``); a window's score is the difflib ratio between the quote and the
  transcript words it aligns to; ``FUZZY_THRESHOLD`` (0.9) passes.
- A quote cut with "…" / "..." is fragments: every fragment must pass.

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
import re
import unicodedata
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal, TypeGuard

from src.services.transcript.cleaner import remove_fillers

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

QuoteStatus = Literal["verified", "unverified", "translated"]
VERIFIED: QuoteStatus = "verified"
UNVERIFIED: QuoteStatus = "unverified"
TRANSLATED: QuoteStatus = "translated"

# Written on every checked item of the copy assembly reads.
QUOTE_STATUS_KEY = "quoteStatus"
# A translated quote's flag, carried onto the card that shows it (rendered as prose).
# A boolean: the English→source translation walk translates every string leaf, so a
# "translated" status string would arrive in the source-language tabs translated too.
QUOTE_TRANSLATED_KEY = "quoteTranslated"

FUZZY_THRESHOLD = 0.9
FILLERS: frozenset[str] = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "hmm", "mm"})

# Words per anchor; a quote shorter than this passes only as an exact match.
_NGRAM = 3
# An anchor heard more often than this ("you know what") votes for no window.
_COMMON_NGRAM = 50
# Windows scored per fragment.
_CANDIDATES = 4
# Votes for starts this close are one window: a dropped or added word shifts the start.
_JITTER = 3
# Occurrences of an exact match weighed against ``near_seconds``.
_EXACT_SCAN = 64
_ANCHOR_KEYS = ("timestamp", "seconds", "startSeconds")
# A quote list's item IS its quote: an unverified one leaves the list.
_QUOTE_LIST_KEY = "quotes"

_ELLIPSIS = re.compile(r"…|\.{3,}")
_APOSTROPHES = re.compile(r"['`´‘’ʼ]")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_WORD = re.compile(r"\w+")


def normalize_words(text: str) -> list[str]:
    """The words a quote or transcript line is compared by (module docstring)."""
    folded = unicodedata.normalize("NFKC", remove_fillers(text)).casefold()
    folded = _THOUSANDS.sub("", _APOSTROPHES.sub("", folded))
    return [word for word in _WORD.findall(folded) if word not in FILLERS]


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _segment_start(segment: Mapping[str, Any], previous: int) -> int:
    """A segment's start in whole seconds (``start`` / ``seconds``, or ``startMs``);
    the previous segment's when it states none."""
    if _is_number(segment.get("startMs")):
        return int(segment["startMs"] / 1000)
    for key in ("start", "seconds"):
        if _is_number(segment.get(key)):
            return int(segment[key])
    return previous


@dataclass(frozen=True)
class QuoteVerdict:
    """``seconds``: where the quote was found — for a failed quote, where its closest
    window sits (None when no anchor hit); ``score``: 1.0 exact, else the best ratio."""

    status: QuoteStatus
    seconds: int | None
    score: float


@dataclass(frozen=True)
class _Match:
    score: float
    position: int | None


@dataclass
class QuoteIndex:
    """One transcript's words with the second each is spoken at, its anchors and memo."""

    words: list[str]
    seconds: list[int]
    source_language: str | None = None
    _text: str = ""
    _offsets: list[int] = field(default_factory=list)
    _anchors: dict[tuple[str, ...], list[int]] = field(default_factory=dict)
    _memo: dict[tuple[str, int | None], QuoteVerdict] = field(default_factory=dict)

    @classmethod
    def build(
        cls, segments: Sequence[Mapping[str, Any]], source_language: str | None = None
    ) -> QuoteIndex:
        """``segments``: ``{text, start|seconds|startMs}`` dicts in time order;
        ``source_language``: ``ctx.source_language_code`` (None = English)."""
        words: list[str] = []
        seconds: list[int] = []
        start = 0
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            start = _segment_start(segment, start)
            spoken = normalize_words(str(segment.get("text") or ""))
            words.extend(spoken)
            seconds.extend([start] * len(spoken))
        index = cls(words, seconds, source_language)
        index._prepare()
        return index

    def _prepare(self) -> None:
        offset = 1
        for word in self.words:
            self._offsets.append(offset)
            offset += len(word) + 1
        self._text = f" {' '.join(self.words)} "
        for i in range(len(self.words) - _NGRAM + 1):
            self._anchors.setdefault(tuple(self.words[i : i + _NGRAM]), []).append(i)

    @property
    def english_source(self) -> bool:
        code = (self.source_language or "en").strip().lower()
        return code == "en" or code.startswith("en-")

    def verify(self, quote: str, near_seconds: int | None = None) -> QuoteVerdict:
        """The memoized verdict on ``quote`` (see ``verify_quote``)."""
        key = (quote.strip(), near_seconds)
        verdict = self._memo.get(key)
        if verdict is None:
            verdict = self._verdict(key[0], near_seconds)
            self._memo[key] = verdict
        return verdict

    def _verdict(self, quote: str, near: int | None) -> QuoteVerdict:
        fragments = [words for part in _ELLIPSIS.split(quote) if (words := normalize_words(part))]
        score = 1.0 if fragments else 0.0
        found: int | None = None
        for words in fragments:
            match = self._match(words, near if found is None else found)
            score = min(score, match.score)
            if found is None and match.position is not None:
                found = self.seconds[match.position]
        if score >= FUZZY_THRESHOLD:
            return QuoteVerdict(VERIFIED, found, round(score, 3))
        status = UNVERIFIED if self.english_source else TRANSLATED
        return QuoteVerdict(status, found, round(score, 3))

    def _match(self, words: list[str], near: int | None) -> _Match:
        exact = self._exact_positions(words)
        if exact:
            return _Match(1.0, self._nearest(exact, near))
        if len(words) < _NGRAM:
            return _Match(0.0, None)
        scored = [self._window_score(words, start) for start in self._candidates(words, near)]
        passing = [m.position for m in scored if m.score >= FUZZY_THRESHOLD]
        if passing:
            return _Match(max(m.score for m in scored), self._nearest(passing, near))
        return max(scored, key=lambda m: m.score, default=_Match(0.0, None))

    def _exact_positions(self, words: list[str]) -> list[int]:
        needle = f" {' '.join(words)} "
        positions: list[int] = []
        at = self._text.find(needle)
        while at != -1 and len(positions) < _EXACT_SCAN:
            positions.append(bisect_left(self._offsets, at + 1))
            at = self._text.find(needle, at + 1)
        return positions

    def _nearest(self, positions: Sequence[int | None], near: int | None) -> int | None:
        known = [p for p in positions if p is not None]
        if near is None or not known:
            return known[0] if known else None
        return min(known, key=lambda p: abs(self.seconds[p] - near))

    def _candidates(self, words: list[str], near: int | None) -> list[int]:
        """Window starts the quote's anchors vote for, most votes first, ties nearest."""
        votes: Counter[int] = Counter()
        for i in range(len(words) - _NGRAM + 1):
            hits = self._anchors.get(tuple(words[i : i + _NGRAM]), [])
            if len(hits) <= _COMMON_NGRAM:
                votes.update(max(0, hit - i) for hit in hits)
        windows = _merge_votes(votes)
        last = len(self.seconds) - 1

        def rank(window: tuple[int, int]) -> tuple[int, int]:
            distance = 0 if near is None else abs(self.seconds[min(window[1], last)] - near)
            return -window[0], distance

        return [start for _, start in sorted(windows, key=rank)[:_CANDIDATES]]

    def _window_score(self, words: list[str], start: int) -> _Match:
        """The ratio between the quote and the transcript words it aligns to near ``start``."""
        slack = max(2, len(words) // 5)
        low = max(0, start - slack)
        window = self.words[low : start + len(words) + slack]
        matcher = SequenceMatcher(None, words, window, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if not blocks:
            return _Match(0.0, None)
        first, end = blocks[0].b, blocks[-1].b + blocks[-1].size
        span = " ".join(window[first:end])
        ratio = SequenceMatcher(None, " ".join(words), span, autojunk=False).ratio()
        return _Match(ratio, low + first)


def _merge_votes(votes: Counter[int]) -> list[tuple[int, int]]:
    """``(votes, start)`` per window: starts within ``_JITTER`` of each other are one,
    led by its most-voted start."""
    groups: list[list[int]] = []
    for start in sorted(votes):
        if groups and start - groups[-1][-1] <= _JITTER:
            groups[-1].append(start)
        else:
            groups.append([start])
    return [(sum(votes[s] for s in group), max(group, key=votes.__getitem__)) for group in groups]


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
