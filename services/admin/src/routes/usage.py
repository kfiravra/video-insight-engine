"""Usage analytics endpoints for LLM cost monitoring."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from src.dependencies import get_database
from src.routes._helpers import cutoff as _cutoff
from src.services import langfuse


def _serialize_value(v: object) -> object:
    """Serialize a single MongoDB value for JSON response."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return _serialize_doc(v)
    if isinstance(v, list):
        return [_serialize_value(item) for item in v]
    return v


def _serialize_doc(doc: dict) -> dict:
    """Serialize MongoDB document for JSON response.

    Recursively converts ObjectId to str, datetime to ISO format string,
    and handles nested dicts and lists.
    """
    return {k: _serialize_value(v) for k, v in doc.items()}


router = APIRouter(prefix="/usage", tags=["usage"])

# Latency percentiles on llm_usage.duration_ms. ``$percentile`` needs MongoDB 7+
# (the stack pins mongo:7); ``approximate`` is the only method Mongo offers and
# is exact enough for an ops dashboard.
LATENCY_PERCENTILES = [0.5, 0.95]
LATENCY_PERCENTILE_STAGE = {
    "$percentile": {"input": "$duration_ms", "p": LATENCY_PERCENTILES, "method": "approximate"}
}


def _unpack_latency(row: dict) -> dict:
    """Turn the ``latency_percentiles`` array into ``p50_duration_ms``/``p95_duration_ms``."""
    values = row.pop("latency_percentiles", None) or []
    for pct, value in zip(LATENCY_PERCENTILES, values, strict=False):
        row[f"p{int(pct * 100)}_duration_ms"] = value
    return row


# 30-second cache for expensive aggregations
_cache = TTLCache(maxsize=64, ttl=30)


def invalidate_video_cache(video_id: str) -> None:
    """Drop the cached detail for one video (after a global purge)."""
    _cache.pop(f"video:{video_id}", None)


MAX_DAYS = 90
# Pipeline attempts the video detail shows (newest first, within MAX_DAYS).
VIDEO_RUNS_LIMIT = 50

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
    regen_ordinal: int | None  # 1 = first run for this video, 2 = first regen, etc.
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
    langfuse_url: str | None = None  # The Langfuse session ``chat-<session_id>``.


@router.get("/stats")
async def usage_stats(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    feature: str | None = None,
    provider: str | None = None,
    service: str | None = None,
):
    """Aggregated usage totals with optional filters."""
    cache_key = f"stats:{days}:{feature}:{provider}:{service}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    match: dict[str, Any] = {"timestamp": {"$gte": _cutoff(days)}}
    if feature:
        match["feature"] = feature
    if provider:
        match["provider"] = provider
    if service:
        match["service"] = service

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total_calls": {"$sum": 1},
                "total_tokens_in": {"$sum": "$tokens_in"},
                "total_tokens_out": {"$sum": "$tokens_out"},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "avg_duration_ms": {"$avg": "$duration_ms"},
                "latency_percentiles": LATENCY_PERCENTILE_STAGE,
                "success_count": {"$sum": {"$cond": ["$success", 1, 0]}},
                "failure_count": {"$sum": {"$cond": ["$success", 0, 1]}},
            }
        },
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(1)
    if results:
        _unpack_latency(results[0])
    data = (
        results[0]
        if results
        else {
            "total_calls": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_cost_usd": 0,
            "avg_duration_ms": 0,
            "success_count": 0,
            "failure_count": 0,
        }
    )
    data.pop("_id", None)
    # Add computed total_tokens field
    data["total_tokens"] = (data.get("total_tokens_in") or 0) + (data.get("total_tokens_out") or 0)
    _cache[cache_key] = data
    return data


