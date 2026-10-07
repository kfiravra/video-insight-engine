"""Unit tests for the per-run timing recorder (``pipeline.timing``)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from litellm.exceptions import RateLimitError
from llm_common.context import llm_feature_var

from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.pipeline_timing import (
    PipelineTimingRecorder,
    current_timing,
    is_fallback_response,
    mark_step,
    record_download,
    record_llm_call,
    record_llm_failure,
    start_run_timing,
    timed_step,
)


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    """Bind a recorder for one test and unbind it afterwards (sync tests share
    the main-thread context, so a leaked recorder would bleed into others)."""
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _response(
    *, prompt: int = 100, completion: int = 50, cache_read: int = 0, model: str = "claude-x"
) -> SimpleNamespace:
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        usage=usage, model=model, choices=[SimpleNamespace(finish_reason="stop")]
    )


def _rate_limit_error() -> RateLimitError:
    return RateLimitError(message="slow down", llm_provider="anthropic", model="m")


class TestObserveSse:
    def test_should_stamp_first_tab_ready_once(self, recorder: PipelineTimingRecorder) -> None:
        recorder.observe_sse(sse_event("tab_ready", {"id": "a", "position": 0}))
        first = recorder.milestones["firstTabReadyMs"]
        time.sleep(0.01)
        recorder.observe_sse(sse_event("tab_ready", {"id": "b", "position": 1}))

        assert recorder.milestones["firstTabReadyMs"] == first

    def test_should_count_every_emitted_tab(self, recorder: PipelineTimingRecorder) -> None:
        for position in range(3):
            recorder.observe_sse(
                sse_event("tab_ready", {"id": str(position), "position": position})
            )

        assert recorder.tabs_emitted == 3

    def test_should_stamp_done_and_complete(self, recorder: PipelineTimingRecorder) -> None:
        recorder.observe_sse(
            sse_event("complete", {"tabCount": 1, "processingTimeMs": 1, "degraded": False})
        )
        recorder.observe_sse(
            sse_event("done", {"videoSummaryId": "x", "processingTimeMs": 1, "degraded": False})
        )

        assert {"completeMs", "doneMs"} <= set(recorder.milestones)

    def test_should_ignore_non_event_chunks(self, recorder: PipelineTimingRecorder) -> None:
        recorder.observe_sse("data: [DONE]\n\n")
        recorder.observe_sse(": keepalive\n\n")

        assert recorder.milestones == {}


class TestFallbackDetection:
    @pytest.mark.parametrize(
        ("requested", "answered", "expected"),
        [
            ("anthropic/claude-sonnet-4-6", "claude-sonnet-4-6", False),
            ("anthropic/claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", False),
            ("anthropic/claude-sonnet-4-6", "gpt-4o-2024-08-06", True),
            ("openai/gpt-4o-mini", "gpt-4o-mini-2024-07-18", False),
            ("anthropic/claude-sonnet-4-6", None, False),
        ],
    )
    def test_should_flag_only_a_different_model(
        self, requested: str, answered: str | None, expected: bool
    ) -> None:
        assert is_fallback_response(requested, answered) is expected


class TestRecordLlmCall:
    def test_should_be_a_noop_outside_a_run(self) -> None:
        token = pipeline_timing._recorder_var.set(None)
        try:
            record_llm_call(
                span="plan", model="m", response=_response(), start_monotonic=0.0, latency_ms=1
            )
            assert current_timing() is None
        finally:
            pipeline_timing._recorder_var.reset(token)

    def test_should_record_tokens_and_feature(self, recorder: PipelineTimingRecorder) -> None:
        feature_token = llm_feature_var.set("summarize:plan")
        try:
            record_llm_call(
                span="plan",
                model="anthropic/claude-x",
                response=_response(prompt=900, completion=120, cache_read=800),
                start_monotonic=time.monotonic(),
                latency_ms=2500,
                attempt=2,
            )
        finally:
            llm_feature_var.reset(feature_token)

        call = recorder.llm_calls[0]
        assert (call["feature"], call["inputTokens"], call["outputTokens"]) == (
            "summarize:plan",
            900,
            120,
        )
        assert (call["cacheReadTokens"], call["attempt"], call["wallMs"]) == (800, 2, 2500)


class TestRecordFailureAndDownload:
    def test_should_count_rate_limits(self, recorder: PipelineTimingRecorder) -> None:
        record_llm_failure(
            span="extraction",
            model="m",
            error=_rate_limit_error(),
            start_monotonic=time.monotonic(),
            attempt=1,
        )

        doc = recorder.to_document(tabs_planned=0, tabs_assembled=0)
        assert doc["counts"]["rateLimited"] == 1

    def test_should_record_download_size(
        self, recorder: PipelineTimingRecorder, tmp_path: Path
    ) -> None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x" * 2048)

        record_download(
            kind="720p", purpose="prefetch", start_monotonic=time.monotonic(), path=video, ok=True
        )

        assert recorder.downloads[0]["bytes"] == 2048

    def test_should_record_failed_download_without_bytes(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        record_download(
            kind="lowres",
            purpose="scene_detect",
            start_monotonic=time.monotonic(),
            path=None,
            ok=False,
        )

        assert recorder.downloads[0]["bytes"] is None


class TestSteps:
    def test_timed_step_records_a_phase(self, recorder: PipelineTimingRecorder) -> None:
        with timed_step("transcript"):
            pass

        assert [p["name"] for p in recorder.phases] == ["transcript"]

    def test_mark_step_records_wall(self, recorder: PipelineTimingRecorder) -> None:
        started = time.monotonic()
        time.sleep(0.03)

        mark_step("frames.scene_detect", started)

        assert recorder.phases[0]["wallMs"] >= 30


class TestToDocument:
    def test_should_carry_counts_and_tab_numbers(self, recorder: PipelineTimingRecorder) -> None:
        record_llm_call(
            span="extraction",
            model="anthropic/claude-sonnet-4-6",
            response=_response(model="gpt-4o"),
            start_monotonic=time.monotonic(),
            latency_ms=10,
        )
        recorder.observe_sse(sse_event("tab_ready", {"id": "a", "position": 0}))

        counts = recorder.to_document(tabs_planned=4, tabs_assembled=5)["counts"]

        assert (counts["tabsPlanned"], counts["tabsAssembled"], counts["tabsEmitted"]) == (4, 5, 1)
        assert (counts["llmCalls"], counts["fallbacks"]) == (1, 1)

    def test_should_stamp_schema_version(self, recorder: PipelineTimingRecorder) -> None:
        doc = recorder.to_document(tabs_planned=0, tabs_assembled=0)

        assert doc["version"] == pipeline_timing.TIMING_SCHEMA_VERSION
