"""Pydantic v2 models for v3 domain data, modifiers, and VIEResponse."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ..shared_config.domain_config import valid_content_tags, valid_modifiers
from .domain_validation import ValidationReport, merge_report, validate_block_items
from .idea_types import (
    Claim,
    LearningKeyIdea,
    PodcastQA,
    TechTopic,
    seconds_or_none,
    text_or_none,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Travel Domain
# ─────────────────────────────────────────────────────


class TravelSpot(BaseModel):
    """Mirrors TS ``SpotItem`` (vie-response.ts) — undeclared fields silently
    drop on ``model_dump``, so every TS-declared field must be listed here
    (enforced by ``tests/test_ts_pydantic_parity.py``)."""

    name: str
    emoji: str = ""
    description: str = ""
    cost: str | None = None
    currency: str | None = None
    duration: str | None = None
    map_query: str | None = Field(None, alias="mapQuery")
    booking_search: str | None = Field(None, alias="bookingSearch")
    tips: str | None = None
    specs: str | None = None
    pronunciation: str | None = None
    rating: float | None = None
    thumbnail_url: str | None = Field(None, alias="thumbnailUrl")
    # Where the spot is discussed (seconds) — the seek chip (output-quality R2).
    timestamp: int | None = None
    # The speaker's verbatim line about the spot (output-quality R10).
    quote: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        """Seconds into the video: a non-negative integer, else None."""
        return seconds_or_none(v)

    @field_validator("rating", mode="before")
    @classmethod
    def coerce_rating(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


class TravelDay(BaseModel):
    day: int
    city: str | None = None
    theme: str | None = None
    spots: list[TravelSpot] = []
    tips: list[str] = []

    @field_validator("day", mode="before")
    @classmethod
    def coerce_day(cls, v: Any) -> int:
        if v is None:
            return 1
        return int(v)


class TravelBudgetItem(BaseModel):
    category: str
    amount: float = 0
    currency: str = "USD"
    notes: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v: Any) -> float:
        if v is None:
            return 0.0
        return float(v)


class TravelBudget(BaseModel):
    total: float = 0
    currency: str = "USD"
    breakdown: list[TravelBudgetItem] = []

    @field_validator("total", mode="before")
    @classmethod
    def coerce_total(cls, v: Any) -> float:
        if v is None:
            return 0.0
        return float(v)

    @field_validator("currency", mode="before")
    @classmethod
    def coerce_currency(cls, v: Any) -> str:
        if not v:
            return "USD"
        return str(v)


class TravelPackingItem(BaseModel):
    item: str
    category: str = "Essentials"
    essential: bool = False
    weight: float | None = None
    emoji: str = ""

    @field_validator("weight", mode="before")
    @classmethod
    def coerce_weight(cls, v: Any) -> float | None:
        """Kilograms as a positive number, else ``None``.

        Unit strings ("200 g") are dropped rather than parsed: the schema asks
        for kg, so reading the digits would turn grams into kilograms, and a
        hard failure would push the whole travel block through unvalidated.
        """
        if isinstance(v, bool) or v is None:
            return None
        try:
            kg = float(v)
        except (TypeError, ValueError):
            return None
        return kg if kg > 0 else None


class TravelTip(BaseModel):
    text: str
    type: str = "tip"

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> str:
        valid = ("tip", "warning", "info")
        return v if v in valid else "tip"


class TravelData(BaseModel):
    model_config = {"populate_by_name": True}

    itinerary: list[TravelDay] = []
    budget: TravelBudget = Field(default_factory=TravelBudget)
    packing_list: list[TravelPackingItem] = Field([], alias="packingList")
    accommodation_tips: list[TravelTip] = Field([], alias="accommodationTips")
    transportation_tips: list[TravelTip] = Field([], alias="transportationTips")
    best_season: str | None = Field(None, alias="bestSeason")

    @field_validator("accommodation_tips", "transportation_tips", mode="before")
    @classmethod
    def coerce_tips_to_list(cls, v: Any) -> list:
        """Accept legacy string format and convert to list."""
        if isinstance(v, str):
            return [{"text": v, "type": "tip"}] if v.strip() else []
        if v is None:
            return []
        return v


# ─────────────────────────────────────────────────────
# Food Domain
# ─────────────────────────────────────────────────────


class FoodMeta(BaseModel):
    model_config = {"populate_by_name": True}

    prep_time: int | None = Field(None, alias="prepTime")
    cook_time: int | None = Field(None, alias="cookTime")
    total_time: int | None = Field(None, alias="totalTime")
    servings: int | None = None
    difficulty: str | None = None
    cuisine: str | None = None

    @field_validator("prep_time", "cook_time", "total_time", mode="before")
    @classmethod
    def coerce_time_minutes(cls, v: Any) -> int | None:
        """Extract first integer from strings like '~12-15 minutes'."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, (float,)):
            return int(v)
        if isinstance(v, str):
            import re

            match = re.search(r"\d+", v)
            return int(match.group()) if match else None
        return None

    @field_validator("difficulty", mode="before")
    @classmethod
    def coerce_difficulty(cls, v: Any) -> str | None:
        if v in ("easy", "medium", "hard"):
            return v
        return None


