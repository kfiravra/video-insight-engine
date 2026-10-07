"""Golden-eval reports: per-video outcomes → CSV, Markdown and a JSON summary.

The JSON summary (``reports/eval-<ts>.json``) is the machine-readable run
record: ``scripts/gate.py`` compares it to the baseline, and the noise mode
builds ``noise.json`` from several of them.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _eval_assertions import AssertionResult
from _eval_metrics import PRIMARY_METRICS, mean_or_none

SUMMARY_SCHEMA_VERSION = 1


@dataclass
class VideoOutcome:
    id: str
    domain: str
    quality: dict[str, Any]  # asdict(run_eval.EvalResult)
    duplicate_rate: float | None = None
    faithfulness: float | None = None
    assertions: list[AssertionResult] = field(default_factory=list)
    components: list[str] = field(default_factory=list)  # tab order, for layout stability
    youtube_id: str | None = None
    video_summary_id: str | None = None
    trace_id: str | None = None
    error: str | None = None

    @property
    def overall(self) -> float:
        return float(self.quality.get("overall", 0.0))

    def metrics(self) -> dict[str, float | None]:
        return {
            "quality": self.overall,
            "faithfulness": self.faithfulness,
            "duplicateRate": self.duplicate_rate,
        }


@dataclass(frozen=True)
class RunInfo:
    run_name: str
    api_url: str
    bypass_cache: bool
    dry_run: bool


# ─── Summary ───────────────────────────────────────────────────────────
def _assertion_dict(result: AssertionResult) -> dict[str, Any]:
    return {
        "type": result.type,
        "passed": result.passed,
        "detail": result.detail,
        "xfail": result.xfail,
    }


def _video_dict(outcome: VideoOutcome) -> dict[str, Any]:
    return {
        "id": outcome.id,
        "domain": outcome.domain,
        "youtubeId": outcome.youtube_id,
        "videoSummaryId": outcome.video_summary_id,
        "traceId": outcome.trace_id,
        "error": outcome.error,
        "metrics": outcome.metrics(),
        "components": outcome.components,
        "quality": outcome.quality,
        "assertions": [_assertion_dict(a) for a in outcome.assertions],
    }


def _assertion_counts(outcomes: list[VideoOutcome]) -> dict[str, int]:
    results = [a for o in outcomes for a in o.assertions]
    return {
        "total": len(results),
        "failed": sum(1 for a in results if a.gating_failure),
        "xfailed": sum(1 for a in results if a.passed is False and a.xfail),
        "xpassed": sum(1 for a in results if a.passed is True and a.xfail),
        "skipped": sum(1 for a in results if a.passed is None),
    }


def build_summary(outcomes: list[VideoOutcome], info: RunInfo) -> dict[str, Any]:
    per_metric = {m: [o.metrics()[m] for o in outcomes] for m in PRIMARY_METRICS}
    return {
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "runName": info.run_name,
        "createdAt": datetime.now(UTC).isoformat(),
        "apiUrl": info.api_url,
        "bypassCache": info.bypass_cache,
        "dryRun": info.dry_run,
        "metrics": {m: mean_or_none(v) for m, v in per_metric.items()},
        "metricCoverage": {m: sum(1 for x in v if x is not None) for m, v in per_metric.items()},
        "assertions": _assertion_counts(outcomes),
        "videos": [_video_dict(o) for o in outcomes],
    }


# ─── Writers ───────────────────────────────────────────────────────────
def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _write_csv(path: Path, outcomes: list[VideoOutcome]) -> None:
    quality_fields = list(outcomes[0].quality) if outcomes else ["id", "overall"]
    columns = [*quality_fields, "faithfulness", "duplicate_rate", "assertions_failed"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for o in outcomes:
            failed = [a.type for a in o.assertions if a.gating_failure]
            writer.writerow(
                {
                    **o.quality,
                    "faithfulness": o.faithfulness,
                    "duplicate_rate": o.duplicate_rate,
                    "assertions_failed": ";".join(failed),
                }
            )


def _video_row(o: VideoOutcome) -> str:
    q = o.quality
    # Rows that never produced tabs (rejected URL, pipeline failure) carry
    # the default forbidden_ok=1.0 — render "not measured", not a pass.
    tab_count = q.get("tab_count", 0)
    forbidden = "—" if tab_count == 0 else ("OK" if q.get("forbidden_ok") == 1.0 else "HIT")
    failed = sum(1 for a in o.assertions if a.gating_failure)
    return (
        f"| {o.id} | {o.domain} | {tab_count}/{q.get('expected_tab_count', 0)} | "
        f"{q.get('component_coverage', 0):.2f} | {q.get('content_coverage', 0):.2f} | "
        f"{q.get('empty_tab_count', 0)} | {forbidden} | {_fmt(o.faithfulness)} | "
        f"{_fmt(o.duplicate_rate)} | {failed}/{len(o.assertions)} | **{o.overall:.3f}** |"
    )


def _assertion_lines(outcomes: list[VideoOutcome]) -> list[str]:
    lines = ["", "## Assertions", ""]
    for o in outcomes:
        for a in o.assertions:
            if a.passed is True and not a.xfail:
                continue
            label = {True: "XPASS", False: "XFAIL" if a.xfail else "FAIL", None: "SKIP"}[a.passed]
            lines.append(f"- **{label}** `{o.id}` {a.type}: {a.detail}")
    return lines if len(lines) > 3 else [*lines, "- all passed"]


def _markdown(summary: dict[str, Any], outcomes: list[VideoOutcome], ts: str) -> str:
    m, cov = summary["metrics"], summary["metricCoverage"]
    lines = [
        f"# Eval Report — {ts}",
        "",
        f"**Average overall:** {_fmt(m['quality'])} (n={len(outcomes)})",
        f"**Faithfulness:** {_fmt(m['faithfulness'])} (n={cov['faithfulness']})",
        f"**Duplicate-item rate:** {_fmt(m['duplicateRate'])} (n={cov['duplicateRate']})",
        f"**bypassCache:** {summary['bypassCache']}",
        "",
        "| id | domain | tabs (got/exp) | components | content | empty | forbidden "
        "| faith | dup | assert fail | overall |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_video_row(o) for o in sorted(outcomes, key=lambda x: x.overall)]
    return "\n".join(lines + _assertion_lines(outcomes)) + "\n"


def write_reports(
    outcomes: list[VideoOutcome], info: RunInfo, out_dir: Path, suffix: str = ""
) -> tuple[Path, Path, Path]:
    """Write ``eval-<ts><suffix>.csv|.md|.json``; return the three paths.

    ``suffix`` (e.g. ``-r2``) keeps the passes of one noise run apart.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S") + suffix
    csv_path, md_path, json_path = (out_dir / f"eval-{ts}.{ext}" for ext in ("csv", "md", "json"))
    summary = build_summary(outcomes, info)
    _write_csv(csv_path, outcomes)
    md_path.write_text(_markdown(summary, outcomes, ts), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return csv_path, md_path, json_path
