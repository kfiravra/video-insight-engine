"""Usage by pipeline run and by chat session (observability D10, plan 2.1).

``/usage/by-run`` groups the summarizer's ledger rows by ``run_id`` (legacy rows
by ``request_id``) and joins each attempt's ``videoSummaryCache.runs[]`` entry;
``/usage/by-session`` groups the assistant's rows by the web's chat session id.
Langfuse links come from stored ids (``src.services.langfuse``), never from a
Langfuse call. ``run_summaries`` also feeds the video detail (``usage.py``).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.dependencies import get_database
from src.routes._helpers import USAGE_MAX_DAYS as MAX_DAYS
from src.routes._helpers import cutoff as _cutoff
from src.routes._helpers import usage_cache as _cache
from src.services import langfuse

router = APIRouter(prefix="/usage", tags=["usage"])

# ─── Response models ───


class RunCallSummary(BaseModel):
    """One LLM call belonging to a pipeline run."""

    id: str
    feature: str | None
    model: str | None
    cost_usd: float
    tokens_in: int | None
    tokens_out: int | None
    duration_ms: float | None
    success: bool | None
    timestamp: str
    # Cost-unit discriminator: "tokens" (default) or "audio_seconds" for
    # transcription rows. Lets the UI render "N min audio" instead of "0 tokens".
    unit: str | None = None
    audio_seconds: float | None = None


class RunSummary(BaseModel):
    """Aggregated summary of one pipeline attempt.

    Keyed by ``run_id`` (D10); rows written before P1 have none and group by
    their ``request_id`` instead (``run_id`` is then ``None``).
    """

    run_id: str | None = None
    request_id: str | None
    video_id: str | None
    video_summary_id: str | None
    user_id: str | None
    first_call: str
    last_call: str
    total_cost_usd: float
    call_count: int
    # 1 = the video's first request in the window, 2 = its first regeneration, …;
    # retries of one request (fresh run ids, same request id) share its ordinal.
    regen_ordinal: int | None
    # The attempt's runs[] entry (P-2), the ledger's ids as the fallback; None on
    # legacy rows that predate runs[].
    attempt: int | None = None
    pipeline_version: str | None = None
    bypass: bool | None = None
    path: str | None = None  # worker | sse | dev
    status: str | None = None  # dispatched | running | completed | failed | cache_hit
    retry_of: str | None = None
    langfuse_url: str | None = None  # This attempt's Langfuse trace, from its stored trace id.
    session_url: str | None = None  # The row's Langfuse session (every attempt of the video row).
    # The rows' own trace id: the link's fallback when the runs[] entry is gone (not sent).
    ledger_trace_id: str | None = Field(default=None, exclude=True)
    # Degraded-run flag from the videoSummaryCache doc: a partial extraction
    # (dropped batches / failed field group / critical coverage) or a failed
    # frame download. True = degraded, False = clean doc, None = no doc
    # resolvable (legacy rows / missing video_summary_id).
    degraded: bool | None = None
    # Why ("partial_extraction", "media_download_failed"); None = not recorded
    # (rows from before the summarizer wrote reasons, or no doc).
    degraded_reasons: list[str] | None = None
    calls: list[RunCallSummary]


class SessionSummary(BaseModel):
    """One chat session's assistant usage (D10), keyed by the web's session id.

    Rows written before P1 carry no ``session_id``; they share one bucket with
    ``session_id=None`` (shown as "unattributed").
    """

    session_id: str | None
    user_id: str | None
    # The video chat's summary id; None for the library chat (and the legacy bucket).
    video_summary_id: str | None
    turns: int  # distinct gateway requests: one per chat turn or action
    # LLM calls made inside tools; a tool that calls no model is not in the ledger.
    tool_llm_calls: int
    call_count: int
    total_cost_usd: float
    first_call: str
    last_call: str
    session_url: str | None = None  # The Langfuse session ``chat-<session_id>``.


# ─── Run rows ───


def _build_run_call(raw: dict) -> RunCallSummary:
    """Map a raw llm_usage document to a RunCallSummary."""
    ts = raw.get("timestamp")
    return RunCallSummary(
        id=str(raw["_id"]),
        feature=raw.get("feature"),
        model=raw.get("model"),
        cost_usd=float(raw.get("cost_usd") or 0),
        tokens_in=raw.get("tokens_in"),
        tokens_out=raw.get("tokens_out"),
        duration_ms=raw.get("duration_ms"),
        success=raw.get("success"),
        timestamp=ts.isoformat() if isinstance(ts, datetime) else str(ts or ""),
        unit=raw.get("unit"),
        audio_seconds=raw.get("audio_seconds"),
    )


# The fields of a runs[] entry the run rows read (P-2).
_RUN_ENTRY_PROJECTION = {
    f"runs.{field}": 1
    for field in (
        "runId",
        "requestId",
        "traceId",
        "attempt",
        "pipelineVersion",
        "bypass",
        "path",
        "status",
        "retryOf",
    )
}
# A pipeline attempt's ledger key: its run id (D9), the request id on rows from before it.
_RUN_KEY = {"$ifNull": ["$run_id", "$request_id"]}
YOUTUBE_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"


async def _cache_rows_by_summary_id(db: Any, summary_ids: set[str]) -> dict[str, dict]:
    """Map video_summary_id → its videoSummaryCache doc (degraded fields + runs[] entries).

    Ids that are not valid ObjectIds (or have no doc) are simply absent from
    the returned map — the caller renders those runs with ``degraded=None``
    and no Langfuse link.
    """
    oids: dict[ObjectId, str] = {}
    for sid in summary_ids:
        try:
            oids[ObjectId(sid)] = sid
        except (InvalidId, TypeError):
            continue
    if not oids:
        return {}
    cursor = db.videoSummaryCache.find(
        {"_id": {"$in": list(oids)}},
        {"degraded": 1, "degradedReasons": 1, **_RUN_ENTRY_PROJECTION},
    )
    docs = await cursor.to_list(len(oids))
    return {oids[doc["_id"]]: doc for doc in docs}


def _attach_run_entry(run: RunSummary, entry: dict[str, Any] | None, trace_id: str | None) -> None:
    """Fill the attempt's fields from its runs[] entry; the ledger's values stay as the fallback."""
    if entry is not None:
        run.attempt = entry.get("attempt", run.attempt)
        run.pipeline_version = entry.get("pipelineVersion", run.pipeline_version)
        if entry.get("bypass") is not None:
            run.bypass = bool(entry["bypass"])
        run.path = entry.get("path")
        run.status = entry.get("status")
        run.retry_of = entry.get("retryOf")
        trace_id = entry.get("traceId") or trace_id
    run.langfuse_url = langfuse.trace_url(trace_id)


def _attach_cache_fields(results: list[RunSummary], rows: dict[str, dict]) -> None:
    """Fill each run's degraded badge, runs[] fields and Langfuse links from its cache doc.

    The trace link is the stored trace id of the run's ``runs[]`` entry: the entry
    with its run id, or (legacy rows without one) the newest entry with its
    request id (P-6). The rows' own ``trace_id`` is the fallback when the entry is
    gone. A run without a doc gets only that fallback.
    """
    for run in results:
        row = rows.get(run.video_summary_id) if run.video_summary_id else None
        if run.video_summary_id:
            run.session_url = langfuse.session_url(f"pipeline-{run.video_summary_id}")
        if row is None:
            run.langfuse_url = langfuse.trace_url(run.ledger_trace_id)
            continue
        run.degraded, run.degraded_reasons = bool(row.get("degraded")), row.get("degradedReasons")
        entry = langfuse.run_entry(row.get("runs"), run_id=run.run_id, request_id=run.request_id)
        _attach_run_entry(run, entry, run.ledger_trace_id)


def _build_run_summary(run_group: dict, calls: list[dict], ordinal: int | None) -> RunSummary:
    """Assemble a RunSummary from an aggregation result + child call documents.

    ``_id`` is the run key (run id, or the request id on legacy rows). The
    unattributed bucket (neither id) mixes videos and users, so it names none.
    """
    first = run_group.get("first_call")
    last = run_group.get("last_call")
    bypass = run_group.get("bypass")
    bucket = run_group.get("_id") is None
    return RunSummary(
        run_id=run_group.get("run_id"),
        request_id=run_group.get("request_id"),
        video_id=None if bucket else run_group.get("video_id"),
        video_summary_id=None if bucket else run_group.get("video_summary_id"),
        user_id=None if bucket else run_group.get("user_id"),
        first_call=first.isoformat() if isinstance(first, datetime) else str(first or ""),
        last_call=last.isoformat() if isinstance(last, datetime) else str(last or ""),
        total_cost_usd=float(run_group.get("total_cost_usd") or 0),
        call_count=int(run_group.get("call_count") or 0),
        regen_ordinal=ordinal,
        attempt=run_group.get("attempt"),
        pipeline_version=run_group.get("pipeline_version"),
        bypass=bool(bypass) if bypass is not None else None,
        ledger_trace_id=run_group.get("trace_id"),
        calls=[_build_run_call(c) for c in calls],
    )


def _run_rows_match(days: int, video_id: str | None) -> dict[str, Any]:
    """The summarizer's ledger rows in the window, optionally of one video (its YouTube id)."""
    match: dict[str, Any] = {"timestamp": {"$gte": _cutoff(days)}, "service": "summarizer"}
    if video_id:
        match["video_id"] = video_id
    return match


