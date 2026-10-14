"""Streamed answers keep their order through LiteLLM's rebuild (pipeline-1min 3.4 fix).

LiteLLM 1.91–1.105.0 (re-verified 2026-10-13) sorts a stream's chunks by a
``time.time()`` stamp before it rebuilds them into one response. The WSL2 dev
box's wall clock steps back ~2 s every 30 s, and on 2026-10-13 that
interleaved two stretches of long streamed extraction answers
(``extraction_text1``) into unparseable JSON — in our rebuild (what Langfuse
logs) and in LiteLLM's (what its success callbacks, the usage ledger, get).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import litellm
import pytest
from litellm.integrations.custom_logger import CustomLogger
from litellm.litellm_core_utils import streaming_handler
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from src.services.llm_streaming import stream_completion

_MODEL = "anthropic/claude-haiku-4-5-20251001"
_ANSWER = json.dumps(
    {
        "items": [
            {
                "item": f"Item {i}",
                "tier": "ABC"[i % 3],
                "reason": f"reason {i}, needed for deck {i}",
            }
            for i in range(120)
        ]
    },
    indent=2,
)
_PIECES = [_ANSWER[i : i + 9] for i in range(0, len(_ANSWER), 9)]  # ~1,400 deltas
_PLAIN_ANSWER = "plain " * 400


# ─── LiteLLM's real Anthropic stream, offline (httpx MockTransport) ───


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _anthropic_events() -> list[dict[str, Any]]:
    usage = {"input_tokens": 10, "cache_read_input_tokens": 0, "output_tokens": 1}
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": _MODEL,
        "usage": usage,
    }
    deltas = [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}}
        for piece in _PIECES
    ]
    return [
        {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        *deltas,
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {**usage, "output_tokens": 3000},
        },
        {"type": "message_stop"},
    ]


async def _in_parts(body: bytes) -> AsyncIterator[bytes]:
    """The body in network-sized parts, yielding to the loop between them."""
    for start in range(0, len(body), 700):
        yield body[start : start + 700]
        await asyncio.sleep(0)


def _client(transport: httpx.MockTransport) -> dict[str, Any]:
    handler = AsyncHTTPHandler()
    handler.client = httpx.AsyncClient(transport=transport)
    return {
        "model": _MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 4096,
        "num_retries": 0,
        "client": handler,
        "api_key": "sk-test",
    }


def _streamed_kwargs() -> dict[str, Any]:
    body = _sse(_anthropic_events())
    headers = {"content-type": "text/event-stream"}
    return _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(200, content=_in_parts(body), headers=headers)
        )
    )


def _plain_kwargs() -> dict[str, Any]:
    body = {
        "id": "msg_2",
        "type": "message",
        "role": "assistant",
        "model": _MODEL,
        "content": [{"type": "text", "text": _PLAIN_ANSWER}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 400},
    }

    async def respond(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=body)

    return _client(httpx.MockTransport(respond))


class _SteppingClock:
    """``time.time()`` that advances 10 ms per call and steps back 1.2 s once, mid-stream."""

    def __init__(self, step_at_call: int) -> None:
        self._calls = 0
        self._step_at_call = step_at_call
        self._now = 1_800_000_000.0

    def time(self) -> float:
        self._calls += 1
        self._now += 0.01 - (1.2 if self._calls == self._step_at_call else 0.0)
        return self._now

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture
def stepping_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM's chunk stamps come from a wall clock that steps back half-way through."""
    monkeypatch.setattr(streaming_handler, "time", _SteppingClock(step_at_call=len(_PIECES) // 2))


class _LoggedAnswers(CustomLogger):
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # type: ignore[override]
        self.answers.append(response_obj.choices[0].message.content)


@pytest.fixture
def logged(fresh_litellm_callbacks: None) -> _LoggedAnswers:
    callback = _LoggedAnswers()
    litellm.callbacks.append(callback)
    return callback


def _content(response: object) -> str | None:
    assert isinstance(response, litellm.ModelResponse)
    return response.choices[0].message.content  # pyright: ignore[reportAttributeAccessIssue]


async def _streamed_answer() -> str | None:
    streamed = await stream_completion(litellm.acompletion, _streamed_kwargs(), lambda: None)
    return _content(streamed.response)


class TestLongStreamedAnswer:
    async def test_should_rebuild_a_long_answer_byte_identical_on_a_steady_clock(self) -> None:
        assert await _streamed_answer() == _ANSWER

    @pytest.mark.usefixtures("stepping_clock")
    async def test_should_rebuild_a_long_answer_byte_identical_when_the_wall_clock_steps_back(
        self,
    ) -> None:
        assert await _streamed_answer() == _ANSWER

    @pytest.mark.usefixtures("stepping_clock")
    async def test_should_log_the_answer_in_order_when_the_wall_clock_steps_back(
        self, logged: _LoggedAnswers
    ) -> None:
        await _streamed_answer()
        await asyncio.sleep(0.2)  # LiteLLM logs from a task

        assert logged.answers == [_ANSWER]

    @pytest.mark.usefixtures("stepping_clock")
    async def test_should_rebuild_a_long_answer_byte_identical_beside_unstreamed_calls(
        self,
    ) -> None:
        plain = [litellm.acompletion(**_plain_kwargs()) for _ in range(2)]

        streamed, *others = await asyncio.gather(_streamed_answer(), *plain)

        assert (streamed, [_content(o) for o in others]) == (
            _ANSWER,
            [_PLAIN_ANSWER, _PLAIN_ANSWER],
        )


# ─── Chunks stamped out of order, no transport ───

_REQUEST: dict[str, Any] = {
    "model": _MODEL,
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 16,
}


def _stamped(text: str, created_at: float, finish_reason: str | None = None) -> ModelResponseStream:
    choice = StreamingChoices(index=0, delta=Delta(content=text), finish_reason=finish_reason)
    chunk = ModelResponseStream(id="s", model="claude-haiku-4-5", choices=[choice])
    chunk._hidden_params = {"created_at": created_at}
    return chunk


class _Stream:
    def __init__(self, chunks: list[ModelResponseStream]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[ModelResponseStream]:
        for chunk in self._chunks:
            yield chunk


class TestArrivalOrder:
    async def test_should_rebuild_the_chunks_in_arrival_order_when_their_stamps_go_back(
        self,
    ) -> None:
        chunks = [_stamped("a", 3.0), _stamped("b", 1.0), _stamped("c", 2.0, finish_reason="stop")]
        complete = AsyncMock(return_value=_Stream(chunks))

        streamed = await stream_completion(complete, dict(_REQUEST), lambda: None)

        assert streamed.response.choices[0].message.content == "abc"  # pyright: ignore[reportAttributeAccessIssue]
