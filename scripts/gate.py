#!/usr/bin/env python3
"""Golden-eval gate: fail when a primary metric regresses beyond its noise.

Compares one eval run summary (``reports/eval-<ts>.json`` from
``run_eval.py``) with the noise file (``run_eval.py --noise-runs 2``):

* Each primary metric (``quality``, ``faithfulness`` higher-is-better;
  ``duplicateRate`` lower-is-better) is averaged over the videos present in
  BOTH files (paired), and fails when it moves the wrong way by more than its
  tolerance (``tolerance_for``: a one-sided 5 % t-test of the run's paired
  mean against the baseline mean, using the per-video run-to-run sd from the
  noise file; floored at ``MIN_TOLERANCE``). The floor keeps a metric whose
  baseline passes happened to agree exactly from gating on rounding-level
  changes.
* A missing score is "not scored", never a regression: a video with no
  value for a metric in the run (e.g. no Langfuse trace → no faithfulness)
  or none in the baseline is listed as not scored and left out of that
  metric's comparison. A video whose pipeline errored is not scored either —
  its failed ``completed`` assertion is what fails the gate.
* Any per-video assertion that failed and is not marked ``xfail`` fails. The
  ``xfail`` markers are read from ``videos.yaml`` (``--dataset``), so marking
  a known failure there needs no re-run; XFAIL / XPASS are printed, never
  gating.
* Only live golden ids count: report and noise rows of an entry that is now
  disabled or removed (a dead link) are ignored.
* The report must be a fresh (``bypassCache``) run against the same API as
  the noise file, or the comparison is meaningless (exit 2).

Usage::

    python3 scripts/gate.py --report reports/eval-20261008-031500.json
    python3 scripts/gate.py --report <json> --noise dev/golden-dataset/noise.json --allow-subset

Exit codes: 0 pass, 1 regression or assertion failure, 2 unusable input.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _eval_assertions import AssertionResult, apply_markers
from _eval_metrics import PRIMARY_METRICS
from _eval_noise import (
    DEFAULT_NOISE_PATH,
    NOISE_SCHEMA_VERSION,
    live_only,
    load_json,
    summary_api_label,
)
from _eval_schema import DATASET_PATH, live_records, parse_assertions, read_dataset

# Tolerance derivation (``tolerance_for``). Model a per-video metric as
# x_ip = μ_i + ε_ip, ε ~ N(0, s²) independent across videos i and passes p.
# The noise file pools the N baseline passes over n videos into
# ŝ² = ΣΣ(x_ip − x̄_i)² / df with df = n·(N−1) (ŝ²·df/s² ~ χ²_df), and the
# baseline of video i is x̄_i. A fresh run of an UNCHANGED pipeline, averaged
# over the m videos it shares with the baseline, then differs from the
# baseline mean by D = mean_i(y_i − x̄_i) ~ N(0, s²·(1 + 1/N)/m), independent
# of ŝ (residuals ⟂ means). So D / (ŝ·√((1 + 1/N)/m)) ~ t_df and
#     tolerance = t_{1−α, df} · ŝ · √((1 + 1/N) / m)
# false-fails one metric with probability α = 0.05 (``t_quantile_95``). Full
# golden set (n = m = 18, N = 2, df = 18): t = 1.734 → tolerance ≈ 0.50·ŝ
# ≈ 2.12·σ̂_mean, where
# σ̂_mean = ŝ/√n is the sd of one pass's set mean. A subset run (m < n) uses
# the same ŝ with its own m — the √(n/m) widening falls out of the formula.
# Caveat: a shift shared by every video in a pass (provider drift) is only
# partly in ŝ; MIN_TOLERANCE keeps a lucky-quiet baseline from over-tightening.

# One-sided 95 % Student-t quantiles, df 1–30; ``t_quantile_95`` extends it.
_T95: tuple[float, ...] = (
    6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
    1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
    1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697,
)  # fmt: skip
_Z95 = 1.6448536

MIN_TOLERANCE: dict[str, float] = {
    "quality": 0.01,
    "faithfulness": 0.02,
    "duplicateRate": 0.01,
}


class GateInputError(ValueError):
    """The report and noise file cannot be compared."""


@dataclass(frozen=True)
class MetricCheck:
    metric: str
    direction: str
    baseline: float | None
    current: float | None
    tolerance: float
    paired: int
    # Videos with no value in the run or none in the baseline: "not scored",
    # left out of the comparison — never a regression on their own.
    not_scored: tuple[str, ...] = ()

    @property
    def gated(self) -> bool:
        return self.baseline is not None and self.current is not None

    @property
    def regressed(self) -> bool:
        if self.baseline is None or self.current is None:
            return False
        if self.direction == "higher":
            return self.current < self.baseline - self.tolerance
        return self.current > self.baseline + self.tolerance

    def describe(self) -> str:
        unscored = f"; not scored: {', '.join(self.not_scored)}" if self.not_scored else ""
        if not self.gated:
            return f"{self.metric}: not gated (no video scored in both run and baseline){unscored}"
        verdict = "REGRESSED" if self.regressed else "ok"
        return (
            f"{self.metric}: {self.current:.4f} vs baseline {self.baseline:.4f} "
            f"(±{self.tolerance:.4f}, {self.direction} is better, n={self.paired}) "
            f"{verdict}{unscored}"
        )


@dataclass(frozen=True)
class GateResult:
    checks: list[MetricCheck]
    assertion_failures: list[str] = field(default_factory=list)
    # Known failures (XFAIL) and marked checks that now pass (XPASS) — reported only.
    xfails: list[str] = field(default_factory=list)
    xpasses: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)  # report rows of retired ids

    @property
    def passed(self) -> bool:
        return not self.assertion_failures and not any(c.regressed for c in self.checks)


# ─── Comparison ────────────────────────────────────────────────────────
def _report_videos(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["id"]: v for v in report.get("videos") or []}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def t_quantile_95(df: int) -> float:
    """One-sided 95 % Student-t quantile (α = 0.05).

    Table for df ≤ 30; beyond, the Cornish–Fisher expansion around z (error
    < 1e-4 there) — no scipy on the gate's CI runner.
    """
    if df < 1:
        raise ValueError(f"t quantile needs df >= 1, got {df}")
    if df <= len(_T95):
        return _T95[df - 1]
    z = _Z95
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
    )


def tolerance_for(metric: str, noise: dict[str, Any], paired: int) -> float:
    """t_{0.95, df} · ŝ · √((1 + 1/N) / paired), floored at ``MIN_TOLERANCE``.

    See the derivation above ``_T95``. No per-video estimate (no video had
    the metric in every pass) or nothing paired → the floor alone.
    """
    estimate = (noise.get("noise") or {}).get(metric)
    floor = MIN_TOLERANCE[metric]
    if not estimate or paired < 1:
        return floor
    spread = estimate["sd"] * math.sqrt((1 + 1 / estimate["passes"]) / paired)
    return max(t_quantile_95(int(estimate["df"])) * spread, floor)


def check_metric(report: dict[str, Any], noise: dict[str, Any], metric: str) -> MetricCheck:
    """Paired comparison of one metric over the videos both files scored.

    A report video absent from the baseline, without a baseline value, or
    without a value in this run is "not scored"; a baseline video absent
    from the report (``--allow-subset``) is simply not compared.
    """
    current_by_id = _report_videos(report)
    baseline_by_id = noise.get("videos") or {}
    base_pairs: list[float] = []
    current_pairs: list[float] = []
    not_scored: list[str] = []
    for vid, video in sorted(current_by_id.items()):
        base = (baseline_by_id.get(vid) or {}).get(metric)
        current = (video.get("metrics") or {}).get(metric)
        if base is None or current is None:
            not_scored.append(vid)
            continue
        base_pairs.append(base)
        current_pairs.append(current)
    return MetricCheck(
        metric=metric,
        direction=PRIMARY_METRICS[metric],
        baseline=_mean(base_pairs),
        current=_mean(current_pairs),
        tolerance=tolerance_for(metric, noise, len(current_pairs)),
        paired=len(current_pairs),
        not_scored=tuple(not_scored),
    )


def _results(report: dict[str, Any]) -> list[tuple[str, AssertionResult]]:
    return [
        (video["id"], AssertionResult(**a))
        for video in report.get("videos") or []
        for a in video.get("assertions") or []
    ]


def _line(vid: str, result: AssertionResult) -> str:
    marker = f" ({result.marker})" if result.marker else ""
    return f"{vid}: {result.type} — {result.detail}{marker}"


def assertion_failures(report: dict[str, Any]) -> list[str]:
    """Gating assertion failures: failed and not marked xfail."""
    return [_line(vid, r) for vid, r in _results(report) if r.gating_failure]


def _labelled(report: dict[str, Any], label: str) -> list[str]:
    return [_line(vid, r) for vid, r in _results(report) if r.label == label]


def live_view(
    report: dict[str, Any], noise: dict[str, Any], records: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Report + noise restricted to the live golden ids, xfail markers from the dataset.

    Returns the two views and the report ids that were dropped (retired).
    """
    live = live_records(records)
    rows = []
    for video in live_only(report, set(live)).get("videos") or []:
        stored = [AssertionResult(**a) for a in video.get("assertions") or []]
        remarked = apply_markers(stored, parse_assertions(live[video["id"]]))
        rows.append({**video, "assertions": [vars(r) for r in remarked]})
    ignored = sorted(v["id"] for v in report.get("videos") or [] if v["id"] not in live)
    baseline = {k: v for k, v in (noise.get("videos") or {}).items() if k in live}
    return {**report, "videos": rows}, {**noise, "videos": baseline}, ignored


