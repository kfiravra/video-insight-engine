"""Tests for the golden eval's Langfuse I/O (``_eval_langfuse.py``) and the
``--refresh-langfuse`` backfill (``_eval_refresh.py``).

Langfuse is a fake of the v4 SDK client (``async_api.observations``,
``async_api.scores_v3``, ``async_api.dataset_run_items``, ``create_score``) and
every sleep is faked — no network, no waiting, no spend.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langfuse.api.core.api_error import ApiError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import _eval_refresh  # noqa: E402
from _eval_assertions import AssertionResult  # noqa: E402
from _eval_langfuse import (  # noqa: E402
    LangfuseConfig,
    ReadPacer,
    RunItem,
    TraceTarget,
    collect_signals,
    publish_dataset_run,
    retry_wait,
)
from _eval_report import RunInfo, VideoOutcome, write_reports  # noqa: E402


# ─── A fake v4 client ──────────────────────────────────────────────────
class _Model(dict):
    """An SDK model stand-in: attribute access + ``model_dump``."""

    def __getattr__(self, name: str) -> Any:
        return self[name]

    def model_dump(self, **_kw: Any) -> dict[str, Any]:
        return dict(self)


_SCORE = _Model(name="faithfulness", value=0.75, timestamp="2026-10-07T10:00:00Z")
_PROBE = _Model(
    name="tier_probe", output='{"format": "tutorial"}', startTime="2026-10-07T10:00:00Z"
)


class FakeLangfuse:
    """Every known trace scores faithfulness 0.75 and probes format ``tutorial``."""

    def __init__(self, *, roots: dict[str, str] | None = None, scored: bool = True) -> None:
        self.roots = roots or {}  # root observation name → trace id (pre-P0b name lookup)
        self.scored = scored
        self.reads: list[dict[str, Any]] = []
        self.writes: list[str] = []
        self.created_scores: list[dict[str, Any]] = []
        self.async_api = SimpleNamespace(
            observations=SimpleNamespace(get_many=self._observations),
            scores_v3=SimpleNamespace(get_many_v3=self._scores),
            dataset_run_items=SimpleNamespace(create=self._run_item),
        )

    async def _observations(self, **kw: Any) -> SimpleNamespace:
        self.reads.append({"what": "observations", **kw})
        if "filter" in kw:
            conditions = json.loads(kw["filter"])
            name = next(c["value"] for c in conditions if c["column"] == "traceName")
            self.reads[-1]["trace_name"] = name
            trace = self.roots.get(name)
            found = [_Model(trace_id=trace, start_time="2026-10-07T10:00:00Z")] if trace else []
            return SimpleNamespace(data=found)
        return SimpleNamespace(data=[_PROBE])

    async def _scores(self, **kw: Any) -> SimpleNamespace:
        self.reads.append({"what": "scores", **kw})
        return SimpleNamespace(data=[_SCORE] if self.scored else [])

    async def _run_item(self, **kw: Any) -> None:
        self.writes.append(f"run-item:{kw['dataset_item_id']}:{kw['trace_id']}")

    def create_score(self, **kw: Any) -> None:
        self.created_scores.append(kw)


async def _no_sleep(_: float) -> None:
    return None


_TARGET = TraceTarget("vs1", datetime(2026, 10, 7, tzinfo=UTC), trace_id="tr1")


# ─── Signals + dataset runs ────────────────────────────────────────────
class TestLangfuse:
    async def test_should_read_faithfulness_and_format_when_the_trace_id_is_known(self) -> None:
        fake = FakeLangfuse()

        signals = await collect_signals(fake, {"g1": _TARGET}, sleep=_no_sleep)

        assert (signals["g1"].trace_id, signals["g1"].faithfulness, signals["g1"].probe_format) == (
            "tr1",
            0.75,
            "tutorial",
        )
        observations, scores = fake.reads
        assert (observations["trace_id"], observations["request_options"]) == (
            "tr1",
            {"max_retries": 0},
        )
        assert (scores["trace_id"], scores["name"]) == ("tr1", "faithfulness")

    async def test_should_find_the_trace_by_trace_name_when_the_report_predates_trace_ids(
        self,
    ) -> None:
        fake = FakeLangfuse(roots={"pipeline:vs1": "tr-old"})
        target = TraceTarget("vs1", datetime(2026, 10, 7, tzinfo=UTC))

        signals = await collect_signals(fake, {"g1": target}, rounds=1, sleep=_no_sleep)

        conditions = json.loads(fake.reads[0]["filter"])
        assert [c["column"] for c in conditions] == ["traceName", "startTime"]
        assert fake.reads[0]["trace_name"] == "pipeline:vs1"
        assert signals["g1"].trace_id == "tr-old"

    async def test_should_omit_the_video_when_no_trace_matches(self) -> None:
        target = TraceTarget("vs1", None)

        signals = await collect_signals(FakeLangfuse(), {"g1": target}, rounds=2, sleep=_no_sleep)

        assert signals == {}

    async def test_should_re_poll_when_the_faithfulness_score_has_not_landed(self) -> None:
        fake = FakeLangfuse(scored=False)

        signals = await collect_signals(fake, {"g1": _TARGET}, rounds=3, sleep=_no_sleep)

        assert signals["g1"].faithfulness is None
        assert sum(r["what"] == "scores" for r in fake.reads) == 3

    async def test_should_link_only_items_that_have_a_trace_when_publishing(self) -> None:
        fake = FakeLangfuse()
        items = [
            RunItem("g1", "tr1", {}, {"quality": 0.9, "duplicateRate": None}),
            RunItem("g2", None, {}),
        ]

        linked = await publish_dataset_run(fake, "golden-test", items)

        assert (linked, fake.writes) == (1, ["run-item:g1:tr1"])
        assert [(s["trace_id"], s["name"], s["value"]) for s in fake.created_scores] == [
            ("tr1", "golden.quality", 0.9)
        ]


class TestMakeClient:
    async def test_should_flush_and_never_shut_down_when_used_twice_in_one_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK reuses one resource manager per key: a shutdown would kill the next client."""
        import langfuse as sdk
        from _eval_langfuse import make_client

        calls: list[str] = []

        class _Sdk:
            def __init__(self, **kw: Any) -> None:
                calls.append(f"init:{kw['base_url']}")

            def flush(self) -> None:
                calls.append("flush")

            def shutdown(self) -> None:
                calls.append("shutdown")

        monkeypatch.setattr(sdk, "Langfuse", _Sdk)
        config = LangfuseConfig("http://lf.test", "pk", "sk")

        for _ in range(2):
            async with make_client(config):
                pass

        assert calls == ["init:http://lf.test", "flush", "init:http://lf.test", "flush"]


