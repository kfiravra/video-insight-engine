"""Backfill Langfuse-derived values into finished eval reports ($0).

``run_eval.py --refresh-langfuse <eval-….json> [...]`` re-reads the pipeline
trace of every row that is missing a Langfuse-derived value — faithfulness,
or the classifier format behind a skipped ``expectedFormat`` assertion — and
rewrites the report's .json/.md/.csv in place (each original kept once as
``<file>.bak``). It only reads Langfuse: no vie-api calls, no pipeline runs.
Rows that errored, have no ``videoSummaryId``, or are complete are left as
they are, and values already present are never overwritten.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from _eval_assertions import AssertionResult, TraceSignals, evaluate_assertions
from _eval_langfuse import LangfuseConfig, ReadPacer, TraceTarget, collect_signals, make_client
from _eval_noise import load_json
from _eval_report import VideoOutcome, outcomes_from_summary, rewrite_reports
from _eval_schema import parse_assertions

logger = logging.getLogger("run_eval.refresh")

_FORMAT = "expectedFormat"


def needs_refresh(outcome: VideoOutcome) -> bool:
    """A completed row still missing a value that only Langfuse can supply."""
    if outcome.error or not outcome.video_summary_id:
        return False
    format_skipped = any(a.type == _FORMAT and a.passed is None for a in outcome.assertions)
    return outcome.faithfulness is None or format_skipped


def _target(outcome: VideoOutcome) -> TraceTarget:
    submitted = datetime.fromisoformat(outcome.submitted_at) if outcome.submitted_at else None
    return TraceTarget(video_summary_id=str(outcome.video_summary_id), submitted_at=submitted)


def _refreshed_assertions(
    outcome: VideoOutcome, record: dict[str, Any] | None, signals: TraceSignals
) -> list[AssertionResult]:
    """Re-evaluate only the skipped ``expectedFormat`` results (they ignore the tabs)."""
    if record is None:
        return outcome.assertions
    typed = [a for a in parse_assertions(record) if a.type == _FORMAT]
    fresh = iter(evaluate_assertions(typed, {}, signals))
    results = []
    for result in outcome.assertions:
        latest = next(fresh, result) if result.type == _FORMAT else result
        results.append(latest if result.passed is None else result)
    return results


def apply_signals(
    outcome: VideoOutcome, signals: TraceSignals | None, record: dict[str, Any] | None
) -> VideoOutcome:
    """Fill the row's missing Langfuse values from ``signals``; keep present ones."""
    if signals is None:
        return outcome
    return replace(
        outcome,
        faithfulness=outcome.faithfulness
        if outcome.faithfulness is not None
        else signals.faithfulness,
        trace_id=outcome.trace_id or signals.trace_id,
        assertions=_refreshed_assertions(outcome, record, signals),
    )


def _backup(json_path: Path) -> None:
    for path in (json_path, json_path.with_suffix(".csv"), json_path.with_suffix(".md")):
        backup = path.with_name(path.name + ".bak")
        if path.exists() and not backup.exists():  # keep the ORIGINAL on re-runs
            shutil.copy2(path, backup)


async def refresh_report(
    client: httpx.AsyncClient,
    json_path: Path,
    records: dict[str, dict[str, Any]],
    pacer: ReadPacer,
) -> int:
    """Refresh one report in place; return how many rows gained a value."""
    summary = load_json(json_path)
    outcomes = outcomes_from_summary(summary)
    targets = {o.id: _target(o) for o in outcomes if needs_refresh(o)}
    if not targets:
        logger.info("%s: nothing to refresh", json_path)
        return 0
    signals = await collect_signals(client, targets, rounds=1, pacer=pacer)
    refreshed = [apply_signals(o, signals.get(o.id), records.get(o.id)) for o in outcomes]
    filled = sum(1 for old, new in zip(outcomes, refreshed, strict=True) if old != new)
    _backup(json_path)
    rewrite_reports(refreshed, summary, json_path)
    still = sorted(o.id for o in refreshed if needs_refresh(o))
    logger.info("%s: filled %d/%d rows; still missing: %s", json_path, filled, len(targets), still)
    return filled


async def refresh_reports(
    paths: list[Path],
    records: dict[str, dict[str, Any]],
    config: LangfuseConfig,
    pacer: ReadPacer | None = None,
) -> int:
    """Refresh every report (one paced Langfuse client); return rows filled."""
    pacer = pacer or ReadPacer()
    async with make_client(config) as client:
        return sum([await refresh_report(client, path, records, pacer) for path in paths])


def run_refresh(report_paths: list[str], records: dict[str, dict[str, Any]]) -> int:
    """CLI entry (``--refresh-langfuse``); exit code 2 without Langfuse keys."""
    config = LangfuseConfig.from_env()
    if config is None:
        logger.error("--refresh-langfuse needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
        return 2
    paths = [Path(p) for p in report_paths]
    filled = asyncio.run(refresh_reports(paths, records, config))
    logger.info("Langfuse refresh: %d row(s) filled across %d report(s)", filled, len(paths))
    return 0