def validate_inputs(report: dict[str, Any], noise: dict[str, Any], allow_subset: bool) -> None:
    """Raise ``GateInputError`` when the two files cannot be compared."""
    if report.get("dryRun"):
        raise GateInputError("report is a --dry-run (stub output); nothing to gate")
    if noise.get("schemaVersion") != NOISE_SCHEMA_VERSION:
        raise GateInputError(
            f"noise schemaVersion {noise.get('schemaVersion')!r} unsupported — rebuild it "
            "for $0: run_eval.py --noise-from <eval-…-r1.json> <eval-…-r2.json>"
        )
    if not report.get("videos"):
        raise GateInputError("report has no live golden video to gate (check --dataset)")
    if report.get("bypassCache") is not True:
        raise GateInputError("report is not a bypassCache run (it scored stored output)")
    report_api, noise_api = summary_api_label(report), summary_api_label(noise)
    if report_api != noise_api:
        raise GateInputError(f"report ran against {report_api!r}, noise against {noise_api!r}")
    missing = sorted(set(noise.get("videos") or {}) - set(_report_videos(report)))
    if missing and not allow_subset:
        raise GateInputError(
            f"report lacks {len(missing)} baseline video(s): {', '.join(missing)} "
            "(pass --allow-subset to gate on the overlap)"
        )


