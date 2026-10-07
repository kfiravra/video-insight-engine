"""LLM call telemetry — Langfuse generation logging extracted from the provider.

Kept as standalone helpers so :mod:`src.services.llm_provider` stays focused
on completion plumbing. Every helper here is best-effort: observability
outages must never bubble out of an LLM call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from litellm.cost_calculator import completion_cost

from src.services.observability import log_generation

logger = logging.getLogger(__name__)


def stopwatch_ms_since(start_monotonic: float) -> int:
    """Convert a ``time.monotonic()`` start into elapsed milliseconds."""
    return int((time.monotonic() - start_monotonic) * 1000)


def record_generation(
    *,
    span_name: str,
    model: str,
    msg_dicts: list[dict[str, Any]],
    content: str,
    response: Any,
    latency_ms: int,
    finish_reason: str | None,
    extra_metadata: dict[str, Any] | None,
    model_parameters: dict[str, Any] | None = None,
) -> None:
    """Best-effort Langfuse generation log.

    ``model_parameters`` carries the sampling parameters the request actually
    sent (e.g. ``temperature``) into the generation's ``modelParameters``.
    Wrapped in a broad try/except — observability outages must never
    bubble out of an LLM call.
    """
    try:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        # Anthropic prompt-cache token breakdown. Surfacing these in Langfuse
        # metadata lets us answer "is the static extraction prompt actually
        # being cached?" from the UI without cross-referencing MongoDB.
        cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        cost = 0.0
        try:
            cost = completion_cost(completion_response=response) or 0.0
        except Exception as exc:  # noqa: BLE001 — cost lookup is non-critical
            logger.debug("Cost lookup failed (span=%s): %s", span_name, exc)
        cache_metadata: dict[str, Any] = {}
        if isinstance(cache_creation_tokens, int) and cache_creation_tokens > 0:
            cache_metadata["cacheCreationTokens"] = cache_creation_tokens
        if isinstance(cache_read_tokens, int) and cache_read_tokens > 0:
            cache_metadata["cacheReadTokens"] = cache_read_tokens
            cache_metadata["cacheHit"] = True
        log_generation(
            name=span_name,
            model=model,
            input_payload=msg_dicts,
            output_payload=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            model_parameters=model_parameters,
            metadata={
                "finishReason": finish_reason,
                **cache_metadata,
                **(extra_metadata or {}),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Generation record skipped (span=%s): %s", span_name, exc)