def _calls_filter(group: dict, days: int, video_id: str | None) -> dict[str, Any]:
    """The ledger rows one run group was summed over (the same population as ``call_count``).

    The group's window (and video, when the report is narrowed to one) always
    applies. A run-keyed group then matches its ``run_id``; a legacy group (no
    run id) matches its ``request_id`` on rows without one, and ``None`` is the
    single unattributed bucket (rows with neither id).
    """
    match = _run_rows_match(days, video_id)
    if group.get("run_id"):
        return {**match, "run_id": group["run_id"]}
    return {**match, "run_id": None, "request_id": group.get("_id")}


async def _regen_ordinals(
    db: Any, days: int, run_groups: list[dict]
) -> dict[tuple[str | None, str | None], int]:
    """(request id, video_id) → 1-based position of the request among its video's requests.

    A regeneration is a new request; a retry keeps the request id (and gets a
    fresh run id), so retries share their request's ordinal.
    """
    video_ids_on_page = {g["video_id"] for g in run_groups if g.get("video_id")}
    if not video_ids_on_page:
        return {}
    rank_pipeline: list[dict] = [
        {
            "$match": {
                **_run_rows_match(days, None),
                "video_id": {"$in": list(video_ids_on_page)},
            }
        },
        {
            "$group": {
                "_id": {"request_id": "$request_id", "video_id": "$video_id"},
                "first_call": {"$min": "$timestamp"},
            }
        },
        {"$sort": {"_id.video_id": 1, "first_call": 1}},
    ]
    requests = await db.llm_usage.aggregate(rank_pipeline).to_list(1000)

    by_video: dict[str, list[str | None]] = defaultdict(list)
    for r in requests:
        key = r.get("_id") or {}
        if key.get("video_id") and key.get("request_id"):
            by_video[key["video_id"]].append(key["request_id"])
    return {
        (request_id, vid): pos + 1
        for vid, request_ids in by_video.items()
        for pos, request_id in enumerate(request_ids)
    }


