"""Phase 7: Assembly — tabs first, then synthesis ∥ moment fill ∥ quiz, then save, cache, done.

Order (pipeline-1min 1d.1/1d.3; every ``tab_ready`` through the run's
``TabLedger`` — the emission contract, 2.1):

1. the description analysis (capped wait) — meta + its SSE event;
2. synthesis FIRST only when memory left the hero empty: the overview and the
   minimum-3 Key Info fallback need its tldr/takeaways;
3. every tab but the moment tabs streams at once (the quiz is not in yet) —
   final where no late phase can change it;
4. the late group — the moment fill (streams the moment tabs, framed),
   synthesis unless step 2 ran it (patches meta + the overview in place), the
   quiz (the response is assembled again with it, ``late_quiz``);
5. the final re-emit: every tab not yet sent final, at its final position;
6. ``complete`` (with the final tab order), the save, the shared artifacts
   (Redis, Qdrant), ``done``.

Split path (3.6): every group has landed, so finals follow the rules above
again; the tabs already streamed per group as previews (``phases/group_emission``),
so step 3 previews only what the client lacks — the overview, tabs assembly
added, tabs the last groups changed — the quiz call the split extraction
started is awaited in step 4 (cancelled when the plan no longer uses it), and
step 5 sends the overview last.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.services.cache.response_cache import response_cache
from src.services.media.s3_client import S3Client
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.core import count_dropped_tabs
from src.services.pipeline.assembly.moment_frame_fill import fill_moment_frames
from src.services.pipeline.enrichment import needs_quiz
from src.services.pipeline.group_failures import group_drops
from src.services.pipeline.phases import run_phase_enrichment, run_phase_synthesis
from src.services.pipeline.phases.enrichment import cancel_quiz_task
from src.services.pipeline.phases.extraction import extraction_record
from src.services.pipeline.phases.frames import hires_upgrade_pending
from src.services.pipeline.phases.group_emission import assembled_tab_events
from src.services.pipeline.phases.late_quiz import carry_moment_props
from src.services.pipeline.phases.metadata import await_description_analysis
from src.services.pipeline.phases.moments import video_meta
from src.services.pipeline.phases.qdrant_index import index_in_qdrant
from src.services.pipeline.phases.synthesis import (
    apply_synthesis_to_assembled,
    needs_hero_fallback,
    seed_synthesis_dict,
    settle_tab_references,
)
from src.services.pipeline.pipeline_helpers import (
    normalize_segments,
    run_parallel_phases,
    spawn_background,
    sse_event,
)
from src.services.pipeline.plan_reconcile import PLAN_PARSE
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.pipeline.quote_verify import run_quote_index
from src.services.pipeline.reconcile_pass import ADDED_ACTIONS
from src.services.pipeline.tab_ledger import tab_id
from src.services.status_callback import send_video_status_background
from src.services.video.description_analyzer import DescriptionAnalysis

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

# The description analysis started at metadata-end and is capped at 30 s of
# its own, so by assembly it has nearly always landed. Tabs never wait longer
# than this for it: past the cap, meta goes out without descriptionAnalysis.
DESCRIPTION_WAIT_SECONDS = 5.0


async def _description_analysis(ctx: PipelineContext) -> dict[str, Any] | None:
    """The description analysis as meta carries it; ``None`` when empty or late."""
    try:
        analysis = await asyncio.wait_for(
            await_description_analysis(ctx), timeout=DESCRIPTION_WAIT_SECONDS
        )
    except TimeoutError:
        logger.warning("Description analysis still running at assembly — meta goes without it")
        return None
    if isinstance(analysis, DescriptionAnalysis) and analysis.has_content:
        return analysis.to_dict()
    return None


def _assemble(
    ctx: PipelineContext, description: dict[str, Any] | None, synthesis: dict[str, Any]
) -> dict:
    """``assemble_response`` over the run (with whatever quiz it has so far).

    ``synthesis`` is memory's hero (seeded) unless synthesis already ran; the
    late synthesis patches meta + the overview in place. Leaves the result on
    ``ctx.assembled_tabs`` / ``ctx.assembled_meta`` (+ coverage).
    """
    assembled = assemble_response(
        triage=ctx.triage_dict,
        extraction=ctx.extraction_data,
        enrichment=ctx.enrichment_data,
        synthesis=synthesis or None,
        video_meta=video_meta(ctx),
        description_analysis=description,
        frames=ctx.scene_frames_for_assembly,
        gallery_frames=ctx.scene_frames_gallery,
        all_frames=ctx.scene_frames_all,
        frame_descriptions=ctx.frame_descriptions or None,
        quote_index=run_quote_index(ctx),
    )
    ctx.assembled_tabs = assembled.get("tabs", [])
    ctx.assembled_meta = assembled.get("meta", {})
    # The tab that takes the plan-done provisional's place under another id
    # carries ``replaces`` on its first emission.
    ctx.tab_ledger.track_response(ctx.assembled_tabs)
    _flag_coverage(ctx)
    return assembled


def _flag_coverage(ctx: PipelineContext) -> None:
    """Coverage metric on meta, plus ``degraded`` for a partial result (FE retry affordance)."""
    coverage = getattr(ctx, "extraction_coverage", None)
    if not coverage or ctx.assembled_meta is None:
        return
    ctx.assembled_meta["extractionCoverage"] = coverage
    if coverage_is_degraded(coverage):
        ctx.assembled_meta["degraded"] = True


def _moment_positions(tabs: list[dict]) -> list[int]:
    return [i for i, t in enumerate(tabs) if t.get("component") == "moment_track"]


async def run_phase_moment_fill(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Frame every still-frameless moment at its own timestamp, then stream the moment tabs.

    moment_track tabs are held back until then so they stream WITH their
    guaranteed images. Best-effort: ``fill_moment_frames`` never raises.
    """
    tabs = ctx.assembled_tabs or []
    positions = _moment_positions(tabs)
    if positions:
        await fill_moment_frames(tabs, ctx.youtube_id, getattr(ctx, "hires_video", None))
    for position in positions:
        yield ctx.tab_ledger.event(tabs[position], position)


