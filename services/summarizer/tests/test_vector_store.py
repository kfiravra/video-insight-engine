"""Tests for vector store background tasks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.services.pipeline.visual_annotations import render_visual_annotations
from src.services.vector.qdrant_service import COLLECTION_NAME, VectorService
from src.services.vector.store import (
    store_default_output_chunks,
    store_transcript_chunks,
    store_visual_chunks,
)


class TestStoreTranscriptChunks:
    """Test the transcript chunk background pipeline."""

    @patch("src.services.vector.store.settings")
    async def test_skips_when_disabled(self, mock_settings):
        mock_settings.QDRANT_ENABLED = False

        await store_transcript_chunks("video123", "Some transcript text.")

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_chunks_embeds_and_stores(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.return_value = [
            {"text": "chunk 1", "start_char": 0, "end_char": 7},
            {"text": "chunk 2", "start_char": 8, "end_char": 15},
        ]
        mock_embed.return_value = [[0.1] * 384, [0.2] * 384]
        mock_svc = MagicMock()
        mock_svc.store_chunks.return_value = True
        mock_get_svc.return_value = mock_svc

        await store_transcript_chunks("video123", "Some transcript text.")

        mock_chunk.assert_called_once()
        mock_embed.assert_called_once_with(["chunk 1", "chunk 2"])
        mock_svc.store_chunks.assert_called_once()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_pre_deletes_before_upsert(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        """Pre-delete must happen before upsert so reprocesses with fewer
        chunks don't leave orphan points behind."""
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.return_value = [{"text": "chunk", "start_char": 0, "end_char": 5}]
        mock_embed.return_value = [[0.1] * 384]

        call_order: list[str] = []
        mock_svc = MagicMock()
        mock_svc.delete_by_video_and_source.side_effect = lambda *_a, **_kw: call_order.append(
            "delete"
        )
        mock_svc.store_chunks.side_effect = lambda *_a, **_kw: call_order.append("store") or True
        mock_get_svc.return_value = mock_svc

        await store_transcript_chunks("video123", "text")

        assert call_order == ["delete", "store"]
        # The delete must scope to ``transcript`` source.
        delete_call = mock_svc.delete_by_video_and_source.call_args
        args = delete_call.args
        assert args[0] == "video123"
        assert args[1] == "transcript"

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_segments_produce_chunk_timestamps(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        """When segments are passed, the chunks handed to store_chunks carry
        start_time/end_time so the payload gets non-null [MM:SS] timestamps."""
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.return_value = [
            {"text": "chunk 1", "start_char": 0, "end_char": 20},
            {"text": "chunk 2", "start_char": 21, "end_char": 41},
        ]
        mock_embed.return_value = [[0.1] * 384, [0.2] * 384]
        mock_svc = MagicMock()
        mock_svc.store_chunks.return_value = True
        mock_get_svc.return_value = mock_svc

        segments = [
            {"text": "a" * 20, "start": 0.0, "duration": 30.0},
            {"text": "b" * 20, "start": 30.0, "duration": 30.0},
        ]
        await store_transcript_chunks("video123", "text", segments=segments)

        stored_chunks = mock_svc.store_chunks.call_args.args[1]
        assert stored_chunks[0]["start_time"] == 0.0
        assert stored_chunks[1]["start_time"] == 30.0
        assert stored_chunks[1]["end_time"] == 60.0

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_no_segments_stores_chunks_without_timestamps(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        """Metadata-only transcripts have no segments — storage still works,
        chunks simply carry no start_time (payload timestamp stays null)."""
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.return_value = [{"text": "chunk", "start_char": 0, "end_char": 5}]
        mock_embed.return_value = [[0.1] * 384]
        mock_svc = MagicMock()
        mock_svc.store_chunks.return_value = True
        mock_get_svc.return_value = mock_svc

        await store_transcript_chunks("video123", "text", segments=None)

        stored_chunks = mock_svc.store_chunks.call_args.args[1]
        assert "start_time" not in stored_chunks[0]

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_empty_chunks_skips_storage(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.return_value = []

        await store_transcript_chunks("video123", "")

        mock_embed.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.chunk_transcript")
    @patch("src.services.vector.store.settings")
    async def test_handles_exception_gracefully(
        self,
        mock_settings,
        mock_chunk,
        mock_embed,
        mock_get_svc,
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_chunk.side_effect = Exception("boom")

        # Should not raise
        await store_transcript_chunks("video123", "text")


class TestStoreDefaultOutputChunks:
    """Test the assembled-output chunk background pipeline."""

    @patch("src.services.vector.store.settings")
    async def test_skips_when_disabled(self, mock_settings):
        mock_settings.QDRANT_ENABLED = False

        await store_default_output_chunks(
            "video123", [{"id": "x", "component": "overview", "props": {}}]
        )

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_pre_deletes_before_upsert(self, mock_settings, mock_embed, mock_get_svc):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.return_value = [[0.1] * 384]

        call_order: list[str] = []
        mock_svc = MagicMock()
        mock_svc.delete_by_video_and_source.side_effect = lambda *_a, **_kw: call_order.append(
            "delete"
        )
        mock_svc.store_chunks.side_effect = lambda *_a, **_kw: call_order.append("store") or True
        mock_get_svc.return_value = mock_svc

        tabs = [
            {
                "id": "overview_tab",
                "component": "overview",
                "props": {
                    "masterSummary": "A long enough sentence that crosses the six word minimum bar.",
                },
            },
        ]

        await store_default_output_chunks("video123", tabs)

        assert call_order[0] == "delete"
        assert "store" in call_order
        # Pre-delete must scope to "default_output".
        delete_call = mock_svc.delete_by_video_and_source.call_args
        args = delete_call.args
        assert args[0] == "video123"
        assert args[1] == "default_output"

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_empty_tabs_pre_delete_only(self, mock_settings, mock_embed, mock_get_svc):
        """When chunker yields nothing, still pre-delete to clean up old points."""
        mock_settings.QDRANT_ENABLED = True
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        await store_default_output_chunks("video123", [])

        mock_svc.delete_by_video_and_source.assert_called_once_with(
            "video123",
            "default_output",
        )
        mock_svc.store_chunks.assert_not_called()
        mock_embed.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_handles_chunker_exception_gracefully(
        self,
        mock_settings,
        mock_embed,
        mock_get_svc,
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_get_svc.return_value = MagicMock()

        with patch(
            "src.services.vector.store.chunk_assembled_tabs",
            side_effect=Exception("boom"),
        ):
            # Must not raise.
            await store_default_output_chunks(
                "video123", [{"id": "x", "component": "overview", "props": {}}]
            )

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_pre_delete_runs_even_when_chunker_raises(
        self,
        mock_settings,
        mock_embed,
        mock_get_svc,
    ):
        """Regression: chunker exception must NOT strand orphan output chunks
        — pre-delete fires before chunking so prior runs are always cleaned up."""
        mock_settings.QDRANT_ENABLED = True
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        with patch(
            "src.services.vector.store.chunk_assembled_tabs",
            side_effect=Exception("boom"),
        ):
            await store_default_output_chunks(
                "video123", [{"id": "x", "component": "overview", "props": {}}]
            )

        mock_svc.delete_by_video_and_source.assert_called_once_with(
            "video123",
            "default_output",
        )

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_batches_chunks_into_single_upsert(self, mock_settings, mock_embed, mock_get_svc):
        """Chunks from multiple tabs are batched into ONE store_chunks call
        (not one call per tab) — saves N HTTP round-trips per pipeline run."""
        mock_settings.QDRANT_ENABLED = True
        mock_embed.return_value = [[0.1] * 384, [0.2] * 384]
        mock_svc = MagicMock()
        mock_svc.store_chunks.return_value = True
        mock_get_svc.return_value = mock_svc

        tabs = [
            {
                "id": "overview_tab",
                "component": "overview",
                "props": {
                    "masterSummary": "Long enough overview summary text crossing six words easily."
                },
            },
            {
                "id": "checklist_tab",
                "component": "checklist",
                "props": {
                    "items": [{"label": "Long enough checklist label text crossing six words"}]
                },
            },
        ]

        await store_default_output_chunks("video123", tabs)

        # Single batched call across all tabs — per-chunk tab metadata
        # supplied via tab_ids/tab_components kwargs.
        assert mock_svc.store_chunks.call_count == 1
        call = mock_svc.store_chunks.call_args
        # Positional args: (video_id, chunks, embeddings, language,
        #                   original_chunks, source, tab_id, tab_component,
        #                   prop_paths, tab_ids, tab_components)
        tab_ids = call.args[9]
        tab_components = call.args[10]
        assert sorted(tab_ids) == ["checklist_tab", "overview_tab"]
        assert sorted(tab_components) == ["checklist", "overview"]


_VISUAL_BLOCK = render_visual_annotations(
    [
        {
            "timestamp_sec": 12,
            "content": "Code editor with the search loop",
            "text_visible": "while low <= high:",
            "scene_type": "code",
            "original_index": 1,
        }
    ],
    [{"index": 2, "timestamp": 95, "ocr_text": "Binary Search Complexity"}],
)


def _qdrant_backed_service() -> tuple[VectorService, MagicMock]:
    """A real VectorService over a mocked client, so the upserted payloads are inspectable."""
    client = MagicMock()
    collection = MagicMock()
    collection.name = COLLECTION_NAME
    client.get_collections.return_value.collections = [collection]
    service = VectorService(host="localhost", port=6333)
    service._client = client
    return service, client


class TestStoreVisualChunks:
    """The rendered <visual_annotations> block indexed as source="visual" points."""

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.settings")
    async def test_should_do_nothing_when_qdrant_is_disabled(self, mock_settings, mock_get_svc):
        mock_settings.QDRANT_ENABLED = False

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        mock_get_svc.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_pre_delete_visual_points_before_upsert(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.return_value = [[0.1] * 384]
        call_order: list[tuple[str, str]] = []
        mock_svc = MagicMock()
        mock_svc.delete_by_video_and_source.side_effect = lambda _vid, source: call_order.append(
            ("delete", source)
        )
        mock_svc.store_chunks.side_effect = lambda *args: (
            call_order.append(("store", args[5])) or True
        )
        mock_get_svc.return_value = mock_svc

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        assert call_order == [("delete", "visual"), ("store", "visual")]

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_still_clear_stale_visual_points_when_the_block_is_empty(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        await store_visual_chunks("video123", "")

        mock_svc.delete_by_video_and_source.assert_called_once_with("video123", "visual")
        mock_svc.store_chunks.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_tag_points_visual_with_frame_timestamps(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.return_value = [[0.1] * 384]
        service, client = _qdrant_backed_service()
        mock_get_svc.return_value = service

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        payload = client.upsert.call_args.kwargs["points"][0].payload
        assert (payload["source"], payload["timestamp"], payload["end_timestamp"]) == (
            "visual",
            12.0,
            95.0,
        )

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_label_the_chunk_text_as_on_screen_content(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.return_value = [[0.1] * 384]
        service, client = _qdrant_backed_service()
        mock_get_svc.return_value = service

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        payload = client.upsert.call_args.kwargs["points"][0].payload
        assert payload["text"] == (
            "On screen:\n"
            "[0:12] Code editor with the search loop | while low <= high:\n"
            "[1:35] | Binary Search Complexity"
        )

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_never_raise_when_embedding_fails(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.side_effect = RuntimeError("model not loaded")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        mock_svc.store_chunks.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_keep_previous_visual_points_when_embedding_fails(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        mock_embed.side_effect = RuntimeError("model not loaded")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        mock_svc.delete_by_video_and_source.assert_not_called()

    @patch("src.services.vector.store._get_vector_service")
    @patch("src.services.vector.store.embed_texts")
    @patch("src.services.vector.store.settings")
    async def test_should_embed_before_deleting_the_old_visual_points(
        self, mock_settings, mock_embed, mock_get_svc
    ):
        mock_settings.QDRANT_ENABLED = True
        call_order: list[str] = []
        mock_embed.side_effect = lambda texts: call_order.append("embed") or [[0.1] * 384]
        mock_svc = MagicMock()
        mock_svc.delete_by_video_and_source.side_effect = lambda *_: call_order.append("delete")
        mock_svc.store_chunks.side_effect = lambda *_: call_order.append("store") or True
        mock_get_svc.return_value = mock_svc

        await store_visual_chunks("video123", _VISUAL_BLOCK)

        assert call_order == ["embed", "delete", "store"]
