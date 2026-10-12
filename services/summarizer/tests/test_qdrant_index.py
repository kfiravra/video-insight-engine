"""Transcript points of a non-English video: English text without re-transcribing (2.3).

The embedding model is English-only. ``index_in_qdrant`` awaits nothing: an
English source stores at once, a non-English one picks its English text in the
background — Whisper translate of the audio only when this run transcribed it
(cached, today's path), the transcript as is when it already reads as English,
otherwise a text translation of the transcript (captions, S3 hits, Gemini:
never an audio download or a Whisper call) — and embeds the original text when
that fails. Chunk times follow the text: the SponsorBlock-cut segments for the
transcript's own text, the whole recording's for Whisper's translation. The
non-English task is kept on ``ctx.rag_transcript_task`` for the runner's
bounded wait.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.services.pipeline.phases import qdrant_index
from src.services.pipeline.pipeline_helpers import TranscriptData, TranscriptTrail
from src.services.transcript.english import EnglishTranscript
from src.services.transcription import whisper_transcriber

_ARABIC = "هذا البرغر مغمور بصلصة سرية تحتفظ العائلة بوصفتها منذ أكثر من سبعين عاما. " * 4
_ENGLISH_DUB = (
    "Our AI agent just returned eight thousand bags to the customer instead of eighty. "
    "Let's imagine for a moment that we have an online store with an agent that handles refunds."
)
_RAW_SEGMENTS = [
    {"text": "مقدمة إعلانية", "start": 0.0, "duration": 5.0},
    {"text": "هذا البرغر مغمور بصلصة سرية", "start": 5.0, "duration": 4.0},
]
_PROMPT_SEGMENTS = _RAW_SEGMENTS[1:]
_ENGLISH = EnglishTranscript(
    text="This burger is smothered in a secret sauce.",
    blocks=[
        {"text": "This burger is smothered in a secret sauce.", "startMs": 5000, "endMs": 9000}
    ],
)
_AUDIO = Path("/tmp/ytid_cached.mp3")


def _ctx(
    *,
    language: str | None = "ar",
    text: str = _ARABIC,
    source: str = "ytdlp",
    origin: str | None = None,
) -> SimpleNamespace:
    transcript = TranscriptData(
        segments=list(_RAW_SEGMENTS), raw_text=text, transcript_type="auto-generated", source=source
    )
    transcript.trail = TranscriptTrail(origin=origin)
    return SimpleNamespace(
        youtube_id="ytid",
        source_language_code=language,
        clean_text=text,
        transcript_data=transcript,
        prompt_segments=list(_PROMPT_SEGMENTS),
        llm_service=MagicMock(name="llm_service"),
        assembled_tabs=[],
        visual_annotations="",
    )


@pytest.fixture
def stores() -> Iterator[SimpleNamespace]:
    """The three Qdrant writers, stubbed."""
    with (
        patch.object(qdrant_index, "store_transcript_chunks", new=AsyncMock()) as transcript,
        patch.object(qdrant_index, "store_default_output_chunks", new=AsyncMock()) as output,
        patch.object(qdrant_index, "store_visual_chunks", new=AsyncMock()) as visual,
    ):
        yield SimpleNamespace(transcript=transcript, output=output, visual=visual)


@pytest.fixture
def translate() -> Iterator[AsyncMock]:
    """The text translation (``transcript.english.english_blocks``), stubbed."""
    with patch.object(qdrant_index, "english_blocks", new=AsyncMock(return_value=_ENGLISH)) as fake:
        yield fake


@pytest.fixture
def audio() -> Iterator[SimpleNamespace]:
    """Whisper's audio download and translate call — the real translate_audio_to_english runs."""
    with (
        patch.object(whisper_transcriber, "_download_audio_sync") as download,
        patch.object(whisper_transcriber, "_translate_sync") as whisper,
        patch.object(whisper_transcriber, "emit_transcription_usage"),
    ):
        yield SimpleNamespace(download=download, whisper=whisper)


