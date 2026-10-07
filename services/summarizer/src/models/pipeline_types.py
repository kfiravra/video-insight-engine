"""Shared pipeline types used across synthesis, enrichment, and manifest stages."""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator, model_validator


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
    tldr: str
    key_takeaways: list[str] = Field(alias="keyTakeaways")
    master_summary: str = Field(alias="masterSummary")
    seo_description: str = Field(alias="seoDescription")

    model_config = {"populate_by_name": True}


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int = Field(alias="correctIndex")
    explanation: str

    model_config = {"populate_by_name": True}


class Flashcard(BaseModel):
    front: str
    back: str


class CodeCheatSheetItem(BaseModel):
    title: str
    code: str
    description: str


class ScenarioOption(BaseModel):
    text: str
    correct: bool = False
    explanation: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_string(cls, data: object) -> object:
        """LLM sometimes returns plain strings instead of option dicts."""
        if isinstance(data, str):
            return {"text": data, "correct": False, "explanation": ""}
        return data


class ScenarioItem(BaseModel):
    question: str
    emoji: str = ""
    options: list[ScenarioOption] = []


class EnrichmentData(BaseModel):
    quiz: list[QuizQuestion] | None = None
    flashcards: list[Flashcard] | None = None
    cheat_sheet: list[CodeCheatSheetItem] | None = Field(None, alias="cheatSheet")
    scenarios: list[ScenarioItem] | None = None

    model_config = {"populate_by_name": True}


# ── Manifest Stage ──


class ItemCounts(BaseModel):
    model_config = {"populate_by_name": True}

    steps: int = 0
    spots: int = 0
    exercises: int = 0
    ingredients: int = 0
    songs: int = 0
    tips: int = 0
    products: int = 0
    code_snippets: int = Field(0, alias="codeSnippets")
    concepts: int = 0
    quotes: int = 0

    @field_validator("*", mode="before")
    @classmethod
    def coerce_int(cls, v):
        if v is None:
            return 0
        return int(v)


class ManifestSection(BaseModel):
    model_config = {"populate_by_name": True}

    title: str = ""
    start_percent: int = Field(0, alias="startPercent")
    end_percent: int = Field(100, alias="endPercent")
    density: str = "medium"


class ManifestFlags(BaseModel):
    model_config = {"populate_by_name": True}

    has_storytelling: bool = Field(False, alias="hasStorytelling")
    has_budget_discussion: bool = Field(False, alias="hasBudgetDiscussion")
    has_code_snippets: bool = Field(False, alias="hasCodeSnippets")
    has_recipe: bool = Field(False, alias="hasRecipe")
    has_workout: bool = Field(False, alias="hasWorkout")
    has_product_review: bool = Field(False, alias="hasProductReview")
    speaker_count: int = Field(1, alias="speakerCount")


class ManifestIntent(BaseModel):
    model_config = {"populate_by_name": True}

    user_goal: str = Field("", alias="userGoal")
    video_value: str = Field("", alias="videoValue")
    unique_angle: str = Field("", alias="uniqueAngle")
    return_reason: str = Field("", alias="returnReason")
    actionable_data: list[str] = Field([], alias="actionableData")


# ── Video DNA models (new manifest output shape) ──


class ManifestIdentity(BaseModel):
    """Creator identity from video_dna."""

    model_config = {"populate_by_name": True}

    creator_type: str = Field("", alias="creatorType")
    tone: str = ""
    production: str = ""
    audience: str = ""


class ManifestValue(BaseModel):
    """Value proposition from video_dna."""

    model_config = {"populate_by_name": True}

    core_promise: str = Field("", alias="corePromise")
    actionable_data: list[str] = Field([], alias="actionableData")
    unique_angle: str = Field("", alias="uniqueAngle")
    return_trigger: str = Field("", alias="returnTrigger")
    tool_potential: str = Field("", alias="toolPotential")


