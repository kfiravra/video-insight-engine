"""The Langfuse generation of one named assistant LLM call.

Opened before the request (so its start/end are the call's), finished once with
the outcome: usage and cost on success, ``ERROR`` on a provider failure,
``WARNING`` when the client walks away from a stream. Best-effort throughout:
observability never breaks an LLM call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.services.observability import error_status, finish_generation, generation, usage_details


class CallGeneration:
    """The Langfuse generation of one named LLM call, finished once with its outcome."""

    def __init__(self, handle: Any | None) -> None:
        self.handle = handle
        self.done = False

    def succeed(
        self,
        *,
        content: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        finish_reason: str | None,
        extra_metadata: dict[str, Any] | None,
    ) -> None:
        self.done = True
        finish_generation(
            self.handle,
            output=content,
            usage=usage_details(input_tokens, output_tokens),
            cost_usd=cost_usd,
            metadata={
                "finishReason": finish_reason,
                "latencyMs": latency_ms,
                **(extra_metadata or {}),
            },
        )

    def fail(self, error: BaseException) -> None:
        if self.done:
            return
        self.done = True
        # A stream the client walked away from (generator closed, task
        # cancelled) is a warning, not a provider failure.
        if isinstance(error, (GeneratorExit, asyncio.CancelledError)):
            finish_generation(self.handle, level="WARNING", status_message="client disconnected")
        else:
            finish_generation(self.handle, level="ERROR", status_message=error_status(error))


@contextmanager
def call_generation(
    model: str,
    span_name: str | None,
    messages: list[dict],
    span_metadata: dict[str, Any] | None,
) -> Iterator[CallGeneration]:
    """Open the call's generation before the request (only for a named span).

    A raise (cancellation and a closed stream included) ends it as failed;
    leaving the block without ``succeed`` ends it without an outcome.
    """
    handle = (
        generation(span_name, model=model, input=messages, metadata=span_metadata)
        if span_name
        else None
    )
    call = CallGeneration(handle)
    try:
        yield call
    except BaseException as exc:
        call.fail(exc)
        raise
    finally:
        if not call.done:
            finish_generation(handle)
