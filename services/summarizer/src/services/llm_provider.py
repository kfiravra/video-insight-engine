"""LiteLLM-based multi-provider LLM abstraction.

Provides unified API for calling LLMs across Anthropic, OpenAI, and Gemini
with cost tracking. One request = one model: retries and the cross-provider
fallback live in ``call_llm_with_retry``, never inside LiteLLM, so every
attempt is recorded under the model that actually answered.
"""

import logging
import time
from collections.abc import Sequence
from typing import Any

from litellm import acompletion
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.llms.openai.chat.gpt_5_transformation import OpenAIGPT5Config

from src.config import settings
from src.services.llm_messages import (
    Message,
    UserContent,
    build_prompt_messages,
    prepare_for_model,
    to_message_dicts,
)
from src.services.llm_telemetry import record_generation, stopwatch_ms_since
from src.services.pipeline.pipeline_timing import record_llm_call, record_llm_failure

logger = logging.getLogger(__name__)

# LiteLLM num_retries is disabled because retries are handled by
# call_llm_with_retry(), which adds structured logging, per-stage timeouts,
# retry-after-aware backoff and the cross-provider fallback.
_LITELLM_NUM_RETRIES = 0


def _attempt_of(span_metadata: dict[str, Any] | None) -> int:
    """Retry attempt number stamped by ``call_llm_with_retry`` (1 when absent)."""
    attempt = (span_metadata or {}).get("attempt", 1)
    return attempt if isinstance(attempt, int) else 1


def _is_fallback_attempt(span_metadata: dict[str, Any] | None) -> bool:
    """True when ``call_llm_with_retry`` routed this attempt to the fallback model."""
    return bool((span_metadata or {}).get("fallbackFrom"))


def _rejects_temperature(model: str, temperature: float) -> bool:
    """gpt-5 reasoning models accept only temperature=1; LiteLLM raises on any other value."""
    if temperature == 1 or not OpenAIGPT5Config.is_model_gpt_5_model(model):
        return False
    return not OpenAIGPT5Config.is_model_gpt_5_1_model(model)


def _with_sampling(kwargs: dict[str, Any], temperature: float | None) -> dict[str, Any]:
    """Add ``temperature`` to the request only when the caller set one.

    ``None`` leaves the key out so the provider default applies and the
    request stays byte-identical to calls that never passed it. A value the
    model rejects is dropped (with a warning) rather than failing every attempt.
    """
    if temperature is None:
        return kwargs
    if _rejects_temperature(kwargs["model"], temperature):
        logger.warning(
            "Dropping temperature=%s: %s accepts only temperature=1", temperature, kwargs["model"]
        )
        return kwargs
    kwargs["temperature"] = temperature
    return kwargs


