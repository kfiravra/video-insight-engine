"""Quiz v2 (output-quality 2.1b, D9 / D13) — every answer proved by the speaker's words.

Calls on ``LLM_MEMORY_MODEL`` (Haiku 5.5, stage ``quiz_v2``) read the run's marked
transcript and the target key ideas (``quiz_v2_ideas.quiz_targets``: thesis / argument
/ number, each with an id) and write the quiz: 3 / 5 / 8 questions by length
(``quizPolicy``, :func:`enrichment.quiz_question_count`) plus ``SPARE_QUESTIONS``
candidates, each naming the idea it tests, with its answer's ``timestamp`` in seconds
and a ≤ 25-word evidence ``quote``. It is one call per part of the video that holds a
target — each extraction chunk of a chunked run, else each third past the 15-min band —
in parallel, each reading only its part (``quiz_v2_prompt``, G2-4, 4.0 b); otherwise one
call over the whole video.
Every question then passes the 2.0 quote check (``quote_verify``) or is dropped:

- **verified** — the stamp is set from where the quote was found (segment-precise;
  the model's stamp is the marker of a ~25 s prompt line); a stamp that moves more
  than :data:`STAMP_AGREEMENT_S` is logged;
- **unverified** (an English source, not found) — dropped;
- **translated** (any other source, not found: the quote is an English rendering,
  D12) — stamped at its idea's location, else kept only with a stamp inside its call's
  part (``quiz_v2_targets.translated_place``, G3-3); assembly flags the quote
  ``quoteTranslated`` and the page shows it as prose;
- no quote — dropped.

and must test a target idea: the one it names, else the one stamped within 90 s of it,
else it is dropped; a second question on one idea ranks behind every first one and
ships only when the firsts run out (``quiz_v2_targets``).

Each reply is read item by item when its envelope is broken (``quiz_v2_parse``, G2-1);
a reply in which no question parses is asked for once more — that call's only second
call. Every call shares one deadline, :data:`QUIZ_V2_TOTAL_TIMEOUT_S` from the stage's
start: a call that fails or runs out of time loses only its own part. The calls'
candidates merge by rank (every call's first, then every call's second …); of the
checked ones the video's count is chosen one part first, then one per untested outline
section, a key that gives itself away by its length last, and the shipped keys are
dealt evenly over the four positions (``quiz_v2_select``: G2-4, G2-5, G2-2).

Fewer than :data:`MIN_QUIZ_V2_QUESTIONS` shipped → no quiz (no quiz tab). The checked
questions not shipped travel on as ``EnrichmentData.spares``: once the hero rewrite
(written in parallel) lands, a question whose keyed option restates a shipped
takeaway goes and a spare takes its place (:func:`restates_takeaway`,
``phases.enrichment.settle_late_quiz`` — p2 review D7).

No prompt cache: memory sends its transcript without a breakpoint, so there is no
shared prefix to read, and the part calls share only the hero and the rules. On
S01 (63 min) one call was ≈ 21 K tokens in + ≈ 2 K out ≈ $0.003 (Haiku 5.5 $0.10 /
$0.50 per MTok); its part calls read each part once (± 30 s) and the shared part once
per call — two chunk calls ≈ 24 K tokens in all, three thirds ≈ 26 K; past
``TRANSCRIPT_MAX_CHARS`` only the lines around the target ideas go, which keeps every
prompt in the ≤ 100 K-token price tier.

Never raises: ``None`` means no quiz, as for the flag-off ``enrich_quiz``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import chain, zip_longest
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...models.pipeline_types import EnrichmentData
from ...utils.llm_retry import call_llm_with_retry
from .enrichment import _quiz_cap, parse_quiz, quiz_question_count
from .pipeline_timing import record_llm_failure
from .quiz_v2_parse import quiz_items
from .quiz_v2_prompt import QuizV2Prompt, QuizV2Request, build_quiz_v2_prompts
from .quiz_v2_select import quiz_parts, select_quiz, shuffle_quiz
from .quiz_v2_targets import (
    Candidate,
    aimed,
    candidates,
    ideas_first,
    repeats,
    translated_place,
)
from .quote_verify import TRANSLATED, VERIFIED, QuoteIndex, verify_quote

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

QUIZ_V2_STAGE = "quiz_v2"
MIN_QUIZ_V2_QUESTIONS = 3
QUOTE_MAX_WORDS = 25
# A verified quote's location this far from the model's stamp moves the stamp (logged).
STAMP_AGREEMENT_S = 30
# Up to 10 questions with a quote each run ~2 K tokens; a cut-off JSON loses all.
QUIZ_V2_MAX_TOKENS = 4096
# Haiku 5.5 answered memory's 17.5 K-in / 1.3 K-out in ~7 s: 18 s per attempt,
# one retry after the 1 s backoff, the whole stage (every call) bounded.
QUIZ_V2_ATTEMPT_TIMEOUT_S = 18.0
QUIZ_V2_TOTAL_TIMEOUT_S = 37.0
QUIZ_V2_MAX_RETRIES = 1


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


# ─── The quote check ───


def clip_quote(quote: str | None) -> str:
    """The quote cut to its first ``QUOTE_MAX_WORDS`` words (``…`` marks the cut)."""
    words = (quote or "").split()
    if len(words) <= QUOTE_MAX_WORDS:
        return " ".join(words)
    return f"{' '.join(words[:QUOTE_MAX_WORDS])}…"


def _quote_checked(
    candidate: Candidate, index: QuoteIndex, duration_s: float | None
) -> tuple[Candidate | None, str]:
    """The candidate with its checked quote and stamp, or None — and the verdict's name."""
    question = candidate.question
    quote = clip_quote(question.quote)
    if not quote:
        return None, "unquoted"
    stamp = question.timestamp
    verdict = verify_quote(quote, index, near_seconds=stamp)
    if verdict.status == VERIFIED and verdict.seconds is not None:
        moved = stamp is None or abs(stamp - verdict.seconds) > STAMP_AGREEMENT_S
        checked = question.model_copy(update={"quote": quote, "timestamp": verdict.seconds})
        return replace(candidate, question=checked), "stamp_set" if moved else VERIFIED
    if verdict.status != TRANSLATED:
        return None, verdict.status
    placed = translated_place(candidate, index, duration_s)
    if placed is None:
        return None, "translated_outside"
    return replace(placed, question=placed.question.model_copy(update={"quote": quote})), TRANSLATED


