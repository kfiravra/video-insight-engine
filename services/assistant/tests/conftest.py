"""Shared fixtures for assistant service tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------


@pytest.fixture
def langfuse_capture():
    """The real Langfuse v4 SDK exporting to memory (``llm_common.observability.testing``)."""
    from llm_common.observability.testing import start_capture, stop_capture

    capture = start_capture()
    yield capture
    stop_capture()


@pytest.fixture(autouse=True)
def _langfuse_disabled():
    """Every test starts and ends with the shared Langfuse client disabled."""
    from llm_common.observability import client as shared_client

    shared_client._reset_for_tests()
    yield
    shared_client._reset_for_tests()


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

def make_video_context(**overrides):
    """Create a sample VideoContext with sensible defaults."""
    from src.repositories.video_repository import VideoContext

    defaults = {
        "id": "vid123",
        "youtube_id": "abc123",
        "title": "Understanding Neural Networks",
        "creator": "3Blue1Brown",
        "summary": (
            "A visual introduction to neural networks covering the "
            "fundamentals of how they learn from data."
        ),
        "takeaways": [
            "Neural networks learn by adjusting weights",
            "Backpropagation is the key training algorithm",
            "Layers transform data step by step",
        ],
        "tabs": [
            {"id": "key_points", "label": "Key Points", "emoji": "\U0001f4dd"},
            {"id": "concepts", "label": "Concepts", "emoji": "\U0001f4a1"},
            {"id": "quizzes", "label": "Quizzes", "emoji": "\U0001f9e0"},
        ],
        "output_data": None,
    }
    defaults.update(overrides)
    return VideoContext(**defaults)


def make_rag_sources(count: int = 3):
    """Create a list of sample RAGSource models."""
    from src.models.responses import RAGSource

    samples = [
        RAGSource(text="A neural network consists of layers.", video_id="abc123", timestamp="0:45", score=0.92, chunk_index=0),
        RAGSource(text="Backpropagation computes the gradient.", video_id="abc123", timestamp="2:00", score=0.87, chunk_index=1),
        RAGSource(text="Each layer applies a non-linear activation.", video_id="abc123", timestamp="3:20", score=0.81, chunk_index=2),
    ]
    return samples[:count]


# ---------------------------------------------------------------------------
# Fixtures — sample data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_video_context():
    return make_video_context()


@pytest.fixture
def sample_rag_sources():
    return make_rag_sources(3)


# ---------------------------------------------------------------------------
# Fixtures — mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_video_repo(sample_video_context):
    repo = AsyncMock()
    repo.get_video_context.return_value = sample_video_context
    return repo


@pytest.fixture
def mock_qdrant_repo():
    repo = AsyncMock()
    repo.search.return_value = [
        {"text": "A neural network consists of layers.", "video_id": "abc123", "timestamp": "0:45", "score": 0.92, "chunk_index": 0},
        {"text": "Backpropagation computes the gradient.", "video_id": "abc123", "timestamp": "2:00", "score": 0.87, "chunk_index": 1},
        {"text": "Each layer applies a non-linear activation.", "video_id": "abc123", "timestamp": "3:20", "score": 0.81, "chunk_index": 2},
    ]
    return repo


@pytest.fixture
def mock_llm():
    llm = AsyncMock()

    async def _mock_stream(*_args, **_kwargs):
        for token in ["Hello", " world", "!"]:
            yield token

    llm.stream_with_messages = _mock_stream
    llm.complete_with_messages = AsyncMock(return_value="Hello world!")
    llm.model = "anthropic/claude-sonnet-4-6"

    return llm


@pytest.fixture
def mock_rag(sample_rag_sources):
    rag = AsyncMock()
    rag.search.return_value = sample_rag_sources
    return rag


@pytest.fixture
def mock_api_client():
    """AsyncMock stand-in for the vie-api ApiClient.

    Every method returns a benign default so tools under test only need to
    assert which endpoint method was awaited, not stub each one.
    """
    client = AsyncMock()
    client.list_folders.return_value = []
    client.create_folder.return_value = {"id": "folder-1", "name": "New Folder"}
    client.update_folder.return_value = {"id": "folder-1", "name": "Renamed"}
    client.move_folder.return_value = {"id": "folder-1", "name": "Moved"}
    client.delete_folder.return_value = {"deleted": True}
    client.list_videos.return_value = []
    client.move_video.return_value = {"moved": True}
    client.generate_video.return_value = {"videoSummaryId": "vs-1", "status": "processing"}
    return client


@pytest.fixture
def mock_settings():
    from unittest.mock import MagicMock
    s = MagicMock()
    s.MAX_CONTEXT_CHUNKS = 8
    s.MAX_CONVERSATION_TURNS = 20
    s.llm_model = "anthropic/claude-sonnet-4-6"
    s.llm_fast_model = "anthropic/claude-haiku-4-5-20251001"
    return s


@pytest.fixture
def context_builder():
    from src.services.context_builder import ContextBuilder
    return ContextBuilder()


@pytest.fixture
def assistant_service(mock_video_repo, mock_rag, mock_llm, mock_settings):
    from src.services.assistant import AssistantService
    from src.services.context_builder import ContextBuilder

    return AssistantService(
        llm=mock_llm,
        rag=mock_rag,
        video_repo=mock_video_repo,
        context_builder=ContextBuilder(),
        settings=mock_settings,
    )


@pytest.fixture
def agentic_assistant_service(
    mock_video_repo, mock_rag, mock_llm, mock_settings, mock_api_client,
):
    """AssistantService with an injected api_client so library_chat runs the
    agentic tool-calling loop instead of degrading to a plain stream."""
    from src.services.assistant import AssistantService
    from src.services.context_builder import ContextBuilder

    return AssistantService(
        llm=mock_llm,
        rag=mock_rag,
        video_repo=mock_video_repo,
        context_builder=ContextBuilder(),
        settings=mock_settings,
        api_client=mock_api_client,
    )


@pytest.fixture
async def app_client(mock_llm, mock_rag, mock_video_repo, mock_settings):
    from httpx import ASGITransport, AsyncClient

    from src.server import app

    mock_assistant = AsyncMock()

    async def _mock_chat(*_args, **_kwargs):
        yield 'data: {"type": "text", "content": "Hello"}\n\n'
        yield 'data: {"type": "done", "content": ""}\n\n'

    mock_assistant.chat = _mock_chat

    # Inject mocks into app.state (matches current server pattern)
    app.state.assistant_service = mock_assistant
    app.state.rag_service = mock_rag

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Secret": "dev-internal-secret-change-me"},
    ) as client:
        yield client

    # Cleanup
    app.state.assistant_service = None
    app.state.rag_service = None
