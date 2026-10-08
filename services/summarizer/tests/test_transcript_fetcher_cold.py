"""1a.7: a cold-media benchmark run skips the S3 transcript cache read.

A warm run still serves the S3 blob; a cold one goes straight to the live
chain (here: the yt-dlp caption track) without touching S3, so the benchmark
measures cold media. The fresh transcript is stored by assembly as usual.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.pipeline.pipeline_helpers import TranscriptData
from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript_fetcher import fetch_transcript
from tests.test_transcript_fetcher import _drain, _video_data_without_captions

_CACHED = SimpleNamespace(
    segments=[{"text": "cached", "startMs": 0, "endMs": 1000}],
    source="whisper",
    language="en",
)


def _video_data_with_captions() -> MagicMock:
    video_data = _video_data_without_captions()
    video_data.subtitles = [SimpleNamespace(text="live", start=0.0, duration=1.0)]
    video_data.transcript_text = "live"
    video_data.language = "en"
    video_data.caption_track = "manual"
    return video_data


async def _fetch(*, skip_cache: bool) -> tuple[TranscriptData, AsyncMock]:
    store_get = AsyncMock(return_value=_CACHED)
    with (
        patch.object(transcript_fetcher.S3Client, "is_available", return_value=True),
        patch.object(transcript_fetcher.transcript_store, "get", new=store_get),
    ):
        items = await _drain(
            fetch_transcript("dQw4w9WgXcQ", _video_data_with_captions(), 600, skip_cache=skip_cache)
        )
    data = next(item for item in items if isinstance(item, TranscriptData))
    return data, store_get


class TestColdTranscriptFetch:
    async def test_should_not_read_the_s3_cache_when_skip_cache_is_set(self):
        _, store_get = await _fetch(skip_cache=True)

        assert store_get.await_count == 0

    async def test_should_use_the_live_caption_track_when_skip_cache_is_set(self):
        data, _ = await _fetch(skip_cache=True)

        assert data.source == "ytdlp"

    async def test_should_serve_the_s3_cache_when_skip_cache_is_unset(self):
        data, _ = await _fetch(skip_cache=False)

        assert data.source == "s3"
