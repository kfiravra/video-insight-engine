"""The split extraction phase (pipeline-1min 3.4): plan-done firing, frames-done hook, orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Iterator
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import PlanResult
from src.routes import pipeline_orchestration
from src.services.pipeline import extraction_group_prompt, extraction_groups, pipeline_timing
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.phases import frames_done as frames_done_phase
from src.services.pipeline.phases import moments as moments_phase
from src.services.pipeline.phases import probe as probe_phase
from src.services.pipeline.phases.extraction import extraction_record
from src.services.pipeline.phases.frames_done import then_frames_done
from src.services.pipeline.phases.split_extraction import run_phase_split_extraction
from src.services.pipeline.pipeline_helpers import PipelineTimer
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.pipeline.post_processor import coverage_is_degraded
from src.services.pipeline.triage import TriageResult

_FOOD_TABS = [
    {
        "id": "ingredients",
        "label": "Ingredients",
        "component": "checklist",
        "dataSource": "food.ingredients",
    },
    {"id": "steps", "label": "Steps", "component": "step_player", "dataSource": "food.steps"},
]
_TECH_TABS = [
    {"id": "stack", "label": "Stack", "component": "info_grid", "dataSource": "tech.topics"},
    {"id": "code", "label": "Code", "component": "code_playground", "dataSource": "tech.snippets"},
]
_LEARNING_TABS = [
    {
        "id": "moments",
        "label": "Moments",
        "component": "moment_track",
        "dataSource": "learning.timestamps",
    },
    {
        "id": "concepts",
        "label": "Concepts",
        "component": "flash_deck",
        "dataSource": "learning.concepts",
    },
    {
        "id": "takeaways",
        "label": "Takeaways",
        "component": "checklist",
        "dataSource": "learning.takeaways",
    },
]
_ITEMS: dict[str, list[Any]] = {
    "ingredients": [{"name": "eggs"}],
    "steps": [{"number": 1, "instruction": "whisk"}],
    "timestamps": [{"time": "5:00", "seconds": 300, "label": "the demo"}],
    "concepts": [{"name": "agent", "definition": "a loop that acts"}],
    "takeaways": ["ship small"],
}


def _plan(tags: list[str], tabs: list[dict]) -> PlanResult:
    return PlanResult.model_validate(
        {"contentTags": tags, "primaryTag": tags[0], "tabs": tabs, "confidence": 0.9}
    )


def _ctx(tags: list[str], tabs: list[dict]) -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.plan_result = _plan(tags, tabs)
    ctx.triage = TriageResult(content_tags=tags, primary_tag=tags[0], tabs=tabs)
    ctx.video_data = SimpleNamespace(title="A video", duration=600)  # type: ignore[assignment]
    ctx.prompt_transcript = "[0:00] hello"
    return ctx


class _Groups:
    """``run_group_call`` stand-in: answers each group's fields from ``_ITEMS``."""

    def __init__(self, *, fail: bool = False, fail_fields: frozenset[str] = frozenset()) -> None:
        self.fail = fail
        self.fail_fields = fail_fields
        self.fired: list[str] = []

    async def __call__(self, call: GroupCall, run: SplitRun, **_kwargs: Any) -> GroupOutcome:
        self.fired.append(call.group.group_id)
        data: dict[str, Any] = {}
        for f in call.group.fields:
            data.setdefault(f.domain, {})[f.field] = _ITEMS.get(f.field, [])
        failed = self.fail or any(f.field in self.fail_fields for f in call.group.fields)
        status = "failed" if failed else "ok"
        report = {"dropped": {}, "defaulted": 0}
        payload = None if failed else data
        return GroupOutcome(call.group, call.chunk.index, payload, report, status, 1, 0)  # type: ignore[arg-type]


async def _drain(ctx: PipelineContext, groups: _Groups) -> list[str]:
    with patch.object(extraction_groups, "run_group_call", groups):
        return [event async for event in run_phase_split_extraction(ctx)]


def _events(chunks: list[str], name: str) -> list[dict[str, Any]]:
    payloads = [json.loads(c.removeprefix("data: ")) for c in chunks]
    return [p for p in payloads if p["event"] == name]


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _done(ctx: PipelineContext) -> PipelineContext:
    ctx.plan_done.set()
    ctx.frames_done.set()
    return ctx