class ManifestVisualContent(BaseModel):
    """Visual content analysis from video_dna."""

    model_config = {"populate_by_name": True}

    screen_heavy: bool = Field(False, alias="screenHeavy")
    has_code_on_screen: bool = Field(False, alias="hasCodeOnScreen")
    has_diagrams: bool = Field(False, alias="hasDiagrams")
    frame_summary: str = Field("", alias="frameSummary")


class ManifestExtractionGuidance(BaseModel):
    """Strategic extraction guidance from video_dna."""

    model_config = {"populate_by_name": True}

    primary_focus: str = Field("", alias="primaryFocus")
    quality_bar: str = Field("", alias="qualityBar")
    watch_out_for: str = Field("", alias="watchOutFor")


class ManifestResult(BaseModel):
    model_config = {"populate_by_name": True}

    summary: str = ""
    content_type: str = Field("", alias="contentType")
    main_topics: list[str] = Field([], alias="mainTopics")
    item_counts: ItemCounts = Field(default_factory=ItemCounts, alias="itemCounts")
    sections: list[ManifestSection] = []
    key_names: list[str] = Field([], alias="keyNames")
    flags: ManifestFlags = Field(default_factory=ManifestFlags)

    # Legacy intent (v1 format)
    intent: ManifestIntent = Field(default_factory=ManifestIntent)

    # Video DNA fields (v2 format)
    reasoning: str = ""
    identity: ManifestIdentity = Field(default_factory=ManifestIdentity)
    value: ManifestValue = Field(default_factory=ManifestValue)
    visual_content: ManifestVisualContent = Field(
        default_factory=ManifestVisualContent, alias="visualContent"
    )
    extraction_guidance: ManifestExtractionGuidance = Field(
        default_factory=ManifestExtractionGuidance, alias="extractionGuidance"
    )

    @model_validator(mode="before")
    @classmethod
    def unwrap_nested_format(cls, data):
        """Support multiple manifest formats with backward compatibility.

        Handles:
        1. New video_dna format: {reasoning, identity, value, structure, visualContent, extractionGuidance}
        2. Old nested format: {intent, structure}
        3. Legacy flat format: {summary, contentType, ...}
        """
        if not isinstance(data, dict):
            return data

        # Unwrap "structure" key (shared by v1 and v2 formats)
        if "structure" in data:
            structure = data.pop("structure")
            if isinstance(structure, dict):
                for key, val in structure.items():
                    if key not in data:
                        data[key] = val

        # Map v2 value fields → legacy intent fields for backward compat
        if "value" in data and "intent" not in data:
            v = data.get("value", {})
            if isinstance(v, dict):
                data["intent"] = {
                    "userGoal": v.get("corePromise", ""),
                    "videoValue": v.get("toolPotential", ""),
                    "uniqueAngle": v.get("uniqueAngle", ""),
                    "returnReason": v.get("returnTrigger", ""),
                    "actionableData": v.get("actionableData", []),
                }

        return data


# ── Plan Stage (merged Manifest + Triage) ──


class PlanIdentity(BaseModel):
    """Creator identity from the plan stage."""

    model_config = {"populate_by_name": True}

    creator_type: str = Field("", alias="creatorType")
    tone: str = ""
    audience: str = ""


class PlanExtractionGuidance(BaseModel):
    """Strategic extraction guidance from the plan stage."""

    model_config = {"populate_by_name": True}

    primary_focus: str = Field("", alias="primaryFocus")
    watch_out_for: str = Field("", alias="watchOutFor")


