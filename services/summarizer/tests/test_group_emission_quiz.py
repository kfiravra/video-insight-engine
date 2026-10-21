"""The split path's early quiz (pipeline-1min 3.6, B.6): when ``GroupLanding`` starts it.

Once every text group holding a field of a ``quizDomains`` domain has landed —
the visual group when no text group holds one — over the extraction merged so
far; a strip-only quiz (no quiz tab) also waits until frames-done can no longer
drop its host (a deferred code tab). A split member that fails or is
cancelled after starting it stops it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.phases import split_extraction
from tests import test_split_extraction as split_member
from tests.test_group_emission import (
    _CONCEPTS,
    _FIELDS,
    _TABS,
    _TAKEAWAYS,
    _TIMESTAMPS,
    _ctx,
    _field,
    _group,
    _outcome,
    _Run,
)

_SNIPPETS = _field("tech.snippets", "code", visual=True)
_INGREDIENTS = _field("food.ingredients", "ingredients")
_TECH_FOOD_TABS = [
    {"id": "ingredients", "label": "Ingredients", "component": "checklist", "dataSource": "food.ingredients"},
    {"id": "code", "label": "Code", "component": "code_playground", "dataSource": "tech.snippets"},
    {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"},
]  # fmt: skip
# No quiz tab: a quick_quiz strip on the sparse concepts tab is the only place it shows.
_STRIP_ONLY_TABS = [
    {"id": "concepts", "label": "Concepts", "component": "flash_deck",
     "dataSource": "learning.concepts", "brief": {"expect": 3}},
    {"id": "code", "label": "Code", "component": "code_playground", "dataSource": "tech.snippets"},
]  # fmt: skip


@pytest.fixture(autouse=True)
def _flag_off_quiz(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests time the flag-off quiz call (``enrich_quiz``); quiz v2 rides the same
    start and cancel and is covered by ``test_p2_1b_quiz_v2`` (output-quality 2.1b)."""
    monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)


@pytest.fixture
async def quiz_call() -> AsyncIterator[AsyncMock]:
    """The quiz LLM boundary; the quiz tasks a test started are cancelled and gathered."""
    with patch.object(enrichment_phase, "enrich_quiz", AsyncMock(return_value=None)) as mock:
        yield mock
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith("quiz_")]
        for task in started:
            task.cancel()
        await asyncio.gather(*started, return_exceptions=True)


class TestQuizStart:
    async def test_should_not_start_the_quiz_before_its_owning_groups_landed(
        self, quiz_call
    ) -> None:
        ctx = _ctx()
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        run = _Run(ctx, [text1, text2])

        run.land(_outcome(text1))

        assert ctx.quiz_task is None

    async def test_should_start_the_quiz_when_its_owning_text_groups_landed(
        self, quiz_call
    ) -> None:
        ctx = _ctx()
        text1, text2 = _group("text1", _CONCEPTS), _group("text2", _TAKEAWAYS)
        run = _Run(ctx, [text1, text2])
        run.land(_outcome(text1))

        run.land(_outcome(text2))

        assert ctx.quiz_task is not None

    async def test_should_not_wait_for_the_visual_group(self, quiz_call) -> None:
        ctx = _ctx()
        text1, visual = _group("text1", *_FIELDS), _group("visual", _SNIPPETS)
        run = _Run(ctx, [text1, visual])

        run.land(_outcome(text1))

        assert ctx.quiz_task is not None

    async def test_should_hand_the_quiz_the_extraction_merged_so_far(self, quiz_call) -> None:
        ctx = _ctx()
        text1, text2 = _group("text1", _CONCEPTS, _TAKEAWAYS), _group("text2", _TIMESTAMPS)
        run = _Run(ctx, [text1, text2])
        run.land(_outcome(text1))
        run.land(_outcome(text2))
        assert ctx.quiz_task is not None

        await ctx.quiz_task

        assert set(quiz_call.await_args.kwargs["extraction_data"]["learning"]) == {
            "concepts",
            "takeaways",
            "timestamps",
        }

    async def test_should_let_the_visual_group_own_the_quiz_when_no_text_group_holds_its_domain(
        self, quiz_call
    ) -> None:
        ctx = _ctx(_TECH_FOOD_TABS, tags=("tech", "food"))
        text1, visual = _group("text1", _INGREDIENTS), _group("visual", _SNIPPETS)
        run = _Run(ctx, [text1, visual])
        run.land(_outcome(text1), (_INGREDIENTS, _SNIPPETS))
        before = ctx.quiz_task

        run.land(_outcome(visual), (_INGREDIENTS, _SNIPPETS))

        assert (before, ctx.quiz_task is not None) == (None, True)

    async def test_should_not_start_the_quiz_when_the_plan_cannot_use_one(self, quiz_call) -> None:
        tabs = [t for t in _TABS if t["id"] != "quiz"]
        ctx = _ctx(tabs, tags=("food",))
        text1 = _group("text1", _INGREDIENTS)
        run = _Run(ctx, [text1])

        run.land(_outcome(text1), (_INGREDIENTS,))

        assert ctx.quiz_task is None


