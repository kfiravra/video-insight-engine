"""Tests for transcript store service — S3 key generation and retrieval."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.transcription.transcript_store import (
    CaptionRef,
    RawTranscript,
    TranscriptStoreService,
    caption_ref,
)


@pytest.fixture
def store():
    """Create a fresh TranscriptStoreService with mocked S3 client."""
    with patch("src.services.transcription.transcript_store.s3_client") as mock_s3:
        service = TranscriptStoreService()
        service._s3 = mock_s3
        yield service, mock_s3


class TestKeyGeneration:
    """Tests for key path generation."""

    def test_new_key_format(self):
        store = TranscriptStoreService()
        assert store._get_key("dQw4w9WgXcQ") == "videos/dQw4w9WgXcQ/transcript.json"


class TestGet:
    """Tests for get() method."""

    @pytest.mark.asyncio
    async def test_get_found(self, store):
        service, mock_s3 = store
        mock_s3.get_json = AsyncMock(
            return_value={
                "youtube_id": "dQw4w9WgXcQ",
                "fetched_at": "2025-01-01T00:00:00Z",
                "source": "api",
                "language": "en",
                "segments": [{"text": "hello", "startMs": 0, "endMs": 1000}],
            }
        )

        result = await service.get("dQw4w9WgXcQ")

        assert result is not None
        assert result.youtube_id == "dQw4w9WgXcQ"
        mock_s3.get_json.assert_awaited_once_with("videos/dQw4w9WgXcQ/transcript.json")

    @pytest.mark.asyncio
    async def test_get_not_found(self, store):
        service, mock_s3 = store
        mock_s3.get_json = AsyncMock(return_value=None)

        result = await service.get("nonexistent123")

        assert result is None
        mock_s3.get_json.assert_awaited_once_with("videos/nonexistent123/transcript.json")


class TestStore:
    """Tests for store() method."""

    @pytest.mark.asyncio
    async def test_store_uses_new_key(self, store):
        service, mock_s3 = store
        mock_s3.ensure_bucket_exists = AsyncMock()
        mock_s3.put_json = AsyncMock()

        with patch(
            "src.services.transcription.transcript_store.S3Client.is_available", return_value=True
        ):
            key = await service.store(
                youtube_id="dQw4w9WgXcQ",
                segments=[{"text": "hello", "startMs": 0, "endMs": 1000}],
                source="api",
                language="en",
            )

        assert key == "videos/dQw4w9WgXcQ/transcript.json"
        mock_s3.put_json.assert_awaited_once()
        call_args = mock_s3.put_json.call_args
        assert call_args[0][0] == "videos/dQw4w9WgXcQ/transcript.json"


class TestGetByRef:
    """Tests for get_by_ref() — reads exact key from MongoDB."""

    @pytest.mark.asyncio
    async def test_get_by_ref(self, store):
        service, mock_s3 = store
        mock_s3.get_json = AsyncMock(
            return_value={
                "youtube_id": "dQw4w9WgXcQ",
                "fetched_at": "2025-01-01T00:00:00Z",
                "source": "api",
                "language": "en",
                "segments": [],
            }
        )

        result = await service.get_by_ref("videos/dQw4w9WgXcQ/transcript.json")

        assert result is not None
        mock_s3.get_json.assert_awaited_once_with("videos/dQw4w9WgXcQ/transcript.json")


class TestCaptionTrackRecord:
    """2.4: a yt-dlp blob records its caption track so a later picker change can spot it."""

    @pytest.mark.asyncio
    async def test_should_record_the_caption_track_in_the_blob_when_one_is_given(self, store):
        service, mock_s3 = store
        mock_s3.ensure_bucket_exists = AsyncMock()
        mock_s3.put_json = AsyncMock()

        with patch(
            "src.services.transcription.transcript_store.S3Client.is_available", return_value=True
        ):
            await service.store(
                youtube_id="dQw4w9WgXcQ",
                segments=[{"text": "hello", "startMs": 0, "endMs": 1000}],
                source="ytdlp",
                language="he",
                caption=CaptionRef(lang="iw", kind="auto-generated"),
            )

        assert mock_s3.put_json.call_args[0][1]["caption"] == {
            "lang": "iw",
            "kind": "auto-generated",
        }

    @pytest.mark.asyncio
    async def test_should_read_a_blob_stored_before_the_picker_without_a_caption(self, store):
        service, mock_s3 = store
        mock_s3.get_json = AsyncMock(
            return_value={
                "youtube_id": "dQw4w9WgXcQ",
                "fetched_at": "2025-01-01T00:00:00Z",
                "source": "ytdlp",
                "language": "nl",
                "segments": [],
            }
        )

        result = await service.get("dQw4w9WgXcQ")

        assert result is not None and result.caption is None


def _raw(source: str, caption: CaptionRef | None) -> RawTranscript:
    return RawTranscript(
        youtube_id="vid",
        fetched_at="2026-01-01T00:00:00Z",
        source=source,  # type: ignore[arg-type]
        language=None,
        segments=[],
        caption=caption,
    )


class TestHoldsOtherCaptionTrack:
    def test_should_be_false_when_the_blob_holds_the_picked_track(self):
        pick = CaptionRef(lang="en-US", kind="manual")

        assert (
            _raw("ytdlp", CaptionRef(lang="en-US", kind="manual")).holds_other_caption_track(pick)
            is False
        )

    def test_should_be_true_when_the_blob_holds_another_key(self):
        pick = CaptionRef(lang="en-US", kind="manual")

        assert (
            _raw("ytdlp", CaptionRef(lang="nl", kind="manual")).holds_other_caption_track(pick)
            is True
        )

    def test_should_be_true_when_a_caption_origin_blob_records_no_track(self):
        pick = CaptionRef(lang="en", kind="auto-generated")

        assert _raw("api", None).holds_other_caption_track(pick) is True

    @pytest.mark.parametrize("source", ["whisper", "gemini"])
    def test_should_be_false_for_an_audio_transcription_whatever_was_picked(self, source: str):
        pick = CaptionRef(lang="en", kind="auto-generated")

        assert _raw(source, None).holds_other_caption_track(pick) is False


class TestCaptionRef:
    def test_should_build_a_ref_when_both_fields_are_strings(self):
        assert caption_ref("iw", "auto-generated") == CaptionRef(lang="iw", kind="auto-generated")

    def test_should_return_none_when_no_track_was_picked(self):
        assert caption_ref(None, None) is None
