"""Fire-and-forget faithfulness judge spawning for the pipeline runner.

Extracted from ``pipeline_runner.py`` — owns the module-level task registry
(strong refs so the GC can't cancel a judge mid-call) and the bounded drain
that runs before the Langfuse trace flushes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from llm_common.context import llm_feature_var

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


# Strong refs for fire-and-forget faithfulness tasks — the asyncio loop only
# keeps weak refs to tasks created via ``create_task``, so without a
# module-level set the GC can cancel the judge mid-call.
_FAITHFULNESS_TASKS: set[asyncio.Task[None]] = set()


# Bounded wait when draining in-flight faithfulness tasks at trace exit. The
# judge itself caps each LLM call at ``_JUDGE_TIMEOUT_SECONDS = 20`` (parallel
# fan-out), so 25s leaves a small margin without delaying SSE clients on a
# stuck judge.
_FAITHFULNESS_DRAIN_TIMEOUT_S = 25.0


def _judge_source(ctx: PipelineContext) -> str:
    """What the judge checks claims against: the transcript + the visual annotations.

    Extraction reads both (1c.2), so a claim grounded only in what the video
    shows on screen is still a grounded claim.
    """
    parts = [ctx.clean_text or "", ctx.visual_annotations]
    return "\n\n".join(part for part in parts if part)


def _launch_faithfulness_check(ctx: PipelineContext) -> asyncio.Task[None] | None:
    """Spawn the faithfulness judge in the background — never blocks.

    Returns the spawned task so the caller can await it before flushing the
    parent Langfuse trace (otherwise the score may be added to the SDK
    buffer after explicit flush and lost on shutdown).
    """
    if not ctx.extraction_data or not ctx.clean_text:
        return None
    try:
        from src.services.pipeline.faithfulness import run_faithfulness_check
    except ImportError as exc:
        logger.debug("Faithfulness module unavailable: %s", exc)
        return None

    async def _run() -> None:
        # asyncio.create_task snapshots the parent task's ContextVars at spawn
        # time, which means this task inherits "summarize:extraction" from the
        # extraction phase that just ran. Without this re-set, every judge LLM
        # call is attributed to extraction in cost tracking instead of its
        # own stage. Setting it inside the spawned task is scoped to this task
        # only — it doesn't leak back to the parent.
        llm_feature_var.set("summarize:faithfulness")
        try:
            await run_faithfulness_check(
                llm_service=ctx.llm_service,
                transcript=_judge_source(ctx),
                extraction_data=ctx.extraction_data or {},
                youtube_id=ctx.youtube_id,
            )
        except Exception as exc:  # noqa: BLE001 — defensive; check is non-critical
            logger.debug("Faithfulness task failed: %s", exc)

    task = asyncio.create_task(_run(), name=f"faithfulness:{ctx.youtube_id}")
    _FAITHFULNESS_TASKS.add(task)
    task.add_done_callback(_FAITHFULNESS_TASKS.discard)
    return task


async def _drain_faithfulness(tasks: list[asyncio.Task[None]]) -> None:
    """Wait for in-flight faithfulness tasks before the Langfuse trace flushes.

    Bounded by ``_FAITHFULNESS_DRAIN_TIMEOUT_S`` so a stuck judge can't
    delay SSE completion. The score is best-effort; any task still running
    after the timeout is left to complete on its own (and its score will
    flush on the SDK's next periodic drain).
    """
    pending = [t for t in tasks if not t.done()]
    if not pending:
        return
    try:
        await asyncio.wait(pending, timeout=_FAITHFULNESS_DRAIN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — never block trace exit
        logger.debug("Faithfulness drain failed: %s", exc)
