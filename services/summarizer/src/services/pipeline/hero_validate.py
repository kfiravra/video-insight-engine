"""Deterministic checks on the hero rewrite's answer (output-quality 2.1; D13, D18, G1-6).

Every check reads the answer against what the call read about the video
(``HeroEvidence``: the transcript when it was sent, ``<video_memory>``, the
compacted extraction — never the job's own instructions, whose "16 words" is no
fact about the video). The prose readers live in ``hero_text``.

- numbers (G1-6, D13): every number in the tldr, brief, takeaways, both lines and
  the SEO line appears in the evidence (thousands separators and leading zeros
  ignored; a clock ``m:ss`` counts as a seek inside the video, not a fact). A
  brief sentence or a takeaway that states another number fails;
- sizes: brief 3–5 sentences, tldr ≤ 200 characters (the prompt asks for 140),
  takeaways = the 3 / 5 / 7 band, worthItFor / skipIf ≤ 20 words each (D18; the
  prompt asks for 16);
- stamps: each takeaway resolves to a second of the video — the cited id's
  stamp (code copies it), else the model's own ``m:ss`` inside the video. A call
  that read no transcript (a chunked run, or no group blocks) has nothing to read
  a clock from, so its typed ``m:ss`` stands only within ``TYPED_STAMP_MAX_GAP_S``
  of a clock it did read — an ``<extracted>`` item's stamp or a ``<video_memory>``
  clock — else the takeaway fails (D13: nothing derived from nothing);
- quotes (2.0 d, D12, R10): a double-quoted span of 3+ words in the tldr, brief,
  takeaways or the two lines is looked up in the run's transcript
  (``quote_verify``): one the video never says fails its sentence / line /
  takeaway; one a non-English source has only in translation loses its marks and
  ships as prose. Without a quote index nothing is checked (as the pre-pass);
- tabs: no tldr / brief / line names a planned tab (``hero_text.HeroTabNames``: the
  label in its own case, or in a sentence about tabs / sections / pages — the
  message names the label) or points at the page ("check out", "this page");
- D18 heuristics: a line judging the creator (``hero_text.CREATOR_JUDGEMENT_RE``)
  and a word that reads as a proper name the evidence never contains (an outside
  fact, ``hero_text.names_in``). Both are proxies — they catch "the host rambles"
  and an invented "Netflix", not every judgement or outside fact.

``finalize`` turns a checked draft into the result: a failing optional line
(worthItFor / skipIf / seoDescription) is dropped, a brief sentence that fails is
dropped while 3 remain, a longer brief or takeaway set keeps its first 5 / its
band; a failing tldr (a long one too — a cut thesis is the first thing the page
shows), brief or takeaway set means no result (the caller keeps memory's hero).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ...models.hero_types import HeroRewriteResult
from ...utils.data_helpers import parse_timestamp_to_seconds
from .hero_text import (
    CLOCK_RE,
    CREATOR_JUDGEMENT_RE,
    PAGE_POINTER_RE,
    HeroTabNames,
    collapse,
    names_in,
    quoted_spans,
    sentences_of,
    tab_names,
)
from .memory import truncate_words
from .quote_verify import TRANSLATED, UNVERIFIED, QuoteIndex, QuoteStatus, verify_quote

# The hero's own cap, never a cut: memory's ``TLDR_MAX_CHARS`` (150) sizes its preview,
# and a thesis that runs past it (S01: 151) ships whole or fails to the retry.
HERO_TLDR_MAX_CHARS = 200
LINE_MAX_WORDS = 20
BRIEF_MIN_SENTENCES = 3
BRIEF_MAX_SENTENCES = 5
SEO_MAX_CHARS = 160
# Gate 2 holds a takeaway stamp to ± 30 s of its claim; without the transcript a typed
# one is a clock the call read, or a guess.
TYPED_STAMP_MAX_GAP_S = 30
CORE_FIELDS = ("tldr", "brief", "keyTakeaways")
OPTIONAL_FIELDS = ("worthItFor", "skipIf", "seoDescription")
D18_FIELDS = ("worthItFor", "skipIf")

_MARKER_RE = re.compile(r"\[\d+(?::\d{2}){1,2}\]")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_REF_RE = re.compile(r"\b([A-Z]\d+)\b")

Repair = Literal["fail", "drop_sentence", "truncate"]


@dataclass(frozen=True)
class Problem:
    """One failed check: the field, what to tell the model, and what finalize does."""

    field: str
    message: str
    repair: Repair = "fail"
    index: int | None = None  # the brief sentence or the takeaway it is about


@dataclass(frozen=True)
class DraftTakeaway:
    text: str
    timestamp: int | None


@dataclass(frozen=True)
class HeroDraft:
    """The model's answer, read field by field (whitespace collapsed, stamps resolved,
    translated quotes unmarked)."""

    tldr: str
    sentences: tuple[str, ...]
    takeaways: tuple[DraftTakeaway, ...]
    worth_it_for: str
    skip_if: str
    seo_description: str


@dataclass(frozen=True)
class HeroEvidence:
    """What the call read about the video, and the sizes its answer must have.

    ``reads_transcript`` False: a typed stamp must sit near one of ``clocks`` (the
    ``<extracted>`` stamps + the ``<video_memory>`` clocks). ``quote_index`` None:
    no quoted span is checked.
    """

    text: str
    stamps: Mapping[str, int]
    duration: int | None
    tab_labels: tuple[str, ...]
    takeaway_count: int
    reads_transcript: bool = True
    clocks: frozenset[int] = frozenset()
    quote_index: QuoteIndex | None = None
    numbers: frozenset[str] = field(init=False)
    _lower: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        content = _MARKER_RE.sub(" ", self.text)
        object.__setattr__(self, "numbers", frozenset(numbers_in(content)))
        object.__setattr__(self, "_lower", content.casefold())

    def contains_word(self, word: str) -> bool:
        pattern = rf"(?<!\w){re.escape(word.casefold())}(?!\w)"
        return re.search(pattern, self._lower) is not None


def clock_seconds(text: str) -> frozenset[int]:
    """The seconds of every ``m:ss`` / ``h:mm:ss`` clock ``text`` shows."""
    seconds = (parse_timestamp_to_seconds(clock) for clock in CLOCK_RE.findall(text))
    return frozenset(s for s in seconds if s is not None)


# ─── Quotes ───


def _quote_status(quote: str, evidence: HeroEvidence, near: int | None) -> QuoteStatus | None:
    index = evidence.quote_index
    return None if index is None else verify_quote(quote, index, near_seconds=near).status


def unverified_quotes(
    text: str, evidence: HeroEvidence, near: int | None = None
) -> list[re.Match[str]]:
    """The quoted spans of ``text`` the transcript never says (an English source)."""
    return [
        span
        for span in quoted_spans(text)
        if _quote_status(span.group(1), evidence, near) == UNVERIFIED
    ]


def unmark_translations(text: str, evidence: HeroEvidence, near: int | None = None) -> str:
    """``text`` with the marks off each quoted span the transcript has only in its source
    language (D12: a translation is never shown as the speaker's words)."""
    if evidence.quote_index is None or evidence.quote_index.english_source:
        return text
    for span in reversed(quoted_spans(text)):
        if _quote_status(span.group(1), evidence, near) == TRANSLATED:
            text = f"{text[: span.start()]}{span.group(1)}{text[span.end() :]}"
    return text


# ─── Reading the answer ───


def _read(raw: object, evidence: HeroEvidence, near: int | None = None) -> str:
    return unmark_translations(collapse(raw), evidence, near)


def _typed_stamp(raw_stamp: object, evidence: HeroEvidence) -> int | None:
    """The model's own clock: inside the video and — without the transcript — near a
    clock the call read."""
    seconds = None if isinstance(raw_stamp, bool) else parse_timestamp_to_seconds(raw_stamp)
    if seconds is None or seconds < 0:
        return None
    if evidence.duration is not None and seconds > evidence.duration:
        return None
    if evidence.reads_transcript:
        return seconds
    near = any(abs(seconds - clock) <= TYPED_STAMP_MAX_GAP_S for clock in evidence.clocks)
    return seconds if near else None


def _stamp_of(raw: Mapping[str, object], evidence: HeroEvidence) -> int | None:
    """The cited id's stamp, else the model's own clock (``_typed_stamp``)."""
    ref = raw.get("ref")
    for item_id in _REF_RE.findall(ref) if isinstance(ref, str) else []:
        if item_id in evidence.stamps:
            return evidence.stamps[item_id]
    return _typed_stamp(raw.get("timestamp"), evidence)


def _takeaways(raw: object, evidence: HeroEvidence) -> tuple[DraftTakeaway, ...]:
    if not isinstance(raw, list):
        return ()
    stamped = [(item, _stamp_of(item, evidence)) for item in raw if isinstance(item, Mapping)]
    return tuple(
        DraftTakeaway(_read(item.get("text"), evidence, stamp), stamp) for item, stamp in stamped
    )


def parse_draft(data: object, evidence: HeroEvidence) -> HeroDraft | None:
    """The answer as a draft; None when it is not a JSON object."""
    if not isinstance(data, Mapping):
        return None
    return HeroDraft(
        tldr=_read(data.get("tldr"), evidence),
        sentences=sentences_of(_read(data.get("brief"), evidence)),
        takeaways=_takeaways(data.get("keyTakeaways"), evidence),
        worth_it_for=_read(data.get("worthItFor"), evidence),
        skip_if=_read(data.get("skipIf"), evidence),
        seo_description=collapse(data.get("seoDescription")),
    )


# ─── Facts ───


def _normalized(number: str) -> str:
    """An integer without leading zeros ("09" → "9"); a decimal as written."""
    return number if "." in number else number.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    """The numbers ``text`` states, normalized ("1,000" and "1000" are one; "09" is "9")."""
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        parts = [token.replace(",", "")] if _THOUSANDS_RE.fullmatch(token) else token.split(",")
        found.update(_normalized(part) for part in parts if part)
    return found


def _clocks_inside(text: str, duration: int | None) -> tuple[str, list[str]]:
    """``text`` without its ``m:ss`` clocks, and the clocks past the video's end."""
    late = [
        clock
        for clock in CLOCK_RE.findall(text)
        if duration is not None and (parse_timestamp_to_seconds(clock) or 0) > duration
    ]
    return CLOCK_RE.sub(" ", text), late


def unknown_numbers(text: str, evidence: HeroEvidence) -> list[str]:
    """Numbers (and clocks past the end) in ``text`` that the video does not contain."""
    rest, late = _clocks_inside(text, evidence.duration)
    return [*late, *sorted(numbers_in(rest) - evidence.numbers)]


def outside_names(text: str, evidence: HeroEvidence) -> list[str]:
    """Names the evidence never contains — the outside-fact proxy (module docstring)."""
    names = [name for sentence in sentences_of(text) for name in names_in(sentence)]
    return sorted({name for name in names if not evidence.contains_word(name)})


def _page_named(sentence: str, tabs: HeroTabNames) -> str | None:
    """How ``sentence`` names the page — the tab label it names, or its pointer — else None."""
    if label := tabs.named(sentence):
        return f'names the tab "{label}"'
    pointer = PAGE_POINTER_RE.search(sentence)
    return f'points the reader at the page ("{pointer.group(0)}")' if pointer else None


# ─── Checks ───


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f'"{value}"' for value in values)


