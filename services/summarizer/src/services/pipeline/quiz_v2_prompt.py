"""Quiz v2's prompts (output-quality 2.1b; split out and written per stretch at 3.0, G2-4;
per chunk at 4.0 b).

Data first — the task, the hero, the key ideas, the transcript — the rules last
(``quiz_v2.txt``). The quiz is written part by part (``quiz_v2_select.quiz_parts``): a
chunked run's extraction chunks, else the video's thirds. Each part that holds a target
idea (``quiz_v2_ideas.quiz_targets``: thesis / argument / number, each with its id) gets
one call (``quiz_v2_select.stretch_quotas`` sizes each, the counts adding up to the
video's count plus the spares), its task naming that part's count and range, its key
ideas the targets stamped there plus every unstamped one (its place is unknown; the call
quotes only its own lines, so the call whose lines hold it can test it), its transcript
only the lines within :data:`STRETCH_MARGIN_S` of the part, its hero without the
takeaways stamped in other parts — S15's third-stretch call copied the hero's 1:20, 27:20
and 44:01 takeaways as its questions (G3-3). Told "at least one question per stretch"
(p3i1), then given a count per stretch beside its grouped ideas (p3i2), Haiku still
wrote nothing past 37:30 of S03's 57:14; a call that sees only its part can only ask
about it. Without quotas the quiz is one call over the whole transcript, as before: an
unknown length, no stamped target, or — without chunks — a quiz of no more questions
than thirds, the 3-question band, ≤ 15 min (``quizPolicy``). There the whole transcript
is a few thousand words, ``select_quiz`` still ships one question per third from the one
call's count + spares, and three calls would ask a third holding one key idea for two or
three questions against the rule "one question per key idea" (p3 review A6).

Where the transcript comes from (output-quality 4.6e): with the split run's blocks
(``QuizV2Request.blocks``) a call reads it from the extraction's cache instead of the
lines above — block 1 + its chunk's head, then the job without a transcript
(``quiz_v2_blocks``): a chunked run's part call still reads only its part (its chunk),
a one-chunk run's third reads the whole head and is held to its stretch by its range
line and its targets. Without blocks the transcript stays inline, as described above.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...shared_config.domain_config import quiz_enrichment
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ..llm_messages import TextBlock, UserContent, text_block
from ..transcript.render import marker_seconds
from .enrichment import EMPTY_HERO, _quiz_cap, quiz_question_count
from .memory import format_clock
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text
from .quiz_v2_blocks import cached_prefix
from .quiz_v2_ideas import QuizTarget, quiz_targets, render_targets
from .quiz_v2_select import QUIZ_STRETCHES, Part, quiz_parts, stretch_of, stretch_quotas

if TYPE_CHECKING:
    from .hero_job import GroupBlocks

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "quiz_v2.txt"

# Extra candidates past the video's count: a dropped question must not take a
# short video's quiz (3) below the minimum, and the choice per part / past a
# length tell needs room (``quiz_v2_select``). Never shipped beyond the count. Four:
# split over three part calls, two spares left S03 a question short when 3 of its
# 10 quotes were not found (p3.0 harness, 2026-10-22).
SPARE_QUESTIONS = 4
# ≈ 80 K tokens (≈ 3 chars / token on S01's memory call): five hours of talk (inline only).
TRANSCRIPT_MAX_CHARS = 240_000
# Past the budget, the transcript lines this close to a key idea's stamp.
KEY_IDEA_WINDOW_S = 90
# A part's call reads the lines this far past its edges: a ~25 s line that starts just
# before the part still holds words said inside it.
STRETCH_MARGIN_S = 30
TITLE_MAX_CHARS = 200


@dataclass(frozen=True)
class QuizV2Request:
    """What the call reads; ``transcript`` is the run's ``[m:ss]``-marked prompt transcript,
    ``windows`` the split run's chunk windows (``quiz_v2_select.chunk_windows``; ``()`` →
    thirds), ``outline`` the starts of memory's outline sections, ``blocks`` the split
    run's cached prefix (None → the transcript inline)."""

    primary_tag: str
    title: str
    duration_s: float | None
    transcript: str
    extraction: Mapping[str, Any]
    hero: str = ""
    windows: tuple[Part, ...] = ()
    outline: tuple[int, ...] = ()
    blocks: GroupBlocks | None = None


@dataclass(frozen=True)
class QuizV2Prompt:
    """One quiz call: its prompt (the job after ``cached``, else the whole inline prompt),
    the number of questions it asks for, the part it reads (None = the whole video), the
    target ideas it shows and the cached blocks it reads first (``()`` = none)."""

    text: str
    asked: int
    part: Part | None = None
    targets: tuple[QuizTarget, ...] = ()
    cached: tuple[TextBlock, ...] = ()

    @property
    def content(self) -> UserContent:
        """What the call sends: the cached blocks and the job, or the one inline message."""
        return [*self.cached, text_block(self.text)] if self.cached else self.text


