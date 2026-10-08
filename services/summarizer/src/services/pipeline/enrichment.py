"""Quiz enrichment — one demand-driven call that writes the video's self-check quiz.

pipeline-1min 1d.1 (brief Appendix B.6): ``quiz`` is the only enrichment left; a
planned ``quiz_arena`` tab and the ``quick_quiz`` attachment read it. The call
runs only when the plan can use it (:func:`needs_quiz`), reads the full
extraction, the run's ``<video_memory>`` block and the tab goals, and never
raises: ``None`` means no quiz — a quiz tab is then dropped at assembly and a
sparse tab falls back to its tip strip, as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from .assembly.attachments import can_host_quick_quiz
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "enrich_quiz.txt"

QUIZ_COMPONENT = "quiz_arena"
QUIZ_DATA_SOURCE = "enrichment.quiz"
QUIZ_STAGE = "enrichment"
QUIZ_MAX_TOKENS = 2048
# The whole stage, retry included, ends within the brief's 30 s, so the quiz
# never holds the tabs longer than that; one attempt alone may use 25 s.
QUIZ_TOTAL_TIMEOUT_S = 30.0
QUIZ_ATTEMPT_TIMEOUT_S = 25.0
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


def needs_quiz(plan: PlanResult | None, content_format: str | None = None) -> bool:
    """Whether this run's plan can use a quiz — the enrichment demand gate (A7).

    A planned quiz tab always demands one (the plan has the final say). Without
    one, only a quick_quiz strip could show it: that needs a tab able to host
    the strip and evidence that does not rule learning out (a missing key is
    "no opinion", not false).
    """
    if plan is None or not quiz_allowed(plan.primary_tag, content_format):
        return False
    tabs = [tab for tab in plan.tabs if isinstance(tab, dict)]
    if any(_is_quiz_tab(tab) for tab in tabs):
        return True
    if plan.evidence.get(quiz_policy()["requiresEvidence"]) is False:
        return False
    return any(can_host_quick_quiz(str(tab.get("component", ""))) for tab in tabs)


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
    """The plan's tabs as the quiz writer reads them (the quiz tab's brief sets the count)."""
    lines = [_tab_line(tab) for tab in tabs if isinstance(tab, Mapping)]
    return "\n".join(lines) or "Not specified"


def _compact_json(data: Mapping[str, Any]) -> str:
    # The full extraction, not a prefix (A7): input tokens are cheap, a quiz
    # that only sees the first minutes misses most of the video.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def build_quiz_prompt(
    primary_tag: str,
    extraction_data: Mapping[str, Any],
    video_memory: str,
    tabs: Sequence[Mapping[str, Any]],
) -> str | None:
    """Render ``enrich_quiz.txt``; ``None`` when ``primary_tag`` has no flavor line."""
    flavor = quiz_enrichment()["flavor"].get(primary_tag)
    if not flavor:
        return None
    body = (
        load_prompt_text(PROMPT_PATH)
        .replace("{flavor}", flavor)
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


def parse_quiz(data: object, cap: int) -> list[QuizQuestion]:
    """Every valid question survives; each broken or repeated one is dropped alone."""
    questions: list[QuizQuestion] = []
    seen: set[str] = set()
    dropped = 0
    for raw in _quiz_items(data):
        try:
            question = QuizQuestion.model_validate(raw)
        except ValidationError:
            dropped += 1
            continue
        key = question.question.casefold()
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        questions.append(question)
    if dropped:
        logger.info(
            "Quiz: dropped %d invalid or repeated question(s), kept %d", dropped, len(questions)
        )
    return questions[:cap]


def _quiz_cap() -> int:
    spec = data_source(QUIZ_DATA_SOURCE)
    if spec is None:
        raise KeyError(f"{QUIZ_DATA_SOURCE} is missing from the dataSources registry")
    return spec["cap"]


# ─── Stage ───


async def _run_quiz(
    llm_service: LLMService, primary_tag: str, prompt: str
) -> EnrichmentData | None:
    async with asyncio.timeout(QUIZ_TOTAL_TIMEOUT_S):
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=QUIZ_MAX_TOKENS,
            timeout=QUIZ_ATTEMPT_TIMEOUT_S,
            max_retries=QUIZ_MAX_RETRIES,
            stage_name=QUIZ_STAGE,
            json_mode=True,
            use_fast_model=True,
            model_override=settings.get_stage_model(QUIZ_STAGE),
        )
    if not raw:
        logger.warning("Quiz LLM call failed after retries for %s", primary_tag)
        return None
    questions = parse_quiz(parse_json_response(raw), _quiz_cap())
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
) -> EnrichmentData | None:
    """Write the quiz (2 to the registry cap of questions); ``None`` on any failure.

    Bounded by ``QUIZ_TOTAL_TIMEOUT_S`` end to end and never raises — the quiz
    is optional and must not fail or stall the run.
    """
    try:
        prompt = build_quiz_prompt(primary_tag, extraction_data, video_memory, tabs)
        if prompt is None:
            logger.info("Quiz: no flavor line for domain %r; skipping", primary_tag)
            return None
        return await _run_quiz(llm_service, primary_tag, prompt)
    except TimeoutError:
        logger.warning(
            "Quiz: no answer within %.0f s; continuing without a quiz", QUIZ_TOTAL_TIMEOUT_S
        )
        return None
    except Exception:  # noqa: BLE001 — the quiz is optional; a bug here must not fail the run
        logger.exception("Quiz stage crashed; continuing without a quiz")
        return None
