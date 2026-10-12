"""The S3 transcript blob goes stale when the caption picker chooses another track (2.4).

``videos/<id>/transcript.json`` is keyed by video id only and read first, so a
video processed before the picker fix would keep its old track forever. A
caption-origin blob is stale when this run's caption fetch produced subtitles
and the blob's recorded track (key + bucket) differs from this run's pick;
pre-2.4 blobs record none. Audio transcriptions never go stale, and a failed
caption fetch keeps the blob as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript_fetcher import fetch_transcript
from src.services.transcription.transcript_store import CaptionRef, RawTranscript, transcript_store
from tests.test_transcript_fetcher import _drain, _video_data_without_captions


@pytest.fixture(autouse=True)
def _stub_caption_negative_cache():
    """Keep the 429 negative cache away from live Redis (see test_transcript_fetcher)."""
    stub = MagicMock()
    stub.is_marked = AsyncMock(return_value=False)
    stub.mark = AsyncMock()
    with patch.object(transcript_fetcher, "caption_negative_cache", stub):
        yield stub


def _blob(source: str = "ytdlp", caption: CaptionRef | None = None) -> RawTranscript:
    return RawTranscript(
        youtube_id="vid123",
        fetched_at="2026-10-01T00:00:00+00:00",
        source=source,  # type: ignore[arg-type]
        language="nl",
        segments=[{"text": "hallo", "startMs": 0, "endMs": 1000}],
        caption=caption,
    )


def _fresh_captions(caption_lang: str = "en-US", caption_track: str = "manual") -> MagicMock:
    """A VideoData whose caption fetch this run produced subtitles."""
    video_data = _video_data_without_captions()
    video_data.subtitles = [SimpleNamespace(text="hello", start=0.0, duration=1.0)]
    video_data.transcript_text = "hello"
    video_data.language = "en"
    video_data.caption_lang = caption_lang
    video_data.caption_track = caption_track
    return video_data


async def _fetch(
    blob: RawTranscript, video_data: MagicMock
) -> tuple[TranscriptData, TranscriptTrail]:
    trail = TranscriptTrail()
    with (
        patch.object(transcript_fetcher.S3Client, "is_available", return_value=True),
        patch.object(transcript_store, "get", new=AsyncMock(return_value=blob)),
    ):
        items = await _drain(fetch_transcript("vid123", video_data, 600, trail=trail))
    data = next(item for item in items if isinstance(item, TranscriptData))
    return data, trail


class TestStaleCaptionBlob:
    async def test_should_serve_the_blob_when_it_holds_the_track_this_run_picked(self):
        blob = _blob(caption=CaptionRef(lang="en-US", kind="manual"))

        data, trail = await _fetch(blob, _fresh_captions("en-US", "manual"))

        assert (data.source, trail.attempted) == ("s3", [])

    async def test_should_use_the_fresh_captions_when_the_blob_holds_another_track(self):
        """vOkkWuEwUmc: the dev blob is the Dutch manual track."""
        blob = _blob(caption=CaptionRef(lang="nl", kind="manual"))

        data, _trail = await _fetch(blob, _fresh_captions("en-US", "manual"))

        assert (data.source, data.raw_text) == ("ytdlp", "hello")

    async def test_should_record_the_stale_blob_in_the_trail_when_it_is_skipped(self):
        blob = _blob(caption=CaptionRef(lang="nl", kind="manual"))

        _data, trail = await _fetch(blob, _fresh_captions("en-US", "manual"))

        assert (trail.attempted, trail.origin) == (["s3-stale"], None)

    async def test_should_treat_a_pre_picker_caption_blob_as_stale_when_captions_were_fetched(
        self,
    ):
        """Blobs stored before 2.4 record no track: stale once, then re-stored."""
        data, _trail = await _fetch(_blob(caption=None), _fresh_captions("iw", "auto-generated"))

        assert data.source == "ytdlp"

    async def test_should_treat_the_same_key_from_the_other_bucket_as_another_track(self):
        blob = _blob(caption=CaptionRef(lang="en", kind="auto-generated"))

        data, _trail = await _fetch(blob, _fresh_captions("en", "manual"))

        assert data.source == "ytdlp"

    @pytest.mark.parametrize("source", ["whisper", "gemini"])
    async def test_should_keep_an_audio_transcription_blob_when_fresh_captions_disagree(
        self, source: str
    ):
        """TNhaISOUy6Q: its dev blob is a Whisper transcript — picker-independent."""
        data, trail = await _fetch(
            _blob(source=source), _fresh_captions("pl-orig", "auto-generated")
        )

        assert (data.source, trail.origin) == ("s3", source)

    async def test_should_use_the_blob_as_before_when_the_caption_fetch_failed(self):
        video_data = _video_data_without_captions()
        video_data.caption_track = "manual"
        video_data.caption_lang = "en-US"
        video_data.caption_fetch_error = "http_429"

        data, trail = await _fetch(_blob(caption=CaptionRef(lang="nl", kind="manual")), video_data)

        assert (data.source, trail.attempted) == ("s3", [])