# ─── Transcript ───


def _near_a_key_idea(line: str, stamps: Sequence[int]) -> bool:
    seconds = marker_seconds(line[:12])
    return bool(seconds) and any(abs(seconds[0] - s) <= KEY_IDEA_WINDOW_S for s in stamps)


def fit_transcript(transcript: str, targets: Sequence[QuizTarget]) -> str:
    """The whole transcript within budget; past it, the lines around the target ideas."""
    if len(transcript) <= TRANSCRIPT_MAX_CHARS:
        return transcript
    stamps = [target.seconds for target in targets if target.seconds is not None]
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


def part_hero(hero: str, parts: Sequence[Part], part: int) -> str:
    """The hero without the takeaways stamped in another part: a part's call is never
    handed a clock it cannot quote (module docstring)."""
    lines = hero.splitlines()
    return "\n".join(
        line
        for line in lines
        if not line.startswith("- ")
        or all(stretch_of(seconds, parts) == part for seconds in marker_seconds(line))
    )


@dataclass(frozen=True)
class _Job:
    """One call's data: its task block, its hero, the targets it shows, its count."""

    task: str
    hero: str
    targets: tuple[QuizTarget, ...]
    asked: int


def _data_sections(job: _Job) -> tuple[str, ...]:
    return (
        ENGLISH_OUTPUT_DIRECTIVE,
        job.task,
        f"<hero>\n{job.hero or EMPTY_HERO}\n</hero>",
        f"<key_ideas>\n{render_targets(job.targets)}\n</key_ideas>",
    )


def _prompt_text(job: _Job, transcript: str) -> str:
    """The inline prompt: the data, the transcript, the rules last."""
    fitted = fit_transcript(transcript, job.targets)
    lines = f"<transcript>\n{sanitize_for_prompt(fitted, len(fitted))}\n</transcript>"
    return "\n\n".join((*_data_sections(job), lines, load_prompt_text(PROMPT_PATH).strip()))


def _job_text(job: _Job) -> str:
    """Block 3 of a call that reads the cache: the data and the rules — the transcript
    is in the cached head above."""
    return "\n\n".join((*_data_sections(job), load_prompt_text(PROMPT_PATH).strip()))


def _quiz_call(
    request: QuizV2Request, job: _Job, part: int | None = None, parts: Sequence[Part] = ()
) -> QuizV2Prompt:
    """One call (``part`` None = the whole video): after the cached prefix when the run
    has one for it (``quiz_v2_blocks``), else with its lines inline — the part's, or the
    whole transcript."""
    bounds = parts[part] if part is not None else None
    cached = cached_prefix(request.blocks, part, request.windows)
    if cached:
        return QuizV2Prompt(_job_text(job), job.asked, bounds, job.targets, cached)
    lines = stretch_transcript(request.transcript, bounds) if bounds else ""
    text = _prompt_text(job, lines or request.transcript)
    return QuizV2Prompt(text, job.asked, bounds, job.targets)


def _part_prompt(
    request: QuizV2Request,
    flavor: str,
    targets: Sequence[QuizTarget],
    parts: Sequence[Part],
    part: int,
    quota: int,
) -> QuizV2Prompt:
    """Part ``part``'s call: its count, its range, its targets (and the unstamped ones),
    its hero, its lines."""
    shown = tuple(t for t in targets if stretch_of(t.seconds, parts) in (part, None))
    task = _task_block(request, flavor, quota, parts[part])
    job = _Job(task, part_hero(request.hero, parts, part), shown, quota)
    return _quiz_call(request, job, part, parts)


def _quotas(request: QuizV2Request, targets: Sequence[QuizTarget], asked: int) -> list[int]:
    """Each part's count; ``[]`` → one call over the whole video (module docstring)."""
    count = quiz_question_count(request.duration_s, _quiz_cap())
    if not request.windows and count <= QUIZ_STRETCHES:
        return []
    stamps = [target.seconds for target in targets if target.seconds is not None]
    return stretch_quotas(stamps, quiz_parts(request.duration_s, request.windows), asked)


def build_quiz_v2_prompts(request: QuizV2Request) -> list[QuizV2Prompt]:
    """The call(s) that write the quiz — one per part with a quota, else one over the
    whole video (module docstring); ``[]`` when the domain has no flavor line or the run
    has no transcript to quote."""
    flavor = quiz_enrichment()["flavor"].get(request.primary_tag)
    if not flavor or not request.transcript.strip():
        return []
    targets = tuple(quiz_targets(request.primary_tag, request.extraction))
    asked = asked_count(request.duration_s)
    quotas = _quotas(request, targets, asked)
    if not quotas:
        task = _task_block(request, flavor, asked, None)
        return [_quiz_call(request, _Job(task, request.hero, targets, asked))]
    parts = quiz_parts(request.duration_s, request.windows)
    return [
        _part_prompt(request, flavor, targets, parts, part, quota)
        for part, quota in enumerate(quotas)
        if quota
    ]
