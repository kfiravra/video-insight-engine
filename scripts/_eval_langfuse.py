"""Langfuse I/O for the golden eval (Langfuse SDK v4 public-API client).

Reads: per video, its pipeline trace, then the trace's ``faithfulness`` score
and its ``tier_probe`` generation output (for the ``expectedFormat``
assertion). The trace id comes from the submit response (``traceId`` =
``sha256(runId)`` hex, first 32 chars, P0b); a report from before P0b only has
the videoSummaryId, so its trace is found by the trace name
``pipeline:<videoSummaryId>`` (SDK v2 and P0a traces alike). Reads use the
non-deprecated endpoints only:
``api.observations.get_many`` (v2 observations) and
``api.scores_v3.get_many_v3`` (v3 scores). Every read goes through
``ReadPacer``: Langfuse Cloud throttles the public read API hard (2026-10-07:
4 reads in 3 s, then 429 for > 30 s, which cost a baseline pass 16/18
faithfulness values), so the SDK's own retries are off and the pacer owns them.

Writes (``--publish-run``): one dataset run item per golden entry in the
``vie-golden-v1`` dataset, linked to that entry's pipeline trace, plus the
eval's own metrics as ``golden.*`` scores on the same trace. A run item
without a trace is rejected by Langfuse, so entries whose trace was not found
are skipped with a warning.

The SDK is imported lazily, so ``--dry-run`` imports this module without it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx
from _eval_assertions import TraceSignals
from _eval_metrics import extract_faithfulness, extract_probe_format

logger = logging.getLogger("run_eval.langfuse")

T = TypeVar("T")

_DEFAULT_HOST = "https://cloud.langfuse.com"
# Re-poll rounds for a trace whose faithfulness score has not landed yet (the
# judge runs in the background after the pipeline's response).
SIGNAL_ROUNDS = 5
SIGNAL_ROUND_WAIT_S = 30.0
# The name lookup (pre-P0b reports) starts this long before the recorded submit.
_SINCE_SKEW = timedelta(minutes=2)
# Read pacing: one read at a time, spaced, so the eval stays under the
# public API's rate limit instead of tripping it.
READ_CONCURRENCY = 1
READ_SPACING_S = 2.0
# Retry of a throttled / failed read: Retry-After when given, else exponential
# backoff from RETRY_BASE_S capped at RETRY_MAX_WAIT_S; at most RETRY_ATTEMPTS
# tries and BACKOFF_BUDGET_S of waiting in total per pacer.
RETRY_ATTEMPTS = 6
RETRY_BASE_S = 5.0
RETRY_MAX_WAIT_S = 60.0
BACKOFF_BUDGET_S = 900.0
_RETRYABLE = frozenset({429, 500, 502, 503, 504})
# The pacer owns retries; the SDK's own (2 by default) would spend the quota twice.
_NO_SDK_RETRY: dict[str, Any] = {"max_retries": 0}
_OBSERVATION_FIELDS = "basic,io"


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
    # None (a report row from before submittedAt was recorded): the name lookup
    # has no time bound — every bypassCache run has its own videoSummaryId anyway.
    submitted_at: datetime | None
    # The run's trace id from the submit response (P0b); None on older reports.
    trace_id: str | None = None


@dataclass(frozen=True)
class RunItem:
    dataset_item_id: str
    trace_id: str | None
    metadata: dict[str, Any]
    scores: dict[str, float | None] = field(default_factory=dict)


def _api_error() -> type[Exception]:
    """The SDK's HTTP error class (``status_code``, ``headers``), imported lazily."""
    from langfuse.api.core.api_error import ApiError

    return ApiError


@asynccontextmanager
async def make_client(config: LangfuseConfig) -> AsyncIterator[Any]:
    """The v4 SDK client; on exit ``flush`` sends the queued ``golden.*`` scores.

    Tracing stays enabled: the SDK drops ``create_score`` calls otherwise. The
    eval opens no spans of its own. Never ``shutdown`` here: the SDK keeps one
    resource manager per public key and hands the same (dead) one to the next
    client, so the eval's second ``make_client`` (``--publish-run`` after the
    reads) would queue scores nothing sends and hang on exit. The SDK shuts
    itself down at interpreter exit (``atexit``).
    """
    from langfuse import Langfuse

    client = Langfuse(
        public_key=config.public_key, secret_key=config.secret_key, base_url=config.host
    )
    try:
        yield client
    finally:
        await asyncio.to_thread(client.flush)


# ─── Read pacing ───────────────────────────────────────────────────────
def retry_wait(headers: Mapping[str, str] | None, attempt: int) -> float:
    """Seconds to wait before a retry: Retry-After when given, else backoff."""
    header = (headers or {}).get("Retry-After", (headers or {}).get("retry-after", "")).strip()
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

    async def _backoff(
        self, status: int | None, headers: Mapping[str, str] | None, attempt: int
    ) -> bool:
        """Sleep before a retry; False when out of attempts or budget."""
        wait = retry_wait(headers, attempt)
        if attempt + 1 >= RETRY_ATTEMPTS or wait > self._budget_s:
            return False
        self._budget_s -= wait
        logger.info("Langfuse %s — retrying in %.0f s", status, wait)
        await self._sleep(wait)
        return True

    async def call(self, read: Callable[[], Awaitable[T]]) -> T:
        """Run ``read`` with pacing + retry; re-raises the SDK error when it still fails."""
        api_error = _api_error()
        async with self._slots:
            attempt = 0
            while True:
                await self._wait_turn()
                try:
                    return await read()
                except api_error as exc:
                    status = getattr(exc, "status_code", None)
                    if status not in _RETRYABLE:
                        raise
                    if not await self._backoff(status, getattr(exc, "headers", None), attempt):
                        raise
                attempt += 1