async def _index(ctx: SimpleNamespace) -> None:
    """Index, then wait for the background stores (the run itself never does)."""
    before = asyncio.all_tasks()
    await qdrant_index.index_in_qdrant(ctx)  # type: ignore[arg-type]
    await asyncio.gather(*(asyncio.all_tasks() - before))


def _stored(stores: SimpleNamespace) -> dict[str, Any]:
    args, kwargs = stores.transcript.call_args
    return {"video": args[0], "text": args[1], **kwargs}


class TestEnglishSource:
    async def test_should_store_the_transcript_as_is(self, stores, translate):
        ctx = _ctx(language=None, text=_ENGLISH_DUB)

        await _index(ctx)

        assert _stored(stores) == {
            "video": "ytid",
            "text": _ENGLISH_DUB,
            "language": "en",
            "transcript_original": None,
            "segments": _PROMPT_SEGMENTS,
        }

    async def test_should_time_the_points_on_the_segments_the_text_was_built_from(
        self, stores, translate
    ):
        # clean_text drops the SponsorBlock read; the raw segments still hold it.
        await _index(_ctx(language=None, text=_ENGLISH_DUB))

        assert _stored(stores)["segments"] == _PROMPT_SEGMENTS

    async def test_should_time_the_points_on_the_transcript_without_prompt_segments(
        self, stores, translate
    ):
        ctx = _ctx(language=None, text=_ENGLISH_DUB)
        ctx.prompt_segments = []

        await _index(ctx)

        assert _stored(stores)["segments"] == _RAW_SEGMENTS

    async def test_should_keep_no_transcript_task_for_the_runner(self, stores, translate):
        ctx = _ctx(language=None, text=_ENGLISH_DUB)

        await _index(ctx)

        assert getattr(ctx, "rag_transcript_task", None) is None

    async def test_should_hand_the_store_over_before_index_returns(self, stores, translate):
        before = asyncio.all_tasks()

        await qdrant_index.index_in_qdrant(_ctx(language=None, text=_ENGLISH_DUB))  # type: ignore[arg-type]
        handed_over = stores.transcript.call_count
        await asyncio.gather(*(asyncio.all_tasks() - before))

        assert handed_over == 1

    async def test_should_not_translate(self, stores, translate, audio):
        await _index(_ctx(language=None, text=_ENGLISH_DUB))

        assert (translate.call_count, audio.whisper.call_count) == (0, 0)

    async def test_should_still_index_the_output_and_visual_points(self, stores, translate):
        await _index(_ctx(language=None, text=_ENGLISH_DUB))

        assert (stores.output.call_count, stores.visual.call_count) == (1, 1)


class TestTranscriptAlreadyEnglish:
    """EqaMrzd9nZU: a Hebrew video whose caption track is YouTube's English auto-dub."""

    async def test_should_embed_the_english_captions_as_is(self, stores, translate):
        ctx = _ctx(language="he", text=_ENGLISH_DUB)

        await _index(ctx)

        assert _stored(stores) == {
            "video": "ytid",
            "text": _ENGLISH_DUB,
            "language": "he",
            "transcript_original": None,
            "segments": _PROMPT_SEGMENTS,
        }

    async def test_should_neither_translate_nor_touch_the_audio(self, stores, translate, audio):
        await _index(_ctx(language="he", text=_ENGLISH_DUB))

        calls = (translate.call_count, audio.download.call_count, audio.whisper.call_count)
        assert calls == (0, 0, 0)


