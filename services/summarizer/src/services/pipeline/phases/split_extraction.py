"""Phase 4, split (``EXTRACTION_PARALLEL``): field groups fired from plan-done (pipeline-1min 3.4).

A member of the phase-2 group, next to the text branch and frames. At
plan-done (reconciled tabs + ``<video_memory>``) it requests the fields the
tabs read (``field_requests``), cuts them into groups (``field_groups``) and
fires them over the run's chunks (``extraction_groups``): the text groups at
once — text1 warms the shared prefix — and the visual group once frames are
done, cut from the tabs frames-done decided. Each landed group sends an
``extraction_progress`` (``section="groups"``); when all landed, the outcomes
are merged and validated with today's functions (3.5 replaces them) into ONE
``extraction_complete``. Assembly still runs after the whole phase-2 group.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.models.domain_types import validate_domain_output_with_drops
from src.services.pipeline.extraction_chunks import plan_chunks
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import (
    GroupOutcome,
    SplitRun,
    VisualCut,
    fire_groups,
)
from src.services.pipeline.extraction_merger import merge_batch_extractions
from src.services.pipeline.extractor import _build_prompt
from src.services.pipeline.field_groups import ExtractionGroup, build_groups, visual_group
from src.services.pipeline.field_requests import FieldRequest, reads_extraction, requested_fields
from src.services.pipeline.phases.extraction import (
    fail_extraction,
    finish_extraction,
    record_validation,
)
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import timed_step
from src.services.pipeline.prompt_builder import format_gallery_frames_for_extraction
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import grouping_config

if TYPE_CHECKING:
    from src.models.pipeline_types import PlanResult
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

_PROGRESS_SECTION = "groups"


# ─── Plan-done: fields → groups → the run ───


def _requested(plan: PlanResult) -> list[FieldRequest]:
    return requested_fields(
        plan.tabs, plan.content_tags, plan.modifiers, scalar_fields, primary_tag=plan.primary_tag
    )


def _job(plan: PlanResult, visual_annotations: str = "", frame_context: str = "") -> GroupJob:
    return GroupJob(
        tabs=list(plan.tabs),
        active=[*plan.content_tags, *plan.modifiers],
        primary_tag=plan.primary_tag,
        visual_annotations=visual_annotations,
        frame_context=frame_context,
    )


def _split_run(ctx: PipelineContext) -> tuple[SplitRun, bool]:
    """The run's calls at plan-done, and whether a visual group is planned.

    The prompt is today's, built without frame inputs: blocks 1 and 2 never
    carry them; the visual group's job binds them at frames-done.
    """
    plan, triage, video = ctx.plan_result, ctx.triage, ctx.video_data
    assert plan is not None and triage is not None and video is not None
    groups = build_groups(_requested(plan), plan.tabs, grouping_config())
    text_groups = tuple(g for g in groups if not g.visual)
    prompt = _build_prompt(
        triage,
        {"title": video.title, "duration": video.duration},
        video_memory=ctx.video_memory,
        frame_context="",
        visual_annotations="",
    )
    run = SplitRun(ctx.llm_service, prompt, _job(plan), tuple(plan_chunks(ctx)), text_groups)
    return run, len(groups) > len(text_groups)


def _visual_cutter(ctx: PipelineContext) -> VisualCut:
    """At frames-done (deferred code tabs already decided): the fields no text group asked for."""

    async def cut(fired: frozenset[str]) -> tuple[ExtractionGroup, GroupJob] | None:
        await ctx.frames_done.wait()
        plan = ctx.plan_result
        assert plan is not None
        group = visual_group(_requested(plan), fired)
        if group is None:
            return None
        frames = format_gallery_frames_for_extraction(
            ctx.scene_frames_gallery, ctx.frame_descriptions
        )
        return group, _job(plan, ctx.visual_annotations, frames)

    return cut


# ─── Landing ───


@dataclass
class _Progress:
    """``extraction_progress`` per landed call; ``of`` follows the visual group's fate."""

    of: int
    visual_planned: bool
    done: int = 0

    def event(self) -> str:
        percent = 5 + int(90 * self.done / self.of) if self.of else 5
        payload = {"section": _PROGRESS_SECTION, "batch": self.done, "of": self.of}
        return sse_event("extraction_progress", {**payload, "percent": percent})

    def skipped(self) -> None:
        """A visual group with nothing left to extract after frames-done."""
        if self.visual_planned:
            self.of -= 1

    def landed(self, outcome: GroupOutcome) -> str:
        if outcome.group.visual and not self.visual_planned:
            self.of += 1  # a frames-done demotion onto a field no text group extracts
        self.done += 1
        self.of = max(self.of, self.done)
        return self.event()


