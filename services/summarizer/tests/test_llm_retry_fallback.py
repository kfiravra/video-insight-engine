"""Retry-after, same-provider retry, cross-provider fallback and dropped-connection
resends (D4 / C4 / A27, task 1d.5).

Driven end to end through the real ``LLMService`` → ``LLMProvider`` with a
scripted ``acompletion`` (``side_effect`` sequences): the provider makes one
request to one model per attempt, ``call_llm_with_retry`` decides waits and the
fallback, and ``pipeline.timing`` / Langfuse record the model that answered.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    Timeout,
)

from src.config import settings
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.observability import langfuse_client as lc
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.utils import llm_retry
from src.utils.llm_retry import call_llm_with_retry, retry_after_seconds

_SONNET = "anthropic/claude-sonnet-4-6"
_HAIKU = "anthropic/claude-haiku-4-5-20251001"
_GPT4O = "openai/gpt-4o"


def _reply(model: str, content: str = '{"ok": true}') -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="stop", message=MagicMock(content=content))]
    response.usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=10,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    response.model = model.split("/", 1)[1]
    return response


def _http(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://llm"))


def _rate_limited(headers: dict[str, str] | None = None) -> RateLimitError:
    return RateLimitError("slow down", "anthropic", "claude", response=_http(429, headers))


def _overloaded() -> InternalServerError:
    return InternalServerError("overloaded", "anthropic", "claude", response=_http(529))


def _service(fallback: str | None = _GPT4O) -> LLMService:
    return LLMService(
        LLMProvider(
            model=_SONNET, fast_model=_HAIKU, fallback_models=[fallback] if fallback else []
        )
    )


@pytest.fixture(autouse=True)
def _clean_override_cache() -> Iterator[None]:
    llm_retry._wrap_with_override.cache_clear()
    yield
    llm_retry._wrap_with_override.cache_clear()


@pytest.fixture(autouse=True)
def _no_configured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the provider built by each test decides the fallback."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", None)


@pytest.fixture
def sleep() -> Iterator[AsyncMock]:
    with patch("src.utils.llm_retry.asyncio.sleep", new_callable=AsyncMock) as fake:
        yield fake


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _scripted(*outcomes: Any) -> Any:
    return patch("src.services.llm_provider.acompletion", AsyncMock(side_effect=list(outcomes)))


def _models_called(acompletion: AsyncMock) -> list[str]:
    return [c.kwargs["model"] for c in acompletion.call_args_list]


class TestRetryAfter:
    async def test_should_wait_retry_after_then_succeed_on_same_provider(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_rate_limited({"retry-after": "7"}), _reply(_SONNET)) as acompletion:
            raw = await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert (raw, _models_called(acompletion), sleep.await_args_list[0].args[0]) == (
            '{"ok": true}',
            [_SONNET, _SONNET],
            7.0,
        )

    def test_should_prefer_retry_after_ms(self) -> None:
        error = _rate_limited({"retry-after-ms": "1500", "retry-after": "9"})

        assert retry_after_seconds(error) == 1.5

    def test_should_read_litellm_response_headers_attribute(self) -> None:
        error = _rate_limited()
        error.litellm_response_headers = httpx.Headers({"Retry-After": "4"})  # type: ignore[attr-defined]

        assert retry_after_seconds(error) == 4.0

    def test_should_cap_retry_after(self) -> None:
        assert retry_after_seconds(_rate_limited({"retry-after": "600"})) == 20.0

    def test_should_return_none_without_header(self) -> None:
        assert retry_after_seconds(_rate_limited()) is None

    async def test_should_fall_back_to_linear_backoff_without_retry_after(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_overloaded(), _overloaded(), _reply(_SONNET)):
            await call_llm_with_retry(_service(None), "p", stage_name="plan", max_retries=2)

        assert [c.args[0] for c in sleep.await_args_list] == [1.0, 2.0]


class TestCrossProviderFallback:
    async def test_should_retry_primary_once_then_answer_from_fallback(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_overloaded(), _rate_limited(), _reply(_GPT4O)) as acompletion:
            raw = await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert (raw, _models_called(acompletion)) == ('{"ok": true}', [_SONNET, _SONNET, _GPT4O])

    async def test_should_not_wait_when_switching_provider(self, sleep: AsyncMock) -> None:
        with _scripted(_rate_limited({"retry-after": "5"}), _rate_limited(), _reply(_GPT4O)):
            await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert [c.args[0] for c in sleep.await_args_list] == [5.0, 0.0]

    async def test_should_give_fallback_an_attempt_when_one_retry_allowed(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_overloaded(), _overloaded(), _reply(_GPT4O)) as acompletion:
            await call_llm_with_retry(_service(), "p", stage_name="memory", max_retries=1)

        assert _models_called(acompletion) == [_SONNET, _SONNET, _GPT4O]

    async def test_should_make_single_attempt_when_no_retries_allowed(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_overloaded()) as acompletion:
            raw = await call_llm_with_retry(_service(), "p", stage_name="probe", max_retries=0)

        assert (raw, acompletion.await_count) == (None, 1)

    async def test_should_never_fall_back_from_fast_model(self, sleep: AsyncMock) -> None:
        with _scripted(_overloaded(), _overloaded(), _overloaded()) as acompletion:
            await call_llm_with_retry(
                _service(), "p", stage_name="synthesis", max_retries=2, use_fast_model=True
            )

        assert _models_called(acompletion) == [_HAIKU, _HAIKU, _HAIKU]

    async def test_should_ignore_fallback_on_the_primary_provider(self, sleep: AsyncMock) -> None:
        with _scripted(_overloaded(), _overloaded(), _overloaded()) as acompletion:
            await call_llm_with_retry(_service(_HAIKU), "p", stage_name="plan", max_retries=2)

        assert _models_called(acompletion) == [_SONNET, _SONNET, _SONNET]

    async def test_should_reraise_last_rate_limit_when_fallback_also_fails(
        self, sleep: AsyncMock
    ) -> None:
        with (
            _scripted(_rate_limited(), _rate_limited(), _rate_limited()),
            pytest.raises(RateLimitError),
        ):
            await call_llm_with_retry(
                _service(), "p", stage_name="extraction", max_retries=2, propagate_rate_limit=True
            )


class TestNonRetryableErrors:
    @pytest.mark.parametrize(
        "error",
        [
            AuthenticationError("bad key", "anthropic", "claude", response=_http(401)),
            BadRequestError("bad request", "claude", "anthropic", response=_http(400)),
        ],
    )
    async def test_should_raise_without_retry_or_fallback(
        self, sleep: AsyncMock, error: Exception
    ) -> None:
        with _scripted(error, _reply(_SONNET), _reply(_GPT4O)) as acompletion:
            with pytest.raises(type(error)):
                await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert acompletion.await_count == 1


def _fallback_auth_error() -> AuthenticationError:
    return AuthenticationError("missing key", "openai", "gpt-4o", response=_http(401))


class TestFallbackRejectsTheRequest:
    """G21-3: the fallback's own 401/400 ends the call with None, never a crash."""

    async def test_should_return_none_when_the_fallback_rejects_the_request(
        self, sleep: AsyncMock
    ) -> None:
        with _scripted(_rate_limited(), _rate_limited(), _fallback_auth_error()):
            result = await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert result is None

    async def test_should_not_retry_a_fallback_that_rejected_the_request(
        self, sleep: AsyncMock
    ) -> None:
        outcomes = (_rate_limited(), _rate_limited(), _fallback_auth_error(), _reply(_GPT4O))
        with _scripted(*outcomes) as acompletion:
            await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=3)

        assert _models_called(acompletion) == [_SONNET, _SONNET, _GPT4O]

    async def test_should_record_the_fallback_rejection_under_the_fallback_model(
        self, sleep: AsyncMock, recorder: PipelineTimingRecorder
    ) -> None:
        with _scripted(_rate_limited(), _rate_limited(), _fallback_auth_error()):
            await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

        assert [f["model"] for f in recorder.llm_failures] == [_SONNET, _SONNET, _GPT4O]


