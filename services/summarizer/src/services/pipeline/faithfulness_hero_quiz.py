"""The judge's hero + quiz sample (output-quality 2.4, D13 / R10).

From phase 2 on the faithfulness judge also reads what a viewer trusts first on the
page: the hero's key takeaways and the quiz. Two calls on the judge's model
(``LLM_JUDGE_MODEL``, gpt-6-luna; stage ``faithfulness``, ledger feature
``summarize:faithfulness``), in the background once the doc is assembled
(``routes/pipeline_faithfulness._launch_hero_quiz_check``):

- **hero** — every takeaway in ``meta.keyTakeaways`` (3 / 5 / 7, each closing with
  the ``[m:ss]`` marker of the moment it cites; at most :data:`HERO_MAX_ITEMS`) is
  judged against the transcript around its marker — :data:`WINDOW_BEFORE_S` before
  to :data:`WINDOW_AFTER_S` after — and what the screen shows there. A takeaway the
  video states only elsewhere reads as ungrounded: the stamp is part of the claim
  (its ▶ chip seeks there). A takeaway without a marker is not judged; the score's
  comment counts it.
- **quiz** — :data:`QUIZ_SAMPLE` stamped questions of the shipped ``quiz_arena``
  tab, drawn deterministically by ``youtube_id``: does the transcript at the
  question's ``timestamp`` give the keyed option as the answer?

Each call logs its own Langfuse score, ``faithfulness_hero`` / ``faithfulness_quiz``
(grounded ÷ judged, comment "N/M … grounded"), so the extraction sample's
``faithfulness`` score keeps its meaning on the dashboards. A doc without a stamped
takeaway (or quiz question) skips that call; so does a run where the hero rewrite
did not write the hero (``HERO_REWRITE_ENABLED`` off, or the rewrite failed — the page
then shows memory's takeaways) or where quiz v2 did not write the quiz
(``QUIZ_V2_ENABLED`` off): the flags stay rollback levers that remove their calls.
``LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0`` skips both, like the extraction sample. One
attempt per call: the score is informational, and the cost bound below holds per run.

**Cost, bounded by construction** (gpt-6-luna $0.10 in / $0.50 out per MTok): every
piece of a prompt is capped (claim, question, option, speech window, screen lines,
item counts), so a hero prompt stays under :data:`HERO_PROMPT_MAX_CHARS` and a quiz
prompt under :data:`QUIZ_PROMPT_MAX_CHARS`; with the output caps the two calls cost
at most ≈ $0.0021 even at 2 characters a token (≈ $0.0013 at English's ~4). A
5-takeaway video sends ≈ 4–5 K tokens: ≈ $0.0006.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

from src.config import settings
from src.models.pipeline_types import MAX_QUIZ_OPTIONS
from src.services.llm import LLMService
from src.services.observability import log_score
from src.services.pipeline.faithfulness import (
    _JUDGE_TIMEOUT_SECONDS,
    JUDGE_STAGE,
    FaithfulnessReport,
)
from src.services.pipeline.pipeline_helpers import sanitize_for_prompt
from src.services.pipeline.visual_annotations import AnnotationEntry, annotation_entries
from src.services.transcript.render import (
    MARKER_PATTERN,
    _segment_start,
    format_marker,
    marker_seconds,
    render_transcript,
)
from src.utils.json_parsing import parse_json_response
from src.utils.llm_retry import call_llm_with_retry

logger = logging.getLogger(__name__)

SCORE_HERO = "faithfulness_hero"
SCORE_QUIZ = "faithfulness_quiz"
QUIZ_COMPONENT = "quiz_arena"
HERO_MAX_ITEMS = 7
QUIZ_SAMPLE = 2
# The evidence window around a stamp: a stamp marks where a point starts, so most
# of the window follows it (gate 2 holds takeaway stamps to ± 30 s of the claim).
WINDOW_BEFORE_S = 30
WINDOW_AFTER_S = 90
_CLAIM_MAX_CHARS = 400
_OPTION_MAX_CHARS = 200
# ≈ 2 minutes of speech at 150–200 words a minute.
_SPEECH_MAX_CHARS = 2_000
_SCREEN_MAX_CHARS = 400
_NO_SPEECH = "(no transcript text in this window)"
# The prompt-size ceilings the caps above guarantee (tests build oversized docs).
HERO_PROMPT_MAX_CHARS = 22_000
QUIZ_PROMPT_MAX_CHARS = 10_000
# Output: ≈ 60 tokens a verdict with its ≤ 25-word evidence span, plus slack.
HERO_MAX_TOKENS = 700
QUIZ_MAX_TOKENS = 300

_REPLY_FORMAT = (
    "Reply in this exact JSON format, one verdict per item id:\n"
    '{"verdicts": [{"id": 1, "grounded": true|false, '
    '"evidence": "quoted span ≤25 words or empty"}]}\n\n'
)
_EVIDENCE_NOTE = (
    "Its EVIDENCE is the transcript around that moment ([m:ss] markers), then what "
    "the screen shows there when known. The evidence may be in another language "
    "than the item: judge the meaning.\n\n"
)
_HERO_PROMPT = (
    "You are a fact-checker. Each item below is a key takeaway from a video's "
    "summary page, with the moment of the video it cites. " + _EVIDENCE_NOTE + "For "
    "each item decide whether its EVIDENCE supports the CLAIM. Be strict: "
    '"grounded" is true only if the evidence states the claim. Paraphrases are '
    "fine; an inference, a number, a name or a stance the evidence does not give "
    "is not.\n\n" + _REPLY_FORMAT + "{items}\n"
)
_QUIZ_PROMPT = (
    "You are checking a video's quiz. Each item below is a multiple-choice question, "
    "the option the quiz marks correct, and the moment of the video the quiz cites "
    "for the answer. " + _EVIDENCE_NOTE + "For each item decide whether the EVIDENCE "
    'gives the MARKED ANSWER as the answer to the QUESTION. Be strict: "grounded" '
    "is true only if the evidence states that answer; it is false when the evidence "
    "gives another option, gives no answer, or fits another option as well.\n\n"
    + _REPLY_FORMAT
    + "{items}\n"
)


# ─── Evidence windows ───────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceSource:
    """The run's timed transcript and on-screen entries, windowed around a stamp."""

    segments: tuple[Mapping[str, Any], ...]
    screen: tuple[AnnotationEntry, ...] = ()
    source_language: str | None = None

    @classmethod
    def build(
        cls,
        segments: Sequence[Any],
        visual_annotations: str = "",
        source_language: str | None = None,
    ) -> EvidenceSource:
        """``segments``: ``{text, start|startMs}`` dicts in time order (the prompt
        segments); ``visual_annotations``: the rendered block (``""`` = none)."""
        timed = tuple(s for s in segments if isinstance(s, Mapping))
        return cls(timed, tuple(annotation_entries(visual_annotations)), source_language)

    def window(self, seconds: int) -> str:
        """The speech from ``seconds - WINDOW_BEFORE_S`` to ``seconds + WINDOW_AFTER_S``
        as ``[m:ss]`` lines, then the screen entries shown in the same span."""
        low, high = seconds - WINDOW_BEFORE_S, seconds + WINDOW_AFTER_S
        spoken = [s for s in self.segments if low <= _segment_start(s) < high]
        speech = render_transcript(spoken, source_language=self.source_language)
        shown = "\n".join(entry.text for entry in self.screen if low <= entry.seconds < high)
        parts = [sanitize_for_prompt(speech, _SPEECH_MAX_CHARS) or _NO_SPEECH]
        if shown:
            parts.append(f"ON SCREEN:\n{sanitize_for_prompt(shown, _SCREEN_MAX_CHARS)}")
        return "\n".join(parts)


