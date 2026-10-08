"""Regression tests for Phase 7 Assembly — S3 transcript re-store gate.

Bug: ``run_phase_assembly`` re-stored the transcript to S3 on EVERY run,
including runs whose transcript was itself read back from S3
(``source="s3"``). ``TranscriptSource`` accepts ``"s3"``, so the blob's
recorded origin (which layer originally produced it — captions, Whisper,
Gemini) decayed to ``"s3"`` after a single regeneration and could never be
reported honestly in ``transcriptMeta.origin``.

Fix: skip the background store when ``ctx.transcript_data.source == "s3"``.
The blob is already in S3, so the skip is lossless.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


_SETTINGS = SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest")


async def _drain(gen: AsyncGenerator[str, None]) -> None:
    """Exhaust an async generator (we only care about side effects)."""
    async for _ in gen:
        pass


async def _let_background_tasks_run() -> None:
    """The S3 store is a fire-and-forget task — yield to the loop so it runs."""
    for _ in range(10):
        await asyncio.sleep(0)


def _transcript(source: str) -> TranscriptData:
    """One-segment TranscriptData tagged with the given fetch layer."""
    return TranscriptData(
        segments=[{"text": "hi", "start": 0.0, "duration": 1.0}],
        raw_text="hi",
        transcript_type="cached-whisper" if source == "s3" else source,
        source=source,
    )


def _build_ctx(transcript_data: TranscriptData) -> SimpleNamespace:
    """Minimal PipelineContext stand-in for run_phase_assembly."""
    triage = SimpleNamespace(tabs=[])
    video_data = SimpleNamespace(
        title="t",
        channel="c",
        duration=10,
        chapters=None,
        thumbnail_url="https://example/thumb.jpg",
    )
    repo = MagicMock()
    repo.save_structured_result = MagicMock(return_value=True)
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)
    return SimpleNamespace(
        video_data=video_data,
        triage=triage,
        triage_dict={},
        plan_result=None,
        extraction_data={},
        enrichment_data={},
        synthesis_dict=None,
        description_analysis=None,
        scene_frames_for_assembly=None,
        scene_frames_gallery=None,
        scene_frames_all=None,
        frame_descriptions=None,
        assembled_tabs=None,
        assembled_meta=None,
        video_summary_id="vsid",
        youtube_id="ytid",
        language="en",
        is_rtl=False,
        source_language_code=None,
        clean_text="hi",
        repository=repo,
        timer=timer,
        transcript_data=transcript_data,
        audio_path=None,
        memory=None,
        video_memory="",
    )


@pytest.fixture(autouse=True)
def _stub_status_callback() -> Iterator[MagicMock]:
    """Phase code mirrors status to the API via the fire-and-forget
    send_video_status_background (sync, spawns a tracked task) — stub it so
    unit tests never attempt real HTTP (and stay fast)."""
    from src.services.pipeline.phases import assembly as phase

    with patch.object(phase, "send_video_status_background", new=MagicMock()) as mock_send:
        yield mock_send


@pytest.fixture
def mock_store() -> Iterator[AsyncMock]:
    """``transcript_store`` is imported lazily INSIDE ``_store_transcript``, so
    patch the module attribute by path — a name patched on the phase module
    would never be seen by the background task."""
    with patch("src.services.transcription.transcript_store.transcript_store") as store_service:
        store_service.store = AsyncMock(return_value="videos/ytid/transcript.json")
        yield store_service.store


async def _run_assembly(ctx: SimpleNamespace, *, s3_available: bool) -> None:
    """Drain the phase with a no-op assembler, then let its background task run."""
    from src.services.pipeline.phases import assembly as phase

    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(phase, "settings", _SETTINGS),
        patch.object(phase, "response_cache"),
        patch.object(phase.S3Client, "is_available", return_value=s3_available),
    ):
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]
        await _let_background_tasks_run()


@pytest.mark.asyncio
async def test_s3_sourced_transcript_is_not_re_stored(mock_store: AsyncMock) -> None:
    """An S3-hit run must not write the blob back with source="s3" — that
    would decay the recorded origin after one regeneration."""
    ctx = _build_ctx(_transcript("s3"))

    await _run_assembly(ctx, s3_available=True)

    mock_store.assert_not_awaited()


@pytest.mark.asyncio
async def test_freshly_fetched_transcript_is_stored_with_its_source(
    mock_store: AsyncMock,
) -> None:
    """A transcript produced by a fetch layer (here Whisper) is stored once,
    tagged with that layer so the blob records the true origin."""
    ctx = _build_ctx(_transcript("whisper"))

    await _run_assembly(ctx, s3_available=True)

    mock_store.assert_awaited_once()
    kwargs = mock_store.await_args.kwargs
    assert kwargs["source"] == "whisper"
    assert kwargs["youtube_id"] == ctx.youtube_id


@pytest.mark.asyncio
async def test_store_skipped_when_s3_unavailable(mock_store: AsyncMock) -> None:
    """Without aioboto3 the store is never attempted, whatever the source."""
    ctx = _build_ctx(_transcript("whisper"))

    await _run_assembly(ctx, s3_available=False)

    mock_store.assert_not_awaited()
