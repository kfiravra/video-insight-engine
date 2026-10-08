"""The extraction prompt reads [m:ss]-marked transcript text (pipeline-1min 1a.4, 1b).

Markers exist only in the prompt: ``ctx.clean_text`` (Qdrant, faithfulness) keeps
the unmarked text, and chunked batches keep absolute times. Extraction reads the
string the text branch rendered once (``ctx.prompt_transcript``,
``render_prompt_transcript``) — the one plan and memory read.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.models.memory_types import MemoryResult, OutlineSection
from src.services.pipeline.phases import extraction as extraction_phase
from src.services.pipeline.phases.text import render_prompt_transcript
from src.services.pipeline.pipeline_helpers import TranscriptData
from src.services.transcript.render import MARKER_PATTERN, marker_seconds
from src.services.transcription.transcript import clean_transcript


def _segments(duration: int, step: int = 5) -> list[dict[str, Any]]:
    return [
        {"text": f"words at {start} seconds", "start": float(start), "duration": float(step)}
        for start in range(0, duration, step)
    ]


def _ctx(duration: int, segments: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    """Minimal PipelineContext stand-in for the extraction phase."""
    segs = _segments(duration) if segments is None else segments
    raw_text = " ".join(s["text"] for s in segs) or "Title: metadata only"
    ctx = SimpleNamespace(
        video_summary_id="vs1",
        youtube_id="yt1",
        video_data=SimpleNamespace(
            title="T", channel="C", duration=duration, chapters=[], description=""
        ),
        triage=SimpleNamespace(content_tags=["learning"], modifiers=[], primary_tag="learning"),
        transcript_data=TranscriptData(
            segments=segs, raw_text=raw_text, transcript_type="manual", source="ytdlp"
        ),
        prompt_segments=list(segs),
        clean_text=clean_transcript(raw_text),
        source_language_code=None,
        frame_descriptions=[],
        scene_frames_all=[],
        scene_frames_gallery=[],
        description_analysis=None,
        llm_service=AsyncMock(),
        video_memory="<video_memory>\ndomains: learning\n</video_memory>",
        visual_annotations="",
        memory=None,
        chapters=None,
        extraction_data=None,
        extraction_coverage=None,
        plan_result=None,
        repository=AsyncMock(),
    )
    ctx.prompt_transcript = render_prompt_transcript(ctx)  # type: ignore[arg-type]
    return ctx


def _capturing_extract(captured: dict[str, Any]):
    async def _extract(_llm, _triage, transcript, _video_info, **kwargs):
        captured["transcript"] = transcript
        captured["chapters"] = kwargs.get("chapters")
        yield {"event": "extraction_complete", "data": {"learning": {"keyPoints": []}}}

    return _extract


async def _run(ctx: SimpleNamespace) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    with patch.object(extraction_phase, "extract", _capturing_extract(captured)):
        async for _ in extraction_phase.run_phase_extraction(ctx):
            pass
    return captured


class TestRenderPromptTranscript:
    def test_should_render_from_the_sponsor_filtered_prompt_segments(self):
        ctx = _ctx(60)
        ctx.prompt_segments = [s for s in ctx.prompt_segments if s["start"] < 20]

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert marker_seconds(prompt) == [0]

    def test_should_render_markers_from_the_segments(self):
        ctx = _ctx(60)

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert marker_seconds(prompt) == [0, 20, 40]

    def test_should_carry_exactly_the_clean_text_words(self):
        ctx = _ctx(60)

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        unmarked = " ".join(MARKER_PATTERN.sub("", ln).strip() for ln in prompt.splitlines())
        assert unmarked == ctx.clean_text

    def test_should_fall_back_to_clean_text_without_segments(self):
        ctx = _ctx(60, segments=[])

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert prompt == ctx.clean_text

    def test_should_leave_visual_annotations_out_when_frames_were_described(self):
        ctx = _ctx(60)
        ctx.frame_descriptions = [
            {"timestamp_sec": 30, "content": "whiteboard diagram", "frame_index": 0}
        ]

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert "whiteboard diagram" not in prompt


class TestExtractionPhaseUsesMarkedTranscript:
    async def test_should_send_the_text_branchs_prompt_transcript_to_extraction(self):
        ctx = _ctx(120)
        ctx.prompt_transcript = "[0:00] rendered once by the text branch"

        captured = await _run(ctx)

        assert captured["transcript"] == "[0:00] rendered once by the text branch"

    async def test_should_send_the_marked_transcript_to_extraction(self):
        ctx = _ctx(120)

        captured = await _run(ctx)

        assert captured["transcript"].startswith("[0:00] words at 0 seconds")

    async def test_should_leave_clean_text_unmarked(self):
        ctx = _ctx(120)
        clean_before = ctx.clean_text

        await _run(ctx)

        assert ctx.clean_text == clean_before
        assert not MARKER_PATTERN.search(ctx.clean_text)

    async def test_should_give_chunked_batches_absolute_times(self):
        ctx = _ctx(1800)

        with patch(
            "src.services.transcription.transcript_chunker._detect_chapters_with_ai",
            new_callable=AsyncMock,
            return_value=None,
        ):
            captured = await _run(ctx)

        chapters = captured["chapters"]
        assert len(chapters) > 1
        assert [marker_seconds(ch.text)[0] for ch in chapters] == [
            int(ch.start_seconds) for ch in chapters
        ]


class TestRenderPromptTranscriptLanguage:
    def test_should_remove_fillers_from_an_english_source(self):
        ctx = _ctx(10, segments=[{"text": "so um we start", "start": 0.0, "duration": 4.0}])

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert prompt == "[0:00] so we start"

    def test_should_keep_um_in_a_portuguese_source(self):
        ctx = _ctx(10, segments=[{"text": "um quilo de farinha", "start": 0.0, "duration": 4.0}])
        ctx.source_language_code = "pt"

        prompt = render_prompt_transcript(ctx)  # type: ignore[arg-type]

        assert prompt == "[0:00] um quilo de farinha"


class TestExtractionPhaseChapterSplitInputs:
    """1b.6: chapter splitting gets the real description and the memory outline."""

    async def _split_kwargs(self, memory: MemoryResult | None = None) -> dict[str, Any]:
        ctx = _ctx(1800)
        ctx.video_data.description = "Neapolitan dough, 72 h cold rise"
        ctx.memory = memory
        split = AsyncMock(return_value=[])

        with patch(
            "src.services.transcription.transcript_chunker.split_transcript_into_chapters", split
        ):
            await _run(ctx)

        return split.await_args.kwargs

    async def test_should_pass_the_video_description_to_chapter_splitting(self):
        kwargs = await self._split_kwargs()

        assert kwargs["video_data"]["description"] == "Neapolitan dough, 72 h cold rise"

    async def test_should_pass_no_outline_when_memory_failed(self):
        kwargs = await self._split_kwargs()

        assert kwargs["memory_outline"] is None

    async def test_should_pass_the_memory_outline_as_clock_strings(self):
        memory = MemoryResult(
            outline=[
                OutlineSection(start=0, end=600, title="dough"),
                OutlineSection(start=600, end=1800, title="bake"),
            ]
        )

        kwargs = await self._split_kwargs(memory)

        assert kwargs["memory_outline"] == [
            {"start": "0:00", "end": "10:00", "title": "dough"},
            {"start": "10:00", "end": "30:00", "title": "bake"},
        ]

    async def test_should_slice_chapters_from_the_prompt_segments(self):
        ctx = _ctx(1800)
        ctx.prompt_segments = ctx.prompt_segments[:10]
        split = AsyncMock(return_value=[])

        with patch(
            "src.services.transcription.transcript_chunker.split_transcript_into_chapters", split
        ):
            await _run(ctx)

        assert len(split.await_args.kwargs["segments"]) == 10


class TestExtractionPhaseVideoContext:
    """1b.4: the extraction prompt's ``{video_context}`` slot carries the video_memory block."""

    async def test_should_hand_extraction_the_runs_video_memory(self):
        ctx = _ctx(120)
        captured: dict[str, Any] = {}

        async def _extract(_llm, _triage, _transcript, _video_info, **kwargs):
            captured.update(kwargs)
            yield {"event": "extraction_complete", "data": {"learning": {"keyPoints": []}}}

        with patch.object(extraction_phase, "extract", _extract):
            async for _ in extraction_phase.run_phase_extraction(ctx):
                pass

        assert captured["video_context"] is ctx.video_memory

    async def test_should_hand_extraction_the_runs_visual_annotations(self):
        ctx = _ctx(120)
        ctx.visual_annotations = (
            "<visual_annotations>\n[0:12] Slide | Agenda\n</visual_annotations>"
        )
        captured: dict[str, Any] = {}

        async def _extract(_llm, _triage, _transcript, _video_info, **kwargs):
            captured.update(kwargs)
            yield {"event": "extraction_complete", "data": {"learning": {"keyPoints": []}}}

        with patch.object(extraction_phase, "extract", _extract):
            async for _ in extraction_phase.run_phase_extraction(ctx):
                pass

        assert captured["visual_annotations"] is ctx.visual_annotations


