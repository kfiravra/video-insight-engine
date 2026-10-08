"""Unit tests for ``src.services.llm_messages`` — cache-breakpoint message shaping (D2).

String prompts must produce exactly today's messages (behaviour-neutral for
every existing caller); block prompts keep a caller-placed user breakpoint on
Anthropic; every other provider gets copies without any ``cache_control``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from src.services.llm_messages import (
    Message,
    TextBlock,
    build_prompt_messages,
    prepare_for_model,
    text_block,
    to_message_dicts,
)

_ANTHROPIC = "anthropic/claude-haiku-4-5-20251001"
_OPENAI = "openai/gpt-4o-mini"
_EPHEMERAL = {"type": "ephemeral"}


def _blocks() -> list[TextBlock]:
    return [text_block("TRANSCRIPT + MEMORY", cache=True), text_block("YOUR JOB")]


def _has_cache_control(messages: list[dict[str, Any]]) -> bool:
    return "cache_control" in json.dumps(messages)


class TestTextBlock:
    def test_should_mark_breakpoint_when_cache_requested(self) -> None:
        assert text_block("x", cache=True) == {
            "type": "text",
            "text": "x",
            "cache_control": _EPHEMERAL,
        }

    def test_should_carry_no_breakpoint_by_default(self) -> None:
        assert text_block("x") == {"type": "text", "text": "x"}


class TestBuildPromptMessages:
    def test_should_send_only_the_user_string_when_no_system_prompt(self) -> None:
        messages = build_prompt_messages("dynamic", system_prompt=None)

        assert messages == [{"role": "user", "content": "dynamic"}]

    def test_should_send_plain_system_then_user_when_system_prompt_given(self) -> None:
        messages = build_prompt_messages("dynamic", system_prompt="rules")

        assert messages == [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "dynamic"},
        ]

    def test_should_keep_user_breakpoint_as_given(self) -> None:
        messages = build_prompt_messages(_blocks(), system_prompt=None)

        assert messages == [{"role": "user", "content": _blocks()}]

    def test_should_never_put_a_breakpoint_on_the_system_prompt(self) -> None:
        messages = build_prompt_messages(_blocks(), system_prompt="RULES")

        assert messages == [
            {"role": "system", "content": "RULES"},
            {"role": "user", "content": _blocks()},
        ]


class TestPrepareForModel:
    def _cached_messages(self) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": [text_block("RULES", cache=True)]},
            {"role": "user", "content": _blocks(), "cache_control": _EPHEMERAL},
        ]

    def test_should_pass_anthropic_messages_through_untouched(self) -> None:
        messages = self._cached_messages()

        assert prepare_for_model(messages, _ANTHROPIC) is messages

    def test_should_strip_every_breakpoint_when_not_anthropic(self) -> None:
        prepared = prepare_for_model(self._cached_messages(), _OPENAI)

        assert not _has_cache_control(prepared)

    def test_should_keep_block_text_and_order_when_stripping(self) -> None:
        prepared = prepare_for_model(self._cached_messages(), _OPENAI)

        assert prepared[1]["content"] == [
            {"type": "text", "text": "TRANSCRIPT + MEMORY"},
            {"type": "text", "text": "YOUR JOB"},
        ]

    def test_should_not_mutate_caller_messages_when_stripping(self) -> None:
        messages = self._cached_messages()
        before = copy.deepcopy(messages)

        prepare_for_model(messages, _OPENAI)

        assert messages == before

    def test_should_serialize_identically_when_nothing_to_strip(self) -> None:
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
        ]

        assert json.dumps(prepare_for_model(messages, _OPENAI)) == json.dumps(messages)


class TestToMessageDicts:
    def test_should_dump_message_objects_and_keep_dicts(self) -> None:
        raw = {"role": "user", "content": _blocks()}

        result = to_message_dicts([Message(role="system", content="s"), raw])

        assert result == [{"role": "system", "content": "s"}, raw]
