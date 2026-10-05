"""Provenance-trail tests for the transcript fallback chain.

``fetch_transcript`` fills a ``TranscriptTrail`` (layers that ran and failed,
the caption-429 skip, the S3 origin) and attaches it to the yielded
``TranscriptData``; the trail must also survive a run where every layer fails.
Split from ``test_transcript_fetcher.py`` to keep that file under the size cap.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript_fetcher import fetch_transcript
from tests.test_transcript_fetcher import _drain, _video_data_without_captions


@pytest.fixture(autouse=True)
def _stub_caption_negative_cache():
    """Keep the 429 negative cache away from live Redis (see test_transcript_fetcher)."""
    stub = MagicMock()
    stub.is_marked = AsyncMock(return_value=False)
    stub.mark = AsyncMock()
    with patch.object(transcript_fetcher, "caption_negative_cache", stub):
        yield stub


def _video_data_with_captions(caption_track: str) -> MagicMock:
    """A VideoData stand-in whose yt-dlp caption fetch succeeded."""
    video_data = _video_data_without_captions()
    video_data.subtitles = [SimpleNamespace(text="hi", start=0.0, duration=1.0)]
    video_data.transcript_text = "hi"
    video_data.language = "en"
    video_data.caption_track = caption_track
    return video_data


def _yielded_data(items: list) -> TranscriptData:
    """The TranscriptData an async-gen drain yielded (exactly one per successful run)."""
    return next(item for item in items if isinstance(item, TranscriptData))


def _yielded_trail(items: list) -> TranscriptTrail:
    """The provenance trail attached to the yielded TranscriptData."""
    trail = _yielded_data(items).trail
    assert trail is not None
    return trail


def _whisper_success() -> AsyncMock:
    """A patched ``_try_whisper_transcription`` that succeeds."""
    return AsyncMock(
        return_value=(
            TranscriptData(
                segments=[{"text": "hi", "start": 0.0, "duration": 1.0}],
                raw_text="hi",
                transcript_type="whisper",
                source="whisper",
                language="he",
            ),
            None,
        )
    )


def _whisper_failure() -> AsyncMock:
    """A patched ``_try_whisper_transcription`` that fails."""
    return AsyncMock(return_value=(None, TranscriptError("w", ErrorCode.UNKNOWN_ERROR)))


def _gemini_success() -> AsyncMock:
    """A patched ``_try_gemini_transcription`` that succeeds."""
    return AsyncMock(
        return_value=TranscriptData(
            segments=[{"text": "hi", "start": 0.0, "duration": 1.0}],
            raw_text="hi",
            transcript_type="gemini",
            source="gemini",
            language="he",
        )
    )


class TestTranscriptTrail:
    """The provenance trail records exactly which layers ran and failed, in order."""

    async def _run(
        self,
        trail: TranscriptTrail,
        *,
        whisper: AsyncMock,
        caption: AsyncMock | None = None,
        gemini: AsyncMock | None = None,
        video_data: MagicMock | None = None,
        is_music: bool = False,
        proxy_url: str | None = None,
        s3_available: bool = False,
    ) -> list:
        """Drain the chain with every layer patched; caption API and Gemini fail by default."""
        caption = caption or AsyncMock(side_effect=TranscriptError("boom", ErrorCode.UNKNOWN_ERROR))
        gemini = gemini or AsyncMock(return_value=None)
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=s3_available),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", "fake-key"),
            patch.object(transcript_fetcher.settings, "YOUTUBE_PROXY_URL", proxy_url),
            patch.object(transcript_fetcher, "get_transcript", new=caption),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
            patch.object(transcript_fetcher, "_try_gemini_transcription", new=gemini),
        ):
            return await _drain(
                fetch_transcript(
                    "vid123",
                    video_data or _video_data_without_captions(),
                    duration=300,
                    is_music=is_music,
                    trail=trail,
                )
            )

    async def test_should_record_api_attempt_when_caption_api_times_out(self):
        """Caption API timeout -> Whisper wins: only the API layer is an attempt."""

        async def _hang(_youtube_id):
            await asyncio.sleep(5)

        trail = TranscriptTrail()
        with patch.object(transcript_fetcher.settings, "TRANSCRIPT_FETCH_TIMEOUT", 0.01):
            items = await self._run(trail, whisper=_whisper_success(), caption=_hang)

        data = _yielded_data(items)
        assert data.source == "whisper"
        assert data.trail is trail
        assert trail.attempted == ["api"]

    async def test_should_record_api_and_whisper_when_gemini_wins(self):
        """Caption error -> Whisper fails -> Gemini succeeds: both losers listed in order."""
        trail = TranscriptTrail()
        items = await self._run(trail, whisper=_whisper_failure(), gemini=_gemini_success())

        assert _yielded_data(items).source == "gemini"
        assert _yielded_trail(items).attempted == ["api", "whisper"]

    async def test_should_record_every_layer_before_metadata_fallback(self):
        """All layers fail on a music video: the metadata fallback carries the full trail."""
        trail = TranscriptTrail()
        items = await self._run(trail, whisper=_whisper_failure(), is_music=True)

        assert _yielded_data(items).source == "metadata"
        assert _yielded_trail(items).attempted == ["api", "whisper", "gemini"]

    async def test_should_flag_caption_api_skip_without_counting_an_attempt(
        self, _stub_caption_negative_cache
    ):
        """A negative-cache marker skips the API: flagged as skipped, not listed as attempted."""
        _stub_caption_negative_cache.is_marked = AsyncMock(return_value=True)
        trail = TranscriptTrail()
        items = await self._run(trail, whisper=_whisper_success())

        assert _yielded_data(items).source == "whisper"
        assert trail.caption_api_skipped is True
        assert trail.attempted == []

    async def test_should_label_api_attempt_as_proxy_when_proxy_url_set(self):
        """With YOUTUBE_PROXY_URL set the API layer is labelled 'proxy'."""
        trail = TranscriptTrail()
        await self._run(
            trail, whisper=_whisper_success(), proxy_url="http://user:pass@proxy.example:8080"
        )

        assert trail.attempted == ["proxy"]

    async def test_should_label_api_attempt_as_api_when_proxy_url_blank(self):
        """A blank YOUTUBE_PROXY_URL never proxies, so the label stays 'api'."""
        trail = TranscriptTrail()
        await self._run(trail, whisper=_whisper_success(), proxy_url="")

        assert trail.attempted == ["api"]

    async def test_should_use_manual_caption_track_as_transcript_type(self):
        """yt-dlp subtitles win outright: transcript_type is the picked track kind, no attempts."""
        trail = TranscriptTrail()
        items = await self._run(
            trail, whisper=_whisper_success(), video_data=_video_data_with_captions("manual")
        )

        data = _yielded_data(items)
        assert data.source == "ytdlp"
        assert data.transcript_type == "manual"
        assert _yielded_trail(items).attempted == []

    async def test_should_use_auto_generated_caption_track_as_transcript_type(self):
        """An auto-generated yt-dlp track is reported as such."""
        trail = TranscriptTrail()
        items = await self._run(
            trail,
            whisper=_whisper_success(),
            video_data=_video_data_with_captions("auto-generated"),
        )

        assert _yielded_data(items).transcript_type == "auto-generated"

    async def test_should_record_ytdlp_attempt_when_picked_track_yielded_nothing(self):
        """A picked caption track whose timedtext fetch failed counts as a yt-dlp attempt."""
        video_data = _video_data_without_captions()
        video_data.caption_track = "auto-generated"
        video_data.caption_fetch_error = "http_429"
        trail = TranscriptTrail()
        items = await self._run(trail, whisper=_whisper_success(), video_data=video_data)

        assert _yielded_data(items).source == "whisper"
        assert trail.attempted == ["ytdlp", "api"]

    async def test_should_record_origin_on_s3_hit(self):
        """An S3 hit is not an attempt; the trail keeps the layer that produced the blob."""
        cached = SimpleNamespace(
            segments=[{"text": "hi", "startMs": 0, "endMs": 1000}],
            source="whisper",
            language="en",
        )
        trail = TranscriptTrail()
        with patch.object(
            transcript_fetcher.transcript_store, "get", new=AsyncMock(return_value=cached)
        ):
            items = await self._run(trail, whisper=_whisper_success(), s3_available=True)

        data = _yielded_data(items)
        assert data.source == "s3"
        assert data.transcript_type == "cached-whisper"
        assert trail.origin == "whisper"
        assert trail.attempted == []

    async def test_should_record_s3_failure_and_continue_chain(self):
        """An S3 lookup exception is an attempt (message kept); the chain continues."""
        trail = TranscriptTrail()
        with patch.object(
            transcript_fetcher.transcript_store,
            "get",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            items = await self._run(trail, whisper=_whisper_success(), s3_available=True)

        assert _yielded_data(items).source == "whisper"
        assert trail.attempted == ["s3", "api"]

    async def test_should_keep_trail_when_every_layer_fails(self):
        """The caller-owned trail survives a run that ends in a TranscriptError."""
        trail = TranscriptTrail()
        with pytest.raises(TranscriptError):
            await self._run(trail, whisper=_whisper_failure())

        assert trail.attempted == ["api", "whisper", "gemini"]
