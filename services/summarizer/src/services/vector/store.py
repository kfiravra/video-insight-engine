"""Store transcript, output and visual chunks in Qdrant as background tasks.

Called after pipeline completion. Failures are logged but never break the
pipeline. ``store_transcript_chunks``, ``store_default_output_chunks`` and
``store_visual_chunks`` pre-delete by ``(video_id, source)`` before upsert to
keep reprocess idempotent — reprocesses with fewer chunks no longer leave
orphan points.
"""

from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.services.pipeline.visual_annotations import annotation_entries
from src.services.vector.chunking import (
    assign_chunk_timestamps,
    chunk_transcript,
    chunk_visual_entries,
)
from src.services.vector.embedding import embed_texts
from src.services.vector.output_chunker import chunk_assembled_tabs
from src.services.vector.qdrant_service import (
    SOURCE_DEFAULT_OUTPUT,
    SOURCE_TRANSCRIPT,
    SOURCE_VISUAL,
    VectorService,
)

logger = logging.getLogger(__name__)

_vector_service: VectorService | None = None


def _align_original_to_chunks(
    english_chunks: list[dict],
    original_text: str,
) -> list[dict] | None:
    """Align original-language text to English chunks by proportional character offsets.

    Since sentence boundaries differ across languages, independent chunking produces
    different counts. Instead, split the original text proportionally based on where
    each English chunk falls relative to the total English text.
    """
    total_en_chars = sum(len(c["text"]) for c in english_chunks)
    if total_en_chars == 0 or not original_text:
        return None

    original_chunks = []
    orig_len = len(original_text)
    cursor = 0

    for i, chunk in enumerate(english_chunks):
        ratio = len(chunk["text"]) / total_en_chars
        if i == len(english_chunks) - 1:
            # Last chunk gets the remainder
            segment = original_text[cursor:]
        else:
            end = min(cursor + int(ratio * orig_len), orig_len)
            segment = original_text[cursor:end]
            cursor = end

        original_chunks.append({"text": segment.strip() or segment})

    return original_chunks if len(original_chunks) == len(english_chunks) else None


def _get_vector_service() -> VectorService:
    """Lazy singleton for VectorService."""
    global _vector_service
    if _vector_service is None:
        _vector_service = VectorService()
    return _vector_service


async def store_transcript_chunks(
    video_id: str,
    transcript: str,
    language: str = "en",
    transcript_original: str | None = None,
    segments: list[dict] | None = None,
) -> None:
    """Chunk, embed, and store transcript in Qdrant.

    For non-English videos, ``transcript`` should be the English translation
    (for embedding) and ``transcript_original`` is the original-language text.

    ``segments`` are the raw transcript segments (with start times, in either
    ``start``/``duration`` or ``startMs``/``endMs`` shape). When provided,
    each chunk's payload carries ``timestamp``/``end_timestamp`` (seconds) so
    the assistant can render [MM:SS] citations. Points written without
    segments (or before payload schema v2) simply have a null timestamp and
    render citation-less — they heal on the next re-ingest of the video.

    Designed to run as a background task — never raises.
    """
    if not settings.QDRANT_ENABLED:
        return

    try:
        chunks = chunk_transcript(transcript)
        if not chunks:
            logger.debug("No chunks produced for video %s", video_id)
            return

        if segments:
            assign_chunk_timestamps(chunks, segments)

        # Align original text to English chunks by proportional character mapping.
        # Independent chunking fails because sentence boundaries differ across languages.
        original_chunks = None
        if transcript_original and language != "en":
            original_chunks = _align_original_to_chunks(chunks, transcript_original)

        texts = [c["text"] for c in chunks]
        embeddings = await asyncio.to_thread(embed_texts, texts)

        service = _get_vector_service()
        # Pre-delete prior transcript points to avoid orphans when the new
        # chunk count is smaller than the previous run.
        await asyncio.to_thread(
            service.delete_by_video_and_source,
            video_id,
            SOURCE_TRANSCRIPT,
        )
        success = await asyncio.to_thread(
            service.store_chunks,
            video_id,
            chunks,
            embeddings,
            language,
            original_chunks,
            SOURCE_TRANSCRIPT,
        )

        if success:
            logger.info(
                "Stored %d transcript chunks for video %s (language=%s, has_original=%s)",
                len(chunks),
                video_id,
                language,
                original_chunks is not None,
            )
        else:
            logger.warning("Failed to store chunks for video %s", video_id)

    except Exception as e:
        logger.warning(
            "Background chunk storage failed for %s: %s",
            video_id,
            e,
        )


