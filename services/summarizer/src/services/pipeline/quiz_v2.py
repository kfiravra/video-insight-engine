"""Quiz v2 (output-quality 2.1b, D9 / D13) — every answer proved by the speaker's words.

One call on ``LLM_MEMORY_MODEL`` (Haiku 5.5, stage ``quiz_v2``) reads the run's
marked transcript and the key ideas (:data:`KEY_IDEA_SOURCES`) and writes the quiz:
3 / 5 / 8 questions by length (``quizPolicy``, :func:`enrichment.quiz_question_count`)
plus :data:`SPARE_QUESTIONS` candidates, each with its answer's ``timestamp`` in
seconds and a ≤ 25-word evidence ``quote``. Every question then passes the 2.0
quote check (``quote_verify``) or is dropped:

- **verified** — the stamp is set from where the quote was found (segment-precise;
  the model's stamp is the marker of a ~25 s prompt line); a stamp that moves more
  than :data:`STAMP_AGREEMENT_S` is logged;
- **unverified** (an English source, not found) — dropped;
- **translated** (any other source, not found: the quote is an English rendering,
  D12) — kept only when its stamp lies inside the video; assembly flags the quote
  ``quoteTranslated`` and the page shows it as prose;
- no quote — dropped.

Fewer than :data:`MIN_QUIZ_V2_QUESTIONS` survivors → no quiz (no quiz tab). The
checked questions past the count travel on as ``EnrichmentData.spares``: once the
hero rewrite (written in parallel) lands, a question whose keyed option restates a
shipped takeaway goes and a spare takes its place (:func:`restates_takeaway`,
``phases.enrichment.settle_late_quiz`` — p2 review D7).

No prompt cache: memory sends its transcript without a breakpoint, so there is no
shared prefix to read, and a breakpoint on this request alone pays its 1.25x write
on every run to save only on the rare retry. On S01 (63 min) the whole request is
≈ 21 K tokens in + ≈ 2 K out ≈ $0.003, the transcript ≈ $0.0015 of it (Haiku 5.5
$0.10 / $0.50 per MTok); past :data:`TRANSCRIPT_MAX_CHARS` only the lines around the
key ideas go, which keeps the prompt in the ≤ 100 K-token price tier.

Never raises: ``None`` means no quiz, as for the flag-off ``enrich_quiz``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...models.pipeline_types import EnrichmentData, QuizQuestion
from ...shared_config.domain_config import data_source, quiz_enrichment
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ...utils.llm_retry import call_llm_with_retry
from ..transcript.render import marker_seconds
from .assembly.quiz_stamps import END_SLACK_S
from .enrichment import EMPTY_HERO, _quiz_cap, parse_quiz, quiz_question_count
from .memory import format_clock, truncate_words
from .pipeline_helpers import sanitize_for_prompt
from .pipeline_timing import record_llm_failure
from .prompt_builder import load_prompt_text
from .quote_verify import TRANSLATED, VERIFIED, QuoteIndex, verify_quote

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "quiz_v2.txt"

QUIZ_V2_STAGE = "quiz_v2"
# Extra candidates past the video's count: a dropped question must not take a
# short video's quiz (3) below the minimum. Never shipped beyond the count.
SPARE_QUESTIONS = 2
MIN_QUIZ_V2_QUESTIONS = 3
QUOTE_MAX_WORDS = 25
# A verified quote's location this far from the model's stamp moves the stamp (logged).
STAMP_AGREEMENT_S = 30
# Up to 10 questions with a quote each run ~2 K tokens; a cut-off JSON loses all.
QUIZ_V2_MAX_TOKENS = 4096
# Haiku 5.5 answered memory's 17.5 K-in / 1.3 K-out in ~7 s: 18 s per attempt,
# one retry after the 1 s backoff, the whole stage bounded.
QUIZ_V2_ATTEMPT_TIMEOUT_S = 18.0
QUIZ_V2_TOTAL_TIMEOUT_S = 37.0
QUIZ_V2_MAX_RETRIES = 1
# ≈ 80 K Haiku 5.5 tokens (≈ 3 chars / token on S01's memory call): five hours of talk.
TRANSCRIPT_MAX_CHARS = 240_000
# Past the budget, the transcript lines this close to a key idea's stamp.
KEY_IDEA_WINDOW_S = 90
KEY_IDEA_TEXT_MAX_CHARS = 300
TITLE_MAX_CHARS = 200
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
# An item's line: its statement, the reason / detail behind it, and its stamp.
_MAIN_KEYS = ("claim", "title", "name", "label", "phrase", "question", "text", "statement")
_DETAIL_KEYS = ("why", "detail", "description", "definition", "explanation", "answer")
_STAMP_KEYS = ("timestamp", "seconds", "startSeconds")

# A keyed option restates a takeaway when this share of its content words is in it —
# the moment-anchor rule's share (``assembly.sampling``) — and at least this many of
# them (all of a shorter option's: "About 1,000x").
RESTATES_SHARE = 0.6
RESTATES_MIN_WORDS = 3
# A word with its "1,000x" / "don't" joins; the takeaway's closing [m:ss] is no word.
_WORD_RE = re.compile(r"[^\W_]+(?:[.,'’][^\W_]+)*")
_MARKER_RE = re.compile(r"\[\d{1,2}(?::\d{2}){1,2}\]")
_SUFFIXES = ("ing", "ed", "s")
_STOPWORDS = frozenset(
    "the a an and of to in on for with by at from as is are was were be been it its this that "
    "these those his her their our your you we they he she him them not no or but so if than "
    "into over about after before more most very just can will would should could do does did "
    "have has had how what why when where which who while also all any some such only own same "
    "other new now out up down off".split()
)


@dataclass(frozen=True)
class QuizV2Request:
    """What the call reads; ``transcript`` is the run's ``[m:ss]``-marked prompt transcript."""

    primary_tag: str
    title: str
    duration_s: float | None
    transcript: str
    extraction: Mapping[str, Any]
    hero: str = ""


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


