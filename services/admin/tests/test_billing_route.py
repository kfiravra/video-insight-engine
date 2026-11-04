"""Tests for GET /billing/accounts/{user_id} — auth, validation, account shape, lots."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from src.main import app
from tests.billing_fakes import (
    ACCOUNT_OID,
    NOW,
    URL,
    USER_ID,
    USER_OID,
    UseDb,
    api_get,
    install_service,
    make_account,
    make_db,
    make_lot,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def use_db() -> Iterator[UseDb]:
    yield install_service
    app.dependency_overrides.clear()


# ─── Auth + validation ───


@pytest.mark.anyio
async def test_should_require_auth() -> None:
    resp = await api_get(headers={})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_should_reject_wrong_key() -> None:
    resp = await api_get(headers={"Authorization": "Bearer wrong-key"})

    assert resp.status_code == 401


@pytest.mark.anyio
# "%0A" is a trailing newline: Python's "$" would have let it through to ObjectId and a 500.
@pytest.mark.parametrize("bad_id", ["abc", "z" * 24, "a" * 25, "a" * 23 + "-", "a" * 24 + "%0A"])
async def test_should_return_400_for_an_invalid_user_id(bad_id: str, use_db: UseDb) -> None:
    use_db(make_db())

    resp = await api_get(f"/billing/accounts/{bad_id}")

    assert resp.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=abc"])
async def test_should_reject_an_out_of_range_limit(query: str, use_db: UseDb) -> None:
    use_db(make_db())

    resp = await api_get(f"{URL}?{query}")

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_should_return_400_for_an_unparseable_before_cursor(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()]))

    resp = await api_get(f"{URL}?before=yesterday")

    assert resp.status_code == 400


@pytest.mark.anyio
async def test_should_return_404_for_an_unknown_user(use_db: UseDb) -> None:
    use_db(make_db(users=[]))

    resp = await api_get()

    assert resp.status_code == 404


# ─── No account yet ───


@pytest.mark.anyio
async def test_should_return_the_no_account_shape_for_a_user_without_a_billing_row(
    use_db: UseDb,
) -> None:
    use_db(make_db())

    resp = await api_get()

    assert resp.status_code == 200
    assert resp.json() == {
        "account": None,
        "lots": [],
        "holds": [],
        "ledger": [],
        "recurringGrants": [],
        "ledgerNextBefore": None,
    }


# ─── Account shape ───


@pytest.mark.anyio
async def test_should_return_plan_buckets_and_caps_for_an_account(use_db: UseDb) -> None:
    caps = {"maxVideoMinutes": 20, "maxConcurrent": 3, "blocked": True}
    use_db(
        make_db(
            billingAccounts=[make_account(caps=caps)], creditLots=[make_lot(1, tokensRemaining=900)]
        )
    )

    body = (await api_get()).json()["account"]

    assert body["accountId"] == "b" * 24
    assert body["ownerId"] == USER_ID
    assert body["plan"] == {
        "id": "pro",
        "status": "active",
        "source": "manual",
        "periodStart": "2026-10-01T00:00:00.000Z",
        "periodEnd": "2027-10-01T00:00:00.000Z",
    }
    assert body["planTokens"] == {
        "cycleStart": "2026-10-01T00:00:00.000Z",
        "cycleEnd": "2026-11-01T00:00:00.000Z",
        "granted": 20_000,
        "used": 4_200,
        "remaining": 15_800,
    }
    assert body["credits"] == {"available": 900}
    assert (body["debt"], body["balance"], body["signupGrantAt"]) == (0, 16_700, None)
    assert body["caps"] == {
        "maxVideoMinutes": 20,
        "maxConcurrent": 3,
        "blocked": True,
    }
    assert body["version"] == 7
    assert body["updatedAt"] == "2026-10-27T08:30:15.123Z"


@pytest.mark.anyio
async def test_should_report_no_plan_tokens_remaining_once_the_cycle_has_ended(
    use_db: UseDb,
) -> None:
    ended = make_account()
    ended["buckets"]["plan"]["cycleEnd"] = NOW - timedelta(minutes=1)
    use_db(make_db(billingAccounts=[ended]))

    plan_tokens = (await api_get()).json()["account"]["planTokens"]

    assert plan_tokens["remaining"] == 0
    assert plan_tokens["granted"] == 20_000


@pytest.mark.anyio
async def test_should_report_no_plan_tokens_on_the_free_plan(use_db: UseDb) -> None:
    free = make_account(plan={"id": "free", "status": "active", "source": None})
    free["buckets"]["plan"] = {
        "cycleStart": None,
        "cycleEnd": None,
        "tokensGranted": 0,
        "tokensUsed": 0,
    }
    use_db(make_db(billingAccounts=[free]))

    body = (await api_get()).json()["account"]

    assert body["plan"]["periodEnd"] is None
    assert body["planTokens"]["remaining"] == 0


@pytest.mark.anyio
async def test_should_leave_unset_caps_null(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()]))

    caps = (await api_get()).json()["account"]["caps"]

    assert caps == {
        "maxVideoMinutes": None,
        "maxConcurrent": None,
        "blocked": None,
    }


# ─── Debt, signup grant, accounts from before the token migration ───


@pytest.mark.anyio
async def test_should_show_debt_as_a_negative_balance(use_db: UseDb) -> None:
    owing = make_account(plan={"id": "free", "status": "active", "source": None})
    owing["buckets"] = {
        "plan": {"cycleStart": None, "cycleEnd": None, "tokensGranted": 0, "tokensUsed": 0},
        "credit": {"tokensAvailable": 0},
        "debt": {"tokens": 120},
    }
    use_db(make_db(billingAccounts=[owing]))

    body = (await api_get()).json()["account"]

    assert (body["debt"], body["balance"]) == (120, -120)


@pytest.mark.anyio
async def test_should_show_when_the_signup_grant_was_written(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account(signupGrantAt=datetime(2026, 10, 2, 9))]))

    body = (await api_get()).json()["account"]

    assert body["signupGrantAt"] == "2026-10-02T09:00:00.000Z"


@pytest.mark.anyio
async def test_should_read_a_minute_account_as_an_empty_token_account(use_db: UseDb) -> None:
    legacy = make_account()
    legacy["buckets"] = {
        "free": {"recentFreeVideoAt": []},
        "plan": {
            "cycleStart": datetime(2026, 10, 1),
            "cycleEnd": datetime(2026, 11, 1),
            "minutesGranted": 600,
            "minutesUsed": 140,
        },
        "credit": {"minutesAvailable": 150},
    }
    legacy["holds"] = [
        {
            "requestId": "old-run",
            "userId": USER_OID,
            "youtubeId": "dQw4w9WgXcQ",
            "durationSec": 300,
            "minutes": 5,
            "split": {"free": 5, "plan": 0, "credit": 0, "lots": []},
            "freeVideoAt": datetime(2026, 10, 28),
            "planCycleStart": None,
            "createdAt": datetime(2026, 10, 28),
        }
    ]
    minute_lot = {
        "_id": ObjectId("9" * 24),
        "accountId": ACCOUNT_OID,
        "minutesGranted": 150,
        "minutesRemaining": 150,
        "source": {"kind": "admin", "ref": "d" * 24},
        "expiresAt": None,
        "createdAt": datetime(2026, 10, 5),
    }
    use_db(make_db(billingAccounts=[legacy], creditLots=[minute_lot]))

    body = (await api_get()).json()

    assert body["account"]["planTokens"]["granted"] == 0
    assert (body["account"]["credits"], body["account"]["debt"]) == ({"available": 0}, 0)
    assert body["lots"] == []
    assert (body["holds"][0]["tokens"], body["holds"][0]["split"]["lots"]) == (0, [])


# ─── Lots, holds, grants ───


@pytest.mark.anyio
async def test_should_count_only_unexpired_lots_as_available_credit(use_db: UseDb) -> None:
    lots = [
        make_lot(1, tokensRemaining=40),
        make_lot(2, tokensRemaining=60, expiresAt=None),
        make_lot(3, tokensRemaining=25, expiresAt=NOW - timedelta(days=1)),
        make_lot(4, tokensRemaining=0),
    ]
    use_db(make_db(billingAccounts=[make_account()], creditLots=lots))

    body = (await api_get()).json()

    assert body["account"]["credits"] == {"available": 100}


@pytest.mark.anyio
async def test_should_list_lots_newest_first_with_expiry_state(use_db: UseDb) -> None:
    lots = [
        make_lot(1, tokensRemaining=0),
        make_lot(2, expiresAt=NOW - timedelta(days=1)),
        make_lot(3, expiresAt=None, priceUsdCents=None, source={"kind": "admin", "ref": "d" * 24}),
    ]
    use_db(make_db(billingAccounts=[make_account()], creditLots=lots))

    listed = (await api_get()).json()["lots"]

    assert [lot["lotId"] for lot in listed] == [f"{n:024x}" for n in (3, 2, 1)]
    assert [lot["expired"] for lot in listed] == [False, True, False]
    assert listed[0] == {
        "lotId": f"{3:024x}",
        "tokensGranted": 5_000,
        "tokensRemaining": 5_000,
        "source": {"kind": "admin", "ref": "d" * 24},
        "priceUsdCents": None,
        "expiresAt": None,
        "expired": False,
        "createdAt": "2026-10-03T00:00:00.000Z",
    }


@pytest.mark.anyio
async def test_should_ignore_other_accounts_lots(use_db: UseDb) -> None:
    foreign = make_lot(1, accountId=ObjectId("e" * 24))
    use_db(make_db(billingAccounts=[make_account()], creditLots=[foreign]))

    body = (await api_get()).json()

    assert body["lots"] == []
    assert body["account"]["credits"]["available"] == 0


@pytest.mark.anyio
async def test_should_map_open_holds(use_db: UseDb) -> None:
    hold = {
        "requestId": "req-1",
        "userId": USER_OID,
        "youtubeId": "dQw4w9WgXcQ",
        "durationSec": 1830,
        "tokens": 1_200,
        "split": {
            "plan": 400,
            "credit": 800,
            "lots": [{"lotId": ObjectId("1" * 24), "tokens": 800}],
        },
        "planCycleStart": datetime(2026, 10, 1),
        "createdAt": datetime(2026, 10, 28, 11, 55),
    }
    use_db(make_db(billingAccounts=[make_account(holds=[hold])]))

    body = (await api_get()).json()

    assert body["account"]["openHolds"] == 1
    assert body["holds"] == [
        {
            "requestId": "req-1",
            "userId": USER_ID,
            "youtubeId": "dQw4w9WgXcQ",
            "durationSec": 1830,
            "tokens": 1_200,
            "split": {
                "plan": 400,
                "credit": 800,
                "lots": [{"lotId": "1" * 24, "tokens": 800}],
            },
            "planCycleStart": "2026-10-01T00:00:00.000Z",
            "createdAt": "2026-10-28T11:55:00.000Z",
        }
    ]


@pytest.mark.anyio
async def test_should_list_live_recurring_grants_and_hide_deleted_ones(use_db: UseDb) -> None:
    def grant(n: int, deleted_at: datetime | None) -> dict[str, Any]:
        return {
            "_id": ObjectId(f"{0x200 + n:024x}"),
            "accountId": ACCOUNT_OID,
            "tokens": 1_000 * n,
            "every": "month",
            "active": True,
            "nextRunAt": datetime(2026, 11, 1),
            "lastRunAt": None,
            "actorId": "f" * 24,
            "auditId": ObjectId("c" * 24),
            "reason": f"demo account {n}",
            "createdAt": datetime(2026, 10, n),
            "updatedAt": datetime(2026, 10, n),
            "deletedAt": deleted_at,
        }

    grants = [grant(2, None), grant(1, datetime(2026, 10, 20)), grant(3, None)]
    use_db(make_db(billingAccounts=[make_account()], recurringGrants=grants))

    listed = (await api_get()).json()["recurringGrants"]

    assert [g["reason"] for g in listed] == ["demo account 2", "demo account 3"]
    assert listed[0] == {
        "grantId": f"{0x202:024x}",
        "tokens": 2_000,
        "every": "month",
        "active": True,
        "nextRunAt": "2026-11-01T00:00:00.000Z",
        "lastRunAt": None,
        "actorId": "f" * 24,
        "auditId": "c" * 24,
        "reason": "demo account 2",
        "createdAt": "2026-10-02T00:00:00.000Z",
        "updatedAt": "2026-10-02T00:00:00.000Z",
    }
