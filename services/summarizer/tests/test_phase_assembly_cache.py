"""Regression tests for Phase 7 Assembly — Redis cache language gate.

Bug: ``run_phase_assembly`` used to write the assembled payload to Redis for
every language, *before* the translation phase ran. For non-English videos,
the source-language tabs/meta would be cached for the full TTL, silently
bypassing translation on every subsequent cache hit and breaking the FE
language toggle.

Fix: gate the cache write on ``ctx.source_language_code`` being unset (English
source). For non-English source the translation phase owns the cache write so
the persisted payload includes the ``sourceLanguage`` block.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _drain(gen):
    """Exhaust an async generator (we only care about side effects)."""
    async for _ in gen:
        pass


async def _collect(gen) -> list[str]:
    """Collect every SSE chunk an async generator yields."""
    return [chunk async for chunk in gen]


def _build_ctx(source_language_code: str | None = None) -> SimpleNamespace:
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
        source_language_code=source_language_code,
        clean_text="",
        repository=repo,
        timer=timer,
        transcript_data=None,
        audio_path=None,
    )


@pytest.fixture(autouse=True)
def _stub_status_callback():
    """Phase code mirrors status to the API via the fire-and-forget
    send_video_status_background (sync, spawns a tracked task) — stub it so
    unit tests never attempt real HTTP (and stay fast)."""
    from src.services.pipeline.phases import assembly as phase

    with patch.object(phase, "send_video_status_background", new=MagicMock()) as mock_send:
        yield mock_send


@pytest.mark.asyncio
async def test_redis_cache_set_for_english_videos() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=True, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache") as mock_cache,
        patch(
            "src.routes.cached_response.build_frontend_response",
            return_value={"meta": {}, "tabs": []},
        ),
    ):
        mock_cache.set_response = AsyncMock(return_value=True)
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]
        mock_cache.set_response.assert_called_once()


@pytest.mark.asyncio
async def test_redis_cache_skipped_when_row_deleted_mid_run() -> None:
    """A global purge removed the row while the run was in flight: the phase
    must not repopulate Redis (or any other store) for a video that is gone."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    ctx.repository.save_structured_result = MagicMock(return_value=False)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=True, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache") as mock_cache,
        patch(
            "src.routes.cached_response.build_frontend_response",
            return_value={"meta": {}, "tabs": []},
        ),
    ):
        mock_cache.set_response = AsyncMock(return_value=True)
        events = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]
        mock_cache.set_response.assert_not_called()
        assert not any("done" in event for event in events)


@pytest.mark.asyncio
async def test_redis_cache_skipped_for_non_english_videos() -> None:
    """Non-English videos must NOT cache here — translation phase owns the write."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(source_language_code="he")
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=True, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache") as mock_cache,
    ):
        mock_cache.set_response = AsyncMock(return_value=True)
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]
        mock_cache.set_response.assert_not_called()


@pytest.mark.asyncio
async def test_status_completed_and_done_emitted_for_english_videos() -> None:
    """English videos are final at assembly: persist status="completed" and
    emit the terminal done/[DONE] here (no translation phase follows)."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        events = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert saved["status"] == "completed"
    assert any("[DONE]" in ev for ev in events)


@pytest.mark.asyncio
async def test_status_processing_and_done_deferred_for_non_english_videos() -> None:
    """Non-English videos stay "processing" at assembly and DEFER the terminal
    event — the translation phase owns "completed" + the done emission so an
    interrupted translation leaves a retriable doc."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(source_language_code="he")
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        events = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert saved["status"] == "processing"
    assert not any("[DONE]" in ev for ev in events)


@pytest.mark.asyncio
async def test_redis_disabled_skips_cache_write_regardless_of_language() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache") as mock_cache,
    ):
        mock_cache.set_response = AsyncMock(return_value=True)
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]
        mock_cache.set_response.assert_not_called()


@pytest.mark.asyncio
async def test_status_callback_completed_for_english(_stub_status_callback) -> None:
    """Assembly must mirror the terminal status onto userVideos via the API
    callback — English videos report "completed" here."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
    ):
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    _stub_status_callback.assert_called_once_with("vsid", None, "completed")


@pytest.mark.asyncio
async def test_status_callback_processing_for_non_english(_stub_status_callback) -> None:
    """Non-English videos stay "processing" at assembly — the translation
    phase owns their "completed" transition."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(source_language_code="he")
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
    ):
        await _drain(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    _stub_status_callback.assert_called_once_with("vsid", None, "processing")
