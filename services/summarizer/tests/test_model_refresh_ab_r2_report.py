"""Unit tests for the model A/B round 2 report pieces (scripts/_model_ab_r2_*, stack cost; 4.0d).

Reviewer clusters are hand-made (presence, merges, duplicates, the verdicts
per arm, the keep-the-rule check, the named check); the downstream check runs
the T1dQhQAm8Tc cassette's recorded plan, with duplicate tabs added, through
the real plan → reconcile → grouping code; the stack cost re-prices hand-made
ledger calls. No network, no LLM, no spend.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.replay.cassette import load_cassette
from tests.replay.group_synth import recorded_answer

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import model_refresh_ab  # noqa: E402
from _model_ab_capture import capture_cassettes  # noqa: E402
from _model_ab_r2_clusters import (  # noqa: E402
    Cassette,
    Cluster,
    arm_verdicts,
    item_counts,
    keep_rule,
    named_check,
    presence,
    run_rows,
)
from _model_ab_r2_downstream import downstream  # noqa: E402
from _model_ab_stack_cost import Move, ratio, reprice, stack_table  # noqa: E402
from _model_ab_types import GPT_4O_MINI, HAIKU_45, SONNET_46, SONNET_55, AbRequest  # noqa: E402

F = "food.steps"


def _ids(output: str, *numbers: int) -> tuple[str, ...]:
    return tuple(f"{output}.{F}.{n}" for n in numbers)


def _cassette(extra: list[Cluster] | None = None) -> Cassette:
    clusters = [
        Cluster(F, "brown", "supported", _ids("A.0", 0) + _ids("A.1", 0) + _ids("A+rule.0", 0)),
        Cluster(F, "simmer", "supported", _ids("A+rule.1", 0) + _ids("B+rule.0", 0)),
        Cluster(F, "preheat-oven-375", "padding", _ids("A.0", 1)),
        Cluster(
            F,
            "bake-uncovered-425-crisp-top",
            "supported",
            _ids("A+rule.0", 1, 2),
            {_ids("A+rule.0", 1)[0]: ("simmer",)},
        ),
        *(extra or []),
    ]
    outputs = ["A.0", "A.1", "A+rule.0", "A+rule.1", "B+rule.0", "B+rule.1"]
    return Cassette("jMq8lEu-of0", clusters, outputs)


class TestClusters:
    def test_should_count_a_merged_cluster_as_present_when_an_item_also_covers_it(self) -> None:
        covered = presence(_cassette().clusters)["A+rule.0"]
        assert (covered[(F, "brown")], covered[(F, "simmer")]) == ("direct", "merged")

    def test_should_count_duplicates_when_one_output_has_two_items_in_a_cluster(self) -> None:
        counts = item_counts(_cassette().clusters, "A+rule.0")
        assert (counts["items"], counts["duplicates"]) == (3, 1)

    def test_should_list_what_a_run_lacks_when_compared_with_a_rules_union(self) -> None:
        rows = {r["output"]: r for r in run_rows(_cassette())}
        assert rows["B+rule.0"]["lacks_vs_ref"] == [
            f"{F}:bake-uncovered-425-crisp-top",
            f"{F}:brown",
        ]

    def test_should_report_an_empty_output_when_it_answered_nothing(self) -> None:
        rows = {r["output"]: r for r in run_rows(_cassette())}
        assert (rows["B+rule.1"]["supported_clusters"], rows["B+rule.1"]["items"]) == (0, 0)

    def test_should_flag_the_invented_oven_steps_when_an_output_carries_them(self) -> None:
        rows = {r["output"]: r for r in named_check(_cassette())}
        assert (rows["A.0"]["invented"], rows["A+rule.0"]["real_425_crisp"]) == (
            ["preheat-oven-375 (padding)"],
            "direct",
        )

    def test_should_fail_an_arm_when_a_run_lacks_a_supported_reference_cluster(self) -> None:
        metrics = {"A+rule": {"filled_share": 0.9, "defaulted": 0.0}}
        metrics["B+rule"] = {"filled_share": 1.0, "defaulted": 0.0}
        verdicts = arm_verdicts(run_rows(_cassette()), metrics)
        assert (verdicts["B+rule"]["lacks_none"], verdicts["B+rule"]["pass"]) == (False, False)


def _sets(runs: dict[str, set[str]]) -> Cassette:
    """A cassette whose outputs cover exactly ``runs``' clusters (all supported)."""
    slugs = sorted(set().union(*runs.values()))
    clusters = [
        Cluster(
            F,
            slug,
            "supported",
            tuple(f"{o}.{F}.{slug}" for o, have in runs.items() if slug in have),
        )
        for slug in slugs
    ]
    return Cassette("v", clusters, sorted(runs))


