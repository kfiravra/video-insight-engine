"""Failure paths of the split extraction, one call at a time (pipeline-1min 3.8).

A lost group answer (not a JSON object, no key of the group, content
validation dropped, a warm-up stream that raised or brought nothing) is asked
for once more, unstreamed; an answer that says the group has nothing here
lands empty. The LLM boundary (``call_llm_with_retry``) is the ``_Model`` stub:
``build_group_messages`` is patched to hand it the group's field paths (and the
chunk). Validation runs as production. Whole runs: ``test_group_failures_run``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.models.domain_validation import ValidationReport
from src.services.pipeline import extraction_groups
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import (
    GroupCall,
    GroupOutcome,
    SplitRun,
    fire_groups,
    run_group_call,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.group_failures import (
    answer_lost,
    failed_paths,
    flag_failed_groups,
    group_drops,
    relabel_group_drops,
)

ITEMS: dict[str, list[Any]] = {
    "food.ingredients": [{"name": "eggs"}, {"name": "milk"}, {"name": "butter"}],
    "food.steps": [{"number": 1, "instruction": "whisk"}, {"number": 2, "instruction": "fold"}],
    "food.tips": [{"text": "keep the heat low"}],
    "project.steps": [{"number": 1, "instruction": "cut"}, {"number": 2, "instruction": "glue"}],
    "learning.timestamps": [{"time": "5:00", "seconds": 300, "label": "the demo"}],
}
SCRAMBLED = 'eggs" : "name ingredients": food'  # deltas out of order: not a JSON object
_PER_FIELD = ""  # ``Model(reply=...)`` unset: each group answered from ``ITEMS``
_CHUNK = "#chunk"  # the marker ``_group_paths`` adds after the paths
Step = str | None | Exception
# Validation reset the whole food block (``<domain>.*``).
_RESET: ValidationReport = {"dropped": {"food.*": {"dropped": 1, "total": 1}}, "defaulted": 0}


def _answer(paths: Sequence[str]) -> str:
    data: dict[str, Any] = {}
    for path in paths:
        if path in ITEMS:
            domain, name = path.split(".", 1)
            data.setdefault(domain, {})[name] = ITEMS[path]
    return json.dumps(data)


class Model:
    """``call_llm_with_retry`` stand-in, answering each group's fields from ``ITEMS``.

    ``script``: the first calls' answers in order (an error is raised);
    ``garbled`` paths: every answer of their group is unusable; ``scrambled``
    paths: only a streamed answer is; ``empty_chunks``: those chunks answer
    ``{}``; ``reply``: every call's answer. A streamed call signals its first
    chunk unless ``silent_stream``.
    """

    def __init__(
        self,
        *,
        garbled: frozenset[str] = frozenset(),
        scrambled: frozenset[str] = frozenset(),
        empty_chunks: frozenset[int] = frozenset(),
        reply: str | None = _PER_FIELD,
        script: Sequence[Step] = (),
        silent_stream: bool = False,
    ) -> None:
        self.garbled, self.scrambled, self.empty_chunks = garbled, scrambled, empty_chunks
        self.reply, self.script, self.silent_stream = reply, list(script), silent_stream
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, _llm: Any, paths: list[str], **kwargs: Any) -> str | None:
        streamed = kwargs.get("on_first_chunk") is not None
        call = {"paths": paths, "streamed": streamed, "retries": kwargs["max_retries"]}
        call["start"] = asyncio.get_running_loop().time()
        self.calls.append(call)
        if streamed and not self.silent_stream:
            kwargs["on_first_chunk"]()
        await asyncio.sleep(0.01)
        call["end"] = asyncio.get_running_loop().time()
        return self._step(paths, streamed)

    def _step(self, paths: list[str], streamed: bool) -> str | None:
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        if self.reply != _PER_FIELD:
            return self.reply
        if any(f"{_CHUNK}{n}" in paths for n in self.empty_chunks):
            return "{}"
        if set(paths) & self.garbled or (streamed and set(paths) & self.scrambled):
            return SCRAMBLED
        return _answer(paths)

    def of(self, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if path in c["paths"]]


def _group_paths(
    _prompt: Any, group: ExtractionGroup, chunk: ExtractionChunk, *_args: Any
) -> list[str]:
    return [*(f.path for f in group.fields), f"{_CHUNK}{chunk.index}"]


def patched(model: Model) -> AbstractContextManager[Any]:
    """``model`` answers the group calls; each call hands it its group's field paths."""
    return patch.multiple(
        extraction_groups, call_llm_with_retry=model, build_group_messages=_group_paths
    )


