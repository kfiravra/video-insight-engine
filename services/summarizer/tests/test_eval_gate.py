"""Tests for the golden-eval gate (``scripts/gate.py``) and noise file (``_eval_noise``).

Synthetic run summaries stand in for real ``reports/eval-*.json`` files; a
drop beyond the t-test tolerance derived from the per-video noise must fail
the gate, a drop within it must pass, and an unchanged pipeline must
false-fail one metric only ≈ 5 % of the time.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import gate  # noqa: E402
from _eval_noise import build_noise, layout_jaccard  # noqa: E402

_BASE = {"quality": 0.80, "faithfulness": 0.90, "duplicateRate": 0.05}


def _video(vid: str, metrics: dict[str, float | None], **extra: Any) -> dict[str, Any]:
    return {"id": vid, "metrics": metrics, "components": ["overview"], "assertions": [], **extra}


def _summary(
    shift: dict[str, float] | None = None, ids: tuple[str, ...] = ("a", "b")
) -> dict[str, Any]:
    """Run summary whose every video carries ``_BASE`` + ``shift``."""
    metrics = {m: round(v + (shift or {}).get(m, 0.0), 4) for m, v in _BASE.items()}
    return {
        "schemaVersion": 1,
        "dryRun": False,
        "bypassCache": True,
        "apiLabel": "localhost:3000",
        "metrics": metrics,
        "videos": [_video(vid, dict(metrics)) for vid in ids],
    }


def _quality_summary(quality: dict[str, float]) -> dict[str, Any]:
    """Run summary with a per-video quality (other metrics at ``_BASE``)."""
    summary = _summary(ids=tuple(quality))
    for video in summary["videos"]:
        video["metrics"]["quality"] = quality[video["id"]]
    return summary


def _shifted(quality: dict[str, float], delta: float) -> dict[str, float]:
    return {vid: q + delta for vid, q in quality.items()}


# 18 videos (the live golden set size) with spread-out true quality.
_TRUE_QUALITY = {f"v{i:02d}": 0.5 + 0.02 * i for i in range(18)}


def _errored(summary: dict[str, Any], vid: str) -> dict[str, Any]:
    """Mark ``vid`` as a failed pipeline run, the way ``_eval_report`` writes it."""
    for video in summary["videos"]:
        if video["id"] == vid:
            video["error"] = "pipeline failed: boom"
            video["metrics"] = {"quality": None, "faithfulness": None, "duplicateRate": None}
    return summary


@pytest.fixture
def noise() -> dict[str, Any]:
    """Two baseline passes; each video's quality differs by 0.04 between them."""
    return build_noise([_summary(), _summary({"quality": 0.04})], ["r1.json", "r2.json"])


# ─── Noise file ────────────────────────────────────────────────────────
class TestBuildNoise:
    def test_should_record_run_mean_spread_when_two_passes_differ(
        self, noise: dict[str, Any]
    ) -> None:
        assert noise["runMeanSpread"]["quality"] == 0.04

    def test_should_pool_per_video_differences_into_sd_and_df_when_building_noise(
        self, noise: dict[str, Any]
    ) -> None:
        # Two videos, d_i = 0.04 each: s^2 = sum(d^2) / 2n = 0.0008, df = n(N-1) = 2.
        estimate = noise["noise"]["quality"]
        assert (estimate["sd"], estimate["df"], estimate["n"]) == (
            pytest.approx(0.0008**0.5, abs=1e-6),
            2,
            2,
        )

    def test_should_derive_sigma_of_one_pass_mean_when_building_noise(
        self, noise: dict[str, Any]
    ) -> None:
        estimate = noise["noise"]["quality"]
        assert estimate["sigmaMean"] == pytest.approx(estimate["sd"] / 2**0.5, abs=1e-6)

    def test_should_record_mean_of_passes_as_baseline(self, noise: dict[str, Any]) -> None:
        assert noise["baseline"]["quality"] == 0.82

    def test_should_keep_per_video_means_when_building_noise(self, noise: dict[str, Any]) -> None:
        assert noise["videos"]["a"]["quality"] == 0.82

    def test_should_refuse_a_single_summary(self) -> None:
        with pytest.raises(ValueError):
            build_noise([_summary()], ["r1.json"])

    def test_should_exclude_a_video_from_noise_when_it_errored_in_any_pass(self) -> None:
        passes = [_summary(), _errored(_summary({"quality": 0.04}), "b")]
        noise = build_noise(passes, ["r1.json", "r2.json"])
        assert (noise["excludedVideos"], sorted(noise["videos"])) == (["b"], ["a"])

    def test_should_not_inflate_noise_when_a_video_errored_in_one_pass(self) -> None:
        # Old behaviour: the failed run scored quality 0.0 → noise ≈ 0.4, baseline halved.
        passes = [_summary(), _errored(_summary({"quality": 0.04}), "b")]
        noise = build_noise(passes, ["r1.json", "r2.json"])
        assert (noise["runMeanSpread"]["quality"], noise["baseline"]["quality"]) == (0.04, 0.82)

    def test_should_label_noise_by_api_when_built_from_legacy_api_url_summaries(self) -> None:
        legacy = [{**_summary(), "apiUrl": "http://localhost:3000"} for _ in range(2)]
        for summary in legacy:
            del summary["apiLabel"]
        assert build_noise(legacy, ["r1", "r2"])["apiLabel"] == "localhost:3000"

    def test_should_score_identical_layouts_as_fully_stable(self) -> None:
        assert layout_jaccard(["overview", "quiz_arena"], ["quiz_arena", "overview"]) == 1.0

    def test_should_score_half_overlap_layouts_as_one_third_stable(self) -> None:
        assert layout_jaccard(["overview", "step_player"], ["overview", "checklist"]) == 1 / 3


