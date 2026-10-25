"""Quiz enrichment — one demand-driven call that writes the video's self-check quiz.

pipeline-1min 1d.1 (brief Appendix B.6): ``quiz`` is the only enrichment left; a
planned ``quiz_arena`` tab and the ``quick_quiz`` attachment read it. The call
runs only when the plan can use it (:func:`needs_quiz`), reads the full
extraction, the run's ``<video_memory>`` block, the hero and the tab goals, and
never raises: ``None`` means no quiz — a quiz tab is then dropped at assembly
and a sparse tab falls back to its tip strip, as before.

output-quality 1.13 (R12 / D9): the question count follows the video's length
(:func:`quiz_count_bands`, domains.json ``quizPolicy``), every question carries the ``timestamp`` of
the sentence that answers it (a stamp past the video's end drops the question),
and a question the hero alone answers is trivia the prompt forbids.

output-quality 2.0 (G1-1): the stamp is in seconds — one that reads as the
minutes of its explanation's (m:ss) takes that clock, the explanation cites the
stamp's clock, and a bare second count in it renders as (m:ss)
(``assembly.quiz_stamps``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ...config import settings
from ...models.pipeline_types import EnrichmentData, PlanResult, QuizQuestion
from ...shared_config.domain_config import (
    data_source,
    effective_requirements,
    quiz_enrichment,
    quiz_policy,
)
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ...utils.llm_retry import call_llm_with_retry
from .assembly.attachments import SPARSE_THRESHOLD, can_host_quick_quiz
from .assembly.quiz_stamps import END_SLACK_S, reconcile_quiz_time
from .memory import UNKNOWN_LENGTH_SECONDS, format_clock
from .pipeline_helpers import sanitize_for_prompt
from .pipeline_timing import record_llm_failure
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...models.memory_types import MemoryResult
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "enrich_quiz.txt"

QUIZ_COMPONENT = "quiz_arena"
QUIZ_DATA_SOURCE = "enrichment.quiz"
QUIZ_STAGE = "enrichment"
QUIZ_MAX_TOKENS = 2048
# A stamp this far past the end is drift, not an invention — the same slack
# assembly gives a moment before dropping it (and the quiz clock check, quiz_stamps).
QUIZ_TIMESTAMP_TOLERANCE_S = END_SLACK_S
HERO_TAKEAWAYS_MAX = 7
EMPTY_HERO = "Not available"
# Where every question's timestamp must lie: "{timestamp_range}" renders this,
# followed by "(0:00–<length>)" when the length is known.
INSIDE_THE_VIDEO = "inside the video"
# Without a usable length the prompt names no length at all: a parenthetical
# that carries it ("({video_length})") goes, never "(unknown)" / "(0:00–unknown)".
_LENGTH_CLAUSE_RE = re.compile(r" ?\([^()]*\{video_length\}[^()]*\)")
# The whole stage, retry included, ends within the brief's 30 s. The quiz runs
# on the fast model (no cross-provider fallback), so it is two attempts: 14 s
# each plus the 1 s backoff fit inside the cap, and a slow first answer still
# leaves a full retry (measured quiz walls are ~6-10 s).
QUIZ_TOTAL_TIMEOUT_S = 30.0
QUIZ_ATTEMPT_TIMEOUT_S = 14.0
QUIZ_MAX_RETRIES = 1
MIN_QUIZ_QUESTIONS = 2
TAB_TEXT_MAX_CHARS = 300
EMPTY_VIDEO_MEMORY = "<video_memory>\nNot available\n</video_memory>"


# ─── Demand ───


def _is_nonempty(value: Any) -> bool:
    """Check if a single value has substantive content."""
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        return len(value) > 10
    if isinstance(value, dict):
        return any(_is_nonempty(v) for v in value.values())
    return value is not None


def _has_meaningful_data(extraction: dict) -> bool:
    """Check if extraction has any non-empty arrays or non-trivial string values."""
    return any(_is_nonempty(v) for v in extraction.values())


def quiz_allowed(domain: str, content_format: str | None = None) -> bool:
    """True when ``domain`` may carry a quiz (quizEnrichment + quiz_arena not forbidden)."""
    if domain not in quiz_enrichment()["quizDomains"]:
        return False
    return QUIZ_COMPONENT not in effective_requirements(domain, content_format)["forbidden"]


def _is_quiz_tab(tab: Mapping[str, Any]) -> bool:
    return tab.get("component") == QUIZ_COMPONENT or tab.get("dataSource") == QUIZ_DATA_SOURCE


def _is_sparse_strip_host(tab: Mapping[str, Any]) -> bool:
    """A tab able to host the quick_quiz strip that the plan expects to stay sparse.

    Assembly attaches the strip only to a list of at most ``SPARSE_THRESHOLD``
    items, so a dense host never shows it. ``expect`` 0 means "not counted"
    (fallback tabs) and is not treated as sparse: a strip-only quiz with no
    sparse host is paid for and seen nowhere.
    """
    if not can_host_quick_quiz(str(tab.get("component", ""))):
        return False
    brief = tab.get("brief")
    expect = brief.get("expect") if isinstance(brief, Mapping) else 0
    return isinstance(expect, int) and 0 < expect <= SPARSE_THRESHOLD


def needs_quiz(plan: PlanResult | None, content_format: str | None = None) -> bool:
    """Whether this run's plan can use a quiz — the enrichment demand gate (A7).

    A planned quiz tab always demands one (the plan has the final say). Without
    one, only a quick_quiz strip could show it: that needs a host the plan
    expects to be sparse (:func:`_is_sparse_strip_host`) and evidence that does
    not rule learning out (a missing key is "no opinion", not false).
    """
    if plan is None or not quiz_allowed(plan.primary_tag, content_format):
        return False
    tabs = [tab for tab in plan.tabs if isinstance(tab, dict)]
    if any(_is_quiz_tab(tab) for tab in tabs):
        return True
    if plan.evidence.get(quiz_policy()["requiresEvidence"]) is False:
        return False
    return any(_is_sparse_strip_host(tab) for tab in tabs)


# ─── Prompt ───


def _clip(value: object) -> str:
    return sanitize_for_prompt(str(value or ""), max_len=TAB_TEXT_MAX_CHARS).strip()


def _tab_line(tab: Mapping[str, Any]) -> str:
    """``- "label" (component): goal — what: …; expect ~N`` for one planned tab."""
    line = f'- "{_clip(tab.get("label") or tab.get("id"))}" ({_clip(tab.get("component"))})'
    line += f": {_clip(tab.get('goal'))}"
    brief = tab.get("brief")
    if not isinstance(brief, Mapping):
        return line
    what, expect = _clip(brief.get("what")), brief.get("expect")
    if what:
        line += f" — what: {what}"
    if isinstance(expect, int) and not isinstance(expect, bool) and expect > 0:
        line += f"; expect ~{expect}"
    return line


def render_tab_goals(tabs: Sequence[Mapping[str, Any]]) -> str:
    """The plan's tabs as the quiz writer reads them (the quiz tab's brief says what to cover)."""
    lines = [_tab_line(tab) for tab in tabs if isinstance(tab, Mapping)]
    return "\n".join(lines) or "Not specified"


def render_hero(title: str, memory: MemoryResult | None) -> str:
    """The page's hero as the quiz writer reads it: title, tldr, takeaways.

    The memory stage's hero is the preview the page shows until ``complete``
    (the synthesis / rewrite hero lands in the same late group as the quiz, so
    it is not available here). ``""`` when there is nothing to show.
    """
    heading = _clip(title)
    tldr = _clip(memory.tldr) if memory is not None else ""
    takeaways = [_clip(item) for item in memory.takeaways[:HERO_TAKEAWAYS_MAX]] if memory else []
    lines = [f"title: {heading}"] if heading else []
    if tldr:
        lines.append(f"tldr: {tldr}")
    lines.extend(f"- {item}" for item in takeaways if item)
    return "\n".join(lines)


def quiz_count_bands() -> tuple[tuple[float, int], ...]:
    """``(upper bound in seconds, questions)`` per band, shortest first — domains.json
    ``quizPolicy.questionsByDuration`` (content-model §9, D9: ≤ 15 min → 3, ≤ 45 → 5,
    longer → 8); an unbounded band's limit is infinity."""
    return tuple(
        (math.inf if band["maxMinutes"] is None else band["maxMinutes"] * 60, band["questions"])
        for band in quiz_policy()["questionsByDuration"]
    )