class TestTranslatedAsText:
    """Captions, an S3 hit or Gemini: the transcript is translated, the audio never touched."""

    async def test_should_embed_english_translated_from_the_captions(self, stores, translate):
        await _index(_ctx())

        stored = _stored(stores)
        assert (stored["text"], stored["segments"]) == (_ENGLISH.text, _ENGLISH.blocks)

    async def test_should_keep_the_original_text_for_display(self, stores, translate):
        await _index(_ctx())

        assert _stored(stores)["transcript_original"] == _ARABIC

    async def test_should_keep_the_transcript_task_for_the_runner(self, stores, translate):
        ctx = _ctx()

        await _index(ctx)

        assert ctx.rag_transcript_task.get_name() == "store_transcript_ytid"

    async def test_should_tag_the_points_with_the_source_language(self, stores, translate):
        await _index(_ctx())

        assert _stored(stores)["language"] == "ar"

    async def test_should_never_download_audio_or_call_whisper(self, stores, translate, audio):
        await _index(_ctx())

        assert (audio.download.call_count, audio.whisper.call_count) == (0, 0)

    async def test_should_translate_the_segments_the_text_was_built_from(self, stores, translate):
        await _index(_ctx())

        _llm, segments, language = translate.call_args.args
        assert (segments, language) == (_PROMPT_SEGMENTS, "ar")

    async def test_should_tag_the_translation_calls_for_the_cost_ledger(self, stores, translate):
        seen: list[str | None] = []

        async def _record(*_args: Any) -> EnglishTranscript:
            seen.append(llm_feature_var.get())
            return _ENGLISH

        translate.side_effect = _record

        await _index(_ctx())

        assert seen == [qdrant_index.RAG_TRANSLATION_FEATURE]

    async def test_should_translate_an_s3_transcript_from_captions(self, stores, translate, audio):
        await _index(_ctx(source="s3", origin="ytdlp"))

        assert (translate.call_count, audio.download.call_count, audio.whisper.call_count) == (
            1,
            0,
            0,
        )

    async def test_should_translate_an_s3_transcript_from_whisper_without_the_audio(
        self, stores, translate, audio
    ):
        await _index(_ctx(source="s3", origin="whisper"))

        assert (translate.call_count, audio.download.call_count, audio.whisper.call_count) == (
            1,
            0,
            0,
        )

    async def test_should_translate_a_gemini_transcript_without_the_audio(
        self, stores, translate, audio
    ):
        await _index(_ctx(source="gemini"))

        assert (translate.call_count, audio.download.call_count, audio.whisper.call_count) == (
            1,
            0,
            0,
        )


class TestEnglishTextFailure:
    async def test_should_embed_the_original_when_the_translation_fails(self, stores, translate):
        translate.return_value = None

        await _index(_ctx())

        stored = _stored(stores)
        assert (stored["text"], stored["transcript_original"], stored["segments"]) == (
            _ARABIC,
            None,
            _PROMPT_SEGMENTS,
        )

    async def test_should_embed_the_original_when_the_translation_raises(self, stores, translate):
        translate.side_effect = RuntimeError("provider down")

        await _index(_ctx())

        assert _stored(stores)["text"] == _ARABIC

    async def test_should_not_fall_back_to_the_audio_when_the_translation_fails(
        self, stores, translate, audio
    ):
        translate.return_value = None

        await _index(_ctx())

        assert (audio.download.call_count, audio.whisper.call_count) == (0, 0)

    async def test_should_return_before_the_english_text_is_ready(self, stores, translate):
        release = asyncio.Event()

        async def _slow(*_args: Any) -> EnglishTranscript:
            await release.wait()
            return _ENGLISH

        translate.side_effect = _slow
        before = asyncio.all_tasks()

        await asyncio.wait_for(qdrant_index.index_in_qdrant(_ctx()), timeout=1.0)  # type: ignore[arg-type]
        await asyncio.sleep(0)
        stored_before_release = stores.transcript.call_count
        release.set()
        await asyncio.gather(*(asyncio.all_tasks() - before))

        assert (stored_before_release, _stored(stores)["text"]) == (0, _ENGLISH.text)