class TestPlanDoneFiring:
    async def test_should_wait_for_plan_done_before_firing(self) -> None:
        ctx, groups = _ctx(["food"], _FOOD_TABS), _Groups()
        ctx.frames_done.set()
        task = asyncio.create_task(_drain(ctx, groups))
        await asyncio.sleep(0.05)
        fired_before = list(groups.fired)
        ctx.plan_done.set()
        await task

        assert (fired_before, bool(groups.fired)) == ([], True)

    async def test_should_send_one_extraction_complete_with_every_groups_data(self) -> None:
        events = await _drain(_done(_ctx(["food"], _FOOD_TABS)), _Groups())

        [complete] = _events(events, "extraction_complete")
        food = complete["data"]["food"]
        assert (food["ingredients"][0]["name"], food["steps"][0]["instruction"]) == (
            "eggs",
            "whisk",
        )

    async def test_should_keep_the_merged_extraction_on_the_context(self) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))

        await _drain(ctx, _Groups())

        assert (ctx.extraction_data or {})["food"]["ingredients"][0]["name"] == "eggs"

    async def test_should_report_progress_once_per_landed_group(self) -> None:
        events = await _drain(_done(_ctx(["food"], _FOOD_TABS)), _Groups())

        progress = _events(events, "extraction_progress")
        assert [(p["section"], p["batch"]) for p in progress] == [
            ("groups", n) for n in range(len(progress))
        ]

    async def test_should_fail_the_run_as_today_when_every_group_failed(self) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))

        events = await _drain(ctx, _Groups(fail=True))

        assert (_events(events, "error")[0]["code"], ctx.extraction_data) == ("LLM_ERROR", None)

    async def test_should_fail_the_run_as_today_when_no_group_answer_holds_its_keys(
        self,
    ) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))
        llm = AsyncMock(return_value='{"recipe": [{"name": "eggs"}]}')

        with (
            patch.object(extraction_groups, "call_llm_with_retry", llm),
            patch.object(extraction_groups, "build_group_messages", return_value=[]),
        ):
            events = [event async for event in run_phase_split_extraction(ctx)]

        assert (_events(events, "error")[0]["code"], ctx.extraction_data) == ("LLM_ERROR", None)

    async def test_should_mark_the_run_degraded_when_one_group_failed(self) -> None:
        ctx = _done(_ctx(["learning"], _LEARNING_TABS))

        await _drain(ctx, _Groups(fail_fields=frozenset({"concepts"})))

        assert coverage_is_degraded(ctx.extraction_coverage)

    async def test_should_extract_nothing_without_an_error_when_no_tab_reads_extraction(
        self,
    ) -> None:
        tabs = [
            {
                "id": "overview",
                "label": "Overview",
                "component": "overview",
                "dataSource": "overview",
            }
        ]
        ctx = _done(_ctx(["tech"], tabs))  # a primary the overview reads no field of

        events = await _drain(ctx, _Groups())

        assert (ctx.extraction_data, _events(events, "error")) == ({}, [])

    async def test_should_extract_nothing_without_an_error_when_only_a_kept_moment_tab_is_planned(
        self,
    ) -> None:
        # The plan-parse guard kept it on an untagged domain; the moment fallback fills it.
        ctx, groups = _done(_ctx(["tech"], _LEARNING_TABS[:1])), _Groups()

        events = await _drain(ctx, groups)

        assert (groups.fired, ctx.extraction_data, _events(events, "error")) == ([], {}, [])

    async def test_should_extract_the_primarys_overview_reads_when_only_the_overview_is_planned(
        self,
    ) -> None:
        tabs = [{"id": "overview", "label": "O", "component": "overview", "dataSource": "overview"}]
        ctx, groups = _done(_ctx(["food"], tabs)), _Groups()

        await _drain(ctx, groups)

        assert groups.fired == ["text1"]

    async def test_should_fail_the_run_when_a_tab_reads_a_field_nobody_extracted(self) -> None:
        tabs = [{"id": "x", "label": "X", "component": "info_grid", "dataSource": "tech.notAField"}]
        ctx = _done(_ctx(["tech"], tabs))  # a primary the overview reads no field of

        events = await _drain(ctx, _Groups())

        assert _events(events, "error")[0]["code"] == "LLM_ERROR"

    async def test_should_read_the_group_job_template_once_per_run(self) -> None:
        """A registry miss must not cost every group call a fetch (R40 B1): the job
        template and the group rules, one read each."""
        ctx = _done(_ctx(["food"], _FOOD_TABS))
        reads = MagicMock(wraps=extraction_group_prompt.load_prompt_text)
        llm = AsyncMock(return_value="{}")

        with (
            patch.object(extraction_group_prompt, "load_prompt_text", reads),
            patch.object(extraction_groups, "call_llm_with_retry", llm),
        ):
            _ = [event async for event in run_phase_split_extraction(ctx)]

        assert (llm.await_count, reads.call_count) == (3, 2)

    async def test_should_send_the_completeness_rule_in_every_group_calls_rules(self) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))
        llm = AsyncMock(return_value="{}")

        with patch.object(extraction_groups, "call_llm_with_retry", llm):
            _ = [event async for event in run_phase_split_extraction(ctx)]

        firsts = [call.args[1][0]["text"] for call in llm.await_args_list]
        assert [("Completeness: emit every distinct step" in t) for t in firsts] == [True] * 3

    async def test_should_record_the_extraction_step_in_the_timing(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        await _drain(_done(_ctx(["food"], _FOOD_TABS)), _Groups())

        assert [phase["name"] for phase in recorder.phases] == ["extraction"]


class TestGroupRecords:
    async def test_should_record_one_groups_row_per_group_call_in_pipeline_extraction(
        self,
    ) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))

        await _drain(ctx, _Groups(fail_fields=frozenset({"steps"})))

        rows = (extraction_record(ctx) or {})["_groups"]
        assert [(r["group"], r["chunk"], r["fields"], r["status"]) for r in rows] == [
            ("text1", 0, ["food.ingredients", "food.meta"], "ok"),
            ("text2", 0, ["food.steps"], "failed"),
            ("text3", 0, ["food.tips"], "ok"),  # the overview's tip read
        ]

    def test_should_leave_groups_out_of_pipeline_extraction_on_todays_path(self) -> None:
        ctx = _ctx(["food"], _FOOD_TABS)
        ctx.extraction_data = {"food": {"ingredients": []}}

        assert "_groups" not in (extraction_record(ctx) or {})

    async def test_should_score_quality_on_the_requested_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ctx = _done(_ctx(["food"], _FOOD_TABS))

        with caplog.at_level(logging.INFO):
            await _drain(ctx, _Groups())

        [line] = [r.getMessage() for r in caplog.records if "extraction_quality" in r.getMessage()]
        assert "populated=2/4 empty=['food.meta', 'food.tips']" in line


