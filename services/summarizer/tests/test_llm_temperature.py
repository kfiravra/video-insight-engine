"""Temperature plumbing (D3): retry wrapper → LLMService → LLMProvider → LiteLLM + Langfuse.

``temperature=None`` must leave every request exactly as it was before the
kwarg existed (no ``temperature`` key reaches LiteLLM, no ``modelParameters``
on the generation); a set value must reach ``acompletion`` and the Langfuse
generation's ``modelParameters`` on every path, including ``model_override``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import llm_provider as lp
from src.services.llm import LLMService
from src.services.observability import langfuse_client as lc
from src.utils import llm_retry
from src.utils.llm_retry import call_llm_with_retry

_SONNET = "anthropic/claude-sonnet-4-6"
_HAIKU = "anthropic/claude-haiku-4-5-20251001"

ProviderCall = Callable[..., Awaitable[str]]

_PROVIDER_ENTRY_POINTS: dict[str, Callable[[lp.LLMProvider], ProviderCall]] = {
    "complete": lambda p: lambda **kw: p.complete("hi", **kw),
    "complete_with_messages": lambda p: (
        lambda **kw: p.complete_with_messages([{"role": "user", "content": "hi"}], **kw)
    ),
    "complete_fast": lambda p: lambda **kw: p.complete_fast("hi", **kw),
}


def _response(content: str = "ok") -> MagicMock:
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    response.model = _SONNET
    return response


@pytest.fixture
def acompletion() -> Iterator[AsyncMock]:
    fake = AsyncMock(return_value=_response())
    with patch("src.services.llm_provider.acompletion", fake):
        yield fake


@pytest.fixture
def provider() -> lp.LLMProvider:
    return lp.LLMProvider(model=_SONNET, fast_model=_HAIKU, fallback_models=["openai/gpt-4o"])


@pytest.fixture
def fake_trace(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """A fake Langfuse SDK whose trace object records ``generation`` calls."""
    lc._reset_for_tests()
    monkeypatch.setattr(lc, "Langfuse", MagicMock(name="LangfuseSDK"))
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
    lc.init_langfuse()
    trace = MagicMock()
    cast(MagicMock, lc._client).trace.return_value = trace
    yield trace
    lc._reset_for_tests()


@pytest.fixture
def clean_override_cache() -> Iterator[None]:
    llm_retry._wrap_with_override.cache_clear()
    yield
    llm_retry._wrap_with_override.cache_clear()


def _service_mock() -> MagicMock:
    service = MagicMock()
    service.model = _SONNET
    service.fast_model = _HAIKU
    service.call_llm = AsyncMock(side_effect=["", "ok"])
    service.call_llm_fast = AsyncMock(side_effect=["", "ok"])
    return service


class TestProviderTemperature:
    @pytest.mark.parametrize("entry", list(_PROVIDER_ENTRY_POINTS))
    async def test_should_send_temperature_to_litellm_when_set(
        self, provider: lp.LLMProvider, acompletion: AsyncMock, entry: str
    ) -> None:
        await _PROVIDER_ENTRY_POINTS[entry](provider)(temperature=0.0)

        assert acompletion.call_args.kwargs["temperature"] == 0.0

    @pytest.mark.parametrize("entry", list(_PROVIDER_ENTRY_POINTS))
    async def test_should_omit_temperature_from_request_when_none(
        self, provider: lp.LLMProvider, acompletion: AsyncMock, entry: str
    ) -> None:
        await _PROVIDER_ENTRY_POINTS[entry](provider)(temperature=None)

        assert "temperature" not in acompletion.call_args.kwargs

    @pytest.mark.parametrize("entry", list(_PROVIDER_ENTRY_POINTS))
    async def test_should_send_identical_request_when_temperature_none_or_omitted(
        self, provider: lp.LLMProvider, acompletion: AsyncMock, entry: str
    ) -> None:
        call = _PROVIDER_ENTRY_POINTS[entry](provider)
        await call()
        await call(temperature=None)

        omitted, explicit_none = acompletion.call_args_list
        assert omitted.kwargs == explicit_none.kwargs


class TestServiceTemperature:
    async def test_call_llm_should_forward_temperature_to_provider_complete(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="ok")

        await LLMService(provider).call_llm("prompt", temperature=0.2)

        assert provider.complete.call_args.kwargs["temperature"] == 0.2

    async def test_call_llm_fast_should_forward_temperature_to_provider_complete_fast(
        self,
    ) -> None:
        provider = MagicMock()
        provider.complete_fast = AsyncMock(return_value="ok")

        await LLMService(provider).call_llm_fast("prompt", temperature=0.0)

        assert provider.complete_fast.call_args.kwargs["temperature"] == 0.0


class TestRetryTemperature:
    @pytest.mark.parametrize(
        ("use_fast_model", "method"), [(False, "call_llm"), (True, "call_llm_fast")]
    )
    async def test_should_forward_temperature_on_every_attempt(
        self, use_fast_model: bool, method: str
    ) -> None:
        service = _service_mock()

        with patch("src.utils.llm_retry.asyncio.sleep", new_callable=AsyncMock):
            await call_llm_with_retry(
                service,
                "prompt",
                stage_name="tier_probe",
                max_retries=1,
                use_fast_model=use_fast_model,
                temperature=0.0,
            )

        sent = [c.kwargs["temperature"] for c in getattr(service, method).call_args_list]
        assert sent == [0.0, 0.0]

    @pytest.mark.usefixtures("clean_override_cache")
    async def test_should_send_temperature_to_litellm_through_model_override(
        self, acompletion: AsyncMock
    ) -> None:
        await call_llm_with_retry(
            MagicMock(),
            "prompt",
            stage_name="memory",
            max_retries=0,
            model_override=_HAIKU,
            temperature=0.0,
        )

        assert acompletion.call_args.kwargs["temperature"] == 0.0

    @pytest.mark.usefixtures("clean_override_cache")
    async def test_should_omit_temperature_through_model_override_when_none(
        self, acompletion: AsyncMock
    ) -> None:
        await call_llm_with_retry(
            MagicMock(), "prompt", stage_name="plan", max_retries=0, model_override=_HAIKU
        )

        assert "temperature" not in acompletion.call_args.kwargs


class TestTemperatureTelemetry:
    @pytest.mark.parametrize("entry", list(_PROVIDER_ENTRY_POINTS))
    async def test_should_record_temperature_in_generation_model_parameters(
        self, provider: lp.LLMProvider, acompletion: AsyncMock, fake_trace: MagicMock, entry: str
    ) -> None:
        with patch("src.services.llm_telemetry.completion_cost", return_value=0.0):
            async with lc.pipeline_trace("vid-1"):
                await _PROVIDER_ENTRY_POINTS[entry](provider)(
                    span_name="tier_probe", temperature=0.0
                )

        assert fake_trace.generation.call_args.kwargs["model_parameters"] == {"temperature": 0.0}

    async def test_should_leave_model_parameters_unset_when_temperature_none(
        self, provider: lp.LLMProvider, acompletion: AsyncMock, fake_trace: MagicMock
    ) -> None:
        with patch("src.services.llm_telemetry.completion_cost", return_value=0.0):
            async with lc.pipeline_trace("vid-1"):
                await provider.complete("hi", span_name="plan")

        assert "model_parameters" not in fake_trace.generation.call_args.kwargs
