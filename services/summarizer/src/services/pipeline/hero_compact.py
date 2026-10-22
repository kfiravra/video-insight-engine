"""The merged extraction as the hero rewrite reads it (output-quality 2.1).

``synthesis.compact_extraction`` cuts JSON to a character budget; the hero needs
something else: every stamped item under a short id (``K12`` a key idea, ``M3`` a
moment …) the model cites, so CODE copies the item's timestamp into the takeaway
(gate 2 holds every takeaway stamp to ± 30 s of its claim — a stamp the model
types is a guess). One line per item, in the extraction's own order:

    learning.keyIdeas:
    K1 [1:00] The AI moment is exciting — He rejects … "I have AI delirium …" (kind=thesis)

Ids are numbered over the WHOLE extraction before anything is cut, so an id
names the same item at every budget level. The text fits
``HERO_EXTRACTION_MAX_TOKENS``: text fields shorten level by level (bodies,
then quotes), and only then do long lists keep an even spread of their items —
the tail of the video stays in (S01's brief lost the second half once).
Deterministic: the same extraction always gives the same bytes.

With the run's quote index the extraction is read as 2.0's pre-pass leaves it
(``verify_extraction_quotes``): a quote the transcript never says is out of the item's
line, the evidence and ``quotes`` (a quote list loses the item) — a number or name
only an invented quote holds can carry no takeaway (G1-6, D13); a translated quote
(a non-English source) stays.

An item's stamp is where the extraction put the item, not always where its words
are said (S01: K's 680 s, its quote said at 723 s). Each id's quote is kept beside
its stamp, and ``stamps_at_quotes`` moves the stamp to where the run's transcript
says the quote (2.0's ``verify_quote``) — only a verified quote, and only within
``QUOTE_STAMP_MAX_SHIFT_S`` of the item's stamp.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from .memory import format_clock
from .quote_verify import (
    QUOTE_STATUS_KEY,
    QUOTE_TRANSLATED_KEY,
    VERIFIED,
    QuoteIndex,
    verify_extraction_quotes,
    verify_quote,
)

HERO_EXTRACTION_MAX_TOKENS = 5000
# A quote found farther than this from its item's stamp is the line said again
# elsewhere (or another item's words), not where the item is: its stamp stays. An
# answer runs on past its question's stamp (S02: the claim and its quote 184 s in).
QUOTE_STAMP_MAX_SHIFT_S = 240
# Measured on S01 (2026-10-21 trace): the memory call's 53.1 K characters of marked
# transcript + prompt billed 17,490 input tokens on the Claude 5.5 tokenizer — 3.03
# characters a token. The project's word estimator (``extractor._estimate_tokens``,
# 1.33 a word) reads the same text as 12.6 K, too low to budget a request by.
CHARS_PER_TOKEN = 3.0

# The id letter of each stamped list the hero may cite; any other stamped list → "I".
_ID_PREFIX: dict[str, str] = {
    "keyIdeas": "K",
    "keyPoints": "K",
    "timestamps": "M",
    "highlights": "M",
    "keyMoments": "M",
    "claims": "C",
    "qa": "Q",
    "segments": "S",
    "steps": "P",
    "exercises": "E",
    "rankings": "R",
    "concepts": "D",
    "topics": "T",
    "snippets": "X",
    "spots": "L",
}
_DEFAULT_PREFIX = "I"
_STAMP_KEYS = ("seconds", "timestamp")
# A line's main text: the first of these the item has.
_HEAD_KEYS = (
    "claim",
    "label",
    "title",
    "name",
    "item",
    "question",
    "instruction",
    "text",
    "competitor",
    "city",
)
_QUOTE_KEY = "quote"
# Never shown: stamps (the line's prefix carries them), media, ids, layout and code.
_SKIP_KEYS = frozenset(
    {
        "seconds",
        "timestamp",
        "endSeconds",
        "endTimestamp",
        "time",
        "emoji",
        "thumbnailUrl",
        "s3Key",
        "url",
        "code",
        "connections",
        "language",
        "displayAmount",
        "number",
        "section",
        "group",
        QUOTE_STATUS_KEY,
        QUOTE_TRANSLATED_KEY,
    }
)
# A string this short is a tag (kind=thesis, speaker=DHH, tier=S), not a body.
_TAG_WORDS = 4
_STAMP_PREFIX_RE = re.compile(r"^(\s*)[A-Z]\d+ \[\d+(?::\d{2}){1,2}\] ", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Level:
    """How much of each text a budget level keeps (0 = none; None = every item)."""

    scalar_words: int
    head_words: int
    body_words: int
    quote_words: int
    list_cap: int | None


# Bodies (why / description) go first, then quotes; items are spread last.
_LEVELS = (
    _Level(120, 40, 30, 25, None),
    _Level(90, 30, 20, 20, None),
    _Level(70, 28, 12, 15, None),
    _Level(60, 25, 0, 12, None),
    _Level(50, 22, 0, 0, None),
    _Level(40, 20, 0, 0, 24),
    _Level(30, 16, 0, 0, 16),
    _Level(25, 12, 0, 0, 10),
)


@dataclass(frozen=True)
class CompactExtraction:
    """The ``<extracted>`` body, the seconds of every id it shows and the quote of
    every shown id whose item carries one."""

    text: str
    stamps: Mapping[str, int] = field(default_factory=dict)
    quotes: Mapping[str, str] = field(default_factory=dict)

    @property
    def evidence(self) -> str:
        """The text as video content: without the ids and stamps code put in front."""
        return _STAMP_PREFIX_RE.sub(r"\1", self.text)


def estimate_tokens(text: str) -> int:
    """Input tokens ``text`` costs on the Claude 5.5 tokenizer (see ``CHARS_PER_TOKEN``)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# ─── Text pieces ───


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _words(text: str, limit: int) -> str:
    words = _collapse(text).split(" ")
    return " ".join(words) if len(words) <= limit else f"{' '.join(words[:limit])}…"


