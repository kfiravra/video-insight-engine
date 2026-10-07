"""The memory phase (pipeline-1min 1b.3, ``phases/memory.py``).

It reads the run's marked transcript (the plan's exact string) plus the
video's metadata and leaves the answer — or ``None`` — on ``ctx.memory``.
``run_memory`` (the LLM boundary) is patched.
"""

from __future__ import annotations

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