class PlanResult(BaseModel):
    """Merged result of the plan pipeline stage (replaces Manifest + Triage).

    Contains all fields from both ManifestResult and TriageResult in a single
    model. Provides adapter methods for backward compat with downstream consumers.
    """

    model_config = {"populate_by_name": True}

    # Identity & analysis (from manifest)
    reasoning: str = ""
    identity: PlanIdentity = Field(default_factory=PlanIdentity)
    core_promise: str = Field("", alias="corePromise")
    unique_angle: str = Field("", alias="uniqueAngle")
    extraction_guidance: PlanExtractionGuidance = Field(
        default_factory=PlanExtractionGuidance, alias="extractionGuidance"
    )
    item_counts: ItemCounts = Field(default_factory=ItemCounts, alias="itemCounts")

    # Triage fields
    content_tags: list[str] = Field(default_factory=lambda: ["learning"], alias="contentTags")
    modifiers: list[str] = Field(default_factory=list)
    primary_tag: str = Field("learning", alias="primaryTag")
    user_goal: str = Field("General summary of the video content", alias="userGoal")
    tabs: list[dict] = Field(default_factory=list)
    # Tabs the plan stage removed (unregistered dataSource, no sibling) — the
    # assembly phase prepends them to the persisted ``droppedTabs``.
    dropped_tabs: list[dict] = Field(default_factory=list, alias="droppedTabs")
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
    def coerce_tabs(cls, v):
        if not isinstance(v, list):
            return []
        return v

    def to_triage_dict(self) -> dict:
        """Convert to the triage dict shape expected by SSE events and assembly."""
        return {
            "contentTags": self.content_tags,
            "modifiers": self.modifiers,
            "primaryTag": self.primary_tag,
            "userGoal": self.user_goal,
            "tabs": self.tabs,
            "confidence": self.confidence,
        }

    def to_video_context_compact(self) -> str:
        """~300 char summary for extraction/synthesis/enrichment injection."""
        parts: list[str] = []

        ident = self.identity
        if ident.creator_type and ident.tone:
            parts.append(f"Creator: {ident.creator_type} ({ident.tone})")
        elif ident.creator_type:
            parts.append(f"Creator: {ident.creator_type}")

        if self.core_promise:
            parts.append(f"Core promise: {self.core_promise}")

        if self.unique_angle:
            parts.append(f"Unique angle: {self.unique_angle}")

        eg = self.extraction_guidance
        if eg.watch_out_for:
            parts.append(f"Watch out for: {eg.watch_out_for}")
        if eg.primary_focus:
            parts.append(f"Focus: {eg.primary_focus}")

        return "\n".join(parts)

    def to_video_context_full(self) -> str:
        """Full text for logging/debugging."""
        lines: list[str] = []

        if self.reasoning:
            lines.append(f"Reasoning: {self.reasoning[:500]}")

        ident = self.identity
        id_parts = []
        if ident.creator_type:
            id_parts.append(f"type={ident.creator_type}")
        if ident.tone:
            id_parts.append(f"tone={ident.tone}")
        if ident.audience:
            id_parts.append(f"audience={ident.audience}")
        if id_parts:
            lines.append(f"Creator: {', '.join(id_parts)}")

        if self.core_promise:
            lines.append(f"Core promise: {self.core_promise}")
        if self.unique_angle:
            lines.append(f"Unique angle: {self.unique_angle}")

        eg = self.extraction_guidance
        if eg.primary_focus:
            lines.append(f"Extraction focus: {eg.primary_focus}")
        if eg.watch_out_for:
            lines.append(f"Watch out for: {eg.watch_out_for}")

        counts = self.item_counts
        count_parts: list[str] = []
        for field_name in (
            "steps",
            "spots",
            "exercises",
            "ingredients",
            "songs",
            "tips",
            "products",
            "code_snippets",
            "concepts",
            "quotes",
        ):
            v = getattr(counts, field_name, 0)
            if v > 0:
                count_parts.append(f"{v} {field_name}")
        if count_parts:
            lines.append(f"Item counts: {', '.join(count_parts)}")

        lines.append(f"Content tags: {', '.join(self.content_tags)}")
        lines.append(f"User goal: {self.user_goal}")
        lines.append(f"Tabs: {len(self.tabs)}")

        return "\n".join(lines)
