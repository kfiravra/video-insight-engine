"""Quiz v2's prompts (output-quality 2.1b; split out and written per stretch at 3.0, G2-4).

Data first — the task, the hero, the key ideas, the transcript — the rules last
(``quiz_v2.txt``). With a known length and key ideas stamped in it, the quiz is written
stretch by stretch: one call per third of the video that holds a key idea
(``quiz_v2_select.stretch_quotas`` sizes each, the counts adding up to the video's count
plus the spares), its task naming that third's count and range, its key ideas only those
stamped there, its transcript only the lines within :data:`STRETCH_MARGIN_S` of the
third. Told "at least one question per stretch" (p3i1), then given a count per stretch
beside its grouped ideas (p3i2), Haiku still wrote nothing past 37:30 of S03's 57:14; a
call that sees only the last third can only ask about it. Without quotas (an unknown
length, no stamped key idea) the quiz is one call over the whole transcript, as before.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...shared_config.domain_config import quiz_enrichment
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ..transcript.render import marker_seconds
from .enrichment import EMPTY_HERO, _quiz_cap, quiz_question_count
from .memory import format_clock
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text
from .quiz_v2_ideas import item_seconds, key_idea_items, render_key_ideas
from .quiz_v2_select import stretch_bounds, stretch_of, stretch_quotas

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "quiz_v2.txt"

# Extra candidates past the video's count: a dropped question must not take a
# short video's quiz (3) below the minimum, and the choice per stretch / past a
# length tell needs room (``quiz_v2_select``). Never shipped beyond the count. Four:
# split over three stretch calls, two spares left S03 a question short when 3 of its
# 10 quotes were not found (p3.0 harness, 2026-10-22).
SPARE_QUESTIONS = 4
# ≈ 80 K Haiku 5.5 tokens (≈ 3 chars / token on S01's memory call): five hours of talk.
TRANSCRIPT_MAX_CHARS = 240_000
# Past the budget, the transcript lines this close to a key idea's stamp.
KEY_IDEA_WINDOW_S = 90
# A stretch call reads the lines this far past its edges: a ~25 s line that starts just
# before the stretch still holds words said inside it.
STRETCH_MARGIN_S = 30
TITLE_MAX_CHARS = 200


@dataclass(frozen=True)
class QuizV2Request:
    """What the call reads; ``transcript`` is the run's ``[m:ss]``-marked prompt transcript."""

    primary_tag: str
    title: str
    duration_s: float | None
    transcript: str
    extraction: Mapping[str, Any]
    hero: str = ""


@dataclass(frozen=True)
class QuizV2Prompt:
    """One quiz call: its prompt and the number of questions it asks for."""

    text: str
    asked: int


# ─── Transcript ───


def _near_a_key_idea(line: str, stamps: Sequence[int]) -> bool:
    seconds = marker_seconds(line[:12])
    return bool(seconds) and any(abs(seconds[0] - s) <= KEY_IDEA_WINDOW_S for s in stamps)


def fit_transcript(transcript: str, items: Sequence[object]) -> str:
    """The whole transcript within budget; past it, the lines around the key ideas."""
    if len(transcript) <= TRANSCRIPT_MAX_CHARS:
        return transcript
    stamps = [s for s in (item_seconds(item) for item in items) if s is not None]
    lines = [line for line in transcript.splitlines() if _near_a_key_idea(line, stamps)]
    logger.info(
        "Quiz v2: transcript of %d chars over budget; %d lines around %d key ideas",
        len(transcript), len(lines), len(stamps),
    )  # fmt: skip
    return "\n".join(lines or transcript.splitlines())[:TRANSCRIPT_MAX_CHARS]


def stretch_transcript(transcript: str, bounds: tuple[int, int]) -> str:
    """The lines whose marker lies within :data:`STRETCH_MARGIN_S` of the stretch; a line
    without a marker goes with the line before it."""
    start, end = bounds[0] - STRETCH_MARGIN_S, bounds[1] + STRETCH_MARGIN_S
    kept: list[str] = []
    inside = False
    for line in transcript.splitlines():
        seconds = marker_seconds(line[:12])
        if seconds:
            inside = start <= seconds[0] <= end
        if inside:
            kept.append(line)
    return "\n".join(kept)


# ─── Prompt ───


def asked_count(duration_s: float | None) -> int:
    """Questions the quiz asks for in all: the video's count plus the spares."""
    return quiz_question_count(duration_s, _quiz_cap()) + SPARE_QUESTIONS


def _range_line(duration_s: float | None, stretch: tuple[int, int] | None) -> str:
    seconds = duration_s if duration_s and duration_s > 0 else None
    length = f"0:00–{format_clock(int(seconds))}" if seconds else "its whole length"
    if stretch is None:
        return f"Every timestamp lies inside the video ({length})."
    span = f"{format_clock(stretch[0])}–{format_clock(stretch[1])}"
    return (
        f"Every timestamp lies inside this stretch of the video: {span} (the video runs {length})."
    )


def _task_block(
    request: QuizV2Request, flavor: str, asked: int, stretch: tuple[int, int] | None
) -> str:
    title = sanitize_for_prompt(request.title or "Untitled", TITLE_MAX_CHARS)
    return (
        f"<task>\nVideo: {title}\nFocus: {flavor}\nQuestions to write: {asked}\n"
        f"{_range_line(request.duration_s, stretch)}\n</task>"
    )


def _prompt_text(
    request: QuizV2Request, task: str, items: Sequence[object], transcript: str
) -> str:
    fitted = fit_transcript(transcript, items)
    return "\n\n".join(
        (
            ENGLISH_OUTPUT_DIRECTIVE,
            task,
            f"<hero>\n{request.hero or EMPTY_HERO}\n</hero>",
            f"<key_ideas>\n{render_key_ideas(items)}\n</key_ideas>",
            f"<transcript>\n{sanitize_for_prompt(fitted, len(fitted))}\n</transcript>",
            load_prompt_text(PROMPT_PATH).strip(),
        )
    )


def _stretch_prompt(
    request: QuizV2Request, flavor: str, items: Sequence[object], stretch: int, quota: int
) -> QuizV2Prompt:
    """Stretch ``stretch``'s call: its count, its range, its key ideas, its lines."""
    bounds = stretch_bounds(request.duration_s)[stretch]
    ideas = [
        item for item in items if stretch_of(item_seconds(item), request.duration_s) == stretch
    ]
    transcript = stretch_transcript(request.transcript, bounds) or request.transcript
    task = _task_block(request, flavor, quota, bounds)
    return QuizV2Prompt(_prompt_text(request, task, ideas, transcript), quota)


def build_quiz_v2_prompts(request: QuizV2Request) -> list[QuizV2Prompt]:
    """The call(s) that write the quiz — one per stretch with a quota, else one over the
    whole video (module docstring); ``[]`` when the domain has no flavor line or the run
    has no transcript to quote."""
    flavor = quiz_enrichment()["flavor"].get(request.primary_tag)
    if not flavor or not request.transcript.strip():
        return []
    items = key_idea_items(request.primary_tag, request.extraction)
    asked = asked_count(request.duration_s)
    stamps = [s for s in (item_seconds(item) for item in items) if s is not None]
    quotas = stretch_quotas(stamps, request.duration_s, asked)
    if not quotas:
        task = _task_block(request, flavor, asked, None)
        return [QuizV2Prompt(_prompt_text(request, task, items, request.transcript), asked)]
    return [
        _stretch_prompt(request, flavor, items, stretch, quota)
        for stretch, quota in enumerate(quotas)
        if quota
    ]
