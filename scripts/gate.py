#!/usr/bin/env python3
"""Golden-eval gate: fail when a primary metric regresses beyond its noise.

Compares one eval run summary (``reports/eval-<ts>.json`` from
``run_eval.py``) with the noise file (``run_eval.py --noise-runs 2``):

* Each primary metric (``quality``, ``faithfulness`` higher-is-better;
  ``duplicateRate`` lower-is-better) is averaged over the videos present in
  BOTH files (paired), and fails when it moves the wrong way by more than its
  tolerance = max(measured noise, ``MIN_TOLERANCE``). The floor keeps a
  metric whose two baseline passes happened to agree exactly from gating on
  rounding-level changes.
* A metric with a baseline but no value in the run (e.g. no Langfuse trace →
  no faithfulness) fails: a gate that silently skips is not a gate.
* Any per-video assertion that failed and is not marked ``xfail`` fails.

Usage::

    python3 scripts/gate.py --report reports/eval-20261008-031500.json
    python3 scripts/gate.py --report <json> --noise dev/golden-dataset/noise.json --allow-subset

Exit codes: 0 pass, 1 regression or assertion failure, 2 unusable input.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _eval_metrics import PRIMARY_METRICS
from _eval_noise import DEFAULT_NOISE_PATH, NOISE_SCHEMA_VERSION, load_json

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
    # Videos with a baseline value but none in this run (e.g. no Langfuse trace).
    missing: tuple[str, ...] = ()

    @property
    def gated(self) -> bool:
        return self.baseline is not None or bool(self.missing)

    @property
    def regressed(self) -> bool:
        if self.missing:
            return True
        if self.baseline is None or self.current is None:
            return False
        if self.direction == "higher":
            return self.current < self.baseline - self.tolerance
        return self.current > self.baseline + self.tolerance

    def describe(self) -> str:
        if not self.gated:
            return f"{self.metric}: not gated (no baseline value)"
        if self.missing:
            return f"{self.metric}: REGRESSED — unavailable for {', '.join(self.missing)}"
        verdict = "REGRESSED" if self.regressed else "ok"
        return (
            f"{self.metric}: {self.current:.4f} vs baseline {self.baseline:.4f} "
            f"(±{self.tolerance:.4f}, {self.direction} is better, n={self.paired}) {verdict}"
        )


@dataclass(frozen=True)
class GateResult:
    checks: list[MetricCheck]
    assertion_failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.assertion_failures and not any(c.regressed for c in self.checks)


# ─── Comparison ────────────────────────────────────────────────────────
def _report_videos(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["id"]: v.get("metrics") or {} for v in report.get("videos") or []}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def check_metric(report: dict[str, Any], noise: dict[str, Any], metric: str) -> MetricCheck:
    """Paired comparison of one metric over the videos both files measured."""
    current_by_id = _report_videos(report)
    base_pairs: list[float] = []
    current_pairs: list[float] = []
    missing: list[str] = []
    for vid, base_metrics in sorted((noise.get("videos") or {}).items()):
        base = base_metrics.get(metric)
        if base is None or vid not in current_by_id:
            continue
        current = current_by_id[vid].get(metric)
        if current is None:
            missing.append(vid)
            continue
        base_pairs.append(base)
        current_pairs.append(current)
    measured = (noise.get("noise") or {}).get(metric) or 0.0
    return MetricCheck(
        metric=metric,
        direction=PRIMARY_METRICS[metric],
        baseline=_mean(base_pairs),
        current=_mean(current_pairs),
        tolerance=max(measured, MIN_TOLERANCE[metric]),
        paired=len(current_pairs),
        missing=tuple(missing),
    )


def assertion_failures(report: dict[str, Any]) -> list[str]:
    """Gating assertion failures: failed and not marked xfail."""
    return [
        f"{video['id']}: {a['type']} — {a.get('detail', '')}"
        for video in report.get("videos") or []
        for a in video.get("assertions") or []
        if a.get("passed") is False and not a.get("xfail")
    ]


def validate_inputs(report: dict[str, Any], noise: dict[str, Any], allow_subset: bool) -> None:
    """Raise ``GateInputError`` when the two files cannot be compared."""
    if report.get("dryRun"):
        raise GateInputError("report is a --dry-run (stub output); nothing to gate")
    if noise.get("schemaVersion") != NOISE_SCHEMA_VERSION:
        raise GateInputError(f"noise schemaVersion {noise.get('schemaVersion')!r} unsupported")
    missing = sorted(set(noise.get("videos") or {}) - set(_report_videos(report)))
    if missing and not allow_subset:
        raise GateInputError(
            f"report lacks {len(missing)} baseline video(s): {', '.join(missing)} "
            "(pass --allow-subset to gate on the overlap)"
        )


def evaluate(
    report: dict[str, Any], noise: dict[str, Any], *, allow_subset: bool = False
) -> GateResult:
    validate_inputs(report, noise, allow_subset)
    checks = [check_metric(report, noise, m) for m in PRIMARY_METRICS]
    return GateResult(checks=checks, assertion_failures=assertion_failures(report))


# ─── CLI ───────────────────────────────────────────────────────────────
def _print_result(result: GateResult) -> None:
    for check in result.checks:
        print(check.describe())
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
    args = parser.parse_args(argv)
    try:
        result = evaluate(
            load_json(Path(args.report)),
            load_json(Path(args.noise)),
            allow_subset=args.allow_subset,
        )
    except (OSError, ValueError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 2
    _print_result(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
