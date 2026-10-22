"""merge_rank wired into the split run (output-quality 3.1).

At text-groups-done on a run of more than one chunk, ``GroupLanding`` starts merge_rank
beside the hero, over the same merge: one call on ``LLM_MEMORY_MODEL`` under its own
stage and ledger feature. ``split_extraction._complete`` applies the answer to the final
merge before ``extraction_complete`` and records ``pipeline.extraction._mergeRank``; the
hero keeps reading the extraction as it was. One chunk, the flag off, a timeout, a
refusal or a crash → the code merge as it is. The LLM is faked at the retry wrapper; the
hero call at ``hero_rewrite_for_run``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.config import Settings, settings
from src.models.memory_types import MemoryResult, OutlineSection
from src.models.pipeline_types import PlanResult
from src.services.llm_model_rules import LLMRefusalError
from src.services.pipeline import extraction_groups, merge_rank, pipeline_timing
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.group_merge import merge_groups
from src.services.pipeline.phases import split_extraction
from src.services.pipeline.phases import synthesis as synthesis_phase
from src.services.pipeline.phases.extraction import extraction_record
from src.services.pipeline.phases.group_emission import GroupLanding
from src.services.pipeline.pipeline_helpers import PipelineTimer
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.pipeline.triage import TriageResult

_DURATION = 3600
_TWO_CHUNKS = (
    ExtractionChunk(index=0, transcript="[0:00] a", end_s=1800, video_end_s=_DURATION),
    ExtractionChunk(index=1, transcript="[30:00] b", start_s=1800, video_end_s=_DURATION),
)
_ONE_CHUNK = (ExtractionChunk(index=0, transcript="[0:00] a"),)
_TABS = [
    {"id": "ideas", "label": "Positions", "component": "spot_explorer",
     "dataSource": "learning.keyIdeas"},
    {"id": "moments", "label": "Moments", "component": "moment_track",
     "dataSource": "learning.timestamps"},
]  # fmt: skip
_IDEAS = FieldRequest("learning", "keyIdeas", "list", "planned", ("ideas",), 10, 3, 50, False)
_MOMENTS = FieldRequest("learning", "timestamps", "list", "planned", ("moments",), 10, 3, 50, False)
_TEXT1 = ExtractionGroup("text1", (_IDEAS,), False, 0, ("ideas",))
_TEXT2 = ExtractionGroup("text2", (_MOMENTS,), False, 0, ("moments",))
_FIELDS = (_IDEAS, _MOMENTS)
# Chunk 1 restates chunk 0's last idea under another name (S01's "Pencils down").
_TWIN = "Retiring from hand-written code"
_ITEMS: dict[tuple[str, int], list[dict[str, Any]]] = {
    ("keyIdeas", 0): [
        {"claim": f"Idea {n}", "why": "Because.", "timestamp": 300 * n} for n in range(1, 5)
    ]
    + [{"claim": "Pencils down", "why": "Agents write it.", "timestamp": 1700}],
    ("keyIdeas", 1): [{"claim": _TWIN, "why": "Agents write it.", "timestamp": 1850}]
    + [
        {"claim": f"Late idea {n}", "why": "Because.", "timestamp": 1800 + 300 * n}
        for n in range(1, 5)
    ],
    ("timestamps", 0): [
        {"seconds": 200 * n, "label": f"early {n}", "description": "shows"} for n in range(1, 8)
    ],
    ("timestamps", 1): [
        {"seconds": 1800 + 200 * n, "label": f"late {n}", "description": "shows"}
        for n in range(1, 8)
    ],
}
_ANSWER = json.dumps({"duplicates": [{"keep": "K5", "drop": ["K6"]}], "sections": {"2": ["K6"]}})
_MEMORY_MODEL = "anthropic/claude-haiku-5-5"


def _outcome(group: ExtractionGroup, chunk: int) -> GroupOutcome:
    data = {"learning": {f.field: list(_ITEMS[(f.field, chunk)]) for f in group.fields}}
    report = {"dropped": {}, "defaulted": 0}
    return GroupOutcome(group, chunk, data, report, "ok", 1, 0)  # type: ignore[arg-type]


def _ctx() -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": ["learning"], "primaryTag": "learning", "tabs": _TABS, "confidence": 0.9}
    )
    ctx.triage = TriageResult(content_tags=["learning"], primary_tag="learning", tabs=_TABS)
    ctx.triage_dict = {"contentTags": ["learning"], "primaryTag": "learning", "tabs": _TABS}
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="A keynote", channel="c", duration=_DURATION, chapters=None, description=""
    )
    ctx.memory = MemoryResult(
        outline=[
            OutlineSection(start=0, end=1800, title="the bet"),
            OutlineSection(start=1800, end=_DURATION, title="the close"),
        ]
    )
    ctx.prompt_transcript = "[0:00] a\n[30:00] b"
    return ctx


class _Landing:
    """A split run's landing: feeds outcomes in order."""

    def __init__(self, chunks: tuple[ExtractionChunk, ...] = _TWO_CHUNKS) -> None:
        self.ctx = _ctx()
        self.run = SplitRun(
            MagicMock(), MagicMock(), MagicMock(), chunks, (_TEXT1, _TEXT2), ("learning",), ()
        )
        self.landing = GroupLanding.for_run(self.ctx, self.run.text_groups, len(chunks), self.run)
        self.outcomes: list[GroupOutcome] = []

    def land(self, *outcomes: GroupOutcome) -> None:
        for outcome in outcomes:
            self.outcomes.append(outcome)
            self.landing.events(outcome, self.outcomes, _FIELDS)

    def land_all(self) -> None:
        for chunk in range(len(self.run.chunks)):
            self.land(_outcome(_TEXT1, chunk), _outcome(_TEXT2, chunk))

    def merged(self) -> dict[str, Any]:
        return merge_groups(self.outcomes)[0]


