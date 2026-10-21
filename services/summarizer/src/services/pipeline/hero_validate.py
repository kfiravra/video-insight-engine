"""Deterministic checks on the hero rewrite's answer (output-quality 2.1; D13, D18, G1-6).

Every check reads the answer against what the call read about the video
(``HeroEvidence``: the transcript when it was sent, ``<video_memory>``, the
compacted extraction — never the job's own instructions, whose "20 words" is no
fact about the video):

- numbers (G1-6, D13): every number in the tldr, brief, takeaways, both lines and
  the SEO line appears in the evidence (thousands separators and leading zeros
  ignored; a clock ``m:ss`` counts as a seek inside the video, not a fact). A
  brief sentence or a takeaway that states another number fails;
- sizes: brief 3–5 sentences, tldr ≤ 150 characters, takeaways = the 3 / 5 / 7
  band, worthItFor / skipIf ≤ 20 words each (D18);
- stamps: each takeaway resolves to a second of the video — the cited id's
  stamp (code copies it), else the model's own ``m:ss`` inside the video;
- tabs: no tldr / brief / line names a planned tab (the 2.0 scrub's matcher)
  or points at the page ("check out", "this page") — assembly may still drop a tab;
- D18 heuristics: a line judging the creator matches a small explicit lexicon
  (``_CREATOR_JUDGEMENT_RE``), and a capitalized word past a sentence's first
  word that the evidence never contains reads as a fact from outside the video.
  Both are proxies — they catch "the host rambles" and an invented "Netflix",
  not every judgement or outside fact.

``finalize`` turns a checked draft into the result: a failing optional line
(worthItFor / skipIf / seoDescription) is dropped, a brief sentence that fails is
dropped while 3 remain, a too-long tldr is cut; a failing tldr, brief or
takeaway set means no result (the caller keeps memory's hero).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ...models.hero_types import HeroRewriteResult
from ...utils.data_helpers import parse_timestamp_to_seconds
from .assembly.tab_references import TabNames, bare_label
from .memory import TLDR_MAX_CHARS, truncate_words

LINE_MAX_WORDS = 20
BRIEF_MIN_SENTENCES = 3
BRIEF_MAX_SENTENCES = 5
SEO_MAX_CHARS = 160
CORE_FIELDS = ("tldr", "brief", "keyTakeaways")
OPTIONAL_FIELDS = ("worthItFor", "skipIf", "seoDescription")

_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[\"“'‘(]?[A-Z0-9])")
_MARKER_RE = re.compile(r"\[\d+(?::\d{2}){1,2}\]")
_CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}(?::\d{2}){1,2}(?![\d:])")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-z][\w&'’.-]*")
_REF_RE = re.compile(r"\b([A-Z]\d+)\b")
# Pointing the reader at the page, whatever the tab is called.
_PAGE_POINTER_RE = re.compile(r"\bcheck\s+(?:\w+\s+)?out\b|\b(?:this|the)\s+page\b", re.I)
# Heuristic, documented in the module docstring: words that only judge a person, and
# a verdict word next to the person it judges. Content words that also name ideas
# ("boring technology", "clear steps") stay out of the bare list on purpose.
_CREATOR_JUDGEMENT_RE = re.compile(
    r"\b(?:rambl\w*|annoying|annoys|insufferable|smug|arrogant|condescending|cringe\w*"
    r"|clickbait\w*|self-promot\w*|monotone|charismatic|likeable|likable)\b"
    r"|\b(?:great|good|bad|poor|weak|strong|excellent|engaging|entertaining|funny|boring"
    r"|charming|brilliant|terrible|awkward)\s+(?:presenter|speaker|host|creator|narrator"
    r"|teacher|storyteller|youtuber|personality|delivery|presentation)\b"
    r"|\b(?:host|presenter|speaker|creator|narrator|youtuber)\s+(?:is|are|was|seems|gets|can be"
    r"|comes across)\b",
    re.I,
)
_ALWAYS_CAPITALIZED = frozenset({"I"})

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
    """The model's answer, read field by field (whitespace collapsed, stamps resolved)."""

    tldr: str
    sentences: tuple[str, ...]
    takeaways: tuple[DraftTakeaway, ...]
    worth_it_for: str
    skip_if: str
    seo_description: str