class TestKeepRule:
    _PLAIN = {"A.0": {"a", "b", "c", "d"}, "A.1": {"a", "b", "c"}}  # run-to-run 1 - 3/4 = 0.25

    def test_should_not_be_within_noise_when_the_rule_moves_more_than_the_runs_differ(
        self,
    ) -> None:
        # A+rule ×2 = {a, b}: vs A.0 0.5, vs A.1 1/3 → mean ≈ 0.417 > 0.25.
        check = keep_rule(_sets({**self._PLAIN, "A+rule.0": {"a", "b"}, "A+rule.1": {"a", "b"}}))
        assert check["rule_distance"] == pytest.approx(0.4165, abs=0.001)
        assert (check["run_to_run"], check["within_noise"]) == ({"A": 0.25, "A+rule": 0.0}, False)

    def test_should_be_within_noise_when_the_rule_moves_less_than_the_runs_differ(self) -> None:
        # A+rule ×2 = {a, b, c, d}: vs A.0 0, vs A.1 0.25 → mean 0.125 ≤ 0.25.
        ruled = {"a", "b", "c", "d"}
        check = keep_rule(_sets({**self._PLAIN, "A+rule.0": ruled, "A+rule.1": set(ruled)}))
        assert (check["rule_distance"], check["within_noise"]) == (0.125, True)

    def test_should_leave_an_arms_noise_unknown_when_it_has_one_run(self) -> None:
        check = keep_rule(_sets({**self._PLAIN, "A+rule.0": {"a", "b"}}))
        assert check["run_to_run"] == {"A": 0.25, "A+rule": None}


class TestArmGates:
    def _verdicts(self, filled: float, defaulted: float) -> dict[str, Any]:
        runs = run_rows(_sets({"A+rule.0": {"a"}, "A+rule.1": {"a"}, "C+rule.0": {"a"}}))
        metrics = {
            "A+rule": {"filled_share": 0.9, "defaulted": 0.1},
            "C+rule": {"filled_share": filled, "defaulted": defaulted},
        }
        return arm_verdicts(runs, metrics)["C+rule"]

    def test_should_pass_an_arm_when_it_lacks_nothing_and_meets_both_gates(self) -> None:
        assert self._verdicts(0.9, 0.1)["pass"] is True

    def test_should_fail_an_arm_when_its_filled_share_is_below_the_reference(self) -> None:
        assert self._verdicts(0.89, 0.0)["pass"] is False

    def test_should_fail_an_arm_when_it_defaults_more_than_the_reference(self) -> None:
        assert self._verdicts(1.0, 0.2)["pass"] is False


@pytest.fixture(scope="module")
def plan_request() -> AbRequest:
    return next(r for r in capture_cassettes(("T1dQhQAm8Tc",)) if r.call_type == "plan")


def _plan_with(extra_tabs: list[dict[str, Any]]) -> str:
    answer = recorded_answer(
        next(e for e in load_cassette("T1dQhQAm8Tc").llm if e.key.span == "plan")
    )
    answer = copy.deepcopy(answer)
    answer["tabs"] = [*answer["tabs"][:4], *extra_tabs]
    return json.dumps(answer)


class TestDownstream:
    def _duplicate(self, plan_request: AbRequest, component: str) -> dict[str, Any]:
        source = json.loads(_plan_with([]))["tabs"][1]
        tab = {**source, "id": "second_view", "label": "Second view", "component": component}
        return downstream(_plan_with([tab]), plan_request.context, None)

    def test_should_drop_the_second_tab_when_it_repeats_source_and_component(
        self, plan_request: AbRequest
    ) -> None:
        source = json.loads(_plan_with([]))["tabs"][1]
        result = self._duplicate(plan_request, source["component"])
        assert "1 dropped at plan-done" in next(iter(result["outcomes"].values()))

    def test_should_add_no_group_call_when_a_second_tab_reads_the_same_source(
        self, plan_request: AbRequest
    ) -> None:
        result = self._duplicate(plan_request, "info_grid")
        assert (result["extra_group_calls"], result["same_fields_as_deduped"]) == (0, True)