async def run_summaries(
    db: Any, *, days: int, limit: int, offset: int, video_id: str | None = None
) -> list[RunSummary]:
    """One page of pipeline attempts, newest first (the by-run report and a video's runs)."""
    # Step 1: group the summarizer's llm_usage rows by run → one document per attempt.
    group_pipeline: list[dict] = [
        {"$match": _run_rows_match(days, video_id)},
        {
            "$group": {
                "_id": _RUN_KEY,
                "run_id": {"$first": "$run_id"},
                "request_id": {"$first": "$request_id"},
                "trace_id": {"$first": "$trace_id"},
                "attempt": {"$max": "$attempt"},
                "pipeline_version": {"$first": "$pipeline_version"},
                "bypass": {"$first": "$bypass"},
                "video_id": {"$first": "$video_id"},
                "video_summary_id": {"$first": "$video_summary_id"},
                "user_id": {"$first": "$user_id"},
                "first_call": {"$min": "$timestamp"},
                "last_call": {"$max": "$timestamp"},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "call_count": {"$sum": 1},
            }
        },
        {"$sort": {"first_call": -1}},
        {"$skip": offset},
        {"$limit": limit},
    ]
    run_groups = await db.llm_usage.aggregate(group_pipeline).to_list(limit)
    if not run_groups:
        return []

    # Step 2: regen ordinals per video; step 3: each run's calls (≤ 50), in parallel.
    ordinal_map = await _regen_ordinals(db, days, run_groups)

    async def _fetch_calls(group: dict) -> list[dict]:
        cursor = (
            db.llm_usage.find(_calls_filter(group, days, video_id), {"prompt_preview": 0})
            .sort("timestamp", 1)
            .limit(50)
        )
        return await cursor.to_list(50)

    call_lists = await asyncio.gather(*[_fetch_calls(g) for g in run_groups])

    # Step 4: response models; step 5: one batched videoSummaryCache lookup for the
    # page's rows (degraded badge, runs[] entry fields, Langfuse links from stored
    # ids — no Langfuse call; links need LANGFUSE_PROJECT_ID).
    results = [
        _build_run_summary(
            group, calls, ordinal_map.get((group.get("request_id"), group.get("video_id")))
        )
        for group, calls in zip(run_groups, call_lists, strict=True)
    ]
    summary_ids = {r.video_summary_id for r in results if r.video_summary_id}
    _attach_cache_fields(results, await _cache_rows_by_summary_id(db, summary_ids))
    return results


