"""Fake ``acompletion`` that replays recorded completions.

Patched over ``src.services.llm_provider.acompletion`` — since task 0.2 the
only LiteLLM seam in summarizer src — so every stage (including per-stage
model overrides, the vision provider and description analysis) goes through
it while the real provider code (timing record, telemetry, parsing) still runs.

Keying is ``llm_feature_var`` + span + ordinal (see :class:`LLMKey`). The
span is not an ``acompletion`` argument, so :func:`span_tagging_patches`
wraps the two span-bearing provider methods to publish it on a harness-local
ContextVar for the duration of the call. Prompt-hash keying was rejected: any
prompt or settings drift (registry versions, visual annotations) would miss.
"""

from __future__ import annotations

import asyncio
import functools
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from unittest.mock import patch

from litellm import ModelResponse
from litellm.types.utils import Usage
from llm_common.context import llm_feature_var

from src.services.llm_provider import LLMProvider
from tests.replay.cassette import LLMEntry, LLMKey

_span_var: ContextVar[str | None] = ContextVar("replay_llm_span", default=None)

_SPAN_METHODS = ("complete_with_messages", "complete_fast")


class CassetteMissError(LookupError):
    """The pipeline made an LLM call the cassette has no recording for."""


def _model_tail(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def _build_response(entry: LLMEntry) -> ModelResponse:
    """LiteLLM-shaped response: providers echo the bare model name."""
    usage = Usage(
        prompt_tokens=entry.input_tokens,
        completion_tokens=entry.output_tokens,
        total_tokens=entry.input_tokens + entry.output_tokens,
        cache_creation_input_tokens=entry.cache_write_tokens,
        cache_read_input_tokens=entry.cache_read_tokens,
    )
    return ModelResponse(
        model=_model_tail(entry.model),
        choices=[
            {
                "index": 0,
                "finish_reason": entry.finish_reason,
                "message": {"role": "assistant", "content": entry.output},
            }
        ],
        usage=usage,
    )


class ReplayLLM:
    """Serves recorded completions, sleeping ``latency × speed`` per call."""

    def __init__(self, entries: list[LLMEntry], *, speed: float, video_id: str) -> None:
        self._entries = {entry.key: entry for entry in entries}
        self._speed = speed
        self._video_id = video_id
        self._ordinals: dict[tuple[str, str | None], int] = defaultdict(int)
        self.used: list[LLMKey] = []
        self.misses: list[str] = []
        self.model_mismatches: list[str] = []

    def _next_key(self) -> LLMKey:
        feature = llm_feature_var.get()
        span = _span_var.get()
        ordinal = self._ordinals[(feature, span)]
        self._ordinals[(feature, span)] = ordinal + 1
        return LLMKey(feature=feature, span=span, ordinal=ordinal)

    def _miss_message(self, key: LLMKey) -> str:
        siblings = sorted(
            k.ordinal for k in self._entries if (k.feature, k.span) == (key.feature, key.span)
        )
        recorded = ", ".join(sorted(k.label() for k in self._entries))
        return (
            f"Cassette {self._video_id} has no LLM recording for {key.label()} "
            f"(feature={key.feature!r} span={key.span!r} ordinal={key.ordinal}); "
            f"recorded ordinals for this feature/span: {siblings or 'none'}; "
            f"all recorded keys: {recorded}"
        )

    def unused_keys(self) -> list[str]:
        used = set(self.used)
        return sorted(k.label() for k in self._entries if k not in used)

    async def acompletion(self, **kwargs: Any) -> ModelResponse:
        key = self._next_key()
        entry = self._entries.get(key)
        if entry is None:
            message = self._miss_message(key)
            self.misses.append(message)
            raise CassetteMissError(message)
        self.used.append(key)
        requested = str(kwargs.get("model", ""))
        if _model_tail(requested) != _model_tail(entry.model):
            self.model_mismatches.append(
                f"{key.label()}: requested {requested}, recorded {entry.model}"
            )
        if self._speed > 0:
            await asyncio.sleep(entry.latency_ms / 1000 * self._speed)
        return _build_response(entry)


def _tag_span(
    original: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    @functools.wraps(original)
    async def wrapper(self: LLMProvider, *args: Any, **kwargs: Any) -> str:
        token = _span_var.set(kwargs.get("span_name"))
        try:
            return await original(self, *args, **kwargs)
        finally:
            _span_var.reset(token)

    return wrapper


@contextmanager
def span_tagging_patches() -> Iterator[None]:
    """Publish ``span_name`` on the replay ContextVar around provider calls."""
    with (
        patch.object(LLMProvider, _SPAN_METHODS[0], _tag_span(LLMProvider.complete_with_messages)),
        patch.object(LLMProvider, _SPAN_METHODS[1], _tag_span(LLMProvider.complete_fast)),
    ):
        yield


@contextmanager
def replay_llm(entries: list[LLMEntry], *, speed: float, video_id: str) -> Iterator[ReplayLLM]:
    """Install the fake at the provider's ``acompletion`` seam."""
    fake = ReplayLLM(entries, speed=speed, video_id=video_id)
    with span_tagging_patches(), patch("src.services.llm_provider.acompletion", fake.acompletion):
        yield fake
