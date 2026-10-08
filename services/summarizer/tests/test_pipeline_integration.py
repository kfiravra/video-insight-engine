"""Integration tests: full pipeline (triage -> extract -> enrich -> synthesize) for all domains.

Each test mocks the LLM to return realistic JSON for its domain,
then verifies the full chain produces valid data and correct events.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.domain_types import validate_domain_output
from src.models.pipeline_types import EnrichmentData, SynthesisResult
from src.services.pipeline.enrichment import enrich_quiz
from src.services.pipeline.extractor import extract
from src.services.pipeline.synthesis import synthesize
from src.services.pipeline.triage import TriageResult

# ─────────────────────────────────────────────────────────────────────────────
# LLM response fixtures per content tag
# ─────────────────────────────────────────────────────────────────────────────

_TRIAGE_RESPONSES = {
    "learning": {
        "contentTags": ["learning"],
        "primaryTag": "learning",
        "confidence": 0.95,
        "userGoal": "Understand the topic",
        "modifiers": [],
        "tabs": [
            {
                "id": "key_points",
                "label": "Key Points",
                "emoji": "💡",
                "dataSource": "learning.keyPoints",
            }
        ],
    },
    "food": {
        "contentTags": ["food"],
        "primaryTag": "food",
        "confidence": 0.98,
        "userGoal": "Learn a recipe",
        "modifiers": [],
        "tabs": [
            {
                "id": "ingredients",
                "label": "Ingredients",
                "emoji": "🛒",
                "dataSource": "food.ingredients",
            }
        ],
    },
    "tech": {
        "contentTags": ["tech"],
        "primaryTag": "tech",
        "confidence": 0.92,
        "userGoal": "Learn coding",
        "modifiers": [],
        "tabs": [{"id": "code", "label": "Code", "emoji": "📝", "dataSource": "tech.snippets"}],
    },
    "travel": {
        "contentTags": ["travel"],
        "primaryTag": "travel",
        "confidence": 0.90,
        "userGoal": "Plan a trip",
        "modifiers": [],
        "tabs": [
            {
                "id": "itinerary",
                "label": "Itinerary",
                "emoji": "🗺️",
                "dataSource": "travel.itinerary",
            }
        ],
    },
    "fitness": {
        "contentTags": ["fitness"],
        "primaryTag": "fitness",
        "confidence": 0.91,
        "userGoal": "Follow a workout",
        "modifiers": [],
        "tabs": [
            {
                "id": "exercises",
                "label": "Exercises",
                "emoji": "🏋️",
                "dataSource": "fitness.exercises",
            }
        ],
    },
    "review": {
        "contentTags": ["review"],
        "primaryTag": "review",
        "confidence": 0.94,
        "userGoal": "Evaluate a product",
        "modifiers": [],
        "tabs": [
            {"id": "verdict", "label": "Verdict", "emoji": "🏆", "dataSource": "review.verdict"}
        ],
    },
    "music": {
        "contentTags": ["music"],
        "primaryTag": "music",
        "confidence": 0.89,
        "userGoal": "Analyze music",
        "modifiers": [],
        "tabs": [
            {"id": "analysis", "label": "Analysis", "emoji": "🎼", "dataSource": "music.analysis"}
        ],
    },
    "project": {
        "contentTags": ["project"],
        "primaryTag": "project",
        "confidence": 0.91,
        "userGoal": "Build a project",
        "modifiers": [],
        "tabs": [{"id": "steps", "label": "Steps", "emoji": "📝", "dataSource": "project.steps"}],
    },
}

_EXTRACTION_RESPONSES: dict[str, dict] = {
    "learning": {
        "keyPoints": [
            {"emoji": "💡", "title": "Core Idea", "detail": "The main concept explained"},
            {"emoji": "🔑", "title": "Key Insight", "detail": "An important realization"},
        ],
        "concepts": [
            {"name": "Entropy", "definition": "Measure of disorder", "emoji": "🌪️"},
        ],
        "takeaways": ["Understand entropy", "Apply to daily decisions"],
        "timestamps": [{"time": "2:30", "seconds": 150, "label": "Introduction to entropy"}],
    },
    "food": {
        "meta": {"prepTime": 15, "cookTime": 30, "servings": 4, "difficulty": "easy"},
        "ingredients": [
            {"name": "Pasta", "amount": "500", "unit": "g"},
            {"name": "Garlic", "amount": "4", "unit": "cloves"},
        ],
        "steps": [
            {"number": 1, "instruction": "Boil water and cook pasta"},
            {"number": 2, "instruction": "Saute garlic in olive oil"},
        ],
        "tips": [{"text": "Use extra virgin olive oil", "type": "chef_tip"}],
        "equipment": ["Large pot", "Frying pan"],
    },
    "tech": {
        "languages": ["Python", "TypeScript"],
        "frameworks": ["FastAPI", "React"],
        "concepts": ["REST API", "Component architecture"],
        "setup": {
            "commands": ["pip install fastapi"],
            "dependencies": [{"name": "fastapi"}, {"name": "uvicorn"}],
            "envVars": [{"name": "API_KEY", "description": "API authentication key"}],
        },
        "snippets": [
            {
                "language": "python",
                "code": "app = FastAPI()",
                "explanation": "Create the FastAPI app",
            },
        ],
    },
    "travel": {
        "itinerary": [
            {
                "day": 1,
                "theme": "Arrival",
                "spots": [
                    {"name": "Senso-ji Temple", "emoji": "⛩️", "description": "Historic temple"},
                ],
            },
        ],
        "budget": {
            "total": 2500,
            "currency": "USD",
            "breakdown": [
                {"category": "Accommodation", "amount": 800, "currency": "USD"},
            ],
        },
        "packingList": [{"item": "Passport", "category": "Essentials", "essential": True}],
    },
    "fitness": {
        "meta": {
            "type": "HIIT",
            "difficulty": "intermediate",
            "duration": 30,
            "muscleGroups": ["legs", "core"],
        },
        "exercises": [
            {"name": "Squats", "emoji": "🏋️", "sets": 3, "reps": "15"},
            {"name": "Push-ups", "emoji": "💪", "sets": 3, "reps": "12"},
        ],
        "warmup": [{"name": "Jumping Jacks", "duration": "2 min"}],
        "cooldown": [{"name": "Stretching", "duration": "5 min"}],
        "tips": [{"text": "Keep your core engaged", "type": "form"}],
    },
    "review": {
        "product": "Sony WH-1000XM5",
        "price": "$349",
        "rating": {"score": 8.5, "maxScore": 10, "label": "Excellent"},
        "pros": ["Best-in-class ANC", "Comfortable fit"],
        "cons": ["No waterproofing"],
        "verdict": {
            "badge": "recommended",
            "bestFor": ["Commuters"],
            "notFor": ["Athletes"],
            "bottomLine": "Best noise-cancelling headphones",
        },
    },
    "music": {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "genre": ["Rock", "Opera"],
        "analysis": [
            {
                "aspect": "Production",
                "emoji": "🎛️",
                "detail": "A groundbreaking composition with multi-track layering",
            },
        ],
        "structure": [
            {"name": "Intro/Ballad", "description": "Piano-driven opening"},
        ],
    },
    "project": {
        "projectName": "Floating Shelf",
        "difficulty": "beginner",
        "estimatedTime": "2 hours",
        "materials": [
            {"name": "Pine Board", "quantity": "1"},
        ],
        "tools": [
            {"name": "Drill", "required": True},
        ],
        "steps": [
            {
                "number": 1,
                "title": "Measure and Mark",
                "instruction": "Mark the wall at desired height",
            },
        ],
        "safetyWarnings": ["Always find wall studs"],
    },
}

_SYNTHESIS_RESPONSE = {
    "tldr": "A comprehensive overview of the topic",
    "keyTakeaways": ["First key point", "Second key point", "Third key point"],
    "masterSummary": "This video covers the subject in depth.",
    "seoDescription": "Learn about the topic with this comprehensive video summary",
}

_ENRICHMENT_STUDY = {
    "quiz": [
        {
            "question": "What is a qubit?",
            "options": ["A classical bit", "A quantum bit", "A byte", "A register"],
            "correctIndex": 1,
            "explanation": "A qubit is the quantum equivalent of a classical bit",
        },
        {
            "question": "What does superposition let a qubit do?",
            "options": ["Hold several states at once", "Store a byte", "Run faster", "Skip errors"],
            "correctIndex": 0,
            "explanation": "Superposition is being in multiple states simultaneously",
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """Mock LLM service with configurable responses.

    Both call_llm and call_llm_fast share the same AsyncMock so tests that
    set return_value work regardless of which method the production code uses.
    """
    service = MagicMock()
    shared_mock = AsyncMock()
    service.call_llm = shared_mock
    service.call_llm_fast = shared_mock
    service.model = "anthropic/claude-sonnet-4-6"
    service.fast_model = "anthropic/claude-haiku-4-5-20251001"
    return service


def _make_video_data(title: str = "Test Video", duration: int = 600):
    return {"title": title, "channel": "Test Channel", "duration": duration}


def _make_triage(tag: str) -> TriageResult:
    """Build a TriageResult for testing."""
    triage_data = _TRIAGE_RESPONSES[tag]
    return TriageResult(
        content_tags=triage_data["contentTags"],
        modifiers=triage_data.get("modifiers", []),
        primary_tag=triage_data["primaryTag"],
        user_goal=triage_data["userGoal"],
        tabs=triage_data["tabs"],
        confidence=triage_data["confidence"],
    )


_TRANSCRIPT = "This is a test transcript with enough words to pass basic validation. " * 50


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPipelineIntegration:
    """Full pipeline integration for all 8 domains."""

    @pytest.mark.parametrize("tag", list(_EXTRACTION_RESPONSES.keys()))
    async def test_extract_validate_for_all_tags(self, mock_llm, tag):
        """Test extraction + domain validation for each content tag."""
        # 1. Triage decision (plan-stage output carried as TriageResult)
        triage = _make_triage(tag)
        assert isinstance(triage, TriageResult)
        assert triage.primary_tag == tag

        # 2. Extraction
        mock_llm.call_llm.return_value = json.dumps(_EXTRACTION_RESPONSES[tag])
        events = []
        async for evt in extract(mock_llm, triage, _TRANSCRIPT, _make_video_data()):
            events.append(evt)

        # Must have progress events and extraction_complete
        progress_events = [e for e in events if e["event"] == "extraction_progress"]
        assert len(progress_events) >= 2

        complete_event = next(e for e in events if e["event"] == "extraction_complete")

        # Data must be a valid dict with the domain key
        data = complete_event["data"]
        assert isinstance(data, dict)
        assert tag in data

    @pytest.mark.parametrize("tag", list(_EXTRACTION_RESPONSES.keys()))
    async def test_synthesis_for_all_tags(self, mock_llm, tag):
        """Test synthesis produces valid SynthesisResult for each tag."""
        mock_llm.call_llm.return_value = json.dumps(_SYNTHESIS_RESPONSE)

        extraction_summary = json.dumps(_EXTRACTION_RESPONSES[tag])[:2000]
        result = await synthesize(
            mock_llm, "Test Video", "Test Channel", 600, tag, extraction_summary
        )

        assert isinstance(result, SynthesisResult)
        assert result.tldr
        assert len(result.key_takeaways) >= 1
        assert result.master_summary

    async def test_enrichment_learning(self, mock_llm):
        """Enrichment for a learning video writes the quiz and nothing else."""
        mock_llm.call_llm.return_value = json.dumps(_ENRICHMENT_STUDY)

        result = await enrich_quiz(
            mock_llm,
            primary_tag="learning",
            extraction_data=_EXTRACTION_RESPONSES["learning"],
            video_memory="",
            tabs=[],
        )

        assert isinstance(result, EnrichmentData)
        assert len(result.quiz) == 2

    @pytest.mark.parametrize("tag", ["food", "fitness", "review", "music", "travel", "project"])
    async def test_quiz_is_not_written_for_quiz_forbidden_domains(self, mock_llm, tag):
        """Domains that forbid quiz_arena get no quiz call at all."""
        mock_llm.call_llm.return_value = json.dumps(_ENRICHMENT_STUDY)

        result = await enrich_quiz(
            mock_llm, primary_tag=tag, extraction_data={}, video_memory="", tabs=[]
        )

        assert result is None
        mock_llm.call_llm.assert_not_called()


class TestPipelineEdgeCases:
    """Edge cases for the pipeline."""

    async def test_extraction_with_null_fields_in_fitness(self, mock_llm):
        """Test extraction handles LLM returning null for fitness fields."""
        response = {
            "meta": {"type": None, "difficulty": None, "duration": None, "muscleGroups": []},
            "exercises": [{"name": "Push-ups", "emoji": "💪"}],
        }
        mock_llm.call_llm.return_value = json.dumps(response)
        triage = _make_triage("fitness")

        events = []
        async for evt in extract(mock_llm, triage, _TRANSCRIPT, _make_video_data()):
            events.append(evt)

        complete = next(e for e in events if e["event"] == "extraction_complete")
        fitness_data = complete["data"]["fitness"]
        assert fitness_data["meta"]["type"] == "general"
        assert fitness_data["meta"]["difficulty"] == "intermediate"

    async def test_extraction_with_null_fields_in_review(self, mock_llm):
        """Test extraction handles LLM returning null for review rating fields."""
        response = {
            "product": "Test Product",
            "rating": {"score": None, "maxScore": None, "label": "N/A"},
            "pros": ["Good"],
            "cons": ["Bad"],
            "verdict": {"badge": "invalid", "bestFor": [], "notFor": [], "bottomLine": "OK"},
        }
        mock_llm.call_llm.return_value = json.dumps(response)
        triage = _make_triage("review")

        events = []
        async for evt in extract(mock_llm, triage, _TRANSCRIPT, _make_video_data()):
            events.append(evt)

        complete = next(e for e in events if e["event"] == "extraction_complete")
        review_data = complete["data"]["review"]
        assert review_data["rating"]["score"] == 0.0
        assert review_data["rating"]["maxScore"] == 10.0
        assert review_data["verdict"]["badge"] == "recommended"

    async def test_synthesis_handles_truncated_extraction(self, mock_llm):
        """Test synthesis works with very large extraction data."""
        mock_llm.call_llm.return_value = json.dumps(_SYNTHESIS_RESPONSE)
        large_extraction = json.dumps({"data": "x" * 10000})

        result = await synthesize(
            mock_llm, "Long Video", "Channel", 7200, "learning", large_extraction
        )
        assert isinstance(result, SynthesisResult)
        assert result.tldr

    async def test_enrichment_failure_returns_none(self, mock_llm):
        """Test that enrichment gracefully returns None on LLM error."""
        mock_llm.call_llm.side_effect = Exception("LLM timeout")

        result = await enrich_quiz(
            mock_llm, primary_tag="learning", extraction_data={}, video_memory="", tabs=[]
        )
        assert result is None


class TestValidateDomainOutput:
    """Tests for validate_domain_output."""

    def test_single_tag_validates(self):
        data = {
            "keyPoints": [{"emoji": "💡", "title": "Point", "detail": "Detail"}],
            "concepts": [],
            "takeaways": ["Takeaway"],
        }
        result = validate_domain_output(["learning"], [], data)
        assert "learning" in result
        assert len(result["learning"]["keyPoints"]) == 1

    def test_multi_tag_with_wrappers(self):
        data = {
            "techData": {
                "languages": ["Python"],
                "frameworks": [],
                "concepts": [],
                "setup": {"commands": []},
                "snippets": [],
            },
            "learningData": {
                "keyPoints": [{"emoji": "💡", "title": "Point", "detail": "Detail"}],
                "concepts": [],
                "takeaways": [],
            },
        }
        result = validate_domain_output(["tech", "learning"], [], data)
        assert "tech" in result
        assert "learning" in result

    def test_modifier_validation(self):
        data = {
            "keyPoints": [{"emoji": "💡", "title": "Point", "detail": "Detail"}],
            "concepts": [],
            "takeaways": [],
            "narrativeData": {
                "keyMoments": [{"description": "A moment", "mood": "happy"}],
                "quotes": [],
                "takeaways": [],
            },
        }
        result = validate_domain_output(["learning"], ["narrative"], data)
        assert "learning" in result
        assert "narrative" in result

    def test_empty_data_returns_empty(self):
        result = validate_domain_output(["learning"], [], {})
        # Empty data still creates a valid learning object with defaults
        assert "learning" in result


class TestTranscriptLengthEdgeCases:
    """Edge cases for very short, very long, and empty transcripts."""

    async def test_very_short_transcript(self, mock_llm):
        """Very short video with minimal transcript still extracts."""
        short_transcript = "Hello and welcome. Today I explain entropy briefly. Goodbye."
        mock_llm.call_llm.return_value = json.dumps(_EXTRACTION_RESPONSES["learning"])
        triage = _make_triage("learning")

        events = []
        async for evt in extract(mock_llm, triage, short_transcript, _make_video_data(duration=90)):
            events.append(evt)

        complete = next(e for e in events if e["event"] == "extraction_complete")
        assert "learning" in complete["data"]
        assert mock_llm.call_llm.call_count == 1

    async def test_empty_transcript(self, mock_llm):
        """Empty transcript still attempts extraction."""
        mock_llm.call_llm.return_value = json.dumps(_EXTRACTION_RESPONSES["learning"])
        triage = _make_triage("learning")

        events = []
        async for evt in extract(mock_llm, triage, "", _make_video_data()):
            events.append(evt)

        complete = next(e for e in events if e["event"] == "extraction_complete")
        assert "learning" in complete["data"]

    async def test_synthesis_with_zero_duration(self, mock_llm):
        """Synthesis handles zero duration gracefully."""
        mock_llm.call_llm.return_value = json.dumps(_SYNTHESIS_RESPONSE)

        result = await synthesize(mock_llm, "Live Stream", "Channel", 0, "learning", "Some data")
        assert isinstance(result, SynthesisResult)

    async def test_synthesis_with_none_duration(self, mock_llm):
        """Synthesis handles None duration gracefully."""
        mock_llm.call_llm.return_value = json.dumps(_SYNTHESIS_RESPONSE)

        result = await synthesize(mock_llm, "Live Stream", "Channel", None, "music", "Some data")
        assert isinstance(result, SynthesisResult)