@pytest.fixture(autouse=True)
def _prod_slots() -> Iterator[None]:
    with patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", 6):
        yield


def request(path: str) -> FieldRequest:
    domain, name = path.split(".", 1)
    return FieldRequest(domain, name, "list", "planned", (name,), 10, 3, 40, False)


def _group(group_id: str, *paths: str) -> ExtractionGroup:
    fields = tuple(request(path) for path in paths)
    return ExtractionGroup(group_id, fields, False, 120, tuple(f.field for f in fields))


def _split_run(*groups: ExtractionGroup, chunks: int = 1) -> SplitRun:
    job = GroupJob([], ["food"], "food")
    run_chunks = tuple(ExtractionChunk(i, f"[{i}:00] whisk") for i in range(chunks))
    return SplitRun(MagicMock(), MagicMock(), job, run_chunks, groups, ("food",), ())


async def _one(model: Model, *, streamed: bool = False) -> GroupOutcome:
    group = _group("text1", "food.ingredients")
    call = GroupCall(group, ExtractionChunk(0, "[0:00] whisk"), GroupJob([], ["food"], "food"))
    with patched(model):
        return await run_group_call(
            call, _split_run(group), on_first_chunk=(lambda: None) if streamed else None
        )


async def _fire(model: Model, *groups: ExtractionGroup, chunks: int = 1) -> list[GroupOutcome]:
    async def no_visual(_fired: frozenset[str]) -> None:
        return None

    run = _split_run(*groups, chunks=chunks)
    with patched(model), patch.object(extraction_groups, "GROUP_RELEASE_FALLBACK_S", 5.0):
        items = [item async for item in fire_groups(run, no_visual)]
    return [item for item in items if item is not None]


class TestOneMoreCall:
    async def test_should_retry_once_on_unparseable_json(self) -> None:
        model = Model(scrambled=frozenset({"food.ingredients"}))

        outcome = await _one(model, streamed=True)

        assert (outcome.status, outcome.retried, len(model.calls)) == ("ok", "parse", 2)

    async def test_should_send_the_retry_as_one_unstreamed_attempt(self) -> None:
        model = Model(scrambled=frozenset({"food.ingredients"}))

        await _one(model, streamed=True)

        assert [(c["streamed"], c["retries"]) for c in model.calls] == [(True, 1), (False, 0)]

    @pytest.mark.parametrize(
        "lost", ['{"recipe": [{"name": "eggs"}]}', '{"food": {"recipe": [{"name": "eggs"}]}}']
    )
    async def test_should_retry_once_when_the_answer_lost_the_groups_content(
        self, lost: str
    ) -> None:
        model = Model(reply=lost)

        outcome = await _one(model)

        assert (outcome.status, outcome.retried, len(model.calls)) == ("failed", "empty", 2)

    async def test_should_not_call_again_when_a_plain_call_brought_no_answer(self) -> None:
        model = Model(reply=None)

        outcome = await _one(model)

        assert (outcome.status, outcome.retried, len(model.calls)) == ("failed", None, 1)

    async def test_should_not_call_again_when_a_plain_call_raises(self) -> None:
        model = Model(script=[RuntimeError("bad request")])

        with pytest.raises(RuntimeError):
            await _one(model)

        assert len(model.calls) == 1

    async def test_should_not_retry_a_usable_answer(self) -> None:
        model = Model()

        outcome = await _one(model)

        assert (outcome.retried, len(model.calls)) == (None, 1)

    async def test_should_keep_retried_and_the_first_report_when_the_extra_call_raises(
        self,
    ) -> None:
        reset = _RESET
        model = Model(script=['{"food": {"ingredients": 7}}', RuntimeError("provider down")])

        with patch.object(extraction_groups, "validate_group", return_value=({}, reset)):
            outcome = await _one(model)

        assert (outcome.status, outcome.retried, outcome.validation) == ("failed", "empty", reset)


