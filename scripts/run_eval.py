#!/usr/bin/env python3
"""Run the pipeline against the golden dataset and score the output.

Phases:

1. Load and validate ``dev/golden-dataset/videos.yaml`` (``_eval_schema``).
2. For each live entry, POST it to vie-api with ``bypassCache`` (so the
   current code runs, not a stored output) and hold the SSE stream until the
   run ends — OR, with ``--dry-run``, score a stub without network calls.
3. Read each run's Langfuse trace (faithfulness score, classifier format).
4. Score every video: the legacy quality score (``score_entry``), the
   duplicate-item rate, faithfulness, and the per-video assertions.
5. Write ``reports/eval-{ts}.csv|.md|.json`` and optionally publish a
   Langfuse dataset run.

Primary metrics (gated by ``scripts/gate.py`` against the noise file):
quality (mean ``overall``), faithfulness, duplicate-item rate.

Usage::

    # One live pass against a running vie-api (EVAL_USER_* + LANGFUSE_* exported)
    python3 scripts/run_eval.py --output reports/

    # Noise baseline: the live set twice → dev/golden-dataset/noise.json
    python3 scripts/run_eval.py --noise-runs 2 --publish-run golden-baseline-YYYYMMDD

    # Faster: 3 videos at a time (home IP; drop to 2 if YouTube throttles)
    python3 scripts/run_eval.py --concurrency 3

    # Cheap post-change check: one live video per domain
    python3 scripts/run_eval.py --subset quick --concurrency 3

    # Resume a stopped noise run: pass r1 scores the eval user's runs that
    # completed since the given time instead of re-running them
    python3 scripts/run_eval.py --noise-runs 2 --resume-since 2026-10-07T16:30:00+03:00

    # Backfill Langfuse values a throttled read missed ($0), in place (<file>.<ts>.bak kept)
    python3 scripts/run_eval.py --refresh-langfuse reports/eval-<ts>-r1.json \\
        reports/eval-<ts>-r2.json

    # Re-derive noise.json from the passes of an earlier noise run ($0)
    python3 scripts/run_eval.py --noise-from reports/eval-<ts>-r1.json reports/eval-<ts>-r2.json

    # Re-score stored rows + re-read xfail markers after a videos.yaml edit ($0)
    python3 scripts/run_eval.py --resync reports/eval-<ts>-r1.json reports/eval-<ts>-r2.json

    # Fold a new golden video into an existing baseline: run it twice, write
    # pass k into the k-th report (<file>.<ts>.bak kept), rebuild noise.json from them
    python3 scripts/run_eval.py --ids review-new --noise-runs 2 \
        --merge-into reports/eval-<ts>-r1.json reports/eval-<ts>-r2.json

    # Dry-run — exercises scoring + reporting without network calls
    python3 scripts/run_eval.py --dry-run --fail-under 0.7
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from _eval_api import SharedToken, authenticate, run_pipeline, run_with_reauth
from _eval_assertions import (
    AssertionResult,
    TraceSignals,
    completed_result,
    evaluate_assertions,
)
from _eval_cli import build_parser, check_run_args
from _eval_langfuse import (
    LangfuseConfig,
    RunItem,
    TraceTarget,
    collect_signals,
    make_client,
    publish_dataset_run,
)
from _eval_merge import merge_into_reports, run_resync
from _eval_metrics import duplicate_item_rate
from _eval_noise import api_label, write_noise_from
from _eval_refresh import run_refresh
from _eval_report import RunInfo, VideoOutcome, write_reports
from _eval_resume import load_reusable_runs, parse_since, youtube_id
from _eval_schema import (
    DATASET_PATH,
    is_allowed_video_url,
    live_records,
    parse_assertions,
    read_dataset,
)
from _eval_scoring import EvalResult, failed_result, score_entry, stub_actual

# `baseline_golden_dataset.py` imports these from here; __all__ keeps the
# re-exports explicit (and safe from `ruff --fix`).
__all__ = [
    "EvalResult",
    "authenticate",
    "is_allowed_video_url",
    "load_dataset",
    "run_pipeline",
    "score_entry",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_eval")

_POLITENESS_GAP_S = 1.0


# ─── Loading ───────────────────────────────────────────────────────────
def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    """Return the raw dataset records after validating the whole file.

    Raises ``pydantic.ValidationError`` on any schema problem so a typo in an
    assertion fails before the first paid run.
    """
    return read_dataset(path)


def _parse_ids(ids: str) -> list[str]:
    return [i.strip() for i in ids.split(",") if i.strip()]


def select_records(
    records: list[dict[str, Any]],
    id_filter: str = "",
    limit: int = 0,
    subset: str = "all",
    ids: str = "",
) -> list[dict[str, Any]]:
    """Live entries (``subset="quick"``: only ``quick`` ones) matching ``id_filter``
    and, when given, the exact comma-separated ``ids``; capped at ``limit`` (0 = all).

    Disabled entries (dead links, placeholders) are excluded from the run AND
    the averages — scoring them 0 would fail the gate on dataset rot. An
    ``ids`` entry that is not live raises ``ValueError`` before any spend.
    """
    skipped = [r["id"] for r in records if r.get("disabled")]
    if skipped:
        logger.info("Skipping %d disabled entries: %s", len(skipped), ", ".join(skipped))
    live = [r for r in records if not r.get("disabled") and id_filter in r["id"]]
    wanted = _parse_ids(ids)
    if wanted:
        unknown = sorted(set(wanted) - {r["id"] for r in live})
        if unknown:
            raise ValueError(f"--ids not live in the dataset: {', '.join(unknown)}")
        live = [r for r in live if r["id"] in wanted]
    if subset == "quick":
        live = [r for r in live if r.get("quick")]
    return live[:limit] if limit else live


# ─── One pass ──────────────────────────────────────────────────────────
@dataclass
class Session:
    """Run-wide settings plus the eval user's token, shared by concurrent runs."""

    api_url: str
    bypass_cache: bool
    dry_run: bool
    langfuse: LangfuseConfig | None
    concurrency: int = 1
    auth: SharedToken = field(init=False)

    def __post_init__(self) -> None:
        self.auth = SharedToken(self.api_url)


