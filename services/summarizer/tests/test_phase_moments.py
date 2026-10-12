"""The provisional Moments tab at plan-done (pipeline-1min 2.1, ``phases/moments.py``).

Streams right after the text branch (plan ∥ memory) from the fallback chain,
provisional and non-final, with the label assembly will give the real tab;
the description analysis counts only if it already landed (no wait).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

from src.models.memory_types import MemoryResult, OutlineSection
from src.services.pipeline.phases import moments
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.tab_ledger import TabLedger
from src.services.video.description_analyzer import DescriptionAnalysis, DescriptionTimestamp

_MEMORY = MemoryResult(
    outline=[
        OutlineSection(start=0, end=300, title="The stack"),
        OutlineSection(start=300, end=900, title="Agent workflow"),
    ]
)
_PLANNED = {"id": "moments", "label": "🎬 Demos", "emoji": "🎬", "component": "moment_track"}
_CODE = {"id": "code", "label": "Code", "component": "code_playground"}


def _ctx(tabs: list[dict], **overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "video_summary_id": "vsid",
        "video_data": SimpleNamespace(title="t", channel="c", duration=900, chapters=None),
        "triage": SimpleNamespace(primary_tag="tech", tabs=tabs),
        "content_format": None,
        "memory": _MEMORY,
        "description_analysis": None,
        "tab_ledger": TabLedger(),
    }
    return SimpleNamespace(**{**fields, **overrides})


def _payload(chunk: str | None) -> dict:
    assert chunk is not None
    return json.loads(chunk.removeprefix("data: "))


class TestProvisionalMomentsEvent:
    def test_should_stream_a_provisional_non_final_tab(self) -> None:
        payload = _payload(moments.provisional_moments_event(_ctx([_CODE, _PLANNED])))  # type: ignore[arg-type]

        assert (payload["event"], payload["provisional"], payload["final"]) == (
            "tab_ready",
            True,
            False,
        )

    def test_should_carry_the_planned_id_and_the_label_assembly_gives_it(self) -> None:
        payload = _payload(moments.provisional_moments_event(_ctx([_CODE, _PLANNED])))  # type: ignore[arg-type]

        assert (payload["id"], payload["label"], payload["position"]) == ("moments", "Demos", 2)

    def test_should_build_the_moments_from_the_memory_outline(self) -> None:
        payload = _payload(moments.provisional_moments_event(_ctx([_PLANNED])))  # type: ignore[arg-type]

        assert [m["label"] for m in payload["props"]["items"]] == ["The stack", "Agent workflow"]

    def test_should_record_the_tab_so_the_final_re_emit_replaces_it(self) -> None:
        ctx = _ctx([_PLANNED])

        moments.provisional_moments_event(ctx)  # type: ignore[arg-type]

        assert ctx.tab_ledger.sent["moments"].final is False

    def test_should_use_a_landed_description_analysis(self) -> None:
        landed = DescriptionAnalysis(
            timestamps=[DescriptionTimestamp(time="0:30", seconds=30, label="Setup")]
        )
        ctx = _ctx([_PLANNED], memory=None, description_analysis=landed)

        payload = _payload(moments.provisional_moments_event(ctx))  # type: ignore[arg-type]

        assert [m["label"] for m in payload["props"]["items"]] == ["Setup"]

    def test_should_emit_nothing_without_any_source(self) -> None:
        ctx = _ctx([_PLANNED], memory=None)

        assert moments.provisional_moments_event(ctx) is None  # type: ignore[arg-type]


async def _text_branch(ctx: Any) -> AsyncGenerator[str, None]:
    yield sse_event("phase", {"phase": "transcript"})


class TestThenProvisionalMoments:
    async def test_should_stream_the_tab_after_the_text_branch(self) -> None:
        wrapped = moments.then_provisional_moments(_text_branch)

        chunks = [c async for c in wrapped(_ctx([_PLANNED]))]  # type: ignore[arg-type]

        assert [_payload(c)["event"] for c in chunks] == ["phase", "tab_ready"]

    async def test_should_add_nothing_without_a_source(self) -> None:
        wrapped = moments.then_provisional_moments(_text_branch)

        chunks = [c async for c in wrapped(_ctx([_PLANNED], memory=None))]  # type: ignore[arg-type]

        assert [_payload(c)["event"] for c in chunks] == ["phase"]

    def test_should_keep_the_phase_name_for_pipeline_timing(self) -> None:
        assert moments.then_provisional_moments(_text_branch).__name__ == "_text_branch"
