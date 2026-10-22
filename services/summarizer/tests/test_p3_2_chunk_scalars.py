"""A chunked run's scalars reach the hero part by part (output-quality 3.2, L20).

``merge_groups`` keeps the first chunk's scalar (``summary`` is "In short",
``keyQuestion`` the hero brief's first line). ``chunk_scalars`` gives the hero every
chunk's value of each scalar the chunks wrote differently, in chunk order, under the
chunk's window; ``compact_for_hero`` shows such a scalar as a header + one line per part,
and without parts keeps today's bytes. The hero LLM call is faked at the retry wrapper
or at ``hero_rewrite_for_run``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import PlanResult
from src.services.pipeline import hero_rewrite
from src.services.pipeline.assembly.assemblers_learning import assemble_overview
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_groups import GroupOutcome, GroupStatus, SplitRun
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.group_merge import ScalarPart, chunk_scalars, merge_groups
from src.services.pipeline.hero_compact import compact_for_hero
from src.services.pipeline.hero_job import HeroRequest
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.phases.group_emission import GroupLanding
from src.services.pipeline.pipeline_helpers import PipelineTimer

_DURATION = 3787
_CHUNKS = (
    ExtractionChunk(index=0, transcript="", end_s=2361, video_end_s=_DURATION),
    ExtractionChunk(index=1, transcript="", start_s=2361, video_end_s=_DURATION),
)
_FIRST = "DHH argues the agent era began with Opus 4.5 and compares it to the Kodak Brownie."
_SECOND = "He retires from hand-written code and turns to architecture, CLIs and Omarchy."
_PARTS = {
    "learning.summary": (
        ScalarPart("0:00–39:21", _FIRST),
        ScalarPart("39:21–1:03:07", _SECOND),
    )
}
_IDEAS = FieldRequest("learning", "keyIdeas", "list", "planned", ("ideas",), 10, 3, 50, False)
_GROUP = ExtractionGroup("text1", (_IDEAS,), False, 0, ("ideas",))
_TABS = [
    {"id": "ideas", "label": "Key Ideas", "component": "spot_explorer",
     "dataSource": "learning.keyIdeas"},
]  # fmt: skip
_WHY = "Opus 4.5 made the agent loop good enough to trust"
# A small extraction and its compact as 2.1 shipped it (HEAD's hero_compact gives these
# bytes) — pinned: no parts, no change.
_EXTRACTION = {
    "learning": {
        "keyIdeas": [{"claim": "Agents write the code", "why": _WHY, "timestamp": 60}],
        "summary": _FIRST,
    }
}
_PINNED = (
    f"learning.keyIdeas:\nK1 [1:00] Agents write the code — {_WHY}\nlearning.summary: {_FIRST}"
)


def _outcome(chunk: int, summary: str, status: GroupStatus = "ok") -> GroupOutcome:
    idea = {"claim": f"idea of part {chunk + 1}", "why": "because", "timestamp": 60 + 2400 * chunk}
    data = {"learning": {"keyIdeas": [idea], "summary": summary, "keyQuestion": "What now?"}}
    payload = data if status == "ok" else None
    return GroupOutcome(_GROUP, chunk, payload, {"dropped": {}, "defaulted": 0}, status, 1, 0)


# ─── chunk_scalars ───


class TestChunkScalars:
    def test_should_give_both_parts_in_chunk_order_with_their_windows(self) -> None:
        outcomes = [_outcome(1, _SECOND), _outcome(0, _FIRST)]

        parts = chunk_scalars(outcomes, _CHUNKS)

        assert parts["learning.summary"] == _PARTS["learning.summary"]

    def test_should_omit_a_scalar_every_chunk_wrote_the_same(self) -> None:
        outcomes = [_outcome(0, _FIRST), _outcome(1, _SECOND)]

        assert "learning.keyQuestion" not in chunk_scalars(outcomes, _CHUNKS)

    def test_should_omit_a_scalar_that_differs_only_in_case_and_spacing(self) -> None:
        outcomes = [_outcome(0, _FIRST), _outcome(1, f"  {_FIRST.upper()} ")]

        assert chunk_scalars(outcomes, _CHUNKS) == {}

    def test_should_give_nothing_when_the_run_is_one_chunk(self) -> None:
        assert chunk_scalars([_outcome(0, _FIRST)], _CHUNKS[:1]) == {}

    def test_should_read_nothing_from_a_failed_group(self) -> None:
        outcomes = [_outcome(0, _FIRST), _outcome(1, _SECOND, status="failed")]

        assert chunk_scalars(outcomes, _CHUNKS) == {}

    def test_should_keep_the_first_chunks_value_in_the_overview(self) -> None:
        merged, _ = merge_groups([_outcome(0, _FIRST), _outcome(1, _SECOND)])

        overview = assemble_overview({"_primary_tag": "learning"}, None, merged, None)

        assert overview is not None and overview["data"]["summary"] == _FIRST


# ─── The compact ───


class TestCompact:
    def test_should_keep_todays_bytes_when_there_are_no_parts(self) -> None:
        assert compact_for_hero(_EXTRACTION, _DURATION).text == _PINNED

    def test_should_keep_todays_bytes_when_the_parts_are_empty(self) -> None:
        assert compact_for_hero(_EXTRACTION, _DURATION, chunk_scalars={}).text == _PINNED

    def test_should_show_a_parted_scalar_as_a_header_and_one_line_per_part(self) -> None:
        compact = compact_for_hero(_EXTRACTION, _DURATION, chunk_scalars=_PARTS)

        assert compact.text.split("\n")[2:] == [
            "learning.summary (one per part of the video):",
            f"- (0:00–39:21) {_FIRST}",
            f"- (39:21–1:03:07) {_SECOND}",
        ]

    def test_should_count_the_second_part_as_evidence(self) -> None:
        compact = compact_for_hero(_EXTRACTION, _DURATION, chunk_scalars=_PARTS)

        assert "Omarchy" in compact.evidence


async def test_should_send_the_parts_in_the_hero_job_when_the_request_carries_them() -> None:
    request = HeroRequest(
        title="Rails World keynote",
        duration=_DURATION,
        extraction=_EXTRACTION,
        chunk_scalars=_PARTS,
    )
    llm = AsyncMock(return_value=None)

    with patch.object(hero_rewrite, "call_llm_with_retry", llm):
        await hero_rewrite.rewrite_hero(MagicMock(), request)

    job = llm.await_args_list[0].args[1][-1]["text"]
    assert f"- (39:21–1:03:07) {_SECOND}" in job


# ─── Text-groups-done hands the parts to the hero ───


def _ctx() -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": ["learning"], "primaryTag": "learning", "tabs": _TABS, "confidence": 0.9}
    )
    ctx.triage_dict = {"contentTags": ["learning"], "primaryTag": "learning", "tabs": _TABS}
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="Rails World keynote", channel="c", duration=_DURATION, chapters=None
    )
    return ctx


def _landed(chunks: tuple[ExtractionChunk, ...]) -> PipelineContext:
    ctx = _ctx()
    run = SplitRun(MagicMock(), MagicMock(), MagicMock(), chunks, (_GROUP,), ("learning",), ())
    landing = GroupLanding.for_run(ctx, run.text_groups, len(chunks), run)
    outcomes: list[GroupOutcome] = []
    for chunk, summary in list(enumerate((_FIRST, _SECOND)))[: len(chunks)]:
        outcomes.append(_outcome(chunk, summary))
        landing.events(outcomes[-1], outcomes, (_IDEAS,))
    return ctx


@pytest.fixture
async def hero_call() -> AsyncIterator[AsyncMock]:
    with patch.object(synthesis_phase, "hero_rewrite_for_run", AsyncMock(return_value=None)) as m:
        yield m
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith(("hero_", "merge_"))]
        for task in started:
            task.cancel()
        await asyncio.gather(*started, return_exceptions=True)


@pytest.fixture(autouse=True)
def hero_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", True)
    monkeypatch.setattr(settings, "MERGE_RANK_ENABLED", False)


class TestHeroStart:
    async def test_should_hand_the_hero_both_parts_at_text_groups_done(
        self, hero_call: AsyncMock
    ) -> None:
        ctx = _landed(_CHUNKS)
        assert ctx.hero_task is not None

        await ctx.hero_task

        assert hero_call.call_args.kwargs["chunk_scalars"] == _PARTS

    async def test_should_hand_the_hero_no_parts_when_the_run_is_one_chunk(
        self, hero_call: AsyncMock
    ) -> None:
        ctx = _landed(_CHUNKS[:1])
        assert ctx.hero_task is not None

        await ctx.hero_task

        assert hero_call.call_args.kwargs["chunk_scalars"] == {}


def test_should_build_the_request_with_the_parts_when_the_run_passes_them() -> None:
    ctx: Any = SimpleNamespace(
        video_data=SimpleNamespace(title="T", duration=_DURATION),
        plan_result=None,
        triage=None,
        video_memory="",
    )

    with patch.object(hero_rewrite, "run_quote_index", MagicMock(return_value=None)):
        request = hero_rewrite.hero_request(ctx, _EXTRACTION, None, _PARTS)

    assert request.chunk_scalars == _PARTS
