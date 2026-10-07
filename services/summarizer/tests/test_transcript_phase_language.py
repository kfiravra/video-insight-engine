"""Regression test for transcript-phase source-language detection.

The pipeline is English-canonical: ``ctx.language`` always stays ``"en"`` (all
generation runs in English) and the DETECTED original language is recorded on
``ctx.source_language_code`` to drive the final English→source translation pass.

Bug guarded: a transcript source that returns no language (the Gemini fallback)
must still have its language detected from content, so a non-English video gets
a sourceLanguage block + FE toggle instead of being silently treated as English.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.pipeline.phases import transcript as transcript_phase
from src.services.pipeline.pipeline_helpers import TranscriptData

HEBREW_TEXT = (
    "שלום וברוכים הבאים לשידור המחזק. היום נדבר על כוח הבחירה החופשית "
    "ועל החשיבות של פתיחת הלב, התפילה והאמונה בדרך אל הגאולה."
)


async def _drain(gen) -> None:
    async for _ in gen:
        pass


def _build_ctx() -> SimpleNamespace:
    """Minimal PipelineContext stand-in for run_phase_transcript."""
    video_data = SimpleNamespace(
        context=SimpleNamespace(category="education"),
        duration=3465,
    )
    return SimpleNamespace(
        youtube_id="vid123",
        video_data=video_data,
        language="en",  # stays English (English-canonical generation)
        is_rtl=False,
        source_language_code=None,
        clean_text=None,
        transcript_data=None,
        transcript_trail=None,
    )


class TestTranscriptPhaseLanguageNet:
    async def test_detects_language_when_source_omits_it(self):
        """A languageless Hebrew transcript records source_language_code='he'."""
        ctx = _build_ctx()

        def fake_fetch(*_args, **_kwargs):
            async def _gen():
                yield TranscriptData(
                    segments=[{"text": HEBREW_TEXT, "start": 0, "duration": 5}],
                    raw_text=HEBREW_TEXT,
                    transcript_type="gemini",
                    source="gemini",
                    language=None,  # the Gemini-fallback signature
                )

            return _gen()

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.source_language_code == "he"
        assert ctx.language == "en"  # generation stays English
        assert ctx.is_rtl is False

    async def test_respects_explicit_source_language(self):
        """An explicit source language is recorded verbatim on source_language_code."""
        ctx = _build_ctx()

        def fake_fetch(*_args, **_kwargs):
            async def _gen():
                yield TranscriptData(
                    segments=[{"text": "hello world", "start": 0, "duration": 5}],
                    raw_text="hello world this is plainly english content here",
                    transcript_type="whisper",
                    source="whisper",
                    language="ar",
                )

            return _gen()

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.source_language_code == "ar"
        assert ctx.language == "en"


def _raising_fetch(exc: BaseException) -> Callable[..., AsyncGenerator[str, None]]:
    """fetch_transcript stand-in whose generator raises before yielding anything."""

    def fake_fetch(*_args: object, **_kwargs: object) -> AsyncGenerator[str, None]:
        async def _gen() -> AsyncGenerator[str, None]:
            raise exc
            yield  # unreachable — only here so _gen is an async generator

        return _gen()

    return fake_fetch


class TestTranscriptPhaseTrail:
    """The phase hangs a TranscriptTrail on ctx for successful AND failed fetches."""

    async def test_attaches_trail_with_timing_when_fetch_succeeds(self):
        """The trail handed to fetch_transcript lands on ctx with wall time and no error."""
        ctx = _build_ctx()
        seen_trails = []

        def fake_fetch(*_args, **kwargs):
            seen_trails.append(kwargs["trail"])

            async def _gen():
                yield TranscriptData(
                    segments=[{"text": "hello world", "start": 0, "duration": 5}],
                    raw_text="hello world this is plainly english content here",
                    transcript_type="auto-generated",
                    source="ytdlp",
                    language="en",
                )

            return _gen()

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.transcript_trail is seen_trails[0]
        assert ctx.transcript_trail.error_code is None
        assert isinstance(ctx.transcript_trail.fetch_wall_ms, int)
        assert ctx.transcript_trail.fetch_wall_ms >= 0

    async def test_records_error_code_when_fetch_raises_transcript_error(self):
        """A TranscriptError from the chain re-raises with its code stamped on the trail."""
        ctx = _build_ctx()
        fake_fetch = _raising_fetch(TranscriptError("no", ErrorCode.NO_TRANSCRIPT))

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
            pytest.raises(TranscriptError),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.transcript_trail.error_code == "NO_TRANSCRIPT"
        assert isinstance(ctx.transcript_trail.fetch_wall_ms, int)

    async def test_records_no_transcript_when_fetch_yields_only_events(self):
        """A chain that emits SSE events but no TranscriptData is a NO_TRANSCRIPT failure."""
        ctx = _build_ctx()

        def fake_fetch(*_args, **_kwargs):
            async def _gen():
                yield 'event: phase\ndata: {"phase": "transcript"}\n\n'

            return _gen()

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
            pytest.raises(TranscriptError) as exc_info,
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert exc_info.value.code is ErrorCode.NO_TRANSCRIPT
        assert ctx.transcript_trail.error_code == "NO_TRANSCRIPT"

    async def test_records_cancelled_when_fetch_is_cancelled(self):
        """A cancelled producer stamps CANCELLED so the block is not read as a chain failure."""
        ctx = _build_ctx()
        fake_fetch = _raising_fetch(asyncio.CancelledError())

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
            pytest.raises(asyncio.CancelledError),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.transcript_trail.error_code == "CANCELLED"

    async def test_records_unknown_error_when_fetch_raises_unexpectedly(self):
        """A non-TranscriptError from the chain propagates and is recorded as UNKNOWN_ERROR."""
        ctx = _build_ctx()
        fake_fetch = _raising_fetch(RuntimeError("boom"))

        with (
            patch.object(transcript_phase, "fetch_transcript", fake_fetch),
            patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
            patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
            pytest.raises(RuntimeError),
        ):
            await _drain(transcript_phase.run_phase_transcript(ctx))

        assert ctx.transcript_trail.error_code == "UNKNOWN_ERROR"


async def _run_with_language(text: str, language: str) -> SimpleNamespace:
    """Run the transcript phase on one segment whose source reports ``language``."""
    ctx = _build_ctx()

    def fake_fetch(*_args, **_kwargs):
        async def _gen():
            yield TranscriptData(
                segments=[{"text": text, "start": 0, "duration": 5}],
                raw_text=text,
                transcript_type="manual",
                source="ytdlp",
                language=language,
            )

        return _gen()

    with (
        patch.object(transcript_phase, "fetch_transcript", fake_fetch),
        patch.object(transcript_phase.settings, "TRANSCRIPT_CLEANING_ENABLED", False),
        patch.object(transcript_phase, "get_sponsor_segments", new=AsyncMock(return_value=[])),
    ):
        await _drain(transcript_phase.run_phase_transcript(ctx))
    return ctx


class TestTranscriptPhaseFillerLanguage:
    """1a.5: basic cleaning drops English fillers, and only from English sources."""

    async def test_should_drop_fillers_from_english_clean_text(self):
        ctx = await _run_with_language("so um we basically start with the dough", "en")

        assert ctx.clean_text == "so we start with the dough"

    async def test_should_keep_um_in_portuguese_clean_text(self):
        text = "Adicione um quilo de farinha e misture bem com a manteiga"

        ctx = await _run_with_language(text, "pt")

        assert ctx.clean_text == text
