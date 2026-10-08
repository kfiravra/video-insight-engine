"""Tests that LLMProvider records generations on the current Langfuse trace.

Covers the integration boundary between :mod:`src.services.llm_provider`
and :mod:`src.services.observability.langfuse_client`:

- ``complete_with_messages(span_name=...)`` produces a generation
- ``complete_fast(span_name=...)`` produces a generation
- Calls without ``span_name`` are silent (no generation logged)
- LLM provider behavior is unchanged when no trace is active
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import llm_provider as lp
from src.services.observability import langfuse_client as lc


@pytest.fixture(autouse=True)
def _reset_langfuse_state(monkeypatch):
    """Wire a fake Langfuse SDK and reset the module state."""
    lc._reset_for_tests()
    fake_sdk_cls = MagicMock(name="LangfuseSDK")
    monkeypatch.setattr(lc, "Langfuse", fake_sdk_cls)
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
    yield
    lc._reset_for_tests()


def _build_litellm_response(
    content: str = "hello", model: str = "anthropic/claude-sonnet-4-6"
) -> MagicMock:
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    response.usage = usage
    response.model = model
    return response


@pytest.mark.asyncio
async def test_complete_with_messages_records_generation_when_span_name_set():
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response(content="output text")

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0042),
    ):
        async with lc.pipeline_trace("vid-1"):
            result = await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                span_name="extraction",
                span_metadata={"attempt": 1},
            )

    assert result == "output text"
    fake_trace.generation.assert_called_once()
    kwargs = fake_trace.generation.call_args.kwargs
    assert kwargs["name"] == "extraction"
    assert kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert kwargs["usage"]["input"] == 10
    assert kwargs["usage"]["output"] == 5
    assert kwargs["usage"]["totalCost"] == 0.0042
    assert kwargs["metadata"]["attempt"] == 1
    assert kwargs["metadata"]["finishReason"] == "stop"


@pytest.mark.asyncio
async def test_complete_with_messages_no_span_when_span_name_missing():
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)):
        async with lc.pipeline_trace("vid-1"):
            await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
            )

    fake_trace.generation.assert_not_called()


@pytest.mark.asyncio
async def test_complete_fast_records_generation_when_span_name_set():
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    provider = lp.LLMProvider(
        model="anthropic/claude-sonnet-4-6",
        fast_model="anthropic/claude-haiku-4-5-20251001",
    )
    fake_resp = _build_litellm_response(content="cls", model="anthropic/claude-haiku-4-5-20251001")

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0),
    ):
        async with lc.pipeline_trace("vid-1"):
            await provider.complete_fast(
                prompt="probe this",
                span_name="tier_probe",
                span_metadata={"useFastModel": True},
            )

    fake_trace.generation.assert_called_once()
    kwargs = fake_trace.generation.call_args.kwargs
    assert kwargs["name"] == "tier_probe"
    assert kwargs["model"] == "anthropic/claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_no_trace_active_no_span_logged():
    """Without a pipeline_trace block, span_name is harmless — generation skipped."""
    lc.init_langfuse()
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)):
        result = await provider.complete_with_messages(
            messages=[{"role": "user", "content": "hi"}],
            span_name="extraction",
        )
    # Behavior unchanged: returns content, doesn't crash
    assert result == "hello"


@pytest.mark.asyncio
async def test_cost_lookup_failure_does_not_crash_call():
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch(
            "src.services.llm_telemetry.completion_cost", side_effect=ValueError("unknown model")
        ),
    ):
        async with lc.pipeline_trace("vid-1"):
            result = await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                span_name="extraction",
            )

    assert result == "hello"
    # Generation still logged, just with cost=0
    fake_trace.generation.assert_called_once()
    assert fake_trace.generation.call_args.kwargs["usage"]["totalCost"] == 0.0
