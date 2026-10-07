"""Command line of ``run_eval.py``: the argument parser and its cross-flag checks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from _eval_noise import DEFAULT_NOISE_PATH, api_label, load_json, summary_api_label
from _eval_resume import parse_since
from _eval_schema import DATASET_PATH


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--filter", default="", help="Only ids containing this substring.")
    parser.add_argument(
        "--ids",
        default="",
        metavar="ID,ID",
        help="Only these exact golden ids (comma-separated; each must be a live entry).",
    )
    parser.add_argument("--limit", type=int, default=0, help="First N live entries (0 = all).")
    parser.add_argument(
        "--subset",
        choices=("all", "quick"),
        default="all",
        help="quick = the one live entry per domain flagged `quick: true` in videos.yaml.",
    )


def _add_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Videos in flight at once (default 1). Home IP: 3; drop to 2 if YouTube "
        "throttles (caption/yt-dlp 429s).",
    )
    parser.add_argument(
        "--resume-since",
        default="",
        metavar="ISO8601",
        help="First pass only: score the eval user's runs that COMPLETED at/after this "
        "time (offset required, e.g. 2026-10-07T16:30:00+03:00) instead of re-running them.",
    )
    parser.add_argument("--fail-under", type=float, default=0.0)
    parser.add_argument(
        "--no-bypass-cache",
        action="store_true",
        help="Score the stored output instead of a fresh run ($0; scorer debugging only).",
    )
    parser.add_argument(
        "--noise-runs",
        type=int,
        default=0,
        help="Run the live set N (>= 2) times and write the per-metric noise file.",
    )
    parser.add_argument("--noise-out", default=str(DEFAULT_NOISE_PATH))
    parser.add_argument(
        "--merge-into",
        nargs="+",
        default=[],
        metavar="REPORT_JSON",
        help="Write pass k's rows into the k-th existing report (same id → replaced; "
        "in place, <file>.<ts>.bak kept); with --noise-runs, rebuild the noise file from them.",
    )
    parser.add_argument(
        "--publish-run", default="", help="Langfuse dataset run name (empty = skip)."
    )


def _add_offline(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--noise-from",
        nargs="+",
        default=[],
        metavar="REPORT_JSON",
        help="Rebuild the noise file from existing pass summaries ($0, no API calls; "
        "rows of ids that are not live in --dataset are ignored).",
    )
    parser.add_argument(
        "--refresh-langfuse",
        nargs="+",
        default=[],
        metavar="REPORT_JSON",
        help="Re-read missing Langfuse values into finished reports in place "
        "($0; keeps <file>.<ts>.bak).",
    )
    parser.add_argument(
        "--resync",
        nargs="+",
        default=[],
        metavar="REPORT_JSON",
        help="Re-score stored rows and re-read xfail markers from --dataset, in place "
        "($0; keeps <file>.<ts>.bak).",
    )


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("EVAL_API_URL") or "http://localhost:3000"
    )
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--output", default="reports/")
    parser.add_argument("--dry-run", action="store_true")
    _add_selection(parser)
    _add_run(parser)
    _add_offline(parser)
    return parser


def _check_resume(parser: argparse.ArgumentParser, args: Any) -> None:
    if args.dry_run or args.no_bypass_cache:
        parser.error(
            "--resume-since reuses live bypassCache runs; drop --dry-run/--no-bypass-cache"
        )
    try:
        parse_since(args.resume_since)
    except ValueError as exc:
        parser.error(str(exc))


def _check_merge(parser: argparse.ArgumentParser, args: Any) -> None:
    passes = args.noise_runs or 1
    if len(args.merge_into) != passes:
        parser.error(
            f"--merge-into needs one report per pass ({passes}), got {len(args.merge_into)}"
        )
    if args.dry_run or args.no_bypass_cache:
        parser.error("--merge-into folds fresh live runs; drop --dry-run/--no-bypass-cache")
    if not args.ids:
        parser.error("--merge-into needs --ids (the videos to fold into the reports)")
    resolved = [Path(t).resolve() for t in args.merge_into]
    if len(set(resolved)) != len(resolved):
        parser.error("--merge-into reports must be distinct files (one per pass)")
    run_label = api_label(args.api_url.rstrip("/"))
    for target in args.merge_into:
        problem = merge_target_problem(Path(target), run_label)
        if problem:
            parser.error(f"--merge-into {target}: {problem}")


def merge_target_problem(path: Path, run_label: str) -> str | None:
    """Why ``path`` cannot take this run's rows (checked before any paid pass), or None."""
    if not path.is_file():
        return "no such report"
    try:
        summary = load_json(path)
    except (OSError, ValueError) as exc:
        return f"unreadable report ({exc})"
    if summary.get("dryRun") or summary.get("bypassCache") is not True:
        return "not a fresh bypassCache run — its rows are not comparable"
    target_label = summary_api_label(summary)
    if target_label != run_label:
        return f"ran against {target_label!r}, this run targets {run_label!r}"
    return None


def check_run_args(parser: argparse.ArgumentParser, args: Any) -> None:
    """Reject flag combinations that would spend money on a meaningless run."""
    if args.noise_runs == 1 or args.noise_runs < 0:
        parser.error("--noise-runs needs at least 2 passes")
    if args.noise_runs and args.dry_run:
        parser.error("--noise-runs measures live variance; it cannot be combined with --dry-run")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.resume_since:
        _check_resume(parser, args)
    if args.merge_into:
        _check_merge(parser, args)
