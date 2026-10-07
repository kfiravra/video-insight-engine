"""LLM call wrapper with timeout, retry, and structured logging.

Provides a single function that every pipeline stage uses for LLM calls.
Handles transient failures (timeout, rate limit, API errors) with
linear backoff (1s, 2s, ...). Returns raw string or None (never raises).
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING, overload

from litellm.exceptions import (
    APIError as LitellmAPIError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LitellmTimeout,
)

from src.services.pipeline.pipeline_timing import record_llm_failure

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.services.llm_messages import TextBlock, UserContent

logger = logging.getLogger(__name__)

# Conservative character limits per model family (leaves headroom for system overhead).
# These are safety nets — chunking should prevent them from triggering.
MODEL_CHAR_LIMITS: dict[str, int] = {
    "anthropic/claude-sonnet-4-6": 600_000,
    "anthropic/claude-haiku-4-5-20251001": 600_000,
    "openai/gpt-4o": 380_000,
    "openai/gpt-4o-mini": 380_000,
    "openai/gpt-5-mini": 380_000,
    "gemini/gemini-2.5-flash": 3_000_000,
    "gemini/gemini-2.5-flash-lite": 3_000_000,
}

DEFAULT_CHAR_LIMIT = 300_000

_TRUNCATION_MARKER = "\n\n[TRANSCRIPT TRUNCATED DUE TO LENGTH]"


@lru_cache(maxsize=8)
def _wrap_with_override(model: str) -> LLMService:
    """Build (and cache) a one-off LLMService pinned to ``model``.

    Used by ``call_llm_with_retry`` when the caller passes ``model_override``.
    The cache keeps allocations off the hot path — there are at most ~7
    override targets (one per pipeline stage), and the cached ``LLMService``
    holds no per-call state. Local import keeps the heavy LLMProvider /
    LLMService imports out of module-import time (this module is imported by
    every pipeline stage).
    """
    from src.services.llm import LLMService as _LLMService
    from src.services.llm_provider import LLMProvider as _LLMProvider

    provider = _LLMProvider(model=model, fast_model=model)
    return _LLMService(provider)


def _prompt_chars(prompt: UserContent) -> int:
    if isinstance(prompt, str):
        return len(prompt)
    return sum(len(block["text"]) for block in prompt)


def _truncate_longest_block(blocks: list[TextBlock], overflow: int) -> list[TextBlock]:
    """Cut ``overflow`` chars off the longest block — the transcript — keeping its breakpoint."""
    longest = max(range(len(blocks)), key=lambda i: len(blocks[i]["text"]))
    text = blocks[longest]["text"]
    cut: TextBlock = {**blocks[longest], "text": text[: max(len(text) - overflow, 0)]}
    cut["text"] += _TRUNCATION_MARKER
    return [cut if i == longest else block for i, block in enumerate(blocks)]


@overload
def truncate_prompt_if_needed(prompt: str, model: str) -> str: ...
@overload
def truncate_prompt_if_needed(prompt: list[TextBlock], model: str) -> list[TextBlock]: ...
def truncate_prompt_if_needed(prompt: UserContent, model: str) -> UserContent:
    """Truncate prompt to model's character limit if exceeded.

    This is a safety net — chunked extraction should prevent it from
    ever triggering. Logs a warning when truncation occurs. A content-block
    prompt is measured across all blocks and loses the overflow from its
    longest block.
    """
    limit = MODEL_CHAR_LIMITS.get(model, DEFAULT_CHAR_LIMIT)
    size = _prompt_chars(prompt)
    if size <= limit:
        return prompt
    logger.warning(
        "Prompt truncated from %d to %d chars for model %s (limit=%d)",
        size,
        limit,
        model,
        limit,
    )
    if isinstance(prompt, str):
        return prompt[:limit] + _TRUNCATION_MARKER
    return _truncate_longest_block(prompt, size - limit)


async def call_llm_with_retry(
    llm_service: LLMService,
    prompt: UserContent,
    *,
    max_tokens: int = 4096,
    timeout: float = 60.0,
    max_retries: int = 2,
    stage_name: str = "unknown",
    use_fast_model: bool = False,
    json_mode: bool = False,
    cache_static: str | None = None,
    propagate_rate_limit: bool = False,
    model_override: str | None = None,
    temperature: float | None = None,
    system_prompt: str | None = None,
) -> str | None:
    """Call LLM with timeout and retry. Returns raw string or None.

    Args:
        llm_service: LLMService instance with call_llm / call_llm_fast methods.
        prompt: The prompt to send — a string, or content blocks where one
            block ends an Anthropic cached prefix (``llm_messages.text_block``).
        max_tokens: Maximum tokens in response.
        timeout: Per-attempt timeout in seconds.
        max_retries: Maximum retry attempts (0 = no retries).
        stage_name: Name for logging (e.g., "triage", "extraction").
        use_fast_model: When True, route to the fast model (Haiku/mini/flash-lite).
        propagate_rate_limit: When True, re-raise the terminal
            ``RateLimitError`` / ``ServiceUnavailableError`` after exhausting
            retries instead of returning ``None``. Used by the parallel batch
            extractor so it can route rate-limited batches into the sequential
            fallback. Default ``False`` preserves the "never raises" contract
            for every other call site.
        model_override: When non-None, wrap ``llm_service`` with a one-off
            provider pinned to this model. Stages look up their per-stage
            override via ``settings.get_stage_model(stage_name)`` and pass
            it here explicitly. Default ``None`` means "use the service as
            given" — keeps tests that pass a ``MagicMock`` for ``llm_service``
            working with no extra setup.
        temperature: Sampling temperature forwarded on every attempt (also on
            the ``model_override`` path); ``None`` sends none, i.e. the
            provider default.
        system_prompt: System text sent WITHOUT a cache breakpoint — the
            alternative to ``cache_static`` (system text WITH one) when only a
            user-block breakpoint is wanted.

    Returns:
        Raw LLM response string, or None if all attempts failed.
    """
    if model_override:
        llm_service = _wrap_with_override(model_override)

    # Safety net: truncate oversized prompts
    model_name = llm_service.fast_model if use_fast_model else llm_service.model
    prompt = truncate_prompt_if_needed(prompt, model_name)

    last_rate_limit_error: RateLimitError | ServiceUnavailableError | None = None

    for attempt in range(max_retries + 1):
        start = time.monotonic()
        span_metadata = {
            "attempt": attempt + 1,
            "maxAttempts": max_retries + 1,
            "useFastModel": use_fast_model,
            "modelOverride": model_override,
        }
        try:
            if use_fast_model:
                raw = await llm_service.call_llm_fast(
                    prompt,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    json_mode=json_mode,
                    span_name=stage_name,
                    span_metadata=span_metadata,
                    temperature=temperature,
                    system_prompt=system_prompt,
                )
            else:
                raw = await llm_service.call_llm(
                    prompt,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    json_mode=json_mode,
                    cache_static=cache_static,
                    span_name=stage_name,
                    span_metadata=span_metadata,
                    temperature=temperature,
                    system_prompt=system_prompt,
                )
            duration = time.monotonic() - start

            if raw and raw.strip():
                logger.info(
                    "[%s] LLM call succeeded in %.1fs (attempt %d/%d, model=%s)",
                    stage_name,
                    duration,
                    attempt + 1,
                    max_retries + 1,
                    model_name,
                )
                return raw

            logger.warning(
                "[%s] Empty LLM response in %.1fs (attempt %d/%d)",
                stage_name,
                duration,
                attempt + 1,
                max_retries + 1,
            )

        except asyncio.TimeoutError as e:
            # The outer asyncio.timeout cancels the provider coroutine, so the
            # provider never sees this failure — record it here.
            record_llm_failure(
                span=stage_name,
                model=model_name,
                error=e,
                start_monotonic=start,
                attempt=attempt + 1,
            )
            duration = time.monotonic() - start
            logger.warning(
                "[%s] Timeout after %.1fs (attempt %d/%d)",
                stage_name,
                duration,
                attempt + 1,
                max_retries + 1,
            )

        except (LitellmAPIError, RateLimitError, LitellmTimeout, OSError, ConnectionError) as e:
            # Transient LLM/network errors — safe to retry.
            # Programming errors (TypeError, AttributeError, etc.) propagate immediately.
            duration = time.monotonic() - start
            logger.warning(
                "[%s] LLM error in %.1fs: %s (attempt %d/%d)",
                stage_name,
                duration,
                str(e)[:200],
                attempt + 1,
                max_retries + 1,
            )
            if propagate_rate_limit and isinstance(e, (RateLimitError, ServiceUnavailableError)):
                last_rate_limit_error = e

        if attempt < max_retries:
            backoff = 1.0 * (attempt + 1)
            logger.info("[%s] Retrying in %.0fs...", stage_name, backoff)
            await asyncio.sleep(backoff)

    logger.error("[%s] All %d attempts failed", stage_name, max_retries + 1)
    if propagate_rate_limit and last_rate_limit_error is not None:
        raise last_rate_limit_error
    return None