def _not_in_video(what: str, values: Sequence[str]) -> str:
    return f"{what} {_quoted(values)}, which the video does not contain"


def _never_said(where: str, spans: Sequence[re.Match[str]]) -> str:
    said = _quoted([span.group(1) for span in spans])
    return f"{where} quotes {said}, which the video never says — quote its words exactly, or none"


def _check_tldr(draft: HeroDraft, evidence: HeroEvidence, tabs: HeroTabNames) -> list[Problem]:
    if not draft.tldr:
        return [Problem("tldr", "tldr is empty")]
    problems: list[Problem] = []
    if len(draft.tldr) > HERO_TLDR_MAX_CHARS:
        message = f"tldr has {len(draft.tldr)} characters — at most {HERO_TLDR_MAX_CHARS}"
        problems.append(Problem("tldr", message))
    if unknown := unknown_numbers(draft.tldr, evidence):
        problems.append(Problem("tldr", _not_in_video("tldr states", unknown)))
    if unsaid := unverified_quotes(draft.tldr, evidence):
        problems.append(Problem("tldr", _never_said("tldr", unsaid)))
    if named := _page_named(draft.tldr, tabs):
        problems.append(Problem("tldr", f"tldr {named} — state the video's claim"))
    return problems


def _sentence_at(sentences: Sequence[str], offset: int) -> int:
    """The index of the sentence holding ``offset`` of ``" ".join(sentences)``."""
    end = -1
    for index, sentence in enumerate(sentences):
        end += len(sentence) + 1
        if offset < end:
            return index
    return len(sentences) - 1


