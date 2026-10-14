"""Unit tests for the model A/B's estimate, run order and arm re-shaping (pipeline-1min 4.0d).

The estimate is checked on hand-made requests (cache breakpoints, tokenizer
factor, price tiers on a patched map entry, the spend ceiling); the arm
re-shaping and the wire check on the requests the T1dQhQAm8Tc capture records,
sent through the A/B's live send path with the HTTP layer patched
(``tests/llm_wire.offline_http``). No network, no LLM, no spend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import litellm
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from _model_ab_capture import capture_cassettes  # noqa: E402
from _model_ab_cost import (  # noqa: E402
    CacheLedger,
    CallEstimate,
    PlannedCall,
    estimate_call,
    model_tokens,
    over_ceiling,
    prices,
    run_order,
)
from _model_ab_types import (  # noqa: E402
    CALL_TYPES,
    GPT_4O_MINI,
    HAIKU_45,
    HAIKU_55,
    AbRequest,
    Arm,
    arms_for,
    request_for_arm,
    reshaped_keys,
)
from _model_ab_wire import wire_check, wire_findings  # noqa: E402

# What an arm may add to or drop from today's request (4.0b's request rules).
_ALLOWED_CHANGES = frozenset({"+thinking", "+output_config", "-temperature", "+reasoning_effort"})


@pytest.fixture(scope="module")
def captured() -> list[AbRequest]:
    return capture_cassettes(("T1dQhQAm8Tc",))


def _request(
    model: str, blocks: list[tuple[str, str | None]], *, repeats: int = 1, video: str = "v1"
) -> AbRequest:
    content: list[dict[str, Any]] = []
    for text, ttl in blocks:
        block: dict[str, Any] = {"type": "text", "text": text}
        if ttl:
            block["cache_control"] = {"type": "ephemeral", **({"ttl": "1h"} if ttl == "1h" else {})}
        content.append(block)
    return AbRequest(
        request_id=f"{video}/extraction#{len(content)}",
        call_type="extraction",
        source="test",
        video_id=video,
        kwargs={"model": model, "messages": [{"role": "user", "content": content}], "timeout": 5},
        expected_output_tokens=100,
        repeats=repeats,
    )


def _words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


class TestCacheEstimate:
    def test_should_read_a_prefix_when_the_same_model_sends_it_a_second_time(self) -> None:
        request = _request(HAIKU_55, [(_words(2000), "1h"), ("job", None)])
        ledger, arm = CacheLedger(), Arm("B", HAIKU_55)
        first = estimate_call(PlannedCall(request, arm, 0), ledger)
        second = estimate_call(PlannedCall(request, arm, 1), ledger)
        assert (first.cache_read, second.cache_read) == (0, first.cache_write_1h)

    def test_should_cache_the_whole_prefix_at_the_next_breakpoint_when_a_block_is_too_short(
        self,
    ) -> None:
        request = _request(HAIKU_45, [(_words(1500), "1h"), (_words(1500), "5m"), ("job", None)])
        estimate = estimate_call(PlannedCall(request, Arm("A", HAIKU_45), 0), CacheLedger())
        assert (estimate.cache_write_1h, estimate.cache_write_5m > 4096) == (0, True)

    def test_should_credit_no_cache_when_the_model_is_openai(self) -> None:
        request = _request(GPT_4O_MINI, [(_words(2000), "1h"), ("job", None)])
        ledger, arm = CacheLedger(), Arm("A", GPT_4O_MINI)
        estimate_call(PlannedCall(request, arm, 0), ledger)
        second = estimate_call(PlannedCall(request, arm, 1), ledger)
        assert second.cache_read + second.cache_write_5m + second.cache_write_1h == 0

    def test_should_count_30_percent_more_tokens_when_the_model_has_the_new_tokenizer(
        self,
    ) -> None:
        assert (model_tokens(1000, HAIKU_55), model_tokens(1000, HAIKU_45)) == (1300, 1000)


class TestPrices:
    @pytest.fixture
    def tiered(self, monkeypatch: pytest.MonkeyPatch) -> str:
        entry = {
            "input_cost_per_token": 1e-07,
            "input_cost_per_token_above_100k_tokens": 5e-07,
            "output_cost_per_token": 5e-07,
        }
        monkeypatch.setitem(litellm.model_cost, "ab-test-model", entry)
        return "anthropic/ab-test-model"

    def test_should_price_the_higher_tier_when_the_prompt_is_over_100k(self, tiered: str) -> None:
        assert (prices(tiered, 1_000).input, prices(tiered, 150_000).input) == (1e-07, 5e-07)

    def test_should_price_a_1h_write_at_twice_the_input_when_the_map_has_no_rate(
        self, tiered: str
    ) -> None:
        assert prices(tiered, 1_000).write_1h == pytest.approx(2e-07)


class TestCeiling:
    def _estimates(self, cost: float) -> list[CallEstimate]:
        return [CallEstimate("r", "plan", "A", HAIKU_45, 1, 1, cost_usd=cost)]

    def test_should_flag_the_run_when_the_estimate_is_above_the_ceiling(self) -> None:
        assert over_ceiling(self._estimates(1.51)) is True

    def test_should_pass_the_run_when_the_estimate_is_under_the_ceiling(self) -> None:
        assert over_ceiling(self._estimates(1.49)) is False


class TestRunOrder:
    def test_should_send_each_arm_once_per_repeat_when_a_request_repeats(self) -> None:
        request = _request(HAIKU_45, [("x", None)], repeats=2)
        calls = [(c.arm.name, c.repeat) for c in run_order([request])]
        assert calls == [("A", 0), ("B", 0), ("A", 1), ("B", 1)]

    def test_should_start_with_the_other_arm_when_the_next_video_runs(self) -> None:
        requests = [_request(HAIKU_45, [("x", None)], video=v) for v in ("v1", "v2")]
        firsts = [c.arm.name for c in run_order(requests)][::2]
        assert firsts == ["A", "B"]


class TestArmReshaping:
    def test_should_keep_the_request_byte_identical_when_the_arm_is_todays_model(
        self, captured: list[AbRequest]
    ) -> None:
        for request in captured:
            sent = request_for_arm(request.kwargs, arms_for(request.call_type)[0])
            assert json.dumps(sent, sort_keys=True) == json.dumps(request.kwargs, sort_keys=True)

    def test_should_change_only_the_models_params_when_the_arm_is_a_new_model(
        self, captured: list[AbRequest]
    ) -> None:
        for request in captured:
            for arm in arms_for(request.call_type, medium=frozenset({"plan"}))[1:]:
                changes = reshaped_keys(request.kwargs, request_for_arm(request.kwargs, arm))
                assert set(changes) <= _ALLOWED_CHANGES, (request.request_id, arm.name)

    def test_should_refuse_an_arm_when_its_model_is_on_another_provider(
        self, captured: list[AbRequest]
    ) -> None:
        openai_request = next(r for r in captured if r.call_type == "faithfulness")
        with pytest.raises(ValueError, match="provider"):
            request_for_arm(openai_request.kwargs, Arm("B", HAIKU_55))

    def test_should_send_no_param_a_model_rejects_when_every_arm_goes_over_the_wire(
        self, captured: list[AbRequest]
    ) -> None:
        firsts = {r.call_type: r for r in reversed(captured)}
        planned = [
            PlannedCall(firsts[c], arm, 0)
            for c in CALL_TYPES
            if c in firsts
            for arm in arms_for(c, medium=frozenset({"plan"}))
        ]
        rows = wire_check(planned)
        assert [(r["call_type"], r["arm"]) for r in rows if r["problems"] or r["error"]] == []


class TestWireFindings:
    def test_should_flag_a_sampling_param_when_a_new_model_gets_it(self) -> None:
        body = {"temperature": 0.0, "messages": [{"role": "user", "content": "hi"}]}
        assert wire_findings(HAIKU_55, body) == ["sends temperature=0.0"]

    def test_should_flag_a_prefill_when_the_last_message_is_the_assistants(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "{"}]
        }
        assert wire_findings(HAIKU_45, body) == ["assistant prefill"]