@dataclass(frozen=True)
class EntryRun:
    record: dict[str, Any]
    actual: dict[str, Any] | None
    error: str | None


def _scrub(message: str, api_url: str) -> str:
    """Replace the API base URL (a CI secret) in an error that lands in the report."""
    return message.replace(api_url, f"<{api_label(api_url)}>") if api_url else message


async def run_entry(
    record: dict[str, Any], session: Session, reuse: dict[str, dict[str, Any]] | None = None
) -> EntryRun:
    """Run (or stub, or reuse) one golden video; never raises for a pipeline failure.

    ``reuse`` maps youtubeId → the payload of a run that already completed
    (``--resume-since``); such an entry is scored without a POST.
    """
    if session.dry_run:
        return EntryRun(record, stub_actual(record), None)
    reused = (reuse or {}).get(youtube_id(record["url"]) or "")
    if reused is not None:
        logger.info("Reusing the completed run for %s (no POST)", record["id"])
        return EntryRun(record, reused, None)
    try:
        actual, _ = await run_with_reauth(
            session.api_url,
            record["url"],
            session.auth.token,
            session.bypass_cache,
            refresh=session.auth.refresh,
        )
    except Exception as exc:  # noqa: BLE001 — one failed video must not abort the run
        logger.warning("Pipeline failed for %s: %s", record["id"], exc)
        return EntryRun(record, None, _scrub(str(exc) or type(exc).__name__, session.api_url))
    await asyncio.sleep(_POLITENESS_GAP_S)  # keeps cache-hit bursts off the IP limiter
    return EntryRun(record, actual, None)


def _trace_targets(runs: list[EntryRun]) -> dict[str, TraceTarget]:
    targets: dict[str, TraceTarget] = {}
    for run in runs:
        actual = run.actual or {}
        if actual.get("videoSummaryId") and actual.get("submittedAt"):
            targets[run.record["id"]] = TraceTarget(
                video_summary_id=actual["videoSummaryId"],
                submitted_at=datetime.fromisoformat(actual["submittedAt"]),
            )
    return targets