def _log_landed(ctx: PipelineContext, outcome: GroupOutcome) -> None:
    logger.info(
        "[pipeline] extraction group %s chunk %d %s in %d ms (%d output tokens) video_id=%s",
        outcome.group.group_id,
        outcome.chunk,
        outcome.status,
        outcome.wall_ms,
        outcome.output_tokens,
        ctx.video_summary_id,
    )


async def _land(
    ctx: PipelineContext, run: SplitRun, visual_planned: bool, outcomes: list[GroupOutcome]
) -> AsyncGenerator[str, None]:
    """Fire every call; one progress event per landed call (outcomes collected in order)."""
    per_chunk = len(run.text_groups) + int(visual_planned)
    progress = _Progress(of=len(run.chunks) * per_chunk, visual_planned=visual_planned)
    yield progress.event()
    async for outcome in fire_groups(run, _visual_cutter(ctx)):
        if outcome is None:
            progress.skipped()
            continue
        outcomes.append(outcome)
        _log_landed(ctx, outcome)
        yield progress.landed(outcome)


# ─── Merge (interim: today's merger + validation, 3.5 replaces) ───


def _ordered(run: SplitRun, outcomes: Sequence[GroupOutcome]) -> list[GroupOutcome]:
    """Chunk-major, then text1..textN, the visual group last."""
    rank = {group.group_id: i for i, group in enumerate(run.text_groups)}
    last = len(rank)
    return sorted(outcomes, key=lambda o: (o.chunk, rank.get(o.group.group_id, last)))


def _nothing_to_extract(ctx: PipelineContext) -> bool:
    """No call fired because no planned tab reads an extraction field (overview, quiz, frames)."""
    plan = ctx.plan_result
    return plan is not None and not any(reads_extraction(tab) for tab in plan.tabs)


async def _complete(
    ctx: PipelineContext, run: SplitRun, outcomes: Sequence[GroupOutcome]
) -> list[str]:
    """ONE ``extraction_complete`` from every landed group; today's error when none landed.

    No call fired is only an empty extraction when no planned tab reads one:
    it stays ``{}`` (no faithfulness judge over a defaults skeleton). The
    groups count as batches, so a failed group marks the run degraded.
    """
    landed = [o for o in _ordered(run, outcomes) if o.status == "ok" and o.data is not None]
    empty_by_design = not outcomes and _nothing_to_extract(ctx)
    if not landed and not empty_by_design:
        logger.error(
            "[pipeline] Split extraction landed no group (%d fired) for %s",
            len(outcomes),
            ctx.video_summary_id,
        )
        return [await fail_extraction(ctx)]
    triage = ctx.triage
    assert triage is not None
    validated: dict[str, Any] = {}
    if landed:
        merged = merge_batch_extractions([o.data for o in landed], triage.content_tags)
        validated, report = validate_domain_output_with_drops(
            triage.content_tags, triage.modifiers, merged
        )
        record_validation(ctx, report)
    ctx.extraction_data = validated
    finish_extraction(ctx, len(outcomes), len(landed))
    return [sse_event("extraction_complete", {"data": validated})]


async def run_phase_split_extraction(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """From plan-done: fire the field groups, stream their progress, then the merged result."""
    await ctx.plan_done.wait()
    if ctx.plan_result is None:
        return  # the text branch failed: no plan to extract for (the group is being cancelled)
    with timed_step("extraction"):
        run, visual_planned = _split_run(ctx)
        outcomes: list[GroupOutcome] = []
        async for event in _land(ctx, run, visual_planned, outcomes):
            yield event
        for event in await _complete(ctx, run, outcomes):
            yield event
