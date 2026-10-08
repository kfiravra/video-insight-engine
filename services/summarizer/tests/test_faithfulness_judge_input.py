"""What the faithfulness judge checks claims against (pipeline-1min 1c.2).

Extraction reads the transcript AND the ``<visual_annotations>`` block, so the
judge gets both: a claim grounded only in what the video shows is grounded.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.routes import pipeline_faithfulness
from src.services.pipeline import faithfulness

_BLOCK = "<visual_annotations>\n[0:12] Code editor | while low <= high:\n</visual_annotations>"


def _ctx(visual_annotations: str) -> SimpleNamespace:
    return SimpleNamespace(
        extraction_data={"key_points": [{"text": "a claim long enough to be judged"}]},
        clean_text="we loop while low is at most high",
        visual_annotations=visual_annotations,
        youtube_id="abc123",
        llm_service=MagicMock(),
    )


async def _judged_transcript(ctx: SimpleNamespace) -> str:
    """Launch the judge with the check stubbed; return the transcript it was given."""
    captured: dict[str, Any] = {}

    async def fake_check(**kwargs: Any) -> None:
        captured.update(kwargs)

    with patch.object(faithfulness, "run_faithfulness_check", new=fake_check):
        task = pipeline_faithfulness._launch_faithfulness_check(ctx)  # type: ignore[arg-type]
        assert task is not None
        await task
    return captured["transcript"]


class TestJudgeInput:
    async def test_should_append_the_visual_annotations_after_the_transcript(self):
        transcript = await _judged_transcript(_ctx(_BLOCK))

        assert transcript == f"we loop while low is at most high\n\n{_BLOCK}"

    async def test_should_pass_the_transcript_alone_when_there_are_no_annotations(self):
        transcript = await _judged_transcript(_ctx(""))

        assert transcript == "we loop while low is at most high"


class TestJudgePrompt:
    async def test_should_tell_the_judge_on_screen_annotations_count_as_support(self):
        call = AsyncMock(return_value='{"grounded": true, "evidence": ""}')
        with patch("src.utils.llm_retry.call_llm_with_retry", new=call):
            await faithfulness._judge_one(MagicMock(), _BLOCK, "the loop runs while low <= high")

        prompt = call.call_args.args[1]
        assert "a claim supported there counts as supported" in prompt
