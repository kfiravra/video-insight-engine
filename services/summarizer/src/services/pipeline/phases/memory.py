"""Phase 3b: Memory — the fast-model read of the full transcript, in parallel with the plan.

Reads the same marked transcript as the plan (``ctx.prompt_transcript``) and
leaves outline, evidence, tldr and takeaways on ``ctx.memory`` (``None`` when
the call failed — every reader has a fallback). Never raises: ``run_memory``
returns ``None`` on any failure.

When memory has a tldr or takeaways it emits the first ``synthesis_complete``
right away — the hero, long before synthesis (pipeline-1min 1b.5). The
synthesis phase re-emits the full superset later; web and API merge the two
(an empty field never overwrites a filled one).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from src.models.memory_types import MemoryInput, MemoryResult
from src.services.pipeline.memory import run_memory
from src.services.pipeline.pipeline_helpers import sse_event

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def memory_input(ctx: PipelineContext) -> MemoryInput:
    """The memory call's input: metadata + the run's marked transcript."""
    video_data = ctx.video_data
    assert video_data is not None
    return MemoryInput(
        title=video_data.title or "",
        channel=video_data.channel,
        duration=video_data.duration or 0,
        description=video_data.description or "",
        transcript=ctx.prompt_transcript,
    )


def early_synthesis(memory: MemoryResult | None) -> dict[str, object] | None:
    """The memory-done ``synthesis_complete`` payload, or ``None`` when memory has neither field."""
    if memory is None or not (memory.tldr or memory.takeaways):
        return None
    return {"tldr": memory.tldr, "keyTakeaways": list(memory.takeaways)}


async def run_phase_memory(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run the memory call, keep its answer on ``ctx.memory``, emit the early hero.

    No timed segments (a metadata-only transcript) → no call: the model would
    invent an ``[m:ss]`` outline and takeaways from the title and description.
    ``ctx.memory`` stays ``None`` and every reader takes its fallback.
    """
    if not ctx.prompt_segments:
        ctx.memory = None
        logger.info("pipeline.memory", extra={"video_id": ctx.video_summary_id, "skipped": True})
        return
    ctx.memory = await run_memory(ctx.llm_service, memory_input(ctx))
    logger.info("pipeline.memory", extra={"video_id": ctx.video_summary_id, "ok": bool(ctx.memory)})
    payload = early_synthesis(ctx.memory)
    if payload is not None:
        yield sse_event("synthesis_complete", payload)