async def fetch_signals(runs: list[EntryRun], session: Session) -> dict[str, TraceSignals]:
    """Faithfulness + classifier format per golden id, from the runs' Langfuse traces."""
    if session.dry_run:
        return {}
    if session.langfuse is None:
        logger.warning("LANGFUSE_* keys not set — faithfulness and format checks unavailable")
        return {}
    async with make_client(session.langfuse) as client:
        return await collect_signals(client, _trace_targets(runs))


def _assertion_results(
    run: EntryRun, signals: TraceSignals, dry_run: bool
) -> list[AssertionResult]:
    results = [] if dry_run else [completed_result(run.error)]
    if run.actual is None:
        return results
    return results + evaluate_assertions(parse_assertions(run.record), run.actual, signals)


def to_outcome(run: EntryRun, signals: TraceSignals | None, dry_run: bool) -> VideoOutcome:
    """Score one entry run into the reported per-video outcome."""
    record, actual = run.record, run.actual
    signals = signals or TraceSignals()
    result = score_entry(record, actual) if actual else failed_result(record, run.error or "")
    tabs = (actual or {}).get("tabs") or []
    return VideoOutcome(
        id=record["id"],
        domain=record.get("domain", "unknown"),
        quality=result.as_dict(),
        duplicate_rate=duplicate_item_rate(tabs) if actual else None,
        faithfulness=signals.faithfulness,
        assertions=_assertion_results(run, signals, dry_run),
        components=[str(t.get("component", "")).lower() for t in tabs],
        youtube_id=(actual or {}).get("youtubeId"),
        video_summary_id=(actual or {}).get("videoSummaryId"),
        trace_id=signals.trace_id,
        error=run.error,
        submitted_at=(actual or {}).get("submittedAt"),
    )


def _run_items(outcomes: list[VideoOutcome]) -> list[RunItem]:
    return [
        RunItem(
            dataset_item_id=o.id,
            trace_id=o.trace_id,
            metadata={"quality": o.quality, "duplicateRate": o.duplicate_rate},
            scores={
                "quality": o.metrics()["quality"],
                "duplicateRate": o.duplicate_rate,
                "assertionsFailed": float(sum(1 for a in o.assertions if a.gating_failure)),
            },
        )
        for o in outcomes
    ]


async def publish(outcomes: list[VideoOutcome], run_name: str, session: Session) -> None:
    """Publish one pass as Langfuse dataset run ``run_name`` (no-op without keys)."""
    if session.langfuse is None or session.dry_run:
        logger.info("Langfuse publish skipped (dry-run or no LANGFUSE_* keys)")
        return
    async with make_client(session.langfuse) as client:
        await publish_dataset_run(client, run_name, _run_items(outcomes))


async def run_entries(
    records: list[dict[str, Any]], session: Session, reuse: dict[str, dict[str, Any]] | None
) -> list[EntryRun]:
    """Run every record, at most ``session.concurrency`` at once; ordered by golden id."""
    slots = asyncio.Semaphore(session.concurrency)

    async def one(record: dict[str, Any]) -> EntryRun:
        async with slots:
            return await run_entry(record, session, reuse)

    runs = await asyncio.gather(*(one(record) for record in records))
    return sorted(runs, key=lambda run: run.record["id"])


async def run_pass(
    records: list[dict[str, Any]],
    session: Session,
    out_dir: Path,
    *,
    suffix: str,
    run_name: str,
    reuse: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[VideoOutcome], Path]:
    """Run every record once, score, report and publish; return outcomes + JSON path."""
    logger.info(
        "Eval pass%s: %d entries (dry_run=%s, bypassCache=%s, concurrency=%d, reused=%d)",
        suffix,
        len(records),
        session.dry_run,
        session.bypass_cache,
        session.concurrency,
        len(reuse or {}),
    )
    runs = await run_entries(records, session, reuse)
    signals = await fetch_signals(runs, session)
    outcomes = [to_outcome(r, signals.get(r.record["id"]), session.dry_run) for r in runs]
    info = RunInfo(
        run_name=run_name,
        api_label=api_label(session.api_url),
        bypass_cache=session.bypass_cache,
        dry_run=session.dry_run,
    )
    csv_path, md_path, json_path = write_reports(outcomes, info, out_dir, suffix=suffix)
    logger.info("Wrote %s, %s, %s", csv_path, md_path, json_path)
    if run_name:
        await publish(outcomes, run_name, session)
    return outcomes, json_path


