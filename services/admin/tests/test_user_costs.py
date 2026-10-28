"""Per-user spend from ``llm_usage`` (``src.services.user_costs``): reads, merge, list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from src.services.user_costs import (
    DayCost,
    merge_days,
    plan_id,
    plan_ids,
    summarize,
    usage_by_day_pipeline,
    user_cost_summaries,
    window_start,
)

ALICE = "a" * 24
BOB = "b" * 24
CAROL = "c" * 24


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def usage(user: str, date: str, cost: float, runs: int = 1) -> dict[str, Any]:
    return {"user": user, "date": date, "costUsd": cost, "runs": runs}


def history(user: str, date: str, total: float = 0.0, videos: int = 0, credit: float = 0.0):
    return {
        "userId": ObjectId(user),
        "date": date,
        "totalCostUsd": total,
        "videoCount": videos,
        "creditAdjustmentUsd": credit,
    }


def fake_db(
    *,
    usage_rows: list[dict[str, Any]] | None = None,
    history_rows: list[dict[str, Any]] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    users: list[dict[str, Any]] | None = None,
    account: dict[str, Any] | None = None,
) -> MagicMock:
    """Motor: aggregate()/find() are sync and return cursors; to_list()/find_one() are async."""
    db = MagicMock()
    db.llm_usage.aggregate.return_value.to_list = AsyncMock(return_value=usage_rows or [])
    db.userCosts.find.return_value.to_list = AsyncMock(return_value=history_rows or [])
    db.billingAccounts.find.return_value.to_list = AsyncMock(return_value=accounts or [])
    db.billingAccounts.find_one = AsyncMock(return_value=account)
    db.users.find.return_value.to_list = AsyncMock(return_value=users or [])
    return db


def user_doc(user: str, email: str | None, name: str | None = None) -> dict[str, Any]:
    return {"_id": ObjectId(user), "email": email, "name": name}


def account_row(user: str, plan: str | None) -> dict[str, Any]:
    return {"ownerId": user, "plan": {"id": plan} if plan else {}}


# ─── The window ───


class TestWindowStart:
    def test_should_be_midnight_utc(self) -> None:
        start = window_start(7)

        assert (start.tzinfo, start.hour, start.minute, start.second, start.microsecond) == (
            UTC,
            0,
            0,
            0,
            0,
        )

    @pytest.mark.parametrize("days", [1, 7, 90])
    def test_should_reach_back_days_minus_one_days_so_today_is_included(self, days: int) -> None:
        def midnight() -> datetime:
            return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        before = midnight()
        start = window_start(days)
        after = midnight()  # the clock may cross midnight between the two reads

        assert start in {before - timedelta(days=days - 1), after - timedelta(days=days - 1)}


# ─── The query ───


class TestUsagePipeline:
    since = datetime(2026, 10, 22, tzinfo=UTC)

    def test_should_read_only_rows_attributed_to_a_user_in_the_window(self) -> None:
        match = usage_by_day_pipeline(self.since, None)[0]["$match"]

        assert match == {
            "timestamp": {"$gte": self.since},
            "user_id": {"$type": "string", "$ne": ""},
        }

    def test_should_read_one_users_rows_by_their_exact_id(self) -> None:
        match = usage_by_day_pipeline(self.since, ALICE)[0]["$match"]

        assert match["user_id"] == ALICE

    def test_should_group_by_user_and_utc_day(self) -> None:
        group = usage_by_day_pipeline(self.since, None)[1]["$group"]["_id"]

        assert group["user"] == "$user_id"
        assert group["date"]["$dateToString"] == {
            "format": "%Y-%m-%d",
            "date": "$timestamp",
            "timezone": "UTC",
        }

    def test_should_count_runs_from_summarizer_rows_only(self) -> None:
        runs = usage_by_day_pipeline(self.since, None)[1]["$group"]["runs"]["$addToSet"]

        assert {"$eq": ["$service", "summarizer"]} in runs["$cond"][0]["$and"]
        assert runs["$cond"][1] == "$request_id"


# ─── The merge ───


class TestMergeDays:
    def test_should_take_a_days_cost_and_videos_from_usage(self) -> None:
        merged = merge_days([usage(ALICE, "2026-10-28", 0.25, runs=2)], [])

        assert merged[ALICE]["2026-10-28"] == DayCost(0.25, 2, 0.0)

    def test_should_keep_a_history_only_day(self) -> None:
        merged = merge_days([], [history(ALICE, "2026-07-08", total=1.5, videos=3)])

        assert merged[ALICE]["2026-07-08"] == DayCost(1.5, 3, 0.0)

    def test_should_not_add_history_to_a_day_that_has_usage(self) -> None:
        merged = merge_days(
            [usage(ALICE, "2026-10-28", 0.25, runs=1)],
            [history(ALICE, "2026-10-28", total=0.9, videos=4)],
        )

        assert (
            merged[ALICE]["2026-10-28"].total_cost_usd,
            merged[ALICE]["2026-10-28"].video_count,
        ) == (0.25, 1)

    def test_should_keep_the_history_credit_on_a_day_that_has_usage(self) -> None:
        merged = merge_days(
            [usage(ALICE, "2026-10-28", 0.25)], [history(ALICE, "2026-10-28", credit=-2.0)]
        )

        assert merged[ALICE]["2026-10-28"].credit_adjustment_usd == -2.0

    def test_should_keep_users_apart(self) -> None:
        merged = merge_days([usage(ALICE, "2026-10-28", 1.0), usage(BOB, "2026-10-28", 2.0)], [])

        assert (
            merged[ALICE]["2026-10-28"].total_cost_usd,
            merged[BOB]["2026-10-28"].total_cost_usd,
        ) == (1.0, 2.0)

    def test_should_treat_missing_history_fields_as_zero(self) -> None:
        merged = merge_days([], [{"userId": ObjectId(ALICE), "date": "2026-07-08"}])

        assert merged[ALICE]["2026-07-08"] == DayCost()

    def test_should_total_a_users_days(self) -> None:
        days = {"2026-10-27": DayCost(1.0, 1, -0.5), "2026-10-28": DayCost(2.0, 2, 0.0)}

        assert summarize(days) == (3.0, 3, -0.5, 2)


# ─── Plans ───


class TestPlans:
    @pytest.mark.anyio
    async def test_should_map_every_account_owner_to_its_plan(self) -> None:
        db = fake_db(accounts=[account_row(ALICE, "pro"), account_row(BOB, "free")])

        assert await plan_ids(db) == {ALICE: "pro", BOB: "free"}

    @pytest.mark.anyio
    async def test_should_read_a_user_account_by_owner_type_and_id(self) -> None:
        db = fake_db()

        await plan_ids(db)

        assert db.billingAccounts.find.call_args.args[0] == {"ownerType": "user"}

    @pytest.mark.anyio
    async def test_should_call_an_account_with_no_plan_free(self) -> None:
        assert await plan_ids(fake_db(accounts=[account_row(ALICE, None)])) == {ALICE: "free"}

    @pytest.mark.anyio
    async def test_should_read_one_users_plan(self) -> None:
        db = fake_db(account=account_row(ALICE, "pro"))

        assert await plan_id(db, ALICE) == "pro"
        assert db.billingAccounts.find_one.call_args.args[0] == {
            "ownerType": "user",
            "ownerId": ALICE,
        }

    @pytest.mark.anyio
    async def test_should_fall_back_to_free_when_the_user_has_no_account_row(self) -> None:
        assert await plan_id(fake_db(account=None), ALICE) == "free"


# ─── The list ───


class TestUserCostSummaries:
    @pytest.mark.anyio
    async def test_should_total_a_users_spend_from_llm_usage(self) -> None:
        db = fake_db(
            usage_rows=[usage(ALICE, "2026-10-27", 0.1), usage(ALICE, "2026-10-28", 0.25, runs=2)],
            users=[user_doc(ALICE, "alice@example.com", "Alice")],
        )

        [row] = await user_cost_summaries(db, 7)

        assert (
            row.user_id,
            row.email,
            row.name,
            row.total_cost_usd,
            row.video_count,
            row.days,
        ) == (
            ALICE,
            "alice@example.com",
            "Alice",
            pytest.approx(0.35),
            3,
            2,
        )

    @pytest.mark.anyio
    async def test_should_show_the_plan_from_the_billing_account(self) -> None:
        db = fake_db(
            usage_rows=[usage(ALICE, "2026-10-28", 0.1), usage(BOB, "2026-10-28", 0.2)],
            accounts=[account_row(ALICE, "pro")],
            users=[user_doc(ALICE, "a@x"), user_doc(BOB, "b@x")],
        )

        plans = {row.user_id: row.plan for row in await user_cost_summaries(db, 7)}

        assert plans == {ALICE: "pro", BOB: "free"}

    @pytest.mark.anyio
    async def test_should_list_a_user_with_an_account_and_no_spend(self) -> None:
        db = fake_db(
            accounts=[account_row(CAROL, "free")], users=[user_doc(CAROL, "carol@example.com")]
        )

        [row] = await user_cost_summaries(db, 7)

        assert (row.user_id, row.total_cost_usd, row.video_count, row.days) == (CAROL, 0.0, 0, 0)

    @pytest.mark.anyio
    async def test_should_list_a_user_who_signed_up_in_the_range_with_no_spend_or_account(
        self,
    ) -> None:
        db = fake_db(users=[user_doc(BOB, "new@example.com")])

        [row] = await user_cost_summaries(db, 7)

        assert (row.user_id, row.email, row.plan, row.total_cost_usd) == (
            BOB,
            "new@example.com",
            "free",
            0.0,
        )

    @pytest.mark.anyio
    async def test_should_ask_for_users_created_since_the_window_started(self) -> None:
        db = fake_db(usage_rows=[usage(ALICE, "2026-10-28", 0.1)], users=[user_doc(ALICE, "a@x")])

        await user_cost_summaries(db, 7)

        query = db.users.find.call_args.args[0]
        assert query["$or"][0] == {"_id": {"$in": [ObjectId(ALICE)]}}
        assert query["$or"][1] == {"createdAt": {"$gte": window_start(7)}}

    @pytest.mark.anyio
    async def test_should_not_list_spend_attributed_to_an_id_that_is_not_a_user_id(self) -> None:
        db = fake_db(
            usage_rows=[usage("not-a-user-id", "2026-10-28", 5.0), usage(ALICE, "2026-10-28", 0.1)]
        )

        assert [row.user_id for row in await user_cost_summaries(db, 7)] == [ALICE]

    @pytest.mark.anyio
    async def test_should_keep_a_user_whose_record_is_gone_with_no_email(self) -> None:
        db = fake_db(usage_rows=[usage(ALICE, "2026-10-28", 0.1)], users=[])

        [row] = await user_cost_summaries(db, 7)

        assert (row.user_id, row.email, row.name) == (ALICE, None, None)

    @pytest.mark.anyio
    async def test_should_sort_by_effective_cost_highest_first(self) -> None:
        db = fake_db(
            usage_rows=[
                usage(ALICE, "2026-10-28", 1.0),
                usage(BOB, "2026-10-28", 3.0),
                usage(CAROL, "2026-10-28", 2.0),
            ],
            users=[user_doc(ALICE, "a@x"), user_doc(BOB, "b@x"), user_doc(CAROL, "c@x")],
        )

        assert [row.user_id for row in await user_cost_summaries(db, 7)] == [BOB, CAROL, ALICE]

    @pytest.mark.anyio
    async def test_should_count_the_retired_dollar_credit_in_the_effective_cost(self) -> None:
        db = fake_db(
            usage_rows=[usage(ALICE, "2026-10-28", 3.0), usage(BOB, "2026-10-28", 2.0)],
            history_rows=[history(ALICE, "2026-10-28", credit=-2.5)],
            users=[user_doc(ALICE, "a@x"), user_doc(BOB, "b@x")],
        )

        rows = await user_cost_summaries(db, 7)

        assert [(r.user_id, r.credit_adjustment_usd, r.effective_usd) for r in rows] == [
            (BOB, 0.0, 2.0),
            (ALICE, -2.5, pytest.approx(0.5)),
        ]

    @pytest.mark.anyio
    async def test_should_put_users_with_no_spend_last_in_email_order(self) -> None:
        db = fake_db(
            usage_rows=[usage(ALICE, "2026-10-28", 1.0)],
            accounts=[account_row(CAROL, "free"), account_row(BOB, "free")],
            users=[user_doc(ALICE, "a@x"), user_doc(BOB, "z@x"), user_doc(CAROL, "m@x")],
        )

        assert [row.user_id for row in await user_cost_summaries(db, 7)] == [ALICE, CAROL, BOB]

    @pytest.mark.anyio
    async def test_should_keep_history_only_spend_for_a_user_with_no_attributed_rows(self) -> None:
        db = fake_db(
            history_rows=[history(ALICE, "2026-07-08", total=4.0, videos=2)],
            users=[user_doc(ALICE, "a@x")],
        )

        [row] = await user_cost_summaries(db, 90)

        assert (row.total_cost_usd, row.video_count) == (4.0, 2)

    @pytest.mark.anyio
    async def test_should_read_nothing_but_never_write(self) -> None:
        db = fake_db(usage_rows=[usage(ALICE, "2026-10-28", 1.0)], users=[user_doc(ALICE, "a@x")])

        await user_cost_summaries(db, 7)

        written = [
            call
            for collection in (db.llm_usage, db.userCosts, db.billingAccounts, db.users)
            for call in collection.method_calls
            if call[0].split(".")[0]
            in {"insert_one", "update_one", "update_many", "delete_one", "replace_one"}
        ]
        assert written == []
