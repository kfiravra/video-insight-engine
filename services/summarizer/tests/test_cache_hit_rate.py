"""Tests for the gate tool ``scripts/cache_hit_rate.py`` (pipeline-1min 2.0).

The aggregation is pure: ``pipeline.timing`` documents in, per call type
(span × provider) cache totals out. Hit rate = reads ÷ (reads + writes) in
tokens, for Anthropic only; cached share = reads ÷ input tokens.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import cache_hit_rate  # noqa: E402

_SONNET = "anthropic/claude-sonnet-4-6"
_MINI = "openai/gpt-4o-mini"


def _call(span: str, model: str, **tokens: int) -> dict[str, Any]:
    return {
        "span": span,
        "model": model,
        "inputTokens": tokens.get("input", 0),
        "cacheReadTokens": tokens.get("read", 0),
        "cacheWriteTokens": tokens.get("write", 0),
        "cacheWrite1hTokens": tokens.get("write_1h", 0),
        "cacheWrite5mTokens": tokens.get("write_5m", 0),
    }


def _plan_runs() -> list[dict[str, Any]]:
    """Three plan calls: one 1h write, two reads of the same 16k-token block."""
    return [
        {
            "version": 2,
            "llmCalls": [_call("plan", _SONNET, input=20_000, write=16_000, write_1h=16_000)],
        },
        {"version": 2, "llmCalls": [_call("plan", _SONNET, input=21_000, read=16_000)]},
        {"version": 2, "llmCalls": [_call("plan", _SONNET, input=19_000, read=16_000)]},
    ]


def _only(stats: list[cache_hit_rate.CallTypeStats]) -> cache_hit_rate.CallTypeStats:
    assert len(stats) == 1
    return stats[0]


class TestAggregate:
    def test_should_compute_hit_rate_as_reads_over_reads_plus_writes_when_anthropic(self) -> None:
        plan = _only(cache_hit_rate.aggregate(_plan_runs()))

        assert plan.hit_rate == pytest.approx(32_000 / 48_000)

    def test_should_split_writes_by_lifetime_when_aggregating(self) -> None:
        runs = [
            {
                "llmCalls": [
                    _call("extraction", _SONNET, write=5_000, write_1h=3_000, write_5m=2_000)
                ]
            },
            {"llmCalls": [_call("extraction", _SONNET, write=4_000, write_5m=4_000)]},
        ]

        extraction = _only(cache_hit_rate.aggregate(runs))

        assert (extraction.writes, extraction.writes_1h, extraction.writes_5m) == (
            9_000,
            3_000,
            6_000,
        )

    def test_should_report_cached_share_without_a_hit_rate_when_openai(self) -> None:
        runs = [{"llmCalls": [_call("synthesis", _MINI, input=2_000, read=1_024)]}]

        synthesis = _only(cache_hit_rate.aggregate(runs))

        assert (synthesis.hit_rate, synthesis.cached_share) == (None, pytest.approx(0.512))

    def test_should_keep_a_fallback_provider_in_its_own_row(self) -> None:
        runs = [*_plan_runs(), {"llmCalls": [_call("plan", "openai/gpt-4o", input=20_000)]}]

        rows = [(s.span, s.provider, s.calls) for s in cache_hit_rate.aggregate(runs)]

        assert rows == [("plan", "anthropic", 3), ("plan", "openai", 1)]

    def test_should_skip_transcription_rows_when_aggregating(self) -> None:
        runs = [{"llmCalls": [{"span": "transcription:openai", "model": "whisper-1"}]}]

        assert cache_hit_rate.aggregate(runs) == []

    def test_should_have_no_hit_rate_when_nothing_was_cached(self) -> None:
        runs = [{"llmCalls": [_call("tier_probe", _SONNET, input=3_000)]}]

        assert _only(cache_hit_rate.aggregate(runs)).hit_rate is None

    def test_should_count_runs_per_timing_version(self) -> None:
        runs = [*_plan_runs(), {"version": 1, "llmCalls": []}]

        assert cache_hit_rate.timing_versions(runs) == {"2": 3, "1": 1}


class TestBuildQuery:
    _NOW = datetime(2026, 10, 12, tzinfo=UTC)

    def test_should_select_non_eval_runs_started_in_the_window_when_no_ids(self) -> None:
        """Golden passes and dev runs are eval-user bursts: they would inflate the prod hit rate."""
        query = cache_hit_rate.build_query(None, 14, self._NOW)

        assert query == {
            "pipeline.timing.startedAt": {"$gte": self._NOW - timedelta(days=14)},
            "evalRun": {"$ne": True},
        }

    def test_should_keep_eval_runs_in_the_window_when_asked_to(self) -> None:
        query = cache_hit_rate.build_query(None, 14, self._NOW, include_eval=True)

        assert query == {"pipeline.timing.startedAt": {"$gte": self._NOW - timedelta(days=14)}}

    def test_should_not_filter_eval_runs_when_ids_given(self) -> None:
        query = cache_hit_rate.build_query(["T1dQhQAm8Tc"], 14, self._NOW)

        assert "evalRun" not in query

    def test_should_parse_the_include_eval_flag(self) -> None:
        assert cache_hit_rate.parse_args(["--include-eval"]).include_eval is True

    def test_should_match_summary_ids_and_youtube_ids_when_ids_given(self) -> None:
        summary_id = "6702f0c1a2b3c4d5e6f70809"

        query = cache_hit_rate.build_query([summary_id, "T1dQhQAm8Tc"], 14, self._NOW)

        assert query["$or"] == [
            {"_id": {"$in": [ObjectId(summary_id)]}},
            {"youtubeId": {"$in": ["T1dQhQAm8Tc"]}},
        ]


class TestRenderTable:
    def test_should_print_one_row_per_call_type_with_its_hit_rate(self) -> None:
        table = cache_hit_rate.render_table(cache_hit_rate.aggregate(_plan_runs()), {"2": 3})

        assert "66.7 %" in next(line for line in table.splitlines() if line.startswith("plan"))
