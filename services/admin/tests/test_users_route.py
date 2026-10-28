"""Per-user cost views, and the grant-credit proxy to vie-api's credits endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

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
