"""What reaches the provider, through LiteLLM's own transforms (pipeline-1min 4.0b) — zero spend.

Each call goes through ``LLMProvider`` and the real ``litellm.acompletion``;
only the HTTP send is faked (``tests.llm_wire``). Per model: the thinking-off
shape, the effort and the sampling params in the wire body. And the answer is
read by block type: a response or stream whose thinking block comes first
still yields only the text.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.llm_model_rules import Effort
from src.services.llm_provider import LLMProvider
from tests.llm_wire import (
    THINKING,
    anthropic_message,
    anthropic_stream,
    as_json,
    as_stream,
    offline_http,
    openai_message,
    set_fake_keys,
    text,
)

HAIKU_45 = "anthropic/claude-haiku-4-5-20251001"
SONNET_46 = "anthropic/claude-sonnet-4-6"
MINI = "openai/gpt-4o-mini"
GPT_4O = "openai/gpt-4o"
HAIKU_55 = "anthropic/claude-haiku-5-5"
SONNET_55 = "anthropic/claude-sonnet-5-5"
LUNA = "openai/gpt-6-luna"

_WIRE_KEYS = ("thinking", "output_config", "reasoning_effort", "temperature", "top_p", "top_k")
# The pick shapes' projection: the guards' keys, the stream flag, and every key
# a JSON reply could reach the wire as (a forced tool, a native output format).
_PICK_KEYS = (*_WIRE_KEYS, "stream", "tools", "tool_choice", "output_format")
# A 1x1 PNG, as frame vision sends its frames (data URLs).
_PIXEL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "pfZFQAAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, fresh_litellm_callbacks: None) -> None:
    set_fake_keys(monkeypatch)


def _plain_answer(body: dict[str, Any]):
    if body["model"].startswith("claude"):
        return as_json(anthropic_message([text("ok")]))
    return as_json(openai_message("ok"))


def _streamed_or_plain_answer(body: dict[str, Any]):
    if body.get("stream"):
        return as_stream(anthropic_stream([text("{}")]))
    return as_json(anthropic_message([text("{}")]))


def _wire(body: dict[str, Any], keys: tuple[str, ...] = _WIRE_KEYS) -> dict[str, Any]:
    return {key: body[key] for key in keys if key in body}


class TestWireBodyPerModel:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (HAIKU_45, {"temperature": 0.0}),
            (SONNET_46, {"temperature": 0.0}),
            (MINI, {"temperature": 0.0}),
            (GPT_4O, {"temperature": 0.0}),
            (HAIKU_55, {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}),
            (
                SONNET_55,
                {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}},
            ),
            (LUNA, {"reasoning_effort": "none", "temperature": 0.0}),
        ],
    )
    async def test_should_send_the_models_own_params_when_litellm_builds_the_body(
        self, model: str, expected: dict[str, Any]
    ) -> None:
        with offline_http(_plain_answer) as wire:
            await LLMProvider(model=model).complete("hi", temperature=0.0)

        assert _wire(wire.bodies[0]) == expected

    async def test_should_send_the_callers_effort_when_plan_asks_for_medium(self) -> None:
        with offline_http(_plain_answer) as wire:
            await LLMProvider(model=SONNET_55).complete("hi", effort="medium")

        assert wire.bodies[0]["output_config"] == {"effort": "medium"}


class TestWireBodyOfThePicks:
    """The request shapes the 4.0d picks send: plan / extraction on Sonnet 5.5 (JSON,
    the warm-up streamed, effort low or medium), memory on Haiku 5.5 (JSON, temperature 0)."""

    @pytest.mark.parametrize("effort", ["low", "medium"])
    async def test_should_send_sonnet_5_5_thinking_off_and_the_effort_when_json_is_streamed(
        self, effort: Effort
    ) -> None:
        with offline_http(_streamed_or_plain_answer) as wire:
            await LLMProvider(model=SONNET_55).complete(
                "hi", json_mode=True, on_first_chunk=lambda: None, effort=effort
            )

        assert _wire(wire.bodies[0], _PICK_KEYS) == {
            "thinking": {"type": "between_tools"},
            "output_config": {"effort": effort},
            "stream": True,
        }

    @pytest.mark.parametrize("effort", ["low", "medium"])
    async def test_should_send_sonnet_5_5_no_stream_flag_when_json_is_not_streamed(
        self, effort: Effort
    ) -> None:
        with offline_http(_streamed_or_plain_answer) as wire:
            await LLMProvider(model=SONNET_55).complete("hi", json_mode=True, effort=effort)

        assert _wire(wire.bodies[0], _PICK_KEYS) == {
            "thinking": {"type": "between_tools"},
            "output_config": {"effort": effort},
        }

    async def test_should_drop_the_temperature_when_memory_sends_json_to_haiku_5_5(self) -> None:
        with offline_http(_streamed_or_plain_answer) as wire:
            await LLMProvider(model=HAIKU_55).complete("hi", json_mode=True, temperature=0.0)

        assert _wire(wire.bodies[0], _PICK_KEYS) == {
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low"},
        }


class TestAnswerTextByBlockType:
    async def test_should_return_only_the_text_when_a_thinking_block_comes_first(self) -> None:
        def answer(_body: dict[str, Any]):
            return as_json(anthropic_message([THINKING, text("ANSWER")]))

        with offline_http(answer):
            content = await LLMProvider(model=HAIKU_55).complete("hi")

        assert content == "ANSWER"

    async def test_should_return_only_the_text_when_a_streamed_thinking_block_comes_first(
        self,
    ) -> None:
        def answer(_body: dict[str, Any]):
            return as_stream(anthropic_stream([THINKING, text("STREAMED ANSWER")]))

        with offline_http(answer):
            content = await LLMProvider(model=HAIKU_55).complete("hi", on_first_chunk=lambda: None)

        assert content == "STREAMED ANSWER"

    async def test_should_return_only_the_text_when_a_vision_answer_starts_with_thinking(
        self,
    ) -> None:
        frames = [text("Describe the frame."), {"type": "image_url", "image_url": {"url": _PIXEL}}]

        def answer(_body: dict[str, Any]):
            return as_json(anthropic_message([THINKING, text('[{"frame": 0}]')]))

        with offline_http(answer):
            content = await LLMProvider(model=SONNET_55).complete_with_messages(
                [{"role": "user", "content": frames}]
            )

        assert content == '[{"frame": 0}]'
