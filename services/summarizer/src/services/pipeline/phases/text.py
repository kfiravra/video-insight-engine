"""Phase 2a: the text branch — transcript → tier probe → plan ∥ memory → video_memory.

One member of the phase-2 group, so the readers run while frames and vision
still do (pipeline-1min 1b, brief §2):

1. the transcript phase, once the t=0 caption fetch landed; then
   ``ctx.transcript_ready`` releases the tier probe, which reads the native
   (untranslated) ``clean_text`` — nothing translates before the plan;
2. the probe's answer (the plan's playbook + hint; frames waits for it on its own);
   a caption transcript it judged unusable is replaced ONCE by the audio path
   and probed again; an answer still unusable is dropped, so the plan gets no
   probe (``transcript_fallback.py``, 2.5);
3. the marked transcript, rendered ONCE from ``ctx.prompt_segments`` — plan
   and memory read the same string, never frame annotations (those render
   into their own ``<visual_annotations>`` block after the whole group);
4. plan ∥ memory, from probe-done;
5. reconcile at plan-done (3.2): the memory's second opinion over the planned
   tabs, then ``triage_complete`` + ``meta`` on the reconciled plan;
6. the ``<video_memory>`` block, rendered once for every writer's
   ``{video_context}`` slot from the reconciled tabs.

Extraction still waits for the whole group (plan AND frames).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator

from src.services.pipeline.phases import (
    run_phase_memory,
    run_phase_plan,
    run_phase_transcript,
)
from src.services.pipeline.phases.metadata import after_captions
from src.services.pipeline.phases.probe import probe_for_plan
from src.services.pipeline.phases.transcript_fallback import replace_unusable_captions
from src.services.pipeline.phases.triage import plan_done_events
from src.services.pipeline.pipeline_helpers import run_parallel_phases
from src.services.pipeline.pipeline_timing import timed_step
from src.services.pipeline.video_memory import render_video_memory
from src.services.transcript.render import render_transcript

if TYPE_CHECKING:
    from src.models.pipeline_types import PlanResult
    from src.services.pipeline.context import PipelineContext


def render_prompt_transcript(ctx: PipelineContext) -> str:
    """``[m:ss]``-marked ``prompt_segments``; ``clean_text`` when there are none
    (metadata-only transcript)."""
    marked = render_transcript(ctx.prompt_segments, source_language=ctx.source_language_code)
    return marked or ctx.clean_text


def _with_reader_evidence(plan: PlanResult) -> PlanResult:
    """The reconciled plan carrying the readers' own answers as its evidence.

    The block's evidence line keeps showing what plan and memory said
    (``merge_evidence``: a plan ``false`` the memory left silent still renders
    ``✗``); reconcile and the readers after it use the strict doubly-false view.
    """
    return plan.model_copy(update={"evidence": plan.plan_evidence or {}})


async def run_phase_text(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Transcript, then the probe, then plan ∥ memory, reconcile, ``ctx.video_memory``."""
    with timed_step("transcript"):
        async for event in after_captions(run_phase_transcript)(ctx):
            yield event
    ctx.transcript_ready.set()
    await probe_for_plan(ctx)
    async for event in replace_unusable_captions(ctx):
        yield event
    ctx.prompt_transcript = render_prompt_transcript(ctx)
    async for event in run_parallel_phases([run_phase_plan, run_phase_memory], ctx):
        yield event
    for event in plan_done_events(ctx):
        yield event
    assert ctx.plan_result is not None
    ctx.video_memory = render_video_memory(_with_reader_evidence(ctx.plan_result), ctx.memory)
