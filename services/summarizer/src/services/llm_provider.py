"""LiteLLM-based multi-provider LLM abstraction.

Provides unified API for calling LLMs across Anthropic, OpenAI, and Gemini
with built-in fallbacks, retries, and cost tracking.
"""

import logging
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from litellm import acompletion, completion_cost
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from pydantic import BaseModel

from src.config import settings
from src.services.llm_telemetry import record_generation, stopwatch_ms_since
from src.services.pipeline.pipeline_timing import record_llm_call, record_llm_failure

logger = logging.getLogger(__name__)

# LiteLLM num_retries is disabled because retries are handled by
# call_llm_with_retry() which adds structured logging, per-stage
# timeouts, and linear backoff.
_LITELLM_NUM_RETRIES = 0

_PROVIDER_ERRORS = (RateLimitError, AuthenticationError, Timeout, ServiceUnavailableError, APIError)


def _attempt_of(span_metadata: dict[str, Any] | None) -> int:
    """Retry attempt number stamped by ``call_llm_with_retry`` (1 when absent)."""
    attempt = (span_metadata or {}).get("attempt", 1)
    return attempt if isinstance(attempt, int) else 1


class Message(BaseModel):
    """Chat message for LLM conversation."""

    role: str  # "system", "user", "assistant"
    content: str


