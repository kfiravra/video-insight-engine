"""Per-user cost views, and the grant-credit proxy to vie-api's credits endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

import src.routes.users as users_module
from src.config import settings
from src.main import app
from src.services.vie_api_client import IDEMPOTENCY_HEADER
from tests._actor import assert_signed_for


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_user_costs_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/users/costs")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_user_costs_rejects_wrong_key():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/users/costs",
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_user_detail_rejects_short_user_id():
    """User detail endpoint must reject IDs shorter than 24 chars."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/users/abc/costs",
            headers={"Authorization": "Bearer test-admin-key"},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_user_detail_rejects_invalid_object_id():
    """User detail endpoint must reject non-hex 24-char IDs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/users/" + "z" * 24 + "/costs",
            headers={"Authorization": "Bearer test-admin-key"},
        )
    # Either 400 (validation) or 500 (DB error) — we just want it to NOT silently 200
    assert resp.status_code in {400, 500}


USER_ID = "a" * 24
GRANT_URL = f"/users/{USER_ID}/grant-credit"
GRANT = {"minutes": 150, "reason": "support ticket 42"}


def _mock_client(response: MagicMock | None = None, error: Exception | None = None):
    """Context-manager mock yielding an AsyncClient whose `.request` answers or raises."""
    instance = MagicMock()
    instance.request = AsyncMock(return_value=response, side_effect=error)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), instance


def _upstream(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = __import__("json").dumps(body)
    resp.json = MagicMock(return_value=body)
    return resp


async def _grant(headers: dict[str, str], json: dict | None = None, url: str = GRANT_URL):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, headers=headers, json=json if json is not None else GRANT)


@pytest.mark.anyio
async def test_grant_credit_requires_auth():
    resp = await _grant({})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_grant_credit_refuses_the_shared_machine_key():
    """The machine key reads; granting minutes needs a named admin."""
    resp = await _grant({"Authorization": "Bearer test-admin-key"})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_grant_credit_forwards_minutes_and_reason_to_vie_api(admin_session):
    upstream = _upstream(200, {"lotId": "l" * 24, "auditId": "u" * 24, "before": {}, "after": {}})
    factory, instance = _mock_client(upstream)

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 200
    assert resp.json()["lotId"] == "l" * 24
    method, url = instance.request.call_args.args
    assert (method, url) == (
        "POST",
        f"{settings.VIE_API_URL}/api/admin/billing/accounts/{USER_ID}/credits",
    )
    assert instance.request.call_args.kwargs["content"] == (
        b'{"minutes":150,"reason":"support ticket 42"}'
    )


@pytest.mark.anyio
async def test_grant_credit_forwards_an_optional_expiry(admin_session):
    factory, instance = _mock_client(_upstream(200, {}))
    body = {**GRANT, "expiresAt": "2027-01-31T00:00:00Z"}

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await _grant(admin_session, body)

    assert b'"expiresAt":"2027-01-31T00:00:00Z"' in instance.request.call_args.kwargs["content"]


@pytest.mark.anyio
async def test_grant_credit_names_the_session_admin_in_a_signed_actor_header(admin_session):
    factory, instance = _mock_client(_upstream(200, {}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await _grant(admin_session)

    call = instance.request.call_args
    assert call.kwargs["headers"]["X-Admin-Key"] == settings.ADMIN_API_KEY
    assert_signed_for(
        call,
        method="POST",
        target=f"/api/admin/billing/accounts/{USER_ID}/credits",
        body=call.kwargs["content"],
    )


@pytest.mark.anyio
async def test_grant_credit_forwards_the_idempotency_key(admin_session):
    factory, instance = _mock_client(_upstream(200, {}))
    headers = {**admin_session, IDEMPOTENCY_HEADER: "dialog-open-0001"}

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await _grant(headers)

    assert instance.request.call_args.kwargs["headers"][IDEMPOTENCY_HEADER] == "dialog-open-0001"


@pytest.mark.anyio
async def test_grant_credit_sends_no_idempotency_key_when_none_was_given(admin_session):
    factory, instance = _mock_client(_upstream(200, {}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await _grant(admin_session)

    assert IDEMPOTENCY_HEADER not in instance.request.call_args.kwargs["headers"]


@pytest.mark.anyio
async def test_grant_credit_rejects_an_expiry_without_a_timezone(admin_session):
    """A naive timestamp is ambiguous; vie-api would read it as the server's local time."""
    resp = await _grant(admin_session, {**GRANT, "expiresAt": "2027-01-31T00:00:00"})

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_logs_the_grant_by_ids_never_the_reason(admin_session):
    from structlog.testing import capture_logs

    factory, _ = _mock_client(_upstream(200, {}))

    with capture_logs() as logs, patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        await _grant(admin_session)

    event = next(entry for entry in logs if entry["event"] == "credit_grant_requested")
    assert (event["user_id"], event["minutes"]) == (USER_ID, 150)
    assert "support ticket 42" not in repr(logs)