class TestVisualGroup:
    async def test_should_fire_the_visual_group_after_frames_done(self) -> None:
        ctx, groups = _ctx(["tech"], _TECH_TABS), _Groups()
        ctx.plan_done.set()
        task = asyncio.create_task(_drain(ctx, groups))
        await asyncio.sleep(0.05)
        before = list(groups.fired)
        ctx.frames_done.set()
        await task

        assert ("visual" in before, "visual" in groups.fired) == (False, True)

    async def test_should_cut_it_from_the_tabs_frames_done_decided(self) -> None:
        ctx, groups = _ctx(["tech"], _TECH_TABS), _Groups()
        ctx.plan_done.set()
        task = asyncio.create_task(_drain(ctx, groups))
        await asyncio.sleep(0.05)
        ctx.plan_result = _plan(["tech"], _TECH_TABS[:1])  # the code tab dropped at frames-done
        ctx.frames_done.set()
        await task

        assert groups.fired == ["text1"]


# ─── The frames-done hook ───


async def _frames_early_return(_ctx: Any) -> AsyncGenerator[str, None]:
    return
    yield


async def _frames_failed(_ctx: Any) -> AsyncGenerator[str, None]:
    raise RuntimeError("ffmpeg")
    yield


async def _consume(events: AsyncGenerator[str, None]) -> list[str]:
    return [event async for event in events]