class TestStackCost:
    _CALL = {"in": 10_000, "out": 1_000, "cr": 6_000, "cw": 2_000, "cw1h": 0, "cw5m": 2_000}

    def test_should_price_each_token_kind_at_its_rate_when_a_call_is_repriced(self) -> None:
        # Haiku 4.5: $1 in, $5 out, read 0.1x, 5-minute write 1.25x (per MTok).
        expected = (2_000 * 1 + 6_000 * 0.1 + 2_000 * 1.25 + 1_000 * 5) / 1e6
        assert reprice(self._CALL, HAIKU_45, 1.0, 1.0) == pytest.approx(expected)

    def test_should_scale_the_prompt_and_answer_when_the_ratios_are_not_one(self) -> None:
        base = reprice(self._CALL, HAIKU_45, 1.0, 1.0)
        assert reprice(self._CALL, HAIKU_45, 2.0, 2.0) == pytest.approx(2 * base)

    def test_should_use_only_requests_both_arms_answered_when_a_ratio_is_measured(self) -> None:
        rows = [
            {"call_type": "plan", "arm": "A", "request_id": "r1", "input_tokens": 100},
            {"call_type": "plan", "arm": "B", "request_id": "r1", "input_tokens": 130},
            {"call_type": "plan", "arm": "A", "request_id": "r2", "input_tokens": 999},
        ]
        assert ratio(rows, "plan", Move(HAIKU_45, "A", "B"), "input_tokens") == pytest.approx(1.3)


class TestStackTable:
    _PLAN = {
        "span": "plan",
        "model": SONNET_46,
        "in": 20_000,
        "out": 900,
        "cr": 16_000,
        "cw": 0,
        "cw1h": 0,
        "cw5m": 0,
    }
    _SYNTHESIS = {
        "span": "synthesis",
        "model": GPT_4O_MINI,
        "in": 2_000,
        "out": 200,
        "cr": 0,
        "cw": 0,
        "cw1h": 0,
        "cw5m": 0,
    }
    _ROWS = [
        {
            "call_type": "plan",
            "arm": "A",
            "request_id": "r1",
            "input_tokens": 100,
            "output_tokens": 10,
        },
        {
            "call_type": "plan",
            "arm": "B-medium",
            "request_id": "r1",
            "input_tokens": 130,
            "output_tokens": 20,
        },
    ]

    def _table(self, tmp_path: Path, plan_usd_off: float = 0.0) -> dict[str, Any]:
        calls = [dict(self._PLAN), dict(self._SYNTHESIS)]
        for call in calls:
            call["usd"] = reprice(call, str(call["model"]), 1.0, 1.0)
        calls[0]["usd"] += plan_usd_off
        run = {"ytid": "T1dQhQAm8Tc", "label": "r2", "doc": {"llm": calls}}
        path = tmp_path / "bench.json"
        path.write_text(json.dumps({"runs": [run]}), encoding="utf-8")
        return stack_table(path, self._ROWS)

    def test_should_reprice_a_moved_call_on_the_new_model_with_the_measured_ratios(
        self, tmp_path: Path
    ) -> None:
        plan = self._table(tmp_path)["videos"]["T1dQhQAm8Tc r2"]["plan"]
        assert plan["stack 1"] == pytest.approx(reprice(self._PLAN, SONNET_55, 1.3, 2.0))

    def test_should_keep_todays_cost_when_a_call_type_does_not_move(self, tmp_path: Path) -> None:
        synthesis = self._table(tmp_path)["videos"]["T1dQhQAm8Tc r2"]["synthesis"]
        assert synthesis["stack 2"] == pytest.approx(synthesis["today"])

    def test_should_total_every_call_type_when_a_video_is_summed(self, tmp_path: Path) -> None:
        table = self._table(tmp_path)
        rows = table["videos"]["T1dQhQAm8Tc r2"].values()
        assert table["totals"]["T1dQhQAm8Tc r2"]["stack 1"] == pytest.approx(
            sum(r["stack 1"] for r in rows), abs=1e-6
        )

    def test_should_report_the_gap_when_the_ledger_disagrees_with_the_reprice(
        self, tmp_path: Path
    ) -> None:
        assert self._table(tmp_path, plan_usd_off=0.001)["max_reprice_diff"] == pytest.approx(0.001)


class TestReportRoundTrip:
    def test_should_keep_round_2_when_round_1_is_rescored(self, tmp_path: Path) -> None:
        args = argparse.Namespace(out_json=tmp_path / "ab.json", out_md=tmp_path / "ab.md")
        projection = {"chunk_cl100k": 1, "total_cl100k": 2, "haiku55_tokens": 3}
        meta = {
            "run_at": "2026-10-13T00:00:00+00:00",
            "estimate_usd": 0.1,
            "ceiling_usd": 1.5,
            "arms": {},
            "requests": {},
            "longest_chunk_projection": projection | {"price_tier_tokens": 4},
            "round2": {"markdown": "## Round 2 — test\n"},
        }
        model_refresh_ab._finish(args, meta, [])
        argv = ["--rescore", "--out-json", str(args.out_json), "--out-md", str(args.out_md)]
        assert model_refresh_ab.main(argv) == 0
        assert args.out_md.read_text(encoding="utf-8").rstrip().endswith("## Round 2 — test")
