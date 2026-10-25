"""What the run reports about itself: the coverage on meta, the planner's designed-tab
count, the row the save writes and the ``complete`` event.

Moved out of ``phases/assembly`` (output-quality 3.7) unchanged — only the version
stamp is now passed in. The save itself, the shared artifacts and ``done`` stay there.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.services.pipeline.phases.extraction import extraction_record
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.plan_reconcile import PLAN_PARSE
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.pipeline.reconcile_pass import ADDED_ACTIONS
from src.services.pipeline.tab_ledger import tab_id

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


def flag_coverage(ctx: PipelineContext) -> None:
    """Coverage metric on meta, plus ``degraded`` for a partial result (FE retry affordance)."""
    coverage = getattr(ctx, "extraction_coverage", None)
    if not coverage or ctx.assembled_meta is None:
        return
    ctx.assembled_meta["extractionCoverage"] = coverage
    if coverage_is_degraded(coverage):
        ctx.assembled_meta["degraded"] = True


def tabs_designed(ctx: PipelineContext, plan_dropped: list[dict]) -> int:
    """What the planner designed: its drops plus its surviving tabs.

    Reconcile (3.2) also adds tabs (required backfill, fill to 3) — never the
    planner's. When the domain defaults stand in (``planFallback``) only the
    parse drops were the planner's: later reconcile drops hit default tabs.
    """
    assert ctx.triage is not None
    plan = ctx.plan_result
    rows = plan.reconcile if plan else []
    if plan is not None and plan.plan_fallback:
        later = [r for r in rows if r.get("action") == "dropped" and r.get("stage") != PLAN_PARSE]
        return len(plan_dropped) - len(later)
    added = {r.get("tabId") for r in rows if r.get("action") in ADDED_ACTIONS}
    return len(plan_dropped) + sum(1 for tab in ctx.triage.tabs if tab.get("id") not in added)


def result_document(
    ctx: PipelineContext,
    accounting: dict,
    processing_time: int,
    degraded: bool,
    *,
    pipeline_version: str,
) -> dict:
    """The row the save writes. Non-English videos stay "processing": the
    translation phase persists the sourceLanguage block and owns "completed",
    so an interrupted translation leaves a retriable doc, not a fake-completed one."""
    assert ctx.video_data is not None
    result: dict = {
        "status": "processing" if ctx.source_language_code else "completed",
        "youtubeId": ctx.youtube_id,
        "title": ctx.video_data.title,
        "creator": ctx.video_data.channel,
        "duration": ctx.video_data.duration,
        "thumbnailUrl": ctx.video_data.thumbnail_url,
        "meta": ctx.assembled_meta or {},
        "tabs": ctx.assembled_tabs or [],
        "language": ctx.language,
        "isRTL": ctx.is_rtl,
        # Version stamp — the api's serve path regens docs whose stored
        # version differs from the canonical pipeline-version.json; docs
        # WITHOUT the field predate stamping and are served as-is.
        "pipelineVersion": pipeline_version,
        "pipeline": {
            "triage": ctx.triage_dict,
            "reconcile": ctx.plan_result.reconcile if ctx.plan_result else [],
            "extraction": extraction_record(ctx),
            "enrichment": ctx.enrichment_data,
            "synthesis": ctx.synthesis_dict,
            "assembly": accounting,
        },
        "processedAt": datetime.now(timezone.utc),
        "processingTimeMs": processing_time,
    }
    if degraded:
        # Top-level mirror of meta.degraded — queryable by the admin run badge
        # without unpacking meta. Only written when True; absence means clean
        # (or pre-feature doc).
        result["degraded"] = True
    return result


def complete_event(ctx: PipelineContext, processing_time: int, degraded: bool) -> str:
    """``complete`` with the final tab order — a streamed tab missing from it was withdrawn."""
    tabs = ctx.assembled_tabs or []
    withdrawn = ctx.tab_ledger.withdrawn(tabs)
    if withdrawn:
        logger.info("Streamed tabs the final response withdrew: %s", withdrawn)
    return sse_event(
        "complete",
        {
            "tabCount": len(tabs),
            "tabIds": [tab_id(tab) for tab in tabs],
            "processingTimeMs": processing_time,
            "degraded": degraded,
        },
    )