class TestTranscribedFromTheAudio:
    """Whisper transcribed this run's audio (no captions): today's path, unchanged."""

    @staticmethod
    async def _run(audio: SimpleNamespace, whisper_text: str) -> None:
        """The run's audio sits in Whisper's cache; Whisper translate answers ``whisper_text``."""
        audio.whisper.return_value = {"text": whisper_text, "segments": [], "duration": 9.0}
        with (
            patch.object(whisper_transcriber, "_cached_audio_path", return_value=_AUDIO),
            patch.object(Path, "exists", return_value=True),
        ):
            await _index(_ctx(source="whisper"))

    async def test_should_whisper_translate_the_cached_audio_without_a_new_download(
        self, stores, translate, audio
    ):
        await self._run(audio, "Whisper English " * 20)

        assert (audio.whisper.call_args.args[0], audio.download.call_count) == (_AUDIO, 0)

    async def test_should_embed_the_whisper_english_and_keep_the_original(
        self, stores, translate, audio
    ):
        await self._run(audio, "Whisper English " * 20)

        stored = _stored(stores)
        assert (stored["text"], stored["transcript_original"], translate.call_count) == (
            "Whisper English " * 20,
            _ARABIC,
            0,
        )

    async def test_should_time_the_whisper_english_on_the_whole_recording(
        self, stores, translate, audio
    ):
        # Whisper translates the whole audio, the sponsor read included.
        await self._run(audio, "Whisper English " * 20)

        assert _stored(stores)["segments"] == _RAW_SEGMENTS

    async def test_should_translate_the_transcript_when_whisper_returns_too_little(
        self, stores, translate, audio
    ):
        await self._run(audio, "Hi")

        assert (_stored(stores)["text"], _stored(stores)["transcript_original"]) == (
            _ENGLISH.text,
            _ARABIC,
        )

    async def test_should_translate_the_transcript_when_whisper_translate_fails(
        self, stores, translate, audio
    ):
        # > 25 MB, or the re-download failed: translate_audio_to_english answers None.
        audio.whisper.side_effect = RuntimeError("413 file too large")
        with (
            patch.object(whisper_transcriber, "_cached_audio_path", return_value=_AUDIO),
            patch.object(Path, "exists", return_value=True),
        ):
            await _index(_ctx(source="whisper"))

        assert _stored(stores)["text"] == _ENGLISH.text

    async def test_should_embed_the_original_when_whisper_and_the_translation_fail(
        self, stores, translate, audio
    ):
        translate.return_value = None

        await self._run(audio, "Hi")

        assert (_stored(stores)["text"], _stored(stores)["transcript_original"]) == (_ARABIC, None)


class TestWaitForRagTranscript:
    """The runner's bounded wait, after ``done``, for the non-English transcript task."""

    async def test_should_return_once_the_transcript_points_are_stored(self, stores, translate):
        before = asyncio.all_tasks()
        ctx = _ctx()

        await qdrant_index.index_in_qdrant(ctx)  # type: ignore[arg-type]
        await qdrant_index.wait_for_rag_transcript(ctx)  # type: ignore[arg-type]
        stored = stores.transcript.call_count
        await asyncio.gather(*(asyncio.all_tasks() - before))

        assert stored == 1

    async def test_should_stop_at_the_bound_without_cancelling_the_task(
        self, stores, translate, monkeypatch
    ):
        release = asyncio.Event()

        async def _slow(*_args: Any) -> EnglishTranscript:
            await release.wait()
            return _ENGLISH

        translate.side_effect = _slow
        monkeypatch.setattr(qdrant_index, "RAG_TRANSCRIPT_WAIT_S", 0.01)
        before = asyncio.all_tasks()
        ctx = _ctx()

        await qdrant_index.index_in_qdrant(ctx)  # type: ignore[arg-type]
        await qdrant_index.wait_for_rag_transcript(ctx)  # type: ignore[arg-type]
        running = not ctx.rag_transcript_task.done()
        release.set()
        await asyncio.gather(*(asyncio.all_tasks() - before))

        assert (running, stores.transcript.call_count) == (True, 1)

    async def test_should_return_at_once_without_a_transcript_task(self):
        ctx = SimpleNamespace(youtube_id="ytid")

        await asyncio.wait_for(qdrant_index.wait_for_rag_transcript(ctx), timeout=1.0)  # type: ignore[arg-type]