@pytest.mark.anyio
@pytest.mark.parametrize("minutes", [0, -5, 100_001, 1.5, "ten"])
async def test_grant_credit_rejects_bad_minutes(admin_session, minutes):
    resp = await _grant(admin_session, {**GRANT, "minutes": minutes})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["", "x" * 501])
async def test_grant_credit_rejects_a_bad_reason(admin_session, reason):
    resp = await _grant(admin_session, {**GRANT, "reason": reason})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_requires_a_reason(admin_session):
    resp = await _grant(admin_session, {"minutes": 10})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [{"adminId": "a" * 24}, {"amountUsd": 5}, {"date": "2026-01-01"}])
async def test_grant_credit_rejects_the_retired_fields(admin_session, extra):
    """An old client (dollar amount, self-attested adminId) must fail loudly."""
    resp = await _grant(admin_session, {**GRANT, **extra})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_rejects_a_malformed_user_id(admin_session):
    resp = await _grant(admin_session, url="/users/not-an-id/grant-credit")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_rejects_a_malformed_idempotency_key(admin_session):
    resp = await _grant({**admin_session, IDEMPOTENCY_HEADER: "short"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_passes_vie_apis_validation_error_through(admin_session):
    detail = {"error": "VALIDATION_ERROR", "message": "expiresAt must be in the future"}
    factory, _ = _mock_client(_upstream(400, detail))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail


@pytest.mark.anyio
async def test_grant_credit_passes_an_unknown_user_404_through(admin_session):
    factory, _ = _mock_client(_upstream(404, {"error": "NOT_FOUND", "message": "User not found"}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_grant_credit_passes_a_permission_refusal_through(admin_session):
    factory, _ = _mock_client(_upstream(403, {"error": "ADMIN_PERMISSION_DENIED"}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_grant_credit_answers_500_when_vie_api_rejects_the_admin_credentials(admin_session):
    factory, _ = _mock_client(_upstream(401, {"error": "UNAUTHORIZED"}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 500


@pytest.mark.anyio
async def test_grant_credit_warns_that_a_timeout_may_have_applied_the_grant(admin_session):
    factory, _ = _mock_client(error=httpx.ReadTimeout("slow"))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 504
    assert "may have been applied" in resp.json()["detail"]


@pytest.mark.anyio
async def test_grant_credit_answers_502_when_vie_api_is_unreachable(admin_session):
    factory, _ = _mock_client(error=httpx.ConnectError("refused"))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_grant_credit_never_touches_the_users_collection(admin_session, monkeypatch):
    """The grant lives entirely in vie-api now; vie-admin opens no database for it."""
    opened = MagicMock(
        side_effect=AssertionError("grant-credit must not read or write the database")
    )
    monkeypatch.setattr("src.routes.users.get_database", opened)
    factory, _ = _mock_client(_upstream(200, {}))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _grant(admin_session)

    assert resp.status_code == 200
    opened.assert_not_called()


# ─── The Users list and per-user cost views, from llm_usage ───

MACHINE = {"Authorization": "Bearer test-admin-key"}
BOB_ID = "b" * 24


def _users_db(
    usage_rows: list[dict] | None = None,
    history_rows: list[dict] | None = None,
    accounts: list[dict] | None = None,
    users: list[dict] | None = None,
    account: dict | None = None,
    user_doc: dict | None = None,
    adjustments: list[dict] | None = None,
) -> MagicMock:
    """Motor: aggregate()/find() are sync and return cursors; to_list()/find_one() are async."""
    db = MagicMock()
    db.llm_usage.aggregate.return_value.to_list = AsyncMock(return_value=usage_rows or [])
    db.userCosts.find.return_value.to_list = AsyncMock(return_value=history_rows or [])
    db.billingAccounts.find.return_value.to_list = AsyncMock(return_value=accounts or [])
    db.billingAccounts.find_one = AsyncMock(return_value=account)
    db.users.find.return_value.to_list = AsyncMock(return_value=users or [])
    db.users.find_one = AsyncMock(return_value=user_doc)
    adjustment_cursor = (
        db.userCostAdjustments.find.return_value.sort.return_value.limit.return_value
    )
    adjustment_cursor.to_list = AsyncMock(return_value=adjustments or [])
    return db


@pytest.fixture
def users_db(monkeypatch: pytest.MonkeyPatch):
    """Install a database stub on the users routes; the 30 s response cache is cleared around it."""

    def install(**kwargs) -> MagicMock:
        db = _users_db(**kwargs)
        monkeypatch.setattr(users_module, "get_database", lambda: db)
        users_module._cache.clear()
        return db

    yield install
    users_module._cache.clear()


async def _get(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=MACHINE)


def _usage(user: str, date: str, cost: float, runs: int = 1) -> dict:
    return {"user": user, "date": date, "costUsd": cost, "runs": runs}


def _person(user: str, email: str) -> dict:
    return {"_id": ObjectId(user), "email": email, "name": email.split("@")[0]}


@pytest.mark.anyio
async def test_user_costs_list_sums_a_users_llm_usage(users_db):
    users_db(
        usage_rows=[
            _usage(USER_ID, "2026-10-27", 0.1),
            _usage(USER_ID, "2026-10-28", 0.25, runs=2),
        ],
        users=[_person(USER_ID, "alice@example.com")],
    )

    resp = await _get("/users/costs?days=7")

    assert resp.status_code == 200
    [row] = resp.json()
    assert (row["userId"], row["email"], row["videoCount"], row["days"]) == (
        USER_ID,
        "alice@example.com",
        3,
        2,
    )
    assert row["totalCostUsd"] == pytest.approx(0.35)
    assert row["effectiveUsd"] == pytest.approx(0.35)


@pytest.mark.anyio
async def test_user_costs_list_keeps_its_response_shape_with_plan_in_place_of_tier(users_db):
    users_db(
        usage_rows=[_usage(USER_ID, "2026-10-28", 0.1)], users=[_person(USER_ID, "a@example.com")]
    )

    [row] = (await _get("/users/costs")).json()

    assert set(row) == {
        "userId",
        "email",
        "name",
        "plan",
        "totalCostUsd",
        "videoCount",
        "creditAdjustmentUsd",
        "effectiveUsd",
        "days",
    }


@pytest.mark.anyio
async def test_user_costs_list_shows_the_account_plan_and_free_without_an_account(users_db):
    users_db(
        usage_rows=[_usage(USER_ID, "2026-10-28", 0.2), _usage(BOB_ID, "2026-10-28", 0.1)],
        accounts=[{"ownerId": USER_ID, "plan": {"id": "pro"}}],
        users=[_person(USER_ID, "a@example.com"), _person(BOB_ID, "b@example.com")],
    )

    rows = (await _get("/users/costs")).json()

    assert {row["userId"]: row["plan"] for row in rows} == {USER_ID: "pro", BOB_ID: "free"}


@pytest.mark.anyio
async def test_user_costs_list_includes_a_new_user_who_has_no_spend_yet(users_db):
    users_db(users=[_person(BOB_ID, "new@example.com")])

    [row] = (await _get("/users/costs")).json()

    assert (row["userId"], row["email"], row["plan"], row["totalCostUsd"], row["videoCount"]) == (
        BOB_ID,
        "new@example.com",
        "free",
        0,
        0,
    )


@pytest.mark.anyio
async def test_user_costs_list_never_reads_the_retired_cost_totals_for_a_day_with_usage(users_db):
    users_db(
        usage_rows=[_usage(USER_ID, "2026-10-28", 0.25)],
        history_rows=[
            {
                "userId": ObjectId(USER_ID),
                "date": "2026-10-28",
                "totalCostUsd": 9.0,
                "videoCount": 9,
            }
        ],
        users=[_person(USER_ID, "a@example.com")],
    )

    [row] = (await _get("/users/costs")).json()

    assert (row["totalCostUsd"], row["videoCount"]) == (0.25, 1)


@pytest.mark.anyio
async def test_user_costs_list_pages_with_limit_and_offset(users_db):
    users_db(
        usage_rows=[
            _usage(USER_ID, "2026-10-28", 3.0),
            _usage(BOB_ID, "2026-10-28", 2.0),
            _usage("c" * 24, "2026-10-28", 1.0),
        ],
        users=[_person(USER_ID, "a@x"), _person(BOB_ID, "b@x"), _person("c" * 24, "c@x")],
    )

    page = (await _get("/users/costs?limit=1&offset=1")).json()

    assert [row["userId"] for row in page] == [BOB_ID]


@pytest.mark.anyio
async def test_user_costs_list_never_touches_the_old_tier_field(users_db):
    db = users_db(usage_rows=[_usage(USER_ID, "2026-10-28", 0.1)], users=[_person(USER_ID, "a@x")])

    await _get("/users/costs")

    projection = db.users.find.call_args.args[1]
    assert "tier" not in projection


@pytest.mark.anyio
async def test_user_cost_detail_lists_days_oldest_first_with_the_history_credit(users_db):
    users_db(
        usage_rows=[_usage(USER_ID, "2026-10-28", 0.25), _usage(USER_ID, "2026-10-26", 0.1)],
        history_rows=[
            {"userId": ObjectId(USER_ID), "date": "2026-10-28", "creditAdjustmentUsd": -1.0},
            {
                "userId": ObjectId(USER_ID),
                "date": "2026-07-08",
                "totalCostUsd": 0.5,
                "videoCount": 1,
            },
        ],
        account={"plan": {"id": "pro"}},
        user_doc={"email": "alice@example.com", "name": "Alice"},
    )

    body = (await _get(f"/users/{USER_ID}/costs?days=90")).json()

    assert [d["date"] for d in body["daily"]] == ["2026-07-08", "2026-10-26", "2026-10-28"]
    assert body["daily"][2] == {
        "date": "2026-10-28",
        "totalCostUsd": 0.25,
        "videoCount": 1,
        "creditAdjustmentUsd": -1.0,
        "effectiveUsd": pytest.approx(-0.75),
    }
    assert (body["user"]["plan"], body["user"]["email"], body["user"]["days"]) == (
        "pro",
        "alice@example.com",
        3,
    )
    assert body["user"]["totalCostUsd"] == pytest.approx(0.85)


@pytest.mark.anyio
async def test_user_cost_detail_asks_for_only_this_users_rows(users_db):
    db = users_db()

    await _get(f"/users/{USER_ID}/costs")

    pipeline = db.llm_usage.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["user_id"] == USER_ID


@pytest.mark.anyio
async def test_user_cost_detail_for_an_unknown_user_is_empty_and_free(users_db):
    users_db(user_doc=None, account=None)

    body = (await _get(f"/users/{USER_ID}/costs")).json()

    assert (body["user"]["email"], body["user"]["plan"], body["daily"]) == (None, "free", [])


@pytest.mark.anyio
async def test_user_cost_detail_still_reads_the_old_dollar_adjustments_as_history(users_db):
    users_db(
        adjustments=[
            {
                "_id": ObjectId(),
                "date": "2026-07-01",
                "amountUsd": -2.0,
                "reason": "old goodwill credit",
                "adminId": ObjectId(BOB_ID),
                "createdAt": datetime(2026, 7, 1, tzinfo=UTC),
            }
        ]
    )

    [adjustment] = (await _get(f"/users/{USER_ID}/costs")).json()["adjustments"]

    assert (adjustment["amountUsd"], adjustment["reason"], adjustment["adminId"]) == (
        -2.0,
        "old goodwill credit",
        BOB_ID,
    )


@pytest.mark.anyio
async def test_user_activity_cost_timeline_comes_from_llm_usage(users_db):
    db = users_db(usage_rows=[_usage(USER_ID, "2026-10-28", 0.25)])
    db.userVideos.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[]
    )
    db.llm_usage.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(
        return_value=[]
    )

    body = (await _get(f"/users/{USER_ID}/activity")).json()

    assert body["costTimeline"] == [
        {
            "date": "2026-10-28",
            "totalCostUsd": 0.25,
            "creditAdjustmentUsd": 0.0,
            "effectiveUsd": 0.25,
        }
    ]