def _tabs_designed(ctx: PipelineContext, plan_dropped: list[dict]) -> int:
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


def _drop_accounting(ctx: PipelineContext, assembled: dict) -> dict:
    """``pipeline.assembly`` counts: designed, assembled, dropped (+ the drop list)."""
    assert ctx.triage is not None
    # Drops = the plan's registry check (unregistered dataSource, no sibling)
    # + the assembler's per-tab accounting — assembly also ADDS tabs
    # (overview, backfill, filmstrip, fallbacks), so the old
    # designed-minus-assembled subtraction masked drops and could go negative.
    # A tab a failed extraction group cost says so (3.8).
    plan_dropped = ctx.plan_result.dropped_tabs if ctx.plan_result else []
    dropped_tabs = [*plan_dropped, *group_drops(ctx, assembled.get("dropped", []))]
    plan_fallback = bool(ctx.plan_result and ctx.plan_result.plan_fallback)
    accounting = {
        "tabsDesigned": _tabs_designed(ctx, plan_dropped),
        "tabsAssembled": len(assembled.get("tabs", [])),
        # Evidence-skipped requirements are listed but were never tabs (1d.7).
        "tabsDropped": count_dropped_tabs(dropped_tabs),
        "droppedTabs": dropped_tabs,
        **({"planFallback": True} if plan_fallback else {}),
    }
    logger.info(
        "pipeline.assembly",
        extra={
            "video_id": ctx.video_summary_id,
            "tabs_designed": accounting["tabsDesigned"],
            "tabs_assembled": accounting["tabsAssembled"],
            "tabs_dropped": accounting["tabsDropped"],
            "dropped_detail": dropped_tabs,
            "components_used": [t["component"] for t in assembled.get("tabs", [])],
        },
    )
    return accounting


# ─── Tabs ───


def _first_tab_events(ctx: PipelineContext) -> list[str]:
    """Every tab but the moment tabs (they stream once framed), at its ``position``.

    ``position`` is the tab's index in the persisted order, so the client slots
    a late tab where the DB doc will have it. The payload is a copy — the
    persisted tab dict never gains ``position`` or ``final``.
    """
    tabs = ctx.assembled_tabs or []
    held_back = set(_moment_positions(tabs))
    return [
        ctx.tab_ledger.event(tab, position)
        for position, tab in enumerate(tabs)
        if position not in held_back
    ]