class TestFallbackTelemetry:
    async def _run_fallback(self) -> None:
        with _scripted(_rate_limited(), _rate_limited(), _reply(_GPT4O)):
            await call_llm_with_retry(_service(), "p", stage_name="plan", max_retries=2)

    async def test_should_record_answer_under_fallback_model(
        self, sleep: AsyncMock, recorder: PipelineTimingRecorder
    ) -> None:
        await self._run_fallback()

        call = recorder.llm_calls[0]
        assert (call["model"], call["fallbackUsed"], call["attempt"]) == (_GPT4O, True, 3)

    async def test_should_keep_retry_fallback_and_429_counts(
        self, sleep: AsyncMock, recorder: PipelineTimingRecorder
    ) -> None:
        await self._run_fallback()

        counts = recorder.to_document(tabs_planned=0, tabs_assembled=0)["counts"]
        assert (counts["retries"], counts["fallbacks"], counts["rateLimited"]) == (1, 1, 2)

    async def test_should_record_failed_attempts_under_primary_model(
        self, sleep: AsyncMock, recorder: PipelineTimingRecorder
    ) -> None:
        await self._run_fallback()

        assert [f["model"] for f in recorder.llm_failures] == [_SONNET, _SONNET]

    async def test_should_tag_langfuse_generation_with_fallback_model(
        self, sleep: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lc._reset_for_tests()
        monkeypatch.setattr(lc, "Langfuse", MagicMock(name="LangfuseSDK"))
        monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
        monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
        lc.init_langfuse()
        trace = MagicMock()
        cast(MagicMock, lc._client).trace.return_value = trace
        try:
            with patch("src.services.llm_telemetry.completion_cost", return_value=0.0):
                async with lc.pipeline_trace("vid-1"):
                    await self._run_fallback()
        finally:
            lc._reset_for_tests()

        generation = trace.generation.call_args.kwargs
        assert (generation["model"], generation["metadata"]["fallbackFrom"]) == (_GPT4O, _SONNET)


def _dropped() -> Timeout:
    return Timeout(message="Connection timed out", model="gpt-4o-mini", llm_provider="openai")


def _disconnected() -> APIConnectionError:
    return APIConnectionError(
        message="Server disconnected", llm_provider="openai", model="gpt-4o-mini"
    )


class TestDroppedConnection:
    """A sub-second connection error never reached the model: resend at once (1d.5)."""

    @pytest.mark.parametrize("error", [_dropped(), _disconnected()])
    async def test_should_resend_at_once_outside_the_retry_budget(
        self, sleep: AsyncMock, error: Exception
    ) -> None:
        with _scripted(error, _reply(_HAIKU)) as acompletion:
            raw = await call_llm_with_retry(
                _service(None), "p", stage_name="synthesis", use_fast_model=True, max_retries=0
            )

        assert (raw, acompletion.await_count, sleep.await_count) == ('{"ok": true}', 2, 0)

    async def test_should_resend_only_once_per_call(self, sleep: AsyncMock) -> None:
        with _scripted(_dropped(), _dropped()) as acompletion:
            raw = await call_llm_with_retry(
                _service(None), "p", stage_name="synthesis", use_fast_model=True, max_retries=0
            )

        assert (raw, acompletion.await_count) == (None, 2)

    async def test_should_back_off_after_a_slow_timeout(
        self, sleep: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_retry, "_DROPPED_CONNECTION_SECONDS", 0.0)
        with _scripted(_dropped(), _reply(_HAIKU)):
            await call_llm_with_retry(
                _service(None), "p", stage_name="synthesis", use_fast_model=True, max_retries=1
            )

        assert [c.args[0] for c in sleep.await_args_list] == [1.0]

    async def test_should_keep_the_budget_after_a_resend(self, sleep: AsyncMock) -> None:
        with _scripted(_dropped(), _overloaded(), _reply(_HAIKU)) as acompletion:
            raw = await call_llm_with_retry(
                _service(None), "p", stage_name="synthesis", use_fast_model=True, max_retries=1
            )

        assert (raw, acompletion.await_count) == ('{"ok": true}', 3)
