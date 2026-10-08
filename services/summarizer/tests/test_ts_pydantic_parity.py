"""TS ↔ Pydantic field-parity tests (project-score-9 4.2).

The checked-in TS contract (``packages/types/src/vie-response.ts``) is the
source of truth for the frontend data shapes. The Pydantic mirrors in
``domain_types.py`` / ``vie_response_v2.py`` are hand-synced subsets — and
Pydantic SILENTLY DROPS undeclared fields on ``model_dump``, so drift is
invisible at runtime (the shipped connections/group data-loss bug was exactly
this class).

Mechanism: parse the interface field lists straight out of the checked-in
``vie-response.ts`` (the TS file itself is the fixture — it cannot go stale),
build a fixture value for every field, run it through the Pydantic mirror's
``model_validate(...).model_dump(by_alias=True)`` and assert every TS field
survives. Failure modes caught:

- TS adds a field the mirror doesn't declare  → test fails (silent-drop bug).
- A Pydantic field is removed/renamed          → test fails (CI tripwire).

Known, deliberate exceptions are declared per-mapping with the reason
(usually: the field is injected by the assembler AFTER Pydantic validation,
so it never round-trips through the model).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import BaseModel

from src.models import domain_types as dt
from src.models import vie_response_v2 as v2

# ─────────────────────────────────────────────────────
# TS interface parsing
# ─────────────────────────────────────────────────────

_TS_CANDIDATES = (
    # Local dev / CI checkout: repo root relative to this test file.
    Path(__file__).resolve().parent.parent.parent.parent
    / "packages"
    / "types"
    / "src"
    / "vie-response.ts",
)

# Frame-evidence fields are attached by `inject_frame_thumbnails` /
# `_attach_frame_metadata` AFTER domain validation (assembly/core.py), so they
# legitimately have no Pydantic mirror.
_FRAME_EVIDENCE_FIELDS = frozenset(
    {
        "frameCaption",
        "frameEvidence",
        "frameOcr",
        "frameSceneType",
        # Durable S3 key for the attached frame — written by
        # _attach_frame_metadata alongside thumbnailUrl, same post-validation
        # injection path as the caption fields.
        "s3Key",
    }
)

_INTERFACE_RE = re.compile(
    r"export interface (\w+)(?:\s+extends\s+([\w,\s]+))?\s*\{",
    re.MULTILINE,
)
_FIELD_RE = re.compile(r"^\s*(?:readonly\s+)?([A-Za-z_]\w*)\??\s*:", re.MULTILINE)


def _ts_source() -> str:
    for path in _TS_CANDIDATES:
        if path.exists():
            return path.read_text(encoding="utf-8")
    pytest.fail(f"vie-response.ts not found at {_TS_CANDIDATES}")


def _strip_comments(block: str) -> str:
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", block)


@lru_cache(maxsize=1)
def _parse_interfaces() -> dict[str, list[str]]:
    """Parse every `export interface` into {name: [own+inherited field names]}."""
    src = _ts_source()
    raw: dict[str, tuple[list[str], list[str]]] = {}

    for match in _INTERFACE_RE.finditer(src):
        name = match.group(1)
        bases = [b.strip() for b in (match.group(2) or "").split(",") if b.strip()]
        # Brace-match the interface body (fields may nest object literals).
        depth, i = 1, match.end()
        while depth > 0 and i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        body = _strip_comments(src[match.end() : i - 1])
        # Only top-level fields: drop nested object-literal bodies.
        flat, depth2 = [], 0
        for ch in body:
            if ch in "{(<[":
                depth2 += 1
            elif ch in "})>]":
                depth2 -= 1
            elif depth2 == 0:
                flat.append(ch)
        fields = _FIELD_RE.findall("".join(flat))
        raw[name] = (bases, fields)

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> list[str]:
        if name not in raw or name in seen:
            return []
        bases, fields = raw[name]
        inherited: list[str] = []
        for base in bases:
            inherited.extend(resolve(base, seen | {name}))
        combined = inherited + [f for f in fields if f not in inherited]
        return combined

    return {name: resolve(name) for name in raw}


def ts_fields(interface: str) -> set[str]:
    parsed = _parse_interfaces()
    assert interface in parsed, f"interface {interface} not found in vie-response.ts"
    fields = set(parsed[interface])
    assert fields, f"parser extracted zero fields for {interface}"
    return fields


# ─────────────────────────────────────────────────────
# Fixture-value synthesis
# ─────────────────────────────────────────────────────

# Field-name-keyed sample values for fields whose types need specific shapes.
_SAMPLE_OVERRIDES: dict[str, object] = {
    "number": 1,
    "rating": 4.5,
    "contentTags": ["learning"],
    "modifiers": ["finance"],
    "keyTakeaways": ["takeaway"],
    "isRTL": True,
    "degraded": True,
    "timestamp": 42,
    "endTimestamp": 90,
    "seconds": 42,
    "endSeconds": 90,
    "duration": 5,
    "correctIndex": 0,
    "connections": [{"to": "Other Concept", "type": "relatesTo"}],
    "options": ["a", "b"],
    "tags": ["tag"],
    "props": {"k": "v"},
    "crossTabLinks": [{"targetTab": "budget", "label": "Budget"}],
    "attachments": [{"slot": "top", "component": "tip_callout", "props": {}}],
    "slot": "top",
    "size": "banner",
    "status": "verified",
    "tier": "S",
    "x": 50,
    "y": 50,
    "words": [{"text": "hi", "startTime": 0, "endTime": 1}],
    "lines": [{"text": "line"}],
    "positions": [{"player": "GK", "x": 5, "y": 50}],
}

_STRING_DEFAULT = "sample"


def _fixture_for(fields: set[str]) -> dict:
    return {f: _SAMPLE_OVERRIDES.get(f, _STRING_DEFAULT) for f in fields}


# ─────────────────────────────────────────────────────
# The mapping under test
# ─────────────────────────────────────────────────────


class Mapping:
    def __init__(
        self,
        ts_name: str,
        model: type[BaseModel],
        *,
        exceptions: dict[str, str] | None = None,
    ) -> None:
        self.ts_name = ts_name
        self.model = model
        # field -> reason it is deliberately absent from the Pydantic mirror
        self.exceptions = dict(exceptions or {})


_ASSEMBLER_INJECTED = "attached by the assembler AFTER Pydantic validation"

ITEM_MAPPINGS = [
    Mapping("TabEntry", v2.TabEntry),
    Mapping("TabAttachment", v2.TabAttachment),
    Mapping("CrossTabLinkDef", v2.CrossTabLink),
    Mapping(
        "SpotItem",
        dt.TravelSpot,
        exceptions={f: _ASSEMBLER_INJECTED for f in _FRAME_EVIDENCE_FIELDS},
    ),
    Mapping(
        "StepItem",
        dt.FoodStep,
        exceptions={f: _ASSEMBLER_INJECTED for f in _FRAME_EVIDENCE_FIELDS},
    ),
    Mapping(
        "StepItem",
        dt.ProjectStep,
        exceptions={f: _ASSEMBLER_INJECTED for f in _FRAME_EVIDENCE_FIELDS},
    ),
    Mapping(
        "ConceptItem",
        dt.LearningConcept,
        exceptions={
            **{f: _ASSEMBLER_INJECTED for f in _FRAME_EVIDENCE_FIELDS},
            "thumbnailUrl": _ASSEMBLER_INJECTED,
        },
    ),
    Mapping("KeyPointItem", dt.LearningKeyPoint),
    Mapping(
        "ClaimItem",
        dt.NewsClaim,
        exceptions={f: _ASSEMBLER_INJECTED for f in _FRAME_EVIDENCE_FIELDS},
    ),
    Mapping("TierListItem", dt.GamingRanking),
    Mapping("FormationPosition", dt.SportPosition),
    Mapping("TipItem", dt.FoodTip),
    Mapping("TravelPackingItem", dt.TravelPackingItem),
    Mapping("TravelTip", dt.TravelTip),
    Mapping("FoodIngredient", dt.FoodIngredient),
    Mapping("TechSnippet", dt.TechSnippet),
    Mapping("TechPattern", dt.TechPattern),
    Mapping("TechCheatSheetItem", dt.TechCheatSheetItem),
    Mapping("FitnessExercise", dt.FitnessExercise),
    Mapping("MusicCredit", dt.MusicCredit),
    Mapping("MusicSection", dt.MusicSection),
    Mapping("MusicAnalysisItem", dt.MusicAnalysisItem),
    Mapping("ReviewRating", dt.ReviewRating),
    Mapping("ReviewSpec", dt.ReviewSpec),
    Mapping("ReviewComparison", dt.ReviewComparison),
    Mapping("ReviewVerdict", dt.ReviewVerdict),
    Mapping("ProjectMaterial", dt.ProjectMaterial),
    Mapping("ProjectTool", dt.ProjectTool),
    Mapping("PodcastSegment", dt.PodcastSegment),
    Mapping("PodcastGuest", dt.PodcastGuest),
    Mapping("PodcastQuote", dt.PodcastQuote),
    Mapping("PodcastTopic", dt.PodcastTopic),
    Mapping("NewsTimelineEvent", dt.NewsTimelineEvent),
    Mapping("NewsEntity", dt.NewsEntity),
    Mapping("NewsContextItem", dt.NewsContextItem),
    Mapping("GamingHighlight", dt.GamingHighlight),
    Mapping("GamingLoadoutItem", dt.GamingLoadoutItem),
    Mapping("GamingWalkthroughStep", dt.GamingWalkthroughStep),
    Mapping("SportMatchEvent", dt.SportMatchEvent),
    Mapping("SportFormation", dt.SportFormation),
    Mapping("SportStatRow", dt.SportStatRow),
    Mapping("SportStatComparison", dt.SportStatComparison),
    Mapping("NarrativeKeyMoment", dt.NarrativeKeyMoment),
    Mapping("NarrativeQuote", dt.NarrativeQuote),
]

# Domain envelopes: assert top-level field coverage (children covered above).
DOMAIN_MAPPINGS = [
    # Both Pydantic VIEResponseMeta mirrors (v2 + legacy domain_types) must
    # carry every TS field — `degraded` (3.6) was the first post-audit add.
    Mapping("VIEResponseMeta", v2.VIEResponseMeta),
    Mapping("VIEResponseMeta", dt.VIEResponseMeta),
    Mapping("TravelData", dt.TravelData),
    Mapping("FoodData", dt.FoodData),
    Mapping("FoodMeta", dt.FoodMeta),
    Mapping("TechData", dt.TechData),
    Mapping("TechSetup", dt.TechSetup),
    Mapping("FitnessData", dt.FitnessData),
    Mapping("MusicData", dt.MusicData),
    Mapping(
        "LearningData",
        dt.LearningData,
        exceptions={
            "timestamps": "shape drift is known: TS MomentItem[] vs Pydantic "
            "LearningTimestamp[]; moments are normalized by the "
            "assembler, not the domain model",
        },
    ),
    Mapping("ReviewData", dt.ReviewData),
    Mapping("ProjectData", dt.ProjectData),
    Mapping("PodcastData", dt.PodcastData),
    Mapping("NewsData", dt.NewsData),
    Mapping("GamingData", dt.GamingData),
    Mapping("SportData", dt.SportData),
    Mapping("NarrativeData", dt.NarrativeData),
    Mapping("FinanceData", dt.FinanceData),
]

_ALL = ITEM_MAPPINGS + DOMAIN_MAPPINGS
_IDS = [f"{m.ts_name}->{m.model.__name__}" for m in _ALL]

_LIST_FIELD_SAMPLES: dict[str, object] = {
    # Container fields on domain envelopes get typed child samples so
    # model_validate accepts them; scalar leaves come from _SAMPLE_OVERRIDES.
    "itinerary": [{"day": 1, "spots": [{"name": "Spot", "description": "d"}], "tips": []}],
    "budget": {"total": 100, "currency": "USD", "breakdown": []},
    "packingList": [{"item": "Socks", "category": "Clothes", "essential": True}],
    "accommodationTips": [{"text": "t", "type": "tip"}],
    "transportationTips": [{"text": "t", "type": "tip"}],
    "meta": {},
    "ingredients": [{"name": "Salt", "amount": 1, "displayAmount": "1 tsp"}],
    "steps": [{"number": 1, "instruction": "Do it"}],
    "tips": [{"type": "tip", "text": "t"}],
    "substitutions": [{"original": "a", "substitute": "b"}],
    "nutrition": [{"nutrient": "Protein", "amount": "10", "unit": "g"}],
    "equipment": ["Pan"],
    "languages": ["python"],
    "frameworks": ["fastapi"],
    "topics": ["testing"],
    "setup": {},
    "snippets": [{"language": "py", "code": "x", "explanation": "e"}],
    "patterns": [{"title": "t"}],
    "cheatSheet": [{"title": "t", "code": "c"}],
    "commands": ["pip install"],
    "dependencies": [{"name": "fastapi"}],
    "envVars": [{"name": "KEY", "description": "d"}],
    "warmup": [],
    "exercises": [{"name": "Squat", "emoji": "🏋️", "formCues": [], "modifications": []}],
    "cooldown": [],
    "timer": {"intervals": [], "rounds": 1},
    "credits": [{"role": "Producer", "name": "X"}],
    "analysis": [{"aspect": "Rhythm", "emoji": "🥁", "detail": "d"}],
    "structure": [{"name": "Chorus"}],
    "lyrics": [{"line": "la"}],
    "themes": ["love"],
    "genre": ["pop"],
    "keyPoints": [{"title": "K", "detail": "d"}],
    "concepts": [{"name": "C", "definition": "d"}],
    "takeaways": ["t"],
    "pros": ["fast"],
    "cons": ["pricey"],
    "specs": [{"key": "k", "value": "v"}],
    "comparisons": [{"feature": "f", "thisProduct": "a", "competitor": "b", "competitorName": "X"}],
    "verdict": {"badge": "recommended", "bottomLine": "b"},
    "materials": [{"name": "Wood"}],
    "tools": [{"name": "Saw", "required": True}],
    "safetyWarnings": ["Careful"],
    "segments": [{"title": "Intro"}],
    "guests": [{"name": "Guest"}],
    "quotes": [{"quote": "q"}, {"text": "q", "speaker": "s"}],
    "storyTimeline": [{"label": "Event"}],
    "entities": [{"name": "Entity"}],
    "claims": [{"claim": "c", "source": "s", "status": "verified"}],
    "context": [{"key": "k", "value": "v"}],
    "highlights": [{"label": "h"}],
    "loadout": [{"item": "Sword"}],
    "walkthrough": [{"instruction": "Go left"}],
    "rankings": [{"item": "Hero", "tier": "S"}],
    "matchEvents": [{"label": "Goal"}],
    "formation": {"name": "4-4-2", "positions": []},
    "statComparison": {"comparisons": []},
    "keyMoments": [{"timestamp": 1, "description": "d"}],
    "costs": [{"item": "Hotel", "amount": 100, "currency": "USD", "category": "stay"}],
    "savingTips": ["book early"],
    "subScores": [{"category": "c", "score": 8}],
    "bestFor": ["students"],
    "notFor": ["pros"],
    "muscleGroups": ["legs"],
    "intervals": [{"name": "Work", "duration": 30, "type": "work"}],
    "rounds": 2,
    "modifications": [{"label": "Easier", "description": "d"}],
    "formCues": ["back straight"],
    "sets": 3,
    "servings": 2,
    "prepTime": 10,
    "cookTime": 20,
    "totalTime": 30,
    "caloriesBurned": 200,
    "total": 100,
    "amount": 1.5,
    "score": 8,
    "maxScore": 10,
    "essential": True,
    "required": True,
    "winner": "left",
    "confidence": 0.9,
    "day": 1,
}


def _domain_fixture_for(fields: set[str]) -> dict:
    fixture: dict = {}
    for f in fields:
        if f in _LIST_FIELD_SAMPLES:
            fixture[f] = _LIST_FIELD_SAMPLES[f]
        elif f in _SAMPLE_OVERRIDES:
            fixture[f] = _SAMPLE_OVERRIDES[f]
        else:
            fixture[f] = _STRING_DEFAULT
    return fixture


# Per-model overrides for fields whose sample type differs by model
# (`quotes`: PodcastData wants {quote} vs NarrativeData {text}; `tips`/`duration`
# are strings on item models but lists/ints elsewhere; etc.).
_PER_MODEL_FIELD_SAMPLES: dict[str, dict[str, object]] = {
    "PodcastData": {"quotes": [{"quote": "q"}]},
    "NarrativeData": {"quotes": [{"text": "q", "speaker": "s"}]},
    "NarrativeQuote": {"context": "said during the interview"},
    "NewsData": {"context": [{"key": "k", "value": "v"}]},
    "FitnessData": {"meta": {"type": "hiit", "difficulty": "beginner", "duration": 30}},
    "FitnessExercise": {"duration": "30s"},
    "ReviewData": {"rating": {"score": 8, "maxScore": 10, "label": "great"}},
    "MusicSection": {"duration": 60},
    "FoodIngredient": {"amount": 1.5},
    "ReviewRating": {"label": "great"},
    "SportFormation": {"positions": [{"player": "GK", "x": 5, "y": 50}]},
    "TravelSpot": {"duration": "1-2 hours", "tips": "go early", "specs": "Open 6-17"},
    "FoodStep": {"tips": "pat dry first"},
    "ProjectStep": {"duration": "10 min", "tips": "measure twice"},
    "PodcastTopic": {},
}

# PodcastData.topics is a list of PodcastTopic, unlike TechData.topics (str[]).
_PER_MODEL_FIELD_SAMPLES["PodcastData"]["topics"] = [{"topic": "AI", "detail": "d"}]


@pytest.mark.parametrize("mapping", _ALL, ids=_IDS)
def test_every_ts_field_survives_pydantic_round_trip(mapping: Mapping):
    fields = ts_fields(mapping.ts_name)
    expected = fields - set(mapping.exceptions)

    fixture = _domain_fixture_for(expected)
    fixture.update(_PER_MODEL_FIELD_SAMPLES.get(mapping.model.__name__, {}))
    fixture = {k: v for k, v in fixture.items() if k in expected}

    instance = mapping.model.model_validate(fixture)
    dumped = instance.model_dump(by_alias=True)

    dropped = sorted(f for f in expected if f not in dumped)
    assert not dropped, (
        f"{mapping.model.__name__} silently drops TS "
        f"{mapping.ts_name} field(s): {dropped} — declare them on the model "
        f"(or register a documented exception in test_ts_pydantic_parity.py)"
    )


def test_declared_exceptions_are_still_ts_fields():
    """An exception for a field TS no longer declares is stale — prune it."""
    for mapping in _ALL:
        fields = ts_fields(mapping.ts_name)
        stale = set(mapping.exceptions) - fields - _FRAME_EVIDENCE_FIELDS
        assert not stale, f"stale parity exceptions for {mapping.ts_name}: {sorted(stale)}"


def test_parser_sees_expected_interfaces():
    """Canary for the regex parser itself — if vie-response.ts is restructured
    and the parser goes blind, fail loudly instead of vacuously passing."""
    parsed = _parse_interfaces()
    assert len(parsed) > 40
    assert ts_fields("SpotItem") >= {"name", "currency", "bookingSearch", "rating"}
    # extends-resolution: SpotItem inherits FrameEvidence fields
    assert _FRAME_EVIDENCE_FIELDS <= ts_fields("SpotItem")
