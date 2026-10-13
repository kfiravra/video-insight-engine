"""Phase 4, split (``EXTRACTION_PARALLEL``): field groups fired from plan-done (pipeline-1min 3.4).

A member of the phase-2 group, next to the text branch and frames. At
plan-done (reconciled tabs + ``<video_memory>``) it requests the fields the
tabs read (``field_requests``), cuts them into groups (``field_groups``) and
fires them over the run's chunks (``extraction_groups``): the text groups at
once — text1 warms the shared prefix — and the visual group once frames are
done, cut from the tabs frames-done decided. Each landed group sends an
``extraction_progress`` (``section="groups"``) followed by previews of the
tabs it completed (``group_emission``, 3.6: non-final; the quiz starts once
its groups are in); each group's answer was validated and cut to its own keys
as it landed, and when all landed they are merged (``group_merge``, 3.5) into
ONE ``extraction_complete``. Assembly runs after the whole phase-2 group and
sends every tab final.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.services.pipeline.extraction_chunks import (
    ExtractionChunk,
    key_frames_by_chunk,
    plan_chunks,
)
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import (
    GroupOutcome,
    SplitRun,
    VisualCut,
    fire_groups,
)
from src.services.pipeline.extractor import _build_prompt
from src.services.pipeline.field_groups import ExtractionGroup, build_groups, visual_group
from src.services.pipeline.field_requests import FieldRequest, reads_extraction, requested_fields
from src.services.pipeline.group_failures import flag_failed_groups
from src.services.pipeline.group_merge import merge_groups, merge_order, requested_quality
from src.services.pipeline.phases.enrichment import cancel_quiz_task
from src.services.pipeline.phases.extraction import (
    fail_extraction,
    finish_extraction,
    record_validation,
)
from src.services.pipeline.phases.group_emission import GroupLanding
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


def _job(
    plan: PlanResult,
    visual_annotations: str = "",
    frame_context: str = "",
    chunk_frame_context: Mapping[int, str] | None = None,
) -> GroupJob:
    return GroupJob(
        tabs=list(plan.tabs),
        active=[*plan.content_tags, *plan.modifiers],
        primary_tag=plan.primary_tag,
        visual_annotations=visual_annotations,
        frame_context=frame_context,
        chunk_frame_context=chunk_frame_context or {},
    )


async def _split_run(ctx: PipelineContext) -> tuple[SplitRun, bool]:
    """The run's calls at plan-done, and whether a visual group is planned.

    The chunks come first (a long video's chapter chain can wait on the
    description or chapter_detect), the groups from the tabs as they are then.
    The prompt is today's, built without frame inputs: blocks 1 and 2 never
    carry them; the visual group's job binds them at frames-done.
    """
    chunks = await plan_chunks(ctx)
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
    run = SplitRun(
        ctx.llm_service,
        prompt,
        _job(plan),
        tuple(chunks),
        text_groups,
        tuple(plan.content_tags),
        tuple(plan.modifiers),
    )
    return run, len(groups) > len(text_groups)


def _visual_cutter(ctx: PipelineContext, chunks: Sequence[ExtractionChunk]) -> VisualCut:
    """At frames-done (deferred code tabs already decided): the fields no text group asked for.

    A long video's chunks each get the key frames of their own window.
    """

    async def cut(fired: frozenset[str]) -> tuple[ExtractionGroup, GroupJob] | None:
        await ctx.frames_done.wait()
        plan = ctx.plan_result
        assert plan is not None
        group = visual_group(_requested(plan), fired)
        if group is None:
            return None
        gallery, descriptions = ctx.scene_frames_gallery, ctx.frame_descriptions
        frames = format_gallery_frames_for_extraction(gallery, descriptions)
        per_chunk = key_frames_by_chunk(gallery, descriptions, chunks)
        return group, _job(plan, ctx.visual_annotations, frames, per_chunk)

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
    """Fire every call; per landed call one progress event, then the tabs it completed.

    Outcomes are collected in landing order.
    """
    per_chunk = len(run.text_groups) + int(visual_planned)
    progress = _Progress(of=len(run.chunks) * per_chunk, visual_planned=visual_planned)
    landing = GroupLanding.for_run(ctx, run.text_groups, len(run.chunks))
    yield progress.event()
    async for outcome in fire_groups(run, _visual_cutter(ctx, run.chunks)):
        if outcome is None:
            progress.skipped()
            continue
        outcomes.append(outcome)
        _log_landed(ctx, outcome)
        yield progress.landed(outcome)
        assert ctx.plan_result is not None
        for event in landing.events(outcome, outcomes, _requested(ctx.plan_result)):
            yield event


# ─── Merge ───


def _group_rows(outcomes: Sequence[GroupOutcome]) -> list[dict[str, Any]]:
    """``pipeline.extraction._groups``: one row per group call, in merge order."""
    return [
        {
            "group": o.group.group_id,
            "chunk": o.chunk,
            "fields": [f.path for f in o.group.fields],
            "status": o.status,
            "outputTokens": o.output_tokens,
            "wallMs": o.wall_ms,
            # 3.8: asked once more after a lost answer ("parse" | "empty" | "stream").
            **({"retried": o.retried} if o.retried else {}),
        }
        for o in merge_order(outcomes)
    ]


def _nothing_to_extract(ctx: PipelineContext) -> bool:
    """No call fired because no planned tab reads an extraction field (overview, quiz, frames)."""
    plan = ctx.plan_result
    return plan is not None and not any(reads_extraction(tab) for tab in plan.tabs)


async def _complete(ctx: PipelineContext, outcomes: Sequence[GroupOutcome]) -> list[str]:
    """ONE ``extraction_complete`` from every landed group; today's error when none landed.

    No call fired is only an empty extraction when no planned tab reads one:
    it stays ``{}`` (no faithfulness judge over a defaults skeleton). The
    groups count as batches, so a failed group marks the run degraded; the
    quality score counts the fields the groups were asked for. The group rows
    and the degraded flag are kept either way: assembly names the tabs failed
    groups cost (3.8).
    """
    landed = [o for o in outcomes if o.status == "ok" and o.data is not None]
    empty_by_design = not outcomes and _nothing_to_extract(ctx)
    ctx.extraction_groups = _group_rows(outcomes)
    flag_failed_groups(ctx, len(outcomes), len(landed))
    if not landed and not empty_by_design:
        logger.error(
            "[pipeline] Split extraction landed no group (%d fired) for %s",
            len(outcomes),
            ctx.video_summary_id,
        )
        return [await fail_extraction(ctx)]
    merged, report = merge_groups(outcomes)
    record_validation(ctx, report)
    ctx.extraction_data = merged
    quality = requested_quality([f for o in outcomes for f in o.group.fields], merged)
    finish_extraction(ctx, len(outcomes), len(landed), quality=quality)
    return [sse_event("extraction_complete", {"data": merged})]


async def run_phase_split_extraction(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """From plan-done: fire the field groups, stream their progress + tabs, then the merged result.

    A run that fails or is cancelled here stops the quiz call it started.
    """
    await ctx.plan_done.wait()
    if ctx.plan_result is None:
        return  # the text branch failed: no plan to extract for (the group is being cancelled)
    try:
        with timed_step("extraction"):
            run, visual_planned = await _split_run(ctx)
            outcomes: list[GroupOutcome] = []
            async for event in _land(ctx, run, visual_planned, outcomes):
                yield event
            for event in await _complete(ctx, outcomes):
                yield event
    except BaseException:
        cancel_quiz_task(ctx)
        raise
