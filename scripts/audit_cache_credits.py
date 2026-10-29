#!/usr/bin/env python3
"""Audit Anthropic prompt-cache crediting in the llm_usage collection.

Reads MongoDB ``llm_usage`` records and prints whether cache reads are being
detected, how often, and how much money the cache is saving. Used to verify
that ``cache_control: {"type": "ephemeral"}`` on the static prompt halves
plus the ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` fields
we capture in ``llm_common.callback._build_record`` are all wired correctly.

Read-only: never mutates the source DB.

Usage::

    python scripts/audit_cache_credits.py                          # last 24h, all videos
    python scripts/audit_cache_credits.py --hours 1                # last hour only
    python scripts/audit_cache_credits.py --video-id <youtubeId>   # one video's calls
    python scripts/audit_cache_credits.py --feature extract        # one pipeline stage
    python scripts/audit_cache_credits.py --limit 50000            # raise the cap for long windows
    python scripts/audit_cache_credits.py --json                   # machine-readable

Exit codes:
    0  cache appears to be working (≥1 cache hit observed)
    1  scanned ``llm_usage`` is empty for the window
    2  Anthropic calls present, zero cache hits — likely misconfiguration
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import MongoClient


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true"))
    parser.add_argument("--mongo-db", default=os.environ.get("MONGODB_DB", "video-insight-engine"))
    parser.add_argument("--hours", type=int, default=24, help="Window size in hours (default: 24)")
    parser.add_argument("--video-id", default=None, help="Filter to a single youtubeId / videoSummaryId")
    parser.add_argument("--feature", default=None, help="Filter to a single feature tag (e.g. 'extract')")
    parser.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Max records to read (default: 10000). Raise for week+ windows.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout instead of a table")
    return parser.parse_args(argv)


def build_query(args: argparse.Namespace) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(hours=args.hours)
    query: dict[str, Any] = {"timestamp": {"$gte": cutoff}}
    if args.video_id:
        query["video_id"] = args.video_id
    if args.feature:
        query["feature"] = args.feature
    return query


def aggregate_by_model(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group records by model and compute cache stats per group."""
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_model[str(r.get("model", "unknown"))].append(r)

    out: dict[str, dict[str, Any]] = {}
    for model, rows in sorted(by_model.items()):
        total_calls = len(rows)
        cache_hits = sum(1 for r in rows if r.get("cache_hit"))
        cache_writes = sum(1 for r in rows if int(r.get("cache_creation_tokens", 0) or 0) > 0)
        total_read_tokens = sum(int(r.get("cache_read_tokens", 0) or 0) for r in rows)
        total_creation_tokens = sum(int(r.get("cache_creation_tokens", 0) or 0) for r in rows)
        total_savings = sum(float(r.get("cache_savings_usd", 0) or 0) for r in rows)
        total_cost = sum(float(r.get("cost_usd", 0) or 0) for r in rows)
        provider = str(rows[0].get("provider", "unknown")) if rows else "unknown"

        out[model] = {
            "provider": provider,
            "total_calls": total_calls,
            "cache_hits": cache_hits,
            "cache_writes": cache_writes,
            "cache_hit_rate": round(cache_hits / total_calls, 3) if total_calls else 0.0,
            "total_cache_read_tokens": total_read_tokens,
            "total_cache_creation_tokens": total_creation_tokens,
            "total_cost_usd": round(total_cost, 4),
            "total_cache_savings_usd": round(total_savings, 4),
        }
    return out


def derive_verdict(stats_by_model: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Decide whether the cache appears healthy across all observed Anthropic models."""
    anthropic_models = {m: s for m, s in stats_by_model.items() if s["provider"] == "anthropic"}
    if not anthropic_models:
        return {"status": "no-anthropic", "exit_code": 1, "message": "No Anthropic calls in window."}

    total_anth_calls = sum(s["total_calls"] for s in anthropic_models.values())
    total_anth_hits = sum(s["cache_hits"] for s in anthropic_models.values())

    if total_anth_calls == 0:
        return {"status": "empty", "exit_code": 1, "message": "No records in window."}
    if total_anth_hits == 0:
        return {
            "status": "misconfigured",
            "exit_code": 2,
            "message": (
                "Zero cache hits on any Anthropic call. Check that "
                "`cache_control: ephemeral` is reaching LiteLLM and that the "
                "static prompt halves are stable across calls."
            ),
        }
    return {
        "status": "ok",
        "exit_code": 0,
        "message": f"Cache hits observed on {total_anth_hits}/{total_anth_calls} Anthropic calls.",
    }


def render_table(stats: dict[str, dict[str, Any]], verdict: dict[str, Any], window_hours: int) -> str:
    lines: list[str] = []
    lines.append(f"=== llm_usage cache audit (last {window_hours}h) ===")
    if not stats:
        lines.append("  (no records)")
        lines.append(f"verdict: {verdict['status']} — {verdict['message']}")
        return "\n".join(lines)

    header = (
        f"{'model':45}  {'calls':>6}  {'hits':>5}  {'hit%':>5}  "
        f"{'read_tok':>10}  {'create_tok':>10}  {'cost$':>9}  {'saved$':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for model, s in stats.items():
        lines.append(
            f"{model:45}  {s['total_calls']:>6}  {s['cache_hits']:>5}  "
            f"{s['cache_hit_rate']*100:>4.1f}%  "
            f"{s['total_cache_read_tokens']:>10}  {s['total_cache_creation_tokens']:>10}  "
            f"${s['total_cost_usd']:>8.4f}  ${s['total_cache_savings_usd']:>8.4f}"
        )
    lines.append("")
    lines.append(f"verdict: {verdict['status']} — {verdict['message']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # MongoClient holds a connection pool — release deterministically so the
    # script is safe to import and call from longer-running contexts (tests,
    # notebooks, future scheduled jobs).
    with MongoClient(args.mongo_uri) as client:
        db = client[args.mongo_db]

        query = build_query(args)
        # `.limit()` bounds the cursor so a generous --hours value can't
        # OOM the script on a high-traffic day.
        records = list(
            db["llm_usage"]
            .find(query, {"_id": 0})
            .sort("timestamp", -1)
            .limit(args.limit)
        )

        stats = aggregate_by_model(records)
        verdict = derive_verdict(stats)

        if args.json:
            print(json.dumps(
                {
                    "window_hours": args.hours,
                    "limit": args.limit,
                    "filter": {k: v for k, v in vars(args).items() if k in {"video_id", "feature"} and v},
                    "by_model": stats,
                    "verdict": verdict,
                    "sample_count": len(records),
                },
                indent=2,
                default=str,
            ))
        else:
            print(render_table(stats, verdict, args.hours))

        return int(verdict["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