def _ideas(extraction: dict[str, Any]) -> list[str]:
    return [idea["claim"] for idea in extraction["learning"]["keyIdeas"]]


@pytest.fixture
def llm() -> Iterator[AsyncMock]:
    """The merge_rank call at the retry wrapper; each call's ledger feature is kept."""
    features: list[str] = []
    replies: list[Any] = []

    async def answer(*_args: Any, **_kwargs: Any) -> str | None:
        features.append(llm_feature_var.get())
        reply = replies.pop(0) if replies else _ANSWER
        if isinstance(reply, BaseException):
            raise reply
        return reply

    mock = AsyncMock(side_effect=answer)
    mock.features = features  # type: ignore[attr-defined]
    mock.replies = replies  # type: ignore[attr-defined]
    with patch.object(merge_rank, "call_llm_with_retry", mock):
        yield mock


@pytest.fixture
async def hero_call() -> AsyncIterator[AsyncMock]:
    """The hero call; started tasks of the test are gathered."""
    with patch.object(synthesis_phase, "hero_rewrite_for_run", AsyncMock(return_value=None)) as m:
        yield m
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith(("hero_", "merge_"))]
        await asyncio.gather(*started, return_exceptions=True)


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MERGE_RANK_ENABLED", True)


pytestmark = pytest.mark.usefixtures("flag_on", "hero_call")


# ─── When it starts ───


