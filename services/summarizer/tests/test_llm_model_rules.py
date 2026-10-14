"""Model-keyed request rules (pipeline-1min 4.0b): thinking, effort, sampling, prefill, model map.

Table-driven over today's models (Haiku 4.5, Sonnet 4.6, gpt-4o-mini, gpt-4o —
whose requests must stay byte-identical) and the refresh targets (Haiku 5.5,
Sonnet 5.5, gpt-6-luna). The wire bodies LiteLLM builds from these requests
are checked in ``test_llm_wire_body.py``.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import litellm
import pytest
from litellm import ModelResponse
from litellm.llms.anthropic.common_utils import AnthropicModelInfo
from litellm.types.utils import Usage

from src.services import llm_model_rules as rules
from src.services.llm_model_rules import AssistantPrefillError, refuse_prefill, request_for_model
from src.services.llm_provider import LLMProvider

HAIKU_45 = "anthropic/claude-haiku-4-5-20251001"
SONNET_46 = "anthropic/claude-sonnet-4-6"
MINI = "openai/gpt-4o-mini"
GPT_4O = "openai/gpt-4o"
HAIKU_55 = "anthropic/claude-haiku-5-5"
SONNET_55 = "anthropic/claude-sonnet-5-5"
LUNA = "openai/gpt-6-luna"

TODAYS_MODELS = (HAIKU_45, SONNET_46, MINI, GPT_4O)
ALL_MODELS = (*TODAYS_MODELS, HAIKU_55, SONNET_55, LUNA)


def _request(model: str, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}],
        "max_tokens": 100,
        "timeout": 30.0,
        "num_retries": 0,
        **extra,
    }


def _added(model: str, *, temperature: float | None = None, effort: rules.Effort = "low") -> dict:
    """The keys ``request_for_model`` added to (or changed in) the caller's request."""
    base = _request(model)
    sent = request_for_model(base, temperature=temperature, effort=effort)
    return {key: value for key, value in sent.items() if base.get(key) != value}


class TestTodaysModels:
    @pytest.mark.parametrize("model", TODAYS_MODELS)
    def test_should_send_the_callers_request_unchanged_when_no_temperature_is_set(
        self, model: str
    ) -> None:
        assert request_for_model(_request(model), temperature=None, effort="low") == _request(model)

    @pytest.mark.parametrize("model", TODAYS_MODELS)
    def test_should_send_the_callers_request_unchanged_when_the_effort_is_medium(
        self, model: str
    ) -> None:
        # The plan asks for medium (4.0d); its fallbacks are today's models.
        assert request_for_model(_request(model), temperature=None, effort="medium") == _request(
            model
        )

    @pytest.mark.parametrize("model", TODAYS_MODELS)
    def test_should_add_only_the_temperature_when_one_is_set(self, model: str) -> None:
        assert _added(model, temperature=0.0) == {"temperature": 0.0}


class TestThinkingOffAndEffort:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (HAIKU_55, {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}),
            (
                SONNET_55,
                {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}},
            ),
            (LUNA, {"reasoning_effort": "none"}),
        ],
    )
    def test_should_send_the_models_thinking_off_shape_when_it_thinks_by_default(
        self, model: str, expected: dict[str, Any]
    ) -> None:
        assert _added(model) == expected

    def test_should_send_the_callers_effort_when_it_is_not_the_default(self) -> None:
        assert _added(SONNET_55, effort="medium")["output_config"] == {"effort": "medium"}

    @pytest.mark.parametrize("model", [m for m in ALL_MODELS if m != SONNET_55])
    def test_should_never_send_between_tools_when_the_model_is_not_sonnet_5_5(
        self, model: str
    ) -> None:
        assert _added(model).get("thinking", {}).get("type") != "between_tools"


