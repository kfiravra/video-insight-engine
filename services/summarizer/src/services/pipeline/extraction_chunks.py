"""Extraction chunks — the transcript slices the split extraction fires its groups over.

Every group call of a chunk shares that chunk's cached head block (``<video>``
+ the chunk's transcript + ``<video_memory>``), so a chunk is also the unit of
the warm-up: its own text1 call writes the head, the rest read it. Until
pipeline-1min 3.7 cuts long videos into outline-merged chunks, a run is ONE
chunk: the whole marked transcript, no batch context, every annotation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .extraction_prompt import slice_visual_annotations

if TYPE_CHECKING:
    from .context import PipelineContext


@dataclass(frozen=True)
class ExtractionChunk:
    """One transcript slice: its text, its ``<batch_partial_context>`` line, its annotation window.

    ``start_s`` / ``end_s`` bound the ``<visual_annotations>`` entries the
    chunk's visual group reads (``None`` = open at that side).
    """

    index: int
    transcript: str
    batch_context: str = ""
    start_s: float | None = None
    end_s: float | None = None


def plan_chunks(ctx: PipelineContext) -> list[ExtractionChunk]:
    """The run's chunks — one, the marked transcript plan and memory read, until 3.7."""
    return [ExtractionChunk(index=0, transcript=ctx.prompt_transcript)]


def chunk_annotations(block: str, chunk: ExtractionChunk) -> str:
    """The chunk's slice of a ``<visual_annotations>`` block; the whole block for an open window."""
    if chunk.start_s is None and chunk.end_s is None:
        return block
    return slice_visual_annotations(block, chunk.start_s, chunk.end_s)
