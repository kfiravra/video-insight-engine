"""LLM call telemetry: the Langfuse generation of one provider call.

Kept as standalone helpers so :mod:`src.services.llm_provider` stays focused
on completion plumbing. The generation opens just before the request goes out
(:func:`open_generation`), so its start and end are the call's own, and is
finished with the answer (:func:`record_generation`) or the error
(:func:`record_generation_failure`). Every helper is best-effort:
observability outages must never bubble out of an LLM call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from litellm.cost_calculator import completion_cost

from src.services.observability import error_status, finish_generation, generation, usage_details

logger = logging.getLogger(__name__)


def stopwatch_ms_since(start_monotonic: float) -> int:
    """Convert a ``time.monotonic()`` start into elapsed milliseconds."""
    return int((time.monotonic() - start_monotonic) * 1000)


def _usage_int(usage: object, attr: str) -> int:
    """``usage.attr`` when it is a real int (MagicMock attributes are not)."""
    value = getattr(usage, attr, 0) if usage is not None else 0
    return value if isinstance(value, int) else 0


def open_generation(
    *,
    span_name: str,
    model: str,
    msg_dicts: list[dict[str, Any]],
    model_parameters: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    """Start the call's generation under the current span; ``None`` outside a run trace.

    ``model_parameters`` carries the sampling parameters the request actually
    sends (e.g. ``temperature``) into the generation's ``modelParameters``.
    """
    try:
        return generation(
            span_name,
            model=model,
            input=msg_dicts,
            model_parameters=model_parameters,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Generation open skipped (span=%s): %s", span_name, exc)
        return None


def record_generation(
    *,
    handle: Any | None,
    span_name: str,
    response: Any,
    content: str,
    latency_ms: int,
    finish_reason: str | None,
    extra_metadata: dict[str, Any] | None,
) -> None:
    """Finish a call's generation with its output, token usage, cost and metadata.

    Anthropic's prompt-cache reads and writes go into ``usage_details`` and,
    as ``cacheReadTokens`` / ``cacheCreationTokens`` / ``cacheHit``, into the
    metadata, so "is the extraction prompt cached?" is answerable from the UI.
    """
    if handle is None:
        return
    try:
        usage = getattr(response, "usage", None)
        input_tokens = _usage_int(usage, "prompt_tokens")
        output_tokens = _usage_int(usage, "completion_tokens")
        cache_creation_tokens = _usage_int(usage, "cache_creation_input_tokens")
        cache_read_tokens = _usage_int(usage, "cache_read_input_tokens")
        cost = 0.0
        try:
            cost = completion_cost(completion_response=response) or 0.0
        except Exception as exc:  # noqa: BLE001 — cost lookup is non-critical
            logger.debug("Cost lookup failed (span=%s): %s", span_name, exc)
        cache_metadata: dict[str, Any] = {}
        if cache_creation_tokens > 0:
            cache_metadata["cacheCreationTokens"] = cache_creation_tokens
        if cache_read_tokens > 0:
            cache_metadata["cacheReadTokens"] = cache_read_tokens
            cache_metadata["cacheHit"] = True
        finish_generation(
            handle,
            output=content,
            usage=usage_details(
                input_tokens,
                output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
            ),
            cost_usd=cost,
            metadata={
                "finishReason": finish_reason,
                "latencyMs": latency_ms,
                **cache_metadata,
                **(extra_metadata or {}),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Generation record skipped (span=%s): %s", span_name, exc)


def record_generation_failure(handle: Any | None, error: BaseException) -> None:
    """End a call's generation that raised: ``ERROR``, or ``WARNING`` when it was cancelled."""
    if handle is None:
        return
    cancelled = isinstance(error, asyncio.CancelledError)
    finish_generation(
        handle,
        level="WARNING" if cancelled else "ERROR",
        status_message="cancelled" if cancelled else error_status(error),
    )