@router.get("/by-output-type")
async def usage_by_output_type(days: int = Query(30, ge=1, le=MAX_DAYS)) -> list[dict[str, object]]:
    """LLM cost/calls aggregated by video outputType."""
    cache_key = f"by-output-type:{days}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    # Group by video_id first to reduce lookups from N usage docs to M unique videos
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}, "video_id": {"$ne": None}}},
        {
            "$group": {
                "_id": "$video_id",
                "cost_usd": {"$sum": "$cost_usd"},
                "calls": {"$sum": 1},
                "tokens": {
                    "$sum": {
                        "$add": [{"$ifNull": ["$tokens_in", 0]}, {"$ifNull": ["$tokens_out", 0]}]
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "videoSummaryCache",
                "localField": "_id",
                "foreignField": "youtubeId",
                "as": "_video",
                # Filter to the latest version only — avoids fan-out when multiple
                # versioned cache docs exist for the same youtubeId (cross-user dedup).
                "pipeline": [
                    {"$match": {"isLatest": True}},
                    {"$project": {"outputType": 1}},
                    {"$limit": 1},
                ],
            }
        },
        {"$unwind": {"path": "$_video", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {"$ifNull": ["$_video.outputType", "summary"]},
                "cost_usd": {"$sum": "$cost_usd"},
                "calls": {"$sum": "$calls"},
                "tokens": {"$sum": "$tokens"},
            }
        },
        {"$sort": {"cost_usd": -1}},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(50)
    data = [
        {"output_type": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results
    ]
    _cache[cache_key] = data
    return data


@router.get("/daily")
async def usage_daily(days: int = Query(30, ge=1, le=MAX_DAYS)):
    """Daily breakdown for time-series charts."""
    cache_key = f"daily:{days}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
                "tokens_in": {"$sum": "$tokens_in"},
                "tokens_out": {"$sum": "$tokens_out"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(MAX_DAYS)
    data = [{"date": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results]
    _cache[cache_key] = data
    return data


@router.get("/by-feature")
async def usage_by_feature(days: int = Query(30, ge=1, le=MAX_DAYS)):
    """Cost per feature, sorted descending."""
    cache_key = f"by-feature:{days}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}}},
        {
            "$group": {
                "_id": "$feature",
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
                "avg_duration_ms": {"$avg": "$duration_ms"},
                "latency_percentiles": LATENCY_PERCENTILE_STAGE,
            }
        },
        {"$sort": {"cost_usd": -1}},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(50)
    data = [
        {"feature": r["_id"], **{k: v for k, v in _unpack_latency(r).items() if k != "_id"}}
        for r in results
    ]
    _cache[cache_key] = data
    return data


@router.get("/by-model")
async def usage_by_model(days: int = Query(30, ge=1, le=MAX_DAYS)):
    """Cost per model, sorted descending."""
    cache_key = f"by-model:{days}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}}},
        {
            "$group": {
                "_id": "$model",
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
                "tokens_in": {"$sum": "$tokens_in"},
                "tokens_out": {"$sum": "$tokens_out"},
                "avg_duration_ms": {"$avg": "$duration_ms"},
                "latency_percentiles": LATENCY_PERCENTILE_STAGE,
            }
        },
        {"$sort": {"cost_usd": -1}},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(50)
    data = [
        {"model": r["_id"], **{k: v for k, v in _unpack_latency(r).items() if k != "_id"}}
        for r in results
    ]
    _cache[cache_key] = data
    return data


@router.get("/by-service")
async def usage_by_service(days: int = Query(30, ge=1, le=MAX_DAYS)):
    """Cost per service."""
    cache_key = f"by-service:{days}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}}},
        {
            "$group": {
                "_id": "$service",
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
            }
        },
        {"$sort": {"cost_usd": -1}},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(20)
    data = [{"service": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results]
    _cache[cache_key] = data
    return data


# videoSummaryCache fields the usage endpoints read. Shared by the by-video
# $lookup and the per-video find_one so the two projections cannot drift.
# transcriptMeta is stamped by the summarizer for every run that reached the
# transcript phase; rows served from the Redis response cache lack it.
_VIDEO_METADATA_PROJECTION: dict[str, int] = {
    "title": 1,
    "channel": 1,
    "duration": 1,
    "thumbnailUrl": 1,
    "status": 1,
    "context.category": 1,
    "processedAt": 1,
    "transcriptMeta.source": 1,
    "transcriptMeta.type": 1,
    "transcriptMeta.outcome": 1,
}


def _format_video_metadata(doc: dict | None) -> dict | None:
    """Format a videoSummaryCache document into API-friendly metadata."""
    if not doc:
        return None
    transcript = doc.get("transcriptMeta") or {}
    return {
        "title": doc.get("title"),
        "channel": doc.get("channel"),
        "duration": doc.get("duration"),
        "thumbnail_url": doc.get("thumbnailUrl"),
        "status": doc.get("status"),
        "category": (doc.get("context") or {}).get("category"),
        "processed_at": doc["processedAt"].isoformat() if doc.get("processedAt") else None,
        "transcript_source": transcript.get("source"),
        "transcript_type": transcript.get("type"),
        "transcript_outcome": transcript.get("outcome"),
    }


def _format_video_usage_item(r: dict) -> dict:
    """Map an aggregation result (with $lookup metadata) to API-friendly dict."""
    v = r.get("_v") or {}
    item = {
        "video_id": r["_id"],
        "calls": r["calls"],
        "cost_usd": r["cost_usd"],
        "tokens_in": r["tokens_in"],
        "tokens_out": r["tokens_out"],
        "first_call": r["first_call"].isoformat() if r.get("first_call") else None,
        "last_call": r["last_call"].isoformat() if r.get("last_call") else None,
    }
    metadata = _format_video_metadata(v)
    if metadata:
        item.update(metadata)
    return item


_VIDEO_LOOKUP_STAGE: list[dict] = [
    {
        "$lookup": {
            "from": "videoSummaryCache",
            "localField": "_id",
            "foreignField": "youtubeId",
            "as": "_v",
            # isLatest:True keeps only one doc per youtubeId — prevents fan-out
            # when multiple versioned cache docs exist (cross-user dedup).
            "pipeline": [
                {"$match": {"isLatest": True}},
                {"$project": _VIDEO_METADATA_PROJECTION},
                {"$limit": 1},
            ],
        }
    },
    {"$unwind": {"path": "$_v", "preserveNullAndEmptyArrays": True}},
]


@router.get("/by-video")
async def usage_by_video(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(20, ge=1, le=100),
):
    """Top videos by total cost, enriched with video metadata."""
    cache_key = f"by-video:{days}:{limit}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}, "video_id": {"$ne": None}}},
        {
            "$group": {
                "_id": "$video_id",
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
                "tokens_in": {"$sum": "$tokens_in"},
                "tokens_out": {"$sum": "$tokens_out"},
                "first_call": {"$min": "$timestamp"},
                "last_call": {"$max": "$timestamp"},
            }
        },
        {"$sort": {"cost_usd": -1}},
        {"$limit": limit},
        *_VIDEO_LOOKUP_STAGE,
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(limit)
    data = [_format_video_usage_item(r) for r in results]
    _cache[cache_key] = data
    return data


def _format_usage_summary(results: list[dict]) -> dict:
    """Format aggregated usage summary, converting datetimes to ISO strings."""
    summary = (
        results[0]
        if results
        else {
            "total_calls": 0,
            "total_cost_usd": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "avg_duration_ms": 0,
            "first_call": None,
            "last_call": None,
        }
    )
    summary.pop("_id", None)
    if summary.get("first_call"):
        summary["first_call"] = summary["first_call"].isoformat()
    if summary.get("last_call"):
        summary["last_call"] = summary["last_call"].isoformat()
    return summary


@router.get("/video/{video_id}")
async def usage_for_video(
    video_id: str = Path(..., min_length=1, max_length=64),
    flat: bool = Query(False),
):
    """Detailed usage for one video (its YouTube id): metadata, summary, feature breakdown, runs.

    ``runs`` are the video's pipeline attempts with their calls (D10), as
    ``/usage/by-run`` returns them. ``?flat=1`` adds the old flat ``calls`` list
    (the newest 50 rows of every service) for one release.
    """
    cache_key = f"video:{video_id}:{flat}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()

    # Run all independent queries in parallel
    summary_pipeline = [
        {"$match": {"video_id": video_id}},
        {
            "$group": {
                "_id": None,
                "total_calls": {"$sum": 1},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "total_tokens_in": {"$sum": "$tokens_in"},
                "total_tokens_out": {"$sum": "$tokens_out"},
                "avg_duration_ms": {"$avg": "$duration_ms"},
                "first_call": {"$min": "$timestamp"},
                "last_call": {"$max": "$timestamp"},
            }
        },
    ]
    feature_pipeline = [
        {"$match": {"video_id": video_id}},
        {
            "$group": {
                "_id": "$feature",
                "calls": {"$sum": 1},
                "cost_usd": {"$sum": "$cost_usd"},
                "tokens_in": {"$sum": "$tokens_in"},
                "tokens_out": {"$sum": "$tokens_out"},
                "avg_duration_ms": {"$avg": "$duration_ms"},
            }
        },
        {"$sort": {"cost_usd": -1}},
    ]

    async def _flat_calls() -> list[dict]:
        if not flat:
            return []
        cursor = db.llm_usage.find({"video_id": video_id}, {"prompt_preview": 0})
        return await cursor.sort("timestamp", -1).limit(50).to_list(50)

    video_doc, summary_results, feature_results, runs, raw_calls = await asyncio.gather(
        # isLatest mirrors _VIDEO_LOOKUP_STAGE — without it Mongo may hand back
        # an older version row for a youtubeId that was regenerated.
        db.videoSummaryCache.find_one(
            {"youtubeId": video_id, "isLatest": True},
            _VIDEO_METADATA_PROJECTION,
        ),
        db.llm_usage.aggregate(summary_pipeline).to_list(1),
        db.llm_usage.aggregate(feature_pipeline).to_list(50),
        _run_summaries(db, days=MAX_DAYS, limit=VIDEO_RUNS_LIMIT, offset=0, video_id=video_id),
        _flat_calls(),
    )

    video = _format_video_metadata(video_doc)
    summary = _format_usage_summary(summary_results)
    by_feature = [
        {"feature": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in feature_results
    ]

    result: dict[str, Any] = {
        "video": video,
        "summary": summary,
        "by_feature": by_feature,
        "runs": [run.model_dump() for run in runs],
    }
    if flat:
        result["calls"] = [_serialize_doc(r) for r in raw_calls]
    _cache[cache_key] = result
    return result


@router.get("/anomalies")
async def usage_anomalies(
    threshold_usd: float = Query(0.50, ge=0),
    days: int = Query(7, ge=1, le=MAX_DAYS),
):
    """Expensive calls above threshold."""
    db = get_database()
    cursor = (
        db.llm_usage.find(
            {"cost_usd": {"$gt": threshold_usd}, "timestamp": {"$gte": _cutoff(days)}},
            {"prompt_preview": 0},
        )
        .sort("cost_usd", -1)
        .limit(50)
    )
    results = await cursor.to_list(50)
    return [_serialize_doc(r) for r in results]


@router.get("/recent")
async def usage_recent(
    limit: int = Query(20, ge=1, le=100),
    before_id: str | None = None,
):
    """Cursor-based pagination of recent calls."""
    # Validate cursor before touching DB
    query: dict = {}
    if before_id:
        try:
            query["_id"] = {"$lt": ObjectId(before_id)}
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid cursor ID format")

    db = get_database()

    cursor = db.llm_usage.find(query).sort("_id", -1).limit(limit)
    results = await cursor.to_list(limit)
    return [_serialize_doc(r) for r in results]


@router.get("/duplicates")
async def usage_duplicates(
    days: int = Query(7, ge=1, le=MAX_DAYS),
    min_count: int = Query(3, ge=2),
):
    """Group by prompt_hash to find duplicate prompts."""
    db = get_database()
    pipeline = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}, "prompt_hash": {"$ne": ""}}},
        {
            "$group": {
                "_id": "$prompt_hash",
                "count": {"$sum": 1},
                "total_cost_usd": {"$sum": "$cost_usd"},
                "model": {"$first": "$model"},
                "feature": {"$first": "$feature"},
                "prompt_preview": {"$first": "$prompt_preview"},
            }
        },
        {"$match": {"count": {"$gte": min_count}}},
        {"$sort": {"total_cost_usd": -1}},
        {"$limit": 20},
    ]
    results = await db.llm_usage.aggregate(pipeline).to_list(20)
    return [
        {"prompt_hash": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results
    ]


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


def _attach_cache_fields(
    results: list[RunSummary], rows: dict[str, dict], trace_ids: dict[int, str | None] | None = None
) -> None:
    """Fill each run's degraded badge, runs[] fields and Langfuse links from its cache doc.

    The trace link is the stored trace id of the run's ``runs[]`` entry: the entry
    with its run id, or (legacy rows without one) the newest entry with its
    request id (P-6). ``trace_ids`` (index → the ledger rows' ``trace_id``) is the
    fallback when the entry is gone. A run without a doc gets only that fallback.
    """
    for index, run in enumerate(results):
        ledger_trace = (trace_ids or {}).get(index)
        row = rows.get(run.video_summary_id) if run.video_summary_id else None
        if run.video_summary_id:
            run.session_url = langfuse.session_url(f"pipeline-{run.video_summary_id}")
        if row is None:
            run.langfuse_url = langfuse.trace_url(ledger_trace)
            continue
        run.degraded, run.degraded_reasons = bool(row.get("degraded")), row.get("degradedReasons")
        entry = langfuse.run_entry(row.get("runs"), run_id=run.run_id, request_id=run.request_id)
        _attach_run_entry(run, entry, ledger_trace)


def _build_run_summary(run_group: dict, calls: list[dict], ordinal: int | None) -> RunSummary:
    """Assemble a RunSummary from an aggregation result + child call documents.

    ``_id`` is the run key (run id, or the request id on legacy rows); a group
    from the run-keyed aggregation also names ``run_id`` and ``request_id``.
    """
    first = run_group.get("first_call")
    last = run_group.get("last_call")
    request_id = run_group["request_id"] if "request_id" in run_group else run_group.get("_id")
    bypass = run_group.get("bypass")
    return RunSummary(
        run_id=run_group.get("run_id"),
        request_id=request_id,
        video_id=run_group.get("video_id"),
        video_summary_id=run_group.get("video_summary_id"),
        user_id=run_group.get("user_id"),
        first_call=first.isoformat() if isinstance(first, datetime) else str(first or ""),
        last_call=last.isoformat() if isinstance(last, datetime) else str(last or ""),
        total_cost_usd=float(run_group.get("total_cost_usd") or 0),
        call_count=int(run_group.get("call_count") or 0),
        regen_ordinal=ordinal,
        attempt=run_group.get("attempt"),
        pipeline_version=run_group.get("pipeline_version"),
        bypass=bool(bypass) if bypass is not None else None,
        calls=[_build_run_call(c) for c in calls],
    )


def _run_rows_match(days: int, video_id: str | None) -> dict[str, Any]:
    """The summarizer's ledger rows in the window, optionally of one video (its YouTube id)."""
    match: dict[str, Any] = {"timestamp": {"$gte": _cutoff(days)}, "service": "summarizer"}
    if video_id:
        match["video_id"] = video_id
    return match


def _calls_filter(group: dict) -> dict[str, Any]:
    """The ledger rows one run group was summed over (the same population as ``call_count``).

    A run-keyed group matches its ``run_id``. A legacy group (no run id) matches
    its ``request_id`` on rows without one; ``None`` is the single unattributed
    bucket (rows with neither id), never "every legacy video".
    """
    if group.get("run_id"):
        return {"service": "summarizer", "run_id": group["run_id"]}
    return {"service": "summarizer", "run_id": None, "request_id": group.get("_id")}


async def _regen_ordinals(
    db: Any, days: int, run_groups: list[dict]
) -> dict[tuple[str | None, str | None], int]:
    """(run key, video_id) → 1-based position of the run among its video's runs in the window."""
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
                "_id": _RUN_KEY,
                "video_id": {"$first": "$video_id"},
                "first_call": {"$min": "$timestamp"},
            }
        },
        {"$sort": {"video_id": 1, "first_call": 1}},
    ]
    all_video_runs = await db.llm_usage.aggregate(rank_pipeline).to_list(1000)

    runs_by_video: dict[str, list[str | None]] = defaultdict(list)
    for r in all_video_runs:
        vid = r.get("video_id")
        if vid:
            runs_by_video[vid].append(r["_id"])
    return {
        (key, vid): pos + 1 for vid, keys in runs_by_video.items() for pos, key in enumerate(keys)
    }


async def _run_summaries(
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
            db.llm_usage.find(_calls_filter(group), {"prompt_preview": 0})
            .sort("timestamp", 1)
            .limit(50)
        )
        return await cursor.to_list(50)

    call_lists = await asyncio.gather(*[_fetch_calls(g) for g in run_groups])

    # Step 4: response models; step 5: one batched videoSummaryCache lookup for the
    # page's rows (degraded badge, runs[] entry fields, Langfuse links from stored
    # ids — no Langfuse call; links need LANGFUSE_PROJECT_ID).
    results = [
        _build_run_summary(group, calls, ordinal_map.get((group.get("_id"), group.get("video_id"))))
        for group, calls in zip(run_groups, call_lists, strict=True)
    ]
    summary_ids = {r.video_summary_id for r in results if r.video_summary_id}
    trace_ids = {i: g.get("trace_id") for i, g in enumerate(run_groups)}
    _attach_cache_fields(results, await _cache_rows_by_summary_id(db, summary_ids), trace_ids)
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
    results = await _run_summaries(
        get_database(), days=days, limit=limit, offset=offset, video_id=video_id
    )
    _cache[cache_key] = results
    return results


def _build_session_summary(group: dict) -> SessionSummary:
    """Assemble a SessionSummary from one by-session aggregation group."""
    first = group.get("first_call")
    last = group.get("last_call")
    session_id = group.get("_id")
    return SessionSummary(
        session_id=session_id,
        user_id=group.get("user_id"),
        video_summary_id=group.get("video_summary_id"),
        turns=int(group.get("turns") or 0),
        tool_llm_calls=int(group.get("tool_llm_calls") or 0),
        call_count=int(group.get("call_count") or 0),
        total_cost_usd=float(group.get("total_cost_usd") or 0),
        first_call=first.isoformat() if isinstance(first, datetime) else str(first or ""),
        last_call=last.isoformat() if isinstance(last, datetime) else str(last or ""),
        langfuse_url=langfuse.session_url(f"chat-{session_id}") if session_id else None,
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
