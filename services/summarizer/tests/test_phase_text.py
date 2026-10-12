"""The text branch of phase 2 (pipeline-1min 1b.1/1b.3/1b.4/3.2, ``phases/text.py``).

transcript → ``transcript_ready`` (releases the tier probe) → the probe's
answer → ONE rendered marked transcript → plan ∥ memory → reconcile at
plan-done → ``triage_complete`` + ``meta`` → ``<video_memory>``. The plan and
memory phases are stubbed at the module seam; rendering, the probe wait, the
parallel runner and reconcile run as production code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.models.memory_types import MemoryResult, OutlineSection
from src.models.pipeline_types import PlanResult
from src.models.probe_types import TierProbe
from src.services.pipeline.phases import text as text_phase

_SEGMENTS = [
    {"text": "Hi, I'm Chris.", "start": 0.0, "duration": 3.0},
    {"text": "Today, lasagna.", "start": 21.5, "duration": 4.0},
]
_PLAN = PlanResult.model_validate(
    {"contentTags": ["food"], "primaryTag": "food", "userGoal": "Cook lasagna", "confidence": 0.9}
)
_MEMORY = MemoryResult(
    outline=[
        OutlineSection(start=0, end=120, title="intro"),
        OutlineSection(start=120, end=600, title="the sauce"),
    ],
    tldr="Lasagna without the soggy layers.",
)
_PROBE = TierProbe(domain="food", format="tutorial", has_visual_demo=True, confidence=0.9)


def _ctx(probe_task: asyncio.Future[TierProbe | None] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        video_summary_id="vsid",
        prompt_segments=list(_SEGMENTS),
        clean_text="Hi, I'm Chris. Today, lasagna.",
        source_language_code=None,
        transcript_ready=asyncio.Event(),
        tier_probe_task=probe_task,
        probe=None,
        prompt_transcript="",
        plan_result=None,
        memory=None,
        video_memory="",
        video_data=SimpleNamespace(title="BA's Best Lasagna"),
        content_format=None,
        language="en",
        is_rtl=False,
    )


async def _transcript(_ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    yield "data: transcript_ready\n\n"


class _Readers:
    """Plan + memory stand-ins that record what they saw and when."""

    def __init__(self, memory: MemoryResult | None = _MEMORY, plan: PlanResult = _PLAN) -> None:
        self.seen: dict[str, Any] = {}
        self._memory = memory
        self._plan = plan

    async def plan(self, ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
        self.seen["plan"] = (ctx.prompt_transcript, ctx.transcript_ready.is_set(), ctx.probe)
        ctx.plan_result = self._plan
        return
        yield  # pragma: no cover — makes this an async generator

    async def memory(self, ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
        self.seen["memory"] = ctx.prompt_transcript
        ctx.memory = self._memory
        return
        yield  # pragma: no cover — makes this an async generator


def _names(events: list[str]) -> list[str]:
    """Event names: ``data: name`` stubs as-is, real SSE events by their ``event`` key."""
    return [
        json.loads(e[6:])["event"] if e.startswith("data: {") else e[6:].strip() for e in events
    ]


def _payload(events: list[str], name: str) -> dict[str, Any]:
    return next(json.loads(e[6:]) for e in events if e.startswith(f'data: {{"event": "{name}"'))


async def _run(ctx: SimpleNamespace, readers: _Readers, transcript: Any = _transcript) -> list[str]:
    with (
        patch.object(text_phase, "run_phase_transcript", transcript),
        patch.object(text_phase, "run_phase_plan", readers.plan),
        patch.object(text_phase, "run_phase_memory", readers.memory),
    ):
        return [event async for event in text_phase.run_phase_text(ctx)]  # type: ignore[arg-type]


class TestRunPhaseText:
    async def test_should_stream_transcript_then_plan_events(self) -> None:
        events = await _run(_ctx(), _Readers())

        assert _names(events) == ["transcript_ready", "triage_complete", "meta"]

    async def test_should_release_the_probe_before_the_readers_start(self) -> None:
        readers = _Readers()

        await _run(_ctx(), readers)

        assert readers.seen["plan"][1] is True

    async def test_should_start_the_readers_once_the_probe_answered(self) -> None:
        probe_task: asyncio.Future[TierProbe | None] = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(0.05, probe_task.set_result, _PROBE)
        readers = _Readers()

        await _run(_ctx(probe_task), readers)

        assert readers.seen["plan"][2] == _PROBE

    async def test_should_give_plan_and_memory_the_same_rendered_transcript(self) -> None:
        readers = _Readers()

        await _run(_ctx(), readers)

        assert readers.seen["plan"][0] is readers.seen["memory"]

    async def test_should_render_the_transcript_with_markers(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers())

        assert ctx.prompt_transcript == "[0:00] Hi, I'm Chris.\n[0:21] Today, lasagna."

    async def test_should_run_plan_and_memory_concurrently(self) -> None:
        plan_started, memory_started = asyncio.Event(), asyncio.Event()

        async def plan(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            plan_started.set()
            await asyncio.wait_for(memory_started.wait(), timeout=1.0)
            ctx.plan_result = _PLAN
            yield "data: plan_done\n\n"

        async def memory(_ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            memory_started.set()
            await asyncio.wait_for(plan_started.wait(), timeout=1.0)
            return
            yield  # pragma: no cover — makes this an async generator

        readers = _Readers()
        readers.plan, readers.memory = plan, memory  # type: ignore[method-assign]

        events = await _run(_ctx(), readers)

        assert "data: plan_done\n\n" in events

    async def test_should_render_video_memory_from_plan_and_memory(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers())

        assert "outline:" in ctx.video_memory and ctx.video_memory.startswith("<video_memory>")

    async def test_should_render_the_plan_view_alone_when_memory_failed(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(memory=None))

        assert ctx.video_memory.startswith("<video_memory>") and "outline:" not in ctx.video_memory

    async def test_should_not_plan_when_the_transcript_fails(self) -> None:
        async def failing(_ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            raise RuntimeError("no transcript")
            yield  # pragma: no cover — makes this an async generator

        readers = _Readers()

        with pytest.raises(RuntimeError):
            await _run(_ctx(), readers, transcript=failing)

        assert "plan" not in readers.seen


# ─── Reconcile at plan-done (3.2) ───


def _tab(tab_id: str, component: str, data_source: str) -> dict[str, str]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": data_source}


_LEARNING_PLAN = PlanResult.model_validate(
    {
        "contentTags": ["learning"],
        "primaryTag": "learning",
        "confidence": 0.9,
        "evidence": {"is_learnable": False},
        "tabs": [
            _tab("concepts", "concept_canvas", "learning.concepts"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
            _tab("takeaways", "checklist", "learning.takeaways"),
            _tab("moments", "moment_track", "learning.timestamps"),
        ],
    }
)
_NOT_LEARNABLE = MemoryResult(evidence={"is_learnable": False})


class TestPlanDone:
    async def test_should_emit_meta_after_reconcile(self) -> None:
        events = await _run(_ctx(), _Readers(_NOT_LEARNABLE, _LEARNING_PLAN))

        labels = [t["id"] for t in _payload(events, "meta")["tabLabels"]]

        assert labels == ["concepts", "takeaways", "moments"]

    async def test_should_send_the_reconciled_tabs_in_triage_complete(self) -> None:
        events = await _run(_ctx(), _Readers(_NOT_LEARNABLE, _LEARNING_PLAN))

        tabs = [t["id"] for t in _payload(events, "triage_complete")["tabs"]]

        assert tabs == ["concepts", "takeaways", "moments"]

    async def test_should_keep_the_quiz_when_memory_is_silent(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(MemoryResult(), _LEARNING_PLAN))

        assert [t["id"] for t in ctx.plan_result.tabs][-1] == "quiz"

    async def test_should_give_later_readers_the_doubly_false_view(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(MemoryResult(), _LEARNING_PLAN))

        assert ctx.plan_result.evidence == {}

    async def test_should_keep_the_readers_own_answers_in_the_triage_dict(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(_NOT_LEARNABLE, _LEARNING_PLAN))

        assert (ctx.triage_dict["planEvidence"], ctx.triage_dict["memoryEvidence"]) == (
            {"is_learnable": False},
            {"is_learnable": False},
        )

    async def test_should_render_the_readers_own_evidence_in_video_memory(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(MemoryResult(), _LEARNING_PLAN))

        assert "is_learnable ✗" in ctx.video_memory

    async def test_should_hand_extraction_the_reconciled_tabs(self) -> None:
        ctx = _ctx()

        await _run(ctx, _Readers(_NOT_LEARNABLE, _LEARNING_PLAN))

        assert [t["id"] for t in ctx.triage.tabs] == ["concepts", "takeaways", "moments"]


class TestRenderPromptTranscript:
    def test_should_mark_the_prompt_segments(self) -> None:
        assert text_phase.render_prompt_transcript(_ctx()).startswith("[0:00] Hi")  # type: ignore[arg-type]

    def test_should_read_prompt_segments_not_the_annotated_clean_text(self) -> None:
        ctx = _ctx()
        ctx.clean_text = "Hi. [VISUAL at 0:05: a pot on the stove] Today, lasagna."

        assert "[VISUAL" not in text_phase.render_prompt_transcript(ctx)  # type: ignore[arg-type]

    def test_should_fall_back_to_clean_text_without_segments(self) -> None:
        ctx = _ctx()
        ctx.prompt_segments = []

        assert text_phase.render_prompt_transcript(ctx) == ctx.clean_text  # type: ignore[arg-type]
