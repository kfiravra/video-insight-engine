"""Reading the hero rewrite's prose (output-quality 2.1; D18, review p2 B1 / B5 / B6 / C1).

Pure text readers ``hero_validate`` checks the answer with — none of them knows
what the video contains:

- ``sentences_of``: a break needs an end mark and a capital or digit after it, and
  never comes after a title or a short form ("Dr. Ramos", "e.g. Rails", "vs. Go") or
  a single capital ("J. Smith") — a brief of 5 sentences naming "Dr. Ramos" is 5;
- ``names_in``: words that read as proper names (the outside-fact proxy): a
  Capitalized word past a sentence's first word, or any word of a run of two or
  more Capitalized words; never a token with a non-letter other than an inner
  hyphen / apostrophe ("Q&A", "C++", ".NET", "3D"), an all-caps acronym of ≤ 4
  letters ("AI", "CLI") or a format word (``_FORMAT_WORDS``);
- ``CREATOR_JUDGEMENT_RE``: a line judging the creator — a word that only judges a
  person, a verdict next to the person it judges, a verdict after "the host is …",
  or a verdict on HIS / HER / THEIR / the speaker's delivery ("strong delivery tips"
  stays: no person);
- ``HeroTabNames``: a planned tab's label names the tab when it is written in the
  label's own case with a capital past the sentence's first word ("The Key Ideas
  list…", "a Day by Day plan"), or anywhere in a sentence that speaks of tabs,
  sections or pages — never ordinary prose ("the talk's key ideas", "day by day");
- ``quoted_spans``: the spans in double quote marks of 3 or more words.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .assembly.tab_references import bare_label

_WHITESPACE_RE = re.compile(r"\s+")
# Short forms a period follows mid-sentence; a single capital ("J.") is one too.
_ABBREVIATIONS = ("Dr", "Mr", "Mrs", "Ms", "St", "Jr", "vs", "e.g", "i.e")
_NOT_AFTER_ABBREVIATION = "".join(rf"(?<!\b(?i:{re.escape(a)})\.)" for a in _ABBREVIATIONS)
_SENTENCE_RE = re.compile(
    rf"(?<=[.!?…]){_NOT_AFTER_ABBREVIATION}(?<!\b[A-Z]\.)\s+(?=[\"“'‘(]?[A-Z0-9])"
)
CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}(?::\d{2}){1,2}(?![\d:])")
# A word a name can be: letters, joined only by inner hyphens / apostrophes.
_NAME_WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_CLITIC_RE = re.compile(r"['’](?:s|m|d|ll|ve|re)$", re.I)
OPENERS = "\"“‘'(["
_CLOSERS = ".,;:!?\"”’')]…"
# Punctuation after a word that ends a run of names ("Rails, Kodak" is two names).
_RUN_BREAKS = (",", ";", ":", ")", "—")
_ACRONYM_MAX_LETTERS = 4
# Format / genre words, never a fact from outside the video however they are cased
# (the plurals the acronym rule misses: "FAQs" is not all caps).
_FORMAT_WORDS = frozenset({"q&a", "faq", "faqs", "ama", "amas", "diy", "asmr", "tl;dr"})

# Pointing the reader at the page, whatever the tab is called.
PAGE_POINTER_RE = re.compile(r"\bcheck\s+(?:\w+\s+)?out\b|\b(?:this|the)\s+page\b", re.I)
# A sentence that speaks of the page's parts: any planned label in it names a tab.
_PAGE_PART_RE = re.compile(r"\b(?:tabs?|sections?|pages?)\b", re.I)

# Heuristic (module docstring). Content words that also name ideas ("boring
# technology", "clear steps", "strong delivery tips") need a person to judge.
_VERDICT = (
    r"(?:great|good|bad|poor|weak|strong|excellent|engaging|entertaining|funny|boring|dull"
    r"|charming|brilliant|terrible|awkward|annoying|tedious|captivating|compelling|hilarious"
    r"|articulate|preachy|nervous|smug|arrogant|charismatic|likeable|likable|monotone"
    r"|hard\s+to\s+(?:follow|watch|listen\s+to))"
)
_PERSON = r"(?:host|presenter|speaker|creator|narrator|youtuber)"
_PERSON_ROLE = (
    r"(?:presenter|speaker|host|creator|narrator|teacher|storyteller|youtuber|personality)"
)
_COPULA = r"(?:is|are|was|were|seems|seemed|gets|can\s+be|comes\s+across(?:\s+as)?)"
_OWNER = rf"(?:his|her|their|the\s+{_PERSON}['’]s)"
CREATOR_JUDGEMENT_RE = re.compile(
    r"\b(?:rambl\w*|annoying|annoys|insufferable|smug|arrogant|condescending|cringe\w*"
    r"|clickbait\w*|self-promot\w*|monotone|charismatic|likeable|likable)\b"
    rf"|\b{_VERDICT}\s+{_PERSON_ROLE}\b"
    rf"|\b{_PERSON}s?\s+{_COPULA}\s+(?:\w+\s+){{0,2}}?{_VERDICT}\b"
    rf"|\b{_OWNER}\s+(?:\w+\s+)?{_VERDICT}\s+(?:delivery|presentation)\b"
    rf"|\b{_OWNER}\s+(?:delivery|presentation)\s+{_COPULA}\s+(?:\w+\s+){{0,2}}?{_VERDICT}\b",
    re.I,
)

QUOTE_MIN_WORDS = 3
_QUOTED_RE = re.compile(r"[\"“]([^\"“”]+)[\"”]")


def collapse(raw: object) -> str:
    """``raw`` with whitespace collapsed; "" for anything but a string."""
    return _WHITESPACE_RE.sub(" ", raw).strip() if isinstance(raw, str) else ""


def sentences_of(text: str) -> tuple[str, ...]:
    """The sentences of ``text`` (module docstring); joined by one space they are ``text``."""
    return tuple(s for s in _SENTENCE_RE.split(collapse(text)) if s)


def quoted_spans(text: str) -> list[re.Match[str]]:
    """The double-quoted spans of ``text`` of at least ``QUOTE_MIN_WORDS`` words; group 1
    is the quoted words."""
    return [m for m in _QUOTED_RE.finditer(text) if len(m.group(1).split()) >= QUOTE_MIN_WORDS]


# ─── Names ───


def _capitalized_runs(sentence: str) -> list[tuple[int, list[str]]]:
    """Runs of consecutive Capitalized tokens: (the run's first token's index, tokens)."""
    runs: list[tuple[int, list[str]]] = []
    open_run = False
    for index, raw in enumerate(sentence.split()):
        token = raw.lstrip(OPENERS).rstrip(_CLOSERS)
        if not token[:1].isupper():
            open_run = False
            continue
        if open_run:
            runs[-1][1].append(token)
        else:
            runs.append((index, [token]))
        open_run = not raw.endswith(_RUN_BREAKS)
    return runs


def _is_acronym(word: str) -> bool:
    return word.isupper() and len(word) <= _ACRONYM_MAX_LETTERS


def _name_parts(token: str) -> list[str]:
    """The words of a name token that can name something ("Netflix" of "Netflix-style";
    none of "Q&A", "C++", "3D", "API", "FAQs")."""
    word = _CLITIC_RE.sub("", token)
    if word.casefold() in _FORMAT_WORDS or not _NAME_WORD_RE.fullmatch(word):
        return []
    parts = [part for part in word.split("-") if part]
    return [part for part in parts if part[:1].isupper() and not _is_acronym(part)]


def names_in(sentence: str) -> list[str]:
    """Words that read as names: Capitalized past the first word, or in a run of 2+."""
    runs = [run for start, run in _capitalized_runs(sentence) if start > 0 or len(run) > 1]
    return [part for run in runs for token in run for part in _name_parts(token)]


# ─── Tab names ───


def _label_pattern(label: str, flags: int = 0) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in label.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", flags)


def _capital_past_first_word(sentence: str, match: re.Match[str]) -> bool:
    """A word of ``match`` starts with a capital and is not the sentence's first word."""
    first_word = len(sentence) - len(sentence.lstrip(f" {OPENERS}"))
    return any(
        word.group(0)[:1].isupper() and match.start() + word.start() > first_word
        for word in re.finditer(r"\S+", match.group(0))
    )


@dataclass(frozen=True)
class _Label:
    name: str
    loose: re.Pattern[str]  # any case, whole words
    exact: re.Pattern[str]  # the label's own case


@dataclass(frozen=True)
class HeroTabNames:
    """The page's planned tab labels, as a sentence of the hero may name them."""

    labels: tuple[_Label, ...]

    def named(self, sentence: str) -> str | None:
        """The label ``sentence`` names as a tab (module docstring), else None."""
        about_page = _PAGE_PART_RE.search(sentence) is not None
        for label in self.labels:
            if about_page and label.loose.search(sentence):
                return label.name
            hits = label.exact.finditer(sentence)
            if any(_capital_past_first_word(sentence, hit) for hit in hits):
                return label.name
        return None

    def named_in(self, sentence: str) -> bool:
        return self.named(sentence) is not None


def tab_names(labels: Sequence[str]) -> HeroTabNames:
    """The matcher over the page's tab labels (emoji out)."""
    names = [name for name in (bare_label(label) for label in labels) if name]
    return HeroTabNames(
        tuple(_Label(name, _label_pattern(name, re.I), _label_pattern(name)) for name in names)
    )
