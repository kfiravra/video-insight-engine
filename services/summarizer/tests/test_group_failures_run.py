"""Failure paths of the split extraction over a whole run (pipeline-1min 3.8).

A failed group's fields count as landed; assembly drops the tabs they fed
through today's fallbacks — recorded as ``extraction_group_failed`` — or a
sibling field another group extracted keeps them; the run is degraded. Every
group failing is today's error; a failed plan call requests the fallback
plan's default fields. The split member and assembly run as production code
over the ``Model`` stub (``test_group_failures``) and the assembly patches of
``test_phase_assembly_order``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.models.schemas import ErrorCode, ProcessingStatus
from src.services.pipeline import extraction_groups, plan
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.field_requests import FieldRequest, reads_extraction
from src.services.pipeline.group_failures import EXTRACTION_GROUP_FAILED
from src.services.pipeline.phases import assembly as assembly_phase
from src.services.pipeline.phases.split_extraction import run_phase_split_extraction
from src.services.pipeline.phases.triage import plan_done_events, run_phase_plan
from src.services.pipeline.pipeline_helpers import PipelineTimer
from src.services.pipeline.triage import TriageResult
from src.services.video.youtube import Chapter
from src.shared_config.domain_config import build_fallback_tabs, valid_content_tags
from tests.test_group_failures import SCRAMBLED, Model, patched
from tests.test_phase_assembly_order import _run, _saved

_FOOD_TABS = [
    {"id": "ingredients", "label": "Ingredients", "component": "checklist",
     "dataSource": "food.ingredients"},
    {"id": "steps", "label": "Steps", "component": "step_player", "dataSource": "food.steps"},
]  # fmt: skip


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    """Prod's slots; the save's status mirror to the api (a background HTTP call) stubbed."""
    with (
        patch.object(settings, "EXTRACTION_PARALLEL_BATCHES", 6),
        patch.object(assembly_phase, "send_video_status_background", MagicMock()),
    ):
        yield


def _food_ctx(tags: tuple[str, ...] = ("food",), tabs: list[dict] | None = None) -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    tabs = tabs if tabs is not None else _FOOD_TABS
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": list(tags), "primaryTag": "food", "tabs": tabs, "confidence": 0.9}
    )
    ctx.triage = TriageResult(content_tags=list(tags), primary_tag="food", tabs=tabs)
    ctx.triage_dict = ctx.plan_result.to_triage_dict(None)
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="Omelette", channel="A cook", description="", duration=600, chapters=None,
        thumbnail_url="https://example/t.jpg", context=None,
    )  # fmt: skip
    ctx.prompt_transcript = "[0:00] whisk the eggs"
    ctx.memory = MemoryResult(tldr="Eggs, whisked.", takeaways=["whisk", "fold", "serve"])
    ctx.plan_done.set()
    ctx.frames_done.set()
    return ctx


def _payloads(chunks: list[str]) -> list[dict[str, Any]]:
    return [json.loads(c.removeprefix("data: ")) for c in chunks]


async def _member(ctx: PipelineContext, model: Model) -> list[dict[str, Any]]:
    with patched(model):
        return _payloads([event async for event in run_phase_split_extraction(ctx)])


async def _food_run(
    model: Model, tags: tuple[str, ...] = ("food",), tabs: list[dict] | None = None
) -> SimpleNamespace:
    return await _whole_run(_food_ctx(tags, tabs), model)


async def _whole_run(ctx: PipelineContext, model: Model) -> SimpleNamespace:
    """The split member, then assembly and the save (run after an error too, as today)."""
    streamed = await _member(ctx, model)
    events = await _run(ctx, quiz=False)
    saved = _saved(ctx)
    return SimpleNamespace(model=model, streamed=streamed, events=events, saved=saved)


def _dropped(run: SimpleNamespace) -> list[tuple[str, str]]:
    return [(d["id"], d["reason"]) for d in run.saved["pipeline"]["assembly"]["droppedTabs"]]


def _saved_ids(run: SimpleNamespace) -> list[str]:
    return [tab["id"] for tab in run.saved["tabs"]]


# ─── One group fails twice ───


@pytest.fixture
async def steps_failed() -> SimpleNamespace:
    return await _food_run(Model(garbled=frozenset({"food.steps"})))


class TestOneGroupFailsTwice:
    async def test_should_ship_the_other_groups_tabs(self, steps_failed: SimpleNamespace) -> None:
        assert ("ingredients" in _saved_ids(steps_failed), "steps" in _saved_ids(steps_failed)) == (
            True,
            False,
        )

    async def test_should_record_extraction_group_failed(
        self, steps_failed: SimpleNamespace
    ) -> None:
        assert _dropped(steps_failed) == [("steps", EXTRACTION_GROUP_FAILED)]

    async def test_should_keep_assemblys_own_reason_in_the_detail(
        self, steps_failed: SimpleNamespace
    ) -> None:
        [drop] = steps_failed.saved["pipeline"]["assembly"]["droppedTabs"]

        assert drop["detail"] == "food.steps failed — assembler_returned_none (data was None)"

    async def test_should_ask_the_failing_group_twice(self, steps_failed: SimpleNamespace) -> None:
        assert len(steps_failed.model.of("food.steps")) == 2

    async def test_should_record_the_retry_in_the_group_rows(
        self, steps_failed: SimpleNamespace
    ) -> None:
        rows = steps_failed.saved["pipeline"]["extraction"]["_groups"]

        failed = [(r["fields"], r.get("retried")) for r in rows if r["status"] == "failed"]
        assert failed == [(["food.steps"], "parse")]

    async def test_should_mark_the_run_degraded(self, steps_failed: SimpleNamespace) -> None:
        complete = next(p for n, p in steps_failed.events if n == "complete")

        assert (complete["degraded"], steps_failed.saved["meta"]["degraded"]) == (True, True)

    async def test_should_never_stream_the_failed_groups_tab(
        self, steps_failed: SimpleNamespace
    ) -> None:
        tab_ids = [p["id"] for p in steps_failed.streamed if p["event"] == "tab_ready"]
        tab_ids += [p["id"] for n, p in steps_failed.events if n == "tab_ready"]

        assert "steps" not in tab_ids


# ─── Fallbacks and side reads ───


@pytest.fixture
async def sibling_landed() -> SimpleNamespace:
    """food.steps fails; its registry sibling project.steps lands in the project group."""
    return await _food_run(Model(garbled=frozenset({"food.steps"})), tags=("food", "project"))


class TestSiblingLanded:
    async def test_should_ship_the_tab_from_its_sibling(
        self, sibling_landed: SimpleNamespace
    ) -> None:
        assert "steps" in _saved_ids(sibling_landed)

    async def test_should_drop_no_tab(self, sibling_landed: SimpleNamespace) -> None:
        assert _dropped(sibling_landed) == []

    async def test_should_still_mark_the_run_degraded(
        self, sibling_landed: SimpleNamespace
    ) -> None:
        assert sibling_landed.saved.get("degraded") is True


@pytest.fixture
async def side_read_failed() -> SimpleNamespace:
    """Only the overview's side read (food.tips) fails."""
    return await _food_run(Model(garbled=frozenset({"food.tips"})))


