"""Frames-done backfills tech's required code_playground when the screen shows code (R40 A1/A3).

Kfir's decision 7 covers the requirement as well as the tabs: with has_code
false in both transcript readers, the code_playground backfill is deferred to
frames-done like the code tabs. Code on screen adds it — its tech.snippets
waits for the visual group anyway — and no code records it ``not_backfilled``
under the defaultTab it would have added. Unit level, the split path (before
the visual group is cut) and today's path (before extraction).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline import extraction_groups
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.phases.frames_done import then_frames_done
from src.services.pipeline.phases.split_extraction import run_phase_split_extraction
from src.services.pipeline.phases.triage import plan_done_events
from src.services.pipeline.reconcile import reconcile_plan, resolve_deferred
from tests.test_frames_done_reconcile import _ctx as _orchestration_ctx
from tests.test_frames_done_reconcile import _drive, _metas
from tests.test_reconcile import _ids, _plan, _tab
from tests.test_split_extraction import _ctx as _split_ctx

_NO_CODE = {"has_code": False}
_CODE_FRAME = {
    "original_index": 4,
    "timestamp_sec": 40.0,
    "scene_type": "code",
    "content": "an editor",
    "text_visible": "",
    "educational_value": "shown",
}
_TAGS = ["tech", "learning"]
_TALK = ["stack", "concepts", "moments"]
# Once the code tab is backfilled the plan has four tabs, so frames-done folds the
# stack grid into its host (p1 review R11-W7: info_grid never stands alone).
_TALK_WITH_CODE = ["concepts", "moments", "code"]


def _workflow_tabs() -> list[dict]:
    """The repro: a code tab reconcile defers (setup.commands), no code_playground."""
    return [
        _tab("stack", "info_grid", "tech.topics"),
        _tab("workflow", "step_player", "tech.setup.commands"),
        _tab("moments", "moment_track", "learning.timestamps"),
    ]


def _talk_tabs() -> list[dict]:
    """No code tab planned at all."""
    return [
        _tab("stack", "info_grid", "tech.topics"),
        _tab("concepts", "flash_deck", "learning.concepts"),
        _tab("moments", "moment_track", "learning.timestamps"),
    ]


def _raw(tabs: list[dict], primary: str = "tech") -> PlanResult:
    return _plan(primary, tabs, _NO_CODE, _TAGS if primary == "tech" else [primary, "tech"])


def _reconciled(tabs: list[dict], primary: str = "tech") -> PlanResult:
    return reconcile_plan(_raw(tabs, primary), MemoryResult(evidence=_NO_CODE), None)


def _rows(plan: PlanResult, stage: str) -> list[tuple[str, str]]:
    return [(r["tabId"], r["action"]) for r in plan.reconcile if r["stage"] == stage]


def _code_tabs(plan: PlanResult) -> list[tuple[str, str]]:
    return [(t["id"], t["dataSource"]) for t in plan.tabs if t["component"] == "code_playground"]


# ─── Reconcile ───


class TestKeptCodeTab:
    def test_should_backfill_code_playground_when_the_screen_keeps_a_deferred_code_tab(self):
        plan = resolve_deferred(_reconciled(_workflow_tabs()), [_CODE_FRAME], [], None)

        assert _code_tabs(plan) == [("code", "tech.snippets")]

    def test_should_record_the_kept_tab_and_the_backfill_at_frames_done(self):
        plan = resolve_deferred(_reconciled(_workflow_tabs()), [_CODE_FRAME], [], None)

        assert _rows(plan, "frames_done") == [
            ("workflow", "kept_screen_code"),
            ("code", "backfilled"),
            ("stack", "folded"),
        ]


class TestDeferredRequirement:
    def test_should_defer_the_code_requirement_when_no_code_tab_was_planned(self):
        assert _reconciled(_talk_tabs()).deferred_requirements == ["code_playground"]

    def test_should_record_the_deferral_under_the_default_tab_it_would_add(self):
        assert _rows(_reconciled(_talk_tabs()), "plan_done") == [("code", "deferred_to_frames")]

    def test_should_not_defer_a_requirement_the_primary_does_not_gate_on_has_code(self):
        assert _reconciled(_talk_tabs(), primary="learning").deferred_requirements == []

    def test_should_backfill_it_when_the_screen_shows_code(self):
        plan = resolve_deferred(_reconciled(_talk_tabs()), [_CODE_FRAME], [], None)

        assert _ids(plan.tabs) == _TALK_WITH_CODE

    def test_should_keep_the_tabs_when_the_screen_shows_no_code(self):
        plan = resolve_deferred(_reconciled(_talk_tabs()), [], [], None)

        assert _ids(plan.tabs) == _TALK

    def test_should_record_not_backfilled_when_the_screen_shows_no_code(self):
        plan = resolve_deferred(_reconciled(_talk_tabs()), [], [], None)

        assert plan.reconcile[-1] == {
            "tabId": "code",
            "component": "code_playground",
            "from": None,
            "to": None,
            "reason": "evidence_false",
            "action": "not_backfilled",
            "stage": "frames_done",
            "evidence": {"key": "has_code", "screen": False},
        }

    def test_should_clear_the_deferral_once_decided(self):
        plan = resolve_deferred(_reconciled(_talk_tabs()), [], [], None)

        assert (plan.deferred_requirements, plan.awaits_screen) == ([], False)

    def test_should_record_another_ruled_out_requirement_once_at_plan_done(self):
        tabs = [
            _tab("steps", "step_player", "food.steps"),
            _tab("tips", "info_grid", "food.tips"),
            _tab("cli", "step_player", "tech.setup.commands"),
        ]
        no_recipe_no_code = {"has_ingredients": False, **_NO_CODE}
        raw = _plan("food", tabs, no_recipe_no_code, ["food", "tech"])
        plan = reconcile_plan(raw, MemoryResult(evidence=no_recipe_no_code), None)

        resolved = resolve_deferred(plan, [], [], None)

        assert [r["stage"] for r in resolved.reconcile if r["tabId"] == "ingredients"] == [
            "plan_done"
        ]


class TestLiveTabKeepsItsId:
    """A planned ``code`` tab demoted at frames-done keeps its id (R41 W2)."""

    def _demoted_code_tab(self) -> PlanResult:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("concepts", "flash_deck", "learning.concepts"),
            _tab("moments", "moment_track", "learning.timestamps"),
        ]
        return resolve_deferred(_reconciled(tabs), [], [], None)

    def test_should_keep_the_demoted_tab_under_its_id(self):
        assert self._demoted_code_tab().tabs[0]["id"] == "code"

    def test_should_not_name_a_live_tab_in_the_not_backfilled_row(self):
        assert _rows(self._demoted_code_tab(), "frames_done") == [
            ("code", "demoted"),
            ("", "not_backfilled"),
        ]


# ─── The split path: before the visual group is cut ───


class _AskedFields:
    """``run_group_call`` stand-in: lands every group empty, keeps what each one asked for."""

    def __init__(self) -> None:
        self.asked: dict[str, list[str]] = {}

    async def __call__(self, call: GroupCall, run: SplitRun, **_kwargs: Any) -> GroupOutcome:
        self.asked[call.group.group_id] = [f.path for f in call.group.fields]
        report = {"dropped": {}, "defaulted": 0}
        return GroupOutcome(call.group, call.chunk.index, {}, report, "ok", 1, 0)  # type: ignore[arg-type]


async def _no_frames(_ctx: Any) -> AsyncGenerator[str, None]:
    return
    yield  # pragma: no cover — makes this an async generator


async def _consume(events: AsyncGenerator[str, None]) -> list[str]:
    return [event async for event in events]


async def _split(frames: list[dict]) -> tuple[_AskedFields, list[str]]:
    """Plan-done reconcile, the member firing, then frames-done; returns calls + frames events."""
    ctx = _split_ctx(_TAGS, _talk_tabs())
    ctx.plan_result, ctx.memory = _raw(_talk_tabs()), MemoryResult(evidence=_NO_CODE)
    plan_done_events(ctx)
    ctx.plan_done.set()
    ctx.frame_descriptions = frames
    calls = _AskedFields()
    with patch.object(extraction_groups, "run_group_call", calls):
        member = asyncio.ensure_future(_consume(run_phase_split_extraction(ctx)))
        frame_events = await _consume(then_frames_done(_no_frames)(ctx))
        await member
    return calls, frame_events


class TestSplitPath:
    async def test_should_ask_the_visual_group_for_snippets_when_the_screen_shows_code(self):
        calls, _ = await _split([_CODE_FRAME])

        assert "tech.snippets" in calls.asked.get("visual", [])

    async def test_should_announce_the_backfilled_code_tab_with_a_fresh_meta(self):
        _, frame_events = await _split([_CODE_FRAME])

        assert _metas(frame_events) == [_TALK_WITH_CODE]

    async def test_should_fire_no_visual_group_when_the_screen_shows_no_code(self):
        calls, _ = await _split([])

        assert "visual" not in calls.asked


# ─── Today's path: before extraction ───


async def _talk_text(ctx: Any) -> AsyncGenerator[str, None]:
    ctx.plan_result = _raw(_talk_tabs())
    ctx.memory = MemoryResult(evidence=_NO_CODE)
    for event in plan_done_events(ctx):
        yield event


async def _code_on_screen(ctx: Any) -> AsyncGenerator[str, None]:
    ctx.frame_descriptions = [_CODE_FRAME]
    return
    yield  # pragma: no cover — makes this an async generator


class TestTodaysPath:
    async def test_should_hand_extraction_the_backfilled_code_tab(self):
        _, seen = await _drive(_orchestration_ctx(), _code_on_screen, _talk_text)

        assert seen == [_TALK_WITH_CODE]

    async def test_should_announce_it_with_a_fresh_meta_before_extraction(self):
        events, _ = await _drive(_orchestration_ctx(), _code_on_screen, _talk_text)

        assert _metas(events) == [_TALK, _TALK_WITH_CODE]

    async def test_should_record_not_backfilled_when_the_screen_shows_no_code(self):
        ctx = _orchestration_ctx()

        await _drive(ctx, _no_frames, _talk_text)

        assert _rows(ctx.plan_result, "frames_done") == [("code", "not_backfilled")]