@dataclass(frozen=True)
class HeroEvidence:
    """What the call read about the video, and the sizes its answer must have."""

    text: str
    stamps: Mapping[str, int]
    duration: int | None
    tab_labels: tuple[str, ...]
    takeaway_count: int
    numbers: frozenset[str] = field(init=False)
    _lower: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        content = _MARKER_RE.sub(" ", self.text)
        object.__setattr__(self, "numbers", frozenset(numbers_in(content)))
        object.__setattr__(self, "_lower", content.casefold())

    def contains_word(self, word: str) -> bool:
        pattern = rf"(?<!\w){re.escape(word.casefold())}(?!\w)"
        return re.search(pattern, self._lower) is not None


# ─── Reading the answer ───


def _text(raw: object) -> str:
    return _WHITESPACE_RE.sub(" ", raw).strip() if isinstance(raw, str) else ""


def sentences_of(text: str) -> tuple[str, ...]:
    """The sentences of ``text`` (a break needs an end mark and a capital or digit after)."""
    return tuple(s for s in _SENTENCE_RE.split(_text(text)) if s)


def _stamp_of(raw: Mapping[str, object], evidence: HeroEvidence) -> int | None:
    """The cited id's stamp, else the model's own clock inside the video."""
    ref = raw.get("ref")
    for item_id in _REF_RE.findall(ref) if isinstance(ref, str) else []:
        if item_id in evidence.stamps:
            return evidence.stamps[item_id]
    raw_stamp = raw.get("timestamp")
    seconds = None if isinstance(raw_stamp, bool) else parse_timestamp_to_seconds(raw_stamp)
    if seconds is None or seconds < 0:
        return None
    return seconds if evidence.duration is None or seconds <= evidence.duration else None


def _takeaways(raw: object, evidence: HeroEvidence) -> tuple[DraftTakeaway, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        DraftTakeaway(_text(item.get("text")), _stamp_of(item, evidence))
        for item in raw
        if isinstance(item, Mapping)
    )


def parse_draft(data: object, evidence: HeroEvidence) -> HeroDraft | None:
    """The answer as a draft; None when it is not a JSON object."""
    if not isinstance(data, Mapping):
        return None
    return HeroDraft(
        tldr=_text(data.get("tldr")),
        sentences=sentences_of(_text(data.get("brief"))),
        takeaways=_takeaways(data.get("keyTakeaways"), evidence),
        worth_it_for=_text(data.get("worthItFor")),
        skip_if=_text(data.get("skipIf")),
        seo_description=_text(data.get("seoDescription")),
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
        for clock in _CLOCK_RE.findall(text)
        if duration is not None and (parse_timestamp_to_seconds(clock) or 0) > duration
    ]
    return _CLOCK_RE.sub(" ", text), late


def unknown_numbers(text: str, evidence: HeroEvidence) -> list[str]:
    """Numbers (and clocks past the end) in ``text`` that the video does not contain."""
    rest, late = _clocks_inside(text, evidence.duration)
    return [*late, *sorted(numbers_in(rest) - evidence.numbers)]


def _capitalized_words(sentence: str) -> list[str]:
    """Capitalized words past the sentence's first word (hyphen parts apart, "'s" off)."""
    words = [m.group(0).rstrip(".'’-") for m in _WORD_RE.finditer(sentence)][1:]
    parts = [part for word in words for part in word.split("-") if part]
    stripped = [re.sub(r"['’]s$", "", part) for part in parts]
    return [w for w in stripped if w[:1].isupper() and w not in _ALWAYS_CAPITALIZED]


def outside_names(text: str, evidence: HeroEvidence) -> list[str]:
    """Capitalized words the evidence never contains — the outside-fact proxy."""
    names = [w for s in sentences_of(text) for w in _capitalized_words(s)]
    return sorted({w for w in names if not evidence.contains_word(w)})


