#!/usr/bin/env python3
"""Prompt-cache hit rate per LLM call type, read from ``pipeline.timing``.

The gate tool behind pipeline-1min's cache rule (Kfir 2026-10-12): gate
reports carry the cache hit rate per call type, and the plan's 1-hour hit rate
after two weeks of prod traffic decides whether the plan keeps its 1h
breakpoint — below ~53 % a 1h write costs more than no breakpoint; what
replaces it under 50 % is Kfir's gate-2 decision (``docs/llm-cost-model.md``).

Reads the ``pipeline.timing.llmCalls`` rows of a set of runs from MongoDB
``videoSummaryCache`` — and, from timing version 4, the ``llmFailures`` rows of
refused calls (``error: "Refusal"``: billed, cache tokens included) — and
prints, per call type (the call's ``span``: plan, extraction, synthesis …) and
provider:

- calls, input tokens, cache reads, cache writes and their 1h / 5m split;
- hit rate = reads ÷ (reads + writes), in tokens — Anthropic only, the
  provider that reports writes;
- cached share = reads ÷ input tokens — what OpenAI's automatic cache is
  judged by (it reports reads, never writes).

Runs timed before ``pipeline.timing`` version 2 have no 1h / 5m split and no
OpenAI reads. Transcription rows (Whisper / Gemini, no prompt cache) are
skipped. The window leaves out eval-user runs (golden passes, dev runs —
``evalRun: true``) unless ``--include-eval``; ``--ids`` reads the named runs
whoever ran them. Read-only.

Usage::

    python scripts/cache_hit_rate.py                     # runs started in the last 14 days
    python scripts/cache_hit_rate.py --days 1            # the last 24 hours
    python scripts/cache_hit_rate.py --days 1 --include-eval
                                                         # ... with eval-user runs too
    python scripts/cache_hit_rate.py --ids T1dQhQAm8Tc 6702f0c1a2b3c4d5e6f70809
                                                         # YouTube ids and/or summary _ids
    python scripts/cache_hit_rate.py --json

Exit codes: 0 report printed; 1 no timed run matched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import MongoClient

_OBJECT_ID = re.compile(r"^[0-9a-f]{24}$")
_TRANSCRIPTION_SPAN = "transcription:"
_REFUSAL = "Refusal"


@dataclass
class CallTypeStats:
    """Cache totals for one call type on one provider."""

    span: str
    provider: str
    calls: int = 0
    input_tokens: int = 0
    reads: int = 0
    writes: int = 0
    writes_1h: int = 0
    writes_5m: int = 0

    def add(self, call: Mapping[str, Any]) -> None:
        self.calls += 1
        self.input_tokens += int(call.get("inputTokens") or 0)
        self.reads += int(call.get("cacheReadTokens") or 0)
        self.writes += int(call.get("cacheWriteTokens") or 0)
        self.writes_1h += int(call.get("cacheWrite1hTokens") or 0)
        self.writes_5m += int(call.get("cacheWrite5mTokens") or 0)

    @property
    def hit_rate(self) -> float | None:
        """reads ÷ (reads + writes); None off Anthropic or when nothing was cached."""
        total = self.reads + self.writes
        return self.reads / total if self.provider == "anthropic" and total else None

    @property
    def cached_share(self) -> float | None:
        """reads ÷ input tokens; None without input."""
        return self.reads / self.input_tokens if self.input_tokens else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span": self.span,
            "provider": self.provider,
            "calls": self.calls,
            "inputTokens": self.input_tokens,
            "cacheReadTokens": self.reads,
            "cacheWriteTokens": self.writes,
            "cacheWrite1hTokens": self.writes_1h,
            "cacheWrite5mTokens": self.writes_5m,
            "hitRate": self.hit_rate,
            "cachedShare": self.cached_share,
        }


def _provider(call: Mapping[str, Any]) -> str:
    model = str(call.get("model") or "")
    return model.split("/", 1)[0] if "/" in model else "unknown"


def _billed_calls(timing: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Every answered call, then every refused one (a refusal reads and writes the cache too)."""
    yield from timing.get("llmCalls") or []
    yield from (f for f in timing.get("llmFailures") or [] if f.get("error") == _REFUSAL)


