"""LLM call wrapper with timeout, retry, cross-provider fallback and structured logging.

Every pipeline stage calls the LLM through ``call_llm_with_retry``. Transient
failures (timeout, 429, 5xx, connection, empty reply) are retried on the same
model, honouring the provider's ``retry-after`` (else linear 1 s, 2 s, …
backoff); primary-model calls with a cross-provider fallback configured get ONE
same-provider retry and then go to the fallback model. Each attempt is a single
request to a single model, so telemetry records the model that actually
answered. Returns the raw string, or None when every attempt failed; errors
that another attempt cannot fix (400/401/403/404/422, programming errors)
propagate on first occurrence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, overload

from litellm.exceptions import (
    APIConnectionError,
    APIError as LitellmAPIError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LitellmTimeout,
)

from src.services.pipeline.pipeline_timing import record_llm_failure

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.services.llm_messages import TextBlock, UserContent

logger = logging.getLogger(__name__)

# The request was fine, the provider or the network was not — another attempt
# can succeed. ``OSError`` covers ``ConnectionError`` and ``asyncio.TimeoutError``.
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    LitellmTimeout,
    RateLimitError,
    ServiceUnavailableError,
    InternalServerError,
    BadGatewayError,
    APIConnectionError,
    LitellmAPIError,
    OSError,
)

# Waiting longer than this on one provider costs more than it saves: stage
# timeouts are 25-240 s and the fallback (when configured) is one step away.
_RETRY_AFTER_CAP_SECONDS = 20.0

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


# ─── retry-after ─────────────────────────────────────────────────────────────


def _error_headers(error: BaseException) -> dict[str, Any] | None:
    """Provider response headers carried by a LiteLLM exception, lower-cased keys."""
    headers = getattr(error, "litellm_response_headers", None)
    if headers is None:
        headers = getattr(getattr(error, "response", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    return {str(key).lower(): value for key, value in headers.items()}


def _parse_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def retry_after_seconds(error: BaseException) -> float | None:
    """The wait the provider asked for, capped at ``_RETRY_AFTER_CAP_SECONDS``.

    Reads ``retry-after-ms`` (OpenAI) then ``retry-after`` in seconds
    (Anthropic, OpenAI) from the headers LiteLLM attaches to the exception
    (``litellm_response_headers``, else ``response.headers``). None when the
    provider sent neither.
    """
    headers = _error_headers(error)
    if headers is None:
        return None
    for name, seconds_per_unit in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = _parse_seconds(headers.get(name))
        if value is not None:
            return min(value * seconds_per_unit, _RETRY_AFTER_CAP_SECONDS)
    return None


# ─── attempt plan ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Request:
    """Everything one attempt sends, fixed for the whole call."""

    prompt: UserContent
    stage_name: str
    use_fast_model: bool
    model_override: str | None
    max_tokens: int
    timeout: float
    json_mode: bool
    cache_static: str | None
    temperature: float | None
    system_prompt: str | None


@dataclass(frozen=True)
class _Attempt:
    """One attempt: which model answers it, through which service."""

    number: int
    total: int
    service: LLMService
    model: str
    fallback_from: str | None


def _provider_of(model: str) -> str:
    return model.split("/", 1)[0]


def _fallback_model(llm_service: LLMService, primary: str, use_fast_model: bool) -> str | None:
    """The cross-provider fallback for this call, or None.

    Fast-model calls never fall back (a pricier fallback behind a cheap call
    defeats it), and a fallback on the primary's own provider is no fallback.
    ``isinstance`` also screens out the auto-attributes of test doubles.
    """
    if use_fast_model:
        return None
    fallback = getattr(llm_service, "fallback_model", None)
    if not isinstance(fallback, str) or _provider_of(fallback) == _provider_of(primary):
        return None
    return fallback


def _attempt_models(primary: str, fallback: str | None, max_retries: int) -> list[str]:
    """The model answering each attempt.

    No fallback: ``max_retries + 1`` attempts on the primary. With one: the
    first try and ONE same-provider retry, then the remaining retries on the
    fallback — at least one fallback attempt whenever any retry is allowed
    (``max_retries`` 1 → P P F, 2 → P P F, 3 → P P F F). ``max_retries=0``
    stays a single attempt.
    """
    if fallback is None or max_retries == 0:
        return [primary] * (max_retries + 1)
    return [primary, primary] + [fallback] * max(max_retries - 1, 1)


def _plan_attempts(
    llm_service: LLMService, use_fast_model: bool, max_retries: int
) -> list[_Attempt]:
    primary = llm_service.fast_model if use_fast_model else llm_service.model
    models = _attempt_models(
        primary, _fallback_model(llm_service, primary, use_fast_model), max_retries
    )
    attempts: list[_Attempt] = []
    for index, model in enumerate(models):
        is_fallback = model != primary
        attempts.append(
            _Attempt(
                number=index + 1,
                total=len(models),
                service=_wrap_with_override(model) if is_fallback else llm_service,
                model=model,
                fallback_from=primary if is_fallback else None,
            )
        )
    return attempts


# ─── one attempt ─────────────────────────────────────────────────────────────


def _span_metadata(attempt: _Attempt, request: _Request) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "attempt": attempt.number,
        "maxAttempts": attempt.total,
        "useFastModel": request.use_fast_model,
        "modelOverride": request.model_override,
    }
    if attempt.fallback_from:
        metadata["fallbackFrom"] = attempt.fallback_from
    return metadata


async def _send(attempt: _Attempt, request: _Request) -> str:
    prompt = truncate_prompt_if_needed(request.prompt, attempt.model)
    common: dict[str, Any] = {
        "max_tokens": request.max_tokens,
        "timeout": request.timeout,
        "json_mode": request.json_mode,
        "span_name": request.stage_name,
        "span_metadata": _span_metadata(attempt, request),
        "temperature": request.temperature,
        "system_prompt": request.system_prompt,
    }
    if request.use_fast_model:
        return await attempt.service.call_llm_fast(prompt, **common)
    return await attempt.service.call_llm(prompt, cache_static=request.cache_static, **common)


def _log_attempt(level: int, attempt: _Attempt, stage: str, start: float, what: str) -> None:
    fallback = f", fallback from {attempt.fallback_from}" if attempt.fallback_from else ""
    logger.log(
        level,
        "[%s] %s in %.1fs (attempt %d/%d, model=%s%s)",
        stage,
        what,
        time.monotonic() - start,
        attempt.number,
        attempt.total,
        attempt.model,
        fallback,
    )


async def _try_once(
    attempt: _Attempt, request: _Request
) -> tuple[str | None, BaseException | None]:
    """The reply text, or None plus the transient error (None for an empty reply)."""
    stage = request.stage_name
    start = time.monotonic()
    try:
        raw = await _send(attempt, request)
    except asyncio.TimeoutError as e:
        # The outer asyncio.timeout cancels the provider coroutine, so the
        # provider never sees this failure — record it here.
        record_llm_failure(
            span=stage, model=attempt.model, error=e, start_monotonic=start, attempt=attempt.number
        )
        _log_attempt(logging.WARNING, attempt, stage, start, "Timeout")
        return None, e
    except _TRANSIENT_ERRORS as e:
        _log_attempt(logging.WARNING, attempt, stage, start, f"LLM error {str(e)[:200]!r}")
        return None, e
    if raw and raw.strip():
        _log_attempt(logging.INFO, attempt, stage, start, "LLM call succeeded")
        return raw, None
    _log_attempt(logging.WARNING, attempt, stage, start, "Empty LLM response")
    return None, None


def _pause_seconds(current: _Attempt, upcoming: _Attempt, error: BaseException | None) -> float:
    """No wait when switching provider; else the provider's retry-after, else linear backoff."""
    if upcoming.model != current.model:
        return 0.0
    hinted = retry_after_seconds(error) if error is not None else None
    return hinted if hinted is not None else 1.0 * current.number