class TestSideReadFailed:
    async def test_should_ship_every_planned_tab(self, side_read_failed: SimpleNamespace) -> None:
        assert {"ingredients", "steps"} <= set(_saved_ids(side_read_failed))

    async def test_should_drop_no_tab(self, side_read_failed: SimpleNamespace) -> None:
        assert _dropped(side_read_failed) == []

    async def test_should_mark_the_run_degraded(self, side_read_failed: SimpleNamespace) -> None:
        assert side_read_failed.saved.get("degraded") is True


# ─── A moment tab kept on an inactive domain ───


# The plan-parse guard kept moments ← learning.timestamps on a run that did not tag
# learning (dev run T1dQhQAm8Tc, 2026-10-13); the model would answer it correctly.
_KEPT_MOMENTS_TABS = [
    *_FOOD_TABS,
    {"id": "moments", "label": "Moments", "component": "moment_track",
     "dataSource": "learning.timestamps"},
]  # fmt: skip


@pytest.fixture
async def kept_moments() -> SimpleNamespace:
    return await _food_run(Model(), tabs=_KEPT_MOMENTS_TABS)


@pytest.fixture
async def kept_moments_with_chapters() -> SimpleNamespace:
    """The same run on a video with YouTube chapters — the moment fallback's second rung."""
    ctx = _food_ctx(tabs=_KEPT_MOMENTS_TABS)
    assert ctx.video_data is not None
    ctx.video_data.chapters = [Chapter(0, 240, "Whisk"), Chapter(240, 600, "Fold and serve")]
    return await _whole_run(ctx, Model())