def _known_seconds(duration_s: float | None) -> float | None:
    """The video's length in seconds; None when it is missing, 0, negative or not finite."""
    seconds = float(duration_s or 0)
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def quiz_question_count(duration_s: float | None, cap: int) -> int:
    """How many questions a video of ``duration_s`` seconds gets, at most ``cap``.

    An unknown length (None, 0, NaN) is sized as memory sizes it
    (``UNKNOWN_LENGTH_SECONDS``: the middle band); a length past every bounded
    band takes the last band.
    """
    bands = quiz_count_bands()
    seconds = _known_seconds(duration_s) or UNKNOWN_LENGTH_SECONDS
    for limit, count in bands:
        if seconds <= limit:
            return min(count, cap)
    return min(bands[-1][1], cap)


def _with_video_length(template: str, duration_s: float | None) -> str:
    """``{video_length}`` as ``m:ss`` / ``h:mm:ss`` and ``{timestamp_range}`` as
    "inside the video (0:00–<length>)"; without a usable length the range is
    "inside the video" and every parenthetical carrying the length is dropped."""
    seconds = _known_seconds(duration_s)
    if seconds is None:
        unbounded = template.replace("{timestamp_range}", INSIDE_THE_VIDEO)
        return _LENGTH_CLAUSE_RE.sub("", unbounded)
    length = format_clock(int(seconds))
    return template.replace("{timestamp_range}", f"{INSIDE_THE_VIDEO} (0:00–{length})").replace(
        "{video_length}", length
    )


