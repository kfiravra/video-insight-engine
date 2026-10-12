"""Qdrant points for a saved run: transcript, assembled output, visual annotations.

Called from assembly's shared-artifact step after the save. The three stores
run as background tasks; only the text to embed is awaited first.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.services.pipeline.pipeline_helpers import log_background_error
from src.services.transcription.whisper_transcriber import translate_audio_to_english
from src.services.vector.store import (
    store_default_output_chunks,
    store_transcript_chunks,
    store_visual_chunks,
)
from src.utils.language_utils import get_language_name

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


async def _english_transcript(ctx: PipelineContext) -> tuple[str, str | None]:
    """(text to embed, original text) — Whisper-translated English for a non-English source.

    Generated content is English, the transcript is still the source language:
    embeddings live in the model's strongest language, the original is kept for
    display.
    """
    if not ctx.source_language_code:
        return ctx.clean_text, None
    try:
        translated = await translate_audio_to_english(
            ctx.youtube_id, cached_audio_path=ctx.audio_path
        )
    except Exception as e:
        logger.warning("Whisper translate for Qdrant failed: %s — using original text", e)
        return ctx.clean_text, None
    if not translated or len(translated) <= len(ctx.clean_text) * 0.1:
        logger.warning("Whisper translate too short or failed, using original text for Qdrant")
        return ctx.clean_text, None
    logger.info(
        "Using Whisper-translated English text for Qdrant (%d chars, original %s: %d chars)",
        len(translated),
        get_language_name(ctx.source_language_code),
        len(ctx.clean_text),
    )
    return translated, ctx.clean_text


async def index_in_qdrant(ctx: PipelineContext) -> None:
    """Transcript, assembled output and visual points (background, non-blocking)."""
    english_text, original_text = await _english_transcript(ctx)
    tasks = [
        asyncio.create_task(
            store_transcript_chunks(
                ctx.youtube_id,
                english_text,
                language=ctx.source_language_code or "en",
                transcript_original=original_text,
                segments=ctx.transcript_data.segments if ctx.transcript_data else None,
            ),
            name=f"store_transcript_{ctx.youtube_id}",
        ),
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
