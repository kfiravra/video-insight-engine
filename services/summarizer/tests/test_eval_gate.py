"""Tests for the golden-eval gate (``scripts/gate.py``) and noise file (``_eval_noise``).

Synthetic run summaries stand in for real ``reports/eval-*.json`` files; a
drop larger than the measured noise must fail the gate, a drop within it
must pass.
"""

from __future__ import annotations

import json
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
        "apiUrl": "http://localhost:3000",
        "metrics": metrics,
        "videos": [_video(vid, dict(metrics)) for vid in ids],
    }


@pytest.fixture
def noise() -> dict[str, Any]:
    """Two baseline passes whose quality differs by 0.04 (the measured noise)."""
    return build_noise([_summary(), _summary({"quality": 0.04})], ["r1.json", "r2.json"])


# ─── Noise file ────────────────────────────────────────────────────────
class TestBuildNoise:
    def test_should_record_run_spread_as_noise_when_two_passes_differ(
        self, noise: dict[str, Any]
    ) -> None:
        assert noise["noise"]["quality"] == 0.04

    def test_should_record_mean_of_passes_as_baseline(self, noise: dict[str, Any]) -> None:
        assert noise["baseline"]["quality"] == 0.82

    def test_should_keep_per_video_means_when_building_noise(self, noise: dict[str, Any]) -> None:
        assert noise["videos"]["a"]["quality"] == 0.82

    def test_should_refuse_a_single_summary(self) -> None:
        with pytest.raises(ValueError):
            build_noise([_summary()], ["r1.json"])

    def test_should_score_identical_layouts_as_fully_stable(self) -> None:
        assert layout_jaccard(["overview", "quiz_arena"], ["quiz_arena", "overview"]) == 1.0

    def test_should_score_half_overlap_layouts_as_one_third_stable(self) -> None:
        assert layout_jaccard(["overview", "step_player"], ["overview", "checklist"]) == 1 / 3


# ─── Gate ──────────────────────────────────────────────────────────────
class TestGate:
    def test_should_fail_when_quality_drops_more_than_its_noise(
        self, noise: dict[str, Any]
    ) -> None:
        result = gate.evaluate(_summary({"quality": -0.10}), noise)
        assert not result.passed

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
