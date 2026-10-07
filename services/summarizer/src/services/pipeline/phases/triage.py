"""Phase 3: Plan — single LLM call for video analysis + tab design.

Replaces the old manifest + triage 2-call flow with a single plan call. The
tier probe (started with phase 2, ``phases/probe.py``) steers it: a confident
probe domain picks the playbook, its format refines it, and its answer is the
plan's one ``Hint:`` line.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.override_state import check_override
from src.services.pipeline.phases.probe import probe_for_plan
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.plan import run_plan
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
    ctx.video_dna_text = plan_result.to_video_context_full()
    ctx.triage = TriageResult(
        content_tags=plan_result.content_tags,
        modifiers=plan_result.modifiers,
        primary_tag=plan_result.primary_tag,
        user_goal=plan_result.user_goal,
        tabs=plan_result.tabs,
        confidence=plan_result.confidence,
    )
    ctx.triage_dict = {
        "contentTags": plan_result.content_tags,
        "modifiers": plan_result.modifiers,
        "primaryTag": plan_result.primary_tag,
        "userGoal": plan_result.user_goal,
        "tabs": plan_result.tabs,
        "confidence": plan_result.confidence,
        "contentFormat": ctx.content_format,
    }


def _meta_payload(
    ctx: PipelineContext, video_data: VideoData, plan_result: PlanResult
) -> dict[str, object]:
    """The ``meta`` SSE event — tab labels for progressive rendering."""
    return {
        "title": video_data.title,
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
    """Read the tier probe's answer, then make the single plan call."""
    video_data = ctx.video_data
    assert video_data is not None

    llm_feature_var.set("summarize:plan")
    ctx.override = check_override(ctx.video_summary_id)
    probe = await probe_for_plan(ctx)
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

    _store_plan_result(ctx, plan_result)
    yield sse_event("triage_complete", ctx.triage_dict)
    yield sse_event("meta", _meta_payload(ctx, video_data, plan_result))

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


# Backward-compat alias
run_phase_triage = run_phase_plan
