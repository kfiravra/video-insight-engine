"""Tests for the golden eval's Langfuse I/O (``_eval_langfuse.py``) and the
``--refresh-langfuse`` backfill (``_eval_refresh.py``).

Langfuse is replaced by ``httpx.MockTransport`` handlers and every sleep is
faked — no network, no waiting, no spend.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

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

Handler = Callable[[httpx.Request], httpx.Response]

# ─── Signals + dataset runs ────────────────────────────────────────────
_TRACE = {
    "id": "tr1",
    "scores": [{"name": "faithfulness", "value": 0.75, "timestamp": "2026-10-07T10:00:00Z"}],
    "observations": [{"name": "tier_probe", "output": '{"format": "tutorial"}'}],
}
_TARGET = TraceTarget("vs1", datetime(2026, 10, 7, tzinfo=UTC))


def _langfuse(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://lf.test", transport=httpx.MockTransport(handler))


async def _no_sleep(_: float) -> None:
    return None


class TestLangfuse:
    async def test_should_read_faithfulness_and_format_from_the_run_trace(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/public/traces":
                return httpx.Response(200, json={"data": [{"id": "tr1"}]})
            return httpx.Response(200, json=_TRACE)

        async with _langfuse(handler) as client:
            signals = await collect_signals(client, {"g1": _TARGET}, sleep=_no_sleep)
        assert (signals["g1"].faithfulness, signals["g1"].probe_format) == (0.75, "tutorial")

    async def test_should_search_traces_by_pipeline_summary_id(self) -> None:
        names: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            names.append(request.url.params["name"])
            return httpx.Response(200, json={"data": []})

        async with _langfuse(handler) as client:
            await collect_signals(client, {"g1": _TARGET}, rounds=1, sleep=_no_sleep)
        assert names == ["pipeline:vs1"]

    async def test_should_omit_video_when_no_trace_matches(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        async with _langfuse(handler) as client:
            signals = await collect_signals(client, {"g1": _TARGET}, rounds=2, sleep=_no_sleep)
        assert signals == {}

    async def test_should_link_only_items_that_have_a_trace(self) -> None:
        posted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            posted.append(request.url.path)
            return httpx.Response(200, json={})

        items = [
            RunItem("g1", "tr1", {}, {"quality": 0.9, "duplicateRate": None}),
            RunItem("g2", None, {}),
        ]
        async with _langfuse(handler) as client:
            linked = await publish_dataset_run(client, "golden-test", items)
        assert (linked, posted) == (1, ["/api/public/scores", "/api/public/dataset-run-items"])


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


def _scripted(*responses: httpx.Response) -> Handler:
    queue = list(responses)
    return lambda request: queue.pop(0) if len(queue) > 1 else queue[0]


class TestReadPacer:
    async def test_should_retry_after_the_retry_after_delay_when_langfuse_returns_429(
        self,
    ) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, sleep=clock.sleep, clock=clock)
        handler = _scripted(
            httpx.Response(429, headers={"Retry-After": "17"}), httpx.Response(200, json={})
        )
        async with _langfuse(handler) as client:
            resp = await pacer.get(client, "/api/public/traces")
        assert (resp.status_code, clock.sleeps) == (200, [17.0])

    async def test_should_back_off_exponentially_when_a_5xx_has_no_retry_after(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, sleep=clock.sleep, clock=clock)
        handler = _scripted(httpx.Response(503), httpx.Response(503), httpx.Response(200))
        async with _langfuse(handler) as client:
            await pacer.get(client, "/x")
        assert clock.sleeps == [5.0, 10.0]

    async def test_should_raise_when_the_backoff_budget_is_spent(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=0.0, budget_s=30.0, sleep=clock.sleep, clock=clock)
        handler = _scripted(httpx.Response(429, headers={"Retry-After": "20"}))
        async with _langfuse(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await pacer.get(client, "/x")
        assert clock.sleeps == [20.0]

    async def test_should_space_consecutive_reads_by_the_spacing_interval(self) -> None:
        clock = FakeClock()
        pacer = ReadPacer(spacing_s=2.0, sleep=clock.sleep, clock=clock)
        async with _langfuse(lambda r: httpx.Response(200, json={})) as client:
            for _ in range(3):
                await pacer.get(client, "/x")
        assert clock.sleeps == [2.0, 2.0]

    async def test_should_keep_reads_in_flight_within_the_concurrency_bound(self) -> None:
        in_flight, peak = 0, 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.005)
            in_flight -= 1
            return httpx.Response(200, json={})

        pacer = ReadPacer(concurrency=2, spacing_s=0.0)
        async with _langfuse(handler) as client:
            await asyncio.gather(*(pacer.get(client, "/x") for _ in range(6)))
        assert peak == 2

    def test_should_read_an_http_date_retry_after_as_seconds_from_now(self) -> None:
        resp = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
        assert retry_wait(resp, attempt=0) == 0.0


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


class FakeLangfuse:
    """Langfuse double: every trace scores faithfulness 0.7, format tutorial."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url.params.get("name", ""))))
        if request.url.path == "/api/public/traces":
            return httpx.Response(200, json={"data": [{"id": "tr-new"}]})
        return httpx.Response(200, json=_TRACE | {"id": "tr-new"})


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> FakeLangfuse:
    fake = FakeLangfuse()
    monkeypatch.setattr(_eval_refresh, "make_client", lambda config: _langfuse(fake))
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
        assert [name for _, name in langfuse.requests if name] == ["pipeline:vs-a"]

    async def test_should_never_post_when_refreshing(
        self, report: Path, langfuse: FakeLangfuse
    ) -> None:
        await _refresh(report)
        assert {method for method, _ in langfuse.requests} == {"GET"}

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