async def store_default_output_chunks(
    video_id: str,
    tabs: list[dict],
    language: str = "en",
) -> None:
    """Chunk assembled tab output, embed, and store in Qdrant.

    Reuses the same collection as transcript content but tags each point with
    ``source="default_output"`` plus the originating ``tab_id``,
    ``tab_component`` and ``prop_path`` so the assistant can filter to either
    transcript-only, output-only, or combined retrieval.

    For non-English videos, callers pass the English-primary tabs (the
    translation phase promotes them onto ``ctx.assembled_tabs``) so embeddings
    stay in the same language as the (English-trained) embedding model.

    Designed to run as a background task — never raises.
    """
    if not settings.QDRANT_ENABLED:
        return

    service = _get_vector_service()
    try:
        # Pre-delete first so an exception below cannot strand orphans.
        await asyncio.to_thread(
            service.delete_by_video_and_source,
            video_id,
            SOURCE_DEFAULT_OUTPUT,
        )

        output_chunks = chunk_assembled_tabs(tabs)
        if not output_chunks:
            logger.debug("No output chunks produced for video %s", video_id)
            return

        texts = [c.text for c in output_chunks]
        embeddings = await asyncio.to_thread(embed_texts, texts)

        chunk_dicts = [{"text": c.text} for c in output_chunks]
        prop_paths = [c.prop_path for c in output_chunks]
        tab_ids: list[str | None] = [c.tab_id for c in output_chunks]
        tab_components: list[str | None] = [c.tab_component for c in output_chunks]

        success = await asyncio.to_thread(
            service.store_chunks,
            video_id,
            chunk_dicts,
            embeddings,
            language,
            None,
            SOURCE_DEFAULT_OUTPUT,
            None,
            None,
            prop_paths,
            tab_ids,
            tab_components,
        )

        if success:
            distinct_tabs = len({(c.tab_id, c.tab_component) for c in output_chunks})
            logger.info(
                "Stored %d output chunks for video %s across %d tabs",
                len(output_chunks),
                video_id,
                distinct_tabs,
            )
        else:
            logger.warning("Failed to store output chunks for video %s", video_id)

    except Exception as e:
        logger.warning(
            "Background output chunk storage failed for %s: %s",
            video_id,
            e,
        )


async def store_visual_chunks(video_id: str, visual_annotations: str) -> None:
    """Index the rendered ``<visual_annotations>`` block as ``source="visual"`` points.

    Visual facts left ``clean_text`` in 1c.2, so the transcript points no
    longer carry them; these points keep them searchable, each chunk with the
    ``timestamp``/``end_timestamp`` of its first and last frame. Pre-deletes
    first even when the block is empty, so a rerun without frames leaves no
    stale visual points behind. The vision captions are English, so the points
    are stored as ``language="en"`` for every video.

    Designed to run as a background task — never raises.
    """
    if not settings.QDRANT_ENABLED:
        return

    service = _get_vector_service()
    try:
        await asyncio.to_thread(service.delete_by_video_and_source, video_id, SOURCE_VISUAL)

        chunks = chunk_visual_entries(annotation_entries(visual_annotations))
        if not chunks:
            logger.debug("No visual annotations to index for video %s", video_id)
            return

        embeddings = await asyncio.to_thread(embed_texts, [c["text"] for c in chunks])
        success = await asyncio.to_thread(
            service.store_chunks,
            video_id,
            chunks,
            embeddings,
            "en",
            None,
            SOURCE_VISUAL,
        )
        if success:
            logger.info("Stored %d visual chunks for video %s", len(chunks), video_id)
        else:
            logger.warning("Failed to store visual chunks for video %s", video_id)

    except Exception as e:
        logger.warning("Background visual chunk storage failed for %s: %s", video_id, e)
