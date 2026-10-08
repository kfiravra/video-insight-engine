"""Frame annotations never reach the tier probe, plan or memory (pipeline-1min 1c.2).

The frames-done step only sets ``ctx.visual_annotations``; the readers'
inputs (``clean_text`` for the probe, the rendered ``prompt_transcript`` for
plan and memory) stay speech-only whatever order the branches finish in.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.phases import probe as probe_phase
from src.services.pipeline.phases import triage as plan_phase
from src.services.pipeline.phases.memory import memory_input
from src.services.pipeline.phases.text import render_prompt_transcript
from src.services.pipeline.visual_annotations import render_visual_annotations

_CAPTION = "Zebra-striped whiteboard sketch"
_OCR = "Quokka slide title text"
_SEGMENTS = [
    {"text": "we start with the dough", "start": 0.0, "duration": 4.0},
    {"text": "then we fold it twice", "start": 21.0, "duration": 4.0},
]


def _ctx(segments: list[dict[str, Any]]) -> PipelineContext:
    ctx = PipelineContext(
        video_summary_id="vsid",
        youtube_id="abc123",
        entry={},
        repository=MagicMock(),
        llm_service=MagicMock(),
        timer=MagicMock(),
    )
    ctx.video_data = SimpleNamespace(  # type: ignore[assignment]
        title="Folded dough",
        channel="Kitchen",
        description="",
        duration=60,
        context=SimpleNamespace(youtube_category="Howto & Style", tags=[], category=None),
    )
    ctx.prompt_segments = segments
    ctx.clean_text = "we start with the dough then we fold it twice"
    ctx.frame_descriptions = [
        {"timestamp_sec": 10, "content": _CAPTION, "scene_type": "whiteboard", "original_index": 1}
    ]
    ctx.scene_frames_all = [{"index": 2, "timestamp": 30, "ocr_text": _OCR}]
    # The frames-done step (D4's call site in the orchestration).
    ctx.visual_annotations = render_visual_annotations(ctx.frame_descriptions, ctx.scene_frames_all)
    return ctx


def _annotated(text: str) -> bool:
    return _CAPTION in text or _OCR in text


class TestFramesDoneStep:
    def test_should_put_both_caption_and_ocr_into_the_annotations_block(self):
        ctx = _ctx(_SEGMENTS)

        assert _CAPTION in ctx.visual_annotations and _OCR in ctx.visual_annotations

    def test_should_leave_clean_text_speech_only(self):
        ctx = _ctx(_SEGMENTS)

        assert ctx.clean_text == "we start with the dough then we fold it twice"


class TestReadersSeeNoAnnotations:
    def test_should_render_the_plan_and_memory_transcript_without_annotations(self):
        ctx = _ctx(_SEGMENTS)

        assert not _annotated(render_prompt_transcript(ctx))

    def test_should_fall_back_to_annotation_free_clean_text_without_segments(self):
        ctx = _ctx([])

        assert not _annotated(render_prompt_transcript(ctx))

    def test_should_give_memory_an_annotation_free_transcript(self):
        ctx = _ctx(_SEGMENTS)
        ctx.prompt_transcript = render_prompt_transcript(ctx)

        assert not _annotated(memory_input(ctx).transcript)

    async def test_should_give_the_plan_an_annotation_free_transcript(self):
        ctx = _ctx(_SEGMENTS)
        ctx.prompt_transcript = render_prompt_transcript(ctx)
        run_plan = AsyncMock(return_value=PlanResult.model_validate({"confidence": 0.9}))
        with (
            patch.object(plan_phase, "check_override", return_value=None),
            patch.object(plan_phase, "run_plan", run_plan),
        ):
            _ = [event async for event in plan_phase.run_phase_plan(ctx)]

        assert not _annotated(run_plan.call_args.kwargs["transcript"])

    async def test_should_give_the_tier_probe_annotation_free_windows(self):
        ctx = _ctx(_SEGMENTS)
        run_tier_probe = AsyncMock(return_value=None)
        with patch.object(probe_phase, "run_tier_probe", run_tier_probe):
            probe_phase.start_tier_probe(ctx)
            ctx.transcript_ready.set()
            assert ctx.tier_probe_task is not None
            await ctx.tier_probe_task

        assert not _annotated(run_tier_probe.call_args.args[0].transcript)