# ─── Samples ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class JudgedItem:
    """One sampled item: its rendered body (claim or question), the second it cites,
    and the evidence around that second."""

    body: str
    seconds: int
    evidence: str

    def render(self, item_id: int) -> str:
        cites = format_marker(self.seconds)
        return (
            f'<item id="{item_id}" cites="{cites}">\n{self.body}\n'
            f"EVIDENCE:\n{self.evidence}\n</item>"
        )


@dataclass(frozen=True)
class Sample:
    """One judge call's items; ``skipped`` = entries that could not be judged
    (a takeaway without a marker, a question without a stamp or a valid key)."""

    score_name: str
    items: tuple[JudgedItem, ...]
    skipped: int = 0

    @property
    def is_hero(self) -> bool:
        return self.score_name == SCORE_HERO

    @property
    def max_tokens(self) -> int:
        return HERO_MAX_TOKENS if self.is_hero else QUIZ_MAX_TOKENS

    def prompt(self) -> str:
        template = _HERO_PROMPT if self.is_hero else _QUIZ_PROMPT
        rendered = "\n".join(item.render(i) for i, item in enumerate(self.items, start=1))
        return template.replace("{items}", rendered)


@dataclass(frozen=True)
class HeroQuizSamples:
    """Both calls' samples for one run (either may be empty)."""

    hero: Sample
    quiz: Sample

    def calls(self) -> list[Sample]:
        return [sample for sample in (self.hero, self.quiz) if sample.items]


