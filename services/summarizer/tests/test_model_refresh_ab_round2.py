"""Unit tests for the model A/B round 2 (``scripts/_model_ab_{variants,round2,eqa}.py``, 4.0d).

The completeness variant and the wire check run on the requests the
T1dQhQAm8Tc capture records (round 1's TODAY models pinned); the schedule,
the identity check, the estimate split and the Eqa plan build on small
hand-made inputs (the Eqa build through the pipeline's own ``run_plan`` into
the recording provider). No network, no LLM, no spend.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from _model_ab_capture import capture_cassettes, check_model_agnostic  # noqa: E402
from _model_ab_cost import CallEstimate, PlannedCall  # noqa: E402
from _model_ab_eqa import (  # noqa: E402
    description_of,
    eqa_plan_request,
    match_traced,
    trace_check,
)
from _model_ab_report import true_median  # noqa: E402
from _model_ab_round2 import (  # noqa: E402
    ROUND2_CALL_TYPES,
    ScheduledCall,
    calibrated_cost,
    first_calls,
    identity_rows,
    measured_sizes,
    schedule,
    split_totals,
    variant_check,
)
from _model_ab_runner import arm_request  # noqa: E402
from _model_ab_types import HAIKU_45, HAIKU_55, SONNET_46, SONNET_55, AbRequest, Arm  # noqa: E402
from _model_ab_variants import (  # noqa: E402
    COMPLETENESS,
    COMPLETENESS_INSERT,
    add_completeness_rule,
    apply_variant,
    insertion_point,
)
from _model_ab_wire import wire_check  # noqa: E402

from src.services.video.description_analyzer import load_prompt  # noqa: E402

VIDEO = "T1dQhQAm8Tc"
_CASSETTES = ("T1dQhQAm8Tc", "jMq8lEu-of0", "uC45_4nnEAI")
_EQA = "EqaMrzd9nZU"
_PROBE = (
    '{"domain": "tech", "format": "commentary", "has_visual_demo": false, '
    '"confidence": 0.92, "transcript_usable": true}'
)
_DESCRIPTION = "LangChain and LangGraph, introduced.\n\n00:00 intro\n02:09 chains"


@pytest.fixture(scope="module")
def captured() -> list[AbRequest]:
    return [r for r in capture_cassettes((VIDEO,)) if r.call_type in ROUND2_CALL_TYPES]


def _static_request(rules: str) -> dict[str, Any]:
    static = {"type": "text", "text": rules, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    return {"model": HAIKU_45, "messages": [{"role": "user", "content": [static]}]}


def _request(video: str, call_type: str, number: int = 0) -> AbRequest:
    kwargs = {"model": HAIKU_45, "messages": [{"role": "user", "content": "x"}]}
    return AbRequest(f"{video}/{call_type}#{number}", call_type, "test", video, kwargs, 10)


def _round2_requests() -> list[AbRequest]:
    requests = [_request(_EQA, "plan")]
    for video in _CASSETTES:
        requests += [_request(video, "plan"), _request(video, "memory")]
        requests += [_request(video, "extraction", n) for n in range(2)]
    return requests


class TestCompletenessVariant:
    def test_should_add_the_rule_as_the_last_quality_rule_when_the_block_has_the_section(
        self,
    ) -> None:
        rules = "<quality_rules>\nData accuracy:\n- one\n</quality_rules>\n<output_rules>"
        changed = add_completeness_rule(_static_request(rules))
        text = changed["messages"][0]["content"][0]["text"]
        assert text == rules.replace(
            "\n</quality_rules>", COMPLETENESS_INSERT + "\n</quality_rules>"
        )

    def test_should_leave_the_callers_request_untouched_when_the_rule_is_added(self) -> None:
        request = _static_request("<quality_rules>\n- one\n</quality_rules>")
        before = copy.deepcopy(request)
        add_completeness_rule(request)
        assert request == before

    def test_should_refuse_the_request_when_its_first_block_is_not_the_1h_static_block(
        self,
    ) -> None:
        request = _static_request("<quality_rules>\n- one\n</quality_rules>")
        del request["messages"][0]["content"][0]["cache_control"]
        with pytest.raises(ValueError, match="1-hour"):
            add_completeness_rule(request)

    def test_should_refuse_the_request_when_the_rules_section_is_missing(self) -> None:
        with pytest.raises(ValueError, match="quality_rules"):
            add_completeness_rule(_static_request("<output_rules>\n</output_rules>"))

    def test_should_report_where_the_rule_lands_when_the_request_is_unchanged(self) -> None:
        rules = "<quality_rules>\n- one\n</quality_rules>"
        point = insertion_point(_static_request(rules))
        assert (point["char_offset"], point["inserted_utf8_bytes"]) == (21, 127)

    def test_should_match_the_pipelines_group_rules_byte_for_byte(self) -> None:
        """The 4.0 picks ship the measured configuration: the split path's block 1
        (``with_group_rules``) is the variant applied to round 1's block 1."""
        from src.services.pipeline.extraction_group_prompt import prefix_block, with_group_rules
        from src.services.pipeline.extraction_prompt import (
            ExtractionPromptInput,
            build_extraction_prompt,
        )

        prompt = build_extraction_prompt(
            ExtractionPromptInput(["food"], [], "- one\n- two", title="T", tabs=[])
        )
        measured = add_completeness_rule(
            {"model": HAIKU_45, "messages": [{"role": "user", "content": [prefix_block(prompt)]}]}
        )
        shipped = prefix_block(with_group_rules(prompt))

        assert shipped["text"] == measured["messages"][0]["content"][0]["text"]

    def test_should_return_the_same_request_when_the_arm_has_no_variant(self) -> None:
        request = _static_request("x")
        assert apply_variant(request, None) is request

    def test_should_put_the_rule_in_one_static_block_when_every_group_of_a_video_gets_it(
        self, captured: list[AbRequest]
    ) -> None:
        check = variant_check(captured)
        assert (check["requests"], check["static_blocks_per_video"]) == (4, {VIDEO: 1})

    def test_should_send_the_same_prompt_bytes_when_every_rule_arm_sends_a_group(
        self, captured: list[AbRequest]
    ) -> None:
        group = next(r for r in captured if r.call_type == "extraction")
        arms = [Arm(n, m, "low", COMPLETENESS) for n, m in (("A", HAIKU_45), ("B", HAIKU_55))]
        arms.append(Arm("C", SONNET_55, "low", COMPLETENESS))
        sent = [arm_request(PlannedCall(group, arm, 0))["messages"] for arm in arms]
        assert sent[0] == sent[1] == sent[2] != group.kwargs["messages"]


