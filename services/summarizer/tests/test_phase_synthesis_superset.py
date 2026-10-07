"""The synthesis-done ``synthesis_complete`` is the full superset (pipeline-1min 1b.5).

The memory-done event carried ``{tldr, keyTakeaways}``; synthesis re-emits all
four fields. Synthesis's own values win; memory fills a field synthesis left
empty (a failed call included), and the stored ``ctx.synthesis_dict`` — what
assembly writes into ``meta`` — matches the event.
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
    key_takeaways=["s1", "s2"],
    master_summary="The full summary.",
    seo_description="SEO.",
)


def _ctx(memory: MemoryResult | None = _MEMORY, synthesis_dict: dict | None = None) -> Any:
    return SimpleNamespace(
        video_summary_id="vsid",
        video_data=SimpleNamespace(title="Lasagna", channel="Chef", duration=600),
        triage=SimpleNamespace(primary_tag="food"),
        extraction_data={"food": {"steps": [{"text": "boil"}]}},
        chapters=None,
        llm_service=MagicMock(),
        video_memory="<video_memory></video_memory>",
        synthesis_dict=synthesis_dict or {},
        memory=memory,
    )


async def _run(ctx: Any, synthesize: AsyncMock) -> dict[str, Any]:
    with patch.object(synthesis_phase, "synthesize", synthesize):
        chunks = [c async for c in synthesis_phase.run_phase_synthesis(ctx)]
    (event,) = [json.loads(c.removeprefix("data: ")) for c in chunks]
    return event


async def test_should_emit_all_four_fields_with_synthesis_values() -> None:
    event = await _run(_ctx(), AsyncMock(return_value=_SYNTHESIS))

    assert event == {
        "event": "synthesis_complete",
        "tldr": "Synthesis tldr.",
        "keyTakeaways": ["s1", "s2"],
        "masterSummary": "The full summary.",
        "seoDescription": "SEO.",
    }


async def test_should_keep_memorys_hero_when_synthesis_failed() -> None:
    event = await _run(_ctx(), AsyncMock(side_effect=ValueError("LLM down")))

    assert (event["tldr"], event["keyTakeaways"], event["masterSummary"]) == (
        "Memory tldr.",
        ["m1", "m2", "m3"],
        "",
    )


async def test_should_store_the_filled_dict_for_assembly_meta() -> None:
    ctx = _ctx()

    await _run(ctx, AsyncMock(side_effect=ValueError("LLM down")))

    assert ctx.synthesis_dict == {"tldr": "Memory tldr.", "keyTakeaways": ["m1", "m2", "m3"]}


async def test_should_emit_an_all_empty_superset_without_synthesis_or_memory() -> None:
    event = await _run(_ctx(memory=None), AsyncMock(side_effect=ValueError("LLM down")))

    assert (event["tldr"], event["keyTakeaways"], event["seoDescription"]) == ("", [], "")


async def test_should_re_emit_a_synthesis_the_extraction_retry_already_made() -> None:
    synthesize = AsyncMock()
    stored = _SYNTHESIS.model_dump(by_alias=True)

    event = await _run(_ctx(synthesis_dict=stored), synthesize)

    assert (synthesize.await_count, event["masterSummary"]) == (0, "The full summary.")
