"""Pytest fixtures for summarizer tests."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import settings as _settings
from src.models.schemas import ProcessingStatus, ErrorCode
from src.services.media import download_utils as _download_utils

# Names of every per-stage override on the Settings object. Tests never want
# these to fire — they would wrap a MagicMock with a real ``LLMService`` and
# trigger live API calls. Kept in sync with config.Settings._STAGE_TO_SETTING.
_STAGE_OVERRIDE_ATTRS = (
    "LLM_CLASSIFIER_MODEL",
    "LLM_CHAPTER_DETECT_MODEL",
    "LLM_DESCRIPTION_MODEL",
    "LLM_SYNTHESIS_MODEL",
    "LLM_ENRICHMENT_MODEL",
    "LLM_EXTRACTION_MODEL",
    "LLM_TRANSLATION_MODEL",
    "LLM_VISION_MODEL",
)


@pytest.fixture(autouse=True)
def _disable_stage_model_overrides(monkeypatch):
    """Force every per-stage LLM override to None for the duration of the test.

    `src/config.py` pins `LLM_CLASSIFIER_MODEL` and `LLM_ENRICHMENT_MODEL` to
    Haiku 4.5, and the env can pin any other stage (prod pins extraction).
    Without this fixture, any test that calls a stage function with a
    `MagicMock` for `llm_service` would trigger `call_llm_with_retry` to
    replace the mock with a real `LLMService` and make a live API call.
    """
    for attr in _STAGE_OVERRIDE_ATTRS:
        monkeypatch.setattr(_settings, attr, None)


@pytest.fixture(autouse=True)
def _fresh_exit_memory(monkeypatch):
    """Each test starts with no round-robin assignments and no blocked proxy exits.

    The exit bookkeeping (download_utils._ExitMemory) is per-process state.
    """
    monkeypatch.setattr(_download_utils, "_EXIT_MEMORY", _download_utils._ExitMemory())


def _utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


# ============================================================
# Mock MongoDB Client and Database
# ============================================================


@pytest.fixture
def mock_mongo_client():
    """Mock MongoDB client for testing.

    Usage:
        def test_something(mock_mongo_client):
            db = mock_mongo_client.get_default_database()
            collection = db.videoSummaryCache
    """
    client = MagicMock()
    database = MagicMock()
    collection = MagicMock()

    # Setup client -> database -> collection chain
    client.get_default_database.return_value = database
    database.videoSummaryCache = collection
    database.__getitem__ = MagicMock(return_value=collection)

    # Setup common collection operations
    collection.find_one.return_value = None
    collection.find.return_value = MagicMock()
    collection.insert_one.return_value = MagicMock(inserted_id="mock-id")
    collection.update_one.return_value = MagicMock(modified_count=1)
    collection.delete_one.return_value = MagicMock(deleted_count=1)
    collection.find_one_and_update.return_value = {"retryCount": 1}

    return client


@pytest.fixture
def mock_database(mock_mongo_client):
    """Mock MongoDB database for testing."""
    return mock_mongo_client.get_default_database()


@pytest.fixture
def mock_collection(mock_database):
    """Mock MongoDB collection for testing."""
    return mock_database.videoSummaryCache


# ============================================================
# Mock HTTP Client
# ============================================================


@pytest.fixture
def mock_http_client():
    """Mock async HTTP client for testing external API calls.

    Usage:
        async def test_external_api(mock_http_client):
            mock_http_client.get.return_value = AsyncMock(
                status_code=200,
                json=AsyncMock(return_value={"data": "test"})
            )
    """
    client = AsyncMock()

    # Default successful response
    response = AsyncMock()
    response.status_code = 200
    response.json = AsyncMock(return_value={})
    response.text = ""
    response.raise_for_status = MagicMock()

    # Setup HTTP methods
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.put = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)

    # Support async context manager
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    return client


# ============================================================
# Mock LLM Provider and Service
# ============================================================


@pytest.fixture
def mock_llm_provider():
    """Mock LLMProvider for testing.

    The provider is the low-level abstraction for LLM calls via LiteLLM.
    """
    from src.services.llm_provider import LLMProvider

    provider = MagicMock(spec=LLMProvider)
    provider.model = "anthropic/claude-sonnet-4-6"

    # Default mock for complete() - can be overridden per test
    provider.complete = AsyncMock(
        return_value='{"sections": [{"title": "Test", "startSeconds": 0, "endSeconds": 60}]}'
    )

    # Default mock for stream() - can be overridden per test
    async def mock_stream(*args, **kwargs):
        yield '{"sections": [{"title": "Test", "startSeconds": 0, "endSeconds": 60}]}'

    provider.stream = mock_stream

    return provider


@pytest.fixture
def mock_llm_service(mock_llm_provider):
    """Mock LLM service for testing.

    The service is the high-level abstraction that uses LLMProvider
    to perform video summarization tasks.
    """
    from src.services.llm import LLMService

    service = MagicMock(spec=LLMService)
    service._provider = mock_llm_provider

    # Mock process_video - the main entry point
    service.process_video = AsyncMock(
        return_value={
            "tldr": "Test TLDR summary",
            "key_takeaways": ["Takeaway 1", "Takeaway 2"],
            "sections": [
                {
                    "id": "section-1",
                    "title": "Introduction",
                    "timestamp": "00:00",
                    "startSeconds": 0,
                    "endSeconds": 60,
                    "content": [
                        {"type": "paragraph", "text": "Introduction section summary"},
                        {"type": "bullets", "items": ["Point 1", "Point 2"]},
                    ],
                }
            ],
            "concepts": [
                {
                    "id": "concept-1",
                    "name": "Test Concept",
                    "definition": "A concept used for testing",
                }
            ],
        }
    )

    # Mock individual steps
    service.detect_sections = AsyncMock(
        return_value=[{"title": "Introduction", "startSeconds": 0, "endSeconds": 60}]
    )
    service.summarize_section = AsyncMock(
        return_value={
            "content": [
                {"type": "paragraph", "text": "Section summary"},
                {"type": "bullets", "items": ["Bullet 1", "Bullet 2"]},
            ],
        }
    )
    service.extract_concepts = AsyncMock(
        return_value=[{"name": "Test Concept", "definition": "A test definition"}]
    )
    service.synthesize_summary = AsyncMock(
        return_value={
            "tldr": "Test TLDR",
            "keyTakeaways": ["Takeaway 1", "Takeaway 2"],
        }
    )

    return service


# ============================================================
# Mock Repository
# ============================================================


@pytest.fixture
def mock_repository():
    """Mock video repository for testing.

    This mocks the MongoDBVideoRepository that handles persistence.
    """
    repo = MagicMock()
    repo.get_video_summary = MagicMock(return_value=None)
    repo.update_status = MagicMock()
    repo.save_result = MagicMock()
    repo.increment_retry = MagicMock(return_value=1)
    repo.set_provider_config = MagicMock()
    return repo


# ============================================================
# Test Video Data Fixtures
# ============================================================


@pytest.fixture
def sample_video_metadata():
    """Sample video metadata from YouTube."""
    return {
        "title": "Learn Python Testing in 30 Minutes",
        "channel": "CodeMaster",
        "thumbnail_url": "https://i.ytimg.com/vi/test123/maxresdefault.jpg",
        "duration": 1800,  # 30 minutes
        "description": "In this video we learn about Python testing...",
        "view_count": 150000,
        "upload_date": "2024-01-15",
    }


@pytest.fixture
def sample_video_data(sample_video_metadata):
    """Complete sample video data for testing."""
    return {
        "video_summary_id": "507f1f77bcf86cd799439011",
        "youtube_id": "test123abc",
        "url": "https://www.youtube.com/watch?v=test123abc",
        "user_id": "user-123",
        **sample_video_metadata,
    }


@pytest.fixture
def sample_processing_result():
    """Sample complete processing result."""
    return {
        "title": "Learn Python Testing in 30 Minutes",
        "channel": "CodeMaster",
        "duration": 1800,
        "thumbnail_url": "https://i.ytimg.com/vi/test123/maxresdefault.jpg",
        "transcript": "Hello and welcome to this video about Python testing...",
        "transcript_type": "manual",
        "processing_time_ms": 5000,
        "summary": {
            "tldr": "A comprehensive guide to Python testing fundamentals",
            "key_takeaways": [
                "Write tests before code (TDD)",
                "Use pytest for Python testing",
                "Mock external dependencies",
            ],
            "sections": [
                {
                    "id": "section-intro",
                    "timestamp": "00:00",
                    "startSeconds": 0,
                    "endSeconds": 180,
                    "title": "Introduction to Testing",
                    "content": [
                        {"type": "paragraph", "text": "Overview of why testing matters"},
                        {
                            "type": "bullets",
                            "items": ["Tests catch bugs early", "Tests serve as documentation"],
                        },
                    ],
                }
            ],
            "concepts": [
                {
                    "id": "concept-unit-test",
                    "name": "Unit Test",
                    "definition": "A test that verifies a single unit of code in isolation",
                }
            ],
        },
    }


# ============================================================
# Test Transcript Fixtures
# ============================================================


@pytest.fixture
def sample_segments():
    """Sample transcript segments with timing information.

    These segments produce a total duration of ~75 seconds,
    which is above MIN_VIDEO_DURATION_SECONDS (60).
    """
    return [
        {"text": "Hello and welcome", "start": 0.0, "duration": 5.0},
        {"text": "to this video about testing", "start": 5.0, "duration": 10.0},
        {"text": "Today we will learn", "start": 15.0, "duration": 10.0},
        {"text": "how to write good tests", "start": 25.0, "duration": 15.0},
        {
            "text": "First, let's understand why testing is important",
            "start": 40.0,
            "duration": 15.0,
        },
        {"text": "Testing helps catch bugs early", "start": 55.0, "duration": 10.0},
        {"text": "and serves as documentation for your code", "start": 65.0, "duration": 10.0},
    ]


@pytest.fixture
def sample_transcript():
    """Sample cleaned transcript text."""
    return (
        "Hello and welcome to this video about testing. "
        "Today we will learn how to write good tests. "
        "First, let's understand why testing is important. "
        "Testing helps catch bugs early and serves as documentation for your code."
    )


@pytest.fixture
def sample_normalized_segments():
    """Sample normalized transcript segments with millisecond timestamps.

    Matches sample_segments fixture timing.
    """
    return [
        {"text": "Hello and welcome", "startMs": 0, "endMs": 5000},
        {"text": "to this video about testing", "startMs": 5000, "endMs": 15000},
        {"text": "Today we will learn", "startMs": 15000, "endMs": 25000},
        {"text": "how to write good tests", "startMs": 25000, "endMs": 40000},
        {
            "text": "First, let's understand why testing is important",
            "startMs": 40000,
            "endMs": 55000,
        },
        {"text": "Testing helps catch bugs early", "startMs": 55000, "endMs": 65000},
        {"text": "and serves as documentation for your code", "startMs": 65000, "endMs": 75000},
    ]


# ============================================================
# Sample LLM Response Fixtures
# ============================================================


@pytest.fixture
def sample_llm_summary_response():
    """Sample LLM response for section summary with content blocks."""
    return '{"content": [{"type": "paragraph", "text": "This section covers the basics of testing."}, {"type": "bullets", "items": ["Write tests first", "Keep tests simple"]}]}'


@pytest.fixture
def sample_llm_concepts_response():
    """Sample LLM response for concept extraction."""
    return '{"concepts": [{"name": "Unit Testing", "definition": "Testing individual components"}, {"name": "Integration Testing", "definition": "Testing components together"}]}'


@pytest.fixture
def sample_llm_synthesis_response():
    """Sample LLM response for synthesis."""
    return '{"tldr": "A video about writing effective tests.", "keyTakeaways": ["Always test", "Keep it simple"]}'


# ============================================================
# FastAPI Test Client Fixture
# ============================================================


@pytest.fixture
def mock_app_dependencies(mock_mongo_client, mock_llm_provider):
    """Override FastAPI dependencies for testing.

    Usage:
        async def test_endpoint(mock_app_dependencies):
            from src.main import app
            from httpx import AsyncClient, ASGITransport

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                response = await client.get("/health")
    """
    from src.main import app
    from src.dependencies import get_mongo_client, get_llm_provider

    app.dependency_overrides[get_mongo_client] = lambda: mock_mongo_client
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm_provider

    yield app

    app.dependency_overrides.clear()