def _item_seconds(item: object) -> int | None:
    if not isinstance(item, Mapping):
        return None
    for key in _STAMP_KEYS:
        value = item.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return None


def _clip(text: str) -> str:
    return truncate_words(sanitize_for_prompt(text, len(text)), KEY_IDEA_TEXT_MAX_CHARS)


def _item_parts(item: object) -> tuple[str, str, str]:
    """(statement, detail, speaker) of a key-idea item; a plain string is its statement."""
    if isinstance(item, str):
        return item.strip(), "", ""
    if not isinstance(item, Mapping):
        return "", "", ""
    main = _first_text(item, _MAIN_KEYS)
    return main, _first_text(item, _DETAIL_KEYS, skip=main), _first_text(item, ("speaker",))


def _item_line(item: object) -> str:
    """``- [m:ss] (speaker) statement — detail``; ``""`` for an item with no statement."""
    main, detail, speaker = _item_parts(item)
    if not main:
        return ""
    text = _clip(main) + (f" — {_clip(detail)}" if detail else "")
    if speaker:
        text = f"({_clip(speaker)}) {text}"
    seconds = _item_seconds(item)
    return f"- [{format_clock(seconds)}] {text}" if seconds is not None else f"- {text}"


def render_key_ideas(items: Sequence[object]) -> str:
    lines = [line for line in (_item_line(item) for item in items) if line]
    return "\n".join(lines) or NO_KEY_IDEAS


# ─── Transcript ───


def _near_a_key_idea(line: str, stamps: Sequence[int]) -> bool:
    seconds = marker_seconds(line[:12])
    return bool(seconds) and any(abs(seconds[0] - s) <= KEY_IDEA_WINDOW_S for s in stamps)


def fit_transcript(transcript: str, items: Sequence[object]) -> str:
    """The whole transcript within budget; past it, the lines around the key ideas."""
    if len(transcript) <= TRANSCRIPT_MAX_CHARS:
        return transcript
    stamps = [s for s in (_item_seconds(item) for item in items) if s is not None]
    lines = [line for line in transcript.splitlines() if _near_a_key_idea(line, stamps)]
    logger.info(
        "Quiz v2: transcript of %d chars over budget; %d lines around %d key ideas",
        len(transcript), len(lines), len(stamps),
    )  # fmt: skip
    return "\n".join(lines or transcript.splitlines())[:TRANSCRIPT_MAX_CHARS]


# ─── Prompt ───


def asked_count(duration_s: float | None) -> int:
    """Questions the call is asked for: the video's count plus the spares."""
    return quiz_question_count(duration_s, _quiz_cap()) + SPARE_QUESTIONS