# ─── Read pacing + retry ───────────────────────────────────────────────
class FakeClock:
    """A monotonic clock that only moves when the pacer sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _scripted(*outcomes: Exception | str):
    """A read that raises / returns each outcome in turn (the last one repeats)."""
    queue = list(outcomes)

    async def read() -> str:
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return read


def _http(status: int, retry_after: str | None = None) -> ApiError:
    return ApiError(status_code=status, headers={"Retry-After": retry_after} if retry_after else {})


class TestReadPacer:
    async def test_should_retry_after_the_retry_after_delay_when_langfuse_returns_429(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, sleep=clock.sleep, clock=clock)

        result = await pacer.call(_scripted(_http(429, "17"), "ok"))

        assert (result, clock.sleeps) == ("ok", [17.0])

    async def test_should_back_off_exponentially_when_a_5xx_has_no_retry_after(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, sleep=clock.sleep, clock=clock)

        await pacer.call(_scripted(_http(503), _http(503), "ok"))

        assert clock.sleeps == [5.0, 10.0]

    async def test_should_raise_when_the_backoff_budget_is_spent(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, budget_s=30.0, sleep=clock.sleep, clock=clock)

        with pytest.raises(ApiError):
            await pacer.call(_scripted(_http(429, "20")))

        assert clock.sleeps == [20.0]

    async def test_should_raise_at_once_when_the_error_is_not_retryable(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, sleep=clock.sleep, clock=clock)

        with pytest.raises(ApiError):
            await pacer.call(_scripted(_http(404)))

        assert clock.sleeps == []

    async def test_should_space_consecutive_reads_by_the_spacing_interval(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=2.0, sleep=clock.sleep, clock=clock)

        for _ in range(3):
            await pacer.call(_scripted("ok"))

        assert clock.sleeps == [2.0, 2.0]

    async def test_should_keep_reads_in_flight_within_the_concurrency_bound(self) -> None:
        in_flight, peak = 0, 0

        async def read() -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.005)
            in_flight -= 1

        pacer = ReadPacer(concurrency=2, spacing_s=0.0)
        await asyncio.gather(*(pacer.call(read) for _ in range(6)))

        assert peak == 2

    def test_should_read_an_http_date_retry_after_as_seconds_from_now(self) -> None:
        assert retry_wait({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, attempt=0) == 0.0


# ─── --refresh-langfuse ────────────────────────────────────────────────
_RECORDS = {
    "a": {
        "id": "a",
        "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "domain": "food",
        "format": "tutorial",
        "language": "en",
        "expectedTabs": [],
        "requiredComponents": [],
        "keyContent": [],
        "assertions": [{"type": "expectedFormat", "values": ["tutorial"]}],
    }
}


def _outcome(vid: str, faithfulness: float | None, **extra: Any) -> VideoOutcome:
    return VideoOutcome(
        id=vid,
        domain="food",
        quality={"id": vid, "overall": 0.9},
        duplicate_rate=0.0,
        faithfulness=faithfulness,
        video_summary_id=f"vs-{vid}",
        submitted_at="2026-10-07T14:00:00+00:00",
        **extra,
    )


@pytest.fixture
def report(tmp_path: Path) -> Path:
    """Pass report: ``a`` lost its Langfuse read, ``b`` has it, ``c`` errored."""
    skipped = AssertionResult("expectedFormat", None, "probe format unavailable")
    outcomes = [
        _outcome("a", None, assertions=[skipped]),
        _outcome("b", 0.8, trace_id="tr-b"),
        _outcome("c", None, error="pipeline failed"),
    ]
    info = RunInfo(run_name="r1", api_label="localhost:3000", bypass_cache=True, dry_run=False)
    return write_reports(outcomes, info, tmp_path, suffix="-r1")[2]


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> FakeLangfuse:
    fake = FakeLangfuse(roots={"pipeline:vs-a": "tr-new"})

    @asynccontextmanager
    async def _client(_config: LangfuseConfig) -> AsyncIterator[FakeLangfuse]:
        yield fake

    monkeypatch.setattr(_eval_refresh, "make_client", _client)
    return fake


async def _refresh(report: Path) -> dict[str, Any]:
    config = LangfuseConfig("http://lf.test", "pk", "sk")
    pacer = ReadPacer(spacing_s=0.0, sleep=_no_sleep)
    await _eval_refresh.refresh_reports([report], _RECORDS, config, pacer)
    return {v["id"]: v for v in json.loads(report.read_text())["videos"]}


class TestRefresh:
    async def test_should_fill_faithfulness_only_on_the_row_that_lacked_it(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        rows = await _refresh(report)
        faith = {vid: row["metrics"]["faithfulness"] for vid, row in rows.items()}
        assert faith == {"a": 0.75, "b": 0.8, "c": None}

    async def test_should_look_up_only_the_rows_that_need_a_refresh(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        await _refresh(report)
        assert [r["trace_name"] for r in langfuse.reads if "trace_name" in r] == ["pipeline:vs-a"]

    async def test_should_never_write_when_refreshing(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        await _refresh(report)
        assert (langfuse.writes, langfuse.created_scores) == ([], [])

    async def test_should_re_evaluate_a_skipped_format_assertion_when_refreshing(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        rows = await _refresh(report)
        assert rows["a"]["assertions"][0]["passed"] is True

    async def test_should_keep_a_backup_of_each_report_file_when_rewriting(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        await _refresh(report)
        backups = sorted(p.name for p in report.parent.glob("*.bak"))
        pattern = re.compile(rf"{re.escape(report.stem)}\.(csv|json|md)\.\d{{8}}-\d{{6}}\.bak")
        assert [pattern.fullmatch(name).group(1) for name in backups] == ["csv", "json", "md"]

    async def test_should_update_the_run_mean_when_a_row_is_filled(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        await _refresh(report)
        summary = json.loads(report.read_text())
        assert (summary["metrics"]["faithfulness"], "refreshedAt" in summary) == (0.775, True)
