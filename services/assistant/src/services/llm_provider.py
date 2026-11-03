"""LiteLLM-based multi-provider LLM abstraction for the assistant service.

Provides unified API for calling LLMs across Anthropic, OpenAI, and Gemini
with built-in fallbacks, retries, and streaming support.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from litellm import acompletion, completion_cost
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError as LiteLLMServiceUnavailable,
    Timeout,
)

from src.config import settings
from src.exceptions import LLMError
from src.logging_config import get_logger
from src.services.llm_generation import CallGeneration, call_generation

logger = get_logger(__name__)


@dataclass
class ToolCompletion:
    """Result of a tool-enabled completion.

    ``content`` is the model's natural-language reply (``None`` when the model
    chose to call tools instead). ``tool_calls`` is a parsed list of
    ``{"id", "name", "arguments"}`` dicts — empty when the model produced a
    plain answer.
    """

    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class LLMProvider:
    """Multi-provider LLM abstraction using LiteLLM.

    Supports Anthropic, OpenAI, and Gemini with automatic fallbacks.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float | None = None,
        num_retries: int | None = None,
    ) -> None:
        self._model = model or settings.llm_model
        self._fallback_models = fallback_models or settings.llm_fallback_models
        self._timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        self._num_retries = num_retries if num_retries is not None else settings.LLM_NUM_RETRIES

    @property
    def model(self) -> str:
        """Get the configured model."""
        return self._model

    def _extract_provider(self, model: str) -> str:
        """Extract provider from model string."""
        return model.split("/")[0] if "/" in model else "unknown"

    async def complete_with_messages(
        self,
        messages: list[dict],
        max_tokens: int = 2000,
        *,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Generate completion from message list.

        Args:
            messages: List of message dicts with role and content.
            max_tokens: Maximum tokens in response.
            span_name: When non-None, record the call as a Langfuse generation
                span. Best-effort — observability failures are swallowed.
            span_metadata: Extra metadata merged into the generation span.

        Returns:
            Generated text content.

        Raises:
            LLMError: On any LLM provider failure.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": self._timeout,
            "num_retries": self._num_retries,
        }
        if self._fallback_models:
            kwargs["fallbacks"] = self._fallback_models

        try:
            with call_generation(self._model, span_name, messages, span_metadata) as call:
                start_monotonic = time.monotonic()
                # litellm types this as ModelResponse | CustomStreamWrapper; with
                # stream unset it's always a ModelResponse — Any avoids the union.
                response: Any = await acompletion(**kwargs)
                latency_ms = int((time.monotonic() - start_monotonic) * 1000)
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    logger.warning(
                        "llm_response_truncated",
                        model=self._model,
                        max_tokens=max_tokens,
                    )
                content = choice.message.content or ""
                if span_name:
                    usage = getattr(response, "usage", None)
                    call.succeed(
                        content=content,
                        input_tokens=_usage_int(usage, "prompt_tokens"),
                        output_tokens=_usage_int(usage, "completion_tokens"),
                        cost_usd=_safe_completion_cost(response=response),
                        latency_ms=latency_ms,
                        finish_reason=choice.finish_reason,
                        extra_metadata=span_metadata,
                    )
                return content
        except (RateLimitError, Timeout, LiteLLMServiceUnavailable, APIError) as exc:
            logger.error(
                "llm_completion_failed",
                model=self._model,
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM completion failed: {exc}") from exc
        except AuthenticationError as exc:
            logger.error(
                "llm_auth_error",
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM authentication failed: {exc}") from exc

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2000,
        *,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> ToolCompletion:
        """Generate a completion that may call tools (LLM function-calling).

        Calls ``acompletion`` with the same kwargs as
        :meth:`complete_with_messages` plus ``tools`` and ``tool_choice="auto"``,
        letting the model either answer directly or request tool calls.

        Args:
            messages: List of message dicts with role and content.
            tools: OpenAI-format function schemas.
            max_tokens: Maximum tokens in response.
            span_name: When non-None, record the call as a Langfuse generation.
            span_metadata: Extra metadata merged into the generation span.

        Returns:
            A :class:`ToolCompletion` with ``content`` (text reply or ``None``)
            and parsed ``tool_calls`` (empty when the model answered directly).

        Raises:
            LLMError: On any LLM provider failure.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": self._timeout,
            "num_retries": self._num_retries,
            "tools": tools,
            "tool_choice": "auto",
        }
        if self._fallback_models:
            kwargs["fallbacks"] = self._fallback_models

        try:
            with call_generation(self._model, span_name, messages, span_metadata) as call:
                start_monotonic = time.monotonic()
                # litellm types this as ModelResponse | CustomStreamWrapper; with
                # stream unset it's always a ModelResponse — Any avoids the union.
                response: Any = await acompletion(**kwargs)
                latency_ms = int((time.monotonic() - start_monotonic) * 1000)
                choice = response.choices[0]
                message = choice.message
                content = message.content or None
                tool_calls = _parse_tool_calls(getattr(message, "tool_calls", None))
                if span_name:
                    usage = getattr(response, "usage", None)
                    call.succeed(
                        content=content or "",
                        input_tokens=_usage_int(usage, "prompt_tokens"),
                        output_tokens=_usage_int(usage, "completion_tokens"),
                        cost_usd=_safe_completion_cost(response=response),
                        latency_ms=latency_ms,
                        finish_reason=choice.finish_reason,
                        extra_metadata={"toolCallCount": len(tool_calls), **(span_metadata or {})},
                    )
                return ToolCompletion(content=content, tool_calls=tool_calls)
        except (RateLimitError, Timeout, LiteLLMServiceUnavailable, APIError) as exc:
            logger.error(
                "llm_tool_completion_failed",
                model=self._model,
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM tool completion failed: {exc}") from exc
        except AuthenticationError as exc:
            logger.error(
                "llm_auth_error_tools",
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM authentication failed: {exc}") from exc

    async def stream_with_messages(
        self,
        messages: list[dict],
        max_tokens: int = 2000,
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream completion tokens from message list.

        Args:
            messages: List of message dicts with role and content.
            max_tokens: Maximum tokens in response.
            tools: When the conversation references prior tool calls, the same
                tool schemas must be re-declared or Anthropic rejects the
                request (400). Pair with ``tool_choice="none"`` to forbid
                further calls and force a final text answer.
            tool_choice: Tool-selection mode (e.g. ``"none"``); only applied
                when ``tools`` is provided.
            span_name: When non-None, record the stream as a Langfuse generation
                on completion. ``stream_options={"include_usage": True}`` is
                added so OpenAI/compatible providers emit a final usage chunk;
                Anthropic/Gemini emit usage natively via LiteLLM normalisation.
            span_metadata: Extra metadata merged into the generation log.

        Yields:
            String tokens as generated.

        Raises:
            LLMError: On any LLM provider failure.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": self._timeout,
            "num_retries": self._num_retries,
            "stream": True,
        }
        if tools is not None:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if self._fallback_models:
            kwargs["fallbacks"] = self._fallback_models
        if span_name:
            kwargs["stream_options"] = {"include_usage": True}

        with call_generation(self._model, span_name, messages, span_metadata) as call:
            # ``aclosing``: a client that stops reading closes the provider stream now.
            async with aclosing(
                self._stream_tokens(kwargs, span_name, span_metadata, call)
            ) as tokens:
                async for token in tokens:
                    yield token

    async def _stream_tokens(
        self,
        kwargs: dict[str, Any],
        span_name: str | None,
        span_metadata: dict[str, Any] | None,
        call: CallGeneration,
    ) -> AsyncGenerator[str, None]:
        """The stream behind :meth:`stream_with_messages`; finishes ``call`` on the last chunk."""
        start_monotonic = time.monotonic()
        accumulated: list[str] = []
        usage: Any = None
        finish_reason: str | None = None

        try:
            # stream=True → CustomStreamWrapper (async-iterable); Any avoids
            # litellm's ModelResponse | CustomStreamWrapper union.
            response: Any = await acompletion(**kwargs)
            async for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    content = getattr(delta, "content", None) if delta else None
                    if content:
                        accumulated.append(content)
                        yield content
                    fr = getattr(choice, "finish_reason", None)
                    if fr:
                        finish_reason = fr
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = chunk_usage
        except (RateLimitError, Timeout, LiteLLMServiceUnavailable, APIError) as exc:
            logger.error(
                "llm_stream_failed",
                model=self._model,
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM stream failed: {exc}") from exc
        except AuthenticationError as exc:
            logger.error(
                "llm_auth_error_stream",
                provider=self._extract_provider(self._model),
                error=str(exc),
            )
            raise LLMError(f"LLM authentication failed: {exc}") from exc

        if span_name:
            input_tokens = _usage_int(usage, "prompt_tokens")
            output_tokens = _usage_int(usage, "completion_tokens")
            call.succeed(
                content="".join(accumulated),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=_safe_completion_cost(
                    model=self._model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                ),
                latency_ms=int((time.monotonic() - start_monotonic) * 1000),
                finish_reason=finish_reason,
                extra_metadata={"streamed": True, **(span_metadata or {})},
            )

    async def translate_to_english(self, text: str) -> str | None:
        """Translate a short text to English for RAG search.

        Args:
            text: Non-English text to translate.

        Returns:
            English translation, or None on failure.
        """
        messages = [
            {
                "role": "system",
                "content": "Translate the following text to English. "
                "Return only the translation, nothing else.",
            },
            {"role": "user", "content": text},
        ]
        try:
            result = await self.complete_with_messages(
                messages,
                max_tokens=500,
                span_name="query_translate",
            )
            return result.strip() if result else None
        except LLMError:
            # complete_with_messages already logs the detailed error
            return None


def _parse_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    """Normalise LiteLLM ``message.tool_calls`` into plain dicts.

    Each returned item is ``{"id", "name", "arguments"}`` where ``arguments``
    is the parsed JSON object from ``fn.arguments`` (``{}`` on empty/invalid
    JSON, so a malformed call surfaces to the model as empty args rather than
    crashing the loop). Returns ``[]`` for ``None``/empty input.
    """
    if not raw_calls:
        return []
    parsed: list[dict[str, Any]] = []
    for call in raw_calls:
        fn = getattr(call, "function", None)
        if fn is None:
            continue
        try:
            arguments = json.loads(fn.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        parsed.append(
            {
                "id": getattr(call, "id", None),
                "name": fn.name,
                "arguments": arguments,
            }
        )
    return parsed


def _usage_int(usage: Any, attr: str) -> int:
    """``usage.attr`` when it is a real int (MagicMock attributes are not)."""
    value = getattr(usage, attr, 0) if usage is not None else 0
    return value if isinstance(value, int) else 0


def _safe_completion_cost(
    *,
    response: Any | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float:
    """Best-effort wrapper around ``litellm.completion_cost``.

    LiteLLM raises for models without published pricing; this helper returns
    ``0.0`` instead so observability never breaks an LLM call.
    """
    try:
        if response is not None:
            return float(completion_cost(completion_response=response) or 0.0)
        if model and (prompt_tokens or completion_tokens):
            return float(
                completion_cost(
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                or 0.0
            )
    except Exception:  # noqa: BLE001  # litellm raises for unknown-cost models
        return 0.0
    return 0.0
