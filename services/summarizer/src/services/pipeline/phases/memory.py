"""Phase 3b: Memory — the fast-model read of the full transcript, in parallel with the plan.

Reads the same marked transcript as the plan (``ctx.prompt_transcript``) and
leaves outline, evidence, tldr and takeaways on ``ctx.memory`` (``None`` when
the call failed — every reader has a fallback). Never raises: ``run_memory``
returns ``None`` on any failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from src.models.memory_types import MemoryInput
from src.services.pipeline.memory import run_memory

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


async def run_phase_memory(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run the memory call and keep its answer on ``ctx.memory``."""
    ctx.memory = await run_memory(ctx.llm_service, memory_input(ctx))
    logger.info("pipeline.memory", extra={"video_id": ctx.video_summary_id, "ok": bool(ctx.memory)})
    return
    # A phase is an async generator; the memory-done hero event (1b.5) is
    # this phase's only SSE output.
    yield
