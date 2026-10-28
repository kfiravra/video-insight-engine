"""Tests for the billing account ledger paging, read-only guarantee and constants."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId

from src.dependencies import get_database
from src.main import app
from src.services import billing_account
from src.services.billing_account import to_iso
from tests.billing_fakes import (
    BILLING_COLLECTIONS,
    READ_METHODS,
    URL,
    USER_ID,
    UseDb,
    api_get,
    install_service,
    make_account,
    make_db,
    make_entry,
    make_lot,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def use_db() -> Iterator[UseDb]:
    yield install_service
    app.dependency_overrides.clear()


# ─── Ledger ───


@pytest.mark.anyio
async def test_should_return_ledger_entries_newest_first_with_actor_and_reason(
    use_db: UseDb,
) -> None:
    older = make_entry(
        1, datetime(2026, 10, 20, 9), type="grant", actorId="f" * 24, reason="goodwill"
    )
    newer = make_entry(
        2,
        datetime(2026, 10, 27, 9),
        type="debit",
        bucket="plan",
        minutes=-12,
        source={"kind": "run", "ref": "req-9"},
        youtubeId="dQw4w9WgXcQ",
        durationSec=690.5,
        costUsd=0.0412,
        lotId=ObjectId("1" * 24),
        auditId=ObjectId("c" * 24),
    )
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=[older, newer]))

    ledger = (await api_get()).json()["ledger"]

    assert [e["idempotencyKey"] for e in ledger] == ["admin:2", "admin:1"]
    assert ledger[0] == {
        "id": f"{0x102:024x}",
        "userId": USER_ID,
        "type": "debit",
        "bucket": "plan",
        "minutes": -12,
        "lotId": "1" * 24,
        "source": {"kind": "run", "ref": "req-9"},
        "idempotencyKey": "admin:2",
        "youtubeId": "dQw4w9WgXcQ",
        "durationSec": 690.5,
        "costUsd": 0.0412,
        "actorId": None,
        "auditId": "c" * 24,
        "reason": None,
        "createdAt": "2026-10-27T09:00:00.000Z",
    }
    assert (ledger[1]["actorId"], ledger[1]["reason"]) == ("f" * 24, "goodwill")


def _ledger_of(count: int) -> list[dict[str, Any]]:
    start = datetime(2026, 10, 1)
    return [make_entry(n, start + timedelta(hours=n)) for n in range(count)]


@pytest.mark.anyio
async def test_should_default_to_a_page_of_50_ledger_entries(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=_ledger_of(60)))

    body = (await api_get()).json()

    assert len(body["ledger"]) == 50
    assert body["ledgerNextBefore"] == body["ledger"][-1]["createdAt"]


@pytest.mark.anyio
async def test_should_page_the_ledger_with_the_before_cursor(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=_ledger_of(5)))

    first = (await api_get(f"{URL}?limit=2")).json()
    second = (await api_get(f"{URL}?limit=2&before={first['ledgerNextBefore']}")).json()
    third = (await api_get(f"{URL}?limit=2&before={second['ledgerNextBefore']}")).json()

    keys = [e["idempotencyKey"] for page in (first, second, third) for e in page["ledger"]]
    assert keys == ["admin:4", "admin:3", "admin:2", "admin:1", "admin:0"]
    assert third["ledgerNextBefore"] is None


@pytest.mark.anyio
async def test_should_have_no_next_cursor_when_the_page_ends_exactly_at_the_oldest_entry(
    use_db: UseDb,
) -> None:
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=_ledger_of(2)))

    body = (await api_get(f"{URL}?limit=2")).json()

    assert len(body["ledger"]) == 2
    assert body["ledgerNextBefore"] is None


@pytest.mark.anyio
async def test_should_not_split_entries_that_share_a_timestamp_across_pages(use_db: UseDb) -> None:
    tied = datetime(2026, 10, 10, 10)
    entries = [
        make_entry(1, datetime(2026, 10, 5)),
        make_entry(2, tied),
        make_entry(3, tied),
        make_entry(4, tied),
        make_entry(5, datetime(2026, 10, 20)),
    ]
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=entries))

    first = (await api_get(f"{URL}?limit=3")).json()
    second = (await api_get(f"{URL}?limit=3&before={first['ledgerNextBefore']}")).json()

    assert [e["idempotencyKey"] for e in first["ledger"]] == [
        "admin:5",
        "admin:4",
        "admin:3",
        "admin:2",
    ]
    assert [e["idempotencyKey"] for e in second["ledger"]] == ["admin:1"]


@pytest.mark.anyio
async def test_should_accept_a_before_cursor_with_a_z_suffix_or_an_offset(use_db: UseDb) -> None:
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=_ledger_of(3)))
    cutoff = "2026-10-01T02:00:00.000Z"

    zulu = (await api_get(f"{URL}?before={cutoff}")).json()
    offset = (await api_get(f"{URL}?before=2026-10-01T03:00:00%2B01:00")).json()

    assert [e["idempotencyKey"] for e in zulu["ledger"]] == ["admin:1", "admin:0"]
    assert zulu["ledger"] == offset["ledger"]


@pytest.mark.anyio
async def test_should_ignore_other_accounts_ledger_entries(use_db: UseDb) -> None:
    foreign = make_entry(1, datetime(2026, 10, 2), accountId=ObjectId("e" * 24))
    use_db(make_db(billingAccounts=[make_account()], ledgerEntries=[foreign]))

    body = (await api_get()).json()

    assert body["ledger"] == []


# ─── Reads only ───


@pytest.mark.anyio
async def test_should_only_read_the_billing_collections(use_db: UseDb) -> None:
    db = use_db(
        make_db(
            billingAccounts=[make_account()],
            creditLots=[make_lot(1)],
            ledgerEntries=_ledger_of(3),
        )
    )

    resp = await api_get(f"{URL}?limit=2")

    assert resp.status_code == 200
    assert db.calls
    assert {call.split(".")[1] for call in db.calls} <= READ_METHODS
    assert {call.split(".")[0] for call in db.calls} <= BILLING_COLLECTIONS


@pytest.mark.anyio
async def test_should_fail_a_write_attempt_against_the_fake_collections() -> None:
    db = make_db()

    with pytest.raises(AssertionError, match="reads only"):
        await db.ledgerEntries.insert_one({"x": 1})


def test_should_not_call_any_write_method_in_the_billing_modules() -> None:
    sources = [
        Path(billing_account.__file__),
        Path(billing_account.__file__).with_name("billing_reader.py"),
        Path(billing_account.__file__).parents[1] / "routes" / "billing.py",
    ]
    write_call = re.compile(
        r"\.(insert_one|insert_many|update_one|update_many|replace_one|delete_one|delete_many"
        r"|bulk_write|find_one_and_\w+|create_index|drop\w*|aggregate)\("
    )

    offenders = [p.name for p in sources if write_call.search(p.read_text())]

    assert offenders == []


# ─── Wiring + constants ───


@pytest.mark.anyio
async def test_should_serve_from_the_shared_database_dependency() -> None:
    account = make_account()
    account["buckets"]["plan"]["cycleEnd"] = datetime(2999, 1, 1)
    db = make_db(billingAccounts=[account])
    app.dependency_overrides[get_database] = lambda: db
    try:
        resp = await api_get()
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["account"]["accountId"] == "b" * 24


def test_should_format_dates_like_js_to_iso_string() -> None:
    naive_utc = datetime(2026, 1, 2, 3, 4, 5, 6000)
    offset = datetime(2026, 1, 2, 4, 4, 5, 6000, tzinfo=UTC) + timedelta(0)

    assert to_iso(naive_utc) == "2026-01-02T03:04:05.006Z"
    assert to_iso(offset) == "2026-01-02T04:04:05.006Z"
    assert to_iso(None) is None


def test_should_mirror_the_free_video_constants_in_packages_types() -> None:
    billing_ts = Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "billing.ts"
    if not billing_ts.exists():
        pytest.skip("packages/types is not part of this checkout")
    text = billing_ts.read_text()
    free_video = re.search(
        r"FREE_VIDEO\s*=\s*\{\s*perWindow:\s*(\d+),\s*windowHours:\s*(\d+),"
        r"\s*maxVideoMinutes:\s*(\d+)",
        text,
    )

    assert free_video is not None
    assert tuple(int(g) for g in free_video.groups()) == (
        billing_account.FREE_VIDEOS_PER_WINDOW,
        billing_account.FREE_WINDOW_HOURS,
        billing_account.FREE_MAX_VIDEO_MINUTES,
    )