def check_question(
    candidate: Candidate, index: QuoteIndex, duration_s: float | None
) -> tuple[Candidate | None, str]:
    """The candidate checked and aimed at its target idea, or None — and the verdict's name."""
    checked, outcome = _quote_checked(candidate, index, duration_s)
    if checked is None:
        return None, outcome
    targeted = aimed(checked)
    return (targeted, outcome) if targeted is not None else (None, "untargeted")


def keep_checked(
    questions: Sequence[Candidate], index: QuoteIndex, duration_s: float | None
) -> list[Candidate]:
    """The questions whose evidence passes and whose idea is a target, in order (module
    docstring)."""
    kept: list[Candidate] = []
    outcomes: Counter[str] = Counter()
    for question in questions:
        checked, outcome = check_question(question, index, duration_s)
        outcomes[outcome if checked is not None else f"dropped_{outcome}"] += 1
        if checked is not None:
            kept.append(checked)
    matched = sum(1 for c in kept if c.target is not None and not c.named)
    ideas = {c.target.id for c in kept if c.target is not None}
    logger.info(
        "Quiz v2: kept %d of %d after the quote check (%s); %d matched to an idea by stamp, "
        "%d distinct ideas",
        len(kept), len(questions), ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
        matched, len(ideas),
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


async def _call_quiz_v2_llm(llm_service: LLMService, prompt: str, deadline: float) -> str | None:
    """The reply, bounded by the stage's shared ``deadline`` (loop time; recorded here: the
    deadline cancels the attempt in flight, so neither the provider nor the retry wrapper
    sees it)."""
    model = settings.get_stage_model(QUIZ_V2_STAGE)
    started = time.monotonic()
    try:
        async with asyncio.timeout_at(deadline):
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


def _candidates(raw: str, call: QuizV2Prompt, duration_s: float | None) -> list[Candidate]:
    """The reply's valid questions, each with the target idea it names (``quiz_v2_targets``)."""
    items = quiz_items(raw)
    questions = parse_quiz(items, call.asked, duration_s)
    return candidates(questions, items, call.part, call.targets)


async def _reply_candidates(
    llm_service: LLMService, call: QuizV2Prompt, duration_s: float | None, deadline: float
) -> list[Candidate] | None:
    """The reply's valid questions; ``None`` when the call failed. A reply in which no
    question parses is asked for once more (G2-1) — never a third time."""
    raw = await _call_quiz_v2_llm(llm_service, call.text, deadline)
    if not raw:
        return None
    candidates = _candidates(raw, call, duration_s)
    if candidates:
        return candidates
    logger.warning("Quiz v2: no question parsed from a %d-char reply; asking once more", len(raw))
    raw = await _call_quiz_v2_llm(llm_service, call.text, deadline)
    if not raw:
        return None
    candidates = _candidates(raw, call, duration_s)
    logger.info("Quiz v2: the second reply parsed %d question(s)", len(candidates))
    return candidates


async def _call_candidates(
    llm_service: LLMService, call: QuizV2Prompt, duration_s: float | None, deadline: float
) -> list[Candidate] | None:
    """One call's candidates; a call that runs out of time loses only its own part."""
    try:
        return await _reply_candidates(llm_service, call, duration_s, deadline)
    except TimeoutError:
        logger.warning(
            "Quiz v2: a %d-question call got no answer within %.0f s",
            call.asked, QUIZ_V2_TOTAL_TIMEOUT_S,
        )  # fmt: skip
        return None


def merge_by_rank(replies: Sequence[Sequence[Candidate]]) -> list[Candidate]:
    """Every reply's first candidate, then every reply's second, …; a question two calls
    both wrote is kept once. (A second question on one idea moves behind every first
    after the check, which settles each one's idea: ``quiz_v2_targets.ideas_first``.)"""
    merged: list[Candidate] = []
    seen: set[str] = set()
    for candidate in chain.from_iterable(zip_longest(*replies)):
        if candidate is None or candidate.question.question.casefold() in seen:
            continue
        seen.add(candidate.question.question.casefold())
        merged.append(candidate)
    return merged


async def _all_candidates(
    llm_service: LLMService, calls: Sequence[QuizV2Prompt], duration_s: float | None
) -> list[Candidate] | None:
    """Every call at once under one deadline, merged by rank; ``None`` when none answered."""
    deadline = asyncio.get_running_loop().time() + QUIZ_V2_TOTAL_TIMEOUT_S
    replies = await asyncio.gather(
        *(_call_candidates(llm_service, call, duration_s, deadline) for call in calls)
    )
    answered = [reply for reply in replies if reply is not None]
    if len(calls) > 1:
        counts = " / ".join("-" if reply is None else str(len(reply)) for reply in replies)
        logger.info(
            "Quiz v2: %d of %d part calls answered (candidates %s)",
            len(answered), len(calls), counts,
        )  # fmt: skip
    return merge_by_rank(answered) if answered else None


async def _run(
    llm_service: LLMService,
    request: QuizV2Request,
    calls: Sequence[QuizV2Prompt],
    index: QuoteIndex,
) -> EnrichmentData | None:
    merged = await _all_candidates(llm_service, calls, request.duration_s)
    if merged is None:
        logger.warning("Quiz v2 LLM call failed after retries for %s", request.primary_tag)
        return None
    checked = ideas_first(keep_checked(merged, index, request.duration_s))
    parts = quiz_parts(request.duration_s, request.windows)
    count = quiz_question_count(request.duration_s, _quiz_cap())
    questions = [c.question for c in checked]
    quiz, spares = select_quiz(questions, count, parts, request.outline, repeats(checked))
    if len(quiz) < MIN_QUIZ_V2_QUESTIONS:
        logger.warning(
            "Quiz v2 for %s kept %d checked question(s); no quiz", request.primary_tag, len(checked)
        )
        return None
    return EnrichmentData(quiz=shuffle_quiz(quiz), spares=shuffle_quiz(spares), parts=parts)


async def enrich_quiz_v2(
    llm_service: LLMService, request: QuizV2Request, index: QuoteIndex | None
) -> EnrichmentData | None:
    """The checked quiz (3 to the video's count of questions); ``None`` on any failure.

    ``index`` is the run's quote index (``run_quote_index``): without one no quote can
    be checked, so no call is made. Every call — one per part, or the one call, and
    each one's second call after a reply with no question in it — ends by
    ``QUIZ_V2_TOTAL_TIMEOUT_S`` after the stage starts; never raises.
    """
    if index is None:
        logger.info("Quiz v2: no timed transcript to check quotes against; no quiz")
        return None
    try:
        calls = build_quiz_v2_prompts(request)
        if not calls:
            logger.info("Quiz v2: no flavor line or no transcript for %r", request.primary_tag)
            return None
        return await _run(llm_service, request, calls, index)
    except Exception:  # noqa: BLE001 — the quiz is optional; a bug here must not fail the run
        logger.exception("Quiz v2 stage crashed; continuing without a quiz")
        return None
