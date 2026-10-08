"""Extraction phase: one extraction pass, quality recorded as a metric (pipeline-1min 1c.5).

The synthesis-fed retry is gone: a low quality score no longer re-runs extraction or
runs synthesis early — it is only logged.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from src.services.pipeline.phases import extraction as extraction_phase
from src.services.pipeline.pipeline_helpers import TranscriptData

_LOGGER = "src.services.pipeline.phases.extraction"

# Two planned extraction tabs, one filled → score 0.5 (the retry used to fire below 0.6).
_PLAN_TABS = [
    {"id": "points", "dataSource": "learning.keyPoints"},
    {"id": "concepts", "dataSource": "learning.concepts"},
]
_HALF_FILLED = {"learning": {"keyPoints": [{"title": "point"}], "concepts": []}}


def _ctx(plan_tabs: list[dict[str, Any]] | None = _PLAN_TABS) -> SimpleNamespace:
    """Minimal PipelineContext stand-in for a 60 s captioned video (no chapters)."""
    segs = [{"text": f"words at {s}", "start": float(s), "duration": 5.0} for s in range(0, 60, 5)]
    return SimpleNamespace(
        video_summary_id="vs1",
        youtube_id="yt1",
        video_data=SimpleNamespace(
            title="T", channel="C", duration=60, chapters=[], description=""
        ),
        triage=SimpleNamespace(content_tags=["learning"], modifiers=[], primary_tag="learning"),
        transcript_data=TranscriptData(
            segments=segs, raw_text="words", transcript_type="manual", source="ytdlp"
        ),
        prompt_segments=list(segs),
        clean_text="words",
        prompt_transcript="[0:00] words",
        source_language_code=None,
        frame_descriptions=[],
        scene_frames_all=[],
        scene_frames_gallery=[],
        description_analysis=None,
        llm_service=AsyncMock(),
        video_memory="",
        visual_annotations="",
        memory=None,
        chapters=None,
        extraction_data=None,
        extraction_coverage=None,
        synthesis_dict={},
        plan_result=None if plan_tabs is None else SimpleNamespace(tabs=plan_tabs),
        repository=AsyncMock(),
    )


def _counting_extract(calls: list[str], data: dict[str, Any]):
    async def _extract(_llm, _triage, transcript, _video_info, **_kwargs):
        calls.append(transcript)
        yield {"event": "extraction_complete", "data": data}

    return _extract


async def _run(ctx: SimpleNamespace, data: dict[str, Any] = _HALF_FILLED) -> list[str]:
    calls: list[str] = []
    with patch.object(extraction_phase, "extract", _counting_extract(calls, data)):
        async for _ in extraction_phase.run_phase_extraction(ctx):
            pass
    return calls


def _quality_records(caplog: Any) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("pipeline.extraction_quality")]


class TestExtractionRunsOnce:
    async def test_should_call_extraction_once_when_planned_fields_come_back_empty(self):
        ctx = _ctx()

        calls = await _run(ctx)

        assert len(calls) == 1

    async def test_should_keep_the_first_extraction_when_quality_is_low(self):
        ctx = _ctx()

        await _run(ctx)

        assert ctx.extraction_data == _HALF_FILLED

    async def test_should_make_no_llm_call_of_its_own_when_quality_is_low(self):
        """The retry ran synthesis early through ``ctx.llm_service``; nothing does now."""
        ctx = _ctx()

        await _run(ctx)

        assert ctx.llm_service.mock_calls == []

    async def test_should_leave_synthesis_to_its_phase_when_quality_is_low(self):
        ctx = _ctx()

        await _run(ctx)

        assert ctx.synthesis_dict == {}


class TestExtractionQualityMetric:
    async def test_should_log_the_quality_score_when_a_plan_is_present(self, caplog):
        ctx = _ctx()

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await _run(ctx)

        assert [getattr(r, "score", None) for r in _quality_records(caplog)] == [0.5]

    async def test_should_show_the_numbers_in_the_log_message_when_logged(self, caplog):
        ctx = _ctx()

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await _run(ctx)

        assert "score=0.50 populated=1/2" in _quality_records(caplog)[0].getMessage()

    async def test_should_skip_the_quality_metric_when_there_is_no_plan(self, caplog):
        ctx = _ctx(plan_tabs=None)

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await _run(ctx)

        assert _quality_records(caplog) == []


def _extract_with_drops(data: dict[str, Any], dropped: dict[str, int]):
    async def _extract(_llm, _triage, _transcript, _video_info, **_kwargs):
        yield {"event": "extraction_complete", "data": data, "droppedItems": dropped}

    return _extract


async def _chunks_with_drops(ctx: SimpleNamespace, dropped: dict[str, int]) -> list[str]:
    with patch.object(extraction_phase, "extract", _extract_with_drops(_HALF_FILLED, dropped)):
        return [chunk async for chunk in extraction_phase.run_phase_extraction(ctx)]


class TestDroppedItems:
    """Validation drops (hotfix 2.1) reach ctx for ``pipeline.extraction._dropped``, not the SSE."""

    async def test_should_store_dropped_counts_on_the_context(self) -> None:
        ctx = _ctx()

        await _chunks_with_drops(ctx, {"learning.concepts": 2})

        assert ctx.extraction_dropped == {"learning.concepts": 2}

    async def test_should_keep_dropped_counts_out_of_the_sse_event(self) -> None:
        chunks = await _chunks_with_drops(_ctx(), {"learning.concepts": 2})

        complete = [c for c in chunks if '"extraction_complete"' in c]
        assert complete and not any("droppedItems" in c for c in complete)
