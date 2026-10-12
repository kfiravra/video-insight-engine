"""Qdrant points for a saved run: transcript, assembled output, visual annotations.

Called from assembly's shared-artifact step after the save. Nothing here is
awaited by the run: the three stores are background tasks, so the translation
phase and ``done`` never wait on the index.

The embedding model is English-only, so a non-English video's transcript
points embed English text and keep the source-language text for display. Its
transcript task first picks that English:

1. this run transcribed the audio with Whisper (no captions, or their fetch
   failed) → Whisper translate of that cached audio, as before — it also
   deletes the cached file;
2. the transcript already reads as English — YouTube offered an English track
   (an auto-dub, or the caption picker fell back to English) → embedded as is;
3. any other transcript (captions, an S3 hit, Gemini) → translated block by
   block on the fast model (``transcript.english``): no audio download, no
   Whisper call.

When 1 or 3 fails, the original text is embedded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_common.context import llm_feature_var

from src.services.pipeline.pipeline_helpers import log_background_error
from src.services.transcript.english import english_blocks, is_english_text
from src.services.transcription.whisper_transcriber import translate_audio_to_english
from src.services.vector.store import (
    store_default_output_chunks,
    store_transcript_chunks,
    store_visual_chunks,
)
from src.utils.language_utils import get_language_name

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

# Cost-ledger feature of the transcript translation calls.
RAG_TRANSLATION_FEATURE = "summarize:rag_translation"


@dataclass(frozen=True)
class _SourceTranscript:
    """A non-English run's transcript, read off ``ctx`` when its task is created."""

    youtube_id: str
    language: str
    text: str
    segments: list[dict[str, Any]] | None
    # The segments ``text`` was built from (SponsorBlock reads cut).
    prompt_segments: list[dict[str, Any]]
    # Whisper transcribed this run's audio, so the audio sits cached for Whisper translate.
    whisper_audio: bool
    audio_path: Path | None
    llm_service: LLMService


@dataclass(frozen=True)
class _Embedded:
    """The text the points embed, its source-language original, its timeline."""

    text: str
    original: str | None
    segments: list[dict[str, Any]] | None


def _source_transcript(ctx: PipelineContext, language: str) -> _SourceTranscript:
    transcript = ctx.transcript_data
    return _SourceTranscript(
        youtube_id=ctx.youtube_id,
        language=language,
        text=ctx.clean_text,
        segments=transcript.segments if transcript else None,
        prompt_segments=list(getattr(ctx, "prompt_segments", None) or []),
        whisper_audio=getattr(transcript, "source", None) == "whisper",
        audio_path=getattr(ctx, "audio_path", None),
        llm_service=ctx.llm_service,
    )


async def _translated_transcript(source: _SourceTranscript) -> _Embedded | None:
    """The transcript translated as text on the fast model: no audio, no Whisper."""
    started = time.monotonic()
    english = await english_blocks(
        source.llm_service, source.prompt_segments or source.segments or [], source.language
    )
    if english is None:
        logger.warning(
            "RAG transcript %s (%s): translation failed — original text embedded",
            source.youtube_id,
            source.language,
        )
        return None
    logger.info(
        "RAG transcript %s (%s): %d transcript blocks translated to English in %.1fs "
        "(no audio download, no Whisper)",
        source.youtube_id,
        source.language,
        len(english.blocks),
        time.monotonic() - started,
    )
    return _Embedded(english.text, source.text, english.blocks)


async def _whisper_english(source: _SourceTranscript) -> _Embedded | None:
    """Whisper translate of the audio this run transcribed (cached; downloaded if it is gone)."""
    translated = await translate_audio_to_english(
        source.youtube_id, cached_audio_path=source.audio_path
    )
    if not translated or len(translated) <= len(source.text) * 0.1:
        logger.warning("Whisper translate too short or failed, using original text for Qdrant")
        return None
    logger.info(
        "Using Whisper-translated English text for Qdrant (%d chars, original %s: %d chars)",
        len(translated),
        get_language_name(source.language),
        len(source.text),
    )
    return _Embedded(translated, source.text, source.segments)


async def _english(source: _SourceTranscript) -> _Embedded | None:
    """English for a non-English transcript (order in the module doc); None when none was made."""
    if source.whisper_audio:
        return await _whisper_english(source)
    if await asyncio.to_thread(is_english_text, source.text):
        logger.info(
            "RAG transcript %s (%s): the transcript is already English — embedded as is "
            "(no audio download, no Whisper)",
            source.youtube_id,
            source.language,
        )
        return _Embedded(source.text, None, source.segments)
    return await _translated_transcript(source)


async def _embedded_transcript(source: _SourceTranscript) -> _Embedded:
    """English to embed for a non-English transcript; the original text when there is none."""
    try:
        english = await _english(source)
    except Exception as e:
        logger.warning(
            "RAG transcript %s: English text failed (%s) — original text embedded",
            source.youtube_id,
            e,
        )
        english = None
    return english or _Embedded(source.text, None, source.segments)


async def _index_non_english_transcript(source: _SourceTranscript) -> None:
    """Pick the text to embed (never raises), then store the transcript points."""
    llm_feature_var.set(RAG_TRANSLATION_FEATURE)
    embedded = await _embedded_transcript(source)
    await store_transcript_chunks(
        source.youtube_id,
        embedded.text,
        language=source.language,
        transcript_original=embedded.original,
        segments=embedded.segments,
    )


def _transcript_task(ctx: PipelineContext) -> Coroutine[Any, Any, None]:
    """An English source stores at once; a non-English one picks its English text first."""
    if ctx.source_language_code:
        return _index_non_english_transcript(_source_transcript(ctx, ctx.source_language_code))
    return store_transcript_chunks(
        ctx.youtube_id,
        ctx.clean_text,
        language="en",
        transcript_original=None,
        segments=ctx.transcript_data.segments if ctx.transcript_data else None,
    )


async def index_in_qdrant(ctx: PipelineContext) -> None:
    """Transcript, assembled output and visual points — background tasks, nothing awaited."""
    tasks = [
        asyncio.create_task(_transcript_task(ctx), name=f"store_transcript_{ctx.youtube_id}"),
        # Generation is English-canonical, so the assembled tabs are always
        # English and indexed for every video — translation only adds the
        # source-language artifact.
        asyncio.create_task(
            store_default_output_chunks(ctx.youtube_id, ctx.assembled_tabs or [], language="en"),
            name=f"store_output_{ctx.youtube_id}",
        ),
        # What the frames showed (1c.2): the transcript points are speech only,
        # so the rendered annotations get their own "visual" points. Runs even
        # with no annotations — the store clears a previous run's points.
        asyncio.create_task(
            store_visual_chunks(ctx.youtube_id, ctx.visual_annotations),
            name=f"store_visual_{ctx.youtube_id}",
        ),
    ]
    for task in tasks:
        task.add_done_callback(log_background_error)
