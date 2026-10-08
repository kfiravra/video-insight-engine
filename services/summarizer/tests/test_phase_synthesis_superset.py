"""The synthesis-done ``synthesis_complete`` is the full superset (pipeline-1min 1b.5 + 1d.3).

The memory-done event carried ``{tldr, keyTakeaways}``; synthesis re-emits all
four fields. tldr/keyTakeaways stay memory's (the hero the viewer already saw);
synthesis writes them only when memory left one empty, and a failed call keeps
memory's. The stored ``ctx.synthesis_dict`` — what assembly writes into
``meta`` — matches the event.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.services.pipeline.phases import synthesis as synthesis_phase

_MEMORY = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
_SYNTHESIS = SynthesisResult(
    tldr="Synthesis tldr.",
    key_takeaways=["s1", "s2", "s3"],
    master_summary="The full summary.",
    seo_description="SEO.",
)


def _ctx(memory: MemoryResult | None = _MEMORY, synthesis_dict: dict | None = None) -> Any:
    return SimpleNamespace(
        video_summary_id="vsid",
        video_data=SimpleNamespace(title="Lasagna", channel="Chef", duration=600),
        triage=SimpleNamespace(primary_tag="food", tabs=[{"id": "steps", "label": "Steps"}]),
        extraction_data={"food": {"steps": [{"text": "boil"}]}},
        llm_service=MagicMock(),
        video_memory="<video_memory></video_memory>",
        synthesis_dict=synthesis_dict or {},
        memory=memory,
        assembled_tabs=None,
        assembled_meta=None,
    )


async def _run(ctx: Any, synthesize: AsyncMock) -> dict[str, Any]:
    with patch.object(synthesis_phase, "synthesize", synthesize):
        chunks = [c async for c in synthesis_phase.run_phase_synthesis(ctx)]
    (event,) = [json.loads(c.removeprefix("data: ")) for c in chunks]
    return event


async def test_should_emit_all_four_fields_with_memorys_hero_and_synthesis_text() -> None:
    event = await _run(_ctx(), AsyncMock(return_value=_SYNTHESIS))

    assert event == {
        "event": "synthesis_complete",
        "tldr": "Memory tldr.",
        "keyTakeaways": ["m1", "m2", "m3"],
        "masterSummary": "The full summary.",
        "seoDescription": "SEO.",
    }


async def test_should_use_the_synthesis_hero_when_memory_failed() -> None:
    event = await _run(_ctx(memory=None), AsyncMock(return_value=_SYNTHESIS))

    assert (event["tldr"], event["keyTakeaways"]) == ("Synthesis tldr.", ["s1", "s2", "s3"])


async def test_should_ask_synthesis_for_the_hero_only_when_memory_left_a_field_empty() -> None:
    asked: list[bool] = []
    for memory in (_MEMORY, MemoryResult(tldr="Memory tldr."), None):
        synthesize = AsyncMock(return_value=_SYNTHESIS)
        await _run(_ctx(memory=memory), synthesize)
        asked.append(synthesize.await_args.kwargs["hero_fallback"])

    assert asked == [False, True, True]


async def test_should_keep_memorys_hero_when_synthesis_failed() -> None:
    event = await _run(_ctx(), AsyncMock(side_effect=ValueError("LLM down")))

    assert (event["tldr"], event["keyTakeaways"], event["masterSummary"]) == (
        "Memory tldr.",
        ["m1", "m2", "m3"],
        "",
    )


async def test_should_store_the_event_values_for_assembly_meta() -> None:
    ctx = _ctx()

    event = await _run(ctx, AsyncMock(side_effect=ValueError("LLM down")))

    assert ctx.synthesis_dict == {k: v for k, v in event.items() if k != "event"}


async def test_should_emit_an_all_empty_superset_without_synthesis_or_memory() -> None:
    ctx = _ctx(memory=None)

    event = await _run(ctx, AsyncMock(side_effect=ValueError("LLM down")))

    assert (event["tldr"], event["keyTakeaways"], event["seoDescription"]) == ("", [], "")
    assert ctx.synthesis_dict == {}


async def test_should_call_synthesis_even_when_the_dict_was_seeded_from_memory() -> None:
    synthesize = AsyncMock(return_value=_SYNTHESIS)
    seeded = {"tldr": "Memory tldr.", "keyTakeaways": ["m1", "m2", "m3"]}

    event = await _run(_ctx(synthesis_dict=seeded), synthesize)

    assert (synthesize.await_count, event["masterSummary"]) == (1, "The full summary.")


async def test_should_send_the_compact_extraction_and_the_plan_tab_labels() -> None:
    synthesize = AsyncMock(return_value=_SYNTHESIS)

    await _run(_ctx(), synthesize)

    kwargs = synthesize.await_args.kwargs
    assert (kwargs["extraction_summary"], kwargs["tab_labels"]) == (
        '{"food":{"steps":[{"text":"boil"}]}}',
        ["Steps"],
    )
