"""Run-level ``pipeline.timing`` plumbing for :mod:`src.routes.pipeline_orchestration`.

The recorder itself (and the deep-call-site helpers) live in
:mod:`src.services.pipeline.pipeline_timing`. This module holds the
runner-side pieces: stamping finished phases, the end-of-run DONE log
line, and persisting the timing document (Mongo + Langfuse trace) for
successful and failed runs alike.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.services.observability import log_span, update_trace_metadata
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.pipeline_helpers import PipelineTimer
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder

logger = logging.getLogger(__name__)

# An answer cut at max_tokens. The JSON repair keeps whatever parsed, so a
# truncated plan silently loses its trailing tabs — counted per run to see it.
_TRUNCATED_FINISH_REASON = "length"


def mark_phase(
    ctx: PipelineContext, timing: PipelineTimingRecorder, name: str, phase_start: float
) -> None:
    """Record a finished phase on ``ctx.phase_times`` (DONE log) and the timing doc."""
    phase_end = time.monotonic()
    ctx.phase_times[name] = round(phase_end - phase_start, 1)
    timing.add_phase(name, phase_start, phase_end)


def _tab_counts(ctx: PipelineContext) -> tuple[int, int]:
    """(planned, assembled) tab counts — 0 when the phase never produced them."""
    planned = len(ctx.triage.tabs) if ctx.triage else 0
    assembled = len(getattr(ctx, "assembled_tabs", None) or [])
    return planned, assembled


def truncated_calls(timing: PipelineTimingRecorder) -> int:
    """LLM answers this run that stopped at ``max_tokens`` (finish_reason=length)."""
    return sum(1 for c in timing.llm_calls if c.get("finishReason") == _TRUNCATED_FINISH_REASON)


def _phase_walls(ctx: PipelineContext, timing: PipelineTimingRecorder) -> dict[str, float]:
    """Seconds per phase: every recorded step (plan and memory run inside the
    phase-2 group), the runner's own stamps winning."""
    walls = {p["name"]: round(p["wallMs"] / 1000, 1) for p in timing.phases}
    return {**walls, **ctx.phase_times}


def log_run_summary(
    ctx: PipelineContext,
    timer: PipelineTimer,
    timing: PipelineTimingRecorder,
    log: logging.Logger,
) -> None:
    """One-line pipeline summary with ALL phase timings and tab counts.

    Emitted on the caller's ``log`` (the orchestrator's logger): the replay
    driver and log searches key on ``src.routes.pipeline_orchestration`` +
    ``[pipeline] DONE``.
    """
    pt = _phase_walls(ctx, timing)
    planned, assembled = _tab_counts(ctx)
    log.info(
        "[pipeline] DONE youtube_id=%s in %.0fs | "
        "metadata=%.1fs transcript_frames=%.1fs visual_inject=%.1fs "
        "plan=%.1fs(%s) memory=%.1fs(%s) extraction=%.1fs quiz=%.1fs(%s) "
        "assembly=%.1fs synthesis=%.1fs | tabs planned=%d assembled=%d emitted=%d "
        "| llm truncated=%d",
        ctx.youtube_id,
        timer.elapsed(),
        pt.get("metadata", 0),
        pt.get("transcript_frames", 0),
        pt.get("visual_inject", 0),
        pt.get("plan", 0),
        "ok" if ctx.plan_result is not None else "FAIL",
        pt.get("memory", 0),
        "ok" if getattr(ctx, "memory", None) is not None else "FAIL",
        pt.get("extraction", 0),
        pt.get("enrichment", 0),
        _quiz_outcome(ctx, pt),
        pt.get("assembly", 0),
        pt.get("synthesis", 0),
        planned,
        assembled,
        timing.tabs_emitted,
        truncated_calls(timing),
    )


def _quiz_outcome(ctx: PipelineContext, walls: dict[str, float]) -> str:
    """ok / FAIL (asked, nothing usable) / skip (the plan demanded no quiz)."""
    if ctx.enrichment_data:
        return "ok"
    return "FAIL" if "enrichment" in walls else "skip"


def _trace_timing_summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Trace-metadata copy of ``pipeline.timing`` (LLM calls are already
    generations on the trace, so they are summarized as counts here)."""
    return {
        "totalMs": doc["totalMs"],
        "phases": doc["phases"],
        "milestones": doc["milestones"],
        "downloads": doc["downloads"],
        "costUsd": doc["costUsd"],
        "counts": doc["counts"],
    }


def _log_phase_spans(timing: PipelineTimingRecorder) -> None:
    """Mirror each recorded phase as a Langfuse span with real start/end times."""
    for phase in timing.phases:
        log_span(
            name=f"phase:{phase['name']}",
            start_time=timing.wall_time(phase["startMs"]),
            end_time=timing.wall_time(phase["endMs"]),
        )


async def persist_run_timing(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
    timing: PipelineTimingRecorder,
) -> None:
    """Write ``pipeline.timing`` and mirror it onto the trace — for successful
    AND failed runs. Best-effort: timing must never mask the run's own outcome."""
    if ctx.row_deleted:
        return
    try:
        planned, assembled = _tab_counts(ctx)
        doc = timing.to_document(tabs_planned=planned, tabs_assembled=assembled)
        doc["counts"]["truncated"] = truncated_calls(timing)
        update_trace_metadata({"timing": _trace_timing_summary(doc)})
        _log_phase_spans(timing)
        await asyncio.to_thread(repository.set_pipeline_timing, video_summary_id, doc)
    except Exception as exc:
        logger.warning("[pipeline] timing record failed for %s: %s", video_summary_id, exc)
