"""Langfuse I/O for the golden eval (public REST API over httpx).

Reads: per video, the pipeline trace ``pipeline:<videoSummaryId>`` created
after the eval's POST, then its ``faithfulness`` score and its
``tier_probe`` generation output (for the ``expectedFormat`` assertion).
Every read goes through ``ReadPacer`` — Langfuse Cloud throttles the public
read API hard (2026-10-07: 4 reads in 3 s, then 429 for > 30 s, which cost a
baseline pass 16/18 faithfulness values).

Writes (``--publish-run``): one dataset run item per golden entry in the
``vie-golden-v1`` dataset, linked to that entry's pipeline trace, plus the
eval's own metrics as ``golden.*`` scores on the same trace. A run item
without a trace is rejected by Langfuse, so entries whose trace was not
found are skipped with a warning.

The REST API is used instead of the SDK so the eval needs no Langfuse SDK
on the runner (the SDK's dataset-run method differs between v2 and v3).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from _eval_assertions import TraceSignals
from _eval_metrics import extract_faithfulness, extract_probe_format

logger = logging.getLogger("run_eval.langfuse")

_DEFAULT_HOST = "https://cloud.langfuse.com"
# Scores land up to ~25 s after the pipeline ends (bounded faithfulness
# drain) and Langfuse ingestion is asynchronous, so the read-back retries.
SIGNAL_ROUNDS = 5
SIGNAL_ROUND_WAIT_S = 30.0
# Tolerates clock skew between the runner and the prod box.
_SINCE_SKEW = timedelta(minutes=2)

# Read pacing: one read in flight, starts ≥ READ_SPACING_S apart (≤ 30/min).
READ_CONCURRENCY = 1
READ_SPACING_S = 2.0
# 429 / 5xx retry: honour Retry-After, else exponential from RETRY_BASE_S
# (capped at RETRY_MAX_WAIT_S); a read gives up after RETRY_ATTEMPTS or when
# the pacer's total backoff budget is spent — then it fails and stays None.
RETRY_ATTEMPTS = 6
RETRY_BASE_S = 5.0
RETRY_MAX_WAIT_S = 60.0
BACKOFF_BUDGET_S = 900.0
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class LangfuseConfig:
    host: str
    public_key: str
    secret_key: str

    @classmethod
    def from_env(cls) -> LangfuseConfig | None:
        public = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret = os.environ.get("LANGFUSE_SECRET_KEY")
        if not public or not secret:
            return None
        host = os.environ.get("LANGFUSE_BASE_URL") or _DEFAULT_HOST
        return cls(host=host.rstrip("/"), public_key=public, secret_key=secret)


@dataclass(frozen=True)
class TraceTarget:
    video_summary_id: str
    # None (a report row from before submittedAt was recorded): search by
    # name only — every bypassCache run has its own videoSummaryId anyway.
    submitted_at: datetime | None


@dataclass(frozen=True)
class RunItem:
    dataset_item_id: str
    trace_id: str | None
    metadata: dict[str, Any]
    scores: dict[str, float | None] = field(default_factory=dict)


def make_client(config: LangfuseConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.host, auth=(config.public_key, config.secret_key), timeout=30.0
    )


# ─── Read pacing ───────────────────────────────────────────────────────
def retry_wait(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying ``resp``: Retry-After when given, else backoff."""
    header = resp.headers.get("Retry-After", "").strip()
    if header.isdigit():
        return float(header)
    if header:
        try:
            return max(0.0, (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            logger.debug("Unparseable Retry-After %r; using backoff", header)
    return min(RETRY_BASE_S * 2**attempt, RETRY_MAX_WAIT_S)


class ReadPacer:
    """Paces Langfuse reads and retries throttled / failed ones.

    At most ``concurrency`` reads in flight, each start ≥ ``spacing_s`` after
    the previous one; a 429/5xx is retried (``retry_wait``) while the read
    holds its slot, so a throttle pauses the whole read stream instead of
    burning more of the quota. Total backoff is bounded by ``budget_s``.
    """

    def __init__(
        self,
        *,
        concurrency: int = READ_CONCURRENCY,
        spacing_s: float = READ_SPACING_S,
        budget_s: float = BACKOFF_BUDGET_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._slots = asyncio.Semaphore(concurrency)
        self._start_lock = asyncio.Lock()
        self._spacing_s = spacing_s
        self._budget_s = budget_s
        self._sleep = sleep
        self._clock = clock
        self._next_start = 0.0

    async def _wait_turn(self) -> None:
        async with self._start_lock:
            delay = self._next_start - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_start = self._clock() + self._spacing_s

    async def _backoff(self, resp: httpx.Response, attempt: int) -> bool:
        """Sleep before a retry; False when out of attempts or budget."""
        wait = retry_wait(resp, attempt)
        if attempt + 1 >= RETRY_ATTEMPTS or wait > self._budget_s:
            return False
        self._budget_s -= wait
        logger.info("Langfuse %s — retrying in %.0f s", resp.status_code, wait)
        await self._sleep(wait)
        return True

    async def get(
        self, client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """GET with pacing + retry; raises ``httpx.HTTPStatusError`` when it still fails."""
        async with self._slots:
            for attempt in range(RETRY_ATTEMPTS):
                await self._wait_turn()
                resp = await client.get(url, params=params)
                if resp.status_code not in _RETRYABLE or not await self._backoff(resp, attempt):
                    break
        resp.raise_for_status()
        return resp


# ─── Reads ─────────────────────────────────────────────────────────────
async def _find_trace_id(
    client: httpx.AsyncClient, target: TraceTarget, pacer: ReadPacer
) -> str | None:
    params: dict[str, Any] = {
        "name": f"pipeline:{target.video_summary_id}",
        "orderBy": "timestamp.desc",
        "limit": 1,
    }
    if target.submitted_at is not None:
        since = target.submitted_at - _SINCE_SKEW
        params["fromTimestamp"] = since.isoformat().replace("+00:00", "Z")
    resp = await pacer.get(client, "/api/public/traces", params)
    data = resp.json().get("data") or []
    return data[0].get("id") if data else None


async def fetch_signals(
    client: httpx.AsyncClient,
    target: TraceTarget,
    pacer: ReadPacer | None = None,
    trace_id: str | None = None,
) -> TraceSignals | None:
    """Signals of the newest matching trace (or ``trace_id``), ``None`` when absent."""
    pacer = pacer or ReadPacer()
    trace_id = trace_id or await _find_trace_id(client, target, pacer)
    if not trace_id:
        return None
    trace = (await pacer.get(client, f"/api/public/traces/{trace_id}")).json()
    return TraceSignals(
        trace_id=trace_id,
        faithfulness=extract_faithfulness(trace),
        probe_format=extract_probe_format(trace),
    )


async def _fetch_round(
    client: httpx.AsyncClient,
    pending: dict[str, TraceTarget],
    found: dict[str, TraceSignals],
    pacer: ReadPacer,
) -> None:
    for golden_id, target in pending.items():
        known = found[golden_id].trace_id if golden_id in found else None
        try:
            signals = await fetch_signals(client, target, pacer, trace_id=known)
        except httpx.HTTPError as exc:
            logger.warning("Langfuse read failed for %s: %s", golden_id, exc)
            continue
        if signals is not None:
            found[golden_id] = signals


async def collect_signals(
    client: httpx.AsyncClient,
    targets: dict[str, TraceTarget],
    *,
    rounds: int = SIGNAL_ROUNDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    pacer: ReadPacer | None = None,
) -> dict[str, TraceSignals]:
    """Read signals for every target, re-polling those without a faithfulness score."""
    pacer = pacer or ReadPacer(sleep=sleep)
    found: dict[str, TraceSignals] = {}
    for attempt in range(rounds):
        pending = {
            gid: t
            for gid, t in targets.items()
            if gid not in found or found[gid].faithfulness is None
        }
        if not pending:
            break
        if attempt:
            await sleep(SIGNAL_ROUND_WAIT_S)
        await _fetch_round(client, pending, found, pacer)
    missing = sorted(gid for gid in targets if gid not in found)
    if missing:
        logger.warning("No Langfuse pipeline trace found for: %s", ", ".join(missing))
    return found


# ─── Writes ────────────────────────────────────────────────────────────
async def _post_scores(client: httpx.AsyncClient, item: RunItem, run_name: str) -> None:
    for name, value in item.scores.items():
        if value is None:
            continue
        resp = await client.post(
            "/api/public/scores",
            json={
                "traceId": item.trace_id,
                "name": f"golden.{name}",
                "value": float(value),
                "dataType": "NUMERIC",
                "comment": f"golden eval run {run_name}",
            },
        )
        resp.raise_for_status()


async def publish_dataset_run(
    client: httpx.AsyncClient, run_name: str, items: list[RunItem]
) -> int:
    """Link each item's pipeline trace into dataset run ``run_name``; return items linked."""
    linked = 0
    for item in items:
        if not item.trace_id:
            logger.warning("Run upload skipped for %s: no pipeline trace", item.dataset_item_id)
            continue
        try:
            await _post_scores(client, item, run_name)
            resp = await client.post(
                "/api/public/dataset-run-items",
                json={
                    "runName": run_name,
                    "datasetItemId": item.dataset_item_id,
                    "traceId": item.trace_id,
                    "metadata": item.metadata,
                },
            )
            resp.raise_for_status()
            linked += 1
        except httpx.HTTPError as exc:
            logger.warning("Run upload failed for %s: %s", item.dataset_item_id, exc)
    logger.info("Langfuse dataset run %s: %d/%d items linked", run_name, linked, len(items))
    return linked
