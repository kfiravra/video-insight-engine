"""Tests for llm_common.models."""

from datetime import datetime
from unittest.mock import patch

import pytest

from llm_common.models import (
    UsageRecord,
    compute_cache_savings_usd,
    compute_transcription_cost_usd,
    extract_provider,
)


def test_extract_provider_with_slash():
    assert extract_provider("anthropic/claude-sonnet-4-6") == "anthropic"


def test_extract_provider_without_slash():
    assert extract_provider("gpt-4o") == "unknown"


def test_usage_record_defaults():
    record = UsageRecord(model="test/model", provider="test")
    assert record.tokens_in == 0
    assert record.tokens_out == 0
    assert record.cost_usd == 0.0
    assert record.feature == "unknown"
    assert record.success is True
    assert record.service == "unknown"
    assert isinstance(record.timestamp, datetime)


def test_usage_record_full():
    record = UsageRecord(
        model="anthropic/claude-sonnet-4-6",
        provider="anthropic",
        tokens_in=100,
        tokens_out=200,
        cost_usd=0.05,
        feature="summarize:chapter",
        success=True,
        duration_ms=1500,
        request_id="req-123",
        video_id="vid-456",
        is_stream=True,
        service="summarizer",
        prompt_preview="Summarize this...",
        prompt_hash="abc123",
    )
    assert record.tokens_in == 100
    assert record.cost_usd == 0.05
    assert record.video_id == "vid-456"


def test_usage_record_model_dump():
    record = UsageRecord(model="test/m", provider="test")
    d = record.model_dump()
    assert "model" in d
    assert "provider" in d
    assert "timestamp" in d


# ─── Transcription unit fields (Phase 0.5) ───


def test_usage_record_unit_defaults_to_tokens():
    """Existing token rows are unchanged: unit/audio_seconds have safe defaults."""
    record = UsageRecord(model="whisper-1", provider="openai")
    assert record.unit == "tokens"
    assert record.audio_seconds == 0.0
    d = record.model_dump()
    assert d["unit"] == "tokens"
    assert d["audio_seconds"] == 0.0


def test_usage_record_audio_unit_round_trips():
    record = UsageRecord(
        model="whisper-1",
        provider="openai",
        unit="audio_seconds",
        audio_seconds=90.0,
    )
    assert record.unit == "audio_seconds"
    assert record.model_dump()["audio_seconds"] == 90.0


# ─── compute_transcription_cost_usd (Phase 0.5) ───


def test_whisper_cost_is_per_minute():
    """Whisper-1 bills $0.006/min → 60s = $0.006, 600s = $0.06."""
    assert compute_transcription_cost_usd("whisper-1", audio_seconds=60.0) == pytest.approx(0.006)
    assert compute_transcription_cost_usd("whisper-1", audio_seconds=600.0) == pytest.approx(0.06)


def test_whisper_cost_ignores_tokens():
    """Duration-priced models don't charge for tokens."""
    cost = compute_transcription_cost_usd("whisper-1", audio_seconds=0.0, tokens_in=1000)
    assert cost == 0.0


def test_gemini_cost_is_per_token():
    """Gemini transcription bills per token (input $0.30/1M + output $0.40/1M)."""
    cost = compute_transcription_cost_usd(
        "gemini-2.5-flash-lite",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )
    assert cost == pytest.approx(0.30 + 0.40)


def test_unmapped_transcription_model_yields_zero_and_logs_once():
    """An unmapped model returns 0.0 and logs the drift once per model."""
    from llm_common.models import _LOGGED_MISSING_TRANSCRIPTION_MODELS

    unknown = "whisper-future-9"
    _LOGGED_MISSING_TRANSCRIPTION_MODELS.discard(unknown)
    try:
        with patch("llm_common.models.logger") as mock_logger:
            first = compute_transcription_cost_usd(unknown, audio_seconds=120.0)
            second = compute_transcription_cost_usd(unknown, audio_seconds=120.0)
        assert first == 0.0
        assert second == 0.0
        assert mock_logger.info.call_count == 1
        assert mock_logger.info.call_args.args[0] == "transcription.rate_missing"
    finally:
        _LOGGED_MISSING_TRANSCRIPTION_MODELS.discard(unknown)


@pytest.mark.parametrize(
    ("model", "saved_per_million"),
    [
        ("anthropic/claude-sonnet-5-5", 1.80),
        ("anthropic/claude-haiku-5-5", 0.09),
        ("openai/gpt-6-luna", 0.09),
    ],
)
def test_cache_savings_should_credit_the_4_0_models_at_their_docs_rates(
    model: str, saved_per_million: float
) -> None:
    assert compute_cache_savings_usd(model, 1_000_000) == pytest.approx(saved_per_million)
