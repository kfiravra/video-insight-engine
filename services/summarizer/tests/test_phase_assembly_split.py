"""Assembly after the split extraction streamed its previews (pipeline-1min 3.6).

Every group has landed when assembly starts: the finals follow the late
group's rules again; right after the final assembly every tab the client lacks
is previewed (the overview included, the moment tabs and the quiz excluded);
the closing re-emit sends each remaining tab final once, the overview last; a
started quiz no late phase awaits is cancelled. Same patches as
``test_phase_assembly_order``: ``assemble_response`` runs as production code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.models.memory_types import MemoryResult, OutlineSection
from tests.test_phase_assembly_order import _run, _saved, _tech_ctx

_FINAL_ONLY = {"crossTabLinks", "attachments", "position", "final", "replaces"}


def _split_ctx(**previews: dict) -> Any:
    """``_tech_ctx`` after a split extraction that streamed ``previews`` (id → content)."""
    ctx = _tech_ctx()
    ctx.tab_ledger.expect_groups()
    for position, (tab_id, content) in enumerate(previews.items(), start=1):
        ctx.tab_ledger.event({"id": tab_id, **content}, position, preview=True)
    return ctx


def _tabs(events: list[tuple[str, dict]]) -> list[dict]:
    return [payload for name, payload in events if name == "tab_ready"]


def _late_group_start(events: list[tuple[str, dict]]) -> int:
    names = [name for name, _ in events]
    return min(names.index(n) for n in ("synthesis_complete", "enrichment_complete") if n in names)


async def _code_preview() -> dict:
    """The code tab's content as the split path previews it (links/attachments stripped)."""
    events = await _run(_split_ctx(), quiz=False)
    final = next(t for t in _tabs(events) if t["id"] == "code" and t["final"])
    return {k: v for k, v in final.items() if k not in _FINAL_ONLY | {"id"}}


class TestPreviewsAfterTheFinalAssembly:
    async def test_should_preview_the_overview_before_the_late_group(self) -> None:
        events = await _run(_split_ctx(), quiz=True)

        overview_at = next(
            i for i, (n, p) in enumerate(events) if n == "tab_ready" and p["id"] == "overview"
        )
        assert (events[overview_at][1]["final"], overview_at < _late_group_start(events)) == (
            False,
            True,
        )

    async def test_should_preview_a_tab_no_group_streamed(self) -> None:
        events = await _run(_split_ctx(), quiz=True)

        code = [t["final"] for t in _tabs(events) if t["id"] == "code"]
        assert code == [False, True]

    async def test_should_not_preview_a_tab_the_client_holds(self) -> None:
        events = await _run(_split_ctx(code=await _code_preview()), quiz=False)

        assert [t["final"] for t in _tabs(events) if t["id"] == "code"] == [True]

    async def test_should_preview_a_tab_the_last_groups_changed(self) -> None:
        stale = {"label": "Code", "component": "code_playground", "props": {"snippets": []}}

        events = await _run(_split_ctx(code=stale), quiz=False)

        assert [t["final"] for t in _tabs(events) if t["id"] == "code"] == [False, True]

    async def test_should_strip_links_and_attachments_from_the_previews(self) -> None:
        events = await _run(_split_ctx(), quiz=True)

        previews = [t for t in _tabs(events) if not t["final"]]
        assert [t["id"] for t in previews if {"crossTabLinks", "attachments"} & set(t)] == []


class TestClosingAfterPreviews:
    async def test_should_send_the_overview_last(self) -> None:
        events = await _run(_split_ctx(), quiz=True)

        assert (_tabs(events)[-1]["id"], _tabs(events)[-1]["final"]) == ("overview", True)

    async def test_should_send_every_saved_tab_final_exactly_once(self) -> None:
        ctx = _split_ctx()

        events = await _run(ctx, quiz=True)

        finals = sorted(t["id"] for t in _tabs(events) if t["final"])
        assert finals == sorted(t["id"] for t in _saved(ctx)["tabs"])

    async def test_should_send_the_final_tabs_at_their_saved_positions(self) -> None:
        ctx = _split_ctx()

        events = await _run(ctx, quiz=True)

        finals = {t["id"]: t["position"] for t in _tabs(events) if t["final"]}
        assert finals == {t["id"]: i for i, t in enumerate(_saved(ctx)["tabs"])}

    async def test_should_withdraw_a_per_group_tab_the_final_response_drops(self) -> None:
        ghost = {"label": "Ghost", "component": "info_grid", "props": {"items": []}}

        events = await _run(_split_ctx(ghost=ghost), quiz=False)

        complete = next(p for n, p in events if n == "complete")
        assert "ghost" not in complete["tabIds"]

    async def test_should_send_the_moments_tab_final_from_the_fill_without_a_quiz(self) -> None:
        ctx = _split_ctx()
        ctx.video_data.duration = 900
        outline = [OutlineSection(start=0, end=300, title="The stack")]
        ctx.memory = MemoryResult(
            tldr="Memory tldr.", takeaways=["m1", "m2", "m3"], outline=outline
        )

        events = await _run(ctx, quiz=False)

        assert [t["final"] for t in _tabs(events) if t["id"] == "key_moments"] == [True]


class TestStartedQuiz:
    async def test_should_cancel_a_started_quiz_no_late_phase_awaits(self) -> None:
        ctx = _split_ctx()
        ctx.quiz_task = asyncio.create_task(asyncio.sleep(60))

        await _run(ctx, quiz=False)
        await asyncio.gather(ctx.quiz_task, return_exceptions=True)

        assert ctx.quiz_task.cancelled()
