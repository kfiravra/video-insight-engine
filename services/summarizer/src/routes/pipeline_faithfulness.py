"""Fire-and-forget faithfulness judge spawning for the pipeline runner.

Extracted from ``pipeline_runner.py`` — owns the module-level task registry
(strong refs so the GC can't cancel a judge mid-call) and the bounded drain
that runs before the Langfuse trace flushes. Two launches per run: the
extraction sample once extraction has data, and the hero + quiz sample
(output-quality 2.4) once the doc is assembled — each half only when the hero
rewrite / quiz v2 wrote it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from llm_common.context import llm_feature_var

from src.config import settings
from src.services.pipeline import faithfulness
from src.services.pipeline.faithfulness_hero_quiz import (
    EvidenceSource,
    HeroQuizSamples,
    hero_quiz_samples,
    run_hero_quiz_check,
)
from src.services.pipeline.phases.synthesis import wrote_hero

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

# The ledger feature every judge call is attributed to.
_JUDGE_FEATURE = "summarize:faithfulness"

# Strong refs for fire-and-forget faithfulness tasks — the asyncio loop only
# keeps weak refs to tasks created via ``create_task``, so without a
# module-level set the GC can cancel the judge mid-call.
_FAITHFULNESS_TASKS: set[asyncio.Task[None]] = set()


# Bounded wait when draining in-flight faithfulness tasks at trace exit. The
# judge itself caps each LLM call at ``_JUDGE_TIMEOUT_SECONDS = 20`` (parallel
# fan-out), so 25s leaves a small margin without delaying SSE clients on a
# stuck judge.
_FAITHFULNESS_DRAIN_TIMEOUT_S = 25.0


def _spawn_judge(check: Callable[[], Awaitable[object]], name: str) -> asyncio.Task[None]:
    """Run ``check`` in the background under the judge's ledger feature, held in
    ``_FAITHFULNESS_TASKS`` until it ends. A failure is logged, never raised."""

    async def _run() -> None:
        # asyncio.create_task snapshots the parent task's ContextVars at spawn
        # time, so this task would inherit the label of the phase that spawned
        # it ("summarize:extraction" after extraction) and every judge LLM call
        # would be attributed to that phase in cost tracking. Setting it inside
        # the spawned task is scoped to this task only — it doesn't leak back to
        # the parent.
        llm_feature_var.set(_JUDGE_FEATURE)
        try:
            await check()
        except Exception as exc:  # noqa: BLE001 — defensive; check is non-critical
            logger.debug("Faithfulness task failed: %s", exc)

    task = asyncio.create_task(_run(), name=name)
    _FAITHFULNESS_TASKS.add(task)
    task.add_done_callback(_FAITHFULNESS_TASKS.discard)
    return task


def _judge_source(ctx: PipelineContext) -> str:
    """What the judge checks claims against: the transcript + the visual annotations.

    Extraction reads both (1c.2), so a claim grounded only in what the video
    shows on screen is still a grounded claim.
    """
    parts = [ctx.clean_text or "", ctx.visual_annotations]
    return "\n\n".join(part for part in parts if part)


def _launch_faithfulness_check(ctx: PipelineContext) -> asyncio.Task[None] | None:
    """Spawn the extraction-sample judge in the background — never blocks.

    Returns the spawned task so the caller can await it before flushing the
    parent Langfuse trace (otherwise the score may be added to the SDK
    buffer after explicit flush and lost on shutdown).
    """
    if not ctx.extraction_data or not ctx.clean_text:
        return None
    return _spawn_judge(
        lambda: faithfulness.run_faithfulness_check(
            llm_service=ctx.llm_service,
            transcript=_judge_source(ctx),
            extraction_data=ctx.extraction_data or {},
            youtube_id=ctx.youtube_id,
        ),
        name=f"faithfulness:{ctx.youtube_id}",
    )


def _timed_segments(ctx: PipelineContext) -> list[Any]:
    """The segments every prompt read (sponsor reads cut), else the fetched ones."""
    fetched = getattr(ctx.transcript_data, "segments", None)
    for segments in (ctx.prompt_segments, fetched):
        if isinstance(segments, list) and segments:
            return segments
    return []


def _hero_quiz_samples(ctx: PipelineContext) -> HeroQuizSamples | None:
    """The assembled doc's hero + quiz samples, windowed on the run's transcript.

    Each half is judged only when its late call wrote it: the hero rewrite wrote the
    hero (``wrote_hero``; else the page shows memory's takeaways) and quiz v2 made the
    quiz (``QUIZ_V2_ENABLED``). A run with both flags off makes no extra judge call.
    """
    evidence = EvidenceSource.build(
        _timed_segments(ctx), ctx.visual_annotations, ctx.source_language_code
    )
    return hero_quiz_samples(
        youtube_id=ctx.youtube_id,
        meta=ctx.assembled_meta or {},
        tabs=ctx.assembled_tabs or [],
        evidence=evidence,
        judge_hero=wrote_hero(ctx.synthesis_dict),
        judge_quiz=settings.QUIZ_V2_ENABLED,
    )


def _launch_hero_quiz_check(ctx: PipelineContext) -> asyncio.Task[None] | None:
    """Spawn the judge's hero + quiz sample (output-quality 2.4) — never blocks.

    Called once the doc is assembled (the late group wrote the hero and the
    quiz); the caller drains the task with the others. None when a purge
    deleted the row mid-run, the judge is off, the hero rewrite and quiz v2 both
    left their part of the page unwritten, or the doc has neither a stamped
    takeaway nor a stamped quiz question.
    """
    if ctx.row_deleted:
        return None
    try:
        samples = _hero_quiz_samples(ctx)
    except Exception as exc:  # noqa: BLE001 — the run never fails on its judge's input
        logger.debug("Faithfulness hero/quiz sample failed: %s", exc)
        return None
    if samples is None:
        return None
    return _spawn_judge(
        lambda: run_hero_quiz_check(llm_service=ctx.llm_service, samples=samples),
        name=f"faithfulness-hero-quiz:{ctx.youtube_id}",
    )


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