async def _split_kwargs_for(ctx: SimpleNamespace) -> dict[str, Any]:
    split = AsyncMock(return_value=[])
    with patch(
        "src.services.transcription.transcript_chunker.split_transcript_into_chapters", split
    ):
        await _run(ctx)
    assert split.await_args is not None
    return dict(split.await_args.kwargs)


class TestChapterSplitLanguage:
    """G06-1: chunked batches get the same source-language cleaning as one call."""

    async def test_should_pass_the_source_language_to_chapter_splitting(self):
        ctx = _ctx(1800)
        ctx.source_language_code = "pt"

        kwargs = await _split_kwargs_for(ctx)

        assert kwargs["source_language"] == "pt"


class TestChapterSplitDescriptionWait:
    """G03-4: the chunked path awaits the description analysis, capped."""

    _ANALYSIS = SimpleNamespace(timestamps=[SimpleNamespace(seconds=0, label="intro")])

    async def test_should_pass_description_timestamps_once_the_analysis_landed(self):
        ctx = _ctx(1800)
        ctx.description_analysis = self._ANALYSIS

        kwargs = await _split_kwargs_for(ctx)

        assert kwargs["description_chapters"] == [{"seconds": 0, "label": "intro"}]

    async def test_should_split_without_description_timestamps_when_the_analysis_is_late(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(extraction_phase, "_DESCRIPTION_WAIT_SECONDS", 0.01)
        ctx = _ctx(1800)
        ctx.description_task = asyncio.create_task(asyncio.Event().wait())

        kwargs = await _split_kwargs_for(ctx)

        assert kwargs["description_chapters"] is None
        ctx.description_task.cancel()

    async def test_should_leave_a_late_analysis_running_for_its_other_readers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(extraction_phase, "_DESCRIPTION_WAIT_SECONDS", 0.01)
        ctx = _ctx(1800)
        ctx.description_task = asyncio.create_task(asyncio.Event().wait())

        await _split_kwargs_for(ctx)

        assert not ctx.description_task.done()
        ctx.description_task.cancel()
