"""Langfuse I/O for the golden eval (public REST API over httpx).

Reads: per video, the pipeline trace ``pipeline:<videoSummaryId>`` created
after the eval's POST, then its ``faithfulness`` score and its
``classifier`` generation output (for the ``expectedFormat`` assertion).

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from _eval_assertions import TraceSignals
from _eval_metrics import extract_classifier_format, extract_faithfulness

logger = logging.getLogger("run_eval.langfuse")

_DEFAULT_HOST = "https://cloud.langfuse.com"
# Scores land up to ~25 s after the pipeline ends (bounded faithfulness
# drain) and Langfuse ingestion is asynchronous, so the read-back retries.
SIGNAL_ROUNDS = 5
SIGNAL_ROUND_WAIT_S = 30.0
# Tolerates clock skew between the runner and the prod box.
_SINCE_SKEW = timedelta(minutes=2)


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
    submitted_at: datetime


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


# ─── Reads ─────────────────────────────────────────────────────────────
async def _find_trace_id(client: httpx.AsyncClient, target: TraceTarget) -> str | None:
    since = target.submitted_at - _SINCE_SKEW
    resp = await client.get(
        "/api/public/traces",
        params={
            "name": f"pipeline:{target.video_summary_id}",
            "fromTimestamp": since.isoformat().replace("+00:00", "Z"),
            "orderBy": "timestamp.desc",
            "limit": 1,
        },
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    return data[0].get("id") if data else None


async def fetch_signals(client: httpx.AsyncClient, target: TraceTarget) -> TraceSignals | None:
    """Return the signals of the newest matching trace, or ``None`` when absent."""
    trace_id = await _find_trace_id(client, target)
    if not trace_id:
        return None
    resp = await client.get(f"/api/public/traces/{trace_id}")
    resp.raise_for_status()
    trace = resp.json()
    return TraceSignals(
        trace_id=trace_id,
        faithfulness=extract_faithfulness(trace),
        classifier_format=extract_classifier_format(trace),
    )


async def _fetch_round(
    client: httpx.AsyncClient, pending: dict[str, TraceTarget], found: dict[str, TraceSignals]
) -> None:
    for golden_id, target in pending.items():
        try:
            signals = await fetch_signals(client, target)
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
) -> dict[str, TraceSignals]:
    """Read signals for every target, re-polling those without a faithfulness score."""
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
        await _fetch_round(client, pending, found)
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