class TestCapture:
    def test_should_capture_the_plan_on_round_1s_model_when_the_defaults_moved(
        self, captured: list[AbRequest]
    ) -> None:
        plan = next(r for r in captured if r.call_type == "plan")
        assert (plan.kwargs["model"], "thinking" in plan.kwargs) == (SONNET_46, False)

    def test_should_refuse_a_capture_when_a_request_carries_a_models_own_params(self) -> None:
        request = _request(VIDEO, "plan")
        request.kwargs |= {"model": SONNET_46, "thinking": {"type": "between_tools"}}
        with pytest.raises(ValueError, match="thinking"):
            check_model_agnostic([request])


class TestSchedule:
    def _calls(self) -> list[ScheduledCall]:
        return schedule(_round2_requests())

    def test_should_plan_every_round_2_call_once_when_the_schedule_is_built(self) -> None:
        keys = [(c.call.request.request_id, c.call.arm.name, c.call.repeat) for c in self._calls()]
        assert (len(keys), len(set(keys))) == (2 * 4 + 4 + 4 * 2 * 6 + 2 * 2 + 3 * 2, len(keys))

    def test_should_number_the_repeats_after_round_1s_when_an_arm_already_ran(self) -> None:
        repeats = {
            (c.call.request.call_type, c.call.arm.name, c.call.repeat) for c in self._calls()
        }
        assert {r for (t, a, r) in repeats if t == "memory"} == {1, 2}
        assert {r for (t, a, r) in repeats if t == "extraction" and a == "A"} == {1}

    def test_should_mark_only_the_medium_plan_optional_when_the_schedule_is_built(self) -> None:
        optional = {
            (c.call.request.call_type, c.call.arm.name) for c in self._calls() if c.optional
        }
        assert optional == {("plan", "B-medium")}

    def test_should_send_each_plan_arm_back_to_back_before_any_other_call(self) -> None:
        kinds = [(c.call.request.call_type, c.call.arm.name) for c in self._calls()]
        assert kinds[:12] == [("plan", "B-high")] * 8 + [("plan", "B-medium")] * 4

    def test_should_send_an_arms_repeats_of_a_video_back_to_back_when_it_extracts(self) -> None:
        runs = [
            (c.call.request.video_id, c.call.arm.name)
            for c in self._calls()
            if c.call.request.call_type == "extraction"
        ]
        blocks = [runs[0]] + [b for a, b in zip(runs, runs[1:], strict=False) if a != b]
        assert len(blocks) == len(set(blocks))

    def test_should_sample_each_arm_once_when_the_wire_check_picks_its_calls(self) -> None:
        samples = first_calls(self._calls())
        names = [(s.request.call_type, s.arm.name) for s in samples]
        assert len(names) == len(set(names)) == 8


