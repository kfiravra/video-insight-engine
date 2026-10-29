"""Tests for GET /billing/accounts/{user_id} — auth, validation, account shape, lots."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from src.main import app
from src.services.billing_account import to_iso
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
    caps = {"maxVideoMinutes": 20, "freeVideosPerDay": 2, "maxConcurrent": 3, "blocked": True}
    use_db(
        make_db(
            billingAccounts=[make_account(caps=caps)], creditLots=[make_lot(1, minutesRemaining=90)]
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
    assert body["planMinutes"] == {
        "cycleStart": "2026-10-01T00:00:00.000Z",
        "cycleEnd": "2026-11-01T00:00:00.000Z",
        "granted": 600,
        "used": 140,
        "remaining": 460,
    }
    assert body["credits"] == {"available": 90}
    assert body["caps"] == {
        "maxVideoMinutes": 20,
        "freeVideosPerDay": 2,
        "maxConcurrent": 3,
        "blocked": True,
    }
    assert body["version"] == 7
    assert body["updatedAt"] == "2026-10-27T08:30:15.123Z"


@pytest.mark.anyio
async def test_should_report_no_plan_minutes_remaining_once_the_cycle_has_ended(
    use_db: UseDb,
) -> None:
    ended = make_account()
    ended["buckets"]["plan"]["cycleEnd"] = NOW - timedelta(minutes=1)
    use_db(make_db(billingAccounts=[ended]))

    plan_minutes = (await api_get()).json()["account"]["planMinutes"]

    assert plan_minutes["remaining"] == 0
    assert plan_minutes["granted"] == 600


@pytest.mark.anyio
async def test_should_report_no_plan_minutes_on_the_free_plan(use_db: UseDb) -> None:
    free = make_account(plan={"id": "free", "status": "active", "source": None})
    free["buckets"]["plan"] = {
        "cycleStart": None,
        "cycleEnd": None,
        "minutesGranted": 0,
        "minutesUsed": 0,
    }
    use_db(make_db(billingAccounts=[free]))

    body = (await api_get()).json()["account"]

    assert body["plan"]["periodEnd"] is None
    assert body["planMinutes"]["remaining"] == 0


@pytest.mark.anyio
async def test_should_leave_unset_caps_null(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()]))

    caps = (await api_get()).json()["account"]["caps"]

    assert caps == {
        "maxVideoMinutes": None,
        "freeVideosPerDay": None,
        "maxConcurrent": None,
        "blocked": None,
    }


# ─── Free video ───


@pytest.mark.anyio
async def test_should_show_the_free_video_ready_when_none_was_used(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()]))

    free = (await api_get()).json()["account"]["free"]

    assert free == {
        "perWindow": 1,
        "usedInWindow": 0,
        "available": True,
        "nextFreeAt": None,
        "maxVideoMinutes": 30,
    }


@pytest.mark.anyio
async def test_should_show_when_the_next_free_video_opens(use_db: UseDb) -> None:
    used_at = NOW - timedelta(hours=2)
    account = make_account()
    account["buckets"]["free"]["recentFreeVideoAt"] = [used_at]
    use_db(make_db(billingAccounts=[account]))

    free = (await api_get()).json()["account"]["free"]

    assert free["available"] is False
    assert free["usedInWindow"] == 1
    assert free["nextFreeAt"] == to_iso(used_at + timedelta(hours=24))


@pytest.mark.anyio
async def test_should_reopen_the_free_video_at_exactly_24_hours(use_db: UseDb) -> None:
    account = make_account()
    account["buckets"]["free"]["recentFreeVideoAt"] = [NOW - timedelta(hours=24)]
    use_db(make_db(billingAccounts=[account]))

    free = (await api_get()).json()["account"]["free"]

    assert free["available"] is True
    assert free["usedInWindow"] == 0


@pytest.mark.anyio
async def test_should_open_the_slot_of_the_oldest_video_when_the_cap_allows_two(
    use_db: UseDb,
) -> None:
    oldest, newer = NOW - timedelta(hours=10), NOW - timedelta(hours=1)
    account = make_account(caps={"freeVideosPerDay": 2})
    account["buckets"]["free"]["recentFreeVideoAt"] = [newer, oldest]
    use_db(make_db(billingAccounts=[account]))

    free = (await api_get()).json()["account"]["free"]

    assert free["perWindow"] == 2
    assert free["available"] is False
    assert free["nextFreeAt"] == to_iso(oldest + timedelta(hours=24))


@pytest.mark.anyio
async def test_should_report_the_free_video_disabled_when_the_cap_is_zero(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account(caps={"freeVideosPerDay": 0})]))

    free = (await api_get()).json()["account"]["free"]

    assert (free["available"], free["nextFreeAt"], free["perWindow"]) == (False, None, 0)


@pytest.mark.anyio
async def test_should_tighten_the_free_video_length_with_the_max_video_cap(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account(caps={"maxVideoMinutes": 12})]))

    free = (await api_get()).json()["account"]["free"]

    assert free["maxVideoMinutes"] == 12


# ─── Lots, holds, grants ───


@pytest.mark.anyio
async def test_should_count_only_unexpired_lots_as_available_credit(use_db: UseDb) -> None:
    lots = [
        make_lot(1, minutesRemaining=40),
        make_lot(2, minutesRemaining=60, expiresAt=None),
        make_lot(3, minutesRemaining=25, expiresAt=NOW - timedelta(days=1)),
        make_lot(4, minutesRemaining=0),
    ]
    use_db(make_db(billingAccounts=[make_account()], creditLots=lots))

    body = (await api_get()).json()

    assert body["account"]["credits"] == {"available": 100}


@pytest.mark.anyio
async def test_should_list_lots_newest_first_with_expiry_state(use_db: UseDb) -> None:
    lots = [
        make_lot(1, minutesRemaining=0),
        make_lot(2, expiresAt=NOW - timedelta(days=1)),
        make_lot(3, expiresAt=None, priceUsdCents=None, source={"kind": "admin", "ref": "d" * 24}),
    ]
    use_db(make_db(billingAccounts=[make_account()], creditLots=lots))

    listed = (await api_get()).json()["lots"]

    assert [lot["lotId"] for lot in listed] == [f"{n:024x}" for n in (3, 2, 1)]
    assert [lot["expired"] for lot in listed] == [False, True, False]
    assert listed[0] == {
        "lotId": f"{3:024x}",
        "minutesGranted": 150,
        "minutesRemaining": 150,
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
        "minutes": 31,
        "split": {
            "free": 0,
            "plan": 11,
            "credit": 20,
            "lots": [{"lotId": ObjectId("1" * 24), "minutes": 20}],
        },
        "freeVideoAt": None,
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
            "minutes": 31,
            "split": {
                "free": 0,
                "plan": 11,
                "credit": 20,
                "lots": [{"lotId": "1" * 24, "minutes": 20}],
            },
            "freeVideoAt": None,
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
            "minutes": 100 * n,
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
        "minutes": 200,
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
