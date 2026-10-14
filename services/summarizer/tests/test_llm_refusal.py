"""Refusal → the previous model, same call, once (pipeline-1min 4.0b) — zero spend.

Each call runs through ``LLMProvider`` and the real ``litellm.acompletion``
with a faked HTTP send (``tests.llm_wire``), so a refusal reaches the plumbing
exactly as LiteLLM 1.105 surfaces it: Anthropic ``stop_reason: "refusal"`` →
``finish_reason="content_filter"`` (streamed and not), OpenAI
``message.refusal`` → ``provider_specific_fields["refusal"]``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from llm_common.context import llm_refused_model_var

from src.services.llm import LLMService
from src.services.llm_model_rules import LLMRefusalError
from src.services.llm_provider import LLMProvider
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.utils.llm_retry import TRANSIENT_LLM_ERRORS, call_llm_with_retry
from tests.llm_wire import (
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
HAIKU_55 = "anthropic/claude-haiku-5-5"
SONNET_55 = "anthropic/claude-sonnet-5-5"
LUNA = "openai/gpt-6-luna"

Reply = Callable[[dict[str, Any]], httpx.Response]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, fresh_litellm_callbacks: None) -> None:
    set_fake_keys(monkeypatch)


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _claude(*blocks: dict[str, Any], refuse: bool = False) -> Reply:
    stop_reason = "refusal" if refuse else "end_turn"

    def reply(body: dict[str, Any]) -> httpx.Response:
        if body.get("stream"):
            return as_stream(
                anthropic_stream(list(blocks), stop_reason=stop_reason, model=body["model"])
            )
        return as_json(
            anthropic_message(list(blocks), stop_reason=stop_reason, model=body["model"])
        )

    return reply


def _gpt(content: str | None, *, refusal: str | None = None) -> Reply:
    def reply(body: dict[str, Any]) -> httpx.Response:
        return as_json(openai_message(content, refusal=refusal, model=body["model"]))

    return reply


def _by_model(replies: dict[str, Reply]) -> Reply:
    """Each bare model name answers with its own reply; any other model is a 500."""

    def reply(body: dict[str, Any]) -> httpx.Response:
        own = replies.get(body["model"])
        return own(body) if own is not None else httpx.Response(500, json={"error": "unexpected"})

    return reply


_HAIKU_55_REFUSES = {
    "claude-haiku-5-5": _claude(text("I won't"), refuse=True),
    "claude-haiku-4-5-20251001": _claude(text("ANSWER")),
}


class TestRefusalFallbackNonStreamed:
    async def test_should_return_the_previous_models_answer_when_haiku_5_5_refuses(self) -> None:
        with offline_http(_by_model(_HAIKU_55_REFUSES)) as wire:
            content = await LLMProvider(model=HAIKU_55).complete("hi", temperature=0.0)

        assert (content, wire.models) == (
            "ANSWER",
            ["claude-haiku-5-5", "claude-haiku-4-5-20251001"],
        )

    async def test_should_send_the_previous_model_its_own_params_when_falling_back(self) -> None:
        with offline_http(_by_model(_HAIKU_55_REFUSES)) as wire:
            await LLMProvider(model=HAIKU_55).complete("hi", temperature=0.0)

        fallback = wire.bodies[1]
        assert ({"thinking", "output_config"} & fallback.keys(), fallback["temperature"]) == (
            set(),
            0.0,
        )

    async def test_should_return_the_previous_models_answer_when_gpt_6_luna_refuses(self) -> None:
        replies = {"gpt-6-luna": _gpt(None, refusal="I can't help"), "gpt-4o-mini": _gpt("ANSWER")}

        with offline_http(_by_model(replies)) as wire:
            content = await LLMProvider(model=LUNA).complete("hi", json_mode=True)

        assert (content, wire.models, "reasoning_effort" in wire.bodies[1]) == (
            "ANSWER",
            ["gpt-6-luna", "gpt-4o-mini"],
            False,
        )


class TestRefusalFallbackStreamed:
    async def test_should_discard_the_partial_output_when_a_stream_refuses_midway(self) -> None:
        replies = {
            "claude-sonnet-5-5": _claude(text("partial answ"), refuse=True),
            "claude-sonnet-4-6": _claude(text("FULL ANSWER")),
        }

        with offline_http(_by_model(replies)) as wire:
            content = await LLMProvider(model=SONNET_55).complete("hi", on_first_chunk=lambda: None)

        assert (content, wire.models) == ("FULL ANSWER", ["claude-sonnet-5-5", "claude-sonnet-4-6"])

    async def test_should_never_send_between_tools_to_the_previous_model(self) -> None:
        replies = {
            "claude-sonnet-5-5": _claude(text("no"), refuse=True),
            "claude-sonnet-4-6": _claude(text("FULL ANSWER")),
        }

        with offline_http(_by_model(replies)) as wire:
            await LLMProvider(model=SONNET_55).complete("hi", on_first_chunk=lambda: None)

        assert ("thinking" in wire.bodies[0], "thinking" in wire.bodies[1]) == (True, False)


class TestRefusalWithoutAnAnswer:
    async def test_should_raise_a_refusal_error_when_the_previous_model_refuses_too(self) -> None:
        replies = {
            "claude-haiku-5-5": _claude(text("no"), refuse=True),
            "claude-haiku-4-5-20251001": _claude(text("no either"), refuse=True),
        }

        with offline_http(_by_model(replies)) as wire:
            with pytest.raises(LLMRefusalError) as raised:
                await LLMProvider(model=HAIKU_55).complete("hi")

        assert (raised.value.model, len(wire.bodies)) == (HAIKU_45, 2)

    async def test_should_raise_a_refusal_error_when_the_model_has_no_previous_model(self) -> None:
        with offline_http(
            _by_model({"claude-sonnet-4-6": _claude(text("no"), refuse=True)})
        ) as wire:
            with pytest.raises(LLMRefusalError):
                await LLMProvider(model=SONNET_46).complete("hi")

        assert len(wire.bodies) == 1

    async def test_should_raise_a_refusal_error_when_the_previous_model_fails(self) -> None:
        replies = {"claude-haiku-5-5": _claude(text("no"), refuse=True)}

        with offline_http(_by_model(replies)) as wire:
            with pytest.raises(LLMRefusalError) as raised:
                await LLMProvider(model=HAIKU_55).complete("hi")

        assert (raised.value.model, wire.models) == (
            HAIKU_55,
            ["claude-haiku-5-5", "claude-haiku-4-5-20251001"],
        )

    def test_should_not_be_retried_as_a_transient_error_when_vision_catches_it(self) -> None:
        assert not isinstance(LLMRefusalError(SONNET_55, None), TRANSIENT_LLM_ERRORS)


class TestRefusalInTheRetryWrapper:
    async def test_should_end_the_call_without_a_retry_or_cross_provider_hop(self) -> None:
        replies = {
            "claude-sonnet-5-5": _claude(text("no"), refuse=True),
            "claude-sonnet-4-6": _claude(text("no"), refuse=True),
            "gpt-4o": _gpt("cross-provider answer"),
        }
        service = LLMService(LLMProvider(model=SONNET_55, fallback_models=["openai/gpt-4o"]))

        with offline_http(_by_model(replies)) as wire:
            raw = await call_llm_with_retry(service, "hi", max_retries=2, stage_name="plan")

        assert (raw, wire.models) == (None, ["claude-sonnet-5-5", "claude-sonnet-4-6"])

    async def test_should_log_a_warning_not_an_error_when_the_call_ends_on_a_refusal(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        replies = {"claude-sonnet-5-5": _claude(text("no"), refuse=True)}
        service = LLMService(LLMProvider(model=SONNET_55))

        with caplog.at_level(logging.WARNING, logger=_RETRY_LOGGER):
            with offline_http(_by_model(replies)):
                await call_llm_with_retry(service, "hi", max_retries=2, stage_name="plan")

        assert _call_endings(caplog) == [("WARNING", "[plan] ended on a refusal (no retry)")]

    async def test_should_log_an_error_when_every_attempt_failed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        service = LLMService(LLMProvider(model=SONNET_55))

        with caplog.at_level(logging.WARNING, logger=_RETRY_LOGGER):
            with offline_http(_by_model({})):
                await call_llm_with_retry(service, "hi", max_retries=0, stage_name="plan")

        assert _call_endings(caplog) == [("ERROR", "[plan] All 1 attempts failed")]


_RETRY_LOGGER = "src.utils.llm_retry"


def _call_endings(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """The retry wrapper's closing line for a call that got no answer."""
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.name == _RETRY_LOGGER
        and ("ended on" in record.getMessage() or "attempts failed" in record.getMessage())
    ]