class TestIdentity:
    def test_should_flag_each_request_when_its_tokens_differ_from_round_1(self) -> None:
        requests = [_request(VIDEO, "plan"), _request(VIDEO, "memory"), _request(_EQA, "plan")]
        tokens = identity_rows(requests, {})[0]["cl100k_now"]
        round1 = {
            requests[0].request_id: {"prompt_tokens_cl100k": tokens},
            requests[1].request_id: {"prompt_tokens_cl100k": tokens + 1},
        }
        assert [r["equal"] for r in identity_rows(requests, round1)] == [True, False, None]


class TestEstimate:
    _CALL = ScheduledCall(
        PlannedCall(_request(VIDEO, "plan"), Arm("B-high", SONNET_55, "high"), 0), False
    )
    _ESTIMATE = CallEstimate("r", "plan", "B-high", SONNET_55, 20_000, 1_000, cost_usd=0.05)

    def _measured(self, effort: str, *, error: str | None = None) -> Any:
        row = {
            "request_id": self._CALL.call.request.request_id,
            "model": SONNET_55,
            "effort": effort,
            "input_tokens": 20_000,
            "output_tokens": 500,
            "error": error,
        }
        return measured_sizes([row])

    def test_should_keep_optional_calls_apart_when_the_totals_are_split(self) -> None:
        optional = ScheduledCall(self._CALL.call, True)
        totals = split_totals([self._CALL, optional], [self._ESTIMATE, self._ESTIMATE])
        assert sorted(totals) == [(False, "plan", "B-high"), (True, "plan", "B-high")]

    def test_should_use_the_measured_answer_when_round_1_sent_the_same_call(self) -> None:
        cost, how = calibrated_cost(self._CALL, self._ESTIMATE, self._measured("high"))
        assert (how, cost < self._ESTIMATE.cost_usd) == ("exact", True)

    def test_should_use_another_efforts_answer_when_round_1_never_sent_this_effort(self) -> None:
        assert calibrated_cost(self._CALL, self._ESTIMATE, self._measured("medium"))[1] == "proxy"

    def test_should_keep_the_estimate_when_round_1_only_has_an_errored_row(self) -> None:
        measured = self._measured("high", error="Timeout")
        assert calibrated_cost(self._CALL, self._ESTIMATE, measured) == (0.05, "none")