class TestStart:
    async def test_should_make_one_call_when_text_groups_are_done_on_two_chunks(
        self, llm: AsyncMock
    ) -> None:
        split = _Landing()
        split.land_all()
        assert split.ctx.merge_rank_task is not None

        await split.ctx.merge_rank_task

        assert llm.await_count == 1

    async def test_should_start_no_call_when_the_run_is_one_chunk(self, llm: AsyncMock) -> None:
        split = _Landing(_ONE_CHUNK)

        split.land_all()

        assert split.ctx.merge_rank_task is None

    async def test_should_start_no_call_when_the_flag_is_off(
        self, llm: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MERGE_RANK_ENABLED", False)
        split = _Landing()

        split.land_all()

        assert split.ctx.merge_rank_task is None

    async def test_should_wait_until_every_text_group_landed_on_every_chunk(
        self, llm: AsyncMock
    ) -> None:
        split = _Landing()
        split.land(_outcome(_TEXT1, 0), _outcome(_TEXT2, 0), _outcome(_TEXT1, 1))

        assert split.ctx.merge_rank_task is None

    async def test_should_start_on_the_same_landing_as_the_hero(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land(_outcome(_TEXT1, 0), _outcome(_TEXT2, 0), _outcome(_TEXT1, 1))
        before = (split.ctx.hero_task, split.ctx.merge_rank_task)

        split.land(_outcome(_TEXT2, 1))

        after = (split.ctx.hero_task is not None, split.ctx.merge_rank_task is not None)
        assert (before, after) == ((None, None), (True, True))

    async def test_should_start_once_when_a_later_group_lands(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land_all()
        first = split.ctx.merge_rank_task

        split.land(_outcome(_TEXT1, 1))

        assert split.ctx.merge_rank_task is first


# ─── The call ───


class TestCall:
    async def test_should_call_under_its_own_stage_in_json_mode(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land_all()
        assert split.ctx.merge_rank_task is not None

        await split.ctx.merge_rank_task

        kwargs = llm.await_args.kwargs
        assert (kwargs["stage_name"], kwargs["json_mode"]) == ("merge_rank", True)

    async def test_should_bill_its_own_ledger_feature(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land_all()
        assert split.ctx.merge_rank_task is not None

        await split.ctx.merge_rank_task

        assert llm.features == ["summarize:merge_rank"]  # type: ignore[attr-defined]

    async def test_should_call_on_the_memory_model(
        self, llm: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_MEMORY_MODEL", _MEMORY_MODEL)
        split = _Landing()
        split.land_all()
        assert split.ctx.merge_rank_task is not None

        await split.ctx.merge_rank_task

        assert llm.await_args.kwargs["model_override"] == _MEMORY_MODEL

    async def test_should_record_its_timing_step(self, llm: AsyncMock) -> None:
        token = pipeline_timing._recorder_var.set(None)
        try:
            recorder: PipelineTimingRecorder = start_run_timing()
            split = _Landing()
            split.land_all()
            assert split.ctx.merge_rank_task is not None
            await split.ctx.merge_rank_task
        finally:
            pipeline_timing._recorder_var.reset(token)

        assert "merge_rank_call" in [phase["name"] for phase in recorder.phases]


def test_should_follow_the_memory_model_when_its_setting_is_read() -> None:
    config = Settings(LLM_MEMORY_MODEL=_MEMORY_MODEL, LLM_EXTRACTION_MODEL="x/sonnet")

    assert config.get_stage_model("merge_rank") == _MEMORY_MODEL


def test_should_follow_the_memory_fallback_when_the_memory_model_is_blank() -> None:
    config = Settings(LLM_MEMORY_MODEL="", LLM_EXTRACTION_MODEL="x/sonnet")

    assert config.get_stage_model("merge_rank") == "x/sonnet"


# ─── The answer applied ───


async def _applied(split: _Landing) -> dict[str, Any]:
    return await merge_rank.merge_rank_extraction(split.ctx, split.merged())


class TestApplied:
    async def test_should_drop_the_twin_from_the_final_merge(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land_all()

        result = await _applied(split)

        assert _TWIN not in _ideas(result)

    async def test_should_leave_the_twin_in_the_heros_extraction(
        self, llm: AsyncMock, hero_call: AsyncMock
    ) -> None:
        split = _Landing()
        split.land_all()
        await _applied(split)
        assert split.ctx.hero_task is not None

        await split.ctx.hero_task

        assert _TWIN in _ideas(hero_call.call_args.args[1])

    async def test_should_record_the_outcome_on_the_context(self, llm: AsyncMock) -> None:
        split = _Landing()
        split.land_all()

        await _applied(split)

        record = split.ctx.merge_rank_record or {}
        assert (record["status"], [row["id"] for row in record["dropped"]]) == ("ok", ["K6"])

    async def test_should_return_the_code_merge_when_no_call_started(self, llm: AsyncMock) -> None:
        split = _Landing(_ONE_CHUNK)
        split.land_all()
        merged = split.merged()

        assert await merge_rank.merge_rank_extraction(split.ctx, merged) is merged


# ─── Failures leave the code merge ───


async def _forever(*_args: object, **_kwargs: object) -> str:
    await asyncio.sleep(3600)
    return _ANSWER


class TestFailures:
    async def test_should_keep_the_code_merge_when_the_call_times_out(
        self, llm: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(merge_rank, "MERGE_RANK_TOTAL_TIMEOUT_S", 0.05)
        llm.side_effect = _forever
        split = _Landing()
        split.land_all()

        result = await _applied(split)

        assert (_TWIN in _ideas(result), (split.ctx.merge_rank_record or {})["status"]) == (
            True,
            "timeout",
        )

    async def test_should_ask_once_and_keep_the_code_merge_when_refused(
        self, llm: AsyncMock
    ) -> None:
        llm.replies.append(LLMRefusalError("haiku", "cyber"))  # type: ignore[attr-defined]
        split = _Landing()
        split.land_all()

        result = await _applied(split)

        assert (llm.await_count, _TWIN in _ideas(result)) == (1, True)

    async def test_should_keep_the_code_merge_when_the_call_crashes(self, llm: AsyncMock) -> None:
        llm.replies.append(RuntimeError("boom"))  # type: ignore[attr-defined]
        split = _Landing()
        split.land_all()

        result = await _applied(split)

        assert (_TWIN in _ideas(result), (split.ctx.merge_rank_record or {})["status"]) == (
            True,
            "crashed",
        )

    async def test_should_ask_once_more_when_the_reply_is_no_json(self, llm: AsyncMock) -> None:
        llm.replies.append("not json at all")  # type: ignore[attr-defined]
        split = _Landing()
        split.land_all()

        result = await _applied(split)

        assert (llm.await_count, _TWIN in _ideas(result)) == (2, False)

    async def test_should_keep_the_code_merge_when_both_replies_are_no_json(
        self, llm: AsyncMock
    ) -> None:
        llm.replies.extend(["nope", "still nope"])  # type: ignore[attr-defined]
        split = _Landing()
        split.land_all()

        await _applied(split)

        assert (split.ctx.merge_rank_record or {})["status"] == "not_json"


# ─── The split member end to end ───


class _Groups:
    """``run_group_call`` stand-in: each group's own items for its chunk."""

    async def __call__(self, call: GroupCall, run: SplitRun, **_kwargs: Any) -> GroupOutcome:
        return _outcome(call.group, call.chunk.index)


def _member_ctx() -> PipelineContext:
    ctx = _ctx()
    ctx.plan_done.set()
    ctx.frames_done.set()
    return ctx


async def _drain(ctx: PipelineContext) -> list[dict[str, Any]]:
    with (
        patch.object(split_extraction, "plan_chunks", AsyncMock(return_value=list(_TWO_CHUNKS))),
        patch.object(
            split_extraction,
            "build_groups",
            MagicMock(return_value=[_TEXT1, _TEXT2]),
        ),
        patch.object(extraction_groups, "run_group_call", _Groups()),
    ):
        events = [event async for event in split_extraction.run_phase_split_extraction(ctx)]
    return [json.loads(event.removeprefix("data: ")) for event in events]


class TestSplitMember:
    async def test_should_send_the_deduped_lists_in_extraction_complete(
        self, llm: AsyncMock
    ) -> None:
        events = await _drain(_member_ctx())

        [complete] = [e for e in events if e["event"] == "extraction_complete"]
        assert _TWIN not in _ideas(complete["data"])

    async def test_should_store_the_outcome_as_the_extractions_merge_rank_record(
        self, llm: AsyncMock
    ) -> None:
        ctx = _member_ctx()

        await _drain(ctx)

        assert (extraction_record(ctx) or {})["_mergeRank"]["status"] == "ok"

    async def test_should_cancel_a_started_call_when_the_member_fails(self, llm: AsyncMock) -> None:
        llm.side_effect = _forever
        ctx = _member_ctx()

        with patch.object(split_extraction, "_complete", AsyncMock(side_effect=RuntimeError)):
            await asyncio.gather(_drain(ctx), return_exceptions=True)
        assert ctx.merge_rank_task is not None
        await asyncio.gather(ctx.merge_rank_task, return_exceptions=True)

        assert ctx.merge_rank_task.cancelled()
