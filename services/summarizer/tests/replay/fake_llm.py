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

Split extraction (3.4): a call under ``summarize:extraction:<group>`` is not
recorded; ``group_synth`` answers it from the recorded single extraction, and
a streamed request (``stream=True``, the warm-up call) gets an async iterator:
the role chunk after TTFT, the content chunk at the end, then the usage chunk.

Quiz v2 (output-quality 2.1b): the ``quiz_v2`` call is not recorded either;
``quiz_synth`` answers it from the recorded flag-off quiz, which counts as used.

Hero rewrite (output-quality 2.2): neither is the ``hero_rewrite`` call;
``hero_synth`` answers it from the recorded memory + synthesis, and the synthesis
recording counts as used.
"""

from __future__ import annotations

import asyncio
import functools
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from unittest.mock import patch

from litellm import ModelResponse
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from llm_common.context import llm_feature_var

from src.services.llm_provider import LLMProvider
from tests.replay.cassette import LLMEntry, LLMKey
from tests.replay.group_synth import (
    EXTRACTION_KEY,
    SynthCall,
    group_tagging_patch,
    group_var,
    is_group_feature,
    synthesize,
)
from tests.replay.hero_synth import SYNTHESIS_KEY, is_hero_call, synthesize_hero
from tests.replay.quiz_synth import MEMORY_KEY, QUIZ_KEY, is_quiz_v2_call, synthesize_quiz_v2

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


def _synth_entry(entry: LLMEntry, key: LLMKey, synth: SynthCall) -> LLMEntry:
    """A group answer in the recorded-entry shape (latency = the synthesized wall)."""
    return LLMEntry(
        key=key,
        model=entry.model,
        latency_ms=int(synth.wall_s * 1000),
        output=synth.output,
        finish_reason="stop",
        input_tokens=synth.input_tokens,
        output_tokens=synth.output_tokens,
        cache_read_tokens=synth.cache_read_tokens,
        cache_write_tokens=synth.cache_write_tokens,
    )


def _stream_chunk(entry: LLMEntry, delta: Delta, finish_reason: str | None = None) -> Any:
    choice = StreamingChoices(index=0, delta=delta, finish_reason=finish_reason)
    return ModelResponseStream(id="replay", model=_model_tail(entry.model), choices=[choice])


async def _stream(entry: LLMEntry, ttft_s: float, speed: float) -> AsyncIterator[Any]:
    """Role chunk after TTFT, the whole answer at the end, then the usage chunk."""
    if speed > 0:
        await asyncio.sleep(ttft_s * speed)
    yield _stream_chunk(entry, Delta(role="assistant", content=""))
    if speed > 0:
        await asyncio.sleep(max(entry.latency_ms / 1000 - ttft_s, 0.0) * speed)
    yield _stream_chunk(entry, Delta(content=entry.output), finish_reason="stop")
    last = _stream_chunk(entry, Delta())
    last.usage = _build_response(entry).usage  # pyright: ignore[reportAttributeAccessIssue]
    yield last


class ReplayLLM:
    """Serves recorded completions, sleeping ``latency × speed`` per call."""

    def __init__(self, entries: list[LLMEntry], *, speed: float, video_id: str) -> None:
        self._entries = {entry.key: entry for entry in entries}
        self._speed = speed
        self._video_id = video_id
        self._ordinals: dict[tuple[str, str | None], int] = defaultdict(int)
        self.used: list[LLMKey] = []
        # Split-extraction group calls answered by ``group_synth`` (never recorded).
        self.group_calls: list[LLMKey] = []
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

    def _group_entry(self, key: LLMKey, kwargs: dict[str, Any]) -> tuple[LLMEntry, float] | None:
        """A split-extraction group call answered from the recorded single call."""
        recorded = self._entries.get(EXTRACTION_KEY)
        spec = group_var.get()
        if recorded is None or spec is None or not is_group_feature(key.feature):
            return None
        synth = synthesize(recorded, spec, kwargs.get("messages") or [])
        self.group_calls.append(key)
        return _synth_entry(recorded, key, synth), synth.ttft_s

    def _quiz_v2_entry(self, key: LLMKey, kwargs: dict[str, Any]) -> LLMEntry | None:
        """A quiz v2 call answered from the recorded flag-off quiz (now used)."""
        recorded = self._entries.get(QUIZ_KEY)
        if recorded is None or not is_quiz_v2_call(key):
            return None
        self.used.append(QUIZ_KEY)
        memory = self._entries.get(MEMORY_KEY)
        return synthesize_quiz_v2(recorded, memory, key, kwargs.get("messages") or [])

    def _hero_entry(self, key: LLMKey, kwargs: dict[str, Any]) -> LLMEntry | None:
        """A hero rewrite call answered from the recorded memory + synthesis (now used)."""
        if not is_hero_call(key):
            return None
        if SYNTHESIS_KEY in self._entries:
            self.used.append(SYNTHESIS_KEY)
        messages = kwargs.get("messages") or []
        return synthesize_hero(self._entries, MEMORY_KEY, EXTRACTION_KEY, key, messages)

    def _answer(self, key: LLMKey, kwargs: dict[str, Any]) -> tuple[LLMEntry, float]:
        entry = self._entries.get(key)
        if entry is not None:
            self.used.append(key)
            return entry, 0.0
        group = self._group_entry(key, kwargs)
        if group is not None:
            return group
        synthesized = self._quiz_v2_entry(key, kwargs) or self._hero_entry(key, kwargs)
        if synthesized is not None:
            return synthesized, 0.0
        message = self._miss_message(key)
        self.misses.append(message)
        raise CassetteMissError(message)

    async def acompletion(self, **kwargs: Any) -> Any:
        key = self._next_key()
        entry, ttft_s = self._answer(key, kwargs)
        requested = str(kwargs.get("model", ""))
        if _model_tail(requested) != _model_tail(entry.model):
            self.model_mismatches.append(
                f"{key.label()}: requested {requested}, recorded {entry.model}"
            )
        if kwargs.get("stream"):
            return _stream(entry, ttft_s, self._speed)
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
    with (
        span_tagging_patches(),
        group_tagging_patch(),
        patch("src.services.llm_provider.acompletion", fake.acompletion),
    ):
        yield fake
