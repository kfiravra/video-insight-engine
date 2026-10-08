"""Visual annotations through the run: frames-done → ctx block → Qdrant (pipeline-1min 1c.2).

What the frames show is rendered once at frames-done onto
``ctx.visual_annotations``; ``clean_text`` is never touched. Assembly indexes
the block as ``source="visual"`` points, so the transcript points and the S3
transcript blob stay speech only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routes import pipeline_orchestration
from src.services.pipeline.phases import assembly as assembly_phase
from src.services.pipeline.pipeline_helpers import TranscriptData


@pytest.fixture(autouse=True)
def _no_synthesis_call():
    """Synthesis runs inside assembly since 1d.3 (∥ moment fill); its own tests cover it."""
    from src.services.pipeline.phases import assembly as phase

    async def _no_synthesis(_ctx):
        return
        yield  # pragma: no cover — makes this an async generator

    with patch.object(phase, "run_phase_synthesis", _no_synthesis):
        yield


_CAPTION = "Zebra-striped whiteboard sketch"
_OCR = "Quokka slide title text"
_SPEECH = "we start with the dough then we fold it twice"
_SETTINGS = SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=True, PIPELINE_VERSION="vtest")


def _ctx(*, eval_run: bool = False) -> SimpleNamespace:
    """PipelineContext stand-in carrying what frames, assembly and the stores read."""
    repo = MagicMock()
    repo.save_structured_result = MagicMock(return_value=True)
    return SimpleNamespace(
        video_data=SimpleNamespace(
            title="t", channel="c", duration=60, chapters=None, thumbnail_url="https://x/t.jpg"
        ),
        triage=SimpleNamespace(tabs=[]),
        triage_dict={},
        plan_result=None,
        content_format=None,
        extraction_data={},
        extraction_dropped={},
        enrichment_data={},
        synthesis_dict=None,
        description_analysis=None,
        scene_frames_for_assembly=None,
        scene_frames_gallery=None,
        scene_frames_all=[],
        frame_descriptions=[],
        visual_annotations="",
        assembled_tabs=None,
        assembled_meta=None,
        video_summary_id="vsid",
        youtube_id="ytid",
        language="en",
        is_rtl=False,
        source_language_code=None,
        clean_text=_SPEECH,
        repository=repo,
        timer=MagicMock(elapsed=MagicMock(return_value=1.0)),
        transcript_data=TranscriptData(
            segments=[{"text": _SPEECH, "start": 0.0, "duration": 6.0}],
            raw_text=_SPEECH,
            transcript_type="captions",
            source="captions",
        ),
        audio_path=None,
        memory=None,
        video_memory="",
        eval_run=eval_run,
    )


async def _fake_frames(ctx: SimpleNamespace) -> AsyncGenerator[str, None]:
    """Stand-in for the frames phase: vision + OCR results on the context."""
    ctx.frame_descriptions = [
        {"timestamp_sec": 10, "content": _CAPTION, "scene_type": "whiteboard", "original_index": 1}
    ]
    ctx.scene_frames_all = [{"index": 2, "timestamp": 30, "ocr_text": _OCR}]
    yield "data: frames\n\n"


async def _frames_done(ctx: SimpleNamespace) -> None:
    """The frames phase, then the orchestration's Phase 2.5 render."""
    async for _ in _fake_frames(ctx):
        pass
    pipeline_orchestration._render_visual_annotations(ctx)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _stub_status_callback() -> Iterator[None]:
    with patch.object(assembly_phase, "send_video_status_background", new=MagicMock()):
        yield


@pytest.fixture
def stores() -> Iterator[SimpleNamespace]:
    """The three Qdrant writers + the S3 transcript store, all stubbed."""
    with (
        patch.object(assembly_phase, "store_transcript_chunks", new=AsyncMock()) as transcript,
        patch.object(assembly_phase, "store_default_output_chunks", new=AsyncMock()) as output,
        patch.object(assembly_phase, "store_visual_chunks", new=AsyncMock()) as visual,
        patch("src.services.transcription.transcript_store.transcript_store") as s3,
    ):
        s3.store = AsyncMock(return_value="videos/ytid/transcript.json")
        yield SimpleNamespace(transcript=transcript, output=output, visual=visual, s3=s3.store)


async def _run_assembly(ctx: SimpleNamespace) -> None:
    with (
        patch.object(assembly_phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(assembly_phase, "settings", _SETTINGS),
        patch.object(assembly_phase, "response_cache"),
        patch.object(assembly_phase.S3Client, "is_available", return_value=True),
    ):
        async for _ in assembly_phase.run_phase_assembly(ctx):  # type: ignore[arg-type]
            pass
        for _ in range(10):
            await asyncio.sleep(0)


def _annotated(text: Any) -> bool:
    return _CAPTION in str(text) or _OCR in str(text)


class TestFramesDone:
    async def test_should_render_the_annotations_block_onto_the_context(self):
        ctx = _ctx()

        await _frames_done(ctx)

        assert _CAPTION in ctx.visual_annotations and _OCR in ctx.visual_annotations

    async def test_should_leave_clean_text_speech_only(self):
        ctx = _ctx()

        await _frames_done(ctx)

        assert ctx.clean_text == _SPEECH


class TestAssemblyIndexing:
    async def test_should_index_speech_only_transcript_chunks(self, stores: SimpleNamespace):
        ctx = _ctx()
        await _frames_done(ctx)

        await _run_assembly(ctx)

        assert stores.transcript.await_args.args[1] == _SPEECH

    async def test_should_index_the_rendered_block_as_visual_chunks(self, stores: SimpleNamespace):
        ctx = _ctx()
        await _frames_done(ctx)

        await _run_assembly(ctx)

        stores.visual.assert_awaited_once_with("ytid", ctx.visual_annotations)

    async def test_should_store_a_speech_only_transcript_blob_in_s3(self, stores: SimpleNamespace):
        ctx = _ctx()
        await _frames_done(ctx)

        await _run_assembly(ctx)

        assert not _annotated(stores.s3.await_args.kwargs["segments"])

    async def test_should_clear_visual_points_even_without_annotations(
        self, stores: SimpleNamespace
    ):
        ctx = _ctx()

        await _run_assembly(ctx)

        stores.visual.assert_awaited_once_with("ytid", "")

    async def test_should_not_index_visual_chunks_for_an_eval_run(self, stores: SimpleNamespace):
        ctx = _ctx(eval_run=True)
        await _frames_done(ctx)

        await _run_assembly(ctx)

        stores.visual.assert_not_awaited()
