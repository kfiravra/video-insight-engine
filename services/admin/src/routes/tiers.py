"""Plan distribution analytics endpoint (served as ``/tiers/distribution``)."""

from cachetools import TTLCache
from fastapi import APIRouter
from pydantic import BaseModel

from src.dependencies import get_database

router = APIRouter(prefix="/tiers", tags=["tiers"])

_cache = TTLCache(maxsize=8, ttl=60)


class TierItem(BaseModel):
    # The account plan id. The field keeps its pre-minutes name, ``tier``, so a UI bundle built
    # before the rename keeps working.
    tier: str
    count: int
    percentage: float


# Every user once, grouped by their billing account's plan; no account row means ``free``.
# vie-api stores ``billingAccounts.ownerId`` as an ObjectId: compared with ``users._id`` as is.
PLAN_DISTRIBUTION_PIPELINE: list[dict] = [
    {
        "$lookup": {
            "from": "billingAccounts",
            "let": {"uid": "$_id"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$ownerType", "user"]},
                                {"$eq": ["$ownerId", "$$uid"]},
                            ]
                        }
                    }
                },
                {"$project": {"plan.id": 1}},
            ],
            "as": "_account",
        }
    },
    {
        "$group": {
            "_id": {"$ifNull": [{"$arrayElemAt": ["$_account.plan.id", 0]}, "free"]},
            "count": {"$sum": 1},
        }
    },
    {"$sort": {"count": -1}},
]


@router.get("/distribution")
async def tier_distribution() -> list[TierItem]:
    """User count per plan (``billingAccounts.plan.id``; a user with no account is ``free``)."""
    cache_key = "tier-distribution"
    if cache_key in _cache:
        return _cache[cache_key]

    db = get_database()
    results = await db.users.aggregate(PLAN_DISTRIBUTION_PIPELINE).to_list(20)
    total = sum(r["count"] for r in results)
    data = [
        TierItem(
            tier=r["_id"],
            count=r["count"],
            percentage=round((r["count"] / total) * 100, 1) if total > 0 else 0,
        )
        for r in results
    ]
    _cache[cache_key] = data
    return data
