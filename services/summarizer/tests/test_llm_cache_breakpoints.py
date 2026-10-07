"""User-content cache breakpoint through the provider (D2 / C2, provider half of 1c.3).

What reaches ``acompletion``: on Anthropic a caller-placed user-block
breakpoint survives (with or without the system breakpoint); for OpenAI —
primary, fast model or a fast-routed call — every ``cache_control`` is gone.
Also pins what LiteLLM itself puts on the wire for a user-block breakpoint
(Anthropic keeps it, OpenAI drops it — a second guard behind
``prepare_for_model``), and that cache read/write tokens keep landing in
``pipeline.timing`` and Langfuse.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig

from src.services import llm_provider as lp
from src.services.llm_messages import TextBlock, text_block
from src.services.observability import langfuse_client as lc
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.utils import llm_retry
from src.utils.llm_retry import call_llm_with_retry

_SONNET = "anthropic/claude-sonnet-4-6"
_HAIKU = "anthropic/claude-haiku-4-5-20251001"
_MINI = "openai/gpt-4o-mini"
_EPHEMERAL = {"type": "ephemeral"}


def _blocks() -> list[TextBlock]:
    return [text_block("TRANSCRIPT + MEMORY", cache=True), text_block("YOUR JOB")]


def _response(*, cache_read: int = 0, cache_write: int = 0) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="stop", message=MagicMock(content="ok"))]
    response.usage = SimpleNamespace(
        prompt_tokens=900,
        completion_tokens=40,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    response.model = "claude-haiku-4-5-20251001"
    return response


def _cached_caller_messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": [text_block("RULES", cache=True)]},
        {"role": "user", "content": _blocks()},
    ]


def _sent_messages(acompletion: AsyncMock) -> list[dict[str, Any]]:
    return acompletion.call_args.kwargs["messages"]


@pytest.fixture
def acompletion() -> Iterator[AsyncMock]:
    fake = AsyncMock(return_value=_response())
    with patch("src.services.llm_provider.acompletion", fake):
        yield fake


def _provider(model: str, fast_model: str | None = None) -> lp.LLMProvider:
    return lp.LLMProvider(model=model, fast_model=fast_model or model, fallback_models=[_MINI])


class TestAnthropicUserBreakpoint:
    async def test_should_send_user_blocks_with_breakpoint_unchanged(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_HAIKU).complete(_blocks())

        assert _sent_messages(acompletion) == [{"role": "user", "content": _blocks()}]

    async def test_should_keep_system_breakpoint_when_caller_caches_static(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_HAIKU).complete(_blocks(), cache_static="RULES")

        system = _sent_messages(acompletion)[0]
        assert system["content"][0]["cache_control"] == _EPHEMERAL

    async def test_should_send_plain_system_when_caller_drops_its_breakpoint(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_HAIKU).complete(_blocks(), system_prompt="RULES")

        assert _sent_messages(acompletion)[0] == {"role": "system", "content": "RULES"}

    async def test_should_send_caller_messages_unchanged_via_complete_with_messages(
        self, acompletion: AsyncMock
    ) -> None:
        messages = [{"role": "user", "content": _blocks()}]

        await _provider(_SONNET).complete_with_messages(messages)

        assert _sent_messages(acompletion) == [{"role": "user", "content": _blocks()}]


class TestNonAnthropicGetsNoBreakpoint:
    async def test_should_strip_breakpoints_from_complete(self, acompletion: AsyncMock) -> None:
        await _provider(_MINI).complete(_blocks(), cache_static="RULES")

        assert "cache_control" not in json.dumps(_sent_messages(acompletion))

    async def test_should_keep_static_and_block_text_in_order(self, acompletion: AsyncMock) -> None:
        await _provider(_MINI).complete(_blocks(), cache_static="RULES")

        texts = [block["text"] for block in _sent_messages(acompletion)[0]["content"]]
        assert texts == ["RULES", "TRANSCRIPT + MEMORY", "YOUR JOB"]

    async def test_should_strip_system_and_user_breakpoints_from_caller_messages(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_MINI).complete_with_messages(_cached_caller_messages())

        assert "cache_control" not in json.dumps(_sent_messages(acompletion))

    async def test_should_leave_caller_messages_unmutated_when_stripping(
        self, acompletion: AsyncMock
    ) -> None:
        messages = _cached_caller_messages()
        before = copy.deepcopy(messages)

        await _provider(_MINI).complete_with_messages(messages)

        assert messages == before

    async def test_should_strip_when_fast_routed_call_targets_openai(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_SONNET, fast_model=_MINI).complete_with_messages(
            [{"role": "user", "content": _blocks()}], use_fast_model=True
        )

        assert "cache_control" not in json.dumps(_sent_messages(acompletion))

    async def test_should_strip_from_complete_fast(self, acompletion: AsyncMock) -> None:
        await _provider(_SONNET, fast_model=_MINI).complete_fast(_blocks())

        assert "cache_control" not in json.dumps(_sent_messages(acompletion))


class TestRetryWrapperCarriesBlocks:
    @pytest.fixture(autouse=True)
    def _clean_override_cache(self) -> Iterator[None]:
        llm_retry._wrap_with_override.cache_clear()
        yield
        llm_retry._wrap_with_override.cache_clear()

    async def test_should_deliver_block_prompt_to_litellm_through_model_override(
        self, acompletion: AsyncMock
    ) -> None:
        await call_llm_with_retry(
            MagicMock(), _blocks(), stage_name="extraction", max_retries=0, model_override=_HAIKU
        )

        assert _sent_messages(acompletion)[-1] == {"role": "user", "content": _blocks()}

    @pytest.mark.parametrize("use_fast_model", [False, True])
    async def test_should_send_unbroken_system_with_user_breakpoint_when_system_prompt_given(
        self, acompletion: AsyncMock, use_fast_model: bool
    ) -> None:
        await call_llm_with_retry(
            MagicMock(),
            _blocks(),
            stage_name="extraction",
            max_retries=0,
            model_override=_HAIKU,
            use_fast_model=use_fast_model,
            system_prompt="RULES",
        )

        assert _sent_messages(acompletion) == [
            {"role": "system", "content": "RULES"},
            {"role": "user", "content": _blocks()},
        ]

    async def test_fast_path_should_send_single_user_message_without_system_prompt(
        self, acompletion: AsyncMock
    ) -> None:
        await _provider(_SONNET, fast_model=_HAIKU).complete_fast("classify this")

        assert _sent_messages(acompletion) == [{"role": "user", "content": "classify this"}]


class TestLiteLLMTransforms:
    """Offline request transforms: what LiteLLM 1.81 itself puts on the wire."""

    def _messages(self) -> list[Any]:
        return [{"role": "user", "content": _blocks()}]

    def test_anthropic_wire_body_should_keep_user_block_breakpoint(self) -> None:
        body = AnthropicConfig().transform_request(
            model="claude-haiku-4-5-20251001",
            messages=self._messages(),
            optional_params={"max_tokens": 10},
            litellm_params={},
            headers={},
        )

        assert body["messages"][0]["content"][0]["cache_control"] == _EPHEMERAL

    def test_openai_wire_body_should_drop_user_block_breakpoint(self) -> None:
        body = OpenAIGPTConfig().transform_request(
            model="gpt-4o-mini",
            messages=self._messages(),
            optional_params={"max_tokens": 10},
            litellm_params={},
            headers={},
        )

        assert "cache_control" not in json.dumps(body["messages"])


class TestCacheTokensStillRecorded:
    @pytest.fixture
    def recorder(self) -> Iterator[PipelineTimingRecorder]:
        token = pipeline_timing._recorder_var.set(None)
        try:
            yield start_run_timing()
        finally:
            pipeline_timing._recorder_var.reset(token)

    @pytest.fixture
    def fake_trace(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
        lc._reset_for_tests()
        monkeypatch.setattr(lc, "Langfuse", MagicMock(name="LangfuseSDK"))
        monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
        monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
        lc.init_langfuse()
        trace = MagicMock()
        cast(MagicMock, lc._client).trace.return_value = trace
        yield trace
        lc._reset_for_tests()

    async def test_should_record_cache_tokens_in_pipeline_timing(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        fake = AsyncMock(return_value=_response(cache_read=6000, cache_write=1200))
        with patch("src.services.llm_provider.acompletion", fake):
            await _provider(_HAIKU).complete(_blocks(), span_name="extraction")

        call = recorder.llm_calls[0]
        assert (call["cacheReadTokens"], call["cacheWriteTokens"]) == (6000, 1200)

    async def test_should_record_cache_read_tokens_on_langfuse_generation(
        self, fake_trace: MagicMock
    ) -> None:
        fake = AsyncMock(return_value=_response(cache_read=6000))
        with (
            patch("src.services.llm_provider.acompletion", fake),
            patch("src.services.llm_telemetry.completion_cost", return_value=0.0),
        ):
            async with lc.pipeline_trace("vid-1"):
                await _provider(_HAIKU).complete(_blocks(), span_name="extraction")

        assert fake_trace.generation.call_args.kwargs["metadata"]["cacheReadTokens"] == 6000