def _task_block(request: QuizV2Request, flavor: str) -> str:
    seconds = request.duration_s if request.duration_s and request.duration_s > 0 else None
    length = f"0:00–{format_clock(int(seconds))}" if seconds else "its whole length"
    title = sanitize_for_prompt(request.title or "Untitled", TITLE_MAX_CHARS)
    return (
        f"<task>\nVideo: {title}\nFocus: {flavor}\n"
        f"Questions to write: {asked_count(request.duration_s)}\n"
        f"Every timestamp lies inside the video ({length}).\n</task>"
    )


def build_quiz_v2_prompt(request: QuizV2Request) -> str | None:
    """Data first (task, hero, key ideas, transcript), the rules last; ``None`` when the
    domain has no flavor line or the run has no transcript to quote."""
    flavor = quiz_enrichment()["flavor"].get(request.primary_tag)
    if not flavor or not request.transcript.strip():
        return None
    items = key_idea_items(request.primary_tag, request.extraction)
    transcript = fit_transcript(request.transcript, items)
    return "\n\n".join(
        (
            ENGLISH_OUTPUT_DIRECTIVE,
            _task_block(request, flavor),
            f"<hero>\n{request.hero or EMPTY_HERO}\n</hero>",
            f"<key_ideas>\n{render_key_ideas(items)}\n</key_ideas>",
            f"<transcript>\n{sanitize_for_prompt(transcript, len(transcript))}\n</transcript>",
            load_prompt_text(PROMPT_PATH).strip(),
        )
    )


# ─── The quote check ───


def clip_quote(quote: str | None) -> str:
    """The quote cut to its first ``QUOTE_MAX_WORDS`` words (``…`` marks the cut)."""
    words = (quote or "").split()
    if len(words) <= QUOTE_MAX_WORDS:
        return " ".join(words)
    return f"{' '.join(words[:QUOTE_MAX_WORDS])}…"


def _inside(stamp: int | None, duration_s: float | None) -> bool:
    if stamp is None:
        return False
    return not duration_s or duration_s <= 0 or stamp <= duration_s + END_SLACK_S


def check_question(
    question: QuizQuestion, index: QuoteIndex, duration_s: float | None
) -> tuple[QuizQuestion | None, str]:
    """The question with its checked quote and stamp, or None — and the verdict's name."""
    quote = clip_quote(question.quote)
    if not quote:
        return None, "unquoted"
    stamp = question.timestamp
    verdict = verify_quote(quote, index, near_seconds=stamp)
    if verdict.status == VERIFIED and verdict.seconds is not None:
        moved = stamp is None or abs(stamp - verdict.seconds) > STAMP_AGREEMENT_S
        checked = question.model_copy(update={"quote": quote, "timestamp": verdict.seconds})
        return checked, "stamp_set" if moved else VERIFIED
    if verdict.status == TRANSLATED and _inside(stamp, duration_s):
        return question.model_copy(update={"quote": quote}), TRANSLATED
    return None, verdict.status


def keep_checked(
    questions: Sequence[QuizQuestion], index: QuoteIndex, duration_s: float | None
) -> list[QuizQuestion]:
    """The questions whose evidence passes, in order (module docstring)."""
    kept: list[QuizQuestion] = []
    outcomes: Counter[str] = Counter()
    for question in questions:
        checked, outcome = check_question(question, index, duration_s)
        outcomes[outcome if checked is not None else f"dropped_{outcome}"] += 1
        if checked is not None:
            kept.append(checked)
    logger.info(
        "Quiz v2: kept %d of %d after the quote check (%s)",
        len(kept), len(questions), ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
    )  # fmt: skip
    return kept


# ─── The shipped hero (p2 review D7) ───


