"""Phase 3: Plan — single LLM call for video analysis + tab design.

Replaces the old manifest + triage 2-call flow with a single plan call.
Classifier runs concurrently for early domain signal.
Description analysis now runs in metadata phase (Section 2.6).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.override_state import check_override
from src.services.pipeline.classifier import CLASSIFIER_CONFIDENCE_THRESHOLD, classify_domain_format
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.plan import run_plan
from src.services.pipeline.triage import TriageResult
from src.services.transcript.render import render_transcript

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


async def run_phase_plan(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Run classifier concurrently, then single plan call."""
    video_data = ctx.video_data
    assert video_data is not None

    # Override / category hint
    llm_feature_var.set("summarize:plan")
    ctx.override = check_override(ctx.video_summary_id)
    if ctx.override:
        ctx.category_hint = ctx.override.get("category")
    else:
        ctx.category_hint = video_data.context.category if video_data.context else None

    # Run classifier (fast Haiku, ~3s) to get category_hint before plan
    classification = None
    try:
        llm_feature_var.set("summarize:classifier")
        classification = await classify_domain_format(
            title=video_data.title,
            channel=video_data.channel or "",
            duration=video_data.duration or 0,
            tags=video_data.context.tags if video_data.context else [],
            transcript_preview=ctx.clean_text[:2000],
            llm_service=ctx.llm_service,
        )
    except Exception as e:
        logger.warning("Classifier failed: %s — using rule-based category", e)

    if classification is not None and not ctx.override:
        ctx.content_format = classification.format
        if classification.traits:
            ctx.content_traits = classification.traits
        if classification.confidence > CLASSIFIER_CONFIDENCE_THRESHOLD:
            ctx.category_hint = classification.domain
            logger.info(
                "pipeline.classifier.override",
                extra={
                    "video_id": ctx.video_summary_id,
                    "domain": classification.domain,
                    "format": classification.format,
                    "confidence": classification.confidence,
                    "reasoning": (classification.reasoning or "")[:200],
                },
            )
        else:
            logger.info(
                "pipeline.classifier.low_confidence",
                extra={
                    "video_id": ctx.video_summary_id,
                    "domain": classification.domain,
                    "format": classification.format,
                    "confidence": classification.confidence,
                },
            )

    # Run plan (single Sonnet call — replaces manifest + triage). It reads the
    # FULL transcript with [m:ss] markers, rendered from the raw segments so
    # the visual annotations injected into clean_text never reach it; a
    # metadata-only transcript has no segments (renders "") → clean_text.
    llm_feature_var.set("summarize:plan")
    segments = ctx.transcript_data.segments if ctx.transcript_data else []
    plan_result = await run_plan(
        title=video_data.title,
        channel=video_data.channel or "",
        description=video_data.description or "",
        duration=video_data.duration or 0,
        category_hint=ctx.category_hint,
        content_format=ctx.content_format,
        transcript=render_transcript(segments, source_language=ctx.source_language_code)
        or ctx.clean_text,
        llm_service=ctx.llm_service,
    )

    # Store plan result and populate backward-compat fields
    ctx.plan_result = plan_result
    ctx.video_dna_text = plan_result.to_video_context_full()
    ctx.video_dna_compact = plan_result.to_video_context_compact()

    # Populate ctx.triage for backward compat with extraction/enrichment/synthesis/assembly
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
    yield sse_event("triage_complete", ctx.triage_dict)

    # Meta event for progressive rendering
    yield sse_event(
        "meta",
        {
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
        },
    )

    logger.info(
        "pipeline.plan",
        extra={
            "video_id": ctx.video_summary_id,
            "content_tags": plan_result.content_tags,
            "tabs_designed": len(plan_result.tabs),
            "tab_components": [t.get("component") for t in plan_result.tabs],
            "confidence": plan_result.confidence,
            "classifier_domain": classification.domain if classification else None,
            "classifier_format": classification.format if classification else None,
            "classifier_confidence": classification.confidence if classification else None,
        },
    )


# Backward-compat alias
run_phase_triage = run_phase_plan