def _brief_quotes(sentences: Sequence[str], evidence: HeroEvidence) -> list[Problem]:
    """Every brief sentence a never-said quote touches (a quote may run across a break)."""
    problems: list[Problem] = []
    for span in unverified_quotes(" ".join(sentences), evidence):
        first, last = _sentence_at(sentences, span.start()), _sentence_at(sentences, span.end() - 1)
        for index in range(first, last + 1):
            message = _never_said(f"brief sentence {index + 1}", [span])
            problems.append(Problem("brief", message, "drop_sentence", index))
    return problems


def _check_brief(draft: HeroDraft, evidence: HeroEvidence, tabs: HeroTabNames) -> list[Problem]:
    count = len(draft.sentences)
    problems: list[Problem] = []
    if count < BRIEF_MIN_SENTENCES:
        problems.append(Problem("brief", f"brief has {count} sentence(s) — write 3 to 5"))
    elif count > BRIEF_MAX_SENTENCES:
        problems.append(Problem("brief", f"brief has {count} sentences — at most 5", "truncate"))
    for index, sentence in enumerate(draft.sentences):
        where = f"brief sentence {index + 1}"
        if unknown := unknown_numbers(sentence, evidence):
            message = _not_in_video(f"{where} states", unknown)
            problems.append(Problem("brief", message, "drop_sentence", index))
        if named := _page_named(sentence, tabs):
            message = f"{where} {named} — describe the video, never the page"
            problems.append(Problem("brief", message, "drop_sentence", index))
    return [*problems, *_brief_quotes(draft.sentences, evidence)]