class TestRefusalTags:
    async def test_should_tag_only_the_fallback_request_for_the_ledger(self) -> None:
        with offline_http(_by_model(_HAIKU_55_REFUSES)) as wire:
            await LLMProvider(model=HAIKU_55).complete("hi")

        assert (wire.refused_model_tags, llm_refused_model_var.get()) == ([None, HAIKU_55], None)

    async def test_should_record_the_refusal_and_the_fallback_in_pipeline_timing(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_by_model(_HAIKU_55_REFUSES)):
            await LLMProvider(model=HAIKU_55).complete("hi", span_name="extraction")

        failure, call = recorder.llm_failures[0], recorder.llm_calls[0]
        assert (
            failure["error"],
            failure["model"],
            call["model"],
            call["fallbackUsed"],
            call["refusedModel"],
        ) == (
            "Refusal",
            HAIKU_55,
            HAIKU_45,
            True,
            HAIKU_55,
        )

    async def test_should_stamp_the_previous_models_error_with_the_refused_model(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_by_model({"claude-haiku-5-5": _claude(text("no"), refuse=True)})):
            with pytest.raises(LLMRefusalError):
                await LLMProvider(model=HAIKU_55).complete("hi", span_name="extraction")

        error_row = recorder.llm_failures[-1]
        assert (error_row["model"], error_row["refusedModel"], len(recorder.llm_failures)) == (
            HAIKU_45,
            HAIKU_55,
            2,
        )

    async def test_should_count_the_refused_calls_cost_in_the_run_cost(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_by_model(_HAIKU_55_REFUSES)):
            await LLMProvider(model=HAIKU_55).complete("hi", span_name="extraction")

        doc = recorder.to_document(tabs_planned=0, tabs_assembled=0)
        refused_usd = 1200 * 0.10e-6 + 40 * 0.50e-6
        answered_usd = 1200 * 1e-6 + 40 * 5e-6
        assert (doc["counts"]["refusals"], doc["costUsd"]) == (
            1,
            pytest.approx(refused_usd + answered_usd),
        )

    async def test_should_tag_both_langfuse_generations_when_a_refusal_falls_back(self) -> None:
        with patch("src.services.llm_provider.record_generation", MagicMock()) as generation:
            with offline_http(_by_model(_HAIKU_55_REFUSES)):
                await LLMProvider(model=HAIKU_55).complete("hi", span_name="plan")

        refused, answered = (c.kwargs["extra_metadata"] for c in generation.call_args_list)
        assert (refused["refusal"], answered["refusedModel"]) == (True, HAIKU_55)
