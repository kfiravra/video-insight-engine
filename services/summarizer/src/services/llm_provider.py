"""LiteLLM-based multi-provider LLM abstraction.

Provides unified API for calling LLMs across Anthropic, OpenAI, and Gemini
with cost tracking. One request = one model: retries and the cross-provider
fallback live in ``call_llm_with_retry``, never inside LiteLLM, so every
attempt is recorded under the model that actually answered.

Every request is shaped for the model it goes to (``llm_model_rules``:
thinking off + effort, sampling params it accepts, no prefill). An answer the
model declines (a refusal, ``llm_common.refusal``) is asked for once more in
the same call on its previous model (``config.PREVIOUS_MODEL``) — never again
on the model that refused; when that is not possible the call raises
``LLMRefusalError``, which no retry re-sends.
"""

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from litellm import acompletion
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from llm_common.context import llm_refused_model_var
from llm_common.refusal import Refusal, refusal_of

from src.config import PREVIOUS_MODEL, settings
from src.services.llm_call import (
    ModelAnswer,
    ProviderCall,
    base_request,
    is_fallback_attempt,
    model_parameters,
    note_refused_resend,
    record_refused,
)
from src.services.llm_messages import (
    Message,
    UserContent,
    build_prompt_messages,
    to_message_dicts,
)
from src.services.llm_model_rules import (
    DEFAULT_EFFORT,
    Effort,
    LLMRefusalError,
    request_for_model,
)
from src.services.llm_streaming import stream_completion
from src.services.llm_telemetry import record_generation, stopwatch_ms_since
from src.services.pipeline.pipeline_timing import record_llm_call, record_llm_failure