def _no_stamp(where: str, evidence: HeroEvidence) -> str:
    if evidence.reads_transcript:
        return (
            f'{where} has no usable stamp — put the id of an <extracted> item in "ref", '
            'or the transcript\'s m:ss inside the video in "timestamp"'
        )
    return (
        f"{where} has no usable stamp — no transcript was sent, so put the id of the "
        '<extracted> item it rests on in "ref"'
    )


def _check_takeaway(index: int, takeaway: DraftTakeaway, evidence: HeroEvidence) -> list[Problem]:
    where = f"keyTakeaways[{index}]"
    if not takeaway.text:
        return [Problem("keyTakeaways", f"{where} has no text", index=index)]
    problems: list[Problem] = []
    if takeaway.timestamp is None:
        problems.append(Problem("keyTakeaways", _no_stamp(where, evidence), index=index))
    if unknown := unknown_numbers(takeaway.text, evidence):
        message = _not_in_video(f"{where} states", unknown)
        problems.append(Problem("keyTakeaways", message, index=index))
    if unsaid := unverified_quotes(takeaway.text, evidence, takeaway.timestamp):
        problems.append(Problem("keyTakeaways", _never_said(where, unsaid), index=index))
    return problems


def _check_takeaways(draft: HeroDraft, evidence: HeroEvidence) -> list[Problem]:
    want, got = evidence.takeaway_count, len(draft.takeaways)
    problems: list[Problem] = []
    if got != want:
        repair: Repair = "truncate" if got > want else "fail"
        message = f"keyTakeaways has {got} — write exactly {want}"
        problems.append(Problem("keyTakeaways", message, repair))
    seen: set[str] = set()
    for index, takeaway in enumerate(draft.takeaways[:want]):
        problems.extend(_check_takeaway(index, takeaway, evidence))
        if takeaway.text and takeaway.text.casefold() in seen:
            message = f"keyTakeaways[{index}] repeats an earlier takeaway"
            problems.append(Problem("keyTakeaways", message, index=index))
        seen.add(takeaway.text.casefold())
    return problems


