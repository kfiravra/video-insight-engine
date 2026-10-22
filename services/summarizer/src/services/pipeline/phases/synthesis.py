"""Phase 5: Synthesis — the hero rewrite, else masterSummary + seoDescription (pipeline-1min 1d.3).

The hero (output-quality 2.2, D1): with ``HERO_REWRITE_ENABLED`` the ``hero_rewrite``
call takes the synthesis call's place. The split path starts it at text-groups-done
(``start_hero_task``, from ``phases/group_emission``) and this phase awaits it; on
today's path (``EXTRACTION_PARALLEL=false``) this phase makes the call over the whole
extraction. Its meta (``hero_rewrite.hero_meta``: thesis, brief — also
``masterSummary`` —, takeaways closing with their ``[m:ss]``, worth-it-for, skip-if,
SEO) replaces memory's hero. No hero — the flag off, or the call gave none — runs
today's synthesis exactly as before.

Emits the run's second ``synthesis_complete``: the superset of the memory-done one
(``phases/memory.py``) — the four synthesis fields, plus ``brief`` / ``worthItFor`` /
``skipIf`` when the hero wrote them. On the synthesis path tldr / keyTakeaways are
memory's; synthesis writes them only when memory left one empty, so the stored meta
keeps what the hero already showed.

Two orders work:
- before assembly: ``ctx.synthesis_dict`` is complete when ``assemble_response``
  reads it (``carry_hero_keys`` adds the hero's own keys to the meta it builds);
- after assembly, in parallel with the moment fill: the caller seeds
  ``ctx.synthesis_dict`` with ``seed_synthesis_dict`` before assembling (meta +
  overview carry memory's hero), emits the tabs, then runs this phase — it
  patches ``ctx.assembled_meta`` and the overview tab in place. The overview
  went out non-final, so assembly's final re-emit sends the patched one; the
  caller saves only after this phase finished.

After the late group ``settle_tab_references`` counts the hero page's
``readingMinutes`` over the final tabs; the corrected superset carries it — the
run's third ``synthesis_complete``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.services.pipeline.assembly.tab_references import (
    planned_tab_sources,
    settle_shipped_tabs,
)
from src.services.pipeline.hero_rewrite import (
    READING_MINUTES_KEY,
    hero_meta,
    hero_rewrite_for_run,
    reading_minutes,
)
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import timed_step
from src.services.pipeline.synthesis import (
    HERO_KEYS,
    SYNTHESIS_KEYS,
    build_synthesis_dict,
    compact_extraction,
    synthesize,
)

if TYPE_CHECKING:
    from src.models.hero_types import HeroRewriteResult
    from src.models.memory_types import MemoryResult
    from src.models.pipeline_types import SynthesisResult
    from src.services.pipeline.context import PipelineContext
    from src.services.pipeline.extraction_groups import SplitRun

logger = logging.getLogger(__name__)

_OVERVIEW = "overview"
# pipeline.timing step of a hero call the split extraction started (``synthesis`` is the wait).
HERO_CALL_STEP = "hero_call"
# Every hero meta carries a brief; synthesis never writes one.
_HERO_MARK = "brief"


# ─── Event ───


def synthesis_event(synthesis: Mapping[str, Any]) -> dict[str, object]:
    """The ``synthesis_complete`` superset: the four synthesis fields, plus the hero's
    own keys it carries (never null — absent until set)."""
    payload: dict[str, object] = {key: synthesis.get(key) or "" for key in SYNTHESIS_KEYS}
    payload["keyTakeaways"] = list(synthesis.get("keyTakeaways") or [])
    payload.update({key: synthesis[key] for key in HERO_KEYS if synthesis.get(key) is not None})
    return payload


def wrote_hero(synthesis: Mapping[str, Any] | None) -> bool:
    """True when the hero rewrite wrote this synthesis dict (meta)."""
    return synthesis is not None and _HERO_MARK in synthesis


# ─── Input ───


def _is_overview(tab: dict) -> bool:
    return tab.get("id") == _OVERVIEW or tab.get("component") == _OVERVIEW


def tab_labels(ctx: PipelineContext) -> list[str]:
    """Labels of the tabs the viewer gets, overview excluded, first occurrence kept.

    The assembled tabs once assembly ran (dropped tabs gone, backfills in),
    else the plan's.
    """
    assert ctx.triage is not None
    tabs = ctx.assembled_tabs if ctx.assembled_tabs is not None else ctx.triage.tabs
    labels: list[str] = []
    for tab in tabs:
        label = tab.get("label")
        if _is_overview(tab) or not isinstance(label, str) or not label.strip():
            continue
        if label not in labels:
            labels.append(label)
    return labels


def needs_hero_fallback(memory: MemoryResult | None) -> bool:
    """True when memory left the tldr or the takeaways empty (synthesis writes them)."""
    return memory is None or not memory.tldr or not memory.takeaways


async def _synthesize(ctx: PipelineContext) -> SynthesisResult | None:
    """One synthesis call; ``None`` when it failed (non-critical)."""
    assert ctx.video_data is not None
    assert ctx.triage is not None
    try:
        return await synthesize(
            ctx.llm_service,
            title=ctx.video_data.title,
            channel=ctx.video_data.channel,
            duration=ctx.video_data.duration,
            output_type=ctx.triage.primary_tag,
            extraction_summary=compact_extraction(ctx.extraction_data),
            video_context=ctx.video_memory,
            tab_labels=tab_labels(ctx),
            hero_fallback=needs_hero_fallback(ctx.memory),
        )
    except Exception as e:
        logger.warning("[pipeline] Synthesis failed (non-critical): %s", e)
        return None


# ─── The hero rewrite (output-quality 2.2) ───


async def _timed_hero(
    ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun
) -> HeroRewriteResult | None:
    with timed_step(HERO_CALL_STEP):
        return await hero_rewrite_for_run(ctx, extraction, run)


def start_hero_task(ctx: PipelineContext, extraction: Mapping[str, Any], run: SplitRun) -> None:
    """Start the hero rewrite now (split path: every text group landed) over ``extraction``,
    reading ``run``'s cached blocks; the synthesis slot awaits it."""
    ctx.hero_task = asyncio.create_task(
        _timed_hero(ctx, extraction, run), name=f"hero_{ctx.video_summary_id}"
    )


