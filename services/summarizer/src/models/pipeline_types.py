"""Shared pipeline types used across the plan, synthesis and enrichment stages."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from ..shared_config.domain_config import EVIDENCE_KEYS
from ..utils.data_helpers import parse_timestamp_to_seconds


@dataclass
class FrameData:
    """Single extracted scene frame with metadata."""

    index: int
    filename: str
    s3_key: str
    timestamp: float = 0.0
    s3_url: str = ""
    local_path: str | None = None
    ocr_text: str | None = None
    text_density: float = 0.0


class SynthesisResult(BaseModel):
    """The synthesis call's answer (pipeline-1min 1d.3).

    ``masterSummary`` + ``seoDescription`` always; ``tldr`` / ``keyTakeaways``
    only when the call was asked for them because memory left one empty.
    """

    master_summary: str = Field(alias="masterSummary")
    seo_description: str = Field("", alias="seoDescription")
    tldr: str = ""
    key_takeaways: list[str] = Field(default_factory=list, alias="keyTakeaways")

    model_config = {"populate_by_name": True}


# Quiz text the UI renders as-is: trimmed, never blank.
_QuizText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

MIN_QUIZ_OPTIONS = 2
MAX_QUIZ_OPTIONS = 6

# A transcript clock inside free text: "4:32", "[1:02:05]", "at 12:08" (seconds
# and minutes past the first group are clock digits, so "12:60" is not one).
_CLOCK_IN_TEXT_RE = re.compile(r"\d{1,3}(?::[0-5]\d){1,2}")


def quiz_timestamp_seconds(value: object) -> int | None:
    """A question's answer time in seconds, or None when the value is not one.

    Takes the extracted item's seconds (``272``, ``"272"``) or the transcript's
    clock (``"4:32"``, ``"[1:02:05]"``). Negative, non-finite, boolean and
    unreadable values are None — a missing chip, never a dropped question.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return int(text)
    match = _CLOCK_IN_TEXT_RE.search(text)
    return parse_timestamp_to_seconds(match.group()) if match else None


