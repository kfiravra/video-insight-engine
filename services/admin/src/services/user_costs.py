"""Per-user spend for the admin Users views, read from ``llm_usage``.

The LLM ledger of record is ``llm_usage``: one row per call, attributed to the
user who triggered it (``user_id``, a string). Since the dollar gate went away
(minutes billing, P3.2) nothing writes ``userCosts`` any more, so the Users list
and the per-user cost timeline aggregate ``llm_usage`` instead.

``userCosts`` stays as read-only history, for two things:

- ``creditAdjustmentUsd``, the retired dollar credit, which exists only there;
- the days before ``llm_usage`` rows carried a ``user_id`` (before P0.4 a run
  streamed over HTTP was never attributed), so a long range still shows the
  spend that period was recorded under. For a day that has attributed
  ``llm_usage`` rows those rows win and the history row for that day only
  contributes its credit; the two are never added, so nothing is counted twice.

A user is listed when they have spend in the range, a minutes account
(``billingAccounts``), or signed up in the range: a new user has neither
spend nor an account yet, and the admin still has to find them to grant
minutes. Anyone else is idle and not shown, as before.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

DEFAULT_PLAN = "free"

# Upper bounds on one read, far above any real fleet: (user, day) rows over 90
# days, and users or accounts held in memory at once.
_DAY_ROW_CAP = 50_000
_USER_CAP = 5_000

_HEX_ID = re.compile(r"[0-9a-fA-F]{24}")


@dataclass
class DayCost:
    """One user's spend on one UTC day."""

    total_cost_usd: float = 0.0
    video_count: int = 0
    credit_adjustment_usd: float = 0.0

    @property
    def effective_usd(self) -> float:
        return self.total_cost_usd + self.credit_adjustment_usd


@dataclass(frozen=True)
class UserCostSummary:
    """One user's spend over a range, with who they are and their plan."""

    user_id: str
    email: str | None
    name: str | None
    plan: str
    total_cost_usd: float
    video_count: int
    credit_adjustment_usd: float
    days: int

    @property
    def effective_usd(self) -> float:
        return self.total_cost_usd + self.credit_adjustment_usd


# ─── Time window ───


def window_start(days: int) -> datetime:
    """Midnight UTC of the first day of the trailing ``days`` days (today included)."""
    first = datetime.now(UTC) - timedelta(days=max(days - 1, 0))
    return first.replace(hour=0, minute=0, second=0, microsecond=0)


def date_key(moment: datetime) -> str:
    """``YYYY-MM-DD`` of a UTC moment, the key ``userCosts`` rows use."""
    return moment.strftime("%Y-%m-%d")


# ─── Reads ───


