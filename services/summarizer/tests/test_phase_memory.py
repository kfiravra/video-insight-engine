"""The memory phase (pipeline-1min 1b.3/1b.5, ``phases/memory.py``).

It reads the run's marked transcript (the plan's exact string) plus the
video's metadata and leaves the answer — or ``None`` — on ``ctx.memory``.
When memory has a tldr or takeaways it emits the early ``synthesis_complete``.
``run_memory`` (the LLM boundary) is patched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.memory_types import MemoryInput, MemoryResult
from src.services.pipeline.phases import memory as memory_phase

_MEMORY = MemoryResult(tldr="Lasagna without the soggy layers.")


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        video_summary_id="vsid",
        video_data=SimpleNamespace(
            title="BA's Best Lasagna",
            channel="Bon Appétit",
            duration=1001,
            description="The recipe, start to finish.",
        ),
        prompt_transcript="[0:00] Hi, I'm Chris.",
        prompt_segments=[{"text": "Hi, I'm Chris.", "start": 0.0, "duration": 2.0}],
        llm_service=MagicMock(),
        memory=None,
    )


async def _run(ctx: SimpleNamespace, result: MemoryResult | None) -> AsyncMock:
    run = AsyncMock(return_value=result)
    with patch.object(memory_phase, "run_memory", run):
        _ = [event async for event in memory_phase.run_phase_memory(ctx)]  # type: ignore[arg-type]
    return run


async def test_should_read_the_runs_marked_transcript_and_metadata() -> None:
    run = await _run(_ctx(), _MEMORY)

    assert run.call_args.args[1] == MemoryInput(
        title="BA's Best Lasagna",
        channel="Bon Appétit",
        duration=1001,
        description="The recipe, start to finish.",
        transcript="[0:00] Hi, I'm Chris.",
    )


async def test_should_keep_the_answer_on_the_context() -> None:
    ctx = _ctx()

    await _run(ctx, _MEMORY)

    assert ctx.memory == _MEMORY


async def test_should_leave_no_memory_when_the_call_failed() -> None:
    ctx = _ctx()

    await _run(ctx, None)

    assert ctx.memory is None


async def test_should_skip_the_call_when_the_transcript_has_no_timed_segments() -> None:
    """Regression: a metadata-only video got an invented outline + takeaways."""
    ctx = _ctx()
    ctx.prompt_segments = []
    ctx.memory = _MEMORY

    run = await _run(ctx, _MEMORY)

    assert (run.await_count, ctx.memory) == (0, None)


def _events(chunks: list[str]) -> list[dict]:
    return [json.loads(c.removeprefix("data: ")) for c in chunks]


async def _emitted(result: MemoryResult | None) -> list[dict]:
    with patch.object(memory_phase, "run_memory", AsyncMock(return_value=result)):
        chunks = [event async for event in memory_phase.run_phase_memory(_ctx())]  # type: ignore[arg-type]
    return _events(chunks)


async def test_should_emit_the_early_hero_when_memory_has_tldr_and_takeaways() -> None:
    memory = MemoryResult(tldr="Lasagna, no soggy layers.", takeaways=["a", "b", "c"])

    events = await _emitted(memory)

    assert events == [
        {
            "event": "synthesis_complete",
            "tldr": "Lasagna, no soggy layers.",
            "keyTakeaways": ["a", "b", "c"],
        }
    ]


async def test_should_emit_the_hero_with_the_tldr_alone() -> None:
    events = await _emitted(MemoryResult(tldr="Lasagna, no soggy layers."))

    assert [(e["tldr"], e["keyTakeaways"]) for e in events] == [("Lasagna, no soggy layers.", [])]


async def test_should_emit_nothing_when_memory_failed() -> None:
    assert await _emitted(None) == []


async def test_should_emit_nothing_when_memory_has_neither_tldr_nor_takeaways() -> None:
    assert await _emitted(MemoryResult(evidence={"has_steps": True})) == []