def _stamp(item: object, duration: int | None) -> int | None:
    """The item's whole-second stamp inside the video (0 is a model's "no stamp")."""
    if not isinstance(item, Mapping):
        return None
    for key in _STAMP_KEYS:
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value if duration is None or value <= duration else None
    return None


def _spread(count: int, cap: int | None) -> frozenset[int]:
    """Indices of an even spread of ``cap`` items out of ``count`` (first and last kept)."""
    if cap is None or count <= cap:
        return frozenset(range(count))
    if cap == 1:
        return frozenset({0})
    return frozenset(round(i * (count - 1) / (cap - 1)) for i in range(cap))


def _tag(key: str, value: object) -> str | None:
    """``key=value`` for a number, a short string or a small mapping (a figure)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return f"{key}={value}"
    if isinstance(value, Mapping):
        parts = [_collapse(str(v)) for v in value.values() if isinstance(v, str | int | float)]
        joined = " ".join(p for p in parts if p)
        return f"{key}={joined}" if joined else None
    if isinstance(value, str) and 0 < len(value.split()) <= _TAG_WORDS:
        return f"{key}={_collapse(value)}"
    return None


def _body(value: object, level: _Level) -> str | None:
    """A longer string, or a list of strings joined, cut to the level's body words."""
    if isinstance(value, list):
        value = "; ".join(_collapse(v) for v in value if isinstance(v, str) and v.strip())
    if not isinstance(value, str) or not value.strip() or not level.body_words:
        return None
    return _words(value, level.body_words)


def _head_key(item: Mapping[str, object]) -> str | None:
    return next((k for k in _HEAD_KEYS if isinstance(item.get(k), str) and item[k]), None)


def _item_text(item: Mapping[str, object], level: _Level) -> str:
    """One item's line text: head — bodies "quote" (tags)."""
    head_key = _head_key(item)
    bodies: list[str] = []
    tags: list[str] = []
    for key, value in item.items():
        if key in _SKIP_KEYS or key in (head_key, _QUOTE_KEY) or _is_item_list(value):
            continue
        tag = _tag(key, value)
        body = None if tag else _body(value, level)
        tags.extend([tag] if tag else [])
        bodies.extend([body] if body else [])
    text = _words(str(item[head_key]), level.head_words) if head_key else ""
    if bodies:
        text = f"{text} — {' | '.join(bodies)}" if text else " | ".join(bodies)
    quote = item.get(_QUOTE_KEY)
    if isinstance(quote, str) and quote.strip() and level.quote_words:
        text = f'{text} "{_words(quote, level.quote_words)}"'
    return f"{text} ({'; '.join(tags)})".lstrip() if tags else text


def _is_item_list(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(v, Mapping) for v in value)


# ─── Rendering ───