class TestKeptMomentTabOnAnInactiveDomain:
    async def test_should_fire_no_group_for_the_inactive_domain(
        self, kept_moments: SimpleNamespace
    ) -> None:
        asked = {path for call in kept_moments.model.calls for path in call["paths"]}

        assert {path for path in asked if path.startswith("learning.")} == set()

    async def test_should_record_no_failed_group(self, kept_moments: SimpleNamespace) -> None:
        rows = kept_moments.saved["pipeline"]["extraction"]["_groups"]

        assert {r["status"] for r in rows} == {"ok"}

    async def test_should_not_mark_the_run_degraded(self, kept_moments: SimpleNamespace) -> None:
        complete = next(p for n, p in kept_moments.events if n == "complete")

        assert (complete["degraded"], bool(kept_moments.saved.get("degraded"))) == (False, False)

    async def test_should_ship_the_tab_from_the_moment_fallback_when_the_video_has_chapters(
        self, kept_moments_with_chapters: SimpleNamespace
    ) -> None:
        run = kept_moments_with_chapters
        dropped_ids = [tab_id for tab_id, _ in _dropped(run)]

        assert ("moments" in _saved_ids(run), "moments" in dropped_ids) == (True, False)


# ─── Every group fails ───


@pytest.fixture
async def all_failed() -> SimpleNamespace:
    return await _food_run(Model(reply=SCRAMBLED))


class TestEveryGroupFails:
    async def test_should_fail_the_run_as_today_when_every_group_fails(
        self, all_failed: SimpleNamespace
    ) -> None:
        errors = [p for p in all_failed.streamed if p["event"] == "error"]

        assert errors == [
            {"event": "error", "message": "Extraction failed to produce data", "code": "LLM_ERROR"}
        ]

    async def test_should_mark_the_row_failed_with_llm_error(self) -> None:
        ctx = _food_ctx()

        await _member(ctx, Model(reply=SCRAMBLED))

        assert ctx.repository.update_status.call_args.args == (  # type: ignore[attr-defined]
            "vsid", ProcessingStatus.FAILED, "Extraction failed", ErrorCode.LLM_ERROR,
        )  # fmt: skip

    async def test_should_ask_every_group_twice_before_failing(
        self, all_failed: SimpleNamespace
    ) -> None:
        assert [
            len(all_failed.model.of(path))
            for path in ("food.ingredients", "food.steps", "food.tips")
        ] == [2, 2, 2]

    async def test_should_keep_the_group_rows(self, all_failed: SimpleNamespace) -> None:
        rows = all_failed.saved["pipeline"]["extraction"]["_groups"]

        assert {(r["status"], r.get("retried")) for r in rows} == {("failed", "parse")}

    async def test_should_relabel_the_tabs_it_cost(self, all_failed: SimpleNamespace) -> None:
        assert _dropped(all_failed) == [
            ("ingredients", EXTRACTION_GROUP_FAILED),
            ("steps", EXTRACTION_GROUP_FAILED),
        ]

    async def test_should_save_the_run_degraded(self, all_failed: SimpleNamespace) -> None:
        assert all_failed.saved.get("degraded") is True


# ─── The plan call fails ───


def _plan_ctx(tag: str) -> PipelineContext:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="A video", channel="c", description="", duration=600,
        context=SimpleNamespace(category=tag, tags=[]),
    )  # fmt: skip
    ctx.prompt_transcript = "[0:00] hello"
    ctx.memory = MemoryResult(tldr="t", takeaways=["a"], evidence={})
    ctx.frames_done.set()
    return ctx


async def _after_failed_plan(tag: str) -> tuple[PipelineContext, list[FieldRequest]]:
    """The plan call fails, plan-done reconciles, then the field groups fire (recorded)."""
    ctx, fired = _plan_ctx(tag), []

    async def record(call: GroupCall, _run: SplitRun, **_kwargs: Any) -> GroupOutcome:
        fired.extend(call.group.fields)
        return GroupOutcome(call.group, 0, {}, {"dropped": {}, "defaulted": 0}, "ok", 1, 0)

    with patch.object(plan, "call_llm_with_retry", AsyncMock(return_value=None)):
        _ = [e async for e in run_phase_plan(ctx)]
    plan_done_events(ctx)
    ctx.plan_done.set()
    with patch.object(extraction_groups, "run_group_call", record):
        _ = [e async for e in run_phase_split_extraction(ctx)]
    return ctx, fired


@pytest.mark.parametrize("tag", sorted(valid_content_tags()))
class TestPlanFailure:
    async def test_should_request_the_fallback_plans_default_fields_when_the_plan_call_fails(
        self, tag: str
    ) -> None:
        _, fired = await _after_failed_plan(tag)

        defaults = {t["dataSource"] for t in build_fallback_tabs(tag) if reads_extraction(t)}
        assert {f.path for f in fired if f.reason == "planned"} == defaults

    async def test_should_take_no_reconcile_action_on_the_fallback_plan(self, tag: str) -> None:
        ctx, _ = await _after_failed_plan(tag)

        assert ctx.plan_result is not None and ctx.plan_result.reconcile == []
