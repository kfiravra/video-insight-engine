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
    """Aggregated summary of one pipeline run (keyed by request_id)."""

    request_id: str | None
    video_id: str | None
    video_summary_id: str | None
    user_id: str | None
    first_call: str
    last_call: str
    total_cost_usd: float
    call_count: int
    regen_ordinal: int | None  # 1 = first run for this video, 2 = first regen, etc.
    langfuse_url: str | None = None  # Direct link to this run's Langfuse trace, if resolvable.
    # Partial-result flag from the videoSummaryCache doc (dropped extraction
    # batches / critical coverage). True = degraded, False = clean doc,
    # None = no doc resolvable (legacy rows / missing video_summary_id).
    degraded: bool | None = None
    calls: list[RunCallSummary]


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
async def usage_for_video(video_id: str = Path(..., min_length=1, max_length=64)):
    """Detailed usage for a specific video: metadata, summary, feature breakdown, and raw calls."""
    cache_key = f"video:{video_id}"
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

    video_doc, summary_results, feature_results, raw_calls = await asyncio.gather(
        # isLatest mirrors _VIDEO_LOOKUP_STAGE — without it Mongo may hand back
        # an older version row for a youtubeId that was regenerated.
        db.videoSummaryCache.find_one(
            {"youtubeId": video_id, "isLatest": True},
            _VIDEO_METADATA_PROJECTION,
        ),
        db.llm_usage.aggregate(summary_pipeline).to_list(1),
        db.llm_usage.aggregate(feature_pipeline).to_list(50),
        db.llm_usage.find(
            {"video_id": video_id},
            {"prompt_preview": 0},
        )
        .sort("timestamp", -1)
        .limit(50)
        .to_list(50),
    )

    video = _format_video_metadata(video_doc)
    summary = _format_usage_summary(summary_results)
    by_feature = [
        {"feature": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in feature_results
    ]

    result = {
        "video": video,
        "summary": summary,
        "by_feature": by_feature,
        "calls": [_serialize_doc(r) for r in raw_calls],
    }
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


async def _degraded_by_summary_id(db: Any, summary_ids: set[str]) -> dict[str, bool]:
    """Map video_summary_id → degraded flag from the videoSummaryCache docs.

    Ids that are not valid ObjectIds (or have no doc) are simply absent from
    the returned map — the caller renders those runs with ``degraded=None``.
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
        {"degraded": 1},
    )
    docs = await cursor.to_list(len(oids))
    return {oids[doc["_id"]]: bool(doc.get("degraded")) for doc in docs}


def _build_run_summary(run_group: dict, calls: list[dict], ordinal: int | None) -> RunSummary:
    """Assemble a RunSummary from an aggregation result + child call documents."""
    first = run_group.get("first_call")
    last = run_group.get("last_call")
    return RunSummary(
        request_id=run_group.get("_id"),
        video_id=run_group.get("video_id"),
        video_summary_id=run_group.get("video_summary_id"),
        user_id=run_group.get("user_id"),
        first_call=first.isoformat() if isinstance(first, datetime) else str(first or ""),
        last_call=last.isoformat() if isinstance(last, datetime) else str(last or ""),
        total_cost_usd=float(run_group.get("total_cost_usd") or 0),
        call_count=int(run_group.get("call_count") or 0),
        regen_ordinal=ordinal,
        calls=[_build_run_call(c) for c in calls],
    )


@router.get("/by-run", response_model=list[RunSummary])
async def usage_by_run(
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[RunSummary]:
    """Pipeline runs grouped by request_id, newest first.

    Rows without a request_id are grouped under the sentinel key
    ``None`` (displayed as 'unattributed (legacy)' in the UI).
    Each run includes a regen_ordinal (1 = first run for that video,
    2 = first regeneration, etc.) derived by ranking runs per video by
    their first_call timestamp.

    NOTE: ``regen_ordinal`` is relative to the selected day window — the
    ranking only counts runs whose timestamp is within ``days`` (see the
    ``timestamp >= cutoff(days)`` match in the rank pipeline). Runs older
    than ``days`` are not counted, so ordinals may understate the true
    all-time regen count near the window boundary (e.g. a run that is the
    3rd all-time regeneration may report ordinal 1 if its two predecessors
    fall outside the window).
    """
    cache_key = f"by-run:{days}:{limit}:{offset}"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()

    # Step 1: group llm_usage rows by request_id → one document per run.
    group_pipeline: list[dict] = [
        {"$match": {"timestamp": {"$gte": _cutoff(days)}}},
        {
            "$group": {
                "_id": {"$ifNull": ["$request_id", None]},
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
        _cache[cache_key] = []
        return []

    # Step 2: compute regen_ordinals for runs that have a video_id.
    # For each unique video_id in the page, rank runs by first_call ascending.
    video_ids_on_page = {g["video_id"] for g in run_groups if g.get("video_id")}
    ordinal_map: dict[tuple[str | None, str | None], int] = {}  # (request_id, video_id) → ordinal

    if video_ids_on_page:
        rank_pipeline: list[dict] = [
            {
                "$match": {
                    "timestamp": {"$gte": _cutoff(days)},
                    "video_id": {"$in": list(video_ids_on_page)},
                }
            },
            {
                "$group": {
                    "_id": {"$ifNull": ["$request_id", None]},
                    "video_id": {"$first": "$video_id"},
                    "first_call": {"$min": "$timestamp"},
                }
            },
            {"$sort": {"video_id": 1, "first_call": 1}},
        ]
        all_video_runs = await db.llm_usage.aggregate(rank_pipeline).to_list(1000)

        # Build per-video ordered list → ordinal = position + 1
        runs_by_video: dict[str, list[str | None]] = defaultdict(list)
        for r in all_video_runs:
            vid = r.get("video_id")
            if vid:
                runs_by_video[vid].append(r["_id"])

        for vid, req_ids in runs_by_video.items():
            for pos, req_id in enumerate(req_ids):
                ordinal_map[(req_id, vid)] = pos + 1

    # Step 3: fetch child call documents for every run in parallel.
    async def _fetch_calls_for_run(request_id: str | None) -> list[dict]:
        """Fetch up to 50 call documents for a single run.

        Filtering on ``request_id`` alone mirrors how the group stage keys runs
        (``$ifNull: [request_id, None]``) — so the expanded calls are the same
        population the run's ``call_count`` was summed over. A ``None``
        ``request_id`` matches the single legacy bucket (rows missing/null
        ``request_id``); matching by ``video_id`` there would drop the other
        legacy videos' calls and contradict ``call_count``.
        """
        cursor = (
            db.llm_usage.find({"request_id": request_id}, {"prompt_preview": 0})
            .sort("timestamp", 1)
            .limit(50)
        )
        return await cursor.to_list(50)

    call_lists = await asyncio.gather(*[_fetch_calls_for_run(g.get("_id")) for g in run_groups])

    # Step 4: assemble response models.
    results: list[RunSummary] = []
    for group, calls in zip(run_groups, call_lists, strict=True):
        req_id = group.get("_id")
        vid_id = group.get("video_id")
        ordinal = ordinal_map.get((req_id, vid_id))
        results.append(_build_run_summary(group, calls, ordinal))

    # Step 5: best-effort Langfuse deep-links (one batched call; no-op if unconfigured).
    request_ids = [r.request_id for r in results if r.request_id]
    trace_urls = await langfuse.map_request_ids_to_urls(request_ids)
    for run in results:
        if run.request_id:
            run.langfuse_url = trace_urls.get(run.request_id)

    # Step 6: degraded-run badges — one batched videoSummaryCache lookup for
    # the page's summary ids. Runs without a resolvable doc stay None.
    summary_ids = {r.video_summary_id for r in results if r.video_summary_id}
    degraded_map = await _degraded_by_summary_id(db, summary_ids)
    for run in results:
        if run.video_summary_id:
            run.degraded = degraded_map.get(run.video_summary_id)

    _cache[cache_key] = results
    return results