# ─── entry point ─────────────────────────────────────────────────────────────


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
    """Call LLM with timeout, retry and cross-provider fallback. Returns raw string or None.

    Args:
        llm_service: LLMService instance with call_llm / call_llm_fast methods.
        prompt: The prompt to send — a string, or content blocks where one
            block ends an Anthropic cached prefix (``llm_messages.text_block``).
        max_tokens: Maximum tokens in response.
        timeout: Per-attempt timeout in seconds.
        max_retries: Retry budget (0 = single attempt). With a fallback the
            schedule is P P F … — see ``_attempt_models``.
        stage_name: Name for logging (e.g., "triage", "extraction").
        use_fast_model: When True, route to the fast model (never falls back).
        propagate_rate_limit: When True, re-raise the last
            ``RateLimitError`` / ``ServiceUnavailableError`` once every attempt
            failed instead of returning ``None``. Used by the parallel batch
            extractor to route rate-limited batches into its sequential path.
        model_override: When non-None, wrap ``llm_service`` with a one-off
            provider pinned to this model (stages pass
            ``settings.get_stage_model(stage_name)``).
        temperature: Sampling temperature forwarded on every attempt (also on
            the ``model_override`` and fallback paths); ``None`` sends none.
        system_prompt: System text sent WITHOUT a cache breakpoint — the
            alternative to ``cache_static`` (system text WITH one) when only a
            user-block breakpoint is wanted.

    Returns:
        Raw LLM response string, or None if all attempts failed.
    """
    if model_override:
        llm_service = _wrap_with_override(model_override)
    request = _Request(
        prompt=prompt,
        stage_name=stage_name,
        use_fast_model=use_fast_model,
        model_override=model_override,
        max_tokens=max_tokens,
        timeout=timeout,
        json_mode=json_mode,
        cache_static=cache_static,
        temperature=temperature,
        system_prompt=system_prompt,
    )
    attempts = _plan_attempts(llm_service, use_fast_model, max_retries)
    last_rate_limit: RateLimitError | ServiceUnavailableError | None = None
    for current, upcoming in zip(attempts, [*attempts[1:], None], strict=True):
        text, error = await _try_once(current, request)
        if text is not None:
            return text
        if isinstance(error, (RateLimitError, ServiceUnavailableError)):
            last_rate_limit = error
        if upcoming is not None:
            pause = _pause_seconds(current, upcoming, error)
            logger.info("[%s] Retrying in %.1fs on %s", stage_name, pause, upcoming.model)
            await asyncio.sleep(pause)
    logger.error("[%s] All %d attempts failed", stage_name, len(attempts))
    if propagate_rate_limit and last_rate_limit is not None:
        raise last_rate_limit
    return None
