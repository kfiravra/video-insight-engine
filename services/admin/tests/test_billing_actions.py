"""Billing actions and the audit log, forwarded to vie-api with the admin's identity."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.services.vie_api_client import ACTOR_HEADER, IDEMPOTENCY_HEADER
from tests.conftest import ADMIN_USER_ID

USER_ID = "a" * 24
GRANT_ID = "b" * 24
API = f"{settings.VIE_API_URL}/api/admin"
MACHINE = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}

# (name, method, vie-admin path, body, vie-api path, the exact body vie-api should receive)
MUTATIONS: list[tuple[str, str, str, dict[str, Any], str, bytes]] = [
    (
        "plan",
        "POST",
        f"/billing/accounts/{USER_ID}/plan",
        {"planId": "pro", "reason": "comp"},
        f"billing/accounts/{USER_ID}/plan",
        b'{"reason":"comp","planId":"pro"}',
    ),
    (
        "caps",
        "POST",
        f"/billing/accounts/{USER_ID}/caps",
        {"blocked": True, "reason": "abuse"},
        f"billing/accounts/{USER_ID}/caps",
        b'{"reason":"abuse","blocked":true}',
    ),
    (
        "adjust",
        "POST",
        f"/billing/accounts/{USER_ID}/adjust",
        {"bucket": "credit", "minutes": -20, "reason": "clawback"},
        f"billing/accounts/{USER_ID}/adjust",
        b'{"reason":"clawback","bucket":"credit","minutes":-20}',
    ),
    (
        "recurring-grants (create)",
        "POST",
        f"/billing/accounts/{USER_ID}/recurring-grants",
        {"minutes": 300, "every": "month", "active": True, "reason": "demo"},
        f"billing/accounts/{USER_ID}/recurring-grants",
        b'{"reason":"demo","minutes":300,"every":"month","active":true}',
    ),
    (
        "recurring-grants (delete)",
        "DELETE",
        f"/billing/accounts/{USER_ID}/recurring-grants/{GRANT_ID}",
        {"reason": "stop"},
        f"billing/accounts/{USER_ID}/recurring-grants/{GRANT_ID}",
        b'{"reason":"stop"}',
    ),
]
IDS = [m[0] for m in MUTATIONS]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _mock_client(response: MagicMock):
    instance = MagicMock()
    instance.request = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), instance


def _upstream(status_code: int = 200, body: dict | None = None) -> MagicMock:
    payload = body if body is not None else {"before": {}, "after": {}, "auditId": "u" * 24}
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(payload)
    resp.json = MagicMock(return_value=payload)
    return resp


async def _call(method: str, path: str, headers: dict[str, str], body: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=headers, json=body)


async def _forwarded(method: str, path: str, headers: dict[str, str], body: dict | None = None):
    """Call a route with vie-api mocked; returns (response, the kwargs of the forwarded call)."""
    factory, instance = _mock_client(_upstream())
    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _call(method, path, headers, body)
    kwargs = instance.request.call_args.kwargs if instance.request.call_args else {}
    return resp, instance, kwargs


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_requires_auth(name, method, path, body, _api, _sent):
    assert (await _call(method, path, {}, body)).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_refuses_the_shared_machine_key(name, method, path, body, _api, _sent):
    resp, instance, _ = await _forwarded(method, path, MACHINE, body)

    assert resp.status_code == 403
    instance.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "api_path", "sent"), MUTATIONS, ids=IDS)
async def test_forwards_to_the_matching_vie_api_route(
    name, method, path, body, api_path, sent, admin_session
):
    resp, instance, kwargs = await _forwarded(method, path, admin_session, body)

    assert resp.status_code == 200
    assert instance.request.call_args.args == (method, f"{API}/{api_path}")
    assert kwargs["content"] == sent
    assert kwargs["headers"]["Content-Type"] == "application/json"


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_names_the_session_admin_in_the_actor_header(
    name, method, path, body, _api, _sent, admin_session
):
    _, _, kwargs = await _forwarded(method, path, admin_session, body)

    assert kwargs["headers"][ACTOR_HEADER].startswith(f"{ADMIN_USER_ID}.")
    assert kwargs["headers"]["X-Admin-Key"] == settings.ADMIN_API_KEY


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_forwards_the_idempotency_key(name, method, path, body, _api, _sent, admin_session):
    headers = {**admin_session, IDEMPOTENCY_HEADER: "dialog-open-0001"}

    _, _, kwargs = await _forwarded(method, path, headers, body)

    assert kwargs["headers"][IDEMPOTENCY_HEADER] == "dialog-open-0001"


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_requires_a_reason(name, method, path, body, _api, _sent, admin_session):
    without_reason = {k: v for k, v in body.items() if k != "reason"}

    resp, instance, _ = await _forwarded(method, path, admin_session, without_reason)

    assert resp.status_code == 422
    instance.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_rejects_a_blank_or_oversized_reason(
    name, method, path, body, _api, _sent, admin_session
):
    blank = await _call(method, path, admin_session, {**body, "reason": ""})
    long = await _call(method, path, admin_session, {**body, "reason": "x" * 501})

    assert (blank.status_code, long.status_code) == (422, 422)


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_rejects_a_self_attested_admin_id(
    name, method, path, body, _api, _sent, admin_session
):
    resp, instance, _ = await _forwarded(method, path, admin_session, {**body, "adminId": "a" * 24})

    assert resp.status_code == 422
    instance.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_rejects_a_malformed_user_id(name, method, path, body, _api, _sent, admin_session):
    bad = path.replace(USER_ID, "not-an-id")

    assert (await _call(method, bad, admin_session, body)).status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(("name", "method", "path", "body", "_api", "_sent"), MUTATIONS, ids=IDS)
async def test_passes_vie_apis_errors_through(name, method, path, body, _api, _sent, admin_session):
    detail = {"error": "VALIDATION_ERROR", "message": "minutes must not be 0"}
    factory, _ = _mock_client(_upstream(400, detail))

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _call(method, path, admin_session, body)

    assert (resp.status_code, resp.json()["detail"]) == (400, detail)


@pytest.mark.anyio
async def test_plan_forwards_a_term_end(admin_session):
    body = {"planId": "pro", "periodEnd": "2027-01-31T00:00:00Z", "reason": "trial"}

    _, _, kwargs = await _forwarded(
        "POST", f"/billing/accounts/{USER_ID}/plan", admin_session, body
    )

    assert (
        kwargs["content"] == b'{"reason":"trial","planId":"pro","periodEnd":"2027-01-31T00:00:00Z"}'
    )


@pytest.mark.anyio
async def test_plan_rejects_an_unknown_plan(admin_session):
    body = {"planId": "team", "reason": "x"}

    assert (
        await _call("POST", f"/billing/accounts/{USER_ID}/plan", admin_session, body)
    ).status_code == 422


@pytest.mark.anyio
async def test_caps_keeps_an_explicit_null_so_a_cap_can_be_cleared(admin_session):
    body = {"maxVideoMinutes": None, "blocked": False, "reason": "relaxed"}

    _, _, kwargs = await _forwarded(
        "POST", f"/billing/accounts/{USER_ID}/caps", admin_session, body
    )

    assert json.loads(kwargs["content"]) == {
        "maxVideoMinutes": None,
        "blocked": False,
        "reason": "relaxed",
    }


@pytest.mark.anyio
async def test_caps_leaves_absent_caps_out_of_the_forwarded_body(admin_session):
    _, _, kwargs = await _forwarded(
        "POST", f"/billing/accounts/{USER_ID}/caps", admin_session, {"blocked": True, "reason": "x"}
    )

    assert set(json.loads(kwargs["content"])) == {"blocked", "reason"}


@pytest.mark.anyio
@pytest.mark.parametrize("minutes", [0, 100_001, 2.5])
async def test_adjust_rejects_bad_minutes(minutes, admin_session):
    body = {"bucket": "credit", "minutes": minutes, "reason": "x"}

    assert (
        await _call("POST", f"/billing/accounts/{USER_ID}/adjust", admin_session, body)
    ).status_code == 422


@pytest.mark.anyio
async def test_recurring_grant_rejects_a_period_other_than_month(admin_session):
    body = {"minutes": 300, "every": "week", "active": True, "reason": "x"}
    path = f"/billing/accounts/{USER_ID}/recurring-grants"

    assert (await _call("POST", path, admin_session, body)).status_code == 422


@pytest.mark.anyio
async def test_recurring_grant_delete_rejects_a_malformed_grant_id(admin_session):
    path = f"/billing/accounts/{USER_ID}/recurring-grants/nope"

    assert (await _call("DELETE", path, admin_session, {"reason": "x"})).status_code == 422


# ─── Audit log ───


@pytest.mark.anyio
async def test_audit_requires_auth():
    assert (await _call("GET", "/audit", {})).status_code == 401


@pytest.mark.anyio
async def test_audit_may_be_read_with_the_machine_key_without_an_actor():
    resp, _, kwargs = await _forwarded("GET", "/audit", MACHINE)

    assert resp.status_code == 200
    assert ACTOR_HEADER not in kwargs["headers"]


@pytest.mark.anyio
async def test_audit_read_by_a_session_names_the_admin(admin_session):
    _, _, kwargs = await _forwarded("GET", "/audit", admin_session)

    assert kwargs["headers"][ACTOR_HEADER].startswith(f"{ADMIN_USER_ID}.")


@pytest.mark.anyio
async def test_audit_forwards_the_filters_and_drops_the_ones_not_given(admin_session):
    path = f"/audit?targetId={USER_ID}&limit=25&before=2026-11-01T10:00:00Z"

    resp, instance, kwargs = await _forwarded("GET", path, admin_session)

    assert instance.request.call_args.args == ("GET", f"{API}/audit")
    assert kwargs["params"] == {"targetId": USER_ID, "limit": 25, "before": "2026-11-01T10:00:00Z"}
    assert resp.json() == {"before": {}, "after": {}, "auditId": "u" * 24}


@pytest.mark.anyio
async def test_audit_filters_by_actor(admin_session):
    _, _, kwargs = await _forwarded("GET", f"/audit?actorId={ADMIN_USER_ID}", admin_session)

    assert kwargs["params"] == {"actorId": ADMIN_USER_ID}


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=abc", "targetId="])
async def test_audit_rejects_a_bad_query(query, admin_session):
    assert (await _call("GET", f"/audit?{query}", admin_session)).status_code == 422
