"""Streamed completions — the warm-up call whose first chunk releases the rest (pipeline-1min 3.4).

Anthropic makes a prompt-cache entry readable only once the response that
writes it has begun. The split extraction streams ONE call per transcript
chunk (its text1 group): its first chunk tells the chunk's other group calls
they can read the shared prefix instead of writing it again. With LiteLLM
1.91 the consumer never sees ``message_start`` (an empty chunk once its usage
is stripped), so that first chunk — and ``ttftMs`` — is the first content
token. ``LLMProvider`` routes a call here only when its caller passes
``on_first_chunk``; every other request is sent exactly as before, with no
``stream`` key.

The chunks are rebuilt into one response (``litellm.stream_chunk_builder``)
so the timing record, the Langfuse generation and the usage ledger read the
same shape as for a non-streamed call; ``include_usage`` makes the last chunk
carry the token counts, cache reads and writes included. Anthropic sends the
1-hour / 5-minute write split only in ``message_start`` and LiteLLM keeps the
LAST usage chunk's prompt details, so the split is carried forward onto the
later usages LiteLLM holds before it rebuilds the stream (:func:`_carry_cache_split`).
The total timeout is the caller's: ``LLMService.call_llm`` bounds the whole
call with ``asyncio.timeout`` exactly as it bounds a non-streamed one.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import litellm
from litellm.exceptions import APIError

# The provider's ``acompletion`` seam, passed in so the replay harness's patch
# on ``llm_provider.acompletion`` also answers streamed calls.
Completion = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class StreamedCompletion:
    """The rebuilt response and the milliseconds until its first chunk arrived."""

    response: litellm.ModelResponse
    ttft_ms: int


def streaming_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """``kwargs`` as a streamed request whose last chunk carries the usage."""
    return {**kwargs, "stream": True, "stream_options": {"include_usage": True}}


def _cache_split(usage: object) -> object | None:
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cache_creation_token_details", None)


def _carry_cache_split(stream: object) -> None:
    """Copy the write split of the stream's first usage onto its later usages lacking one.

    Reads the usages LiteLLM's ``CustomStreamWrapper`` keeps (``.chunks``);
    called at the chunk carrying the finish reason, before LiteLLM sends the
    usage chunk and builds the response its success callbacks (the ledger)
    receive. Any other stream object is left alone.
    """
    chunks = getattr(stream, "chunks", None)
    if not isinstance(chunks, list):
        return
    usages = [u for u in (getattr(c, "usage", None) for c in chunks) if u is not None]
    split = next((s for s in map(_cache_split, usages) if s is not None), None)
    if split is None:
        return
    for usage in usages:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None and _cache_split(usage) is None:
            details.cache_creation_token_details = split


def _finishes(chunk: Any) -> bool:
    choices = getattr(chunk, "choices", None) or []
    return bool(choices) and getattr(choices[0], "finish_reason", None) is not None


async def _close(stream: object) -> None:
    """Release the HTTP stream even when the read stopped early (timeout, cancel)."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


async def stream_completion(
    complete: Completion,
    kwargs: dict[str, Any],
    on_first_chunk: Callable[[], None],
) -> StreamedCompletion:
    """Send ``kwargs`` streamed; call ``on_first_chunk`` once, at the first chunk.

    Raises ``APIError`` (transient for ``call_llm_with_retry``) when the stream
    ends without a chunk — the same retry a non-streamed empty failure gets.
    """
    request = streaming_request(kwargs)
    started = time.monotonic()
    stream = await complete(**request)
    chunks: list[Any] = []
    ttft_ms: int | None = None
    try:
        async for chunk in stream:
            if ttft_ms is None:
                ttft_ms = int((time.monotonic() - started) * 1000)
                on_first_chunk()
            if _finishes(chunk):
                _carry_cache_split(stream)
            chunks.append(chunk)
    finally:
        await _close(stream)
    response = litellm.stream_chunk_builder(chunks, messages=request["messages"])
    if ttft_ms is None or not isinstance(response, litellm.ModelResponse):
        raise APIError(
            status_code=502,
            message="stream ended without a chunk",
            llm_provider=str(kwargs["model"]).split("/", 1)[0],
            model=str(kwargs["model"]),
        )
    return StreamedCompletion(response=response, ttft_ms=ttft_ms)