@router.get("/by-run", response_model=list[RunSummary])
async def usage_by_run(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    video_id: str | None = Query(None, pattern=YOUTUBE_ID_PATTERN),
) -> list[RunSummary]:
    """Pipeline attempts grouped by run id (D10), newest first; the summarizer's rows only.

    Rows written before P1 have no ``run_id`` and group by ``request_id``; rows
    with neither share the sentinel ``None`` key ('unattributed (legacy)' in the
    UI). ``video_id`` (a YouTube id) narrows the report to one video. Each run
    includes a regen_ordinal (1 = first run for that video, 2 = first
    regeneration, etc.) derived by ranking runs per video by their first_call.

    NOTE: ``regen_ordinal`` is relative to the selected day window — the
    ranking only counts runs whose timestamp is within ``days``. Runs older
    than ``days`` are not counted, so ordinals may understate the true
    all-time regen count near the window boundary.
    """
    cache_key = f"by-run:{days}:{limit}:{offset}:{video_id}"
    if cache_key in _cache:
        return _cache[cache_key]
    results = await run_summaries(
        get_database(), days=days, limit=limit, offset=offset, video_id=video_id
    )
    _cache[cache_key] = results
    return results


def _build_session_summary(group: dict) -> SessionSummary:
    """Assemble a SessionSummary from one by-session aggregation group."""
    first = group.get("first_call")
    last = group.get("last_call")
    session_id = group.get("_id")
    # The unattributed bucket mixes users and surfaces, so it names neither.
    return SessionSummary(
        session_id=session_id,
        user_id=group.get("user_id") if session_id else None,
        video_summary_id=group.get("video_summary_id") if session_id else None,
        turns=int(group.get("turns") or 0),
        tool_llm_calls=int(group.get("tool_llm_calls") or 0),
        call_count=int(group.get("call_count") or 0),
        total_cost_usd=float(group.get("total_cost_usd") or 0),
        first_call=first.isoformat() if isinstance(first, datetime) else str(first or ""),
        last_call=last.isoformat() if isinstance(last, datetime) else str(last or ""),
        session_url=langfuse.session_url(f"chat-{session_id}") if session_id else None,
    )


@router.get("/by-session", response_model=list[SessionSummary])
async def usage_by_session(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[SessionSummary]:
    """Chat sessions (D10): the assistant's ledger rows grouped by ``session_id``.

    Newest activity first. A turn is one gateway request (``request_id``), so an
    action the chat triggered counts as a turn too. Rows from before P1 carry no
    session and share the ``None`` bucket.
    """
    cache_key = f"by-session:{days}:{limit}:{offset}"
    if cache_key in _cache:
        return _cache[cache_key]

    is_tool_call = {
        "$eq": [{"$substrCP": [{"$ifNull": ["$feature", ""]}, 0, 15]}, "assistant:tool:"]
    }
    pipeline: list[dict] = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}, "service": "assistant"}},
        {
            "$group": {
                "_id": {"$ifNull": ["$session_id", None]},
                "user_id": {"$first": "$user_id"},
                "video_summary_id": {"$max": "$video_summary_id"},
                "request_ids": {"$addToSet": "$request_id"},
                "tool_llm_calls": {"$sum": {"$cond": [is_tool_call, 1, 0]}},
                "call_count": {"$sum": 1},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "first_call": {"$min": "$timestamp"},
                "last_call": {"$max": "$timestamp"},
            }
        },
        {"$addFields": {"turns": {"$size": {"$setDifference": ["$request_ids", [None]]}}}},
        {"$project": {"request_ids": 0}},
        {"$sort": {"last_call": -1}},
        {"$skip": offset},
        {"$limit": limit},
    ]
    groups = await get_database().llm_usage.aggregate(pipeline).to_list(limit)
    results = [_build_session_summary(g) for g in groups]
    _cache[cache_key] = results
    return results