def _compact_json(data: Mapping[str, Any]) -> str:
    # The full extraction, not a prefix (A7): input tokens are cheap, a quiz
    # that only sees the first minutes misses most of the video.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def build_quiz_prompt(
    primary_tag: str,
    extraction_data: Mapping[str, Any],
    video_memory: str,
    tabs: Sequence[Mapping[str, Any]],
    *,
    duration_s: float | None = None,
    hero: str = "",
) -> str | None:
    """Render ``enrich_quiz.txt``; ``None`` when ``primary_tag`` has no flavor line.

    ``duration_s`` sets the question count and the range every timestamp must
    lie in; ``hero`` (:func:`render_hero`) is the text no question may be
    answerable from.
    """
    flavor = quiz_enrichment()["flavor"].get(primary_tag)
    if not flavor:
        return None
    body = (
        _with_video_length(load_prompt_text(PROMPT_PATH), duration_s)
        .replace("{flavor}", flavor)
        .replace("{question_count}", str(quiz_question_count(duration_s, _quiz_cap())))
        .replace("{hero}", hero or EMPTY_HERO)
        .replace("{tab_goals}", render_tab_goals(tabs))
        .replace("{video_memory}", video_memory or EMPTY_VIDEO_MEMORY)
        # Last, so text inside the extraction is never mistaken for a placeholder.
        .replace("{extraction_data}", _compact_json(extraction_data))
    )
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{body}"


# ─── Salvage ───


def _quiz_items(data: object) -> list[object]:
    items = data.get("quiz") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _past_the_end(question: QuizQuestion, duration_s: float | None) -> bool:
    """True when the question's stamp lies beyond the video (plus the drift slack)."""
    if question.timestamp is None or not duration_s or duration_s <= 0:
        return False
    return question.timestamp > duration_s + QUIZ_TIMESTAMP_TOLERANCE_S


def _in_seconds(question: QuizQuestion, duration_s: float | None) -> QuizQuestion:
    """The question with its stamp checked against its explanation's clock (a stamp that
    reads as minutes takes the clock) and no bare second count in the explanation (2.0 a)."""
    stamp, explanation = reconcile_quiz_time(question.timestamp, question.explanation, duration_s)
    if stamp == question.timestamp and explanation == question.explanation:
        return question
    return question.model_copy(update={"timestamp": stamp, "explanation": explanation})


def parse_quiz(data: object, cap: int, duration_s: float | None = None) -> list[QuizQuestion]:
    """Every valid question survives; a broken, repeated or past-the-end one is dropped alone.

    ``cap`` is the count the video gets (:func:`quiz_question_count`, at most
    the registry cap); ``duration_s`` bounds every timestamp. A stamp is read in
    seconds against the explanation's clock before the past-the-end check.
    """
    questions: list[QuizQuestion] = []
    seen: set[str] = set()
    dropped = 0
    for raw in _quiz_items(data):
        try:
            question = _in_seconds(QuizQuestion.model_validate(raw), duration_s)
        except ValidationError:
            dropped += 1
            continue
        key = question.question.casefold()
        if key in seen or _past_the_end(question, duration_s):
            dropped += 1
            continue
        seen.add(key)
        questions.append(question)
    if dropped:
        logger.info(
            "Quiz: dropped %d invalid, repeated or past-the-end question(s), kept %d",
            dropped,
            len(questions),
        )
    kept = questions[:cap]
    _log_stampless(kept)
    return kept


