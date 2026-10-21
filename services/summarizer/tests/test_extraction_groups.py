"""Firing the split extraction (pipeline-1min 3.4): warm-first release, slots, the visual group."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.config import settings
from src.services.pipeline import extraction_groups
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import (
    GroupCall,
    GroupOutcome,
    SplitRun,
    fire_groups,
    group_timeout,
    run_group_call,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.pipeline_helpers import run_parallel_phases

_JOB = GroupJob([], ["food"], "food")


def _group(group_id: str, domain: str = "food", visual: bool = False) -> ExtractionGroup:
    request = FieldRequest(domain, group_id, "list", "planned", (), 10, 5, 100, visual)
    return ExtractionGroup(group_id, (request,), visual, 500, ())


def _run(*group_ids: str, chunks: int = 1) -> SplitRun:
    return SplitRun(
        llm_service=MagicMock(),
        prompt=MagicMock(),
        job=_JOB,
        chunks=tuple(ExtractionChunk(i, f"chunk {i}") for i in range(chunks)),
        text_groups=tuple(_group(g) for g in group_ids),
        content_tags=("food",),
        modifiers=(),
    )


async def _no_visual(_fired: frozenset[str]) -> None:
    return None


def _ok(call: GroupCall) -> GroupOutcome:
    data = {"food": {call.group.group_id: []}}
    return GroupOutcome(
        call.group, call.chunk.index, data, {"dropped": {}, "defaulted": 0}, "ok", 1, 0
    )


@dataclass
class _Calls:
    """A stand-in for ``run_group_call``: logs starts / first chunks, tracks slots in use."""

    first_chunk_after: float | None = 0.02
    fail_text1: bool = False
    duration: float = 0.02
    log: list[tuple[str, str, float]] = field(default_factory=list)
    streamed: list[str] = field(default_factory=list)
    in_flight: int = 0
    peak: int = 0
    cancelled: list[str] = field(default_factory=list)
    # Per group id: how long that call takes instead of ``duration``.
    slow: dict[str, float] = field(default_factory=dict)

    def at(self, event: str, group_id: str) -> float:
        return next(t for e, g, t in self.log if (e, g) == (event, group_id))

    async def __call__(
        self, call: GroupCall, run: SplitRun, *, on_first_chunk: Callable[[], None] | None = None
    ) -> GroupOutcome:
        loop = asyncio.get_running_loop()
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.log.append(("start", call.group.group_id, loop.time()))
        try:
            if on_first_chunk is not None:
                await self._warm_up(call, on_first_chunk)
            await asyncio.sleep(self.slow.get(call.group.group_id, self.duration))
            return _ok(call)
        except asyncio.CancelledError:
            self.cancelled.append(call.group.group_id)
            raise
        finally:
            self.in_flight -= 1

    async def _warm_up(self, call: GroupCall, on_first_chunk: Callable[[], None]) -> None:
        self.streamed.append(call.group.group_id)
        if self.fail_text1:
            raise RuntimeError("provider down")
        if self.first_chunk_after is None:
            await asyncio.sleep(1.0)
            return
        await asyncio.sleep(self.first_chunk_after)
        self.log.append(("first_chunk", call.group.group_id, asyncio.get_running_loop().time()))
        on_first_chunk()


async def _fire(
    run: SplitRun, calls: _Calls, cut: Callable[[frozenset[str]], Awaitable[Any]] = _no_visual
) -> list[GroupOutcome | None]:
    with patch.object(extraction_groups, "run_group_call", calls):
        return [item async for item in fire_groups(run, cut)]


@pytest.fixture(autouse=True)
def _six_slots() -> Iterator[None]:
    with patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", 6):
        yield


class TestWarmFirst:
    async def test_should_stream_only_text1(self) -> None:
        calls = _Calls()

        await _fire(_run("text1", "text2", "text3"), calls)

        assert calls.streamed == ["text1"]

    async def test_should_start_the_others_after_text1s_first_chunk(self) -> None:
        calls = _Calls(first_chunk_after=0.05)

        await _fire(_run("text1", "text2", "text3"), calls)

        first_chunk = calls.at("first_chunk", "text1")
        assert min(calls.at("start", g) for g in ("text2", "text3")) >= first_chunk

    async def test_should_release_the_others_after_the_fallback_when_no_chunk_arrives(self) -> None:
        calls = _Calls(first_chunk_after=None)
        with patch.object(extraction_groups, "GROUP_RELEASE_FALLBACK_S", 0.05):
            await _fire(_run("text1", "text2"), calls)

        assert calls.at("start", "text2") - calls.at("start", "text1") < 0.5

    async def test_should_hold_the_others_until_the_fallback_without_a_chunk(self) -> None:
        calls = _Calls(first_chunk_after=None)
        with patch.object(extraction_groups, "GROUP_RELEASE_FALLBACK_S", 0.05):
            await _fire(_run("text1", "text2"), calls)

        assert calls.at("start", "text2") - calls.at("start", "text1") >= 0.04

    async def test_should_release_at_once_when_text1_fails_before_its_first_chunk(self) -> None:
        calls = _Calls(fail_text1=True)
        with patch.object(extraction_groups, "GROUP_RELEASE_FALLBACK_S", 5.0):
            outcomes = await asyncio.wait_for(_fire(_run("text1", "text2"), calls), timeout=2.0)

        assert sorted((o.group.group_id, o.status) for o in outcomes if o) == [
            ("text1", "failed"),
            ("text2", "ok"),
        ]


class TestSlots:
    async def test_should_never_exceed_extraction_parallel_batches_calls_in_flight(self) -> None:
        calls = _Calls(first_chunk_after=0.0)
        with patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", 2):
            await _fire(_run("text1", "text2", "text3", "text4", "text5"), calls)

        assert calls.peak == 2

    async def test_should_give_text1_the_first_slot(self) -> None:
        calls = _Calls(first_chunk_after=0.0)
        with patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", 1):
            await _fire(_run("text1", "text2", "text3"), calls)

        assert [g for e, g, _ in calls.log if e == "start"][0] == "text1"

    async def test_should_warm_every_chunk_with_its_own_text1(self) -> None:
        calls = _Calls()

        await _fire(_run("text1", "text2", chunks=2), calls)

        assert calls.streamed == ["text1", "text1"]


class TestVisualGroup:
    async def test_should_fire_the_visual_group_only_after_frames_done(self) -> None:
        calls, frames_done, done_at = _Calls(), asyncio.Event(), []

        async def cut(_fired: frozenset[str]) -> tuple[ExtractionGroup, GroupJob]:
            await frames_done.wait()
            done_at.append(asyncio.get_running_loop().time())
            return _group("visual", visual=True), _JOB

        async def frames() -> None:
            await asyncio.sleep(0.05)
            frames_done.set()

        _, fired = await asyncio.gather(frames(), _fire(_run("text1"), calls, cut))

        assert calls.at("start", "visual") >= done_at[0] and len(fired) == 2

    async def test_should_cut_it_from_the_fields_no_text_group_asked_for(self) -> None:
        seen: list[frozenset[str]] = []

        async def cut(fired: frozenset[str]) -> None:
            seen.append(fired)

        await _fire(_run("text1", "text2"), _Calls(), cut)

        assert seen == [frozenset({"food.text1", "food.text2"})]

    async def test_should_not_fire_it_when_nothing_is_left(self) -> None:
        calls = _Calls()

        items = await _fire(_run("text1"), calls)

        assert (items.count(None), [g for e, g, _ in calls.log if e == "start"]) == (1, ["text1"])

    async def test_should_go_at_once_when_there_is_no_text_group(self) -> None:
        calls = _Calls()

        async def cut(_fired: frozenset[str]) -> tuple[ExtractionGroup, GroupJob]:
            return _group("visual", visual=True), _JOB

        with patch.object(extraction_groups, "GROUP_RELEASE_FALLBACK_S", 5.0):
            items = await asyncio.wait_for(_fire(_run(), calls, cut), timeout=2.0)

        assert [o.group.group_id for o in items if o] == ["visual"]


def _leaked() -> list[asyncio.Task[Any]]:
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and not t.done()]


class TestCancellation:
    async def test_should_cancel_the_calls_in_flight_when_the_consumer_stops_early(self) -> None:
        calls = _Calls(slow={"text2": 30.0})
        with patch.object(extraction_groups, "run_group_call", calls):
            landed = fire_groups(_run("text1", "text2"), _no_visual)
            first = None
            while first is None:  # the unfired visual group reports None first
                first = await landed.__anext__()
            await landed.aclose()

        assert (first.group.group_id, calls.cancelled, _leaked()) == ("text1", ["text2"], [])

    async def test_should_cancel_the_calls_in_flight_when_a_sibling_phase_fails(self) -> None:
        calls = _Calls(slow={"text1": 30.0})

        async def groups_member(_ctx: Any) -> AsyncGenerator[str, None]:
            async for _ in fire_groups(_run("text1"), _no_visual):
                yield "data: landed\n\n"

        async def failing_member(_ctx: Any) -> AsyncGenerator[str, None]:
            await asyncio.sleep(0.05)
            raise RuntimeError("frames broke")
            yield

        with patch.object(extraction_groups, "run_group_call", calls), pytest.raises(RuntimeError):
            _ = [e async for e in run_parallel_phases([failing_member, groups_member], MagicMock())]

        assert (calls.cancelled, _leaked()) == (["text1"], [])


# ─── One call ───


def _call(group_id: str = "text2", chunk: int = 0) -> GroupCall:
    return GroupCall(_group(group_id), ExtractionChunk(chunk, "t"), _JOB)


def _ingredients_call() -> GroupCall:
    request = FieldRequest("food", "ingredients", "list", "planned", (), 30, 5, 40, False)
    group = ExtractionGroup("text1", (request,), False, 200, ())
    return GroupCall(group, ExtractionChunk(0, "t"), _JOB)


@pytest.fixture
def llm() -> Iterator[AsyncMock]:
    seen: dict[str, Any] = {}

    async def answer(*_args: Any, **kwargs: Any) -> str:
        seen.update(kwargs, feature=llm_feature_var.get())
        return answer.reply  # type: ignore[attr-defined]

    answer.reply = '{"food": {"text2": []}}'  # type: ignore[attr-defined]
    mock = AsyncMock(side_effect=answer)
    mock.seen = seen
    mock.answer = answer
    blocks = [{"type": "text", "text": block} for block in ("rules", "head", "job")]
    with (
        patch.object(extraction_groups, "call_llm_with_retry", mock),
        patch.object(extraction_groups, "build_group_messages", return_value=blocks),
    ):
        yield mock


class TestRunGroupCall:
    async def test_should_tag_each_group_call_with_its_own_feature(self, llm: AsyncMock) -> None:
        await asyncio.create_task(run_group_call(_call("text2"), _run()))

        assert (llm.seen["feature"], llm.seen["stage_name"]) == (
            "summarize:extraction:text2",
            "extraction_text2",
        )

    async def test_should_suffix_the_span_with_the_chunk_after_the_first(
        self, llm: AsyncMock
    ) -> None:
        await asyncio.create_task(run_group_call(_call("text2", chunk=1), _run()))

        assert llm.seen["stage_name"] == "extraction_text2_c1"

    async def test_should_carry_no_system_prompt(self, llm: AsyncMock) -> None:
        await asyncio.create_task(run_group_call(_call(), _run()))

        assert ("system_prompt" in llm.seen, len(llm.await_args_list[-1].args[1])) == (False, 3)

    async def test_should_call_the_extraction_model_with_the_group_limits(
        self, llm: AsyncMock
    ) -> None:
        await asyncio.create_task(run_group_call(_call(), _run()))

        first = llm.await_args_list[0].kwargs  # the fixture's answer is retried (3.8)
        sent = {k: first[k] for k in ("max_tokens", "timeout", "max_retries", "json_mode")}
        assert sent == {"max_tokens": 16384, "timeout": 120.0, "max_retries": 1, "json_mode": True}

    async def test_should_mark_the_group_failed_when_its_answer_is_not_json(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = "sorry, no"

        outcome = await asyncio.create_task(run_group_call(_call(), _run()))

        assert (outcome.status, outcome.data) == ("failed", None)

    async def test_should_validate_a_flat_single_domain_answer_under_its_domain(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = '{"ingredients": [{"name": "eggs"}], "steps": []}'

        outcome = await asyncio.create_task(run_group_call(_ingredients_call(), _run()))

        assert (
            list(outcome.data or {}),
            (outcome.data or {})["food"]["ingredients"][0]["amount"],
        ) == (
            ["food"],
            None,
        )

    async def test_should_mark_the_group_failed_when_its_answer_holds_none_of_its_keys(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = '{"recipe": [{"name": "eggs"}]}'

        outcome = await asyncio.create_task(run_group_call(_ingredients_call(), _run()))

        assert (outcome.status, outcome.data) == ("failed", None)

    async def test_should_log_the_answer_keys_when_it_holds_none_of_its_keys(
        self, llm: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        llm.answer.reply = '{"recipe": [{"name": "eggs"}]}'

        with caplog.at_level(logging.WARNING, logger=extraction_groups.__name__):
            await asyncio.create_task(run_group_call(_ingredients_call(), _run()))

        assert any("['recipe']" in r.getMessage() for r in caplog.records)

    async def test_should_report_what_validating_the_groups_keys_dropped(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = '{"food": {"ingredients": [{"name": "eggs"}, {"amount": 2}]}}'

        outcome = await asyncio.create_task(run_group_call(_ingredients_call(), _run()))

        assert outcome.validation.get("dropped") == {"food.ingredients": {"dropped": 1, "total": 2}}

    async def test_should_mark_the_group_failed_when_the_call_raises(self) -> None:
        async def broken(*_args: Any, **_kwargs: Any) -> GroupOutcome:
            raise ValueError("template lost its sections")

        with patch.object(extraction_groups, "run_group_call", broken):
            items = [item async for item in fire_groups(_run("text1"), _no_visual)]

        assert [o.status for o in items if o] == ["failed"]


# ─── The call's timeout (R40 B2) ───


def _alone(request: FieldRequest) -> ExtractionGroup:
    return ExtractionGroup("text1", (request,), False, request.est_output_tokens, request.tab_ids)


# travel.itinerary × 8 alone: ≈ 11k output tokens, more than 120 s land at 83–91 tok/s.
_ITINERARY = FieldRequest("travel", "itinerary", "list", "planned", ("stops",), 25, 8, 1387, False)


class TestGroupTimeout:
    def test_should_derive_a_heavy_single_field_groups_timeout_from_its_estimate(self) -> None:
        assert group_timeout(_alone(_ITINERARY)) == pytest.approx(8 * 1387 / 60 + 30)

    def test_should_keep_120_s_for_a_small_group(self) -> None:
        assert group_timeout(_group("text2")) == 120.0

    def test_should_cap_the_timeout_at_300_s(self) -> None:
        lyrics = FieldRequest("music", "lyrics", "list", "planned", (), 200, 200, 100, False)

        assert group_timeout(_alone(lyrics)) == 300.0

    async def test_should_give_the_streamed_warm_up_call_its_groups_timeout(
        self, llm: AsyncMock
    ) -> None:
        llm.answer.reply = '{"travel": {"itinerary": []}}'
        call = GroupCall(_alone(_ITINERARY), ExtractionChunk(0, "t"), _JOB)

        await asyncio.create_task(run_group_call(call, _run(), on_first_chunk=lambda: None))

        assert llm.await_args_list[0].kwargs["timeout"] == pytest.approx(8 * 1387 / 60 + 30)
