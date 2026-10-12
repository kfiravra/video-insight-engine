"""The audio-only transcript entry and the audio pass that uses it (pipeline-1min 2.5).

``fetch_audio_transcript`` is Whisper → Gemini within the chain's own gates —
never S3, captions or youtube-transcript-api; the chain's last hop
(``fetch_transcript``) runs the same code, covered by test_transcript_fetcher /
test_transcript_trail. ``run_audio_transcript_pass`` re-runs the transcript
phase on it after the tier probe judged a caption transcript unusable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.pipeline.phases import transcript as transcript_phase
from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript_fetcher import (
    audio_fallback_allowed,
    fetch_audio_transcript,
)


def _data(source: str, text: str = "hello from the audio track") -> TranscriptData:
    return TranscriptData(
        segments=[{"text": text, "start": 0.0, "duration": 2.0}],
        raw_text=text,
        transcript_type=source,
        source=source,
        language="en",
    )


async def _drain(gen: AsyncGenerator[str | TranscriptData, None]) -> list[str | TranscriptData]:
    return [item async for item in gen]


def _gates(whisper: bool = True, gemini_key: str = "", cap_minutes: int = 60) -> list[Any]:
    return [
        patch.object(settings, "WHISPER_ENABLED", whisper),
        patch.object(settings, "GEMINI_API_KEY", gemini_key),
        patch.object(settings, "WHISPER_MAX_DURATION_MINUTES", cap_minutes),
    ]


async def _fetch(
    trail: TranscriptTrail,
    *,
    whisper: AsyncMock,
    gemini: AsyncMock | None = None,
    whisper_enabled: bool = True,
) -> list[str | TranscriptData]:
    patches = [
        *_gates(whisper=whisper_enabled, gemini_key="fake-key"),
        patch.object(transcript_fetcher, "_try_whisper_transcription", whisper),
        patch.object(
            transcript_fetcher, "_try_gemini_transcription", gemini or AsyncMock(return_value=None)
        ),
    ]
    for item in patches:
        item.start()
    try:
        return await _drain(fetch_audio_transcript("vid123", 300, trail=trail))
    finally:
        for item in patches:
            item.stop()


def _whisper_error() -> AsyncMock:
    return AsyncMock(return_value=(None, TranscriptError("w", ErrorCode.UNKNOWN_ERROR)))


class TestAudioFallbackAllowed:
    @pytest.mark.parametrize(
        ("whisper", "gemini_key", "minutes", "expected"),
        [
            (True, "", 10, True),
            (False, "fake-key", 10, True),
            (False, "", 10, False),
            (True, "fake-key", 61, False),
        ],
    )
    def test_should_follow_the_chain_gates(
        self, whisper: bool, gemini_key: str, minutes: int, expected: bool
    ) -> None:
        patches = _gates(whisper=whisper, gemini_key=gemini_key)
        for item in patches:
            item.start()
        try:
            assert audio_fallback_allowed(minutes * 60) is expected
        finally:
            for item in patches:
                item.stop()


class TestFetchAudioTranscript:
    async def test_should_never_read_s3_or_the_caption_api(self) -> None:
        caption_api, s3_get = AsyncMock(), AsyncMock()
        whisper = AsyncMock(return_value=(_data("whisper"), None))

        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=True),
            patch.object(transcript_fetcher, "read_cached_transcript", s3_get),
            patch.object(transcript_fetcher, "get_transcript", caption_api),
        ):
            await _fetch(TranscriptTrail(), whisper=whisper)

        assert (caption_api.await_count, s3_get.await_count) == (0, 0)

    async def test_should_yield_the_whisper_transcript_with_the_trail(self) -> None:
        trail = TranscriptTrail()

        items = await _fetch(trail, whisper=AsyncMock(return_value=(_data("whisper"), None)))

        data = items[-1]
        assert isinstance(data, TranscriptData) and (data.source, data.trail) == ("whisper", trail)

    async def test_should_fall_back_to_gemini_when_whisper_fails(self) -> None:
        trail = TranscriptTrail()
        gemini = AsyncMock(return_value=_data("gemini"))

        items = await _fetch(trail, whisper=_whisper_error(), gemini=gemini)

        assert (items[-1].source, trail.attempted) == ("gemini", ["whisper"])  # type: ignore[union-attr]

    async def test_should_raise_the_whisper_error_when_every_audio_layer_fails(self) -> None:
        trail = TranscriptTrail()

        with pytest.raises(TranscriptError) as caught:
            await _fetch(trail, whisper=_whisper_error())

        assert (caught.value.code, trail.attempted) == (
            ErrorCode.UNKNOWN_ERROR,
            ["whisper", "gemini"],
        )

    async def test_should_raise_no_transcript_when_whisper_is_off_and_gemini_fails(self) -> None:
        with pytest.raises(TranscriptError) as caught:
            await _fetch(TranscriptTrail(), whisper=AsyncMock(), whisper_enabled=False)

        assert caught.value.code is ErrorCode.NO_TRANSCRIPT


def _pass_ctx(trail: TranscriptTrail) -> SimpleNamespace:
    caption = _data("s3", "garbage caption words")
    caption.trail = trail
    return SimpleNamespace(
        youtube_id="vid123",
        video_data=SimpleNamespace(context=SimpleNamespace(category="coding"), duration=300),
        transcript_data=caption,
        transcript_trail=trail,
        prompt_segments=list(caption.segments),
        source_language_code="pl",
        clean_text="garbage caption words",
    )


async def _run_pass(ctx: SimpleNamespace, delay: float = 0.0) -> None:
    def audio(*_args: object, trail: TranscriptTrail, **_kwargs: object) -> Any:
        async def _gen() -> AsyncGenerator[TranscriptData, None]:
            await asyncio.sleep(delay)
            data = _data("whisper", "the speech as Whisper heard it")
            data.trail = trail
            yield data

        return _gen()

    with (
        patch.object(transcript_phase, "fetch_audio_transcript", audio),
        patch.object(transcript_phase, "get_sponsor_segments", AsyncMock(return_value=[])),
        patch.object(settings, "TRANSCRIPT_CLEANING_ENABLED", False),
    ):
        _ = [event async for event in transcript_phase.run_audio_transcript_pass(ctx)]  # type: ignore[arg-type]


class TestRunAudioTranscriptPass:
    async def test_should_add_the_audio_wall_time_to_the_fetch_wall_time(self) -> None:
        trail = TranscriptTrail(fetch_wall_ms=100)

        await _run_pass(_pass_ctx(trail), delay=0.05)

        assert trail.fetch_wall_ms is not None and trail.fetch_wall_ms >= 140

    async def test_should_clean_the_whisper_text_into_clean_text(self) -> None:
        ctx = _pass_ctx(TranscriptTrail())

        await _run_pass(ctx)

        assert ctx.clean_text.startswith("the speech as Whisper heard it")

    async def test_should_drop_the_s3_origin_once_the_audio_transcript_wins(self) -> None:
        trail = TranscriptTrail(origin="ytdlp")

        await _run_pass(_pass_ctx(trail))

        assert trail.origin is None

    async def test_should_keep_the_current_transcript_when_the_audio_layers_fail(self) -> None:
        ctx = _pass_ctx(TranscriptTrail())
        caption = ctx.transcript_data

        def audio(*_args: object, **_kwargs: object) -> Any:
            async def _gen() -> AsyncGenerator[TranscriptData, None]:
                raise TranscriptError("w", ErrorCode.UNKNOWN_ERROR)
                yield  # pragma: no cover — makes this an async generator

            return _gen()

        with patch.object(transcript_phase, "fetch_audio_transcript", audio):
            _ = [event async for event in transcript_phase.run_audio_transcript_pass(ctx)]  # type: ignore[arg-type]

        assert ctx.transcript_data is caption
