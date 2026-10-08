"""Regression tests for Phase 8 Translation — status ownership.

Assembly leaves non-English videos ``status="processing"``; the translation
phase owns the ``"completed"`` transition. On success it persists the
``sourceLanguage`` block AND ``status="completed"``; on a deliberate no-op
(mirror / LLM returned None — English-only is the valid final surface) it still
finalizes the doc to ``"completed"``. An *interrupted* translation never reaches
either path, so the doc stays ``"processing"`` and is retriable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schemas import ProcessingStatus


async def _drain(gen) -> None:
    async for _ in gen:
        pass


def _build_ctx() -> SimpleNamespace:
    """Minimal PipelineContext stand-in for run_phase_translation (non-English)."""
    video_data = SimpleNamespace(
        title="כותרת", channel="c", duration=10, thumbnail_url="https://x/t.jpg"
    )
    return SimpleNamespace(
        source_language_code="he",
        source_language=None,
        llm_service=MagicMock(),
        assembled_tabs=[{"id": "overview"}],
        assembled_meta={"contentTags": ["podcast"]},
        video_data=video_data,
        youtube_id="ytid",
    )


@pytest.fixture(autouse=True)
def _stub_status_callback():
    """Translation mirrors "completed" to the API via the fire-and-forget
    send_video_status_background (sync, spawns a tracked task) — stub it so
    unit tests never attempt real HTTP."""
    from src.services.pipeline.phases import translation as phase

    with patch.object(phase, "send_video_status_background", new=MagicMock()) as mock_send:
        yield mock_send


@pytest.mark.asyncio
async def test_success_persists_sourcelanguage_and_status_completed() -> None:
    from src.services.pipeline.phases import translation as phase

    ctx = _build_ctx()
    repo = MagicMock()
    translated = {
        "tabs": [],
        "meta": {},
        "sourceLanguage": {"code": "he", "name": "עברית", "isRTL": True, "tabs": [], "meta": {}},
    }
    with (
        patch.object(phase, "translate_to_source", AsyncMock(return_value=translated)),
        patch.object(phase, "translate_text", AsyncMock(return_value="English title")),
        patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=False)),
    ):
        await _drain(phase.run_phase_translation(ctx, repo, "vsid"))  # type: ignore[arg-type]

    saved = repo.save_structured_result.call_args.args[1]
    assert saved["status"] == "completed"
    assert saved["sourceLanguage"]["code"] == "he"
    repo.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_noop_finalizes_status_completed_without_sourcelanguage() -> None:
    """Mirror/LLM-None → no sourceLanguage, but the doc must leave "processing"
    so it is not stuck as fake-incomplete (English-only is the final surface)."""
    from src.services.pipeline.phases import translation as phase

    ctx = _build_ctx()
    repo = MagicMock()
    # translate_to_source returns the input unchanged (no sourceLanguage key).
    unchanged = {"tabs": [{"id": "overview"}], "meta": {"contentTags": ["podcast"]}}
    with (
        patch.object(phase, "translate_to_source", AsyncMock(return_value=unchanged)),
        patch.object(phase, "translate_text", AsyncMock(return_value="x")),
        patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=False)),
    ):
        await _drain(phase.run_phase_translation(ctx, repo, "vsid"))  # type: ignore[arg-type]

    repo.update_status.assert_called_once()
    assert repo.update_status.call_args.args[1] is ProcessingStatus.COMPLETED
    repo.save_structured_result.assert_not_called()


@pytest.mark.asyncio
async def test_status_callback_completed_on_both_paths(_stub_status_callback) -> None:
    """Both translation outcomes (sourceLanguage persisted / English-only
    finalize) must mirror "completed" onto userVideos via the API callback."""
    from src.services.pipeline.phases import translation as phase

    ctx = _build_ctx()
    repo = MagicMock()
    with (
        patch.object(
            phase, "translate_to_source", AsyncMock(return_value={"tabs": [], "meta": {}})
        ),
        patch.object(phase, "translate_text", AsyncMock(return_value="x")),
        patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=False)),
    ):
        await _drain(phase.run_phase_translation(ctx, repo, "vsid"))  # type: ignore[arg-type]

    _stub_status_callback.assert_called_once()
    assert _stub_status_callback.call_args.args[2] == "completed"


@pytest.mark.asyncio
async def test_should_skip_status_and_cache_when_row_deleted_mid_run(_stub_status_callback) -> None:
    """A global purge removed the row while translating: nothing may be
    mirrored to the API or written to Redis, and the runner is told."""
    from src.services.pipeline.phases import translation as phase

    ctx = _build_ctx()
    repo = MagicMock()
    repo.save_structured_result = MagicMock(return_value=False)
    translated = {
        "tabs": [],
        "meta": {},
        "sourceLanguage": {"code": "he", "name": "עברית", "isRTL": True, "tabs": [], "meta": {}},
    }
    with (
        patch.object(phase, "translate_to_source", AsyncMock(return_value=translated)),
        patch.object(phase, "translate_text", AsyncMock(return_value="English title")),
        patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=True)),
        patch.object(phase, "response_cache") as mock_cache,
    ):
        mock_cache.set_response = AsyncMock(return_value=True)
        await _drain(phase.run_phase_translation(ctx, repo, "vsid"))  # type: ignore[arg-type]

    _stub_status_callback.assert_not_called()
    mock_cache.set_response.assert_not_called()
    assert ctx.row_deleted is True


@pytest.mark.asyncio
async def test_should_tag_translation_calls_with_their_own_feature() -> None:
    """Regression: translation inherited "summarize:metadata" (ledger + timing misattributed)."""
    from llm_common.context import llm_feature_var

    from src.services.pipeline.phases import translation as phase

    seen: list[str | None] = []

    async def _translate(*_a: object, **_k: object) -> dict:
        seen.append(llm_feature_var.get())
        return {"tabs": [], "meta": {}, "sourceLanguage": {"code": "he", "tabs": [], "meta": {}}}

    token = llm_feature_var.set("summarize:metadata")
    try:
        with (
            patch.object(phase, "translate_to_source", _translate),
            patch.object(phase, "translate_text", AsyncMock(return_value="English title")),
            patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=False)),
        ):
            await _drain(phase.run_phase_translation(_build_ctx(), MagicMock(), "vsid"))  # type: ignore[arg-type]
    finally:
        llm_feature_var.reset(token)

    assert seen == ["summarize:translation"]