class TestThenFramesDone:
    async def test_should_set_frames_done_when_frames_return_early(self) -> None:
        ctx = _ctx(["food"], _FOOD_TABS)
        ctx.plan_done.set()

        _ = [e async for e in then_frames_done(_frames_early_return)(ctx)]

        assert ctx.frames_done.is_set()

    async def test_should_set_frames_done_when_frames_fail(self) -> None:
        ctx = _ctx(["food"], _FOOD_TABS)

        with pytest.raises(RuntimeError):
            _ = [e async for e in then_frames_done(_frames_failed)(ctx)]

        assert ctx.frames_done.is_set()

    async def test_should_run_the_frames_done_reconcile_helper_before_the_visual_group_is_cut(
        self,
    ) -> None:
        ctx, seen = _ctx(["food"], _FOOD_TABS), []
        ctx.plan_done.set()

        def helper(run_ctx: PipelineContext) -> list[str]:
            seen.append((run_ctx.frames_done.is_set(), run_ctx.visual_annotations))
            return ["data: meta\n\n"]

        ctx.frame_descriptions = [{"timestamp_sec": 5, "content": "a slide", "original_index": 0}]
        with patch.object(frames_done_phase, "frames_done_events", helper):
            events = [e async for e in then_frames_done(_frames_early_return)(ctx)]

        assert (seen[0][0], bool(seen[0][1]), events) == (False, True, ["data: meta\n\n"])

    async def test_should_wait_for_plan_done_when_frames_end_first(self) -> None:
        ctx = _ctx(["food"], _FOOD_TABS)
        task = asyncio.create_task(_consume(then_frames_done(_frames_early_return)(ctx)))
        await asyncio.sleep(0.05)
        early = ctx.frames_done.is_set()
        ctx.plan_done.set()
        await task

        assert (early, ctx.frames_done.is_set()) == (False, True)

    async def test_should_decide_deferred_code_tabs_before_the_visual_cut_when_frames_end_first(
        self,
    ) -> None:
        ctx, groups = _ctx(["tech"], _TECH_TABS), _Groups()

        def decide(run_ctx: PipelineContext) -> list[str]:
            """3.2's helper: deferred tabs exist only once reconcile ran (plan-done);
            no code on screen → the code tab drops."""
            if run_ctx.plan_done.is_set():
                run_ctx.plan_result = _plan(["tech"], _TECH_TABS[:1])
            return []

        with patch.object(frames_done_phase, "frames_done_events", decide):
            frames = asyncio.create_task(_consume(then_frames_done(_frames_early_return)(ctx)))
            member = asyncio.create_task(_drain(ctx, groups))
            await asyncio.sleep(0.05)
            ctx.plan_done.set()
            await asyncio.gather(frames, member)

        assert groups.fired == ["text1"]

    def test_should_keep_the_frames_phase_name_for_the_timing(self) -> None:
        assert then_frames_done(_frames_early_return).__name__ == "_frames_early_return"


# ─── Orchestration ───


def _orchestration_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        youtube_id="yt1",
        clean_text="",
        frame_descriptions=None,
        scene_frames_all=None,
        transcript_data=None,
        extraction_data={},
        source_language_code=None,
        row_deleted=False,
        phase_times={},
        plan_result=PlanResult.model_validate({}),
        enrichment_data={"a": 1},
        triage=SimpleNamespace(tabs=[]),
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        content_format=None,
    )


async def _orchestrated_groups(split: bool) -> list[list[str]]:
    groups: list[list[str]] = []

    async def capture(phases: list, _ctx: Any) -> AsyncGenerator[str, None]:
        groups.append([phase.__name__ for phase in phases])
        yield "data: parallel\n\n"

    async def stub(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        yield "data: stub\n\n"

    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "EXTRACTION_PARALLEL", split))
        stack.enter_context(patch.object(probe_phase, "start_tier_probe", lambda _ctx: None))
        stack.enter_context(patch.object(moments_phase, "then_provisional_moments", lambda p: p))
        for name, value in (
            ("run_parallel_phases", capture),
            ("run_phase_metadata", stub),
            ("run_phase_assembly", stub),
        ):
            stack.enter_context(patch.object(pipeline_orchestration, name, value))
        timer = MagicMock(elapsed=MagicMock(return_value=1.0))
        _ = [
            e
            async for e in pipeline_orchestration.run_pipeline_phases(
                _orchestration_ctx(),  # type: ignore[arg-type]
                MagicMock(),
                "vsid",
                timer,
            )
        ]
    return groups


class TestOrchestration:
    async def test_should_run_the_field_groups_inside_phase_two_when_extraction_parallel(
        self,
    ) -> None:
        groups = await _orchestrated_groups(split=True)

        assert groups == [["run_phase_text", "run_phase_frames", "run_phase_split_extraction"]]

    async def test_should_keep_todays_single_extraction_when_extraction_parallel_is_false(
        self,
    ) -> None:
        groups = await _orchestrated_groups(split=False)

        assert groups == [["run_phase_text", "run_phase_frames"], ["run_phase_extraction"]]