def _check_line(name: str, text: str, evidence: HeroEvidence, tabs: HeroTabNames) -> list[Problem]:
    """worthItFor / skipIf (D18): ≤ 20 words, the content not the creator, nothing outside."""
    if not text:
        return [Problem(name, f"{name} is empty")]
    problems: list[Problem] = []
    if (words := len(text.split())) > LINE_MAX_WORDS:
        problems.append(Problem(name, f"{name} has {words} words — at most {LINE_MAX_WORDS}"))
    if judged := CREATOR_JUDGEMENT_RE.search(text):
        message = f'{name} judges the creator ("{judged.group(0)}") — say what the video contains'
        problems.append(Problem(name, message))
    if names := outside_names(text, evidence):
        problems.append(Problem(name, _not_in_video(f"{name} names", names)))
    if unknown := unknown_numbers(text, evidence):
        problems.append(Problem(name, _not_in_video(f"{name} states", unknown)))
    if unsaid := unverified_quotes(text, evidence):
        problems.append(Problem(name, _never_said(name, unsaid)))
    if named := _page_named(text, tabs):
        problems.append(Problem(name, f"{name} {named} — say what the video contains"))
    return problems


def check_hero(draft: HeroDraft, evidence: HeroEvidence) -> list[Problem]:
    """Every failed check of the draft, in field order (empty = the draft passes)."""
    tabs = tab_names(evidence.tab_labels)
    seo_numbers = unknown_numbers(draft.seo_description, evidence)
    seo = [Problem("seoDescription", _not_in_video("seoDescription states", seo_numbers))]
    return [
        *_check_tldr(draft, evidence, tabs),
        *_check_brief(draft, evidence, tabs),
        *_check_takeaways(draft, evidence),
        *_check_line("worthItFor", draft.worth_it_for, evidence, tabs),
        *_check_line("skipIf", draft.skip_if, evidence, tabs),
        *(seo if seo_numbers else []),
    ]


# ─── The result ───


def drops_a_line(problems: Sequence[Problem]) -> bool:
    """Whether ``finalize`` would ship a D18 line empty — a retry may still save it."""
    return any(p.field in D18_FIELDS and p.repair == "fail" for p in problems)


def _brief(draft: HeroDraft, problems: Sequence[Problem]) -> str | None:
    """The brief without its failing sentences, at most 5; None under 3."""
    dropped = {p.index for p in problems if p.field == "brief" and p.repair == "drop_sentence"}
    kept = [s for i, s in enumerate(draft.sentences) if i not in dropped][:BRIEF_MAX_SENTENCES]
    return " ".join(kept) if len(kept) >= BRIEF_MIN_SENTENCES else None


def _takeaways_out(draft: HeroDraft, count: int) -> list[dict[str, object]] | None:
    kept = draft.takeaways[:count]
    if len(kept) < count or any(t.timestamp is None for t in kept):
        return None
    return [{"text": t.text, "timestamp": t.timestamp} for t in kept]


def finalize(
    draft: HeroDraft, problems: Sequence[Problem], takeaway_count: int
) -> HeroRewriteResult | None:
    """The result after the last attempt: a failing optional line dropped, failing brief
    sentences dropped; None when the tldr, brief or takeaways fail (never a cut tldr)."""
    if any(p.field in CORE_FIELDS and p.repair == "fail" for p in problems):
        return None
    brief = _brief(draft, problems)
    takeaways = _takeaways_out(draft, takeaway_count)
    if brief is None or takeaways is None:
        return None
    failed = {p.field for p in problems if p.repair == "fail"}
    return HeroRewriteResult.model_validate(
        {
            "tldr": draft.tldr,
            "brief": brief,
            "keyTakeaways": takeaways,
            "worthItFor": "" if "worthItFor" in failed else draft.worth_it_for,
            "skipIf": "" if "skipIf" in failed else draft.skip_if,
            "seoDescription": (
                ""
                if "seoDescription" in failed
                else truncate_words(draft.seo_description, SEO_MAX_CHARS)
            ),
        }
    )
