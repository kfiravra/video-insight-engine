"""Tests for the golden-eval noise file (``scripts/_eval_noise.py``).

Synthetic run summaries (helpers shared with ``test_eval_gate``) stand in for
real ``reports/eval-*.json`` passes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from _eval_noise import build_noise, layout_jaccard  # noqa: E402

from tests.test_eval_gate import _errored, _summary  # noqa: E402


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

    def test_should_leave_a_metric_unscored_in_the_baseline_when_one_pass_lacks_it(self) -> None:
        second = _summary({"quality": 0.04})
        second["videos"][0]["metrics"]["faithfulness"] = None
        noise = build_noise([_summary(), second], ["r1.json", "r2.json"])
        video = noise["videos"]["a"]
        assert (video["faithfulness"], video["quality"], noise["notScored"]["faithfulness"]) == (
            None,
            0.82,
            ["a"],
        )

    def test_should_keep_an_unscored_video_out_of_that_metric_noise(self) -> None:
        second = _summary()
        second["videos"][0]["metrics"]["faithfulness"] = None
        noise = build_noise([_summary(), second], ["r1.json", "r2.json"])
        assert (noise["noise"]["faithfulness"]["n"], noise["noise"]["quality"]["n"]) == (1, 2)

    def test_should_drop_retired_ids_when_building_noise_for_the_live_set(self) -> None:
        noise = build_noise([_summary(), _summary()], ["r1", "r2"], live_ids={"a"})
        assert (sorted(noise["videos"]), noise["retiredVideos"]) == (["a"], ["b"])

    def test_should_label_noise_by_api_when_built_from_legacy_api_url_summaries(self) -> None:
        legacy = [{**_summary(), "apiUrl": "http://localhost:3000"} for _ in range(2)]
        for summary in legacy:
            del summary["apiLabel"]
        assert build_noise(legacy, ["r1", "r2"])["apiLabel"] == "localhost:3000"

    def test_should_score_identical_layouts_as_fully_stable(self) -> None:
        assert layout_jaccard(["overview", "quiz_arena"], ["quiz_arena", "overview"]) == 1.0

    def test_should_score_half_overlap_layouts_as_one_third_stable(self) -> None:
        assert layout_jaccard(["overview", "step_player"], ["overview", "checklist"]) == 1 / 3