class TestNothingHere:
    """An answer that says the group has nothing here lands empty: no extra call, not failed."""

    @pytest.mark.parametrize(
        "empty", ["{}", '{"food": {}}', '{"food": {"ingredients": null}}', '{"ingredients": null}']
    )
    async def test_should_land_an_empty_group_without_asking_again(self, empty: str) -> None:
        model = Model(reply=empty)

        outcome = await _one(model)

        assert (outcome.status, outcome.data, len(model.calls)) == ("ok", {}, 1)

    async def test_should_land_a_later_chunk_that_answers_an_empty_object(self) -> None:
        model = Model(empty_chunks=frozenset({1}))

        outcomes = await _fire(model, _group("text1", "food.ingredients"), chunks=2)

        assert sorted((o.chunk, o.status, o.retried) for o in outcomes) == [
            (0, "ok", None),
            (1, "ok", None),
        ]

    def test_should_count_a_domain_reset_as_lost(self) -> None:
        reset = _RESET

        assert answer_lost(
            {"food": {"ingredients": None}}, _group("text1", "food.ingredients"), reset
        )


class TestWarmUpStream:
    """Dev 2026-10-13: the streamed ``extraction_text1`` came back with its deltas out of order."""

    async def test_should_land_the_warm_up_group_when_its_stream_came_back_scrambled(self) -> None:
        model = Model(scrambled=frozenset({"food.ingredients"}))

        outcomes = await _fire(
            model, _group("text1", "food.ingredients"), _group("text2", "food.steps")
        )

        assert {o.group.group_id: o.status for o in outcomes} == {"text1": "ok", "text2": "ok"}

    async def test_should_retry_the_warm_up_unstreamed(self) -> None:
        model = Model(scrambled=frozenset({"food.ingredients"}))

        await _fire(model, _group("text1", "food.ingredients"), _group("text2", "food.steps"))

        assert [c["streamed"] for c in model.of("food.ingredients")] == [True, False]

    @pytest.mark.parametrize("fault", [RuntimeError("stream broke"), None])
    async def test_should_ask_again_unstreamed_when_the_stream_raises_or_brings_nothing(
        self, fault: Step
    ) -> None:
        model = Model(script=[fault])

        outcome = await _one(model, streamed=True)

        assert (outcome.status, outcome.retried, [c["streamed"] for c in model.calls]) == (
            "ok",
            "stream",
            [True, False],
        )

    async def test_should_release_the_waiting_groups_before_the_warm_ups_retry(self) -> None:
        model = Model(scrambled=frozenset({"food.ingredients"}), silent_stream=True)
        groups = (_group("text1", "food.ingredients"), _group("text2", "food.steps"))

        await asyncio.wait_for(_fire(model, *groups), timeout=2.0)

        retry = model.of("food.ingredients")[1]
        assert model.of("food.steps")[0]["start"] < retry["end"]


class TestRelabel:
    _STEPS = request("food.steps")

    def test_should_relabel_a_data_drop_of_a_tab_that_waited_for_a_failed_field(self) -> None:
        drop = {"id": "steps", "component": "step_player", "reason": "validation_empty_list"}

        [relabeled] = relabel_group_drops([drop], [self._STEPS])

        assert relabeled["reason"] == "extraction_group_failed"

    def test_should_keep_a_policy_drops_reason(self) -> None:
        drop = {
            "id": "steps",
            "component": "step_player",
            "dataSource": "",
            "reason": "domain_max_cap",
        }

        assert relabel_group_drops([drop], [self._STEPS]) == [drop]

    def test_should_keep_the_reason_of_a_tab_whose_fields_landed(self) -> None:
        drop = {"id": "tips", "component": "info_grid", "reason": "assembler_returned_none"}

        assert relabel_group_drops([drop], [self._STEPS]) == [drop]

    def test_should_count_a_field_failed_when_any_chunks_call_failed(self) -> None:
        rows = [
            {"group": "text1", "chunk": 0, "fields": ["food.steps"], "status": "ok"},
            {"group": "text1", "chunk": 1, "fields": ["food.steps"], "status": "failed"},
        ]

        assert failed_paths(rows) == frozenset({"food.steps"})

    def test_should_leave_the_drops_as_given_on_todays_path(self) -> None:
        drop = {"id": "steps", "component": "step_player", "reason": "assembler_returned_none"}
        ctx = SimpleNamespace(plan_result=MagicMock())  # no group rows

        assert group_drops(ctx, [drop]) == [drop]  # type: ignore[arg-type]

    def test_should_not_replace_a_measured_coverage(self) -> None:
        ctx = SimpleNamespace(extraction_coverage={"ratio": 0.9, "batchesDropped": 1})

        flag_failed_groups(ctx, 3, 2)  # type: ignore[arg-type]

        assert ctx.extraction_coverage == {"ratio": 0.9, "batchesDropped": 1}
