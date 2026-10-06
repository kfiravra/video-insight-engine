"""Regression tests for the transcript fallback chain language signal.

Bug: a fully-Hebrew video whose captions 429'd and whose Whisper run timed out
fell to the Gemini fallback, which built ``TranscriptData`` with no language.
The pipeline then defaulted to ``en``, skipped translation, and hid the FE
language toggle. The fix detects language from the Gemini transcript text.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode, NormalizedTranscript
from src.services.pipeline.pipeline_helpers import TranscriptData
from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript_fetcher import (
    _try_gemini_transcription,
    fetch_transcript,
)


@pytest.fixture(autouse=True)
def _stub_caption_negative_cache():
    """Keep the 429 negative cache away from live Redis (and event loops).

    The singleton's lazy aioredis client would otherwise bind to the first
    test's event loop and poison every later test ("attached to a different
    loop"). Default behavior: unmarked, mark() is a no-op.
    """
    stub = MagicMock()
    stub.is_marked = AsyncMock(return_value=False)
    stub.mark = AsyncMock()
    with patch.object(transcript_fetcher, "caption_negative_cache", stub):
        yield stub


HEBREW_TEXT = (
    "שלום וברוכים הבאים לשידור המחזק. היום נדבר על כוח הבחירה החופשית "
    "ועל החשיבות של פתיחת הלב, התפילה והאמונה בדרך אל הגאולה."
)


class TestGeminiLanguageDetection:
    """Gemini transcription must carry a detected language, not None."""

    async def test_detects_hebrew_from_gemini_transcript(self):
        """A Hebrew Gemini transcript yields TranscriptData.language == 'he'."""
        gemini_result = NormalizedTranscript(
            text=HEBREW_TEXT,
            segments=[],
            source="gemini",
        )

        with (
            patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", "fake-key"),
            patch.object(
                transcript_fetcher,
                "transcribe_with_gemini",
                new=AsyncMock(return_value=gemini_result),
            ),
        ):
            result = await _try_gemini_transcription("vid123", duration=3465, is_music=False)

        assert result is not None
        assert result.source == "gemini"
        assert result.language == "he"

    async def test_honors_transcriber_provided_language(self):
        """When the transcriber already set a language, the fetcher trusts it
        rather than re-detecting from text."""
        gemini_result = NormalizedTranscript(
            text="This English-looking sentence would otherwise detect as en.",
            segments=[],
            source="gemini",
            language="he",
        )

        with (
            patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", "fake-key"),
            patch.object(
                transcript_fetcher,
                "transcribe_with_gemini",
                new=AsyncMock(return_value=gemini_result),
            ),
        ):
            result = await _try_gemini_transcription("vid123", duration=100, is_music=False)

        assert result is not None
        assert result.language == "he"

    async def test_no_api_key_returns_none(self):
        """Without a Gemini key the helper short-circuits to None."""
        with patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", ""):
            result = await _try_gemini_transcription("vid123", duration=100, is_music=False)

        assert result is None


def _video_data_without_captions() -> MagicMock:
    """A VideoData stand-in with no yt-dlp subtitles, forcing the API branch."""
    video_data = MagicMock()
    video_data.subtitles = []
    # MagicMock attrs are truthy by default — an unset flag would read as
    # "rate limited" and spuriously write the 429 negative-cache marker, and
    # unset caption fields would not be the None a real captionless VideoData
    # carries (the trail keys off them).
    video_data.captions_rate_limited = False
    video_data.caption_track = None
    video_data.caption_lang = None
    video_data.caption_fetch_error = None
    return video_data


async def _drain(gen) -> list:
    """Collect every item yielded by an async generator."""
    return [item async for item in gen]


class TestAudioFallbackOnCaptionError:
    """Caption-fetch errors fall back to audio transcription unless the video itself is inaccessible."""

    async def _run(
        self,
        caption_error: TranscriptError,
        whisper: AsyncMock,
        duration: int = 300,
        max_minutes: int = 600,
    ) -> list:
        whisper_data = TranscriptData(
            segments=[{"text": "hi", "start": 0.0, "duration": 1.0}],
            raw_text="hi",
            transcript_type="whisper",
            source="whisper",
            language="he",
        )
        whisper.return_value = (whisper_data, None)
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", max_minutes),
            patch.object(
                transcript_fetcher, "get_transcript", new=AsyncMock(side_effect=caption_error)
            ),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
        ):
            return await _drain(
                fetch_transcript("vid123", _video_data_without_captions(), duration=duration)
            )

    async def test_should_fall_back_to_whisper_when_captions_rate_limited(self):
        """A 429 rate-limit on captions must still attempt audio transcription."""
        whisper = AsyncMock()
        items = await self._run(TranscriptError("rate limited", ErrorCode.RATE_LIMITED), whisper)

        whisper.assert_awaited_once()
        assert any(isinstance(i, TranscriptData) and i.source == "whisper" for i in items)

    async def test_should_fall_back_to_whisper_on_unknown_caption_error(self):
        """A generic caption-fetch failure must still attempt audio transcription."""
        whisper = AsyncMock()
        items = await self._run(TranscriptError("boom", ErrorCode.UNKNOWN_ERROR), whisper)

        whisper.assert_awaited_once()
        assert any(isinstance(i, TranscriptData) and i.source == "whisper" for i in items)

    async def test_should_not_attempt_whisper_when_video_unavailable(self):
        """A genuine video-access failure must propagate without trying audio."""
        whisper = AsyncMock()
        with pytest.raises(TranscriptError) as exc_info:
            await self._run(TranscriptError("private", ErrorCode.VIDEO_UNAVAILABLE), whisper)

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE
        whisper.assert_not_awaited()

    async def test_should_not_attempt_whisper_when_video_too_long(self):
        """The duration cap still wins even for a fall-back-eligible caption error."""
        whisper = AsyncMock()
        with pytest.raises(TranscriptError) as exc_info:
            await self._run(
                TranscriptError("rate limited", ErrorCode.RATE_LIMITED),
                whisper,
                duration=60 * 60,
                max_minutes=10,
            )

        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        whisper.assert_not_awaited()


class TestDecoupledAudioGates:
    """WHISPER_ENABLED must gate only Whisper — Gemini stays reachable."""

    def _gemini_data(self) -> TranscriptData:
        return TranscriptData(
            segments=[{"text": "hi", "start": 0.0, "duration": 1.0}],
            raw_text="hi",
            transcript_type="gemini",
            source="gemini",
            language="he",
        )

    def _patches(
        self,
        *,
        whisper_enabled: bool,
        gemini_key: str,
        caption_error: TranscriptError | None,
        whisper: AsyncMock,
        gemini: AsyncMock,
        max_minutes: int = 600,
    ):
        caption_mock = AsyncMock(side_effect=caption_error) if caption_error else AsyncMock()
        return [
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", whisper_enabled),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", max_minutes),
            patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", gemini_key),
            patch.object(transcript_fetcher, "get_transcript", new=caption_mock),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
            patch.object(transcript_fetcher, "_try_gemini_transcription", new=gemini),
        ]

    async def test_gemini_runs_when_whisper_disabled(self):
        """Disabling Whisper must not kill the Gemini audio path."""
        whisper = AsyncMock()
        gemini = AsyncMock(return_value=self._gemini_data())
        patches = self._patches(
            whisper_enabled=False,
            gemini_key="fake-key",
            caption_error=TranscriptError("rate limited", ErrorCode.RATE_LIMITED),
            whisper=whisper,
            gemini=gemini,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            items = await _drain(
                fetch_transcript("vid123", _video_data_without_captions(), duration=300)
            )

        whisper.assert_not_awaited()
        gemini.assert_awaited_once()
        assert any(isinstance(i, TranscriptData) and i.source == "gemini" for i in items)

    async def test_raises_when_whisper_disabled_and_no_gemini_key(self):
        """With both audio paths gated, the original caption error propagates."""
        whisper = AsyncMock()
        gemini = AsyncMock()
        patches = self._patches(
            whisper_enabled=False,
            gemini_key="",
            caption_error=TranscriptError("rate limited", ErrorCode.RATE_LIMITED),
            whisper=whisper,
            gemini=gemini,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(TranscriptError) as exc_info:
                await _drain(
                    fetch_transcript("vid123", _video_data_without_captions(), duration=300)
                )

        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        whisper.assert_not_awaited()
        gemini.assert_not_awaited()

    async def test_duration_cap_still_gates_both_paths(self):
        """Over the duration cap, neither Whisper nor Gemini runs."""
        whisper = AsyncMock()
        gemini = AsyncMock()
        patches = self._patches(
            whisper_enabled=True,
            gemini_key="fake-key",
            caption_error=TranscriptError("rate limited", ErrorCode.RATE_LIMITED),
            whisper=whisper,
            gemini=gemini,
            max_minutes=10,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with pytest.raises(TranscriptError) as exc_info:
                await _drain(
                    fetch_transcript("vid123", _video_data_without_captions(), duration=60 * 60)
                )

        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        whisper.assert_not_awaited()
        gemini.assert_not_awaited()

    async def test_marker_set_skips_caption_api_and_goes_to_audio(
        self, _stub_caption_negative_cache
    ):
        """A recent 429 marker skips youtube-transcript-api entirely."""
        _stub_caption_negative_cache.is_marked = AsyncMock(return_value=True)
        caption_api = AsyncMock()
        whisper = AsyncMock(
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
        gemini = AsyncMock()
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(transcript_fetcher, "get_transcript", new=caption_api),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
            patch.object(transcript_fetcher, "_try_gemini_transcription", new=gemini),
        ):
            items = await _drain(
                fetch_transcript("vid123", _video_data_without_captions(), duration=300)
            )

        caption_api.assert_not_awaited()
        whisper.assert_awaited_once()
        assert any(isinstance(i, TranscriptData) and i.source == "whisper" for i in items)

    async def test_marker_skip_with_all_audio_gated_raises_no_transcript(
        self, _stub_caption_negative_cache
    ):
        """Marker set but no audio path available -> NO_TRANSCRIPT."""
        _stub_caption_negative_cache.is_marked = AsyncMock(return_value=True)
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", False),
            patch.object(transcript_fetcher.settings, "GEMINI_API_KEY", ""),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
        ):
            with pytest.raises(TranscriptError) as exc_info:
                await _drain(
                    fetch_transcript("vid123", _video_data_without_captions(), duration=300)
                )

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT

    async def test_rate_limited_caption_error_writes_marker(self, _stub_caption_negative_cache):
        """A 429 from youtube-transcript-api records the negative-cache marker."""
        whisper = AsyncMock(return_value=(None, TranscriptError("w", ErrorCode.UNKNOWN_ERROR)))
        gemini = AsyncMock(return_value=None)
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(
                transcript_fetcher,
                "get_transcript",
                new=AsyncMock(side_effect=TranscriptError("429", ErrorCode.RATE_LIMITED)),
            ),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
            patch.object(transcript_fetcher, "_try_gemini_transcription", new=gemini),
        ):
            with pytest.raises(TranscriptError):
                await _drain(
                    fetch_transcript("vid123", _video_data_without_captions(), duration=300)
                )

        _stub_caption_negative_cache.mark.assert_awaited_once()

    async def test_unknown_caption_error_does_not_write_marker(self, _stub_caption_negative_cache):
        """Only RATE_LIMITED caption errors write the marker."""
        whisper = AsyncMock(
            return_value=(
                TranscriptData(
                    segments=[],
                    raw_text="hi",
                    transcript_type="whisper",
                    source="whisper",
                ),
                None,
            )
        )
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(
                transcript_fetcher,
                "get_transcript",
                new=AsyncMock(side_effect=TranscriptError("boom", ErrorCode.UNKNOWN_ERROR)),
            ),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
        ):
            await _drain(fetch_transcript("vid123", _video_data_without_captions(), duration=300))

        _stub_caption_negative_cache.mark.assert_not_awaited()

    async def test_video_data_rate_limit_flag_writes_marker(self, _stub_caption_negative_cache):
        """The metadata-phase timedtext 429 flag records the marker."""
        video_data = _video_data_without_captions()
        video_data.captions_rate_limited = True
        whisper = AsyncMock(
            return_value=(
                TranscriptData(
                    segments=[],
                    raw_text="hi",
                    transcript_type="whisper",
                    source="whisper",
                ),
                None,
            )
        )
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(
                transcript_fetcher,
                "get_transcript",
                new=AsyncMock(side_effect=TranscriptError("429", ErrorCode.RATE_LIMITED)),
            ),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=whisper),
        ):
            await _drain(fetch_transcript("vid123", video_data, duration=300))

        assert _stub_caption_negative_cache.mark.await_count >= 1

    async def test_timeout_branch_falls_to_gemini_when_whisper_disabled(self):
        """A caption-API timeout still reaches Gemini when Whisper is off."""

        async def _hang(_youtube_id, **_kwargs):

            await asyncio.sleep(5)

        whisper = AsyncMock()
        gemini = AsyncMock(return_value=self._gemini_data())
        patches = self._patches(
            whisper_enabled=False,
            gemini_key="fake-key",
            caption_error=None,
            whisper=whisper,
            gemini=gemini,
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(transcript_fetcher.settings, "TRANSCRIPT_FETCH_TIMEOUT", 0.01),
            patch.object(transcript_fetcher, "get_transcript", new=_hang),
        ):
            items = await _drain(
                fetch_transcript("vid123", _video_data_without_captions(), duration=300)
            )

        whisper.assert_not_awaited()
        gemini.assert_awaited_once()
        assert any(isinstance(i, TranscriptData) and i.source == "gemini" for i in items)


class TestTimedtext429Marker:
    """The metadata-phase timedtext fetch rotates through the proxy exits
    itself, so its 429 flag means every tried exit 429'd: the marker is
    written and the caption API (same exits) is skipped for audio."""

    @staticmethod
    def _whisper() -> AsyncMock:
        return AsyncMock(
            return_value=(
                TranscriptData(
                    segments=[], raw_text="hi", transcript_type="whisper", source="whisper"
                ),
                None,
            )
        )

    async def _run(self, rate_limited: bool, get_transcript: AsyncMock) -> list:
        video_data = _video_data_without_captions()
        video_data.captions_rate_limited = rate_limited
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher.settings, "WHISPER_ENABLED", True),
            patch.object(transcript_fetcher.settings, "WHISPER_MAX_DURATION_MINUTES", 600),
            patch.object(transcript_fetcher, "get_transcript", new=get_transcript),
            patch.object(transcript_fetcher, "_try_whisper_transcription", new=self._whisper()),
        ):
            return await _drain(fetch_transcript("vid123", video_data, duration=300))

    async def test_should_mark_and_skip_captions_when_every_exit_429d(
        self, _stub_caption_negative_cache
    ):
        async def _mark() -> None:
            _stub_caption_negative_cache.is_marked.return_value = True

        _stub_caption_negative_cache.mark = AsyncMock(side_effect=_mark)
        get_transcript = AsyncMock()

        items = await self._run(True, get_transcript)

        _stub_caption_negative_cache.mark.assert_awaited_once()
        get_transcript.assert_not_awaited()
        assert items[-1].source == "whisper"

    async def test_should_try_the_caption_api_unmarked_when_timedtext_was_not_rate_limited(
        self, _stub_caption_negative_cache
    ):
        get_transcript = AsyncMock(return_value=([{"text": "hi"}], "hi", "manual", "en"))

        items = await self._run(False, get_transcript)

        get_transcript.assert_awaited_once_with("vid123")
        _stub_caption_negative_cache.mark.assert_not_awaited()
        assert items[-1].transcript_type == "manual"