# ─── Gate ──────────────────────────────────────────────────────────────
class TestGate:
    def test_should_fail_when_quality_drops_beyond_the_t_tolerance(
        self, noise: dict[str, Any]
    ) -> None:
        # t(2) · s · sqrt(1.5 / 2) = 2.920 · 0.0283 · 0.866 ≈ 0.072 below 0.82
        assert not gate.evaluate(_summary({"quality": -0.10}), noise).passed

    def test_should_pass_when_unchanged_run_misses_the_mean_by_more_than_one_pass_gap(
        self, noise: dict[str, Any]
    ) -> None:
        # Baseline 0.82, the passes 0.04 apart: a fresh run at 0.76 (0.06 below)
        # is ordinary variance against a 2-pass mean (the 1×|Δ| gate failed it).
        assert gate.evaluate(_summary({"quality": -0.04}), noise).passed

    def test_should_false_fail_about_five_percent_when_pipeline_is_unchanged(self) -> None:
        rng, trials, fails = random.Random(11), 2_000, 0
        for _ in range(trials):
            x1, x2, fresh = (
                {vid: mu + rng.gauss(0.0, 0.05) for vid, mu in _TRUE_QUALITY.items()}
                for _ in range(3)
            )
            noise = build_noise([_quality_summary(x1), _quality_summary(x2)], ["r1", "r2"])
            fails += gate.check_metric(_quality_summary(fresh), noise, "quality").regressed
        assert 0.03 < fails / trials < 0.07

    def test_should_catch_a_drop_of_two_and_a_half_sigma_of_the_mean(self) -> None:
        # d_i = ±0.05 → s = 0.0707, sigma_mean = s/sqrt(18) ≈ 0.0167, tolerance ≈ 2.12 sigma_mean
        noise = build_noise(
            [
                _quality_summary(_shifted(_TRUE_QUALITY, 0.05)),
                _quality_summary(_shifted(_TRUE_QUALITY, -0.05)),
            ],
            ["r1", "r2"],
        )
        drop = 2.5 * noise["noise"]["quality"]["sigmaMean"]
        report = _quality_summary(_shifted(_TRUE_QUALITY, -drop))
        assert gate.check_metric(report, noise, "quality").regressed

    def test_should_pass_a_drop_of_one_and_a_half_sigma_of_the_mean(self) -> None:
        noise = build_noise(
            [
                _quality_summary(_shifted(_TRUE_QUALITY, 0.05)),
                _quality_summary(_shifted(_TRUE_QUALITY, -0.05)),
            ],
            ["r1", "r2"],
        )
        drop = 1.5 * noise["noise"]["quality"]["sigmaMean"]
        report = _quality_summary(_shifted(_TRUE_QUALITY, -drop))
        assert not gate.check_metric(report, noise, "quality").regressed

    def test_should_widen_tolerance_by_root_of_set_ratio_when_gating_a_subset(self) -> None:
        ids = ("a", "b", "c", "d")
        noise = build_noise([_summary(ids=ids), _summary({"quality": 0.04}, ids)], ["r1", "r2"])
        full = gate.tolerance_for("quality", noise, paired=4)
        assert gate.tolerance_for("quality", noise, paired=1) == pytest.approx(2 * full)

    def test_should_use_the_t_quantile_for_the_noise_df(self) -> None:
        assert [round(gate.t_quantile_95(df), 3) for df in (1, 17, 18, 40)] == [
            6.314,
            1.740,
            1.734,
            1.684,
        ]

    def test_should_refuse_a_v1_noise_file_and_point_at_noise_from(
        self, noise: dict[str, Any]
    ) -> None:
        with pytest.raises(gate.GateInputError, match="--noise-from"):
            gate.evaluate(_summary(), {**noise, "schemaVersion": 1})

    def test_should_not_count_an_errored_video_as_a_missing_metric(
        self, noise: dict[str, Any]
    ) -> None:
        check = gate.check_metric(_errored(_summary(), "a"), noise, "faithfulness")
        assert (check.missing, check.paired) == ((), 1)

    def test_should_pass_when_quality_drop_is_within_noise(self, noise: dict[str, Any]) -> None:
        assert gate.evaluate(_summary({"quality": -0.01}), noise).passed

    def test_should_fail_when_duplicate_rate_rises_beyond_tolerance(
        self, noise: dict[str, Any]
    ) -> None:
        result = gate.evaluate(_summary({"duplicateRate": 0.05}), noise)
        assert [c.metric for c in result.checks if c.regressed] == ["duplicateRate"]

    def test_should_pass_when_duplicate_rate_falls(self, noise: dict[str, Any]) -> None:
        assert gate.evaluate(_summary({"duplicateRate": -0.04}), noise).passed

    def test_should_apply_tolerance_floor_when_measured_noise_is_zero(
        self, noise: dict[str, Any]
    ) -> None:
        # Both passes agreed exactly on faithfulness (noise 0.0); a 0.01 dip is
        # inside the 0.02 floor.
        assert gate.evaluate(_summary({"faithfulness": -0.01}), noise).passed

    def test_should_fail_when_faithfulness_is_unavailable_for_a_baseline_video(
        self, noise: dict[str, Any]
    ) -> None:
        report = _summary()
        report["videos"][0]["metrics"]["faithfulness"] = None
        check = gate.check_metric(report, noise, "faithfulness")
        assert check.regressed and check.missing == ("a",)

    def test_should_fail_when_a_non_xfail_assertion_failed(self, noise: dict[str, Any]) -> None:
        report = _summary()
        report["videos"][0]["assertions"] = [
            {"type": "quizAbsentOrLast", "passed": False, "detail": "quiz at 0", "xfail": None}
        ]
        assert gate.evaluate(report, noise).assertion_failures == [
            "a: quizAbsentOrLast — quiz at 0"
        ]

    def test_should_ignore_failed_assertion_when_marked_xfail(self, noise: dict[str, Any]) -> None:
        report = _summary()
        report["videos"][0]["assertions"] = [
            {"type": "forbiddenComponents", "passed": False, "detail": "", "xfail": "C19"}
        ]
        assert gate.evaluate(report, noise).passed

    def test_should_refuse_a_dry_run_report(self, noise: dict[str, Any]) -> None:
        report = {**_summary(), "dryRun": True}
        with pytest.raises(gate.GateInputError, match="dry-run"):
            gate.evaluate(report, noise)

    def test_should_refuse_a_report_when_it_did_not_bypass_the_cache(
        self, noise: dict[str, Any]
    ) -> None:
        with pytest.raises(gate.GateInputError, match="bypassCache"):
            gate.evaluate({**_summary(), "bypassCache": False}, noise)

    def test_should_refuse_a_report_when_it_ran_against_another_api(
        self, noise: dict[str, Any]
    ) -> None:
        with pytest.raises(gate.GateInputError, match="ran against"):
            gate.evaluate({**_summary(), "apiLabel": "remote:0123456789ab"}, noise)

    def test_should_accept_a_legacy_report_when_its_api_url_maps_to_the_noise_label(
        self, noise: dict[str, Any]
    ) -> None:
        report = {**_summary(), "apiUrl": "http://localhost:3000/"}
        del report["apiLabel"]
        assert gate.evaluate(report, noise).passed

    def test_should_refuse_a_subset_report_unless_allowed(self, noise: dict[str, Any]) -> None:
        with pytest.raises(gate.GateInputError, match="lacks 1 baseline video"):
            gate.evaluate(_summary(ids=("a",)), noise)

    def test_should_gate_on_the_overlap_when_subset_is_allowed(self, noise: dict[str, Any]) -> None:
        result = gate.evaluate(_summary(ids=("a",)), noise, allow_subset=True)
        assert result.passed and result.checks[0].paired == 1


class TestGateCli:
    def _write(self, tmp_path: Path, name: str, data: dict[str, Any]) -> str:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_should_exit_one_when_a_synthetic_drop_exceeds_noise(
        self, tmp_path: Path, noise: dict[str, Any]
    ) -> None:
        report = self._write(tmp_path, "eval.json", _summary({"faithfulness": -0.2}))
        noise_path = self._write(tmp_path, "noise.json", noise)
        assert gate.main(["--report", report, "--noise", noise_path]) == 1

    def test_should_exit_zero_when_run_matches_baseline(
        self, tmp_path: Path, noise: dict[str, Any]
    ) -> None:
        report = self._write(tmp_path, "eval.json", _summary({"quality": 0.02}))
        noise_path = self._write(tmp_path, "noise.json", noise)
        assert gate.main(["--report", report, "--noise", noise_path]) == 0

    def test_should_exit_two_when_noise_file_is_missing(self, tmp_path: Path) -> None:
        report = self._write(tmp_path, "eval.json", _summary())
        missing = str(tmp_path / "absent.json")
        assert gate.main(["--report", report, "--noise", missing]) == 2