def _log_stampless(questions: Sequence[QuizQuestion]) -> None:
    """Count the kept questions without a timestamp — the prompt asks every one for
    its own, so a stampless question is a rule the model broke (no seek chip)."""
    stampless = sum(1 for question in questions if question.timestamp is None)
    if stampless:
        logger.warning(
            "Quiz: %d of %d kept question(s) carry no timestamp", stampless, len(questions)
        )


def _quiz_cap() -> int:
    spec = data_source(QUIZ_DATA_SOURCE)
    if spec is None:
        raise KeyError(f"{QUIZ_DATA_SOURCE} is missing from the dataSources registry")
    return spec["cap"]


# ─── Stage ───


async def _call_quiz_llm(llm_service: LLMService, prompt: str) -> str | None:
    """The quiz reply, bounded by the stage deadline.

    The deadline cancels whatever is in flight, so neither the provider nor the
    retry wrapper records that failure — it is recorded here, timed from the
    stage start, as attempt 0 (the stage deadline, not one provider attempt).
    """
    model = settings.get_stage_model(QUIZ_STAGE)
    started = time.monotonic()
    try:
        async with asyncio.timeout(QUIZ_TOTAL_TIMEOUT_S):
            return await call_llm_with_retry(
                llm_service,
                prompt,
                max_tokens=QUIZ_MAX_TOKENS,
                timeout=QUIZ_ATTEMPT_TIMEOUT_S,
                max_retries=QUIZ_MAX_RETRIES,
                stage_name=QUIZ_STAGE,
                json_mode=True,
                use_fast_model=True,
                model_override=model,
            )
    except TimeoutError as e:
        record_llm_failure(
            span=QUIZ_STAGE,
            model=model or llm_service.fast_model,
            error=e,
            start_monotonic=started,
            attempt=0,
        )
        raise


async def _run_quiz(
    llm_service: LLMService, primary_tag: str, prompt: str, duration_s: float | None
) -> EnrichmentData | None:
    raw = await _call_quiz_llm(llm_service, prompt)
    if not raw:
        logger.warning("Quiz LLM call failed after retries for %s", primary_tag)
        return None
    count = quiz_question_count(duration_s, _quiz_cap())
    questions = parse_quiz(parse_json_response(raw), count, duration_s)
    if len(questions) < MIN_QUIZ_QUESTIONS:
        logger.warning(
            "Quiz for %s kept %d valid question(s); skipping", primary_tag, len(questions)
        )
        return None
    return EnrichmentData(quiz=questions)


async def enrich_quiz(
    llm_service: LLMService,
    *,
    primary_tag: str,
    extraction_data: Mapping[str, Any],
    video_memory: str,
    tabs: Sequence[Mapping[str, Any]],
    duration_s: float | None = None,
    hero: str = "",
) -> EnrichmentData | None:
    """Write the quiz (2 to the video's count of questions); ``None`` on any failure.

    ``duration_s`` sizes the quiz and bounds its timestamps; ``hero`` is the
    page's hero text (:func:`render_hero`) no question may be answerable from.
    Bounded by ``QUIZ_TOTAL_TIMEOUT_S`` end to end and never raises — the quiz
    is optional and must not fail or stall the run.
    """
    try:
        prompt = build_quiz_prompt(
            primary_tag, extraction_data, video_memory, tabs, duration_s=duration_s, hero=hero
        )
        if prompt is None:
            logger.info("Quiz: no flavor line for domain %r; skipping", primary_tag)
            return None
        return await _run_quiz(llm_service, primary_tag, prompt, duration_s)
    except TimeoutError:
        logger.warning(
            "Quiz: no answer within %.0f s; continuing without a quiz", QUIZ_TOTAL_TIMEOUT_S
        )
        return None
    except Exception:  # noqa: BLE001 — the quiz is optional; a bug here must not fail the run
        logger.exception("Quiz stage crashed; continuing without a quiz")
        return None
