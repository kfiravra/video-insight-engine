"""Quiz v2's cached prefix (output-quality 4.6e) — the calls read the extraction's cache.

The quiz runs on the extraction groups' model (``LLM_EXTRACTION_MODEL``: the cache is
per model) and each call sends the split run's cached blocks first, byte-identical to
the group calls' — the hero rewrite's pattern (``hero_job.cached_blocks``):

1. block 1 (1 h) = ``extraction_group_prompt.prefix_block`` — rules, schemas, example;
2. block 2 (5 min) = ``head_block`` of the call's chunk — ``<video>``, that chunk's
   transcript, ``<video_memory>``. On a chunked run part k IS chunk k
   (``quiz_v2_select.chunk_windows``), so a part's call still reads only its part — from
   the cache now, not as a slice of the transcript; a one-chunk run's calls (its thirds,
   or its one call) all read the one head, the whole transcript, and a third's call is
   held to its stretch by its task line and its targets (``quiz_v2_targets``);
3. the quiz job, uncached (``quiz_v2_prompt``): the task, the hero, the key ideas, the
   rules.

No prefix — ``EXTRACTION_PARALLEL=false`` (no group call wrote one), a quiz the late
group calls itself, a whole-video call on a chunked run (one request reads one head), a
chunked run without chunk windows (no known end) or a template that lost its sections —
the call keeps the transcript inline in one uncached message, as before 4.6e.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .extraction_group_prompt import head_block, prefix_block

if TYPE_CHECKING:
    from ..llm_messages import TextBlock
    from .extraction_chunks import ExtractionChunk
    from .hero_job import GroupBlocks
    from .quiz_v2_select import Part

logger = logging.getLogger(__name__)


def _head_chunk(
    blocks: GroupBlocks, part: int | None, windows: Sequence[Part]
) -> ExtractionChunk | None:
    """The chunk whose head the call reads: the one chunk of a one-chunk run; on a chunked
    run the part's own chunk (parts = chunk windows); else None (module docstring)."""
    chunks = blocks.chunks
    if len(chunks) == 1:
        return chunks[0]
    if part is None or len(windows) != len(chunks) or not 0 <= part < len(chunks):
        return None
    return chunks[part]


def cached_prefix(
    blocks: GroupBlocks | None, part: int | None, windows: Sequence[Part]
) -> tuple[TextBlock, ...]:
    """Blocks 1 + 2 for the call of ``part`` (None = a whole-video call); ``()`` when the
    call keeps its transcript inline (module docstring)."""
    if blocks is None or not blocks.chunks:
        return ()
    chunk = _head_chunk(blocks, part, windows)
    if chunk is None:
        return ()
    try:
        return prefix_block(blocks.prompt), head_block(blocks.prompt, chunk)
    except ValueError:
        logger.warning("Quiz v2: the extraction template lost its sections — transcript inline")
        return ()