class CompletionResult(BaseModel):
    """Result from an LLM completion."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int


class LLMProvider:
    """Multi-provider LLM abstraction using LiteLLM.

    Supports Anthropic, OpenAI, and Gemini with automatic fallbacks.
    """

    def __init__(
        self,
        model: str | None = None,
        fast_model: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ):
        """Initialize LLM provider.

        Args:
            model: Model to use (e.g., "anthropic/claude-sonnet-4-6")
            fast_model: Fast model for quick tasks (e.g., "anthropic/claude-haiku-4-5-20251001")
            fallback_models: List of fallback models if primary fails
            timeout: Request timeout in seconds
            num_retries: Number of retries on failure
        """
        self._model = model or settings.llm_model
        self._fast_model = fast_model or settings.llm_fast_model
        self._fallback_models = fallback_models or settings.llm_fallback_models
        self._timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        self._num_retries = num_retries if num_retries is not None else settings.LLM_NUM_RETRIES
        self._is_anthropic_model = self._model.startswith("anthropic/")

    @property
    def model(self) -> str:
        """Get the configured model."""
        return self._model

    @property
    def fast_model(self) -> str:
        """Get the configured fast model for quick tasks."""
        return self._fast_model

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

        Catches LiteLLM errors, logs with context, and re-raises.

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
        except _PROVIDER_ERRORS as e:
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
        else:
            logger.error("API error%s: %s", ctx, e)

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        json_mode: bool = False,
        cache_static: str | None = None,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Generate completion from prompt.

        Args:
            prompt: User prompt (dynamic part)
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt
            metadata: Optional metadata for tracking (user_id, feature, etc.)
            json_mode: Request JSON-only output
            cache_static: Static prompt content to cache (Anthropic prompt caching).
                When provided, the static part is sent as a system message with
                cache_control, and the dynamic prompt as the user message.

        Returns:
            Generated text content
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if cache_static and self._is_anthropic_model:
            # Split into cacheable system block + dynamic user block
            messages.append({
                "role": "system",
                "content": [
                    {"type": "text", "text": cache_static, "cache_control": {"type": "ephemeral"}},
                ],
            })
            messages.append({"role": "user", "content": prompt})
        else:
            # Non-Anthropic or no caching: single user message
            if cache_static:
                messages.append({"role": "user", "content": cache_static + "\n\n" + prompt})
            else:
                messages.append({"role": "user", "content": prompt})

        return await self.complete_with_messages(
            messages, max_tokens, metadata, timeout=timeout, json_mode=json_mode,
            span_name=span_name, span_metadata=span_metadata,
        )

    async def complete_fast(
        self,
        prompt: str,
        max_tokens: int = 50,
        timeout: float = 5.0,
        json_mode: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Generate quick completion using fast model.

        Uses the fast model (e.g., Haiku) for simple classifications
        and quick responses. Has shorter timeout than regular complete().

        Args:
            prompt: User prompt
            max_tokens: Maximum tokens in response (default 50)
            timeout: Request timeout in seconds (default 5.0)

        Returns:
            Generated text content

        Raises:
            RateLimitError: Rate limit exceeded
            Timeout: Request timed out
            APIError: General API error
        """
        msg_dicts = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {
            "model": self._fast_model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "num_retries": _LITELLM_NUM_RETRIES,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start_monotonic = time.monotonic()
        response = await self._call_with_error_logging(
            acompletion(**kwargs),
            model=self._fast_model,
            timeout_value=timeout,
            context="fast model",
            span_name=span_name,
            attempt=_attempt_of(span_metadata),
        )
        latency_ms = stopwatch_ms_since(start_monotonic)
        record_llm_call(
            span=span_name,
            model=self._fast_model,
            response=response,
            start_monotonic=start_monotonic,
            latency_ms=latency_ms,
            attempt=_attempt_of(span_metadata),
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length), model=%s, max_tokens=%d",
                self._fast_model, max_tokens,
            )
        content = choice.message.content or ""
        if span_name:
            record_generation(
                span_name=span_name,
                model=self._fast_model,
                msg_dicts=msg_dicts,
                content=content,
                response=response,
                latency_ms=latency_ms,
                finish_reason=choice.finish_reason,
                extra_metadata=span_metadata,
            )
        return content

    async def complete_with_messages(
        self,
        messages: list[dict | Message],
        max_tokens: int = 2000,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        json_mode: bool = False,
        use_fast_model: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Generate completion from message list.

        Args:
            messages: List of messages (dict or Message objects)
            max_tokens: Maximum tokens in response
            metadata: Optional metadata for tracking
            use_fast_model: When True, route to ``self._fast_model``
                (Haiku/mini/flash-lite). Used by callers that don't need
                primary-model quality (frame vision, chapter detection).
            span_name: When non-None, the result is recorded as a Langfuse
                generation span under this name. Best-effort — observability
                failures are swallowed.
            span_metadata: Extra metadata merged into the generation span.

        Returns:
            Generated text content
        """
        # Convert Message objects to dicts
        msg_dicts = [
            m.model_dump() if isinstance(m, Message) else m for m in messages
        ]

        effective_model = self._fast_model if use_fast_model else self._model

        # Build kwargs, only including optional params if set
        effective_timeout = timeout if timeout is not None else self._timeout
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "timeout": effective_timeout,
            "num_retries": _LITELLM_NUM_RETRIES,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        # Fallbacks are configured for the primary model only — they may
        # not be appropriate for the fast model (e.g., a Sonnet fallback
        # behind a Haiku call defeats the cost savings).
        if self._fallback_models and not use_fast_model:
            kwargs["fallbacks"] = self._fallback_models
        if metadata:
            kwargs["metadata"] = metadata

        start_monotonic = time.monotonic()
        response = await self._call_with_error_logging(
            acompletion(**kwargs),
            model=effective_model,
            timeout_value=effective_timeout,
            span_name=span_name,
            attempt=_attempt_of(span_metadata),
        )
        latency_ms = stopwatch_ms_since(start_monotonic)
        record_llm_call(
            span=span_name,
            model=effective_model,
            response=response,
            start_monotonic=start_monotonic,
            latency_ms=latency_ms,
            attempt=_attempt_of(span_metadata),
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length), model=%s, max_tokens=%d",
                effective_model, max_tokens,
            )

        content = choice.message.content or ""
        if span_name:
            record_generation(
                span_name=span_name,
                model=effective_model,
                msg_dicts=msg_dicts,
                content=content,
                response=response,
                latency_ms=latency_ms,
                finish_reason=choice.finish_reason,
                extra_metadata=span_metadata,
            )
        return content

    async def complete_with_tracking(
        self,
        prompt: str,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompletionResult:
        """Generate completion with full tracking info.

        Args:
            prompt: User prompt
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt
            metadata: Optional metadata for tracking

        Returns:
            CompletionResult with content and usage info
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start_time = datetime.now(UTC)

        # Build kwargs, only including optional params if set
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": self._timeout,
            "num_retries": _LITELLM_NUM_RETRIES,
        }
        if self._fallback_models:
            kwargs["fallbacks"] = self._fallback_models
        if metadata:
            kwargs["metadata"] = metadata

        response = await self._call_with_error_logging(
            acompletion(**kwargs),
            context="tracking call",
        )

        if response.choices[0].finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length), model=%s, max_tokens=%d",
                self._model, max_tokens,
            )

        end_time = datetime.now(UTC)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Calculate cost using LiteLLM's built-in function
        cost = 0.0
        try:
            cost = completion_cost(completion_response=response)
        except Exception as e:
            logger.warning("Could not calculate cost: %s", e)

        return CompletionResult(
            content=response.choices[0].message.content or "",
            model=response.model or self._model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cost_usd=cost,
            duration_ms=duration_ms,
        )

    async def stream(
        self,
        prompt: str,
        max_tokens: int = 2000,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream completion tokens from prompt.

        Args:
            prompt: User prompt
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt
            metadata: Optional metadata for tracking

        Yields:
            String tokens as generated
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async for token in self.stream_with_messages(messages, max_tokens, metadata):
            yield token

    async def stream_with_messages(
        self,
        messages: list[dict | Message],
        max_tokens: int = 2000,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream completion tokens from message list.

        Args:
            messages: List of messages
            max_tokens: Maximum tokens in response
            metadata: Optional metadata for tracking

        Yields:
            String tokens as generated
        """
        # Convert Message objects to dicts
        msg_dicts = [
            m.model_dump() if isinstance(m, Message) else m for m in messages
        ]

        try:
            # Build kwargs, only including optional params if set
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": msg_dicts,
                "max_tokens": max_tokens,
                "timeout": self._timeout,
                "num_retries": _LITELLM_NUM_RETRIES,
                "stream": True,
            }
            if self._fallback_models:
                kwargs["fallbacks"] = self._fallback_models
            if metadata:
                kwargs["metadata"] = metadata

            response = await acompletion(**kwargs)

            async for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content

        except RateLimitError as e:
            logger.warning("Rate limited during stream: %s", e)
            raise
        except AuthenticationError as e:
            logger.error("Auth error during stream: %s", e)
            raise
        except Timeout as e:
            logger.warning("Stream timeout: %s", e)
            raise
        except APIError as e:
            logger.error("API error during stream: %s", e)
            raise


# Default provider instance (can be overridden via DI)
_default_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get or create default LLM provider instance."""
    global _default_provider
    if _default_provider is None:
        _default_provider = LLMProvider()
    return _default_provider
