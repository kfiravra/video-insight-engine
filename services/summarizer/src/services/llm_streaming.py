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

The chunks are rebuilt into one response twice, both with
``litellm.stream_chunk_builder``: OURS feeds the timing record and the Langfuse
generation; LiteLLM's own end-of-stream rebuild feeds its success callbacks
(the usage ledger) and the usage chunk it sends last — so every reader sees
the same shape as for a non-streamed call; ``include_usage`` makes the last
chunk carry the token counts, cache reads and writes included. Two fixes are
applied to the chunks LiteLLM keeps (``.chunks``) before its rebuild, and the
first to our own list before ours:

- order: the rebuild sorts the chunks by a ``time.time()`` stamp taken as each
  one is made. A wall clock that steps back (the WSL2 dev box rewinds ~2 s every
  30 s) stamps later chunks earlier, and the sort interleaves two stretches of
  the answer — scrambled text, unparseable JSON (:func:`_keep_arrival_order`);
- the 1-hour / 5-minute write split: Anthropic sends it only in
  ``message_start`` and LiteLLM keeps the LAST usage chunk's prompt details, so
  it is carried onto the later usages (:func:`_carry_cache_split`).
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


# The key ``stream_chunk_builder`` sorts a stream's chunks by (LiteLLM 1.91).
_CREATED_AT = "created_at"


def _keep_arrival_order(chunks: list[Any]) -> None:
    """Raise each chunk's ``created_at`` stamp to the highest stamp before it in ``chunks``.

    The stamps then never decrease along the list, so LiteLLM's (stable) sort
    keeps the order the chunks arrived in, whatever the wall clock did. A chunk
    without a stamp is left alone.
    """
    latest = float("-inf")
    for chunk in chunks:
        params = getattr(chunk, "_hidden_params", None)
        if not isinstance(params, dict):
            continue
        stamp = params.get(_CREATED_AT)
        if not isinstance(stamp, int | float):
            continue
        if stamp < latest:
            params[_CREATED_AT] = latest
        else:
            latest = stamp


def _cache_split(usage: object) -> object | None:
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cache_creation_token_details", None)


def _carry_cache_split(chunks: list[Any]) -> None:
    """Copy the write split of the first usage in ``chunks`` onto the later usages lacking one."""
    usages = [u for u in (getattr(c, "usage", None) for c in chunks) if u is not None]
    split = next((s for s in map(_cache_split, usages) if s is not None), None)
    if split is None:
        return
    for usage in usages:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None and _cache_split(usage) is None:
            details.cache_creation_token_details = split


def _fix_kept_chunks(stream: object) -> None:
    """Order and complete the chunks LiteLLM's ``CustomStreamWrapper`` keeps (``.chunks``).

    Called at the chunk carrying the finish reason: LiteLLM has kept every
    chunk by then and rebuilds them when it is asked for the next one — the
    response its success callbacks get (the usage ledger) and the final usage
    chunk it sends us. Langfuse logs OUR rebuild, not this one. Any other
    stream object is left alone.
    """
    kept = getattr(stream, "chunks", None)
    if not isinstance(kept, list):
        return
    _keep_arrival_order(kept)
    _carry_cache_split(kept)


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
                _fix_kept_chunks(stream)
            chunks.append(chunk)
    finally:
        await _close(stream)
    _keep_arrival_order(chunks)
    response = litellm.stream_chunk_builder(chunks, messages=request["messages"])
    if ttft_ms is None or not isinstance(response, litellm.ModelResponse):
        raise APIError(
            status_code=502,
            message="stream ended without a chunk",
            llm_provider=str(kwargs["model"]).split("/", 1)[0],
            model=str(kwargs["model"]),
        )
    return StreamedCompletion(response=response, ttft_ms=ttft_ms)