def _strip_only(
    *, deferred: bool, requirement: bool = False, tabs: list[dict] | None = None
) -> tuple:
    ctx = _ctx(tabs or _STRIP_ONLY_TABS, tags=("learning", "tech"))
    assert ctx.plan_result is not None
    ctx.plan_result.deferred_tabs = ["code"] if deferred else []
    ctx.plan_result.deferred_requirements = ["code_playground"] if requirement else []
    text1, visual = _group("text1", _CONCEPTS), _group("visual", _SNIPPETS)
    return ctx, _Run(ctx, [text1, visual]), text1, visual


def _full_plan() -> list[dict]:
    """Six tabs: a frames-done backfill would trim one — maybe the sparse host."""
    extra = [
        {
            "id": f"tip{i}",
            "label": f"Tip {i}",
            "component": "info_grid",
            "dataSource": "tech.topics",
        }
        for i in range(4)
    ]
    return [*_STRIP_ONLY_TABS, *extra]


class TestStripOnlyQuiz:
    async def test_should_hold_a_strip_only_quiz_while_frames_done_may_drop_its_host(
        self, quiz_call
    ) -> None:
        ctx, run, text1, _ = _strip_only(deferred=True)

        run.land(_outcome(text1), (_CONCEPTS, _SNIPPETS))

        assert ctx.quiz_task is None

    async def test_should_start_it_once_frames_are_done(self, quiz_call) -> None:
        ctx, run, text1, visual = _strip_only(deferred=True)
        run.land(_outcome(text1), (_CONCEPTS, _SNIPPETS))
        ctx.frames_done.set()

        run.land(_outcome(visual), (_CONCEPTS, _SNIPPETS))

        assert ctx.quiz_task is not None

    async def test_should_start_it_at_once_when_no_tab_is_deferred(self, quiz_call) -> None:
        ctx, run, text1, _ = _strip_only(deferred=False)

        run.land(_outcome(text1), (_CONCEPTS, _SNIPPETS))

        assert ctx.quiz_task is not None

    async def test_should_start_it_at_once_when_only_the_code_requirement_is_deferred(
        self, quiz_call
    ) -> None:
        ctx, run, text1, _ = _strip_only(deferred=False, requirement=True)

        run.land(_outcome(text1), (_CONCEPTS, _SNIPPETS))

        assert ctx.quiz_task is not None

    async def test_should_hold_it_when_a_deferred_backfill_would_trim_a_full_plan(
        self, quiz_call
    ) -> None:
        ctx, run, text1, _ = _strip_only(deferred=False, requirement=True, tabs=_full_plan())

        run.land(_outcome(text1), (_CONCEPTS, _SNIPPETS))

        assert ctx.quiz_task is None


# ─── The member stops the quiz it started ───

_QUIZ_PLAN_TABS = [
    {"id": "stack", "label": "Stack", "component": "info_grid", "dataSource": "tech.topics"},
    {"id": "code", "label": "Code", "component": "code_playground", "dataSource": "tech.snippets"},
    {"id": "concepts", "label": "Concepts", "component": "flash_deck", "dataSource": "learning.concepts"},
    {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"},
]  # fmt: skip


async def _forever(*_args: object, **_kwargs: object) -> None:
    await asyncio.sleep(3600)


async def _quiz_started(ctx: PipelineContext) -> None:
    for _ in range(200):
        if ctx.quiz_task is not None:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the quiz never started")


async def _stopped_quiz(ctx: PipelineContext, member: asyncio.Task[list[str]]) -> bool:
    await asyncio.gather(member, return_exceptions=True)
    assert ctx.quiz_task is not None
    await asyncio.gather(ctx.quiz_task, return_exceptions=True)
    return ctx.quiz_task.cancelled()


class TestMemberStopsItsQuiz:
    async def test_should_cancel_the_quiz_when_the_member_is_cancelled(self) -> None:
        ctx = split_member._ctx(["tech", "learning"], _QUIZ_PLAN_TABS)
        ctx.plan_done.set()  # frames are not done: the visual group keeps the member waiting

        with patch.object(enrichment_phase, "enrich_quiz", AsyncMock(side_effect=_forever)):
            member = asyncio.create_task(split_member._drain(ctx, split_member._Groups()))
            await _quiz_started(ctx)
            member.cancel()
            cancelled = await _stopped_quiz(ctx, member)

        assert cancelled

    async def test_should_cancel_the_quiz_when_the_member_raises(self) -> None:
        ctx = split_member._done(split_member._ctx(["tech", "learning"], _QUIZ_PLAN_TABS))

        with (
            patch.object(enrichment_phase, "enrich_quiz", AsyncMock(side_effect=_forever)),
            patch.object(split_extraction, "_complete", AsyncMock(side_effect=RuntimeError)),
        ):
            member = asyncio.create_task(split_member._drain(ctx, split_member._Groups()))
            cancelled = await _stopped_quiz(ctx, member)

        assert cancelled
