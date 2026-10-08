"""Assembly runs synthesis after the tabs, in parallel with the moment fill (1d.3).

Order on the wire: every eager tab → synthesis ∥ moment fill (the overview
re-sent with synthesis's text, the moment tab once framed) → ``complete`` →
save. Assembly itself reads memory's hero (the seeded synthesis dict); the
saved doc carries synthesis's masterSummary because the save comes last.
The synthesis LLM call (``synthesize``) and the frame fill are patched; the
synthesis phase and the parallel runner run as production code.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.services.pipeline.phases import assembly as phase
from src.services.pipeline.phases import synthesis as synthesis_phase
from tests.test_phase_assembly_cache import _build_ctx

_MEMORY = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
_SYNTHESIS = SynthesisResult(master_summary="The full summary.", seo_description="SEO.")


def _tabs() -> list[dict]:
    return [
        {"id": "overview", "component": "overview", "props": {"data": {}}},
        {"id": "moments", "component": "moment_track", "props": {"items": []}},
        {"id": "facts", "component": "info_grid", "props": {"items": []}},
    ]


def _events(chunks: list[str]) -> list[tuple[str, dict]]:
    parsed = []
    for chunk in chunks:
        body = chunk.strip().removeprefix("data: ")
        if body != "[DONE]":
            payload = json.loads(body)
            parsed.append((payload.pop("event"), payload))
    return parsed


def _ctx() -> Any:
    ctx = _build_ctx()
    ctx.memory = _MEMORY
    ctx.video_memory = "<video_memory></video_memory>"
    ctx.synthesis_dict = {}
    ctx.triage.primary_tag = "food"
    ctx.llm_service = MagicMock()
    return ctx


async def _run(
    ctx: Any, fill: Any = None, synthesize: Any = None
) -> tuple[list[tuple[str, dict]], MagicMock]:
    assemble = MagicMock(side_effect=lambda **_kw: {"tabs": _tabs(), "meta": {}})
    with (
        patch.object(phase, "send_video_status_background", MagicMock()),
        patch.object(phase, "assemble_response", assemble),
        patch.object(phase, "fill_moment_frames", fill or AsyncMock(return_value=0)),
        patch.object(
            synthesis_phase, "synthesize", synthesize or AsyncMock(return_value=_SYNTHESIS)
        ),
        patch.object(phase.settings, "REDIS_ENABLED", False),
        patch.object(phase.settings, "QDRANT_ENABLED", False),
    ):
        chunks = [chunk async for chunk in phase.run_phase_assembly(ctx)]
    return _events(chunks), assemble


async def test_should_assemble_with_memorys_hero_before_synthesis_runs() -> None:
    _events_, assemble = await _run(_ctx())

    assert assemble.call_args.kwargs["synthesis"]["tldr"] == "Memory tldr."


async def test_should_emit_the_eager_tabs_before_synthesis_completes() -> None:
    events, _ = await _run(_ctx())

    names = [(e, d.get("id")) for e, d in events]
    first_synthesis = names.index(("synthesis_complete", None))
    assert names.index(("tab_ready", "facts")) < first_synthesis


async def test_should_re_send_the_overview_with_synthesis_text() -> None:
    events, _ = await _run(_ctx())

    overviews = [d for e, d in events if e == "tab_ready" and d["id"] == "overview"]
    assert overviews[-1]["props"]["data"]["masterSummary"] == "The full summary."


async def test_should_complete_after_synthesis_and_moment_fill() -> None:
    events, _ = await _run(_ctx())

    names = [e for e, _ in events]
    assert names.index("complete") > max(
        names.index("synthesis_complete"),
        [i for i, (e, d) in enumerate(events) if d.get("id") == "moments"][0],
    )


async def test_should_save_the_synthesis_patched_meta() -> None:
    ctx = _ctx()

    await _run(ctx)

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert (saved["meta"]["masterSummary"], saved["meta"]["tldr"]) == (
        "The full summary.",
        "Memory tldr.",
    )


async def test_should_run_synthesis_while_the_moment_fill_runs() -> None:
    order: list[str] = []

    async def slow_fill(_tabs: list, _yt: str, _hires: Any) -> int:
        await asyncio.sleep(0.05)
        order.append("fill-end")
        return 0

    async def synthesize(*_a: object, **_k: object) -> SynthesisResult:
        order.append("synthesis")
        return _SYNTHESIS

    await _run(_ctx(), fill=slow_fill, synthesize=synthesize)

    assert order == ["synthesis", "fill-end"]