def _late_phases(ctx: PipelineContext, synthesized: bool) -> list[PhaseFn]:
    """Moment fill, synthesis unless it already ran, and the quiz when the plan can use one."""
    phases: list[PhaseFn] = [run_phase_moment_fill]
    if not synthesized:
        phases.append(run_phase_synthesis)
    if needs_quiz(ctx.plan_result, ctx.content_format):
        phases.append(run_phase_enrichment)
    return phases


def _start_late_group(ctx: PipelineContext, synthesized: bool) -> list[PhaseFn]:
    """The late phases, declared to the ledger; a started quiz no late phase awaits stops."""
    late = _late_phases(ctx, synthesized)
    if run_phase_enrichment not in late:
        cancel_quiz_task(ctx)  # e.g. a strip-only quiz whose host frames-done dropped
    ctx.tab_ledger.expect_late_changes(
        quiz=run_phase_enrichment in late, synthesis=run_phase_synthesis in late
    )
    return late


def _add_late_quiz(
    ctx: PipelineContext,
    description: dict[str, Any] | None,
    synthesis_input: dict[str, Any],
    synthesized_first: bool,
) -> dict:
    """Re-assemble with the quiz; the final re-emit sends what it changed."""
    before = ctx.assembled_tabs or []
    assembled = _assemble(ctx, description, synthesis_input)
    carry_moment_props(before, ctx.assembled_tabs or [])
    if not synthesized_first:
        apply_synthesis_to_assembled(ctx)
    return assembled


