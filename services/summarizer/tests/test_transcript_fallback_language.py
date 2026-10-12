"""The source language after the 2.5 fallback: garbage never pays for a translation batch.

A run that keeps the transcript the probe judged unusable drops the garbage
track's language label (TNhaISOUy6Q: ``pl`` on English speech), so neither the
translation phase nor the RAG translation runs. Shares the text-branch harness
of ``test_transcript_fallback.py`` (boundaries faked, the text branch,
transcript phase and probe as production code).
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline.phases import translation as translation_phase
from tests.test_transcript_fallback import (
    GARBAGE_PL_ASR,
    HEBREW_CAPTIONS,
    Fakes,
    _ctx,
    _transcript,
    _whisper_failed,
    run_text_branch,
)


def _hebrew_whisper() -> AsyncMock:
    return AsyncMock(return_value=(_transcript(HEBREW_CAPTIONS, "whisper", "he"), None))


class TestKeptUnusableTranscript:
    # Every way a non-music run keeps the transcript the first probe judged
    # unusable: (duration, first-pass source, fakes). S3 without an origin = a
    # blob whose producer is no longer recorded.
    @pytest.mark.parametrize(
        ("duration", "source", "make_fakes"),
        [
            (600, "ytdlp", lambda: Fakes(whisper_enabled=False)),
            (2 * 3600, "ytdlp", Fakes),
            (600, "ytdlp", lambda: Fakes(whisper=_whisper_failed())),
            (600, "s3", Fakes),
            (600, "whisper", Fakes),
        ],
        ids=["no-audio-layer", "over-the-cap", "audio-pass-failed", "s3-no-origin", "audio"],
    )
    async def test_should_drop_the_source_language_when_the_unusable_transcript_is_kept(
        self, duration: int, source: str, make_fakes: Callable[[], Fakes]
    ) -> None:
        ctx = _ctx(duration=duration)

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR, source), make_fakes())

        assert ctx.source_language_code is None

    async def test_should_skip_the_translation_phase_when_the_unusable_transcript_is_kept(
        self,
    ) -> None:
        ctx = _ctx()
        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes(whisper_enabled=False))
        translate = AsyncMock(return_value={})

        with patch.object(translation_phase, "translate_to_source", translate):
            events = [
                e async for e in translation_phase.run_phase_translation(ctx, MagicMock(), "v")
            ]

        assert (events, translate.await_count) == ([], 0)


class TestLanguageKept:
    async def test_should_take_the_language_of_the_audio_transcript_when_the_swap_succeeds(
        self,
    ) -> None:
        ctx = _ctx()

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes(whisper=_hebrew_whisper()))

        assert ctx.source_language_code == "he"

    async def test_should_keep_the_source_language_of_a_music_video(self) -> None:
        # 60 s keeps the words-per-second above the sound-only rule's threshold.
        ctx = _ctx(category="music", duration=60)

        await run_text_branch(ctx, _transcript(GARBAGE_PL_ASR), Fakes())

        assert ctx.source_language_code == "pl"

    async def test_should_keep_the_source_language_when_the_probe_says_usable(self) -> None:
        ctx = _ctx()

        await run_text_branch(ctx, _transcript(HEBREW_CAPTIONS, language="he"), Fakes())

        assert ctx.source_language_code == "he"
