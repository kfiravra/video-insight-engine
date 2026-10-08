"""Synthesis after assembly, in parallel with the moment fill (pipeline-1min 1d.3).

The caller seeds ``ctx.synthesis_dict`` with memory's hero, assembles and emits
the tabs, then runs the synthesis phase: it patches the assembled meta and the
overview in place and re-sends the overview. The patched output must equal what
assembling with the final synthesis dict gives — core.py stays untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.domain_types import validate_domain_output
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult, SynthesisResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.plan import _validate_tabs
from src.services.pipeline.synthesis import build_synthesis_dict

_FOOD_FIXTURE = Path(__file__).parent / "fixtures" / "llm_responses" / "food.json"
_MEMORY = MemoryResult(tldr="Memory tldr.", takeaways=["m1", "m2", "m3"])
_SYNTHESIS = SynthesisResult(master_summary="The full summary.", seo_description="SEO.")


def _ctx(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "video_summary_id": "vsid",
        "video_data": SimpleNamespace(title="Shakshuka", channel="Chef", duration=600),
        "triage": SimpleNamespace(
            primary_tag="food",
            tabs=[
                {"id": "overview", "label": "Overview", "component": "overview"},
                {"id": "steps", "label": "Steps"},
                {"id": "quiz", "label": "Quiz"},
            ],
        ),
        "extraction_data": {"food": {"steps": [{"text": "bloom the spices"}]}},
        "llm_service": MagicMock(),
        "video_memory": "<video_memory></video_memory>",
        "synthesis_dict": {},
        "memory": _MEMORY,
        "assembled_tabs": None,
        "assembled_meta": None,
    }
    return SimpleNamespace(**{**fields, **overrides})


def _assemble(synthesis: dict[str, Any]) -> dict[str, Any]:
    """The food fixture through validation + assembly with ``synthesis`` as the dict."""
    fixture = json.loads(_FOOD_FIXTURE.read_text())
    plan = PlanResult.model_validate(fixture["plan_response"])
    plan.tabs = _validate_tabs(plan.tabs)
    extraction = validate_domain_output(["food"], plan.modifiers, fixture["extraction_response"])
    return assemble_response(
        triage=plan.to_triage_dict(),
        extraction=extraction,
        enrichment=None,
        synthesis=synthesis or None,
        video_meta=fixture["video_meta"],
    )


async def _run(ctx: Any, result: SynthesisResult | Exception) -> list[tuple[str, dict]]:
    synthesize = AsyncMock(side_effect=[result])
    with patch.object(synthesis_phase, "synthesize", synthesize):
        chunks = [c async for c in synthesis_phase.run_phase_synthesis(ctx)]
    events = [json.loads(c.removeprefix("data: ")) for c in chunks]
    return [(e.pop("event"), e) for e in events]


def test_tab_labels_should_list_assembled_tabs_without_the_overview_once_assembly_ran() -> None:
    assembled = [
        {"id": "overview", "label": "Overview", "component": "overview"},
        {"id": "steps", "label": "Steps"},
        {"id": "steps-2", "label": "Steps"},
        {"id": "moments", "label": "Key Moments"},
    ]

    assert synthesis_phase.tab_labels(_ctx(assembled_tabs=assembled)) == ["Steps", "Key Moments"]


def test_tab_labels_should_list_the_plan_tabs_before_assembly() -> None:
    assert synthesis_phase.tab_labels(_ctx()) == ["Steps", "Quiz"]


def test_seed_should_put_memorys_hero_into_the_synthesis_dict() -> None:
    ctx = _ctx()

    synthesis_phase.seed_synthesis_dict(ctx)

    assert ctx.synthesis_dict == {
        "tldr": "Memory tldr.",
        "keyTakeaways": ["m1", "m2", "m3"],
        "masterSummary": "",
        "seoDescription": "",
    }


def test_apply_should_do_nothing_before_assembly() -> None:
    ctx = _ctx(synthesis_dict=build_synthesis_dict(_MEMORY, _SYNTHESIS))

    assert synthesis_phase.apply_synthesis_to_assembled(ctx) is None


async def test_phase_after_assembly_should_patch_and_re_send_the_overview() -> None:
    synthesis_phase.seed_synthesis_dict(ctx := _ctx())
    assembled = _assemble(ctx.synthesis_dict)
    ctx.assembled_tabs, ctx.assembled_meta = assembled["tabs"], assembled["meta"]

    events = await _run(ctx, _SYNTHESIS)

    assert [name for name, _ in events] == ["synthesis_complete", "tab_ready"]
    overview = events[1][1]
    assert (overview["id"], overview["position"]) == ("overview", 0)
    assert overview["props"]["data"]["masterSummary"] == "The full summary."
    assert ctx.assembled_meta["masterSummary"] == "The full summary."


async def test_patched_output_should_equal_assembling_with_the_final_synthesis() -> None:
    final = build_synthesis_dict(_MEMORY, _SYNTHESIS)
    expected = _assemble(final)
    synthesis_phase.seed_synthesis_dict(ctx := _ctx())
    seeded = _assemble(ctx.synthesis_dict)
    ctx.assembled_tabs, ctx.assembled_meta = seeded["tabs"], seeded["meta"]

    await _run(ctx, _SYNTHESIS)

    assert ctx.assembled_meta == expected["meta"]
    assert ctx.assembled_tabs[0] == expected["tabs"][0]


async def test_failed_synthesis_after_assembly_should_keep_memorys_hero_and_re_send() -> None:
    synthesis_phase.seed_synthesis_dict(ctx := _ctx())
    seeded = _assemble(ctx.synthesis_dict)
    ctx.assembled_tabs, ctx.assembled_meta = seeded["tabs"], seeded["meta"]

    events = await _run(ctx, ValueError("LLM down"))

    assert events[0][1]["tldr"] == "Memory tldr."
    assert ctx.assembled_meta["tldr"] == "Memory tldr."