def _is_seconds(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _hero_claim(raw: object) -> tuple[str, int] | None:
    """A takeaway's words and the second its last marker cites; None without one."""
    if not isinstance(raw, str):
        return None
    stamps = marker_seconds(raw)
    claim = MARKER_PATTERN.sub("", raw).strip()
    if not stamps or not claim:
        return None
    return claim, stamps[-1]


def hero_sample(meta: Mapping[str, Any], evidence: EvidenceSource) -> Sample:
    """Every takeaway (≤ ``HERO_MAX_ITEMS``) that closes with a marker."""
    takeaways = meta.get("keyTakeaways")
    raw = takeaways[:HERO_MAX_ITEMS] if isinstance(takeaways, list) else []
    stamped = [claim for claim in map(_hero_claim, raw) if claim is not None]
    items = tuple(
        JudgedItem(
            f"CLAIM: {sanitize_for_prompt(claim, _CLAIM_MAX_CHARS)}",
            seconds,
            evidence.window(seconds),
        )
        for claim, seconds in stamped
    )
    return Sample(SCORE_HERO, items, skipped=len(raw) - len(stamped))


def _shipped_questions(tabs: Sequence[Any]) -> list[Mapping[str, Any]]:
    """The questions of every ``quiz_arena`` tab, in page order."""
    questions: list[Mapping[str, Any]] = []
    for tab in tabs:
        if not isinstance(tab, Mapping) or tab.get("component") != QUIZ_COMPONENT:
            continue
        props = tab.get("props")
        found = props.get("questions") if isinstance(props, Mapping) else None
        if isinstance(found, list):
            questions.extend(q for q in found if isinstance(q, Mapping))
    return questions


def _keyed_options(question: Mapping[str, Any]) -> tuple[list[str], int] | None:
    """The lettered options (≤ ``MAX_QUIZ_OPTIONS``) and the keyed one's index."""
    options = question.get("options")
    key = question.get("correctIndex")
    if not isinstance(options, list) or not isinstance(key, int) or isinstance(key, bool):
        return None
    shown = options[:MAX_QUIZ_OPTIONS]
    if not 0 <= key < len(shown):
        return None
    lettered = [
        f"{chr(ord('A') + i)}. {sanitize_for_prompt(str(option), _OPTION_MAX_CHARS)}"
        for i, option in enumerate(shown)
    ]
    return lettered, key


def _question_item(question: Mapping[str, Any]) -> tuple[str, int] | None:
    """A question rendered for the judge and the second its answer is stamped at."""
    text = question.get("question")
    stamp = question.get("timestamp")
    keyed = _keyed_options(question)
    if not isinstance(text, str) or not text.strip() or not _is_seconds(stamp) or not keyed:
        return None
    lettered, key = keyed
    lines = [f"QUESTION: {sanitize_for_prompt(text.strip(), _CLAIM_MAX_CHARS)}", "OPTIONS:"]
    lines += [*lettered, f"MARKED ANSWER: {lettered[key]}"]
    return "\n".join(lines), int(stamp)


def quiz_sample(tabs: Sequence[Any], evidence: EvidenceSource, youtube_id: str) -> Sample:
    """``QUIZ_SAMPLE`` judgeable questions, drawn by ``youtube_id``, kept in page order."""
    questions = _shipped_questions(tabs)
    judgeable = [item for item in map(_question_item, questions) if item is not None]
    rng = random.Random(f"{youtube_id}:quiz")
    picked = sorted(rng.sample(range(len(judgeable)), k=min(QUIZ_SAMPLE, len(judgeable))))
    items = tuple(
        JudgedItem(body, seconds, evidence.window(seconds))
        for body, seconds in (judgeable[i] for i in picked)
    )
    return Sample(SCORE_QUIZ, items, skipped=len(questions) - len(judgeable))


def hero_quiz_samples(
    *,
    youtube_id: str,
    meta: Mapping[str, Any],
    tabs: Sequence[Any],
    evidence: EvidenceSource,
    sample_rate: float | None = None,
    judge_hero: bool = True,
    judge_quiz: bool = True,
) -> HeroQuizSamples | None:
    """Both samples of an assembled doc; None when the judge is off
    (``LANGFUSE_FAITHFULNESS_SAMPLE_RATE`` ≤ 0), the run has no timed transcript,
    or neither sample has an item.

    ``judge_hero`` / ``judge_quiz`` are False when the hero rewrite / quiz v2 did
    not write that part of the page (their flags are off, or the call failed): the
    page then shows memory's takeaways / no quiz, and judging them would add calls
    the rollback flags are meant to remove. That sample is empty."""
    rate = sample_rate if sample_rate is not None else settings.LANGFUSE_FAITHFULNESS_SAMPLE_RATE
    if rate <= 0 or not evidence.segments:
        return None
    samples = HeroQuizSamples(
        hero_sample(meta, evidence) if judge_hero else Sample(SCORE_HERO, ()),
        quiz_sample(tabs, evidence, youtube_id) if judge_quiz else Sample(SCORE_QUIZ, ()),
    )
    return samples if samples.calls() else None


# ─── Judging ────────────────────────────────────────────────────────────
def _verdict(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _item_index(value: object, count: int) -> int | None:
    """A verdict's 1-based ``id`` as a list index; None when out of range."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= count:
        return value - 1
    return None


def parse_verdicts(raw: str, count: int) -> list[bool | None]:
    """The judge's verdicts by item id; None where it left an id out or garbled it."""
    verdicts: list[bool | None] = [None] * count
    rows = parse_json_response(raw).get("verdicts") if raw else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        index = _item_index(row.get("id"), count)
        verdict = _verdict(row.get("grounded"))
        if index is not None and verdict is not None and verdicts[index] is None:
            verdicts[index] = verdict
    return verdicts


async def _judge(llm_service: LLMService, sample: Sample) -> list[bool | None]:
    """One call for the whole sample; every verdict None when the call fails."""
    try:
        raw = await call_llm_with_retry(
            llm_service,
            sample.prompt(),
            max_tokens=sample.max_tokens,
            timeout=_JUDGE_TIMEOUT_SECONDS,
            max_retries=0,
            stage_name=JUDGE_STAGE,
            use_fast_model=True,
            json_mode=True,
            model_override=settings.get_stage_model(JUDGE_STAGE),
        )
    except Exception as exc:  # noqa: BLE001 — judge errors must not crash the pipeline
        logger.debug("Faithfulness %s call failed: %s", sample.score_name, exc)
        return [None] * len(sample.items)
    return parse_verdicts(raw or "", len(sample.items))


def _comment(sample: Sample, report: FaithfulnessReport, errors: int) -> str:
    if sample.is_hero:
        what, skipped = "takeaways grounded at their stamps", "unstamped"
    else:
        what, skipped = "sampled keyed answers supported at their stamps", "unjudgeable questions"
    return (
        f"{report.grounded_count}/{report.sample_size} {what} "
        f"({errors} judge errors, {sample.skipped} {skipped})"
    )


def _log_report(sample: Sample, verdicts: list[bool | None]) -> FaithfulnessReport | None:
    """Score the sample on the trace; None when the judge returned no usable verdict."""
    judged = [verdict for verdict in verdicts if verdict is not None]
    if not judged:
        logger.debug("Faithfulness %s: no usable verdicts", sample.score_name)
        return None
    grounded = sum(1 for verdict in judged if verdict)
    report = FaithfulnessReport(
        sample_size=len(judged),
        grounded_count=grounded,
        score=round(grounded / len(judged), 3),
    )
    comment = _comment(sample, report, len(verdicts) - len(judged))
    log_score(sample.score_name, report.score, comment=comment)
    if not report.passed:
        logger.warning("Low %s score: %.2f (%s)", sample.score_name, report.score, comment)
    return report


async def run_hero_quiz_check(
    *, llm_service: LLMService, samples: HeroQuizSamples
) -> dict[str, FaithfulnessReport]:
    """Judge both samples in parallel and score each on the active Langfuse trace.

    Returns the reports by score name (a sample with no usable verdict has none).
    Never raises.
    """
    calls = samples.calls()
    # ``return_exceptions``: one unexpected raise loses only that sample's score.
    results = await asyncio.gather(
        *(_judge(llm_service, sample) for sample in calls), return_exceptions=True
    )
    reports: dict[str, FaithfulnessReport] = {}
    for sample, result in zip(calls, results):
        if isinstance(result, BaseException):
            logger.debug("Faithfulness %s failed: %s", sample.score_name, result)
            continue
        report = _log_report(sample, result)
        if report is not None:
            reports[sample.score_name] = report
    return reports
