"""The runner's bounded wait for a non-English RAG transcript task (review fix R3-1).

The transcript task of a non-English video makes its translation calls tens of
seconds after the save. ``run_pipeline_phases`` waits for it, at most
``RAG_TRANSCRIPT_WAIT_S``, once the phases finished (``done`` already out) and
before it persists ``pipeline.timing``, so those calls are in the document.
A failing run never waits: its error event follows the runner's ``finally``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.pipeline_types import PlanResult
from src.routes import pipeline_orchestration
from src.services.pipeline.phases import qdrant_index


def _ctx() -> SimpleNamespace:
    """The fields the runner reads around the (patched) phases."""
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
        plan_result=PlanResult(),
        enrichment_data={},
        triage=SimpleNamespace(tabs=[]),
        assembled_tabs=[],
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        content_format=None,
    )


async def _one(*_a: object, **_k: object) -> AsyncGenerator[str, None]:
    yield "data: x\n\n"


async def _drive(ctx: SimpleNamespace, repository: MagicMock, assembly: object) -> None:
    """Run ``run_pipeline_phases`` to its end with every phase but assembly stubbed."""
    timer = MagicMock(elapsed=MagicMock(return_value=1.0))
    with ExitStack() as stack:
        stack.enter_context(patch.object(pipeline_orchestration, "run_phase_metadata", _one))
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_parallel_phases", lambda _p, _c: _one())
        )
        stack.enter_context(patch.object(pipeline_orchestration, "run_phase_extraction", _one))
        stack.enter_context(patch.object(pipeline_orchestration, "run_phase_assembly", assembly))
        async for _ in pipeline_orchestration.run_pipeline_phases(ctx, repository, "vsid", timer):
            pass


class TestRagTranscriptWait:
    async def test_should_persist_the_timing_after_the_transcript_task_finished(self) -> None:
        order: list[str] = []
        repository = MagicMock()
        repository.set_pipeline_timing.side_effect = lambda *_a: order.append("timing")

        async def _translate() -> None:
            await asyncio.sleep(0.05)
            order.append("rag")

        async def _assembly(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            ctx.rag_transcript_task = asyncio.create_task(_translate())
            yield "data: done\n\n"

        await _drive(_ctx(), repository, _assembly)

        assert order == ["rag", "timing"]

    async def test_should_persist_the_timing_at_the_bound_when_the_task_runs_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(qdrant_index, "RAG_TRANSCRIPT_WAIT_S", 0.01)
        release = asyncio.Event()
        repository = MagicMock()
        tasks: list[asyncio.Task[bool]] = []

        async def _assembly(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            ctx.rag_transcript_task = asyncio.create_task(release.wait())
            tasks.append(ctx.rag_transcript_task)
            yield "data: done\n\n"

        await _drive(_ctx(), repository, _assembly)
        running = not tasks[0].done()
        release.set()
        await tasks[0]

        assert (running, repository.set_pipeline_timing.call_count) == (True, 1)

    async def test_should_not_wait_when_the_run_failed(self) -> None:
        release = asyncio.Event()
        repository = MagicMock()
        tasks: list[asyncio.Task[bool]] = []

        async def _assembly(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
            ctx.rag_transcript_task = asyncio.create_task(release.wait())
            tasks.append(ctx.rag_transcript_task)
            yield "data: x\n\n"
            raise RuntimeError("translation exploded")

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(_drive(_ctx(), repository, _assembly), timeout=5.0)
        persisted_while_running = not tasks[0].done()
        release.set()
        await tasks[0]

        assert (persisted_while_running, repository.set_pipeline_timing.call_count) == (True, 1)