class FoodIngredient(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    # Only as the speaker states it (D13): no stated number → None, never 0.
    amount: float | None = None
    display_amount: str = Field("", alias="displayAmount")
    unit: str | None = None
    group: str | None = None
    notes: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v: Any) -> float | None:
        if v is None or isinstance(v, bool):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


class FoodStep(BaseModel):
    """Mirrors TS ``StepItem`` (vie-response.ts) for ``FoodData.steps``.

    NOTE: TS declares ``duration?: string`` but this mirror deliberately
    coerces to minutes-as-int (legacy numeric contract for cook timers).
    """

    model_config = {"populate_by_name": True}

    number: int
    title: str | None = None
    instruction: str
    duration: int | None = None
    tips: str | None = None
    safety_note: str | None = Field(None, alias="safetyNote")
    timestamp: int | None = None
    thumbnail_url: str | None = Field(None, alias="thumbnailUrl")

    @field_validator("duration", mode="before")
    @classmethod
    def coerce_duration(cls, v: Any) -> int | None:
        """Extract first integer from strings like '5-10 minutes'."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, (float,)):
            return int(v)
        if isinstance(v, str):
            import re

            match = re.search(r"\d+", v)
            return int(match.group()) if match else None
        return None


class FoodTip(BaseModel):
    type: str = "chef_tip"
    text: str

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> str:
        valid = ("chef_tip", "warning", "substitution", "storage")
        return v if v in valid else "chef_tip"


class FoodSubstitution(BaseModel):
    original: str
    substitute: str
    notes: str | None = None


class FoodNutrition(BaseModel):
    nutrient: str
    amount: str
    unit: str | None = None


class FoodData(BaseModel):
    meta: FoodMeta = Field(default_factory=FoodMeta)
    ingredients: list[FoodIngredient] = []
    steps: list[FoodStep] = []
    tips: list[FoodTip] = []
    equipment: list[str] = []
    substitutions: list[FoodSubstitution] = []
    nutrition: list[FoodNutrition] = []


# ─────────────────────────────────────────────────────
# Learning Domain
# ─────────────────────────────────────────────────────


class LearningKeyPoint(BaseModel):
    emoji: str = ""
    title: str
    detail: str = ""
    timestamp: int | None = None


class ConceptConnection(BaseModel):
    """Typed edge between two concepts (`{to, type}`), mirroring the TS
    `ConceptConnection`. Shared by `LearningConcept` and `ScienceConcept`.

    Accepts `name`/`target` as defensive aliases for `to`, and keeps `to`
    defaultable + `type` a free string ON PURPOSE: a malformed item must never
    raise `ValidationError` here, or `validate_domain_output` would drop the whole
    concept it belongs to. The assembler's `_normalize_connections` is the
    single source of relation-enum validation (off-enum → `relatesTo`) and drops
    empty targets, so leniency here is safe.
    """

    model_config = {"populate_by_name": True}

    to: str = Field(default="", validation_alias=AliasChoices("to", "name", "target"))
    type: str = "relatesTo"


class LearningConcept(BaseModel):
    name: str
    emoji: str = ""
    definition: str = ""
    example: str | None = None
    analogy: str | None = None
    # Thematic cluster label the planner asks the LLM to assign; powers the
    # ConceptCanvas Groups view. Dropped silently if not declared here.
    group: str | None = None
    # Where the speaker explains it (seconds) — the seek chip and the frame (L13).
    timestamp: int | None = None
    connections: list[str | ConceptConnection] = []

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)


class LearningTimestamp(BaseModel):
    """One moment of a learning video (R7): what was established at this beat.

    ``description`` is required — a moment without one is a chapter jump, and
    validation drops it (counted in ``_dropped``); a model writing ``summary``
    or ``detail`` instead keeps it. ``quote`` is the speaker's line where the
    moment is a claim.
    """

    model_config = {"populate_by_name": True}

    time: str = ""
    seconds: int = 0
    label: str = ""
    description: str = Field(validation_alias=AliasChoices("description", "summary", "detail"))
    quote: str | None = None

    @field_validator("seconds", mode="before")
    @classmethod
    def coerce_seconds(cls, v: Any) -> int:
        if v is None:
            return 0
        return int(v)

    @field_validator("description")
    @classmethod
    def require_description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a moment states what was established")
        return v.strip()

    @field_validator("quote", mode="before")
    @classmethod
    def coerce_quote(cls, v: Any) -> str | None:
        return text_or_none(v)


class LearningData(BaseModel):
    model_config = {"populate_by_name": True}

    key_points: list[LearningKeyPoint] = Field([], alias="keyPoints")
    # The argument tab of a talk, lecture or podcast (D10); keyPoints stays for explainers.
    key_ideas: list[LearningKeyIdea] = Field([], alias="keyIdeas")
    concepts: list[LearningConcept] = []
    takeaways: list[str] = []
    timestamps: list[LearningTimestamp] = []
    # Checkable claims of a talk or lecture (D11) — claims_tracker on has_claims.
    claims: list[Claim] = []
    key_question: str = Field("", alias="keyQuestion")
    summary: str = ""


# ─────────────────────────────────────────────────────
# Review Domain
# ─────────────────────────────────────────────────────


class ReviewRating(BaseModel):
    model_config = {"populate_by_name": True}

    score: float = 0.0
    max_score: float = Field(10.0, alias="maxScore")
    label: str = ""
    # Where the reviewer gives the score — without it the score never renders (D13).
    timestamp: int | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, v: Any) -> float:
        if v is None:
            return 0.0
        return float(v)

    @field_validator("max_score", mode="before")
    @classmethod
    def coerce_max_score(cls, v: Any) -> float:
        if v is None:
            return 10.0
        return float(v)


class ReviewSpec(BaseModel):
    key: str
    value: str


class ReviewComparison(BaseModel):
    model_config = {"populate_by_name": True}

    feature: str
    this_product: str = Field(alias="thisProduct")
    competitor: str
    competitor_name: str = Field(alias="competitorName")
    winner: str | None = None

    @field_validator("winner", mode="before")
    @classmethod
    def coerce_winner(cls, v: Any) -> str | None:
        valid = ("left", "right", "tie")
        return v if v in valid else None


# The four verdicts ReviewSummary renders (``ReviewVerdict.badge`` in
# vie-response.ts); anything else is no verdict, in the validator and in assembly
# (``normalizers._normalize_verdict``) alike.
VERDICT_BADGES: tuple[str, ...] = ("recommended", "not_recommended", "conditional", "best_in_class")


class ReviewVerdict(BaseModel):
    model_config = {"populate_by_name": True}

    # None when the reviewer states no verdict (D13): a defaulted "recommended"
    # shipped as the reviewer's call.
    badge: str | None = None
    best_for: list[str] = Field([], alias="bestFor")
    not_for: list[str] = Field([], alias="notFor")
    bottom_line: str = Field("", alias="bottomLine")
    sub_scores: list[dict] | None = Field(None, alias="subScores")
    # Where the reviewer states the verdict — the badge renders only with it (R2).
    timestamp: int | None = None

    @field_validator("badge", mode="before")
    @classmethod
    def coerce_badge(cls, v: Any) -> str | None:
        return v if v in VERDICT_BADGES else None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)


class ReviewData(BaseModel):
    product: str = ""
    price: str | None = None
    rating: ReviewRating = Field(default_factory=ReviewRating)
    pros: list[str] = []
    cons: list[str] = []
    specs: list[ReviewSpec] = []
    comparisons: list[ReviewComparison] = []
    verdict: ReviewVerdict = Field(default_factory=ReviewVerdict)


# ─────────────────────────────────────────────────────
# Tech Domain
# ─────────────────────────────────────────────────────


class TechDependency(BaseModel):
    name: str
    version: str | None = None


class TechEnvVar(BaseModel):
    name: str
    description: str = ""
    example: str | None = None


class TechSetup(BaseModel):
    model_config = {"populate_by_name": True}

    commands: list[str] = []
    dependencies: list[TechDependency] = []
    env_vars: list[TechEnvVar] = Field([], alias="envVars")


class TechSnippet(BaseModel):
    filename: str | None = None
    language: str = ""
    code: str = ""
    explanation: str = ""
    timestamp: int | None = None


class TechPattern(BaseModel):
    model_config = {"populate_by_name": True}

    title: str
    do_example: str = Field("", alias="doExample")
    dont_example: str = Field("", alias="dontExample")
    explanation: str = ""


class TechCheatSheetItem(BaseModel):
    title: str
    code: str = ""
    description: str = ""


class TechData(BaseModel):
    model_config = {"populate_by_name": True}

    languages: list[str] = []
    frameworks: list[str] = []
    # Objects since output-quality 1.3 (L25): a bare string is a topic's name.
    topics: list[TechTopic] = []
    setup: TechSetup = Field(default_factory=TechSetup)
    snippets: list[TechSnippet] = []
    patterns: list[TechPattern] = []
    cheat_sheet: list[TechCheatSheetItem] = Field([], alias="cheatSheet")
    # Checkable claims a tech talk makes (benchmarks, adoption figures — D11).
    claims: list[Claim] = []

    @model_validator(mode="before")
    @classmethod
    def migrate_concepts_to_topics(cls, data: Any) -> Any:
        """Backward compat: rename cached 'concepts' (string[]) to 'topics'."""
        if isinstance(data, dict) and "concepts" in data and "topics" not in data:
            if isinstance(data.get("concepts"), list) and all(
                isinstance(c, str) for c in data["concepts"]
            ):
                data["topics"] = data.pop("concepts")
        return data

    @field_validator("topics", mode="before")
    @classmethod
    def _coerce_topics(cls, v: Any) -> Any:
        """A bare string topic (the pre-1.3 shape, still in cached rows) is its name."""
        if not isinstance(v, list):
            return v
        return [{"name": item} if isinstance(item, str) else item for item in v]

    @field_validator("languages", "frameworks", mode="before")
    @classmethod
    def _coerce_string_lists(cls, v: Any) -> list[str]:
        """Coerce list items to strings — LLM sometimes returns objects like {name: '...'}."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.append(item.get("name") or item.get("label") or str(item))
            else:
                result.append(str(item))
        return result


