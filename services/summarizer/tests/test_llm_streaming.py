"""Streamed warm-up calls (pipeline-1min 3.4): first-chunk signal, rebuilt usage, additive plumbing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import litellm
import pytest
from litellm import ModelResponse
from litellm.exceptions import APIError
from litellm.integrations.custom_logger import CustomLogger
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage

from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.llm_streaming import stream_completion, streaming_request
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.utils.llm_retry import call_llm_with_retry

_MODEL = "anthropic/claude-haiku-4-5-20251001"
_KWARGS: dict[str, Any] = {
    "model": _MODEL,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    "max_tokens": 16,
    "timeout": 120.0,
    "num_retries": 0,
}


def _chunk(delta: Delta, finish_reason: str | None = None) -> ModelResponseStream:
    choice = StreamingChoices(index=0, delta=delta, finish_reason=finish_reason)
    return ModelResponseStream(id="s", model="claude-haiku-4-5", choices=[choice])


def _usage_chunk() -> ModelResponseStream:
    last = _chunk(Delta())
    last.usage = Usage(  # pyright: ignore[reportAttributeAccessIssue]
        prompt_tokens=4500,
        completion_tokens=7,
        total_tokens=4507,
        cache_creation_input_tokens=4200,
        cache_read_input_tokens=300,
    )
    return last


def _chunks() -> list[ModelResponseStream]:
    return [
        _chunk(Delta(role="assistant", content="")),
        _chunk(Delta(content='{"tech": {"topics": []}}'), finish_reason="stop"),
        _usage_chunk(),
    ]


class _FakeStream:
    """An async chunk iterator that records the order of events and its close."""

    def __init__(self, chunks: list[ModelResponseStream], log: list[str]) -> None:
        self._chunks = chunks
        self._log = log
        self.closed = False

    def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[ModelResponseStream]:
        for chunk in self._chunks:
            self._log.append("chunk")
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _completion(stream: _FakeStream) -> AsyncMock:
    return AsyncMock(return_value=stream)


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


class TestStreamCompletion:
    async def test_should_call_on_first_chunk_once_before_the_rest_arrives(self) -> None:
        log: list[str] = []
        stream = _FakeStream(_chunks(), log)

        await stream_completion(_completion(stream), dict(_KWARGS), lambda: log.append("first"))

        assert log == ["chunk", "first", "chunk", "chunk"]

    async def test_should_rebuild_the_answer_from_the_chunks(self) -> None:
        stream = _FakeStream(_chunks(), [])

        streamed = await stream_completion(_completion(stream), dict(_KWARGS), lambda: None)

        assert streamed.response.choices[0].message.content == '{"tech": {"topics": []}}'

    async def test_should_keep_cache_reads_and_writes_in_the_rebuilt_usage(self) -> None:
        stream = _FakeStream(_chunks(), [])

        usage = (
            await stream_completion(_completion(stream), dict(_KWARGS), lambda: None)
        ).response.usage  # pyright: ignore[reportAttributeAccessIssue]

        assert (usage.cache_creation_input_tokens, usage.cache_read_input_tokens) == (4200, 300)

    async def test_should_ask_for_a_stream_with_usage(self) -> None:
        complete = _completion(_FakeStream(_chunks(), []))

        await stream_completion(complete, dict(_KWARGS), lambda: None)

        sent = complete.await_args.kwargs
        assert (sent["stream"], sent["stream_options"]) == (True, {"include_usage": True})

    async def test_should_close_the_stream_when_it_ends(self) -> None:
        stream = _FakeStream(_chunks(), [])

        await stream_completion(_completion(stream), dict(_KWARGS), lambda: None)

        assert stream.closed

    async def test_should_raise_a_transient_api_error_when_no_chunk_arrives(self) -> None:
        with pytest.raises(APIError, match="without a chunk"):
            await stream_completion(_completion(_FakeStream([], [])), dict(_KWARGS), lambda: None)

    def test_should_leave_the_callers_kwargs_unstreamed(self) -> None:
        kwargs = dict(_KWARGS)

        streaming_request(kwargs)

        assert "stream" not in kwargs


def _plain_response() -> ModelResponse:
    return ModelResponse(
        model="claude-haiku-4-5",
        choices=[
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
    )


class TestProviderRouting:
    async def test_should_send_no_stream_key_when_no_first_chunk_callback_is_given(self) -> None:
        acompletion = AsyncMock(return_value=_plain_response())
        with patch("src.services.llm_provider.acompletion", acompletion):
            await LLMProvider(model=_MODEL).complete("hi", max_tokens=16)

        assert "stream" not in acompletion.await_args.kwargs

    async def test_should_stream_when_a_first_chunk_callback_is_given(self) -> None:
        acompletion = AsyncMock(return_value=_FakeStream(_chunks(), []))
        with patch("src.services.llm_provider.acompletion", acompletion):
            text = await LLMProvider(model=_MODEL).complete("hi", on_first_chunk=lambda: None)

        assert text == '{"tech": {"topics": []}}'

    async def test_should_record_ttft_ms_for_the_streamed_call(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        acompletion = AsyncMock(return_value=_FakeStream(_chunks(), []))
        with patch("src.services.llm_provider.acompletion", acompletion):
            await LLMProvider(model=_MODEL).complete(
                "hi", span_name="extraction_text1", on_first_chunk=lambda: None
            )

        assert isinstance(recorder.llm_calls[0]["ttftMs"], int)

    async def test_should_record_the_streamed_cache_write_in_the_timing_row(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        acompletion = AsyncMock(return_value=_FakeStream(_chunks(), []))
        with patch("src.services.llm_provider.acompletion", acompletion):
            await LLMProvider(model=_MODEL).complete("hi", on_first_chunk=lambda: None)

        row = recorder.llm_calls[0]
        assert (row["cacheWriteTokens"], row["cacheReadTokens"]) == (4200, 300)


class TestCallerPlumbing:
    async def test_should_forward_the_callback_to_the_provider_when_set(self) -> None:
        provider = MagicMock(complete=AsyncMock(return_value="ok"))
        callback = MagicMock()

        await LLMService(provider).call_llm("hi", on_first_chunk=callback)

        assert provider.complete.await_args.kwargs["on_first_chunk"] is callback

    async def test_should_not_pass_a_callback_key_when_unset(self) -> None:
        provider = MagicMock(complete=AsyncMock(return_value="ok"))

        await LLMService(provider).call_llm("hi")

        assert "on_first_chunk" not in provider.complete.await_args.kwargs

    async def test_should_forward_the_callback_on_every_attempt(self) -> None:
        service = MagicMock(model=_MODEL, fallback_model=None)
        service.call_llm = AsyncMock(side_effect=[asyncio.TimeoutError(), "ok"])
        callback = MagicMock()

        with patch("src.utils.llm_retry.asyncio.sleep", AsyncMock()):
            await call_llm_with_retry(service, "hi", max_retries=1, on_first_chunk=callback)

        assert [c.kwargs["on_first_chunk"] for c in service.call_llm.await_args_list] == [
            callback,
            callback,
        ]

    async def test_should_refuse_a_streamed_fast_model_call(self) -> None:
        with pytest.raises(ValueError, match="primary-model"):
            await call_llm_with_retry(
                MagicMock(), "hi", use_fast_model=True, on_first_chunk=lambda: None
            )


# ─── LiteLLM's real Anthropic stream, offline (httpx MockTransport) ───


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


_WRITE_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 5000,
    "cache_read_input_tokens": 0,
    "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 1000},
    "output_tokens": 1,
}
# Anthropic sends the 1h/5m split in message_start only; message_delta has the totals.
_ANTHROPIC_EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5-20251001",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": _WRITE_USAGE,
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {
            "output_tokens": 2,
            "input_tokens": 10,
            "cache_creation_input_tokens": 5000,
            "cache_read_input_tokens": 0,
        },
    },
    {"type": "message_stop"},
]


class _Ledger(CustomLogger):
    def __init__(self) -> None:
        self.splits: list[object] = []

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # type: ignore[override]
        details = getattr(response_obj.usage, "prompt_tokens_details", None)
        self.splits.append(getattr(details, "cache_creation_token_details", None))


@pytest.fixture
def ledger(fresh_litellm_callbacks: None) -> _Ledger:
    callback = _Ledger()
    litellm.callbacks.append(callback)
    return callback


def _anthropic_kwargs() -> dict[str, Any]:
    handler = AsyncHTTPHandler()
    body = _sse(_ANTHROPIC_EVENTS)
    handler.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            )
        )
    )
    return {**_KWARGS, "client": handler, "api_key": "sk-test"}


def _split(usage: Any) -> tuple[int, int]:
    details = usage.prompt_tokens_details.cache_creation_token_details
    return details.ephemeral_1h_input_tokens, details.ephemeral_5m_input_tokens


class TestAnthropicCacheSplit:
    async def test_should_keep_the_1h_and_5m_write_split_when_streamed(
        self, ledger: _Ledger
    ) -> None:
        streamed = await stream_completion(litellm.acompletion, _anthropic_kwargs(), lambda: None)

        assert _split(streamed.response.usage) == (1000, 4000)  # pyright: ignore[reportAttributeAccessIssue]

    async def test_should_hand_the_ledger_the_split_once(self, ledger: _Ledger) -> None:
        await stream_completion(litellm.acompletion, _anthropic_kwargs(), lambda: None)
        await asyncio.sleep(0.2)  # LiteLLM logs from a task

        assert [
            (s.ephemeral_1h_input_tokens, s.ephemeral_5m_input_tokens) for s in ledger.splits
        ] == [  # type: ignore[attr-defined]
            (1000, 4000)
        ]

    async def test_should_signal_the_first_content_chunk_once(self, ledger: _Ledger) -> None:
        firsts: list[int] = []

        await stream_completion(litellm.acompletion, _anthropic_kwargs(), lambda: firsts.append(1))

        assert firsts == [1]


# ─── Cancellation + retries ───


class _HangingStream(_FakeStream):
    """One chunk, then silence until cancelled."""

    async def _iterate(self) -> AsyncIterator[ModelResponseStream]:
        yield self._chunks[0]
        await asyncio.Event().wait()


class TestStreamLifecycle:
    async def test_should_close_the_stream_when_cancelled_mid_stream(self) -> None:
        stream, firsts = _HangingStream(_chunks(), []), asyncio.Event()
        task = asyncio.create_task(
            stream_completion(_completion(stream), dict(_KWARGS), firsts.set)
        )
        await firsts.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed

    async def test_should_refire_the_first_chunk_and_record_ttft_on_every_streamed_attempt(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        empty = [_chunk(Delta(role="assistant", content=""), finish_reason="stop"), _usage_chunk()]
        acompletion = AsyncMock(side_effect=[_FakeStream(empty, []), _FakeStream(_chunks(), [])])
        firsts: list[int] = []
        service = LLMService(LLMProvider(model=_MODEL))
        with (
            patch("src.services.llm_provider.acompletion", acompletion),
            patch("src.utils.llm_retry.asyncio.sleep", AsyncMock()),
        ):
            await call_llm_with_retry(
                service, "hi", max_retries=1, on_first_chunk=lambda: firsts.append(1)
            )

        rows = [(r["attempt"], isinstance(r["ttftMs"], int)) for r in recorder.llm_calls]
        assert (len(firsts), rows) == (2, [(1, True), (2, True)])
