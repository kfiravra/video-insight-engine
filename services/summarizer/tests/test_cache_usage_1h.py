"""1h prompt-cache accounting from a real Anthropic usage payload (pipeline-1min 2.0).

The fixture holds the usage objects Anthropic returned to the 2.0 live probe
(Sonnet 4.6, one user block cached with ttl 1h). Each test runs one through
LiteLLM's own Anthropic response parser, then through what we record: the
per-call ``pipeline.timing`` row (reads, writes, the 1h/5m split) and the
``llm_usage`` ledger, which must price a 1h write at twice the input rate.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import llm_common.callback as llm_callback
import pytest
from litellm import ModelResponse
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from llm_common import MongoDBUsageCallback

from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import (
    PipelineTimingRecorder,
    record_llm_call,
    start_run_timing,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "anthropic_cache_usage_1h.json").read_text()
)
_MODEL: str = _FIXTURE["model"]
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Anthropic list prices for Sonnet 4.6, USD per million tokens: input 3, output
# 15; a 1h cache write costs 2x input, a 5m write 1.25x, a cache read 0.1x.
_PRICE = {"input": 3.0, "write_1h": 6.0, "write_5m": 3.75, "read": 0.30, "output": 15.0}


def _litellm_response(usage: dict[str, Any]) -> ModelResponse:
    """What LiteLLM hands our provider for an Anthropic reply carrying ``usage``."""
    body = {
        "id": "msg_probe",
        "type": "message",
        "role": "assistant",
        "model": _FIXTURE["responseModel"],
        "content": [{"type": "text", "text": "OK"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }
    raw = httpx.Response(200, json=body, request=httpx.Request("POST", _ANTHROPIC_URL))
    return AnthropicConfig().transform_response(
        model=_FIXTURE["responseModel"],
        raw_response=raw,
        model_response=ModelResponse(),
        logging_obj=MagicMock(),
        request_data={},
        messages=[{"role": "user", "content": "probe"}],
        optional_params={},
        litellm_params={},
        encoding=None,
    )


def _list_price(usage: dict[str, Any]) -> float:
    """The call's cost at Anthropic's published Sonnet 4.6 rates."""
    by_ttl = usage["cache_creation"]
    micro_usd = (
        usage["input_tokens"] * _PRICE["input"]
        + by_ttl["ephemeral_1h_input_tokens"] * _PRICE["write_1h"]
        + by_ttl["ephemeral_5m_input_tokens"] * _PRICE["write_5m"]
        + usage["cache_read_input_tokens"] * _PRICE["read"]
        + usage["output_tokens"] * _PRICE["output"]
    )
    return micro_usd / 1_000_000


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _timing_row(recorder: PipelineTimingRecorder, usage: dict[str, Any]) -> dict[str, Any]:
    record_llm_call(
        span="plan",
        model=_MODEL,
        response=_litellm_response(usage),
        start_monotonic=0.0,
        latency_ms=1,
    )
    return recorder.llm_calls[-1]


class TestTimingRecordsTheCacheSplit:
    def test_should_record_a_one_hour_write_when_the_first_call_writes_the_block(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        row = _timing_row(recorder, _FIXTURE["write"])

        assert (
            row["cacheWrite1hTokens"],
            row["cacheWrite5mTokens"],
            row["cacheWriteTokens"],
            row["cacheReadTokens"],
        ) == (2318, 0, 2318, 0)

    def test_should_record_a_read_when_another_video_reuses_the_block(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        row = _timing_row(recorder, _FIXTURE["readOtherVideo"])

        assert (row["cacheReadTokens"], row["cacheWriteTokens"], row["cacheWrite1hTokens"]) == (
            2318,
            0,
            0,
        )

    def test_should_count_the_whole_prompt_as_input_tokens_when_cached(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        row = _timing_row(recorder, _FIXTURE["readSameVideo"])

        assert row["inputTokens"] == 18 + 2318

    def test_should_cost_a_one_hour_write_at_twice_the_input_rate(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        row = _timing_row(recorder, _FIXTURE["write"])

        assert row["costUsd"] == pytest.approx(_list_price(_FIXTURE["write"]))


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MongoDBUsageCallback, MagicMock]]:
    """A ledger callback over a fake ``llm_usage`` collection.

    Building one registers the process-wide manual-emit buffer; monkeypatch
    puts the previous one back, and shutdown stops the flush timer.
    """
    monkeypatch.setattr(llm_callback, "_active_buffer", None)
    usage_rows = MagicMock()
    callback = MongoDBUsageCallback(
        {"llm_usage": usage_rows, "llm_alerts": MagicMock()}, service="summarizer", mode="sync"
    )
    yield callback, usage_rows
    callback.shutdown_sync()


def _ledger_cost(ledger: tuple[MongoDBUsageCallback, MagicMock], usage: dict[str, Any]) -> float:
    callback, usage_rows = ledger
    now = datetime.now(UTC)
    callback.log_success_event(
        kwargs={"model": _MODEL, "messages": [{"role": "user", "content": "probe"}]},
        response_obj=_litellm_response(usage),
        start_time=now,
        end_time=now,
    )
    callback.shutdown_sync()
    return usage_rows.insert_many.call_args.args[0][0]["cost_usd"]


class TestLedgerPricesOneHourWrites:
    def test_should_price_a_one_hour_write_at_twice_the_input_rate(
        self, ledger: tuple[MongoDBUsageCallback, MagicMock]
    ) -> None:
        assert _ledger_cost(ledger, _FIXTURE["write"]) == pytest.approx(
            _list_price(_FIXTURE["write"])
        )

    def test_should_price_a_cache_read_at_a_tenth_of_the_input_rate(
        self, ledger: tuple[MongoDBUsageCallback, MagicMock]
    ) -> None:
        assert _ledger_cost(ledger, _FIXTURE["readOtherVideo"]) == pytest.approx(
            _list_price(_FIXTURE["readOtherVideo"])
        )