def _stem(word: str) -> str:
    """A light stem, the same on both sides: "agents" / "agent", "shipping" / "ship"."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiou":
        return word[:-1]
    return word


def content_words(text: str) -> frozenset[str]:
    """The stemmed content words of ``text``: no stopword, no closing ``[m:ss]``; a word
    holding a digit counts at any length ("8", "1,000x")."""
    words = _WORD_RE.findall(_MARKER_RE.sub(" ", text.casefold().replace("’", "'")))
    return frozenset(
        _stem(word.removesuffix("'s"))
        for word in words
        if word not in _STOPWORDS and (len(word) >= 3 or any(c.isdigit() for c in word))
    )


def restates_takeaway(option: str, takeaway: str) -> bool:
    """True when the keyed ``option`` says what ``takeaway`` already says — the hero alone
    answers the question (:data:`RESTATES_SHARE` of its content words, at least
    :data:`RESTATES_MIN_WORDS` or all of a shorter option's)."""
    words = content_words(option)
    if not words:
        return False
    shared = len(words & content_words(takeaway))
    return shared >= min(RESTATES_MIN_WORDS, len(words)) and shared / len(words) >= RESTATES_SHARE


def keyed_option(question: Mapping[str, Any]) -> str:
    """The text of a stored question's correct option; ``""`` when it has none."""
    options, index = question.get("options"), question.get("correctIndex")
    if not isinstance(options, list) or not isinstance(index, int) or isinstance(index, bool):
        return ""
    return str(options[index]) if 0 <= index < len(options) else ""


def restates_the_hero(question: Mapping[str, Any], takeaways: Sequence[str]) -> bool:
    """True when the question's keyed option restates any of the shipped ``takeaways``."""
    option = keyed_option(question)
    return bool(option) and any(restates_takeaway(option, text) for text in takeaways)


# ─── Stage ───


async def _call_quiz_v2_llm(llm_service: LLMService, prompt: str) -> str | None:
    """The reply, bounded by the stage deadline (recorded here: the deadline cancels the
    attempt in flight, so neither the provider nor the retry wrapper sees it)."""
    model = settings.get_stage_model(QUIZ_V2_STAGE)
    started = time.monotonic()
    try:
        async with asyncio.timeout(QUIZ_V2_TOTAL_TIMEOUT_S):
            return await call_llm_with_retry(
                llm_service,
                prompt,
                max_tokens=QUIZ_V2_MAX_TOKENS,
                timeout=QUIZ_V2_ATTEMPT_TIMEOUT_S,
                max_retries=QUIZ_V2_MAX_RETRIES,
                stage_name=QUIZ_V2_STAGE,
                json_mode=True,
                use_fast_model=True,
                model_override=model,
            )
    except TimeoutError as e:
        record_llm_failure(
            span=QUIZ_V2_STAGE,
            model=model or llm_service.fast_model,
            error=e,
            start_monotonic=started,
            attempt=0,
        )
        raise


async def _run(
    llm_service: LLMService, request: QuizV2Request, prompt: str, index: QuoteIndex
) -> EnrichmentData | None:
    raw = await _call_quiz_v2_llm(llm_service, prompt)
    if not raw:
        logger.warning("Quiz v2 LLM call failed after retries for %s", request.primary_tag)
        return None
    candidates = parse_quiz(
        parse_json_response(raw), asked_count(request.duration_s), request.duration_s
    )
    count = quiz_question_count(request.duration_s, _quiz_cap())
    checked = keep_checked(candidates, index, request.duration_s)
    if len(checked[:count]) < MIN_QUIZ_V2_QUESTIONS:
        logger.warning(
            "Quiz v2 for %s kept %d checked question(s); no quiz", request.primary_tag, len(checked)
        )
        return None
    return EnrichmentData(quiz=checked[:count], spares=checked[count:])


async def enrich_quiz_v2(
    llm_service: LLMService, request: QuizV2Request, index: QuoteIndex | None
) -> EnrichmentData | None:
    """The checked quiz (3 to the video's count of questions); ``None`` on any failure.

    ``index`` is the run's quote index (``run_quote_index``): without one no quote can
    be checked, so no call is made. Bounded by ``QUIZ_V2_TOTAL_TIMEOUT_S``; never raises.
    """
    if index is None:
        logger.info("Quiz v2: no timed transcript to check quotes against; no quiz")
        return None
    try:
        prompt = build_quiz_v2_prompt(request)
        if prompt is None:
            logger.info("Quiz v2: no flavor line or no transcript for %r", request.primary_tag)
            return None
        return await _run(llm_service, request, prompt, index)
    except TimeoutError:
        logger.warning("Quiz v2: no answer within %.0f s; no quiz", QUIZ_V2_TOTAL_TIMEOUT_S)
        return None
    except Exception:  # noqa: BLE001 — the quiz is optional; a bug here must not fail the run
        logger.exception("Quiz v2 stage crashed; continuing without a quiz")
        return None