class QuizQuestion(BaseModel):
    """One multiple-choice question.

    Strict on purpose: the quiz call is salvaged item by item, so a malformed
    question is dropped instead of shipping a broken answer key.
    """

    question: _QuizText
    options: list[_QuizText] = Field(min_length=MIN_QUIZ_OPTIONS, max_length=MAX_QUIZ_OPTIONS)
    correct_index: int = Field(alias="correctIndex", ge=0)
    explanation: _QuizText
    # Where the transcript gives the answer, in seconds — the seek chip
    # (output-quality R12 / 1.13). Optional: stored rows predate it and a
    # model may omit it; the enrichment drops a stamp past the video's end.
    timestamp: int | None = None
    # The speaker's words that settle the answer, ≤ 25 words from the transcript —
    # quiz v2's evidence, checked by the quote check, which also sets the stamp
    # (output-quality 2.1b). Optional: stored rows and the flag-off quiz carry none,
    # and an unset quote is left out of every dump, so the flag-off wire and stored
    # shapes stay what they were (p2 review B7).
    quote: str | None = Field(None, exclude_if=lambda value: value is None)

    model_config = {"populate_by_name": True}

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: object) -> int | None:
        return quiz_timestamp_seconds(v)

    @field_validator("quote", mode="before")
    @classmethod
    def blank_quote_is_none(cls, v: object) -> object:
        # A blank quote is no evidence: None, so the check drops the question alone.
        return (v.strip() or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _answer_is_one_distinct_option(self) -> QuizQuestion:
        # Clamping an out-of-range index (as the quiz_arena normalizer does)
        # would mark a wrong option correct — the item is rejected instead.
        if self.correct_index >= len(self.options):
            raise ValueError(
                f"correctIndex {self.correct_index} is not one of {len(self.options)} options"
            )
        # Two options that read the same make the answer ambiguous (one of them
        # is marked wrong) — the item is dropped like any malformed question.
        if len({option.casefold() for option in self.options}) < len(self.options):
            raise ValueError("options repeat the same answer")
        return self


class EnrichmentData(BaseModel):
    """Quiz-only enrichment (pipeline-1min 1d.1): read by quiz_arena tabs and quick_quiz."""

    quiz: list[QuizQuestion] = Field(default_factory=list)
    # Quiz v2's checked questions past the video's count, in order: the late group
    # refills from them when it drops a question that restates a shipped hero
    # takeaway (p2 review D7, ``phases.enrichment.settle_late_quiz``). Never dumped.
    spares: list[QuizQuestion] = Field(default_factory=list, exclude=True)

    model_config = {"populate_by_name": True}


# ── Plan Stage ──

# A brief's time point or range, written with the transcript's markers:
# "1:10", "1:10-2:40", "1:02:05-1:04:00" (en/em dashes accepted, normalized to "-").
_TIME = r"\d{1,2}(?::\d{2}){1,2}"
_TIME_RANGE_RE = re.compile(rf"^({_TIME})(?:\s*[-–—]\s*({_TIME}))?$")
MAX_BRIEF_RANGES = 6
MAX_PLAN_TERMS = 12


def _normalize_time_range(raw: str) -> str | None:
    """``"1:10 – 2:40"`` → ``"1:10-2:40"``; ``None`` when it is not a time or range."""
    match = _TIME_RANGE_RE.match(raw.strip())
    if match is None:
        return None
    start, end = match.groups()
    return f"{start}-{end}" if end else start


class TabBrief(BaseModel):
    """What extraction must pull for one planned tab (pipeline-1min Appendix B.2).

    Lenient on purpose: a malformed field becomes its empty value instead of
    failing the whole plan, and fallback tabs (domain defaults) get the empty
    brief — a brief steers extraction, it never gates a tab.
    """

    what: str = ""
    where: list[str] = Field(default_factory=list)
    expect: int = 0

    @field_validator("what", mode="before")
    @classmethod
    def coerce_what(cls, v: object) -> str:
        return v.strip() if isinstance(v, str) else ""

    @field_validator("where", mode="before")
    @classmethod
    def keep_time_ranges(cls, v: object) -> list[str]:
        items = [v] if isinstance(v, str) else v
        if not isinstance(items, list):
            return []
        ranges = [_normalize_time_range(item) for item in items if isinstance(item, str)]
        return [r for r in ranges if r][:MAX_BRIEF_RANGES]

    @field_validator("expect", mode="before")
    @classmethod
    def coerce_expect(cls, v: object) -> int:
        if isinstance(v, bool):
            return 0
        if isinstance(v, int | float):
            return max(0, int(v))
        digits = re.search(r"\d+", v) if isinstance(v, str) else None
        return int(digits.group()) if digits else 0

    @classmethod
    def from_raw(cls, raw: object) -> TabBrief:
        """The brief for an LLM value; anything but an object is the empty brief."""
        return cls.model_validate(raw) if isinstance(raw, dict) else cls()


def _with_brief(tab: dict) -> dict:
    """A copy of ``tab`` with a normalized ``brief``.

    A copy, never in place: fallback tabs are the process-cached domains.json
    ``defaultTabs`` dicts.
    """
    return {**tab, "brief": TabBrief.from_raw(tab.get("brief")).model_dump()}


class PlanIdentity(BaseModel):
    """Creator identity from the plan stage."""

    model_config = {"populate_by_name": True}

    creator_type: str = Field("", alias="creatorType")
    tone: str = ""


class PlanExtractionGuidance(BaseModel):
    """Strategic extraction guidance from the plan stage."""

    model_config = {"populate_by_name": True}

    primary_focus: str = Field("", alias="primaryFocus")
    watch_out_for: str = Field("", alias="watchOutFor")


class PlanResult(BaseModel):
    """Result of the plan stage: video analysis + tab design in one call.

    ``tabs`` stay plain dicts (the triage / SSE / assembly shape); validation
    gives every tab a normalized ``brief``.
    """

    model_config = {"populate_by_name": True}

    # Identity & analysis
    identity: PlanIdentity = Field(default_factory=PlanIdentity)
    core_promise: str = Field("", alias="corePromise")
    unique_angle: str = Field("", alias="uniqueAngle")
    extraction_guidance: PlanExtractionGuidance = Field(
        default_factory=PlanExtractionGuidance, alias="extractionGuidance"
    )
    # Canonical spellings extraction must reuse (people, products, techniques).
    terms: list[str] = Field(default_factory=list)
    # Appendix-C evidence. As parsed: only the keys the planner answered (a
    # missing key is "no opinion", never False — it must not switch a domain
    # requirement off). After reconcile at plan-done (3.2): the strict view
    # every reader shares — true when either reader said true, false only when
    # the plan AND the memory answered false.
    evidence: dict[str, bool] = Field(default_factory=dict)
    # The readers' own answers, kept when reconcile replaces ``evidence`` with
    # the view (None before reconcile; ``memory_evidence`` None = memory failed).
    plan_evidence: dict[str, bool] | None = Field(None, alias="planEvidence")
    memory_evidence: dict[str, bool] | None = Field(None, alias="memoryEvidence")

    # Triage fields
    content_tags: list[str] = Field(default_factory=lambda: ["learning"], alias="contentTags")
    modifiers: list[str] = Field(default_factory=list)
    primary_tag: str = Field("learning", alias="primaryTag")
    user_goal: str = Field("General summary of the video content", alias="userGoal")
    tabs: list[dict] = Field(default_factory=list)
    # Tabs the plan stage removed (unregistered dataSource, no sibling) — the
    # assembly phase prepends them to the persisted ``droppedTabs``.
    dropped_tabs: list[dict] = Field(default_factory=list, alias="droppedTabs")
    # Reconcile decisions (plan parse, plan-done, frames-done: re-pointed,
    # demoted, dropped, backfilled … tabs, with reason, stage and evidence) —
    # persisted as ``pipeline.reconcile``.
    reconcile: list[dict] = Field(default_factory=list)
    # Tab ids reconcile left for the frames-done screen check: code tabs whose
    # has_code is doubly false in the transcript readers (3.2).
    deferred_tabs: list[str] = Field(default_factory=list, alias="deferredTabs")
    # Required components (the primary's code_playground) whose backfill waits
    # for that screen check too: has_code doubly false and no tab planned for it.
    deferred_requirements: list[str] = Field(default_factory=list, alias="deferredRequirements")
    # Plan-time validation left no tab, so ``tabs`` are the domain defaults —
    # assembly then counts only the drops as designed, not drops + defaults.
    plan_fallback: bool = Field(False, alias="planFallback")
    confidence: float = 0.0

    @field_validator("content_tags", mode="before")
    @classmethod
    def coerce_content_tags(cls, v):
        if isinstance(v, str):
            return [v]
        return v or ["learning"]

    @field_validator("modifiers", mode="before")
    @classmethod
    def coerce_modifiers(cls, v):
        if isinstance(v, str):
            return [v]
        return v or []

    @field_validator("tabs", mode="before")
    @classmethod
    def coerce_tabs(cls, v: object) -> object:
        if not isinstance(v, list):
            return []
        return [_with_brief(tab) if isinstance(tab, dict) else tab for tab in v]

    @field_validator("terms", mode="before")
    @classmethod
    def clean_terms(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        terms: list[str] = []
        for raw in v:
            term = raw.strip() if isinstance(raw, str) else ""
            if term and term.casefold() not in {t.casefold() for t in terms}:
                terms.append(term)
        return terms[:MAX_PLAN_TERMS]

    @field_validator("evidence", mode="before")
    @classmethod
    def keep_known_evidence(cls, v: object) -> dict[str, bool]:
        if not isinstance(v, dict):
            return {}
        return {k: val for k, val in v.items() if k in EVIDENCE_KEYS and isinstance(val, bool)}

    @property
    def awaits_screen(self) -> bool:
        """Frames-done may still change the tabs: reconcile left a code tab or a code
        requirement for the screen check."""
        return bool(self.deferred_tabs or self.deferred_requirements)

    def to_triage_dict(self, content_format: str | None = None) -> dict:
        """The ``triage_complete`` SSE payload and the persisted ``pipeline.triage``.

        ``content_format`` is the probe's (it is not part of the plan's answer).
        Evidence (the reconciled view) + canonical terms ride along for the
        assembly backfill check (1d.7) and the gate; the readers' own answers
        for the audit trail (3.2).
        """
        return {
            "contentTags": self.content_tags,
            "modifiers": self.modifiers,
            "primaryTag": self.primary_tag,
            "userGoal": self.user_goal,
            "tabs": self.tabs,
            "confidence": self.confidence,
            "contentFormat": content_format,
            "evidence": self.evidence,
            "planEvidence": self.plan_evidence,
            "memoryEvidence": self.memory_evidence,
            "terms": self.terms,
        }
