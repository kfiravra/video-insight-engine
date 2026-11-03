"""Tests for src.services.llm_telemetry.

Covers the cache-field surfacing path: when the Anthropic response carries
``cache_creation_input_tokens`` / ``cache_read_input_tokens`` on its usage
block, those numbers must appear in the Langfuse generation metadata so we
can answer "is the extraction prompt actually being cached?" from the UI.
Also covers the usage/cost the generation is finished with and the failure path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.services import llm_telemetry


def _build_response(
    *,
    prompt_tokens: int = 1000,
    completion_tokens: int = 200,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> MagicMock:
    """Build a mock LiteLLM response with the given usage shape."""
    response = MagicMock()
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    response.usage.cache_creation_input_tokens = cache_creation
    response.usage.cache_read_input_tokens = cache_read
    return response


def test_records_cache_read_tokens_in_metadata():
    """A response with cache_read_input_tokens > 0 must surface cacheHit + cacheReadTokens in metadata."""
    response = _build_response(prompt_tokens=2000, cache_read=8000)
    with patch("src.services.llm_telemetry.finish_generation") as mock_log:
        llm_telemetry.record_generation(
            handle=MagicMock(name="generation"),
            span_name="extraction",
            content="ok",
            response=response,
            latency_ms=123,
            finish_reason="stop",
            extra_metadata=None,
        )
    metadata = mock_log.call_args.kwargs["metadata"]
    assert metadata["cacheReadTokens"] == 8000
    assert metadata["cacheHit"] is True


def test_records_cache_creation_tokens_on_first_call():
    """The first call writes the cache and only has cache_creation_input_tokens — must surface that too."""
    response = _build_response(prompt_tokens=2000, cache_creation=10_000)
    with patch("src.services.llm_telemetry.finish_generation") as mock_log:
        llm_telemetry.record_generation(
            handle=MagicMock(name="generation"),
            span_name="extraction",
            content="ok",
            response=response,
            latency_ms=123,
            finish_reason="stop",
            extra_metadata=None,
        )
    metadata = mock_log.call_args.kwargs["metadata"]
    assert metadata["cacheCreationTokens"] == 10_000
    # cacheHit only flips True on reads (creations are the cost we PAY to enable hits)
    assert "cacheHit" not in metadata


def test_omits_cache_fields_when_caching_did_not_happen():
    """Non-Anthropic or non-cached calls — metadata must not carry phantom zeroes."""
    response = _build_response(cache_creation=0, cache_read=0)
    with patch("src.services.llm_telemetry.finish_generation") as mock_log:
        llm_telemetry.record_generation(
            handle=MagicMock(name="generation"),
            span_name="synthesis",
            content="ok",
            response=response,
            latency_ms=50,
            finish_reason="stop",
            extra_metadata=None,
        )
    metadata = mock_log.call_args.kwargs["metadata"]
    assert "cacheReadTokens" not in metadata
    assert "cacheCreationTokens" not in metadata
    assert "cacheHit" not in metadata


def test_preserves_extra_metadata_alongside_cache_fields():
    """The extra_metadata bag (attempt/retry counters etc) must still pass through unchanged."""
    response = _build_response(cache_read=5000)
    with patch("src.services.llm_telemetry.finish_generation") as mock_log:
        llm_telemetry.record_generation(
            handle=MagicMock(name="generation"),
            span_name="extraction",
            content="ok",
            response=response,
            latency_ms=100,
            finish_reason="stop",
            extra_metadata={"attempt": 1, "useFastModel": False},
        )
    metadata = mock_log.call_args.kwargs["metadata"]
    assert metadata["attempt"] == 1
    assert metadata["useFastModel"] is False
    assert metadata["cacheReadTokens"] == 5000


def test_should_finish_the_generation_with_usage_and_cost_when_the_call_succeeds():
    response = _build_response(prompt_tokens=2000, completion_tokens=300, cache_read=1500)
    handle = MagicMock(name="generation")
    with (
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0123),
        patch("src.services.llm_telemetry.finish_generation") as mock_finish,
    ):
        llm_telemetry.record_generation(
            handle=handle,
            span_name="extraction",
            content="ok",
            response=response,
            latency_ms=10,
            finish_reason="stop",
            extra_metadata=None,
        )
    assert mock_finish.call_args.args == (handle,)
    kwargs = mock_finish.call_args.kwargs
    # LiteLLM's prompt_tokens include the cache reads: input is the uncached 500.
    assert kwargs["usage"] == {
        "input": 500,
        "output": 300,
        "total": 2300,
        "cache_read_input_tokens": 1500,
    }
    assert (kwargs["cost_usd"], kwargs["output"]) == (0.0123, "ok")
    assert kwargs["metadata"]["latencyMs"] == 10


def test_should_skip_the_record_when_the_call_has_no_generation():
    with patch("src.services.llm_telemetry.finish_generation") as mock_finish:
        llm_telemetry.record_generation(
            handle=None,
            span_name="extraction",
            content="ok",
            response=_build_response(),
            latency_ms=1,
            finish_reason="stop",
            extra_metadata=None,
        )
    mock_finish.assert_not_called()


def test_should_mark_error_or_warning_when_the_call_fails_or_is_cancelled():
    import asyncio

    with patch("src.services.llm_telemetry.finish_generation") as mock_finish:
        llm_telemetry.record_generation_failure(MagicMock(), TimeoutError("slow"))
        llm_telemetry.record_generation_failure(MagicMock(), asyncio.CancelledError())
    failed, cancelled = (c.kwargs for c in mock_finish.call_args_list)
    assert (failed["level"], failed["status_message"]) == ("ERROR", "TimeoutError: slow")
    assert (cancelled["level"], cancelled["status_message"]) == ("WARNING", "cancelled")
