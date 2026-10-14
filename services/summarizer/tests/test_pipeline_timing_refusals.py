"""Refusals in ``pipeline.timing`` (4.0b): the failure row, the re-send's stamps, counts, cost."""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from litellm.types.utils import Usage
from llm_common.context import llm_refused_model_var

from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import (
    PipelineTimingRecorder,
    record_llm_call,
    record_llm_failure,
    record_llm_refusal,
    start_run_timing,
)

_HAIKU_55 = "anthropic/claude-haiku-5-5"
_HAIKU_45 = "anthropic/claude-haiku-4-5-20251001"


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _response(finish_reason: str = "stop") -> SimpleNamespace:
    usage = Usage(
        prompt_tokens=1200, completion_tokens=40, total_tokens=1240, cache_read_input_tokens=800
    )
    return SimpleNamespace(
        usage=usage,
        model="claude-haiku-5-5",
        choices=[SimpleNamespace(finish_reason=finish_reason)],
    )


def _refusal(refused_model: str | None = None) -> None:
    """A refusal row — the previous model's, stamped, when ``refused_model`` is given."""
    record_llm_refusal(
        span="plan",
        model=_HAIKU_45 if refused_model else _HAIKU_55,
        response=_response("content_filter"),
        start_monotonic=time.monotonic(),
        attempt=1,
        category="cyber",
        refused_model=refused_model,
    )


def _call(
    refused_model: str | None = None, refusal_category: str | None = None, model: str = _HAIKU_45
) -> None:
    record_llm_call(
        span="plan",
        model=model,
        response=_response(),
        start_monotonic=time.monotonic(),
        latency_ms=10,
        fallback_used=refused_model is not None or model != _HAIKU_45,
        refused_model=refused_model,
        refusal_category=refusal_category,
    )


def _timeout(model: str = _HAIKU_55) -> None:
    record_llm_failure(
        span="plan", model=model, error=TimeoutError(), start_monotonic=time.monotonic(), attempt=1
    )


def _counts(recorder: PipelineTimingRecorder, *keys: str) -> tuple[int, ...]:
    counts = recorder.to_document(tabs_planned=0, tabs_assembled=0)["counts"]
    return tuple(counts[key] for key in keys)


_REFUSAL_COUNTS = ("refusals", "refusalFallbacks", "refusalsUnanswered")


@patch.object(pipeline_timing, "_call_cost", return_value=0.25)
class TestRefusalRows:
    def test_should_record_a_refusal_failure_row_with_what_it_was_billed(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()

        row = recorder.llm_failures[0]
        keys = ("error", "refusalCategory", "inputTokens", "outputTokens", "cacheReadTokens")
        assert {k: row[k] for k in (*keys, "costUsd")} == {
            "error": "Refusal",
            "refusalCategory": "cyber",
            "inputTokens": 1200,
            "outputTokens": 40,
            "cacheReadTokens": 800,
            "costUsd": 0.25,
        }

    def test_should_tag_the_fallback_call_with_the_refused_model_and_category(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _call(refused_model=_HAIKU_55, refusal_category="cyber")

        row = recorder.llm_calls[0]
        assert (row["fallbackUsed"], row["refusedModel"], row["refusalCategory"]) == (
            True,
            _HAIKU_55,
            "cyber",
        )

    def test_should_add_no_refusal_keys_when_a_call_was_not_a_refusal_fallback(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _call()
        _timeout()

        rows = (recorder.llm_calls[0], recorder.llm_failures[0])
        assert [{"refusedModel", "refusalCategory"} & row.keys() for row in rows] == [set(), set()]

    def test_should_stamp_a_failure_with_the_refused_model_when_it_is_the_re_sends(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        token = llm_refused_model_var.set(_HAIKU_55)
        try:
            _timeout(_HAIKU_45)
        finally:
            llm_refused_model_var.reset(token)

        assert recorder.llm_failures[0]["refusedModel"] == _HAIKU_55

    def test_should_add_the_refused_calls_cost_to_the_run_cost(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()
        _call(refused_model=_HAIKU_55, refusal_category="cyber")

        assert recorder.to_document(tabs_planned=0, tabs_assembled=0)["costUsd"] == 0.5

    def test_should_do_nothing_when_no_run_is_bound(self, _cost: object) -> None:
        token = pipeline_timing._recorder_var.set(None)
        try:
            _refusal()
            assert pipeline_timing.current_timing() is None
        finally:
            pipeline_timing._recorder_var.reset(token)


@patch.object(pipeline_timing, "_call_cost", return_value=0.25)
class TestRefusalCounts:
    def test_should_count_one_answered_refusal_when_the_previous_model_answers(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()
        _call(refused_model=_HAIKU_55, refusal_category="cyber")

        assert _counts(recorder, *_REFUSAL_COUNTS) == (1, 1, 0)

    def test_should_count_one_unanswered_refusal_when_the_previous_model_refuses_too(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()
        _refusal(refused_model=_HAIKU_55)

        assert _counts(recorder, *_REFUSAL_COUNTS) == (1, 0, 1)

    def test_should_count_refusals_apart_from_other_failures(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()
        _timeout()

        assert _counts(recorder, "refusals", "llmFailures") == (1, 2)

    def test_should_count_only_cross_provider_answers_as_fallbacks(
        self, _cost: object, recorder: PipelineTimingRecorder
    ) -> None:
        _refusal()
        _call(refused_model=_HAIKU_55, refusal_category="cyber")
        _call(model="openai/gpt-4o")

        assert _counts(recorder, "fallbacks", "refusalFallbacks") == (1, 1)
