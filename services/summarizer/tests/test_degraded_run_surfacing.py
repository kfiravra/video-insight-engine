"""Degraded-run surfacing (project-score-9 3.6).

A run whose extraction dropped batches (``batchesDropped > 0``) or whose
coverage is critical must surface ``degraded`` end to end:

- ``coverage_is_degraded`` — the single shared predicate
- ``run_phase_assembly`` — persists ``degraded: true`` on the Mongo doc AND on
  ``meta`` (served to the FE), and carries it on the ``complete``/``done``
  SSE events
- ``save_structured_result`` — the allowlist must not silently drop the flag
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.pipeline.post_processor import coverage_is_degraded


@pytest.fixture(autouse=True)
def _no_synthesis_call():
    """Synthesis runs inside assembly since 1d.3 (∥ moment fill); its own tests cover it."""
    from src.services.pipeline.phases import assembly as phase

    async def _no_synthesis(_ctx):
        return
        yield  # pragma: no cover — makes this an async generator

    with patch.object(phase, "run_phase_synthesis", _no_synthesis):
        yield


async def _collect(gen) -> list[str]:
    """Collect every SSE chunk an async generator yields."""
    return [chunk async for chunk in gen]


def _events(chunks: list[str]) -> list[dict]:
    """Parse SSE ``data: {...}`` chunks into event dicts (skips [DONE])."""
    out: list[dict] = []
    for chunk in chunks:
        payload = chunk.removeprefix("data: ").strip()
        if payload == "[DONE]":
            continue
        out.append(json.loads(payload))
    return out


def _build_ctx(
    extraction_coverage: dict | None = None,
    source_language_code: str | None = None,
) -> SimpleNamespace:
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
        content_format=None,
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
        extraction_coverage=extraction_coverage,
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
        memory=None,
        video_memory="",
    )


_CLEAN_COVERAGE = {
    "maxTimestamp": 590,
    "duration": 600,
    "ratio": 0.983,
    "tailMissingSeconds": 10,
    "critical": False,
    "batchesTotal": 4,
    "batchesSucceeded": 4,
    "batchesDropped": 0,
}
_DROPPED_COVERAGE = {
    "maxTimestamp": 300,
    "duration": 600,
    "ratio": 0.5,
    "tailMissingSeconds": 300,
    "critical": False,
    "batchesTotal": 4,
    "batchesSucceeded": 3,
    "batchesDropped": 1,
}
_CRITICAL_COVERAGE = {
    "maxTimestamp": 200,
    "duration": 3600,
    "ratio": 0.056,
    "tailMissingSeconds": 3400,
    "critical": True,
}


# ─── coverage_is_degraded predicate ──────────────────────────────────────
class TestCoverageIsDegraded:
    def test_none_coverage_is_not_degraded(self):
        assert coverage_is_degraded(None) is False

    def test_empty_coverage_is_not_degraded(self):
        assert coverage_is_degraded({}) is False

    def test_clean_coverage_is_not_degraded(self):
        assert coverage_is_degraded(_CLEAN_COVERAGE) is False

    def test_dropped_batches_are_degraded(self):
        assert coverage_is_degraded(_DROPPED_COVERAGE) is True

    def test_critical_coverage_is_degraded(self):
        assert coverage_is_degraded(_CRITICAL_COVERAGE) is True

    def test_non_int_dropped_count_is_ignored(self):
        assert coverage_is_degraded({"batchesDropped": "2"}) is False


# ─── Assembly phase surfacing ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_degraded_run_flags_doc_meta_and_terminal_events() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(extraction_coverage=_DROPPED_COVERAGE)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        events = _events(await _collect(phase.run_phase_assembly(ctx)))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert saved["degraded"] is True
    assert saved["meta"]["degraded"] is True
    complete = next(e for e in events if e["event"] == "complete")
    done = next(e for e in events if e["event"] == "done")
    assert complete["degraded"] is True
    assert done["degraded"] is True


@pytest.mark.asyncio
async def test_critical_coverage_also_flags_degraded() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(extraction_coverage=_CRITICAL_COVERAGE)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert saved["degraded"] is True
    assert saved["meta"]["degraded"] is True


@pytest.mark.asyncio
async def test_clean_run_carries_no_degraded_field_on_doc() -> None:
    """Clean runs: no doc/meta field (lean docs), terminal events say False."""
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(extraction_coverage=_CLEAN_COVERAGE)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        events = _events(await _collect(phase.run_phase_assembly(ctx)))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert "degraded" not in saved
    assert "degraded" not in saved["meta"]
    complete = next(e for e in events if e["event"] == "complete")
    done = next(e for e in events if e["event"] == "done")
    assert complete["degraded"] is False
    assert done["degraded"] is False


@pytest.mark.asyncio
async def test_no_coverage_metric_means_not_degraded() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx(extraction_coverage=None)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        events = _events(await _collect(phase.run_phase_assembly(ctx)))  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert "degraded" not in saved
    complete = next(e for e in events if e["event"] == "complete")
    assert complete["degraded"] is False


# ─── Repository allowlist ────────────────────────────────────────────────
def test_save_structured_result_persists_degraded_flag() -> None:
    from src.repositories.mongodb_repository import MongoDBVideoRepository

    collection = MagicMock()
    database = MagicMock()
    database.videoSummaryCache = collection
    repo = MongoDBVideoRepository(database)

    repo.save_structured_result("0" * 24, {"degraded": True, "meta": {}, "userId": "evil"})

    written = collection.update_one.call_args.args[1]["$set"]
    assert written["degraded"] is True
    assert "userId" not in written  # allowlist still filters injections