def cancel_hero_task(ctx: PipelineContext) -> None:
    """Stop a started hero call nobody will await (the run failed or was cancelled)."""
    task = getattr(ctx, "hero_task", None)
    if task is not None and not task.done():
        task.cancel()


async def _hero(ctx: PipelineContext) -> HeroRewriteResult | None:
    """The hero when enabled: the started task, else one call over the whole extraction
    (nothing extracted → no call, as ``rewrite_hero`` would decide)."""
    if not settings.HERO_REWRITE_ENABLED:
        return None
    task = getattr(ctx, "hero_task", None)
    if task is not None:
        return await task
    if not ctx.extraction_data:
        return None
    return await hero_rewrite_for_run(ctx, ctx.extraction_data, None)


# ─── Late patch (synthesis after assembly) ───


def seed_synthesis_dict(ctx: PipelineContext) -> None:
    """Memory's hero into ``ctx.synthesis_dict`` before assembly reads it."""
    ctx.synthesis_dict = build_synthesis_dict(ctx.memory, None)


def carry_hero_keys(meta: dict[str, Any], synthesis: Mapping[str, Any]) -> None:
    """The hero's own keys into a meta ``assemble_response`` built (it writes only the
    four synthesis keys); nothing for a memory / synthesis dict."""
    meta.update({key: synthesis[key] for key in HERO_KEYS if key in synthesis})


def _patch_meta(meta: dict[str, Any], synthesis: dict[str, Any]) -> None:
    """The meta fields ``assemble_response`` writes from a synthesis dict (+ the hero's)."""
    for key in SYNTHESIS_KEYS:
        meta[key] = synthesis.get(key, [] if key == "keyTakeaways" else "")
    carry_hero_keys(meta, synthesis)


def _patch_overview(data: dict[str, Any], synthesis: dict[str, Any]) -> None:
    """The overview fields ``assemble_overview`` takes from a synthesis dict."""
    if synthesis.get("tldr"):
        data["subtitle"] = synthesis["tldr"]
        data["tldr"] = synthesis["tldr"]
    if synthesis.get("masterSummary"):
        data["masterSummary"] = synthesis["masterSummary"]
    if synthesis.get("keyTakeaways"):
        data["keyTakeaways"] = synthesis["keyTakeaways"]