def usage_by_day_pipeline(since: datetime, user_id: str | None) -> list[dict[str, Any]]:
    """Group attributed ``llm_usage`` rows by user and UTC day.

    ``runs`` counts distinct pipeline runs (``request_id`` of summarizer rows),
    the closest thing to the old per-video count; assistant chat calls add cost
    but not videos.
    """
    user_match: Any = user_id if user_id else {"$type": "string", "$ne": ""}
    summarizer_run = {
        "$cond": [
            {
                "$and": [
                    {"$eq": ["$service", "summarizer"]},
                    {"$eq": [{"$type": "$request_id"}, "string"]},
                ]
            },
            "$request_id",
            "$$REMOVE",
        ]
    }
    day = {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp", "timezone": "UTC"}}
    return [
        {"$match": {"timestamp": {"$gte": since}, "user_id": user_match}},
        {
            "$group": {
                "_id": {"user": "$user_id", "date": day},
                "costUsd": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
                "runs": {"$addToSet": summarizer_run},
            }
        },
        {
            "$project": {
                "_id": 0,
                "user": "$_id.user",
                "date": "$_id.date",
                "costUsd": 1,
                "runs": {"$size": "$runs"},
            }
        },
    ]


def merge_days(
    usage_rows: Iterable[Mapping[str, Any]], history_rows: Iterable[Mapping[str, Any]]
) -> dict[str, dict[str, DayCost]]:
    """``{user_id: {date: DayCost}}``: history first, then attributed usage over it.

    A day with attributed ``llm_usage`` rows takes its cost and video count from
    them; a day without keeps the history row's. The credit adjustment only ever
    comes from history.
    """
    merged: dict[str, dict[str, DayCost]] = defaultdict(dict)
    for doc in history_rows:
        day = merged[str(doc["userId"])].setdefault(doc["date"], DayCost())
        day.total_cost_usd += float(doc.get("totalCostUsd") or 0)
        day.video_count += int(doc.get("videoCount") or 0)
        day.credit_adjustment_usd += float(doc.get("creditAdjustmentUsd") or 0)
    for row in usage_rows:
        day = merged[row["user"]].setdefault(row["date"], DayCost())
        day.total_cost_usd = float(row["costUsd"] or 0)
        day.video_count = int(row["runs"] or 0)
    return dict(merged)


async def cost_days(
    db: AsyncIOMotorDatabase, days: int, user_id: str | None = None
) -> dict[str, dict[str, DayCost]]:
    """Daily spend per user over the trailing window; one user when ``user_id`` is given."""
    since = window_start(days)
    history_query: dict[str, Any] = {"date": {"$gte": date_key(since)}}
    if user_id:
        history_query["userId"] = ObjectId(user_id)
    usage_rows, history_rows = await asyncio.gather(
        db.llm_usage.aggregate(usage_by_day_pipeline(since, user_id)).to_list(_DAY_ROW_CAP),
        db.userCosts.find(history_query).to_list(_DAY_ROW_CAP),
    )
    return merge_days(usage_rows, history_rows)


async def plan_ids(db: AsyncIOMotorDatabase) -> dict[str, str]:
    """``{user_id: plan id}`` of every user account; a user with no row is on ``free``."""
    accounts = await db.billingAccounts.find(
        {"ownerType": "user"}, {"ownerId": 1, "plan.id": 1}
    ).to_list(_USER_CAP)
    return {str(a["ownerId"]): (a.get("plan") or {}).get("id") or DEFAULT_PLAN for a in accounts}


async def plan_id(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """The plan of one user, from ``billingAccounts.plan.id``; ``free`` with no account row."""
    account = await db.billingAccounts.find_one(
        {"ownerType": "user", "ownerId": user_id}, {"plan.id": 1}
    )
    return ((account or {}).get("plan") or {}).get("id") or DEFAULT_PLAN


async def _profiles(
    db: AsyncIOMotorDatabase, user_ids: Iterable[str], signed_up_since: datetime
) -> dict[str, Mapping[str, Any]]:
    """email and name of ``user_ids`` plus everyone who signed up since ``signed_up_since``."""
    oids = [ObjectId(uid) for uid in user_ids if _HEX_ID.fullmatch(uid)]
    docs = await db.users.find(
        {"$or": [{"_id": {"$in": oids}}, {"createdAt": {"$gte": signed_up_since}}]},
        {"email": 1, "name": 1},
    ).to_list(_USER_CAP)
    return {str(doc["_id"]): doc for doc in docs}


# ─── The list ───


def summarize(days: Mapping[str, DayCost]) -> tuple[float, int, float, int]:
    """Total cost, videos, credit adjustment and number of days with a row."""
    return (
        sum(d.total_cost_usd for d in days.values()),
        sum(d.video_count for d in days.values()),
        sum(d.credit_adjustment_usd for d in days.values()),
        len(days),
    )


async def user_cost_summaries(db: AsyncIOMotorDatabase, days: int) -> list[UserCostSummary]:
    """Every listed user, highest effective cost first (ties by email, then id)."""
    by_user, plans = await asyncio.gather(cost_days(db, days), plan_ids(db))
    # A spend row whose id is not a user id (a service or test value) cannot be opened in the UI.
    candidates = {uid for uid in by_user if _HEX_ID.fullmatch(uid)} | set(plans)
    profiles = await _profiles(db, candidates, window_start(days))
    summaries = []
    for uid in candidates | set(profiles):
        total, videos, credit, active_days = summarize(by_user.get(uid, {}))
        profile = profiles.get(uid) or {}
        summaries.append(
            UserCostSummary(
                user_id=uid,
                email=profile.get("email"),
                name=profile.get("name"),
                plan=plans.get(uid, DEFAULT_PLAN),
                total_cost_usd=total,
                video_count=videos,
                credit_adjustment_usd=credit,
                days=active_days,
            )
        )
    summaries.sort(key=lambda s: (-s.effective_usd, (s.email or "").lower(), s.user_id))
    return summaries
