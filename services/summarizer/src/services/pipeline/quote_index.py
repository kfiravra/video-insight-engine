"""The quote matcher (output-quality 2.0 d, R10): where a quote sits in one transcript.

``QuoteIndex`` holds a transcript's words with the second each is spoken at; ``verify``
(``quote_verify.verify_quote``) answers whether a quote is in it:

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
- A quote cut with "…" / "..." is fragments, every one of which must pass, in order and
  close together: the longest is placed like a whole quote, then each other one only
  beside its neighbour — starting after it ends (before it starts, for an earlier one),
  at most ``FRAGMENT_GAP_WORDS`` words and ``FRAGMENT_GAP_SECONDS`` seconds away. A line
  stitched from distant ones, or one that drops a "not" between far-apart words, fails;
  a fragment shorter than an anchor passes only as an exact match inside that bound.

Split out of ``quote_verify`` (the status rules, the extraction pre-pass and the run's
index stay there and re-export these names).
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal, TypeGuard

from src.services.transcript.cleaner import remove_fillers

QuoteStatus = Literal["verified", "unverified", "translated"]
VERIFIED: QuoteStatus = "verified"
UNVERIFIED: QuoteStatus = "unverified"
TRANSLATED: QuoteStatus = "translated"


FUZZY_THRESHOLD = 0.9
FILLERS: frozenset[str] = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "hmm", "mm"})
# How far apart two fragments of a "…"-cut quote may sit (both bounds hold).
FRAGMENT_GAP_WORDS = 40
FRAGMENT_GAP_SECONDS = 60

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


@dataclass(frozen=True)
class _Placed:
    """A run of transcript words a quote's words align to: ``start`` … ``end`` (exclusive)."""

    score: float
    start: int
    end: int


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
        if len(fragments) > 1:
            match = self._chain(fragments, near)
        else:
            match = self._match(fragments[0], near) if fragments else _Match(0.0, None)
        found = None if match.position is None else self.seconds[match.position]
        if match.score >= FUZZY_THRESHOLD:
            return QuoteVerdict(VERIFIED, found, round(match.score, 3))
        status = UNVERIFIED if self.english_source else TRANSLATED
        return QuoteVerdict(status, found, round(match.score, 3))

    def _match(self, words: list[str], near: int | None) -> _Match:
        exact = self._exact_positions(words)
        if exact:
            return _Match(1.0, self._nearest(exact, near))
        if len(words) < _NGRAM:
            return _Match(0.0, None)
        scored = self._windows(words, self._candidates(words, near))
        passing = [m.start for m in scored if m.score >= FUZZY_THRESHOLD]
        if passing:
            return _Match(max(m.score for m in scored), self._nearest(passing, near))
        best = max(scored, key=lambda m: m.score, default=None)
        return _Match(0.0, None) if best is None else _Match(best.score, best.start)

    # ─── A quote cut with "…" ───

    def _chain(self, fragments: list[list[str]], near: int | None) -> _Match:
        """The fragments in order and close together (module docstring): the longest one
        placed nearest ``near`` first, the others beside it. Score = the weakest
        fragment's; position = the first fragment's (the pivot's for a failed chain)."""
        pivot = max(range(len(fragments)), key=lambda i: len(fragments[i]))
        placements = self._placements(fragments[pivot], near)
        for placed in placements:
            chained = self._chained_from(fragments, pivot, placed)
            if chained is not None:
                return chained
        if placements:
            return _Match(0.0, placements[0].start)
        return self._match(fragments[pivot], near)

    def _placements(self, words: list[str], near: int | None) -> list[_Placed]:
        """Where the pivot may sit, nearest ``near`` first: each exact occurrence, else
        each fuzzy window that passes."""
        exact = self._exact_positions(words)
        if exact:
            placed = [_Placed(1.0, p, p + len(words)) for p in exact]
        elif len(words) >= _NGRAM:
            windows = self._windows(words, self._candidates(words, near))
            placed = [w for w in windows if w.score >= FUZZY_THRESHOLD]
        else:
            placed = []
        return sorted(placed, key=lambda p: self._distance(p.start, near))

    def _chained_from(
        self, fragments: list[list[str]], pivot: int, placed: _Placed
    ) -> _Match | None:
        """Every other fragment beside the pivot at ``placed``; None when one cannot be."""
        score, first, last = placed.score, placed, placed
        for words in fragments[pivot + 1 :]:
            after = self._beside(words, last, forward=True)
            if after is None:
                return None
            score, last = min(score, after.score), after
        for words in reversed(fragments[:pivot]):
            before = self._beside(words, first, forward=False)
            if before is None:
                return None
            score, first = min(score, before.score), before
        return _Match(score, first.start)

    def _beside(self, words: list[str], neighbour: _Placed, forward: bool) -> _Placed | None:
        """``words`` passing right after (``forward``) or right before ``neighbour``,
        within the gap bound — the closest placement; None when none passes there."""
        if forward:
            low, high = neighbour.end, neighbour.end + FRAGMENT_GAP_WORDS
        else:
            high = neighbour.start - len(words)
            low = high - FRAGMENT_GAP_WORDS
        found = [_Placed(1.0, p, p + len(words)) for p in self._exact_between(words, low, high)]
        if not found and len(words) >= _NGRAM:
            slack = max(2, len(words) // 5)
            starts = self._candidates(words, None, (low - slack, high + slack))
            found = [w for w in self._windows(words, starts) if w.score >= FUZZY_THRESHOLD]
        pairs = [(neighbour, p) if forward else (p, neighbour) for p in found]
        close = [pair for pair in pairs if self._near(*pair)]
        best = min(close, key=lambda pair: pair[1].start - pair[0].end, default=None)
        if best is None:
            return None
        return best[1] if forward else best[0]

    def _near(self, first: _Placed, second: _Placed) -> bool:
        """``second`` starts after ``first`` ends, within both gap bounds."""
        gap = second.start - first.end
        seconds = self.seconds[second.start] - self.seconds[first.end - 1]
        return 0 <= gap <= FRAGMENT_GAP_WORDS and seconds <= FRAGMENT_GAP_SECONDS

    def _exact_between(self, words: list[str], low: int, high: int) -> list[int]:
        """Starts of the exact occurrences of ``words`` that begin at word ``low`` … ``high``."""
        low, high = max(low, 0), min(high, len(self.words) - len(words))
        if low > high:
            return []
        needle = f" {' '.join(words)} "
        end = self._offsets[high] - 1 + len(needle)
        positions: list[int] = []
        at = self._text.find(needle, self._offsets[low] - 1, end)
        while at != -1:
            positions.append(bisect_left(self._offsets, at + 1))
            at = self._text.find(needle, at + 1, end)
        return positions

    # ─── Lookups ───

    def _distance(self, position: int, near: int | None) -> int:
        return 0 if near is None else abs(self.seconds[position] - near)

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

    def _candidates(
        self, words: list[str], near: int | None, bound: tuple[int, int] | None = None
    ) -> list[int]:
        """Window starts the quote's anchors vote for, most votes first, ties nearest;
        with ``bound``, only starts inside it."""
        low, high = bound if bound is not None else (0, len(self.words))
        votes: Counter[int] = Counter()
        for i in range(len(words) - _NGRAM + 1):
            hits = self._anchors.get(tuple(words[i : i + _NGRAM]), [])
            if len(hits) <= _COMMON_NGRAM:
                votes.update(s for hit in hits if low <= (s := max(0, hit - i)) <= high)
        windows = _merge_votes(votes)
        last = len(self.seconds) - 1

        def rank(window: tuple[int, int]) -> tuple[int, int]:
            distance = 0 if near is None else abs(self.seconds[min(window[1], last)] - near)
            return -window[0], distance

        return [start for _, start in sorted(windows, key=rank)[:_CANDIDATES]]

    def _windows(self, words: list[str], starts: Sequence[int]) -> list[_Placed]:
        scored = (self._window_score(words, start) for start in starts)
        return [window for window in scored if window is not None]

    def _window_score(self, words: list[str], start: int) -> _Placed | None:
        """The ratio between the quote and the transcript words it aligns to near ``start``;
        None when no word aligns."""
        slack = max(2, len(words) // 5)
        low = max(0, start - slack)
        window = self.words[low : start + len(words) + slack]
        matcher = SequenceMatcher(None, words, window, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if not blocks:
            return None
        first, end = blocks[0].b, blocks[-1].b + blocks[-1].size
        span = " ".join(window[first:end])
        ratio = SequenceMatcher(None, " ".join(words), span, autojunk=False).ratio()
        return _Placed(ratio, low + first, low + end)


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