def evaluate(
    report: dict[str, Any],
    noise: dict[str, Any],
    *,
    allow_subset: bool = False,
    records: list[dict[str, Any]] | None = None,
) -> GateResult:
    """Gate ``report`` against ``noise``; ``records`` (videos.yaml) scopes it to live ids."""
    ignored: list[str] = []
    if records is not None:
        report, noise, ignored = live_view(report, noise, records)
    validate_inputs(report, noise, allow_subset)
    return GateResult(
        checks=[check_metric(report, noise, m) for m in PRIMARY_METRICS],
        assertion_failures=assertion_failures(report),
        xfails=_labelled(report, "XFAIL"),
        xpasses=_labelled(report, "XPASS"),
        ignored=ignored,
    )


# ─── CLI ───────────────────────────────────────────────────────────────
def _print_result(result: GateResult) -> None:
    if result.ignored:
        print(f"ignored (no longer live): {', '.join(result.ignored)}")
    for check in result.checks:
        print(check.describe())
    for line in result.xfails:
        print(f"assertion XFAIL {line}")
    for line in result.xpasses:
        print(f"assertion XPASS {line} — drop the xfail marker")
    for failure in result.assertion_failures:
        print(f"assertion FAILED {failure}")
    print("GATE PASS" if result.passed else "GATE FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--report", required=True, help="eval-<ts>.json from run_eval.py")
    parser.add_argument("--noise", default=str(DEFAULT_NOISE_PATH))
    parser.add_argument("--allow-subset", action="store_true")
    parser.add_argument(
        "--dataset", default=str(DATASET_PATH), help="videos.yaml: live ids + xfail markers"
    )
    args = parser.parse_args(argv)
    try:
        result = evaluate(
            load_json(Path(args.report)),
            load_json(Path(args.noise)),
            allow_subset=args.allow_subset,
            records=read_dataset(Path(args.dataset)),
        )
    except (OSError, ValueError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 2
    _print_result(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