class TestSampling:
    @pytest.mark.parametrize(
        ("model", "kept"),
        [
            (HAIKU_45, True),
            (SONNET_46, True),
            (MINI, True),
            (GPT_4O, True),
            (HAIKU_55, False),
            (SONNET_55, False),
            (LUNA, True),
            ("openai/gpt-5-mini", False),
        ],
    )
    def test_should_send_temperature_only_when_the_model_accepts_it_at_the_effort_sent(
        self, model: str, kept: bool
    ) -> None:
        assert ("temperature" in _added(model, temperature=0.0)) is kept

    @pytest.mark.parametrize("model", [HAIKU_55, SONNET_55])
    def test_should_drop_top_p_and_top_k_when_the_model_rejects_sampling_params(
        self, model: str
    ) -> None:
        sent = request_for_model(
            _request(model, top_p=0.9, top_k=40), temperature=None, effort="low"
        )

        assert {"top_p", "top_k"} & sent.keys() == set()

    def test_should_keep_temperature_one_when_the_model_rejects_other_values(self) -> None:
        assert _added(SONNET_55, temperature=1.0)["temperature"] == 1.0

    def test_should_not_warn_when_memorys_temperature_is_dropped_for_haiku_5_5(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Memory sends temperature 0 to Haiku 5.5 on every run, by design (4.0d).
        with caplog.at_level(logging.WARNING, logger=rules.__name__):
            request_for_model(_request(HAIKU_55), temperature=0.0, effort="low")

        assert caplog.records == []


class TestMessages:
    def test_should_leave_the_callers_request_untouched_when_shaping_it(self) -> None:
        cached = {"type": "text", "text": "head", "cache_control": {"type": "ephemeral"}}
        request = _request(MINI, messages=[{"role": "user", "content": [cached]}])

        request_for_model(request, temperature=0.0, effort="low")

        assert request == _request(MINI, messages=[{"role": "user", "content": [cached]}])

    @pytest.mark.parametrize(("model", "kept"), [(HAIKU_55, True), (LUNA, False)])
    def test_should_keep_cache_breakpoints_only_when_the_model_honours_them(
        self, model: str, kept: bool
    ) -> None:
        cached = {"type": "text", "text": "head", "cache_control": {"type": "ephemeral"}}
        request = _request(model, messages=[{"role": "user", "content": [cached]}])

        sent = request_for_model(request, temperature=None, effort="low")

        assert ("cache_control" in sent["messages"][0]["content"][0]) is kept


class TestNoAssistantPrefill:
    def test_should_refuse_a_request_when_its_last_message_is_the_assistants(self) -> None:
        messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "{"}]

        with pytest.raises(AssistantPrefillError):
            refuse_prefill(messages)

    def test_should_accept_a_request_when_its_last_message_is_the_users(self) -> None:
        refuse_prefill([{"role": "assistant", "content": "a"}, {"role": "user", "content": "q"}])

    async def test_should_raise_before_litellm_when_a_caller_prefills(self) -> None:
        fake = AsyncMock()
        prefilled = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "{"}]

        with patch("src.services.llm_provider.acompletion", fake):
            with pytest.raises(AssistantPrefillError):
                await LLMProvider(model=HAIKU_45).complete_with_messages(prefilled)

        fake.assert_not_called()


def _haiku_55_response(prompt_tokens: int, completion_tokens: int) -> ModelResponse:
    usage = Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    choice = {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "x"}}
    return ModelResponse(model="claude-haiku-5-5", choices=[choice], usage=usage)


@pytest.fixture
def map_without_haiku_55(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """LiteLLM's loaded map as 1.105.0 bundles it: no ``claude-haiku-5-5`` entry."""
    trimmed = {key: value for key, value in litellm.model_cost.items() if key != "claude-haiku-5-5"}
    monkeypatch.setattr(litellm, "model_cost", trimmed)
    return trimmed


class TestHaiku55ModelMapEntry:
    def test_should_register_the_entry_when_the_loaded_map_lacks_it(
        self, map_without_haiku_55: dict[str, Any]
    ) -> None:
        assert rules.register_missing_model_entries() is True

    def test_should_leave_the_loaded_entry_alone_when_the_map_has_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loaded = {
            "claude-haiku-5-5": {"input_cost_per_token": 3e-7, "litellm_provider": "anthropic"}
        }
        monkeypatch.setattr(litellm, "model_cost", loaded)

        rules.register_missing_model_entries()

        assert litellm.model_cost["claude-haiku-5-5"]["input_cost_per_token"] == 3e-7

    @pytest.mark.parametrize(
        ("prompt_tokens", "expected_usd"),
        [
            (10_000, 10_000 * 0.10e-6 + 1_000 * 0.50e-6),
            (150_000, 150_000 * 0.50e-6 + 1_000 * 2.50e-6),
        ],
    )
    def test_should_price_calls_at_the_docs_rates_when_registered(
        self, map_without_haiku_55: dict[str, Any], prompt_tokens: int, expected_usd: float
    ) -> None:
        rules.register_missing_model_entries()

        cost = litellm.completion_cost(completion_response=_haiku_55_response(prompt_tokens, 1_000))

        assert cost == pytest.approx(expected_usd)

    def test_should_give_litellm_the_flags_its_anthropic_mapping_keys_on_when_registered(
        self, map_without_haiku_55: dict[str, Any]
    ) -> None:
        rules.register_missing_model_entries()

        assert (
            AnthropicModelInfo._supports_sampling_params("claude-haiku-5-5"),
            AnthropicModelInfo._is_adaptive_thinking_model("claude-haiku-5-5", "anthropic"),
            AnthropicModelInfo._is_always_on_thinking_model("claude-haiku-5-5", "anthropic"),
        ) == (False, True, False)
