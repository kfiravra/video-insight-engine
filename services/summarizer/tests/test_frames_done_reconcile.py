"""Frames-done reconcile on today's path (pipeline-1min 3.2): the orchestrator's
``visual_inject`` step decides the code tabs deferred at plan-done before
extraction starts, and announces a changed tab list with a fresh ``meta``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import settings
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.routes import pipeline_orchestration
from src.services.pipeline.phases import frames as frames_phase
from src.services.pipeline.phases import moments as moments_phase
from src.services.pipeline.phases import probe as probe_phase
from src.services.pipeline.phases.triage import frames_done_events, plan_done_events

_NO_CODE = {"has_code": False}
_CODE_FRAME = {"original_index": 0, "timestamp_sec": 5.0, "scene_type": "code", "content": "editor"}


def _tab(tab_id: str, component: str, data_source: str) -> dict[str, str]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": data_source}


def _plan() -> PlanResult:
    """A learning talk with a tech workflow tab and no code in either reader; the
    stack tab reads tech.topics, so the workflow tab has nowhere to demote to."""
    return PlanResult.model_validate(
        {
            "contentTags": ["learning", "tech"],
            "primaryTag": "learning",
            "confidence": 0.9,
            "evidence": _NO_CODE,
            "tabs": [
                _tab("stack", "info_grid", "tech.topics"),
                _tab("workflow", "step_player", "tech.setup.commands"),
                _tab("concepts", "concept_canvas", "learning.concepts"),
            ],
        }
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        youtube_id="yt1",
        video_summary_id="vsid",
        video_data=SimpleNamespace(title="Agents, explained", duration=600, chapters=None),
        language="en",
        is_rtl=False,
        clean_text="",
        frame_descriptions=[],
        scene_frames_all=[],
        transcript_data=None,
        extraction_data={},
        source_language_code=None,
        row_deleted=False,
        phase_times={},
        plan_result=None,
        memory=None,
        triage=None,
        triage_dict={},
        enrichment_data={},
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        content_format=None,
        hires_video=None,
        lowres_video=None,
    )


async def _text(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    ctx.plan_result = _plan()
    ctx.memory = MemoryResult(evidence=_NO_CODE)
    for event in plan_done_events(ctx):  # type: ignore[arg-type]
        yield event


async def _no_frames(_ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    return
    yield  # pragma: no cover — makes this an async generator


def _phase(label: str, seen: list[list[str]] | None = None) -> Callable[..., Any]:
    async def _gen(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
        if seen is not None:
            seen.append([tab["id"] for tab in ctx.triage.tabs])
        yield f"data: {label}\n\n"

    return _gen


async def _drive(ctx: SimpleNamespace, frames: Callable[..., Any]) -> tuple[list[str], list]:
    """The whole orchestration with stubbed phases; returns events + tabs extraction saw."""
    seen: list[list[str]] = []
    timer = MagicMock(elapsed=MagicMock(return_value=1.0))
    with ExitStack() as stack:
        stack.enter_context(patch.object(probe_phase, "start_tier_probe", lambda _ctx: None))
        stack.enter_context(patch.object(moments_phase, "then_provisional_moments", lambda p: p))
        for name, stub in (
            ("run_phase_metadata", _phase("metadata")),
            ("run_phase_text", _text),
            ("run_phase_frames", frames),
            ("run_phase_extraction", _phase("extraction", seen)),
            ("run_phase_assembly", _phase("assembly")),
        ):
            stack.enter_context(patch.object(pipeline_orchestration, name, stub))
        events = [
            event
            async for event in pipeline_orchestration.run_pipeline_phases(
                ctx,  # type: ignore[arg-type]
                MagicMock(),
                "vsid",
                timer,
            )
        ]
    return events, seen


def _metas(events: list[str]) -> list[list[str]]:
    payloads = [json.loads(e[6:]) for e in events if e.startswith('data: {"event": "meta"')]
    return [[label["id"] for label in p["tabLabels"]] for p in payloads]


class TestVisualInjectStep:
    async def test_should_hand_extraction_the_tabs_without_the_dropped_code_tab(self):
        _, seen = await _drive(_ctx(), _no_frames)

        assert seen == [["stack", "concepts", "takeaways"]]

    async def test_should_record_the_frames_done_decision(self):
        ctx = _ctx()

        await _drive(ctx, _no_frames)

        assert [r["action"] for r in ctx.plan_result.reconcile if r["stage"] == "frames_done"] == [
            "dropped",
            "filled",
        ]

    async def test_should_announce_the_changed_tabs_with_a_fresh_meta(self):
        events, _ = await _drive(_ctx(), _no_frames)

        assert _metas(events) == [
            ["stack", "workflow", "concepts"],
            ["stack", "concepts", "takeaways"],
        ]

    async def test_should_decide_on_the_transcript_when_the_frames_phase_failed(self):
        ctx = _ctx()
        with (
            patch.object(settings, "SCENE_EXTRACTION_ENABLED", True),
            patch.object(settings, "FRAME_TIER_ENABLED", False),
            patch.object(
                frames_phase,
                "extract_scene_keyframes",
                AsyncMock(side_effect=RuntimeError("ffmpeg")),
            ),
        ):
            _, seen = await _drive(ctx, frames_phase.run_phase_frames)

        assert seen == [["stack", "concepts", "takeaways"]]


class TestFramesDoneEvents:
    def _deferred_ctx(self) -> SimpleNamespace:
        ctx = _ctx()
        ctx.plan_result, ctx.memory = _plan(), MemoryResult(evidence=_NO_CODE)
        plan_done_events(ctx)  # type: ignore[arg-type]
        return ctx

    def test_should_send_no_meta_when_the_screen_keeps_the_code_tab(self):
        ctx = self._deferred_ctx()
        ctx.frame_descriptions = [_CODE_FRAME]

        assert frames_done_events(ctx) == []  # type: ignore[arg-type]

    def test_should_keep_the_code_tab_when_the_screen_shows_code(self):
        ctx = self._deferred_ctx()
        ctx.frame_descriptions = [_CODE_FRAME]

        frames_done_events(ctx)  # type: ignore[arg-type]

        assert [t["id"] for t in ctx.triage.tabs] == ["stack", "workflow", "concepts"]

    def test_should_do_nothing_when_no_tab_was_deferred(self):
        ctx = _ctx()
        ctx.plan_result = PlanResult()

        assert frames_done_events(ctx) == []  # type: ignore[arg-type]
