"""Tests for the Langfuse instrumentation wired into LLMProvider.

Runs the real Langfuse v4 SDK against memory (``langfuse_capture``). Covers the
symmetry between ``complete_with_messages``, ``complete_with_tools`` and
``stream_with_messages``: each named call is one generation under the turn's
trace, finished with usage and metadata, ended ``ERROR`` when the provider
fails and ``WARNING`` when the client walks away from a stream.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_common.observability.testing import LangfuseCapture

from src.exceptions import LLMError
from src.services import llm_provider as lp
from src.services.llm_provider import LLMProvider, _safe_completion_cost
from src.services.observability import chat_trace


def _make_stream_response(chunks):
    """Return an async iterator that yields the given chunk objects."""

    class _Stream:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    return _Stream(chunks)


def _delta_chunk(content: str, finish_reason: str | None = None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _usage_chunk(prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class TestSafeCompletionCost:
    """_safe_completion_cost never raises, even when litellm does."""

    def test_should_return_zero_when_no_inputs(self):
        assert _safe_completion_cost() == 0.0

    def test_should_swallow_litellm_exceptions(self, monkeypatch):
        def _boom(**_kwargs):
            raise RuntimeError("unknown model")

        monkeypatch.setattr(lp, "completion_cost", _boom)
        result = _safe_completion_cost(
            model="anthropic/unknown",
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert result == 0.0

    def test_should_return_numeric_cost_when_litellm_returns_one(self, monkeypatch):
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.0042)
        result = _safe_completion_cost(
            model="anthropic/x",
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert result == pytest.approx(0.0042)

    def test_should_return_zero_when_litellm_returns_none(self, monkeypatch):
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: None)
        result = _safe_completion_cost(
            model="anthropic/x",
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert result == 0.0


def _rate_limited():
    from litellm.exceptions import RateLimitError

    return RateLimitError(
        message="rate limited", model="anthropic/claude-sonnet-4-6", llm_provider="anthropic"
    )


def _completion(content: str = "answer", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=4),
    )


class TestStreamWithMessagesObservability:
    """stream_with_messages records one generation, finished when the stream ends."""

    async def test_should_record_generation_when_span_name_set(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        captured_kwargs: dict = {}

        async def _fake_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            return _make_stream_response(
                [
                    _delta_chunk("Hello", finish_reason=None),
                    _delta_chunk(" world", finish_reason="stop"),
                    _usage_chunk(prompt_tokens=12, completion_tokens=3),
                ]
            )

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.0)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        tokens: list[str] = []
        async with chat_trace(video_id="yt1"):
            async for tok in provider.stream_with_messages(
                messages=[{"role": "user", "content": "hi"}],
                span_name="rag_generation",
                span_metadata={"sourcesCount": 2},
            ):
                tokens.append(tok)

        assert tokens == ["Hello", " world"]
        # OpenAI/compatible providers need this flag for usage in the final chunk
        assert captured_kwargs.get("stream_options") == {"include_usage": True}
        (gen,) = langfuse_capture.generations("rag_generation")
        assert gen.parent == "chat:yt1"
        assert gen.usage == {"input": 12, "output": 3, "total": 15}
        assert gen.attr("output") == "Hello world"
        assert gen.metadata["streamed"] is True
        assert gen.metadata["sourcesCount"] == 2
        assert gen.metadata["finishReason"] == "stop"

    async def test_should_skip_logging_when_no_span_name(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        async def _fake_acompletion(**_kwargs):
            return _make_stream_response([_delta_chunk("ok", finish_reason="stop")])

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            async for _ in provider.stream_with_messages(
                messages=[{"role": "user", "content": "hi"}],
            ):
                pass

        assert langfuse_capture.generations() == []

    async def test_should_end_the_generation_as_error_when_the_stream_raises(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        async def _fake_acompletion(**_kwargs):
            raise _rate_limited()

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            with pytest.raises(LLMError):
                async for _ in provider.stream_with_messages(
                    messages=[{"role": "user", "content": "hi"}],
                    span_name="rag_generation",
                ):
                    pass

        (gen,) = langfuse_capture.generations("rag_generation")
        assert gen.level == "ERROR"
        assert gen.attr("status_message").startswith("LLMError: ")

    async def test_should_end_the_generation_as_a_warning_when_the_client_disconnects(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        async def _fake_acompletion(**_kwargs):
            return _make_stream_response([_delta_chunk("a"), _delta_chunk("b")])

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            stream = provider.stream_with_messages(
                messages=[{"role": "user", "content": "hi"}], span_name="rag_generation"
            )
            assert await stream.__anext__() == "a"
            await stream.aclose()

        (gen,) = langfuse_capture.generations("rag_generation")
        assert (gen.level, gen.attr("status_message")) == ("WARNING", "client disconnected")


class TestCompletionObservability:
    """The non-streamed paths record their generation the same way."""

    async def test_complete_with_messages_records_usage_and_cost(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        async def _fake_acompletion(**_kwargs):
            return _completion("answer")

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.0021)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            out = await provider.complete_with_messages(
                [{"role": "user", "content": "hi"}], span_name="query_translate"
            )

        assert out == "answer"
        (gen,) = langfuse_capture.generations("query_translate")
        assert (gen.model, gen.usage, gen.cost) == (
            "anthropic/claude-sonnet-4-6",
            {"input": 20, "output": 4, "total": 24},
            {"total": 0.0021},
        )

    async def test_complete_with_tools_records_the_tool_call_count(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        call = SimpleNamespace(
            id="c1", function=SimpleNamespace(name="quiz_generator", arguments="{}")
        )

        async def _fake_acompletion(**_kwargs):
            return _completion(None, tool_calls=[call])

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.0)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            result = await provider.complete_with_tools(
                [{"role": "user", "content": "quiz me"}], tools=[], span_name="library_agent"
            )

        assert [c["name"] for c in result.tool_calls] == ["quiz_generator"]
        (gen,) = langfuse_capture.generations("library_agent")
        assert gen.metadata["toolCallCount"] == 1

    async def test_failed_completion_ends_its_generation_as_error(
        self, monkeypatch, langfuse_capture: LangfuseCapture
    ):
        async def _fake_acompletion(**_kwargs):
            raise _rate_limited()

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        async with chat_trace(video_id="yt1"):
            with pytest.raises(LLMError):
                await provider.complete_with_messages(
                    [{"role": "user", "content": "hi"}], span_name="query_translate"
                )

        (gen,) = langfuse_capture.generations("query_translate")
        assert gen.level == "ERROR"
        assert gen.attr("status_message").startswith("RateLimitError: ")

    async def test_records_nothing_outside_a_turn(self, monkeypatch, langfuse_capture):
        async def _fake_acompletion(**_kwargs):
            return _completion("answer")

        monkeypatch.setattr(lp, "acompletion", _fake_acompletion)
        monkeypatch.setattr(lp, "completion_cost", lambda **_kw: 0.0)

        provider = LLMProvider(model="anthropic/claude-sonnet-4-6")
        await provider.complete_with_messages(
            [{"role": "user", "content": "hi"}], span_name="query_translate"
        )

        assert langfuse_capture.observations() == []
