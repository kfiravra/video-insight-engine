"""Tests that LLMProvider records each named call as a Langfuse generation.

Runs the real Langfuse v4 SDK against memory (``langfuse_capture``):

- ``complete_with_messages(span_name=...)`` / ``complete_fast(span_name=...)``
  produce one generation with model, usage, cost and metadata
- the generation nests under the phase span (``timed_step``) it ran in
- calls without ``span_name`` or outside a run trace record nothing
- a failed call ends its generation with ``ERROR`` and the error class
- a cost lookup failure still records the generation, at $0
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litellm.exceptions import BadRequestError

from src.services import llm_provider as lp
from src.services.observability import run_trace
from src.services.pipeline.pipeline_timing import timed_step
from llm_common.observability.testing import LangfuseCapture


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
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    response.usage = usage
    response.model = model
    return response


@pytest.mark.asyncio
async def test_complete_with_messages_records_generation_when_span_name_set(
    langfuse_capture: LangfuseCapture,
):
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response(content="output text")

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0042),
    ):
        async with run_trace(name="pipeline:vid-1"):
            result = await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                span_name="extraction",
                span_metadata={"attempt": 1},
            )

    assert result == "output text"
    (generation,) = langfuse_capture.generations()
    assert generation.name == "extraction"
    assert generation.model == "anthropic/claude-sonnet-4-6"
    assert generation.usage == {"input": 10, "output": 5, "total": 15}
    assert generation.cost == {"total": 0.0042}
    assert generation.metadata["attempt"] == 1
    assert generation.metadata["finishReason"] == "stop"
    assert generation.attr("output") == "output text"
    assert generation.parent == "pipeline:vid-1"


@pytest.mark.asyncio
async def test_should_nest_the_generation_under_the_phase_span_when_called_inside_a_step(
    langfuse_capture: LangfuseCapture,
):
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")

    with (
        patch(
            "src.services.llm_provider.acompletion",
            AsyncMock(return_value=_build_litellm_response()),
        ),
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0),
    ):
        async with run_trace(name="pipeline:vid-1"):
            with timed_step("plan"):
                await provider.complete("hi", span_name="plan")

    (generation,) = langfuse_capture.generations("plan")
    assert generation.parent == "phase:plan"
    assert langfuse_capture.named("phase:plan").parent == "pipeline:vid-1"


@pytest.mark.asyncio
async def test_complete_with_messages_no_span_when_span_name_missing(
    langfuse_capture: LangfuseCapture,
):
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)):
        async with run_trace(name="pipeline:vid-1"):
            await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
            )

    assert langfuse_capture.generations() == []


@pytest.mark.asyncio
async def test_complete_fast_records_generation_when_span_name_set(
    langfuse_capture: LangfuseCapture,
):
    provider = lp.LLMProvider(
        model="anthropic/claude-sonnet-4-6",
        fast_model="anthropic/claude-haiku-4-5-20251001",
    )
    fake_resp = _build_litellm_response(content="cls", model="anthropic/claude-haiku-4-5-20251001")

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch("src.services.llm_telemetry.completion_cost", return_value=0.0),
    ):
        async with run_trace(name="pipeline:vid-1"):
            await provider.complete_fast(
                prompt="probe this",
                span_name="tier_probe",
                span_metadata={"useFastModel": True},
            )

    (generation,) = langfuse_capture.generations()
    assert generation.name == "tier_probe"
    assert generation.model == "anthropic/claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_no_trace_active_no_span_logged(langfuse_capture: LangfuseCapture):
    """Without a run trace, span_name is harmless — no generation is recorded."""
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)):
        result = await provider.complete_with_messages(
            messages=[{"role": "user", "content": "hi"}],
            span_name="extraction",
        )
    # Behavior unchanged: returns content, doesn't crash
    assert result == "hello"
    assert langfuse_capture.observations() == []


@pytest.mark.asyncio
async def test_should_end_the_generation_as_error_when_the_call_fails(
    langfuse_capture: LangfuseCapture,
):
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    error = BadRequestError(
        message="prompt too long", model="claude-sonnet-4-6", llm_provider="anthropic"
    )

    with patch("src.services.llm_provider.acompletion", AsyncMock(side_effect=error)):
        async with run_trace(name="pipeline:vid-1"):
            with pytest.raises(BadRequestError):
                await provider.complete("hi", span_name="extraction")

    (generation,) = langfuse_capture.generations()
    assert generation.level == "ERROR"
    assert generation.attr("status_message").startswith("BadRequestError: ")


@pytest.mark.asyncio
async def test_cost_lookup_failure_does_not_crash_call(langfuse_capture: LangfuseCapture):
    provider = lp.LLMProvider(model="anthropic/claude-sonnet-4-6")
    fake_resp = _build_litellm_response()

    with (
        patch("src.services.llm_provider.acompletion", AsyncMock(return_value=fake_resp)),
        patch(
            "src.services.llm_telemetry.completion_cost", side_effect=ValueError("unknown model")
        ),
    ):
        async with run_trace(name="pipeline:vid-1"):
            result = await provider.complete_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                span_name="extraction",
            )

    assert result == "hello"
    # Generation still logged, just with cost=0
    (generation,) = langfuse_capture.generations()
    assert generation.cost == {"total": 0.0}
