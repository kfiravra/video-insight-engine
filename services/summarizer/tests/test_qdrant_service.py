"""Tests for Qdrant vector service."""

import hashlib

import pytest
from unittest.mock import MagicMock, patch

from src.services.vector.qdrant_service import (
    COLLECTION_NAME,
    SOURCE_DEFAULT_OUTPUT,
    SOURCE_TRANSCRIPT,
    SOURCE_VISUAL,
    VectorService,
    _point_id,
)


class TestVectorService:
    """Test VectorService with mocked Qdrant client."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        # Mock get_collections to return empty list (collection needs creation)
        collection_info = MagicMock()
        collection_info.name = COLLECTION_NAME
        collections_response = MagicMock()
        collections_response.collections = [collection_info]
        client.get_collections.return_value = collections_response
        return client

    @pytest.fixture
    def service(self, mock_client):
        svc = VectorService(host="localhost", port=6333)
        svc._client = mock_client
        return svc

    @staticmethod
    def _query_response(points):
        """Wrap a list of mock points into a QueryResponse-like object."""
        resp = MagicMock()
        resp.points = points
        return resp

    def test_store_chunks_success(self, service, mock_client):
        chunks = [
            {"text": "Hello world", "start_char": 0, "end_char": 11},
            {"text": "Second chunk", "start_char": 12, "end_char": 24},
        ]
        embeddings = [[0.1] * 384, [0.2] * 384]

        result = service.store_chunks("video123", chunks, embeddings)

        assert result is True
        mock_client.upsert.assert_called_once()
        call_args = mock_client.upsert.call_args
        assert call_args[1]["collection_name"] == COLLECTION_NAME
        assert len(call_args[1]["points"]) == 2

    def test_store_chunks_failure(self, service, mock_client):
        mock_client.upsert.side_effect = Exception("Connection failed")

        result = service.store_chunks("video123", [{"text": "hi"}], [[0.1] * 384])

        assert result is False

    def test_search_success(self, service, mock_client):
        mock_result = MagicMock()
        mock_result.payload = {
            "text": "Hello world",
            "video_id": "video123",
            "chunk_index": 0,
        }
        mock_result.score = 0.95
        mock_client.query_points.return_value = self._query_response([mock_result])

        results = service.search([0.1] * 384, video_id="video123", limit=5)

        assert len(results) == 1
        assert results[0]["text"] == "Hello world"
        assert results[0]["score"] == 0.95

    def test_search_with_video_filter(self, service, mock_client):
        mock_client.query_points.return_value = self._query_response([])

        service.search([0.1] * 384, video_id="video123")

        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is not None

    def test_search_without_filter(self, service, mock_client):
        mock_client.query_points.return_value = self._query_response([])

        service.search([0.1] * 384)

        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is None

    def test_search_failure_returns_empty(self, service, mock_client):
        mock_client.query_points.side_effect = Exception("Timeout")

        results = service.search([0.1] * 384)

        assert results == []

    def test_search_multi_video(self, service, mock_client):
        mock_client.query_points.return_value = self._query_response([])

        service.search_multi_video([0.1] * 384, ["v1", "v2"], limit=10)

        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is not None
        assert call_args[1]["limit"] == 10

    def test_delete_video_success(self, service, mock_client):
        result = service.delete_video("video123")

        assert result is True
        mock_client.delete.assert_called_once()

    def test_delete_video_failure(self, service, mock_client):
        mock_client.delete.side_effect = Exception("Error")

        result = service.delete_video("video123")

        assert result is False

    def test_delete_by_video_and_source_success(self, service, mock_client):
        result = service.delete_by_video_and_source("video123", "default_output")

        assert result is True
        mock_client.delete.assert_called_once()
        call_args = mock_client.delete.call_args
        # Verify both video_id AND source conditions are present.
        selector = call_args[1]["points_selector"]
        keys = [c.key for c in selector.must]
        assert "video_id" in keys
        assert "source" in keys

    def test_delete_by_video_and_source_failure(self, service, mock_client):
        mock_client.delete.side_effect = Exception("boom")

        result = service.delete_by_video_and_source("video123", "transcript")

        assert result is False

    def test_search_with_sources_filter(self, service, mock_client):
        mock_client.query_points.return_value = self._query_response([])

        service.search([0.1] * 384, video_id="video123", sources=["default_output"])

        call_args = mock_client.query_points.call_args
        query_filter = call_args[1]["query_filter"]
        assert query_filter is not None
        keys = [c.key for c in query_filter.must]
        assert "video_id" in keys
        assert "source" in keys

    def test_search_multi_video_with_sources_filter(self, service, mock_client):
        mock_client.query_points.return_value = self._query_response([])

        service.search_multi_video(
            [0.1] * 384,
            ["v1", "v2"],
            limit=10,
            sources=["transcript", "default_output"],
        )

        call_args = mock_client.query_points.call_args
        query_filter = call_args[1]["query_filter"]
        assert query_filter is not None
        keys = [c.key for c in query_filter.must]
        assert "video_id" in keys
        assert "source" in keys

    def test_store_chunks_supports_per_chunk_tab_metadata(self, service, mock_client):
        """A single batch can carry per-chunk tab_id/tab_component, so multiple
        tabs upsert in one round-trip instead of N HTTP calls."""
        chunks = [
            {
                "text": "Overview chunk text content here long enough",
                "start_char": 0,
                "end_char": 40,
            },
            {
                "text": "Quiz chunk text content here long enough now",
                "start_char": 0,
                "end_char": 40,
            },
        ]
        embeddings = [[0.1] * 384, [0.2] * 384]

        service.store_chunks(
            "video123",
            chunks,
            embeddings,
            language="en",
            source=SOURCE_DEFAULT_OUTPUT,
            prop_paths=["masterSummary", "questions[0]"],
            tab_ids=["overview_tab", "quiz_tab"],
            tab_components=["overview", "quiz"],
        )

        call_args = mock_client.upsert.call_args
        points = call_args[1]["points"]
        assert len(points) == 2
        assert points[0].payload["tab_id"] == "overview_tab"
        assert points[0].payload["tab_component"] == "overview"
        assert points[1].payload["tab_id"] == "quiz_tab"
        assert points[1].payload["tab_component"] == "quiz"
        # Unique IDs (different tab_id + prop_path combinations).
        assert points[0].id != points[1].id

    def test_count_points_sums_scroll_pages(self, service, mock_client):
        # Two scroll pages: 256 + 4 = 260 points.
        page1 = ([MagicMock() for _ in range(256)], "next_token")
        page2 = ([MagicMock() for _ in range(4)], None)
        mock_client.scroll.side_effect = [page1, page2]

        total = service.count_points("video123", source="transcript")

        assert total == 260
        assert mock_client.scroll.call_count == 2
        first_call = mock_client.scroll.call_args_list[0]
        keys = [c.key for c in first_call[1]["scroll_filter"].must]
        assert "video_id" in keys
        assert "source" in keys

    def test_count_points_returns_zero_on_failure(self, service, mock_client):
        mock_client.scroll.side_effect = Exception("Qdrant down")

        assert service.count_points("video123") == 0

    def test_store_chunks_writes_new_payload_fields(self, service, mock_client):
        chunks = [{"text": "Some text content here", "start_char": 0, "end_char": 22}]
        embeddings = [[0.1] * 384]

        service.store_chunks(
            "video123",
            chunks,
            embeddings,
            language="en",
            source=SOURCE_DEFAULT_OUTPUT,
            tab_id="overview_tab",
            tab_component="overview",
            prop_paths=["masterSummary"],
        )

        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        assert point.payload["source"] == "default_output"
        assert point.payload["tab_id"] == "overview_tab"
        assert point.payload["tab_component"] == "overview"
        assert point.payload["prop_path"] == "masterSummary"
        assert point.payload["user_id"] is None

    def test_store_chunks_writes_timestamps_and_schema_version(self, service, mock_client):
        """Payload schema v2: chunks with start_time/end_time carry them as
        timestamp/end_timestamp (seconds) so the assistant renders [MM:SS]."""
        chunks = [
            {
                "text": "First chunk",
                "start_char": 0,
                "end_char": 11,
                "start_time": 0.0,
                "end_time": 42.5,
            },
            {
                "text": "Second chunk",
                "start_char": 12,
                "end_char": 24,
                "start_time": 754.0,
                "end_time": 812.0,
            },
        ]
        embeddings = [[0.1] * 384, [0.2] * 384]

        service.store_chunks("video123", chunks, embeddings)

        points = mock_client.upsert.call_args[1]["points"]
        assert points[0].payload["timestamp"] == 0.0
        assert points[0].payload["end_timestamp"] == 42.5
        assert points[1].payload["timestamp"] == 754.0
        assert points[1].payload["end_timestamp"] == 812.0
        assert all(p.payload["schema_version"] == 2 for p in points)

    def test_store_chunks_without_times_writes_null_timestamp(self, service, mock_client):
        """Output chunks (no timeline) and unmapped transcripts store a null
        timestamp — downstream formatters must null-check, never crash."""
        chunks = [{"text": "No timeline chunk", "start_char": 0, "end_char": 17}]

        service.store_chunks("video123", chunks, [[0.1] * 384])

        point = mock_client.upsert.call_args[1]["points"][0]
        assert point.payload["timestamp"] is None
        assert point.payload["end_timestamp"] is None
        assert point.payload["schema_version"] == 2

    def test_result_to_dict_surfaces_timestamp(self, service, mock_client):
        mock_result = MagicMock()
        mock_result.payload = {
            "text": "Hello",
            "video_id": "video123",
            "chunk_index": 0,
            "timestamp": 754.0,
            "end_timestamp": 812.0,
        }
        mock_result.score = 0.9
        mock_client.query_points.return_value = self._query_response([mock_result])

        results = service.search([0.1] * 384, video_id="video123")

        assert results[0]["timestamp"] == 754.0
        assert results[0]["end_timestamp"] == 812.0

    def test_result_to_dict_handles_legacy_payload_without_timestamp(self, service, mock_client):
        """v1 points written before schema v2 have no timestamp keys."""
        mock_result = MagicMock()
        mock_result.payload = {"text": "Old point", "video_id": "v1", "chunk_index": 0}
        mock_result.score = 0.8
        mock_client.query_points.return_value = self._query_response([mock_result])

        results = service.search([0.1] * 384, video_id="v1")

        assert results[0]["timestamp"] is None
        assert results[0]["end_timestamp"] is None


class TestPointId:
    """Test the _point_id helper directly."""

    def test_transcript_id_preserves_legacy_hash(self):
        """Existing transcript points were written with sha256("video_idx")."""
        legacy = (
            int.from_bytes(
                hashlib.sha256("video123_0".encode()).digest()[:8],
                "big",
            )
            & 0x7FFFFFFFFFFFFFFF
        )
        new = _point_id(SOURCE_TRANSCRIPT, "video123", None, None, 0)
        assert new == legacy

    def test_transcript_ids_are_idempotent_per_chunk_idx(self):
        a = _point_id(SOURCE_TRANSCRIPT, "v1", None, None, 5)
        b = _point_id(SOURCE_TRANSCRIPT, "v1", None, None, 5)
        assert a == b

    def test_different_sources_produce_different_ids(self):
        transcript = _point_id(SOURCE_TRANSCRIPT, "v1", None, None, 0)
        output = _point_id(SOURCE_DEFAULT_OUTPUT, "v1", "tab1", "field", 0)
        assert transcript != output

    def test_should_give_visual_points_ids_distinct_from_transcript_points(self):
        transcript = _point_id(SOURCE_TRANSCRIPT, "v1", None, None, 0)
        visual = _point_id(SOURCE_VISUAL, "v1", None, None, 0)
        assert transcript != visual

    def test_different_tabs_produce_different_ids(self):
        a = _point_id(SOURCE_DEFAULT_OUTPUT, "v1", "tab_a", "x", 0)
        b = _point_id(SOURCE_DEFAULT_OUTPUT, "v1", "tab_b", "x", 0)
        assert a != b

    def test_different_props_produce_different_ids(self):
        a = _point_id(SOURCE_DEFAULT_OUTPUT, "v1", "tab", "p1", 0)
        b = _point_id(SOURCE_DEFAULT_OUTPUT, "v1", "tab", "p2", 0)
        assert a != b

    def test_different_videos_produce_different_ids(self):
        a = _point_id(SOURCE_TRANSCRIPT, "v1", None, None, 0)
        b = _point_id(SOURCE_TRANSCRIPT, "v2", None, None, 0)
        assert a != b

    def test_id_fits_in_int63(self):
        for src in [SOURCE_TRANSCRIPT, SOURCE_DEFAULT_OUTPUT]:
            point = _point_id(src, "v1", "t", "p", 999)
            assert 0 <= point < 1 << 63