def apply_synthesis_to_assembled(ctx: PipelineContext) -> None:
    """Patch the assembled meta + overview in place.

    No-op before assembly has run — the before-assembly order.
    """
    if ctx.assembled_meta is None or ctx.assembled_tabs is None or not ctx.synthesis_dict:
        return
    _patch_meta(ctx.assembled_meta, ctx.synthesis_dict)
    for tab in ctx.assembled_tabs:
        props = tab.get("props")
        data = props.get("data") if isinstance(props, dict) else None
        if tab.get("component") == _OVERVIEW and isinstance(data, dict):
            _patch_overview(data, ctx.synthesis_dict)
            return


# ─── The hero names only tabs that ship (output-quality 2.0 b) ───


def settle_tab_references(ctx: PipelineContext, assembled: dict[str, Any]) -> str | None:
    """After the late group: the final tab list and the hero agree.

    The moment fill framed the Moments and synthesis wrote the hero after
    assembly, so the filmstrip the framed Moments shadow drops here and every
    hero / goal sentence naming a tab that does not ship goes
    (``assembly.tab_references``); a planned tab dropped here joins
    ``assembled["dropped"]``. The tabs are final now, so a hero page gets its
    ``readingMinutes`` (code-counted, ``hero_rewrite.reading_minutes``). Returns a
    corrected ``synthesis_complete`` when the hero no longer reads as the last one
    sent: the superset marked ``final``, which replaces the client's and the relay's
    copy — empties and absent hero keys included.
    """
    meta, tabs = ctx.assembled_meta, ctx.assembled_tabs
    if meta is None or tabs is None:
        return None
    plan = [tab for tab in ctx.triage_dict.get("tabs") or [] if isinstance(tab, dict)]
    # A moment fill deferred past ``complete`` (output-quality 3.7): its frames count as framed.
    pending = getattr(ctx, "pending_moment_frames", None)
    drops = settle_shipped_tabs(meta, tabs, [*plan, *tabs], planned_tab_sources(plan), pending)
    assembled.setdefault("dropped", []).extend(drops)
    if wrote_hero(ctx.synthesis_dict):
        meta[READING_MINUTES_KEY] = reading_minutes(meta, tabs)
    hero = synthesis_event(meta)
    if not ctx.synthesis_dict or hero == synthesis_event(ctx.synthesis_dict):
        return None
    return sse_event("synthesis_complete", {**hero, "final": True})


# ─── Phase ───


def _source(memory_had_it: bool, value: object, writer: str) -> str:
    """Who wrote a hero field: memory (kept by synthesis), the writer, or none."""
    if not value:
        return "none"
    return "memory" if memory_had_it and writer == "synthesis" else writer


def _log_synthesis(ctx: PipelineContext, writer: str) -> None:
    memory = ctx.memory
    synthesis = ctx.synthesis_dict
    logger.info(
        "pipeline.synthesis",
        extra={
            "video_id": ctx.video_summary_id,
            "writer": writer,
            "has_tldr": bool(synthesis.get("tldr")),
            "takeaway_count": len(synthesis.get("keyTakeaways", [])),
            "has_master_summary": bool(synthesis.get("masterSummary")),
            "tldr_source": _source(bool(memory and memory.tldr), synthesis.get("tldr"), writer),
            "takeaways_source": _source(
                bool(memory and memory.takeaways), synthesis.get("keyTakeaways"), writer
            ),
        },
    )


async def _synthesis_dict(ctx: PipelineContext) -> str:
    """``ctx.synthesis_dict`` from the hero rewrite, else today's synthesis; the writer."""
    hero = await _hero(ctx)
    if hero is not None:
        ctx.synthesis_dict = hero_meta(hero)
        return "hero_rewrite"
    llm_feature_var.set("summarize:synthesis")
    result = await _synthesize(ctx)
    ctx.synthesis_dict = build_synthesis_dict(ctx.memory, result)
    return "synthesis"


async def run_phase_synthesis(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Write the hero (or masterSummary + seo); patch what assembly built; emit the superset."""
    writer = await _synthesis_dict(ctx)
    apply_synthesis_to_assembled(ctx)
    yield sse_event("synthesis_complete", synthesis_event(ctx.synthesis_dict))
    _log_synthesis(ctx, writer)
