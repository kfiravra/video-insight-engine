"""Phase 3: Plan — single LLM call for video analysis + tab design.

Replaces the old manifest + triage 2-call flow with a single plan call. The
tier probe (started with phase 2, ``phases/probe.py``) steers it: a confident
probe domain picks the playbook, its format refines it, and its answer is the
plan's one ``Hint:`` line.

The plan phase only plans. Once plan ∥ memory are both in, the text branch
reconciles the tabs (:func:`plan_done_events`, 3.2) and only then emits
``triage_complete`` + ``meta``, so ``meta`` never lists a tab reconcile already
dropped; the code tabs and code requirement it deferred are decided at
frames-done (:func:`frames_done_events`), which sends a fresh ``meta`` when
that changed the tabs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.override_state import check_override
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.plan import run_plan
from src.services.pipeline.reconcile import reconcile_plan, resolve_deferred
from src.services.pipeline.triage import TriageResult

if TYPE_CHECKING:
    from src.models.pipeline_types import PlanResult
    from src.models.probe_types import TierProbe
    from src.services.pipeline.context import PipelineContext
    from src.services.video.youtube import VideoData

logger = logging.getLogger(__name__)

# Above this the probe's domain replaces the metadata category as the playbook
# domain (the classifier it replaced used the same bar).
PROBE_CONFIDENCE_THRESHOLD = 0.6


def probe_hint(probe: TierProbe) -> str:
    """The plan's ``Hint:`` line — the probe's whole answer, confidence included."""
    demo = "true" if probe.has_visual_demo else "false"
    return (
        f"domain={probe.domain} format={probe.format} "
        f"has_visual_demo={demo} confidence={probe.confidence:.2f}"
    )


def _set_plan_inputs(ctx: PipelineContext, video_data: VideoData, probe: TierProbe | None) -> None:
    """``category_hint`` + ``content_format``: override > confident probe > metadata."""
    if ctx.override:
        ctx.category_hint = ctx.override.get("category")
        return
    ctx.category_hint = video_data.context.category if video_data.context else None
    if probe is None:
        return
    ctx.content_format = probe.format
    if probe.confidence > PROBE_CONFIDENCE_THRESHOLD:
        ctx.category_hint = probe.domain


def _store_plan_result(ctx: PipelineContext, plan_result: PlanResult) -> None:
    """Put the plan on ``ctx`` plus the triage carriers downstream stages read."""
    ctx.plan_result = plan_result
    ctx.triage = TriageResult(
        content_tags=plan_result.content_tags,
        modifiers=plan_result.modifiers,
        primary_tag=plan_result.primary_tag,
        user_goal=plan_result.user_goal,
        tabs=plan_result.tabs,
        confidence=plan_result.confidence,
    )
    ctx.triage_dict = plan_result.to_triage_dict(ctx.content_format)


def _meta_payload(ctx: PipelineContext, plan_result: PlanResult) -> dict[str, object]:
    """The ``meta`` SSE event — tab labels for progressive rendering."""
    assert ctx.video_data is not None
    return {
        "title": ctx.video_data.title,
        "contentTags": plan_result.content_tags,
        "modifiers": plan_result.modifiers,
        "primaryTag": plan_result.primary_tag,
        "tabCount": len(plan_result.tabs),
        "tabLabels": [
            {"id": t["id"], "label": t["label"], "emoji": t.get("emoji", "")}
            for t in plan_result.tabs
        ],
        "contentFormat": ctx.content_format,
        "language": ctx.language,
        "isRTL": ctx.is_rtl,
    }


async def run_phase_plan(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Plan from the tier probe's answer, then make the single plan call.

    The text branch awaited the probe (``probe_for_plan``) before starting
    plan ∥ memory, so the answer is already on ``ctx.probe`` — no second wait.
    """
    video_data = ctx.video_data
    assert video_data is not None

    llm_feature_var.set("summarize:plan")
    ctx.override = check_override(ctx.video_summary_id)
    probe = ctx.probe
    _set_plan_inputs(ctx, video_data, probe)

    # Run plan (single Sonnet call — replaces manifest + triage) on the FULL
    # marked transcript the text branch rendered once for plan and memory.
    plan_result = await run_plan(
        title=video_data.title,
        channel=video_data.channel or "",
        description=video_data.description or "",
        duration=video_data.duration or 0,
        category_hint=ctx.category_hint,
        content_format=ctx.content_format,
        transcript=ctx.prompt_transcript,
        llm_service=ctx.llm_service,
        probe_hint=probe_hint(probe) if probe is not None and not ctx.override else None,
    )

    ctx.plan_result = plan_result
    logger.info(
        "pipeline.plan",
        extra={
            "video_id": ctx.video_summary_id,
            "content_tags": plan_result.content_tags,
            "tabs_designed": len(plan_result.tabs),
            "tab_components": [t.get("component") for t in plan_result.tabs],
            "confidence": plan_result.confidence,
            "probe_domain": probe.domain if probe else None,
            "probe_format": probe.format if probe else None,
            "probe_confidence": probe.confidence if probe else None,
        },
    )
    # Still an async generator (run_parallel_phases streams its members);
    # the plan's events go out at plan-done, after reconcile.
    return
    yield


def plan_done_events(ctx: PipelineContext) -> list[str]:
    """Reconcile the plan against the memory, store it, then ``triage_complete`` + ``meta``."""
    assert ctx.plan_result is not None
    _store_plan_result(ctx, reconcile_plan(ctx.plan_result, ctx.memory, ctx.content_format))
    return [
        sse_event("triage_complete", ctx.triage_dict),
        sse_event("meta", _meta_payload(ctx, ctx.plan_result)),
    ]


def _tab_heads(plan: PlanResult) -> list[tuple[str, str]]:
    return [(tab["id"], tab["label"]) for tab in plan.tabs]


def frames_done_events(ctx: PipelineContext) -> list[str]:
    """Frames-done: decide the code tabs and code requirement reconcile deferred,
    on what the frames show.

    A fresh ``meta`` goes out when that changed the tab list, so the client
    drops a pending pill for a tab that will never come (and shows one for a
    backfilled code tab). Called by every path at frames-done (today's: the
    orchestrator's ``visual_inject`` step; the split path's: before the visual
    group is cut).
    """
    plan = ctx.plan_result
    if plan is None or not plan.awaits_screen:
        return []
    frames = (ctx.frame_descriptions or [], ctx.scene_frames_all or [])
    _store_plan_result(ctx, resolve_deferred(plan, *frames, ctx.content_format))
    assert ctx.plan_result is not None
    if _tab_heads(ctx.plan_result) == _tab_heads(plan):
        return []
    return [sse_event("meta", _meta_payload(ctx, ctx.plan_result))]
