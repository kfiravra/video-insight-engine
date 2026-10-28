"""Tests for tiers route validation and access control."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import src.routes.tiers as tiers_module
from src.main import app
from src.routes.tiers import PLAN_DISTRIBUTION_PIPELINE


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_tier_distribution_requires_auth():
    """Tier distribution endpoint should reject unauthenticated requests."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tiers/distribution")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_tier_distribution_rejects_wrong_key():
    """Tier distribution endpoint should reject wrong API key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/tiers/distribution",
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert resp.status_code == 401


MACHINE = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture
def plan_counts(monkeypatch: pytest.MonkeyPatch):
    """Answer the distribution aggregate with canned groups; the 60 s cache is cleared around it."""

    def install(groups: list[dict]) -> MagicMock:
        db = MagicMock()
        db.users.aggregate.return_value.to_list = AsyncMock(return_value=groups)
        monkeypatch.setattr(tiers_module, "get_database", lambda: db)
        tiers_module._cache.clear()
        return db

    yield install
    tiers_module._cache.clear()


async def _distribution() -> list[dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/tiers/distribution", headers=MACHINE)
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.anyio
async def test_plan_distribution_counts_users_per_plan_with_percentages(plan_counts):
    plan_counts([{"_id": "free", "count": 3}, {"_id": "pro", "count": 1}])

    assert await _distribution() == [
        {"tier": "free", "count": 3, "percentage": 75.0},
        {"tier": "pro", "count": 1, "percentage": 25.0},
    ]


@pytest.mark.anyio
async def test_plan_distribution_is_empty_when_there_are_no_users(plan_counts):
    plan_counts([])

    assert await _distribution() == []


class TestPlanDistributionPipeline:
    def test_should_start_from_every_user_and_join_the_billing_account_by_owner(self):
        lookup = PLAN_DISTRIBUTION_PIPELINE[0]["$lookup"]

        assert lookup["from"] == "billingAccounts"
        assert lookup["let"] == {"uid": {"$toString": "$_id"}}
        conditions = lookup["pipeline"][0]["$match"]["$expr"]["$and"]
        assert {"$eq": ["$ownerType", "user"]} in conditions
        assert {"$eq": ["$ownerId", "$$uid"]} in conditions

    def test_should_group_by_the_account_plan_and_call_a_user_with_no_account_free(self):
        group = PLAN_DISTRIBUTION_PIPELINE[1]["$group"]

        assert group["_id"] == {"$ifNull": [{"$arrayElemAt": ["$_account.plan.id", 0]}, "free"]}

    def test_should_not_read_the_old_tier_field(self):
        assert "$tier" not in repr(PLAN_DISTRIBUTION_PIPELINE)