# ─────────────────────────────────────────────────────
# Fitness Domain
# ─────────────────────────────────────────────────────


class FitnessModification(BaseModel):
    label: str
    description: str = ""


class FitnessExercise(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    emoji: str = "💪"
    sets: int | None = None
    reps: str | None = None
    duration: str | None = None
    rest: str | None = None
    difficulty: str | None = None
    form_cues: list[str] = Field([], alias="formCues")
    modifications: list[FitnessModification] = []
    superset_with: str | None = Field(None, alias="supersetWith")
    timestamp: int | None = None

    @field_validator("difficulty", mode="before")
    @classmethod
    def coerce_difficulty(cls, v: Any) -> str | None:
        valid = ("beginner", "intermediate", "advanced")
        return v if v in valid else None


class FitnessTimerInterval(BaseModel):
    name: str
    duration: int = 0
    type: str = "work"

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> str:
        valid = ("work", "rest", "warmup", "cooldown")
        return v if v in valid else "work"


class FitnessTimer(BaseModel):
    intervals: list[FitnessTimerInterval] = []
    rounds: int = 1

    @field_validator("rounds", mode="before")
    @classmethod
    def coerce_rounds(cls, v: Any) -> int:
        return v if v is not None else 1


class FitnessTip(BaseModel):
    type: str = "form"
    text: str

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> str:
        valid = ("form", "safety", "progression")
        return v if v in valid else "form"


class FitnessMeta(BaseModel):
    model_config = {"populate_by_name": True}

    type: str = "general"
    # None when the video states no level (D13): a default read as the video's.
    difficulty: str | None = None
    # None when the instructor states no length (D13): a default 0 read as one.
    duration: int | None = None
    muscle_groups: list[str] = Field([], alias="muscleGroups")
    equipment: list[str] = []
    calories_burned: int | None = Field(None, alias="caloriesBurned")

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> str:
        return v if v else "general"

    @field_validator("difficulty", mode="before")
    @classmethod
    def coerce_difficulty(cls, v: Any) -> str | None:
        valid = ("beginner", "intermediate", "advanced")
        return v if v in valid else None

    @field_validator("duration", mode="before")
    @classmethod
    def coerce_duration(cls, v: Any) -> int | None:
        return seconds_or_none(v)


class FitnessData(BaseModel):
    meta: FitnessMeta = Field(default_factory=FitnessMeta)
    warmup: list[FitnessExercise] = []
    exercises: list[FitnessExercise] = []
    cooldown: list[FitnessExercise] = []
    timer: FitnessTimer | None = None
    tips: list[FitnessTip] = []


# ─────────────────────────────────────────────────────
# Music Domain
# ─────────────────────────────────────────────────────


class MusicCredit(BaseModel):
    role: str
    name: str


class MusicSection(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    timestamp: int | None = None
    end_timestamp: int | None = Field(None, alias="endTimestamp")
    duration: int | None = None
    description: str = ""

    @field_validator("duration", mode="before")
    @classmethod
    def coerce_duration(cls, v: Any) -> int | None:
        """Extract first integer from strings like '~60s', '~25s'."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            import re

            match = re.search(r"\d+", v)
            return int(match.group()) if match else None
        return None


class MusicLyricLine(BaseModel):
    timestamp: int | None = None
    line: str


class MusicAnalysisItem(BaseModel):
    aspect: str
    emoji: str = ""
    detail: str = ""


class MusicData(BaseModel):
    title: str = ""
    artist: str = ""
    genre: list[str] = []
    credits: list[MusicCredit] = []
    analysis: list[MusicAnalysisItem] = []
    structure: list[MusicSection] = []
    lyrics: list[MusicLyricLine] = []
    themes: list[str] = []

    @field_validator("analysis", mode="before")
    @classmethod
    def coerce_analysis(cls, v: Any) -> list:
        """Accept legacy string format and convert to list."""
        if isinstance(v, str):
            return [{"aspect": "Overview", "emoji": "🎵", "detail": v}] if v.strip() else []
        if v is None:
            return []
        return v


# ─────────────────────────────────────────────────────
# Project Domain
# ─────────────────────────────────────────────────────


class ProjectMaterial(BaseModel):
    name: str
    quantity: str | None = None
    cost: str | None = None
    notes: str | None = None


class ProjectTool(BaseModel):
    name: str
    required: bool = True
    alternative: str | None = None


class ProjectStep(BaseModel):
    model_config = {"populate_by_name": True}

    number: int
    title: str = ""
    instruction: str = ""
    duration: str | None = None
    tips: str | None = None
    safety_note: str | None = Field(None, alias="safetyNote")
    timestamp: int | None = None
    thumbnail_url: str | None = Field(None, alias="thumbnailUrl")


class ProjectData(BaseModel):
    model_config = {"populate_by_name": True}

    # None when unstated (D13): "Untitled Project" / "intermediate" read as facts.
    project_name: str | None = Field(None, alias="projectName")
    difficulty: str | None = None
    estimated_time: str = Field("unknown", alias="estimatedTime")
    estimated_cost: str | None = Field(None, alias="estimatedCost")
    materials: list[ProjectMaterial] = []
    tools: list[ProjectTool] = []
    steps: list[ProjectStep] = []
    safety_warnings: list[str] = Field([], alias="safetyWarnings")

    @field_validator("project_name", mode="before")
    @classmethod
    def coerce_name(cls, v: Any) -> str | None:
        return v if v else None

    @field_validator("difficulty", mode="before")
    @classmethod
    def coerce_difficulty(cls, v: Any) -> str | None:
        valid = ("beginner", "intermediate", "advanced")
        return v if v in valid else None

    @field_validator("estimated_time", mode="before")
    @classmethod
    def coerce_time(cls, v: Any) -> str:
        return v if v else "unknown"


# ─────────────────────────────────────────────────────
# Modifiers
# ─────────────────────────────────────────────────────


class NarrativeKeyMoment(BaseModel):
    model_config = {"populate_by_name": True}

    timestamp: int | None = None
    end_timestamp: int | None = Field(None, alias="endTimestamp")
    description: str = ""
    mood: str = ""
    emoji: str = ""


class NarrativeQuote(BaseModel):
    text: str
    speaker: str = ""
    timestamp: int | None = None
    context: str = ""


class NarrativeData(BaseModel):
    model_config = {"populate_by_name": True}

    key_moments: list[NarrativeKeyMoment] = Field([], alias="keyMoments")
    quotes: list[NarrativeQuote] = []
    takeaways: list[str] = []


class FinanceCost(BaseModel):
    item: str
    amount: float = 0
    currency: str = "USD"
    category: str = ""

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v: Any) -> float:
        if v is None:
            return 0.0
        return float(v)


class FinanceData(BaseModel):
    model_config = {"populate_by_name": True}

    costs: list[FinanceCost] = []
    saving_tips: list[str] = Field([], alias="savingTips")


# ─────────────────────────────────────────────────────
# Language Domain
# ─────────────────────────────────────────────────────


class LanguagePhrase(BaseModel):
    phrase: str
    translation: str = ""
    pronunciation: str | None = None
    context: str | None = None
    timestamp: int | None = None


class LanguageRule(BaseModel):
    name: str
    emoji: str = ""
    explanation: str = ""
    examples: list[str] = []
    exceptions: list[str] = []


class LanguageDrill(BaseModel):
    model_config = {"populate_by_name": True}

    number: int = 1
    instruction: str = ""
    prompt: str = ""
    answer: str = ""
    tips: str | None = None
    timestamp: int | None = None

    @field_validator("number", mode="before")
    @classmethod
    def coerce_number(cls, v: Any) -> int:
        if v is None:
            return 1
        return int(v)


class LanguageVocab(BaseModel):
    model_config = {"populate_by_name": True}

    word: str
    definition: str = ""
    pronunciation: str | None = None
    part_of_speech: str | None = Field(None, alias="partOfSpeech")
    example: str | None = None


class LanguageData(BaseModel):
    model_config = {"populate_by_name": True}

    target_language: str = Field("", alias="targetLanguage")
    native_language: str | None = Field(None, alias="nativeLanguage")
    # None when the video states no level (D13): a default read as the video's.
    level: str | None = None
    phrases: list[LanguagePhrase] = []
    rules: list[LanguageRule] = []
    drills: list[LanguageDrill] = []
    vocabulary: list[LanguageVocab] = []

    @field_validator("level", mode="before")
    @classmethod
    def coerce_level(cls, v: Any) -> str | None:
        valid = ("beginner", "intermediate", "advanced")
        return v if v in valid else None


# ─────────────────────────────────────────────────────
# Science Domain
# ─────────────────────────────────────────────────────


class ScienceConcept(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    emoji: str = ""
    definition: str = ""
    formula: str | None = None
    real_world_example: str | None = Field(None, alias="realWorldExample")
    group: str | None = None
    timestamp: int | None = None
    connections: list[str | ConceptConnection] = []


class ScienceKeyFact(BaseModel):
    emoji: str = ""
    title: str
    detail: str = ""
    source: str | None = None


class ScienceExperiment(BaseModel):
    model_config = {"populate_by_name": True}

    number: int = 1
    title: str = ""
    instruction: str = ""
    materials: list[str] = []
    expected_result: str = Field("", alias="expectedResult")
    safety_note: str | None = Field(None, alias="safetyNote")
    timestamp: int | None = None

    @field_validator("number", mode="before")
    @classmethod
    def coerce_number(cls, v: Any) -> int:
        if v is None:
            return 1
        return int(v)


class ScienceData(BaseModel):
    model_config = {"populate_by_name": True}

    field: str = ""
    # None when the video states no level (D13): a default read as the video's.
    level: str | None = None
    concepts: list[ScienceConcept] = []
    key_facts: list[ScienceKeyFact] = Field([], alias="keyFacts")
    experiments: list[ScienceExperiment] = []

    @field_validator("level", mode="before")
    @classmethod
    def coerce_level(cls, v: Any) -> str | None:
        valid = ("beginner", "intermediate", "advanced")
        return v if v in valid else None


# ─────────────────────────────────────────────────────
# Podcast (interactive-overhaul-v2 P5a)
# ─────────────────────────────────────────────────────


class PodcastSegment(BaseModel):
    model_config = {"populate_by_name": True}

    title: str = ""
    # The prompt asks for `summary`; a model that writes `description` instead must not
    # lose the segment's only prose (the moment normalizer carries it as `description`).
    summary: str = Field(default="", validation_alias=AliasChoices("summary", "description"))
    timestamp: int | None = None
    end_seconds: int | None = Field(None, alias="endSeconds")
    speaker: str | None = None
    # The line that carries the segment's conclusion, verbatim (R7, R10).
    quote: str | None = None

    @field_validator("quote", mode="before")
    @classmethod
    def coerce_quote(cls, v: Any) -> str | None:
        return text_or_none(v)

    @model_validator(mode="before")
    @classmethod
    def prefer_filled_prose(cls, data: Any) -> Any:
        """``AliasChoices`` takes the first key present, so an empty or null
        ``summary`` would hide a filled ``description``: the prose wins."""
        if not isinstance(data, dict) or str(data.get("summary") or "").strip():
            return data
        description = data.get("description")
        if isinstance(description, str) and description.strip():
            return {**data, "summary": description}
        return data


class PodcastGuest(BaseModel):
    name: str
    role: str | None = None
    description: str = ""
    emoji: str = ""


class PodcastQuote(BaseModel):
    quote: str
    speaker: str | None = None
    timestamp: int | None = None


class PodcastTopic(BaseModel):
    topic: str
    detail: str = ""


class PodcastData(BaseModel):
    show: str | None = None
    host: str | None = None
    segments: list[PodcastSegment] = []
    # An interview's questions and answers (C10) — moment_track with speaker + question.
    qa: list[PodcastQA] = []
    guests: list[PodcastGuest] = []
    quotes: list[PodcastQuote] = []
    topics: list[PodcastTopic] = []
    # Checkable claims made on the show (D11).
    claims: list[Claim] = []


# ─────────────────────────────────────────────────────
# News (interactive-overhaul-v2 P5b)
# ─────────────────────────────────────────────────────


class NewsTimelineEvent(BaseModel):
    label: str
    description: str = ""
    timestamp: int | None = None


class NewsEntity(BaseModel):
    name: str
    role: str | None = None
    description: str = ""
    emoji: str = ""


# The news claim is the generic claim (D11): one shape on every has_claims domain.
NewsClaim = Claim


class NewsContextItem(BaseModel):
    key: str
    value: str = ""


class NewsData(BaseModel):
    model_config = {"populate_by_name": True}

    headline: str | None = None
    story_timeline: list[NewsTimelineEvent] = Field([], alias="storyTimeline")
    entities: list[NewsEntity] = []
    claims: list[NewsClaim] = []
    context: list[NewsContextItem] = []


# ─────────────────────────────────────────────────────
# Gaming (interactive-overhaul-v2 P5c)
# ─────────────────────────────────────────────────────


class GamingHighlight(BaseModel):
    label: str
    description: str = ""
    timestamp: int | None = None


class GamingLoadoutItem(BaseModel):
    item: str
    category: str | None = None
    note: str = ""


class GamingWalkthroughStep(BaseModel):
    instruction: str
    timestamp: int | None = None


class GamingRanking(BaseModel):
    item: str
    tier: str | None = None
    reason: str = ""
    emoji: str = ""
    # Where the creator places it — a tier renders only with it (D13).
    timestamp: int | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def coerce_timestamp(cls, v: Any) -> int | None:
        return seconds_or_none(v)

    @field_validator("tier", mode="before")
    @classmethod
    def coerce_tier(cls, v: Any) -> str | None:
        if v is None:
            return None
        tier = str(v).strip().upper()
        return tier if tier in ("S", "A", "B", "C", "D") else None


class GamingData(BaseModel):
    title: str | None = None
    highlights: list[GamingHighlight] = []
    loadout: list[GamingLoadoutItem] = []
    walkthrough: list[GamingWalkthroughStep] = []
    rankings: list[GamingRanking] = []


# ─────────────────────────────────────────────────────
# Sport (interactive-overhaul-v2 P5d)
# ─────────────────────────────────────────────────────


class SportMatchEvent(BaseModel):
    label: str
    description: str = ""
    timestamp: int | None = None


class SportPosition(BaseModel):
    player: str
    role: str | None = None
    x: float = 50.0
    y: float = 50.0
    number: int | None = None


class SportFormation(BaseModel):
    name: str | None = None
    team: str | None = None
    positions: list[SportPosition] = []


class SportStatRow(BaseModel):
    feature: str
    left: str = ""
    right: str = ""


class SportStatComparison(BaseModel):
    model_config = {"populate_by_name": True}

    left_label: str | None = Field(None, alias="leftLabel")
    right_label: str | None = Field(None, alias="rightLabel")
    comparisons: list[SportStatRow] = []


class SportData(BaseModel):
    model_config = {"populate_by_name": True}

    title: str | None = None
    match_events: list[SportMatchEvent] = Field([], alias="matchEvents")
    formation: SportFormation | None = None
    stat_comparison: SportStatComparison | None = Field(None, alias="statComparison")


# ─────────────────────────────────────────────────────
# VIEResponse Envelope
# ─────────────────────────────────────────────────────


class TabDefinition(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    label: str
    emoji: str = ""
    data_source: str = Field("", alias="dataSource")


class SectionDefinition(BaseModel):
    type: str
    items: list[dict] = []


class VIEResponseMeta(BaseModel):
    """Mirrors TS ``VIEResponseMeta`` — full field parity enforced by
    ``tests/test_ts_pydantic_parity.py`` (undeclared fields silently drop
    on ``model_dump``)."""

    model_config = {"populate_by_name": True}

    video_id: str = Field("", alias="videoId")
    video_title: str = Field("", alias="videoTitle")
    creator: str = ""
    content_tags: list[str] = Field([], alias="contentTags")
    modifiers: list[str] = []
    primary_tag: str = Field("learning", alias="primaryTag")
    user_goal: str = Field("", alias="userGoal")
    tldr: str | None = None
    key_takeaways: list[str] | None = Field(None, alias="keyTakeaways")
    master_summary: str | None = Field(None, alias="masterSummary")
    seo_description: str | None = Field(None, alias="seoDescription")
    language: str | None = None
    is_rtl: bool | None = Field(None, alias="isRTL")
    # Partial-result flag (dropped extraction batches / critical coverage) —
    # set True-only by the assembly phase; drives the FE retry affordance.
    degraded: bool | None = None


class VIEResponse(BaseModel):
    model_config = {"populate_by_name": True}

    meta: VIEResponseMeta
    tabs: list[TabDefinition] = []
    sections: list[SectionDefinition] = []

    # Domain data (only populated domains present)
    travel: TravelData | None = None
    food: FoodData | None = None
    tech: TechData | None = None
    fitness: FitnessData | None = None
    music: MusicData | None = None
    learning: LearningData | None = None
    review: ReviewData | None = None
    project: ProjectData | None = None
    language: LanguageData | None = None
    science: ScienceData | None = None

    # Modifiers
    narrative: NarrativeData | None = None
    finance: FinanceData | None = None

    # Enrichment
    quizzes: list[dict] | None = None
    scenarios: list[dict] | None = None
    flashcards: list[dict] | None = None


# ─────────────────────────────────────────────────────
# Domain Model Registry
# ─────────────────────────────────────────────────────

DOMAIN_MODELS: dict[str, type[BaseModel]] = {
    "travel": TravelData,
    "food": FoodData,
    "learning": LearningData,
    "review": ReviewData,
    "tech": TechData,
    "fitness": FitnessData,
    "music": MusicData,
    "project": ProjectData,
    "language": LanguageData,
    "science": ScienceData,
    "podcast": PodcastData,
    "news": NewsData,
    "gaming": GamingData,
    "sport": SportData,
}

MODIFIER_MODELS: dict[str, type[BaseModel]] = {
    "narrative": NarrativeData,
    "finance": FinanceData,
}

VALID_CONTENT_TAGS = valid_content_tags()
VALID_MODIFIERS = valid_modifiers()


def _model_keys(model_cls: type[BaseModel]) -> set[str]:
    """Field names and aliases the model accepts at its top level."""
    keys: set[str] = set()
    for name, field in model_cls.model_fields.items():
        keys.add(name)
        if field.alias:
            keys.add(field.alias)
    return keys


def _is_empty(value: object) -> bool:
    return value is None or value in ("", [], {})


def _carries_own_fields(model_cls: type[BaseModel], data: dict) -> bool:
    """True when ``data`` holds at least one non-empty field of ``model_cls``.

    Checked on the RAW data: the validated dump always looks populated for
    models with truthy defaults (``estimated_time="unknown"``,
    ``FitnessMeta.type="general"``), which used to smuggle default-only domains in.
    """
    return any(not _is_empty(data.get(key)) for key in _model_keys(model_cls))


def _flat_fields(model_cls: type[BaseModel], data: dict, exclude: set[str]) -> dict:
    """The non-empty top-level fields of ``model_cls`` in ``data``, minus ``exclude``."""
    return {
        key: data[key]
        for key in sorted(_model_keys(model_cls) - exclude)
        if not _is_empty(data.get(key))
    }


def _wrapped_block(data: dict, tag: str) -> dict | None:
    """The LLM's ``{tag}Data`` / ``{tag}`` block for ``tag``, if it wrapped one."""
    for key in (f"{tag}Data", tag):
        block = data.get(key)
        if isinstance(block, dict) and block:
            return block
    return None


def _merge_mixed_shape(tag: str, wrapper: dict, flat: dict) -> dict:
    """Union of a tag's wrapped block and the same tag's flat top-level fields.

    Reachable when chunked extraction merges a wrapped batch with a flat one
    (``merge_batch_extractions``): both halves are real content, so neither
    is dropped. An empty wrapper value takes the flat one, lists concatenate
    (flat items the wrapper lacks are appended), and a true scalar/dict
    conflict keeps the wrapper's value — a deterministic tie-break, since
    neither half is more trustworthy than the other.
    """
    merged = dict(wrapper)
    conflicts: list[str] = []
    for key, flat_value in flat.items():
        wrapped_value = wrapper.get(key)
        if _is_empty(wrapped_value):
            merged[key] = flat_value
        elif isinstance(wrapped_value, list) and isinstance(flat_value, list):
            merged[key] = wrapped_value + [v for v in flat_value if v not in wrapped_value]
        elif wrapped_value != flat_value:
            conflicts.append(key)
    logger.warning(
        "Tag %s has a wrapped block AND flat fields %s — merged (wrapper kept on %s)",
        tag,
        list(flat),
        conflicts,
    )
    return merged


def _validate_block(
    tag: str, model_cls: type[BaseModel] | None, block: dict, report: ValidationReport
) -> dict:
    """Validate one domain/modifier block item by item (hotfix 2.1).

    Items that fail are dropped and counted in ``report``; the rest is kept.
    Invalid data is never passed through.
    """
    if model_cls is None:
        logger.warning("No model for tag: %s — data passed through unvalidated", tag)
        return block
    validated, block_dropped, block_defaulted = validate_block_items(tag, model_cls, block)
    merge_report(report, block_dropped, block_defaulted)
    return validated


def _single_tag_block(tag: str, data: dict) -> dict:
    """Flat output is the norm for one tag, but the LLM sometimes wraps it
    (``{"tech": {...}}``) — validating the wrapper against the model yields
    all defaults (OuNKBjuV7A4). Unwrap it; when both shapes are present,
    merge them rather than silently dropping either."""
    model_cls = DOMAIN_MODELS.get(tag)
    wrapper = _wrapped_block(data, tag)
    if model_cls is None or not _carries_own_fields(model_cls, data):
        return wrapper or data
    if wrapper is None:
        return data
    return _merge_mixed_shape(tag, wrapper, _flat_fields(model_cls, data, exclude=set()))


def _multi_tag_wrapped_block(tag: str, content_tags: list[str], data: dict) -> dict | None:
    """``tag``'s wrapped block, merged with its flat fields when both exist.

    Only flat keys no other content tag's model declares are merged: a shared
    key (science/learning both have ``concepts``) cannot be attributed.
    """
    wrapper = _wrapped_block(data, tag)
    model_cls = DOMAIN_MODELS.get(tag)
    if wrapper is None or model_cls is None:
        return wrapper
    other_keys: set[str] = set()
    for other in content_tags:
        other_cls = DOMAIN_MODELS.get(other)
        if other != tag and other_cls is not None:
            other_keys |= _model_keys(other_cls)
    flat = _flat_fields(model_cls, data, exclude=other_keys)
    return _merge_mixed_shape(tag, wrapper, flat) if flat else wrapper


def _validate_flat_fallback(tag: str, data: dict) -> dict | None:
    """A missing tag validated from the flat data; ``None`` when absent or invalid.

    Unlike a wrapped block, the flat data is the WHOLE response — passing it
    through on a ValidationError would hand this tag every other tag's data
    (e.g. ``tech`` receiving ``foodData``), so an invalid one is skipped.
    """
    model_cls = DOMAIN_MODELS.get(tag)
    if model_cls is None or not _carries_own_fields(model_cls, data):
        return None
    try:
        return model_cls.model_validate(data).model_dump(by_alias=True)
    except ValidationError as e:
        logger.warning(
            "Flat validation failed for missing tag %s — skipped (fields: %s): %s",
            tag,
            sorted(data),
            str(e)[:300],
        )
        return None


def _validate_multi_tag(content_tags: list[str], data: dict, report: ValidationReport) -> dict:
    """Wrapped blocks first; flat data only for tags whose own fields are present."""
    validated: dict = {}
    for tag in content_tags:
        block = _multi_tag_wrapped_block(tag, content_tags, data)
        if block is not None:
            validated[tag] = _validate_block(tag, DOMAIN_MODELS.get(tag), block, report)

    missing_tags = [tag for tag in content_tags if tag not in validated]
    if missing_tags:
        # Handles LLMs that wrap some domains but leave others at the top level.
        logger.info(
            "Missing wrapped keys for tags %s — trying flat validation fallback", missing_tags
        )
    for tag in missing_tags:
        flat_block = _validate_flat_fallback(tag, data)
        if flat_block is not None:
            validated[tag] = flat_block
    return validated


def validate_domain_output_with_drops(
    content_tags: list[str], modifiers: list[str], data: dict
) -> tuple[dict, ValidationReport]:
    """Validate LLM output against domain Pydantic models, item by item.

    Single tag: flat data validated against DOMAIN_MODELS[tag]; a
    ``{tag}Data``/``{tag}``-wrapped response is unwrapped first.
    Multi-tag: ``{tag}Data`` / ``{tag}`` wrappers first, then the flat data
    for tags whose own fields appear in it.
    Returns the validated dict (ready for storage/SSE) and the validation
    report: ``dropped`` per field that lost items
    (``{"tech.cheatSheet": {"dropped": 3, "total": 22}}``) and ``defaulted``,
    the number of fields reset to their default.
    """
    report: ValidationReport = {"dropped": {}, "defaulted": 0}
    if len(content_tags) == 1:
        tag = content_tags[0]
        block = _single_tag_block(tag, data)
        validated = {tag: _validate_block(tag, DOMAIN_MODELS.get(tag), block, report)}
    else:
        validated = _validate_multi_tag(content_tags, data, report)

    for modifier in modifiers:
        mod_block = _wrapped_block(data, modifier)
        if mod_block is not None:
            validated[modifier] = _validate_block(
                modifier, MODIFIER_MODELS.get(modifier), mod_block, report
            )

    return validated, report


def validate_domain_output(content_tags: list[str], modifiers: list[str], data: dict) -> dict:
    """``validate_domain_output_with_drops`` without the dropped counts."""
    return validate_domain_output_with_drops(content_tags, modifiers, data)[0]