logger = logging.getLogger(__name__)


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

    async def _send(
        self,
        kwargs: dict[str, Any],
        on_first_chunk: Callable[[], None] | None,
        *,
        context: str,
        span_name: str | None,
        attempt: int,
    ) -> tuple[Any, int | None]:
        """One request and its time to first chunk (only known when streamed).

        Streamed only when the caller asked for its first chunk; every other
        request goes out as the plain ``acompletion(**kwargs)`` it always was.
        """
        logged: dict[str, Any] = {
            "model": kwargs["model"],
            "timeout_value": kwargs["timeout"],
            "context": context,
            "span_name": span_name,
            "attempt": attempt,
        }
        if on_first_chunk is None:
            return await self._call_with_error_logging(acompletion(**kwargs), **logged), None
        streamed = await self._call_with_error_logging(
            stream_completion(acompletion, kwargs, on_first_chunk), **logged
        )
        return streamed.response, streamed.ttft_ms

    async def _ask(self, call: ProviderCall, model: str) -> ModelAnswer:
        """Send ``call`` to ``model``, shaped by that model's request rules."""
        sent = request_for_model(
            {**call.request, "model": model}, temperature=call.temperature, effort=call.effort
        )
        start_monotonic = time.monotonic()
        response, ttft_ms = await self._send(
            sent,
            call.on_first_chunk,
            context=call.context,
            span_name=call.span_name,
            attempt=call.attempt,
        )
        latency_ms = stopwatch_ms_since(start_monotonic)
        return ModelAnswer(model, sent, response, start_monotonic, latency_ms, ttft_ms)

    def _log_generation(
        self,
        call: ProviderCall,
        answer: ModelAnswer,
        content: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """The Langfuse generation of one answer (only for a named span)."""
        if not call.span_name:
            return
        metadata = {**(call.span_metadata or {}), **extra} if extra else call.span_metadata
        record_generation(
            span_name=call.span_name,
            model=answer.model,
            msg_dicts=answer.sent["messages"],
            content=content,
            response=answer.response,
            latency_ms=answer.latency_ms,
            finish_reason=answer.response.choices[0].finish_reason,
            extra_metadata=metadata,
            model_parameters=model_parameters(answer.sent),
        )

    def _accept(
        self,
        call: ProviderCall,
        answer: ModelAnswer,
        *,
        fallback_used: bool,
        refused: tuple[str, Refusal] | None = None,
    ) -> str:
        """Record an answer (timing, Langfuse) and return its text — text blocks only."""
        refused_model, refusal = refused if refused is not None else (None, None)
        category = refusal.category if refusal is not None else None
        record_llm_call(
            span=call.span_name,
            model=answer.model,
            response=answer.response,
            start_monotonic=answer.start_monotonic,
            latency_ms=answer.latency_ms,
            attempt=call.attempt,
            fallback_used=fallback_used,
            ttft_ms=answer.ttft_ms,
            refused_model=refused_model,
            refusal_category=category,
        )
        choice = answer.response.choices[0]
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length), model=%s, max_tokens=%d",
                answer.model,
                answer.sent["max_tokens"],
            )
        # LiteLLM joins the text blocks into ``content``; thinking blocks stay in
        # ``reasoning_content`` / ``thinking_blocks`` and are never the answer.
        content = choice.message.content or ""
        extra = {"refusedModel": refused_model, "refusalCategory": category} if refused else None
        self._log_generation(call, answer, content, extra)
        return content

    def _record_refusal(
        self,
        call: ProviderCall,
        answer: ModelAnswer,
        refusal: Refusal,
        refused_model: str | None = None,
    ) -> None:
        category = refusal.category
        record_refused(call, answer, category, refused_model)
        logger.warning("%s refused (span=%s, category=%s)", answer.model, call.span_name, category)
        partial = answer.response.choices[0].message.content or ""
        self._log_generation(call, answer, partial, {"refusal": True, "refusalCategory": category})

    async def _answer_for_refused(
        self, call: ProviderCall, refused_model: str, refusal: Refusal
    ) -> str:
        """The previous model's answer, asked for once in the same call.

        No previous model, its refusal, its error or the call's deadline (``call_deadline``)
        → ``LLMRefusalError``: the refused model is never asked again, no retry re-sends it.
        """
        previous = PREVIOUS_MODEL.get(refused_model)
        if previous is None:
            raise LLMRefusalError(refused_model, refusal.category)
        logger.warning("Asking %s once for the answer %s refused", previous, refused_model)
        note_refused_resend(call, refused_model, refusal.category, previous)
        token = llm_refused_model_var.set(refused_model)
        try:
            answer = await self._ask(call, previous)
        except Exception as e:
            raise LLMRefusalError(refused_model, refusal.category) from e
        finally:
            llm_refused_model_var.reset(token)
        second = refusal_of(answer.response)
        if second is not None:
            self._record_refusal(call, answer, second, refused_model)
            raise LLMRefusalError(previous, second.category)
        return self._accept(call, answer, fallback_used=True, refused=(refused_model, refusal))

    async def _run_completion(self, call: ProviderCall) -> str:
        """Send the call (and, on a refusal, its one fallback); return the answer's text."""
        answer = await self._ask(call, call.request["model"])
        refusal = refusal_of(answer.response)
        if refusal is None:
            return self._accept(call, answer, fallback_used=is_fallback_attempt(call.span_metadata))
        self._record_refusal(call, answer, refusal)
        return await self._answer_for_refused(call, answer.model, refusal)

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
        on_first_chunk: Callable[[], None] | None = None,
        effort: Effort = DEFAULT_EFFORT,
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
            on_first_chunk: Stream the reply and call this at its first chunk
                (``llm_streaming``); ``None`` sends the request unstreamed.
            effort: Effort for a model that takes one (``llm_model_rules``).

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
            on_first_chunk=on_first_chunk,
            effort=effort,
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
        effort: Effort = DEFAULT_EFFORT,
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
            effort: Effort for a model that takes one (``llm_model_rules``).

        Returns:
            Generated text content

        Raises:
            RateLimitError: Rate limit exceeded
            Timeout: Request timed out
            APIError: General API error
        """
        messages = build_prompt_messages(prompt, system_prompt=system_prompt)
        request = base_request(self._fast_model, messages, max_tokens, timeout, json_mode)
        return await self._run_completion(
            ProviderCall(
                request=request,
                temperature=temperature,
                effort=effort,
                span_name=span_name,
                span_metadata=span_metadata,
                context="fast model",
            )
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
        on_first_chunk: Callable[[], None] | None = None,
        effort: Effort = DEFAULT_EFFORT,
    ) -> str:
        """Generate completion from message list; returns the generated text.

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
            on_first_chunk: Stream the reply, calling this at its first chunk.
            effort: Effort for a model that takes one (``llm_model_rules``).
        """
        model = self._fast_model if use_fast_model else self._model
        call_timeout = timeout if timeout is not None else self._timeout
        request = base_request(
            model, to_message_dicts(messages), max_tokens, call_timeout, json_mode, metadata
        )
        return await self._run_completion(
            ProviderCall(
                request=request,
                temperature=temperature,
                effort=effort,
                span_name=span_name,
                span_metadata=span_metadata,
                on_first_chunk=on_first_chunk,
            )
        )


# Default provider instance (can be overridden via DI)
_default_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get or create default LLM provider instance."""
    global _default_provider
    if _default_provider is None:
        _default_provider = LLMProvider()
    return _default_provider
