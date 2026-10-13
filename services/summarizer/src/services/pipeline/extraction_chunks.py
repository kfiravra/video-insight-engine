"""Extraction chunks — the transcript slices the split extraction fires its groups over.

Every group call of a chunk shares that chunk's cached head block (``<video>``
+ the chunk's transcript + ``<video_memory>``), so a chunk is also the unit of
the warm-up: its own text1 call writes the head, the rest read it.

A video one extraction batch covers (``needs_chapter_batching`` false: at
most ``MAX_MINUTES_PER_BATCH`` and ``MAX_TOKENS_PER_BATCH``) is ONE chunk —
the whole marked transcript, no batch context, every annotation — with no
chapter work and no wait for the description. A longer video is cut the way
today's chunked extraction cuts it (pipeline-1min 3.7, A13): today's chapter
chain (creator chapters → description timestamps → the memory outline;
chapter_detect only without a usable outline) merged by ``batch_chapters``.
Each batch is a chunk with today's chapter-headed transcript, its
``<batch_partial_context>`` and the annotation window today's batches read —
from its first marker to the next chunk's, open at both video ends. When the
chain gives no more than one batch (it raised, or there are no segments to
cut) the transcript is force-split by sentences into enough parts for both
limits, one chunk per part — today's force-split path; one chunk only when
that split gives fewer than two parts. At frames-done each chunk's key frames
are formatted from the gallery frames in its window (``key_frames_by_chunk``).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...config import settings
from ..transcription.transcript_chunker import (
    _estimate_tokens,
    force_split_by_sentences,
    needs_chapter_batching,
)
from .extraction_prompt import slice_visual_annotations
from .extractor import (
    _batch_start_seconds,
    _build_batch_context,
    _build_batch_transcript,
    batch_chapters,
)
from .prompt_builder import format_gallery_frames_for_extraction

if TYPE_CHECKING:
    from ..transcription.transcript_chunker import ChapterChunk
    from .context import PipelineContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionChunk:
    """One transcript slice: its text, its ``<batch_partial_context>`` line, its annotation window.

    ``start_s`` / ``end_s`` bound the ``<visual_annotations>`` entries and the
    key frames the chunk's visual group reads (``None`` = open at that side).
    """

    index: int
    transcript: str
    batch_context: str = ""
    start_s: float | None = None
    end_s: float | None = None


# ─── Planning ───


def _duration(ctx: PipelineContext) -> float:
    return float(getattr(ctx.video_data, "duration", 0) or 0)


def _needs_chunks(ctx: PipelineContext) -> bool:
    """Whether one extraction batch cannot cover the video (chapters need a transcript)."""
    return bool(ctx.transcript_data) and needs_chapter_batching(
        _duration(ctx), ctx.prompt_transcript
    )


def _chapter_video_info(ctx: PipelineContext) -> dict[str, Any]:
    """The video fields today's extraction phase hands the chapter chain.

    Keep in sync with ``video_info`` in ``phases/extraction.py:run_phase_extraction``.
    """
    video = ctx.video_data
    assert video is not None
    return {
        "title": video.title,
        "channel": video.channel,
        "duration": video.duration,
        "chapters": getattr(video, "chapters", None),
        # chapter_detect reads it; without the key it ran on an empty description (A9).
        "description": video.description,
    }


async def _chapter_batches(ctx: PipelineContext) -> list[list[ChapterChunk]]:
    """Today's chapter chain (sets ``ctx.chapters``), merged into extraction batches."""
    # The phase sits above this module (and imports it): bound on the long-video path only.
    from .phases.extraction import _split_chapters

    chapters = await _split_chapters(ctx, _chapter_video_info(ctx), ctx.prompt_transcript)
    return batch_chapters(chapters) if chapters else []


def _forced_batches(transcript: str, duration: float) -> list[list[ChapterChunk]]:
    """Today's force-split: sentence-bounded parts, enough for both batch limits, one per batch."""
    parts = max(
        2,
        math.ceil(duration / (settings.MAX_MINUTES_PER_BATCH * 60)),
        math.ceil(_estimate_tokens(transcript) / settings.MAX_TOKENS_PER_BATCH),
    )
    return [[part] for part in force_split_by_sentences(transcript, duration, target_chunks=parts)]


def _batch_chunks(batches: list[list[ChapterChunk]]) -> list[ExtractionChunk]:
    """One chunk per batch: today's batch transcript, context and annotation window."""
    full_duration = max(ch.end_seconds for batch in batches for ch in batch)
    opens = [_batch_start_seconds(batch) for batch in batches]
    last = len(batches) - 1
    return [
        ExtractionChunk(
            index=k,
            transcript=_build_batch_transcript(batch),
            batch_context=_build_batch_context(k, len(batches), batch, full_duration),
            start_s=opens[k] if k else None,
            end_s=opens[k + 1] if k < last else None,
        )
        for k, batch in enumerate(batches)
    ]


async def plan_chunks(ctx: PipelineContext) -> list[ExtractionChunk]:
    """The run's chunks: one when a single batch covers the video, else one per chapter batch."""
    whole = [ExtractionChunk(index=0, transcript=ctx.prompt_transcript)]
    if not _needs_chunks(ctx):
        return whole
    batches = await _chapter_batches(ctx)
    if len(batches) <= 1:
        batches = _forced_batches(ctx.prompt_transcript, _duration(ctx))
    if len(batches) <= 1:
        logger.warning(
            "Split extraction: a long video neither chapters nor force-splits — one chunk "
            "(video_id=%s)",
            ctx.video_summary_id,
        )
        return whole
    chunks = _batch_chunks(batches)
    logger.info(
        "Split extraction: %d chapters (%s) in %d chunks (video_id=%s)",
        sum(len(batch) for batch in batches),
        batches[0][0].source,
        len(chunks),
        ctx.video_summary_id,
    )
    return chunks


# ─── Per-chunk frame inputs ───


def _in_window(seconds: float, chunk: ExtractionChunk) -> bool:
    return (chunk.start_s is None or seconds >= chunk.start_s) and (
        chunk.end_s is None or seconds < chunk.end_s
    )


def chunk_annotations(block: str, chunk: ExtractionChunk) -> str:
    """The chunk's slice of a ``<visual_annotations>`` block; the whole block for an open window."""
    if chunk.start_s is None and chunk.end_s is None:
        return block
    return slice_visual_annotations(block, chunk.start_s, chunk.end_s)


def key_frames_by_chunk(
    gallery: list[dict], descriptions: list[dict] | None, chunks: Sequence[ExtractionChunk]
) -> dict[int, str]:
    """Each chunk's ``<key_frames>`` lines, formatted from the gallery frames in its window
    (the same cap per chunk as the whole video gets); ``{}`` for one chunk — it reads the
    whole video's."""
    if len(chunks) <= 1:
        return {}
    return {
        chunk.index: format_gallery_frames_for_extraction(
            [f for f in gallery if _in_window(float(f.get("timestamp", 0) or 0), chunk)],
            descriptions,
        )
        for chunk in chunks
    }