def _names_a_tab(sentence: str, tabs: TabNames) -> bool:
    return tabs.named_in(sentence) or _PAGE_POINTER_RE.search(sentence) is not None


def tab_names(labels: Sequence[str]) -> TabNames:
    """The matcher 2.0's scrub uses, over the page's tab labels."""
    return TabNames.of([bare_label(label) for label in labels], ())


# ─── Checks ───


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f'"{value}"' for value in values)


def _not_in_video(what: str, values: Sequence[str]) -> str:
    return f"{what} {_quoted(values)}, which the video does not contain"


def _check_tldr(draft: HeroDraft, evidence: HeroEvidence, tabs: TabNames) -> list[Problem]:
    if not draft.tldr:
        return [Problem("tldr", "tldr is empty")]
    problems: list[Problem] = []
    if len(draft.tldr) > TLDR_MAX_CHARS:
        message = f"tldr has {len(draft.tldr)} characters — at most {TLDR_MAX_CHARS}"
        problems.append(Problem("tldr", message, "truncate"))
    if unknown := unknown_numbers(draft.tldr, evidence):
        problems.append(Problem("tldr", _not_in_video("tldr states", unknown)))
    if _names_a_tab(draft.tldr, tabs):
        problems.append(Problem("tldr", "tldr names a tab of the page — state the video's claim"))
    return problems


def _check_brief(draft: HeroDraft, evidence: HeroEvidence, tabs: TabNames) -> list[Problem]:
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
        if _names_a_tab(sentence, tabs):
            message = f"{where} names a tab of the page or points the reader at it"
            problems.append(Problem("brief", message, "drop_sentence", index))
    return problems


def _check_takeaway(index: int, takeaway: DraftTakeaway, evidence: HeroEvidence) -> list[Problem]:
    where = f"keyTakeaways[{index}]"
    if not takeaway.text:
        return [Problem("keyTakeaways", f"{where} has no text", index=index)]
    problems: list[Problem] = []
    if takeaway.timestamp is None:
        message = (
            f'{where} has no usable stamp — put the id of an <extracted> item in "ref", '
            'or the transcript\'s m:ss inside the video in "timestamp"'
        )
        problems.append(Problem("keyTakeaways", message, index=index))
    if unknown := unknown_numbers(takeaway.text, evidence):
        message = _not_in_video(f"{where} states", unknown)
        problems.append(Problem("keyTakeaways", message, index=index))
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


def _check_line(name: str, text: str, evidence: HeroEvidence, tabs: TabNames) -> list[Problem]:
    """worthItFor / skipIf (D18): ≤ 20 words, the content not the creator, nothing outside."""
    if not text:
        return [Problem(name, f"{name} is empty")]
    problems: list[Problem] = []
    if (words := len(text.split())) > LINE_MAX_WORDS:
        problems.append(Problem(name, f"{name} has {words} words — at most {LINE_MAX_WORDS}"))
    if judged := _CREATOR_JUDGEMENT_RE.search(text):
        message = f'{name} judges the creator ("{judged.group(0)}") — say what the video contains'
        problems.append(Problem(name, message))
    if names := outside_names(text, evidence):
        problems.append(Problem(name, _not_in_video(f"{name} names", names)))
    if unknown := unknown_numbers(text, evidence):
        problems.append(Problem(name, _not_in_video(f"{name} states", unknown)))
    if _names_a_tab(text, tabs):
        problems.append(Problem(name, f"{name} names a tab of the page"))
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
    sentences dropped, a long tldr cut; None when the tldr, brief or takeaways fail."""
    if any(p.field in CORE_FIELDS and p.repair == "fail" for p in problems):
        return None
    brief = _brief(draft, problems)
    takeaways = _takeaways_out(draft, takeaway_count)
    if brief is None or takeaways is None:
        return None
    failed = {p.field for p in problems if p.repair == "fail"}
    return HeroRewriteResult.model_validate(
        {
            "tldr": truncate_words(draft.tldr, TLDR_MAX_CHARS),
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