class TestWire:
    def test_should_send_no_param_a_model_rejects_when_every_round_2_arm_goes_out(
        self, captured: list[AbRequest]
    ) -> None:
        rows = wire_check(first_calls(schedule(captured)))
        assert [(r["call_type"], r["arm"]) for r in rows if r["problems"] or r["error"]] == []


def _eqa_data(probe: str = _PROBE, content_format: str = "commentary") -> dict[str, Any]:
    description = load_prompt("description_analysis").replace("{description}", _DESCRIPTION)
    segments = [
        {"text": "סוכן הAI שלנו", "startMs": 0, "endMs": 2_000},
        {"text": "LangChain ו LangGraph", "startMs": 21_000, "endMs": 24_000},
    ]
    run = {"id": "6acd", "title": "מי הם LangChain", "channel": "Code Cave", "duration": 598}
    return {
        "video_id": _EQA,
        "run": {**run, "contentFormat": content_format, "sourceLanguage": "he"},
        "segments": segments,
        "trace": {
            "trace_id": "t1",
            "probe_output": probe,
            "description_request": [{"role": "user", "content": description}],
            "plan_usage": {"output": 833},
            "plan_metadata": {"cacheReadTokens": 16290},
        },
    }


class TestEqaPlan:
    def test_should_build_the_plan_on_round_1s_model_when_the_run_inputs_are_read(self) -> None:
        kwargs = eqa_plan_request(_eqa_data()).kwargs
        video = kwargs["messages"][0]["content"][-1]["text"]
        assert (kwargs["model"], "thinking" in kwargs) == (SONNET_46, False)
        assert "Hint: domain=tech format=commentary has_visual_demo=false confidence=0.92" in video
        assert "02:09 chains" in video

    def test_should_refuse_the_build_when_the_probe_disagrees_with_the_runs_format(self) -> None:
        with pytest.raises(ValueError, match="content format"):
            eqa_plan_request(_eqa_data(content_format="tutorial"))

    def test_should_refuse_the_build_when_the_playbook_came_from_the_youtube_category(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="YouTube category"):
            eqa_plan_request(_eqa_data(probe=_PROBE.replace("0.92", "0.5")))

    def test_should_cut_out_the_description_when_the_request_follows_the_template(self) -> None:
        assert description_of(_eqa_data()["trace"]["description_request"]) == _DESCRIPTION

    def test_should_be_faithful_when_the_trace_kept_a_truncated_static_half(self) -> None:
        data = _eqa_data()
        request = eqa_plan_request(data)
        traced = copy.deepcopy(request.kwargs["messages"])
        static = traced[0]["content"][0]
        static["text"] = static["text"][:5_000] + "\n\n[TRUNCATED]"
        data["trace"]["plan_request"] = traced
        assert trace_check(request, data)["faithful"] is True

    def test_should_not_be_faithful_when_the_video_half_differs(self) -> None:
        data = _eqa_data()
        request = eqa_plan_request(data)
        traced = copy.deepcopy(request.kwargs["messages"])
        traced[0]["content"][-1]["text"] += " "
        data["trace"]["plan_request"] = traced
        assert trace_check(request, data)["faithful"] is False


class TestMatchTraced:
    def test_should_match_when_langfuse_redacted_a_span(self) -> None:
        found = match_traced("overhaul 2026-05-28 done", "overhaul [REDACTED] done")
        assert (found["matches"], found["redacted"]) == (True, ["2026-05-28"])

    def test_should_not_match_when_a_kept_char_differs(self) -> None:
        assert match_traced("abc", "abd")["matches"] is False


class TestMedian:
    def test_should_average_the_two_middle_values_when_the_count_is_even(self) -> None:
        assert true_median([4.0, 1.0, 3.0, 2.0]) == 2.5

    def test_should_report_zero_when_there_are_no_values(self) -> None:
        assert true_median([]) == 0.0