def aggregate(timings: Iterable[Mapping[str, Any]]) -> list[CallTypeStats]:
    """Per (span, provider) cache totals over every billed LLM call of the given timing docs."""
    groups: dict[tuple[str, str], CallTypeStats] = {}
    for timing in timings:
        for call in _billed_calls(timing):
            span = str(call.get("span") or call.get("feature") or "unknown")
            if span.startswith(_TRANSCRIPTION_SPAN):
                continue
            key = (span, _provider(call))
            groups.setdefault(key, CallTypeStats(*key)).add(call)
    return sorted(groups.values(), key=lambda stats: (stats.span, stats.provider))


def timing_versions(timings: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many runs carry each ``pipeline.timing`` schema version."""
    return dict(Counter(str(timing.get("version", "?")) for timing in timings))


def build_query(
    ids: list[str] | None, days: float, now: datetime, *, include_eval: bool = False
) -> dict[str, Any]:
    """Runs by id (24-hex summary ``_id`` or YouTube id), else those started in the window.

    The window leaves out eval-user rows (``evalRun``, D25) unless
    ``include_eval``: a golden pass or a dev-run batch is a burst of plan calls
    minutes apart, so it would lift the plan's 1h hit rate above what prod
    traffic earns. Rows named by id are always read.
    """
    if not ids:
        window = {"pipeline.timing.startedAt": {"$gte": now - timedelta(days=days)}}
        return window if include_eval else {**window, "evalRun": {"$ne": True}}
    object_ids = [ObjectId(i) for i in ids if _OBJECT_ID.match(i)]
    youtube_ids = [i for i in ids if not _OBJECT_ID.match(i)]
    return {
        "pipeline.timing": {"$exists": True},
        "$or": [{"_id": {"$in": object_ids}}, {"youtubeId": {"$in": youtube_ids}}],
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f} %"


def render_table(stats: list[CallTypeStats], versions: dict[str, int]) -> str:
    runs = sum(versions.values())
    by_version = ", ".join(f"v{v}: {n}" for v, n in sorted(versions.items()))
    lines = [f"=== prompt cache per call type — {runs} runs ({by_version}) ==="]
    header = (
        f"{'call type':24} {'provider':10} {'calls':>6} {'input tok':>11} {'reads':>10} "
        f"{'writes':>10} {'1h':>9} {'5m':>9} {'hit rate':>9} {'cached':>8}"
    )
    lines += [header, "-" * len(header)]
    for s in stats:
        lines.append(
            f"{s.span:24} {s.provider:10} {s.calls:>6} {s.input_tokens:>11,} {s.reads:>10,} "
            f"{s.writes:>10,} {s.writes_1h:>9,} {s.writes_5m:>9,} {_percent(s.hit_rate):>9} "
            f"{_percent(s.cached_share):>8}"
        )
    lines.append("hit rate = reads ÷ (reads + writes), Anthropic; cached = reads ÷ input tokens.")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--mongo-uri", default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    )
    parser.add_argument("--mongo-db", default=os.environ.get("MONGODB_DB", "video-insight-engine"))
    parser.add_argument("--days", type=float, default=14.0, help="Window in days (default 14)")
    parser.add_argument("--ids", nargs="+", help="Summary _ids and/or YouTube ids (no window)")
    parser.add_argument(
        "--include-eval",
        action="store_true",
        help="Count eval-user runs (golden passes, dev runs) in the window; --ids always does",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    query = build_query(args.ids, args.days, datetime.now(UTC), include_eval=args.include_eval)
    with MongoClient(args.mongo_uri) as client:
        docs = client[args.mongo_db]["videoSummaryCache"].find(query, {"pipeline.timing": 1})
        timings = [doc["pipeline"]["timing"] for doc in docs]
    if not timings:
        print("No timed pipeline runs matched.")
        return 1
    stats = aggregate(timings)
    versions = timing_versions(timings)
    if args.json:
        call_types = [s.to_dict() for s in stats]
        payload = {"runs": len(timings), "versions": versions, "callTypes": call_types}
        print(json.dumps(payload, indent=2))
    else:
        print(render_table(stats, versions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
