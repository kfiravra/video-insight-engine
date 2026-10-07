"""Golden-eval reports: per-video outcomes → CSV, Markdown and a JSON summary.

The JSON summary (``reports/eval-<ts>.json``) is the machine-readable run
record: ``scripts/gate.py`` compares it to the baseline, and the noise mode
builds ``noise.json`` from several of them.
"""

from __future__ import annotations

import csv
import json
import shutil
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
    submitted_at: str | None = None  # ISO; anchors the Langfuse trace lookup

    @property
    def overall(self) -> float:
        return float(self.quality.get("overall", 0.0))

    def metrics(self) -> dict[str, float | None]:
        # A failed run has no quality to measure — None, not 0.0, so it never
        # drags a mean or a noise spread; the ``completed`` assertion gates it.
        return {
            "quality": None if self.error else self.overall,
            "faithfulness": self.faithfulness,
            "duplicateRate": self.duplicate_rate,
        }


@dataclass(frozen=True)
class RunInfo:
    run_name: str
    api_label: str  # _eval_noise.api_label — never the raw (secret) EVAL_API_URL
    bypass_cache: bool
    dry_run: bool


# ─── Summary ───────────────────────────────────────────────────────────
def _assertion_dict(result: AssertionResult) -> dict[str, Any]:
    return {
        "type": result.type,
        "passed": result.passed,
        "detail": result.detail,
        "xfail": result.xfail,
        "until": result.until,
    }


def _video_dict(outcome: VideoOutcome) -> dict[str, Any]:
    return {
        "id": outcome.id,
        "domain": outcome.domain,
        "youtubeId": outcome.youtube_id,
        "videoSummaryId": outcome.video_summary_id,
        "traceId": outcome.trace_id,
        "submittedAt": outcome.submitted_at,
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
        "apiLabel": info.api_label,
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
            marker = f" ({a.marker})" if a.marker else ""
            lines.append(f"- **{a.label}** `{o.id}` {a.type}: {a.detail}{marker}")
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


def _write_files(
    outcomes: list[VideoOutcome], summary: dict[str, Any], json_path: Path, label: str
) -> tuple[Path, Path, Path]:
    csv_path, md_path = json_path.with_suffix(".csv"), json_path.with_suffix(".md")
    _write_csv(csv_path, outcomes)
    md_path.write_text(_markdown(summary, outcomes, label), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return csv_path, md_path, json_path


def write_reports(
    outcomes: list[VideoOutcome], info: RunInfo, out_dir: Path, suffix: str = ""
) -> tuple[Path, Path, Path]:
    """Write ``eval-<ts><suffix>.csv|.md|.json``; return the three paths.

    ``suffix`` (e.g. ``-r2``) keeps the passes of one noise run apart.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S") + suffix
    return _write_files(outcomes, build_summary(outcomes, info), out_dir / f"eval-{ts}.json", ts)


# ─── Read-back (``--refresh-langfuse``) ────────────────────────────────
def _outcome_from_row(row: dict[str, Any]) -> VideoOutcome:
    metrics = row.get("metrics") or {}
    return VideoOutcome(
        id=row["id"],
        domain=row.get("domain", "unknown"),
        quality=row.get("quality") or {},
        duplicate_rate=metrics.get("duplicateRate"),
        faithfulness=metrics.get("faithfulness"),
        assertions=[AssertionResult(**a) for a in row.get("assertions") or []],
        components=list(row.get("components") or []),
        youtube_id=row.get("youtubeId"),
        video_summary_id=row.get("videoSummaryId"),
        trace_id=row.get("traceId"),
        error=row.get("error"),
        submitted_at=row.get("submittedAt"),
    )


def outcomes_from_summary(summary: dict[str, Any]) -> list[VideoOutcome]:
    """The per-video outcomes of an ``eval-*.json`` summary."""
    return [_outcome_from_row(row) for row in summary.get("videos") or []]


def backup_reports(json_path: Path) -> None:
    """Copy ``json_path`` and its .csv/.md siblings to ``<file>.bak`` before a rewrite.

    An existing ``.bak`` is kept: it holds the ORIGINAL run, not the last rewrite.
    """
    for path in (json_path, json_path.with_suffix(".csv"), json_path.with_suffix(".md")):
        backup = path.with_name(path.name + ".bak")
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)


def rewrite_reports(
    outcomes: list[VideoOutcome], original: dict[str, Any], json_path: Path
) -> tuple[Path, Path, Path]:
    """Rewrite ``json_path`` and its .csv/.md siblings in place from ``outcomes``.

    Run-level fields (run name, API label, createdAt, …) are kept from
    ``original``; ``refreshedAt`` records the rewrite.
    """
    info = RunInfo(
        run_name=original.get("runName", ""),
        api_label=original.get("apiLabel") or "",
        bypass_cache=bool(original.get("bypassCache")),
        dry_run=bool(original.get("dryRun")),
    )
    summary = {
        **build_summary(outcomes, info),
        "createdAt": original.get("createdAt"),
        "refreshedAt": datetime.now(UTC).isoformat(),
    }
    return _write_files(outcomes, summary, json_path, json_path.stem.removeprefix("eval-"))