async def run_phase_assembly(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Emit the tabs, run the late group, then save, cache and finish (module docstring)."""
    assert ctx.video_data is not None
    assert ctx.triage is not None
    split = ctx.tab_ledger.groups_pending  # the split extraction streamed previews
    ctx.tab_ledger.groups_landed()
    description = await _description_analysis(ctx)
    if description is not None:
        yield sse_event("description_analysis", description)

    # Memory left the hero empty: synthesis writes it, and the overview + the
    # minimum-3 Key Info fallback need it at assembly time.
    synthesized_first = needs_hero_fallback(ctx.memory)
    if synthesized_first:
        async for event in run_parallel_phases([run_phase_synthesis], ctx):
            yield event
    else:
        seed_synthesis_dict(ctx)
    synthesis_input = dict(ctx.synthesis_dict or {})
    assembled = _assemble(ctx, description, synthesis_input)
    late = _start_late_group(ctx, synthesized_first)
    for event in assembled_tab_events(ctx) if split else _first_tab_events(ctx):
        yield event

    async for event in run_parallel_phases(late, ctx):
        yield event
    if ctx.enrichment_data:
        assembled = _add_late_quiz(ctx, description, synthesis_input, synthesized_first)
    # The Moments are framed and the hero written: nothing names a tab that does not ship.
    if (corrected_hero := settle_tab_references(ctx, assembled)) is not None:
        yield corrected_hero
    for event in ctx.tab_ledger.closing_events(ctx.assembled_tabs or []):
        yield event
    # Moment fill was the 720p file's last reader — free the disk now, unless
    # the deferred hi-res pass still reads it (the runner closes it after).
    hires_video = getattr(ctx, "hires_video", None)
    if hires_video is not None and not hires_upgrade_pending(ctx):
        await hires_video.close()

    async for event in _finish(ctx, assembled):
        yield event


# ─── Save, shared artifacts, done ───


def _result_document(
    ctx: PipelineContext, accounting: dict, processing_time: int, degraded: bool
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
        "pipelineVersion": settings.PIPELINE_VERSION,
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


def _complete_event(ctx: PipelineContext, processing_time: int, degraded: bool) -> str:
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


async def _finish(ctx: PipelineContext, assembled: dict) -> AsyncGenerator[str, None]:
    """``complete``, the save, the shared artifacts, then ``done`` (English only)."""
    degraded = coverage_is_degraded(getattr(ctx, "extraction_coverage", None))
    accounting = _drop_accounting(ctx, assembled)
    processing_time = int(ctx.timer.elapsed() * 1000)
    yield _complete_event(ctx, processing_time, degraded)

    result = _result_document(ctx, accounting, processing_time, degraded)
    saved = await asyncio.to_thread(
        ctx.repository.save_structured_result, ctx.video_summary_id, result
    )
    if not saved:
        # A global purge deleted the row mid-run; re-creating its Redis, Qdrant
        # and S3 artifacts would orphan them again. Drop the result.
        ctx.row_deleted = True
        logger.warning("pipeline_row_deleted_mid_run video_summary_id=%s", ctx.video_summary_id)
        return
    # Mirror onto userVideos + WS fan-out (best-effort). Non-English videos
    # stay "processing" here — the translation phase owns their "completed".
    # Fire-and-forget: a slow API gateway must not stall the SSE stream.
    send_video_status_background(ctx.video_summary_id, None, result["status"])
    await _write_shared_artifacts(ctx, result)
    _store_transcript_in_s3(ctx)

    # English-source videos are final here. Non-English videos still have the
    # translation phase ahead of them, which owns the terminal event (emitted by
    # the pipeline runner after translation persists the sourceLanguage block)
    # so the FE refetch on `done` sees a "completed" doc with the toggle.
    if not ctx.source_language_code:
        yield sse_event(
            "done",
            {
                "videoSummaryId": ctx.video_summary_id,
                "processingTimeMs": processing_time,
                "degraded": degraded,
            },
        )
        yield "data: [DONE]\n\n"


async def _write_shared_artifacts(ctx: PipelineContext, result: dict) -> None:
    """The Redis response copy and the Qdrant points — what every user is served.

    An eval-user run (D25) is a private version: neither is written, so the
    shared copy and the collection keep serving the users' own result.
    """
    if getattr(ctx, "eval_run", False):
        logger.info("Eval run %s: no Redis response cache, no Qdrant", ctx.video_summary_id)
        return
    await _cache_response(ctx, result)
    if settings.QDRANT_ENABLED:
        await index_in_qdrant(ctx)


async def _cache_response(ctx: PipelineContext, result: dict) -> None:
    """English videos only (non-blocking, best-effort).

    Non-English: the translation phase runs next, builds the source-language
    artifact and writes the final payload (English primary + sourceLanguage).
    Caching here would freeze a sourceLanguage-less payload into Redis for the
    whole TTL and hide the FE language toggle on every cache hit.
    """
    if not settings.REDIS_ENABLED or ctx.source_language_code:
        return
    from src.routes.cached_response import build_frontend_response

    frontend_response = build_frontend_response(result)
    try:
        await response_cache.set_response(ctx.youtube_id, frontend_response)
    except (OSError, ConnectionError) as e:
        logger.debug("Redis cache failed (non-critical): %s", e)


def _store_transcript_in_s3(ctx: PipelineContext) -> None:
    """Raw transcript to S3 (background, best-effort) + ``rawTranscriptRef`` on the row.

    Skipped for an S3-hit run: re-storing the blob it just read with
    source="s3" would decay the recorded origin after one regen. A yt-dlp
    transcript records its caption track, so a later run whose picker chooses
    another track sees the blob as stale; the store replaces the old blob.
    """
    transcript = ctx.transcript_data
    if not S3Client.is_available() or not transcript or transcript.source == "s3":
        return

    async def _store() -> None:
        try:
            from src.services.transcription.transcript_store import caption_ref, transcript_store

            video_data = ctx.video_data
            caption = None
            if transcript.source == "ytdlp":
                caption = caption_ref(
                    getattr(video_data, "caption_lang", None),
                    getattr(video_data, "caption_track", None),
                )
            s3_key = await transcript_store.store(
                youtube_id=ctx.youtube_id,
                segments=normalize_segments(transcript.segments),
                source=transcript.source,
                language=ctx.source_language_code or transcript.language,
                caption=caption,
            )
            await asyncio.to_thread(
                ctx.repository._collection.update_one,
                {"_id": ctx.video_summary_id},
                {"$set": {"rawTranscriptRef": s3_key}},
            )
            logger.info("Stored transcript in S3: %s", s3_key)
        except Exception as e:
            logger.warning("Transcript S3 storage failed (non-critical): %s", e)

    spawn_background(_store(), name=f"store_transcript_s3_{ctx.youtube_id}")