# ─── Reads ─────────────────────────────────────────────────────────────
def _as_json(model: Any) -> dict[str, Any]:
    """An SDK model as the public API's camelCase JSON (what ``_eval_metrics`` reads)."""
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, mode="json")
    return dict(model)


def _trace_name_filter(target: TraceTarget) -> str:
    """v2 ``filter``: observations of the trace named ``pipeline:<videoSummaryId>``.

    Filters on the trace name, not on a root observation: an SDK v2 trace (before
    the P0a deploy) has no root observation of that name.
    """
    conditions: list[dict[str, Any]] = [
        {
            "type": "string",
            "column": "traceName",
            "operator": "=",
            "value": f"pipeline:{target.video_summary_id}",
        }
    ]
    if target.submitted_at is not None:
        since = (target.submitted_at - _SINCE_SKEW).isoformat().replace("+00:00", "Z")
        conditions.append(
            {"type": "datetime", "column": "startTime", "operator": ">=", "value": since}
        )
    return json.dumps(conditions)


async def _find_trace_id(client: Any, target: TraceTarget, pacer: ReadPacer) -> str | None:
    """Pre-P0b reports only: the trace named ``pipeline:<videoSummaryId>`` (newest wins)."""
    page = await pacer.call(
        lambda: client.async_api.observations.get_many(
            filter=_trace_name_filter(target),
            fields="core",
            limit=50,
            request_options=_NO_SDK_RETRY,
        )
    )
    if not page.data:
        return None
    newest = max(page.data, key=lambda o: str(getattr(o, "start_time", "") or ""))
    return newest.trace_id


async def fetch_signals(
    client: Any,
    target: TraceTarget,
    pacer: ReadPacer | None = None,
    trace_id: str | None = None,
) -> TraceSignals | None:
    """Signals of the target's trace (or ``trace_id``), ``None`` when it cannot be found."""
    pacer = pacer or ReadPacer()
    trace_id = trace_id or target.trace_id or await _find_trace_id(client, target, pacer)
    if not trace_id:
        return None
    observations = await pacer.call(
        lambda: client.async_api.observations.get_many(
            trace_id=trace_id, fields=_OBSERVATION_FIELDS, limit=1000, request_options=_NO_SDK_RETRY
        )
    )
    scores = await pacer.call(
        lambda: client.async_api.scores_v3.get_many_v3(
            trace_id=trace_id, name="faithfulness", limit=100, request_options=_NO_SDK_RETRY
        )
    )
    trace = {
        "observations": [_as_json(o) for o in observations.data],
        "scores": [_as_json(s) for s in scores.data],
    }
    return TraceSignals(
        trace_id=trace_id,
        faithfulness=extract_faithfulness(trace),
        probe_format=extract_probe_format(trace),
    )


async def _fetch_round(
    client: Any,
    pending: dict[str, TraceTarget],
    found: dict[str, TraceSignals],
    pacer: ReadPacer,
) -> None:
    api_error = _api_error()
    for golden_id, target in pending.items():
        known = found[golden_id].trace_id if golden_id in found else None
        try:
            signals = await fetch_signals(client, target, pacer, trace_id=known)
        except (api_error, httpx.HTTPError) as exc:
            logger.warning("Langfuse read failed for %s: %s", golden_id, exc)
            continue
        if signals is not None:
            found[golden_id] = signals


async def collect_signals(
    client: Any,
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
def _post_scores(client: Any, item: RunItem, run_name: str) -> None:
    """Queue the eval's metrics as ``golden.*`` scores (sent on ``make_client`` exit)."""
    for name, value in item.scores.items():
        if value is None:
            continue
        client.create_score(
            trace_id=item.trace_id,
            name=f"golden.{name}",
            value=float(value),
            data_type="NUMERIC",
            comment=f"golden eval run {run_name}",
        )


async def publish_dataset_run(client: Any, run_name: str, items: list[RunItem]) -> int:
    """Link each item's pipeline trace into dataset run ``run_name``; return items linked."""
    api_error = _api_error()
    linked = 0
    for item in items:
        if not item.trace_id:
            logger.warning("Run upload skipped for %s: no pipeline trace", item.dataset_item_id)
            continue
        try:
            _post_scores(client, item, run_name)
            await client.async_api.dataset_run_items.create(
                run_name=run_name,
                dataset_item_id=item.dataset_item_id,
                trace_id=item.trace_id,
                metadata=item.metadata,
            )
            linked += 1
        except (api_error, httpx.HTTPError) as exc:
            logger.warning("Run upload failed for %s: %s", item.dataset_item_id, exc)
    logger.info("Langfuse dataset run %s: %d/%d items linked", run_name, linked, len(items))
    return linked
