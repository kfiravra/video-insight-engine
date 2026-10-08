"""Phase 5: Synthesis — masterSummary + seoDescription (pipeline-1min 1d.3).

Emits the run's second ``synthesis_complete``: the full four-field superset of
the memory-done one (``phases/memory.py``). tldr / keyTakeaways are memory's;
synthesis writes them only when memory left one empty, so the stored meta keeps
what the hero already showed.

Two orders work:
- before assembly (today's): ``ctx.synthesis_dict`` is complete when
  ``assemble_response`` reads it;
- after assembly, in parallel with the moment fill: the caller seeds
  ``ctx.synthesis_dict`` with ``seed_synthesis_dict`` before assembling (meta +
  overview carry memory's hero), emits the tabs, then runs this phase — it
  patches ``ctx.assembled_meta`` and the overview tab in place and re-sends the
  overview ``tab_ready``. The caller saves only after this phase finished.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from llm_common.context import llm_feature_var

from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.synthesis import (
    SYNTHESIS_KEYS,
    build_synthesis_dict,
    compact_extraction,
    synthesize,
)

if TYPE_CHECKING:
    from src.models.memory_types import MemoryResult
    from src.models.pipeline_types import SynthesisResult
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

_OVERVIEW = "overview"


# ─── Event ───


def synthesis_event(synthesis: dict[str, Any]) -> dict[str, object]:
    """The synthesis-done ``synthesis_complete`` payload: all four fields (the superset)."""
    payload: dict[str, object] = {key: synthesis.get(key) or "" for key in SYNTHESIS_KEYS}
    payload["keyTakeaways"] = list(synthesis.get("keyTakeaways") or [])
    return payload


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


# ─── Late patch (synthesis after assembly) ───


def seed_synthesis_dict(ctx: PipelineContext) -> None:
    """Memory's hero into ``ctx.synthesis_dict`` before assembly reads it."""
    ctx.synthesis_dict = build_synthesis_dict(ctx.memory, None)


def _patch_meta(meta: dict[str, Any], synthesis: dict[str, Any]) -> None:
    """The meta fields ``assemble_response`` writes from a synthesis dict."""
    for key in SYNTHESIS_KEYS:
        meta[key] = synthesis.get(key, [] if key == "keyTakeaways" else "")


def _patch_overview(data: dict[str, Any], synthesis: dict[str, Any]) -> None:
    """The overview fields ``assemble_overview`` takes from a synthesis dict."""
    if synthesis.get("tldr"):
        data["subtitle"] = synthesis["tldr"]
        data["tldr"] = synthesis["tldr"]
    if synthesis.get("masterSummary"):
        data["masterSummary"] = synthesis["masterSummary"]
    if synthesis.get("keyTakeaways"):
        data["keyTakeaways"] = synthesis["keyTakeaways"]


def apply_synthesis_to_assembled(ctx: PipelineContext) -> int | None:
    """Patch the assembled meta + overview in place; the overview's position, if patched.

    No-op (``None``) before assembly has run — the before-assembly order.
    """
    if ctx.assembled_meta is None or ctx.assembled_tabs is None or not ctx.synthesis_dict:
        return None
    _patch_meta(ctx.assembled_meta, ctx.synthesis_dict)
    for position, tab in enumerate(ctx.assembled_tabs):
        props = tab.get("props")
        data = props.get("data") if isinstance(props, dict) else None
        if tab.get("component") == _OVERVIEW and isinstance(data, dict):
            _patch_overview(data, ctx.synthesis_dict)
            return position
    return None


# ─── Phase ───


def _source(from_memory: bool, value: object) -> str:
    if not value:
        return "none"
    return "memory" if from_memory else "synthesis"


def _log_synthesis(ctx: PipelineContext) -> None:
    memory = ctx.memory
    synthesis = ctx.synthesis_dict
    logger.info(
        "pipeline.synthesis",
        extra={
            "video_id": ctx.video_summary_id,
            "has_tldr": bool(synthesis.get("tldr")),
            "takeaway_count": len(synthesis.get("keyTakeaways", [])),
            "has_master_summary": bool(synthesis.get("masterSummary")),
            "tldr_source": _source(bool(memory and memory.tldr), synthesis.get("tldr")),
            "takeaways_source": _source(
                bool(memory and memory.takeaways), synthesis.get("keyTakeaways")
            ),
        },
    )


async def run_phase_synthesis(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Write masterSummary + seo; emit the full ``synthesis_complete`` (+ overview re-send)."""
    llm_feature_var.set("summarize:synthesis")
    result = await _synthesize(ctx)
    ctx.synthesis_dict = build_synthesis_dict(ctx.memory, result)
    overview_position = apply_synthesis_to_assembled(ctx)
    yield sse_event("synthesis_complete", synthesis_event(ctx.synthesis_dict))
    if overview_position is not None and ctx.assembled_tabs is not None:
        overview = ctx.assembled_tabs[overview_position]
        yield sse_event("tab_ready", {**overview, "position": overview_position})
    _log_synthesis(ctx)