# ─── Modes ─────────────────────────────────────────────────────────────
def _mean_overall(outcomes: list[VideoOutcome]) -> float:
    return sum(o.overall for o in outcomes) / max(1, len(outcomes))


async def reusable_runs(
    records: list[dict[str, Any]], session: Session, args: Any
) -> dict[str, dict[str, Any]]:
    """``--resume-since``: completed runs of these records to score instead of re-run."""
    if not args.resume_since:
        return {}
    wanted = {yid for r in records if (yid := youtube_id(r["url"]))}
    since = parse_since(args.resume_since)
    return await load_reusable_runs(session.api_url, session.auth.token, since, wanted)


def _by_id(path: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in load_dataset(Path(path))}


def _live_ids(path: str) -> set[str]:
    return set(live_records(load_dataset(Path(path))))


async def single_mode(records: list[dict[str, Any]], session: Session, args: Any) -> int:
    reuse = await reusable_runs(records, session, args)
    outcomes, _ = await run_pass(
        records, session, Path(args.output), suffix="", run_name=args.publish_run, reuse=reuse
    )
    if args.merge_into:
        merge_into_reports([outcomes], args.merge_into, _by_id(args.dataset))
    avg = _mean_overall(outcomes)
    logger.info("Average overall %.3f (n=%d)", avg, len(outcomes))
    if args.fail_under and avg < args.fail_under:
        logger.error("Average %.3f below --fail-under threshold %.3f", avg, args.fail_under)
        return 1
    return 0


async def noise_mode(records: list[dict[str, Any]], session: Session, args: Any) -> int:
    """Run the live set ``--noise-runs`` times and write the per-metric noise file.

    ``--resume-since`` reuses completed runs in pass r1 only; later passes
    always run fresh (they are what measures the run-to-run noise). With
    ``--merge-into`` pass k lands in the k-th existing report, and the noise
    file is rebuilt from those merged reports.
    """
    json_paths: list[str] = []
    passes: list[list[VideoOutcome]] = []
    for i in range(1, args.noise_runs + 1):
        run_name = f"{args.publish_run}-r{i}" if args.publish_run else ""
        reuse = await reusable_runs(records, session, args) if i == 1 else {}
        outcomes, json_path = await run_pass(
            records, session, Path(args.output), suffix=f"-r{i}", run_name=run_name, reuse=reuse
        )
        json_paths.append(str(json_path))
        passes.append(outcomes)
    if args.merge_into:
        merge_into_reports(passes, args.merge_into, _by_id(args.dataset))
        json_paths = list(args.merge_into)
    return write_noise_from(json_paths, args.noise_out, _live_ids(args.dataset))


async def _open_session(args: Any) -> Session:
    session = Session(
        api_url=args.api_url.rstrip("/"),
        bypass_cache=not args.no_bypass_cache,
        dry_run=args.dry_run,
        langfuse=LangfuseConfig.from_env(),
        concurrency=args.concurrency,
    )
    if not args.dry_run:
        session.auth.token = await authenticate(session.api_url)
    return session


async def run_all(args: Any) -> int:
    records = select_records(
        load_dataset(Path(args.dataset)), args.filter, args.limit, args.subset, args.ids
    )
    session = await _open_session(args)
    if args.noise_runs:
        return await noise_mode(records, session, args)
    return await single_mode(records, session, args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__ or "")
    args = parser.parse_args(argv)
    if args.refresh_langfuse:
        return run_refresh(args.refresh_langfuse, _by_id(args.dataset))
    if args.resync:
        return run_resync(args.resync, _by_id(args.dataset))
    if args.noise_from:
        return write_noise_from(args.noise_from, args.noise_out, _live_ids(args.dataset))
    check_run_args(parser, args)
    return asyncio.run(run_all(args))


if __name__ == "__main__":
    raise SystemExit(main())