@dataclass
class _Render:
    """One pass over the extraction: ids for EVERY stamped item, lines for the kept ones."""

    level: _Level
    duration: int | None
    lines: list[str] = field(default_factory=list)
    stamps: dict[str, int] = field(default_factory=dict)
    quotes: dict[str, str] = field(default_factory=dict)
    counts: Counter[str] = field(default_factory=Counter)

    def _id(self, name: str) -> str:
        prefix = _ID_PREFIX.get(name, _DEFAULT_PREFIX)
        self.counts[prefix] += 1
        return f"{prefix}{self.counts[prefix]}"

    def _emit(self, indent: str, name: str, item: object, shown: bool) -> None:
        """One list item (and the stamped items listed inside it)."""
        seconds = _stamp(item, self.duration)
        item_id = self._id(name) if seconds is not None else None
        if shown:
            text = _item_text(item, self.level) if isinstance(item, Mapping) else ""
            text = text or (_words(item, self.level.head_words) if isinstance(item, str) else "")
            if item_id is not None and seconds is not None:
                self.stamps[item_id] = seconds
                self._keep_quote(item_id, item)
                self.lines.append(f"{indent}{item_id} [{format_clock(seconds)}] {text}")
            elif text:
                self.lines.append(f"{indent}- {text}")
        if isinstance(item, Mapping):
            for key, value in item.items():
                if _is_item_list(value):
                    self.items(f"{indent}  ", key, value, shown)

    def _keep_quote(self, item_id: str, item: object) -> None:
        quote = item.get(_QUOTE_KEY) if isinstance(item, Mapping) else None
        if isinstance(quote, str) and quote.strip():
            self.quotes[item_id] = quote

    def items(self, indent: str, name: str, items: Sequence[object], shown: bool) -> None:
        kept = _spread(len(items), self.level.list_cap)
        for index, item in enumerate(items):
            self._emit(indent, name, item, shown and index in kept)

    def field(self, path: str, name: str, value: object) -> None:
        """One extraction field: a header + its items (a mapping is one item), or one
        ``path: text`` line."""
        if isinstance(value, list | Mapping) and value:
            self.lines.append(f"{path}:")
            if isinstance(value, list):
                self.items("", name, value, shown=True)
            else:
                self._emit("", name, value, shown=True)
        elif isinstance(value, str) and value.strip():
            self.lines.append(f"{path}: {_words(value, self.level.scalar_words)}")


def _fields(extraction: Mapping[str, object]) -> Iterator[tuple[str, str, object]]:
    """(path, field name, value) of every domain field, ``_``-prefixed keys skipped."""
    for domain, fields in extraction.items():
        if domain.startswith("_") or not isinstance(fields, Mapping):
            continue
        for name, value in fields.items():
            if not str(name).startswith("_"):
                yield f"{domain}.{name}", str(name), value


def _render(extraction: Mapping[str, object], level: _Level, duration: int | None) -> _Render:
    render = _Render(level, duration)
    for path, name, value in _fields(extraction):
        render.field(path, name, value)
    return render


def _cut_to(text: str, max_chars: int) -> str:
    """The last resort: whole lines up to ``max_chars``."""
    cut = text[:max_chars]
    return cut.rsplit("\n", 1)[0] if "\n" in cut else cut


def _said_quotes_only(
    extraction: Mapping[str, object], quote_index: QuoteIndex | None
) -> Mapping[str, object]:
    """The extraction without the quotes the transcript never says (module docstring)."""
    if quote_index is None:
        return extraction
    checked = verify_extraction_quotes(dict(extraction), None, quote_index).extraction
    return extraction if checked is None else checked


def compact_for_hero(
    extraction: Mapping[str, object] | None,
    duration: int | None = None,
    max_tokens: int = HERO_EXTRACTION_MAX_TOKENS,
    quote_index: QuoteIndex | None = None,
) -> CompactExtraction:
    """The extraction as id'd lines within ``max_tokens`` (empty for no extraction).

    ``duration`` (seconds) leaves a stamp past the video's end without an id;
    ``quote_index`` (the run's) leaves out every quote it reads as never said.
    """
    if not extraction:
        return CompactExtraction("")
    extraction = _said_quotes_only(extraction, quote_index)
    for level in _LEVELS:
        render = _render(extraction, level, duration)
        text = "\n".join(render.lines)
        if estimate_tokens(text) <= max_tokens:
            return CompactExtraction(text, render.stamps, render.quotes)
    text = _cut_to(text, int(max_tokens * CHARS_PER_TOKEN))
    shown = {line.strip().split(" ", 1)[0] for line in text.split("\n")}
    return CompactExtraction(
        text,
        {k: v for k, v in render.stamps.items() if k in shown},
        {k: v for k, v in render.quotes.items() if k in shown},
    )


def _quote_second(quote: str, stamp: int, index: QuoteIndex) -> int | None:
    """Where ``quote`` is said — verified and within the shift of ``stamp`` — else None."""
    found = verify_quote(quote, index, near_seconds=stamp)
    if found.status != VERIFIED or found.seconds is None:
        return None
    return found.seconds if abs(found.seconds - stamp) <= QUOTE_STAMP_MAX_SHIFT_S else None


def stamps_at_quotes(compact: CompactExtraction, index: QuoteIndex | None) -> dict[str, int]:
    """Every id's stamp, a quoted item's moved to where its quote is said (module
    docstring); the items' own stamps without a quote index."""
    stamps = dict(compact.stamps)
    if index is None:
        return stamps
    for item_id, quote in compact.quotes.items():
        stamp = stamps.get(item_id)
        said = None if stamp is None else _quote_second(quote, stamp, index)
        if said is not None:
            stamps[item_id] = said
    return stamps
