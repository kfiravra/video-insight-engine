"""Tests for ``llm_common.observability.prompts``: the prompt miss cache and active prompts."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_common.observability import prompts as lp


@pytest.fixture(autouse=True)
def _reset():
    lp.reset()
    yield
    lp.reset()


class _Clock:
    """``lp.time`` stand-in: a monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _registry(monkeypatch, get_prompt: MagicMock) -> tuple[MagicMock, _Clock]:
    clock = _Clock()
    monkeypatch.setattr(lp, "time", clock)
    return MagicMock(get_prompt=get_prompt), clock


class TestPromptMisses:
    def test_should_not_refetch_a_missing_prompt_within_the_miss_ttl(self, monkeypatch):
        get_prompt = MagicMock(side_effect=RuntimeError("404 prompt not found"))
        client, clock = _registry(monkeypatch, get_prompt)
        lp.fetch_prompt(client, "summarizer:extraction_group_job")
        clock.now += lp.PROMPT_MISS_TTL_S - 1

        assert lp.fetch_prompt(client, "summarizer:extraction_group_job") is None

        assert get_prompt.call_count == 1

    def test_should_refetch_a_missing_prompt_once_the_miss_ttl_passed(self, monkeypatch):
        get_prompt = MagicMock(side_effect=RuntimeError("404 prompt not found"))
        client, clock = _registry(monkeypatch, get_prompt)
        lp.fetch_prompt(client, "summarizer:extraction_group_job")
        clock.now += lp.PROMPT_MISS_TTL_S

        lp.fetch_prompt(client, "summarizer:extraction_group_job")

        assert get_prompt.call_count == 2

    def test_should_still_fetch_other_prompts_while_one_is_missing(self, monkeypatch):
        found = MagicMock(prompt="TEXT")
        client, _ = _registry(monkeypatch, MagicMock(side_effect=[RuntimeError("404"), found]))
        lp.fetch_prompt(client, "summarizer:extraction_group_job")

        assert lp.fetch_prompt(client, "summarizer:plan") is found

    def test_should_ask_the_sdk_for_a_found_prompt_on_every_load(self, monkeypatch):
        """Hits keep the SDK's own cache (60 s TTL, background refresh)."""
        get_prompt = MagicMock(return_value=MagicMock(prompt="TEXT"))
        client, _ = _registry(monkeypatch, get_prompt)
        lp.fetch_prompt(client, "summarizer:plan")

        lp.fetch_prompt(client, "summarizer:plan")

        assert get_prompt.call_count == 2

    def test_should_return_none_without_a_client(self):
        assert lp.fetch_prompt(None, "summarizer:plan") is None


class TestActivePrompts:
    def test_should_record_nothing_outside_a_scope(self):
        lp.record_active_prompt("summarizer:plan", "3")
        assert lp.get_active_prompts() == {}

    def test_should_record_versions_and_link_the_latest_prompt_object(self):
        tokens = lp.open_scope()
        try:
            prompt_obj = MagicMock(prompt="TEXT", version=4)
            lp.record_active_prompt("summarizer:plan", prompt_obj)
            lp.record_active_prompt("summarizer:memory", "2")

            assert lp.get_active_prompts() == {"summarizer:plan": "4", "summarizer:memory": "2"}
            assert lp.latest_prompt() is prompt_obj
        finally:
            lp.close_scope(tokens)
        assert lp.get_active_prompts() == {}
        assert lp.latest_prompt() is None