def _model_parameters(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Sampling parameters actually sent, for Langfuse ``modelParameters``."""
    if "temperature" not in kwargs:
        return None
    return {"temperature": kwargs["temperature"]}


class LLMProvider:
    """Multi-provider LLM abstraction using LiteLLM.

    Supports Anthropic, OpenAI, and Gemini; one request goes to one model.
    """

    def __init__(
        self,
        model: str | None = None,
        fast_model: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float | None = None,
    ):
        """Initialize LLM provider.

        Args:
            model: Model to use (e.g., "anthropic/claude-sonnet-4-6")
            fast_model: Fast model for quick tasks (e.g., "anthropic/claude-haiku-4-5-20251001")
            fallback_models: Fallback chain for primary-model calls. Never sent
                to LiteLLM — ``call_llm_with_retry`` reads ``fallback_model``
                and makes the fallback attempt itself.
            timeout: Request timeout in seconds
        """
        self._model = model or settings.llm_model
        self._fast_model = fast_model or settings.llm_fast_model
        self._fallback_models = fallback_models or settings.llm_fallback_models
        self._timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS

    @property
    def model(self) -> str:
        """Get the configured model."""
        return self._model

    @property
    def fast_model(self) -> str:
        """Get the configured fast model for quick tasks."""
        return self._fast_model

    @property
    def fallback_model(self) -> str | None:
        """First model of the fallback chain, or None when none is configured."""
        return self._fallback_models[0] if self._fallback_models else None

    def _extract_provider(self, model: str) -> str:
        """Extract provider from model string."""
        return model.split("/")[0] if "/" in model else "unknown"

    async def _call_with_error_logging(
        self,
        coro,
        *,
        model: str | None = None,
        timeout_value: float | None = None,
        context: str = "",
        span_name: str | None = None,
        attempt: int = 1,
    ):
        """Execute an acompletion coroutine with standardized error logging.

        Records EVERY failed attempt in ``pipeline.timing.llmFailures`` — not
        just the classified LiteLLM errors: Anthropic 500/529
        (``InternalServerError``), ``APIConnectionError``, ``BadRequestError``
        and ``NotFoundError`` are separate classes and used to slip through
        uncounted. Logs with context, and re-raises.

        NOTE: ``coro`` is an already-created coroutine (e.g., ``acompletion(**kwargs)``).
        Any synchronous validation errors raised during coroutine creation will
        propagate to the caller before this method is entered.  This is intentional —
        validation errors (bad model name, invalid params) are programming errors,
        not transient failures, and should not be masked by retry/logging logic.
        """
        effective_model = model or self._model
        effective_timeout = timeout_value or self._timeout
        ctx = f" in {context}" if context else ""
        start_monotonic = time.monotonic()
        try:
            return await coro
        except Exception as e:
            record_llm_failure(
                span=span_name or context or "unknown",
                model=effective_model,
                error=e,
                start_monotonic=start_monotonic,
                attempt=attempt,
            )
            self._log_provider_error(e, effective_model, effective_timeout, ctx)
            raise

    def _log_provider_error(
        self, e: Exception, effective_model: str, effective_timeout: float, ctx: str
    ) -> None:
        """Log a LiteLLM error with the severity its class deserves."""
        if isinstance(e, RateLimitError):
            provider = self._extract_provider(effective_model)
            logger.warning("Rate limited%s by %s: %s", ctx, provider, e)
        elif isinstance(e, AuthenticationError):
            provider = self._extract_provider(effective_model)
            logger.error("Auth error%s for %s: %s", ctx, provider, e)
        elif isinstance(e, Timeout):
            logger.warning("Timeout%s after %ss: %s", ctx, effective_timeout, e)
        elif isinstance(e, ServiceUnavailableError):
            logger.warning("Service unavailable%s: %s", ctx, e)
        elif isinstance(e, APIError):
            logger.error("API error%s: %s", ctx, e)
        else:
            logger.error("LLM call failed%s (%s): %s", ctx, type(e).__name__, e)

    async def _run_completion(
        self,
        kwargs: dict[str, Any],
        *,
        span_name: str | None,
        span_metadata: dict[str, Any] | None,
        context: str = "",
    ) -> str:
        """Send one ``acompletion`` request; record timing and the Langfuse generation."""
        model = kwargs["model"]
        kwargs["messages"] = prepare_for_model(kwargs["messages"], model)
        attempt = _attempt_of(span_metadata)
        start_monotonic = time.monotonic()
        response = await self._call_with_error_logging(
            acompletion(**kwargs),
            model=model,
            timeout_value=kwargs["timeout"],
            context=context,
            span_name=span_name,
            attempt=attempt,
        )
        latency_ms = stopwatch_ms_since(start_monotonic)
        record_llm_call(
            span=span_name,
            model=model,
            response=response,
            start_monotonic=start_monotonic,
            latency_ms=latency_ms,
            attempt=attempt,
            fallback_used=_is_fallback_attempt(span_metadata),
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length), model=%s, max_tokens=%d",
                model,
                kwargs["max_tokens"],
            )
        content = choice.message.content or ""
        if span_name:
            record_generation(
                span_name=span_name,
                model=model,
                msg_dicts=kwargs["messages"],
                content=content,
                response=response,
                latency_ms=latency_ms,
                finish_reason=choice.finish_reason,
                extra_metadata=span_metadata,
                model_parameters=_model_parameters(kwargs),
            )
        return content

    async def complete(
        self,
        prompt: UserContent,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        json_mode: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generate completion from prompt.

        Args:
            prompt: User prompt (dynamic part) — a string, or a list of content
                blocks (``llm_messages.text_block``) where one block may end a
                cached prefix (``text_block(..., cache=True)``): honoured on
                Anthropic, stripped for every other provider.
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt (never carries a breakpoint)
            metadata: Optional metadata for tracking (user_id, feature, etc.)
            json_mode: Request JSON-only output
            temperature: Sampling temperature; ``None`` sends none (provider default).

        Returns:
            Generated text content
        """
        messages = build_prompt_messages(prompt, system_prompt=system_prompt)
        return await self.complete_with_messages(
            messages,
            max_tokens,
            metadata,
            timeout=timeout,
            json_mode=json_mode,
            span_name=span_name,
            span_metadata=span_metadata,
            temperature=temperature,
        )

    async def complete_fast(
        self,
        prompt: UserContent,
        max_tokens: int = 50,
        timeout: float = 5.0,
        json_mode: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Generate quick completion using fast model.

        Uses the fast model (e.g., Haiku) for simple classifications
        and quick responses. Has shorter timeout than regular complete().

        Args:
            prompt: User prompt — a string or content blocks (see ``complete``)
            max_tokens: Maximum tokens in response (default 50)
            timeout: Request timeout in seconds (default 5.0)
            temperature: Sampling temperature; ``None`` sends none (provider default).
            system_prompt: Optional system prompt (no cache breakpoint).

        Returns:
            Generated text content

        Raises:
            RateLimitError: Rate limit exceeded
            Timeout: Request timed out
            APIError: General API error
        """
        kwargs: dict[str, Any] = {
            "model": self._fast_model,
            "messages": build_prompt_messages(prompt, system_prompt=system_prompt),
            "max_tokens": max_tokens,
            "timeout": timeout,
            "num_retries": _LITELLM_NUM_RETRIES,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return await self._run_completion(
            _with_sampling(kwargs, temperature),
            span_name=span_name,
            span_metadata=span_metadata,
            context="fast model",
        )

    async def complete_with_messages(
        self,
        messages: Sequence[dict[str, Any] | Message],
        max_tokens: int = 2000,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        json_mode: bool = False,
        use_fast_model: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generate completion from message list.

        Args:
            messages: List of messages (dict or Message objects). Content may be
                a list of blocks with ``cache_control`` (system and/or user):
                sent as-is to Anthropic, stripped for every other provider.
            max_tokens: Maximum tokens in response
            metadata: Optional metadata for tracking
            use_fast_model: When True, route to ``self._fast_model``
                (Haiku/mini/flash-lite). Used by callers that don't need
                primary-model quality (frame vision, chapter detection).
            span_name: When non-None, the result is recorded as a Langfuse
                generation span under this name. Best-effort — observability
                failures are swallowed.
            span_metadata: Extra metadata merged into the generation span.
            temperature: Sampling temperature; ``None`` sends none (provider default).

        Returns:
            Generated text content
        """
        effective_model = self._fast_model if use_fast_model else self._model
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": to_message_dicts(messages),
            "max_tokens": max_tokens,
            "timeout": timeout if timeout is not None else self._timeout,
            "num_retries": _LITELLM_NUM_RETRIES,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if metadata:
            kwargs["metadata"] = metadata
        return await self._run_completion(
            _with_sampling(kwargs, temperature),
            span_name=span_name,
            span_metadata=span_metadata,
        )


# Default provider instance (can be overridden via DI)
_default_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get or create default LLM provider instance."""
    global _default_provider
    if _default_provider is None:
        _default_provider = LLMProvider()
    return _default_provider
