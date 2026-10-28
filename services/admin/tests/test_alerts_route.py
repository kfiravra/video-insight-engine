"""Alert thresholds: changing them is a named-admin mutation that goes through vie-api."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from tests._actor import assert_signed_for

URL = "/alerts/config"
BODY = {
    "cost_threshold_usd": 1.5,
    "daily_spike_multiplier": 3.0,
    "failure_rate_threshold": 0.4,
    "reason": "noisy during the Friday batch",
}
MACHINE = {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client(status: int = 200, body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = json.dumps(body or {})
    resp.json = MagicMock(return_value=body or {"before": {}, "after": {}, "auditId": "a" * 24})
    instance = MagicMock()
    instance.request = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), instance


async def _post(headers: dict[str, str], body: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(URL, headers=headers, json=BODY if body is None else body)


@pytest.mark.anyio
async def test_requires_auth():
    assert (await _post({})).status_code == 401


@pytest.mark.anyio
async def test_refuses_the_shared_machine_key():
    factory, instance = _client()

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _post(MACHINE)

    assert resp.status_code == 403
    instance.request.assert_not_called()


@pytest.mark.anyio
async def test_forwards_the_thresholds_and_reason_to_vie_api(admin_session):
    factory, instance = _client()

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _post(admin_session)

    assert resp.status_code == 200
    call = instance.request.call_args
    assert json.loads(call.kwargs["content"]) == BODY
    assert_signed_for(
        call, method="POST", target="/api/admin/alerts/config", body=call.kwargs["content"]
    )


@pytest.mark.anyio
async def test_returns_vie_apis_before_and_after(admin_session):
    answer = {
        "before": {"cost_threshold_usd": 0.5},
        "after": {"cost_threshold_usd": 1.5},
        "auditId": "a" * 24,
    }
    factory, _ = _client(body=answer)

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _post(admin_session)

    assert resp.json() == answer


@pytest.mark.anyio
@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 501])
async def test_requires_a_reason(admin_session, reason):
    body = {k: v for k, v in BODY.items() if k != "reason"}
    if reason is not None:
        body["reason"] = reason

    resp = await _post(admin_session, body)

    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        {"cost_threshold_usd": -1},
        {"daily_spike_multiplier": 0.5},
        {"failure_rate_threshold": 1.5},
        {"adminId": "x"},
    ],
)
async def test_rejects_out_of_range_thresholds_and_unknown_fields(admin_session, change):
    resp = await _post(admin_session, {**BODY, **change})

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_passes_vie_apis_permission_refusal_through(admin_session):
    factory, _ = _client(status=403, body={"error": "ADMIN_PERMISSION_DENIED"})

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _post(admin_session)

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_writes_nothing_to_the_alert_config_collection_itself(admin_session, monkeypatch):
    """vie-api is the writer now; vie-admin must not open the database for a change."""
    opened = MagicMock(
        side_effect=AssertionError("alert config changes must not touch the database here")
    )
    monkeypatch.setattr("src.routes.alerts.get_database", opened)
    factory, _ = _client()

    with patch("src.services.vie_api_client.httpx.AsyncClient", factory):
        resp = await _post(admin_session)

    assert resp.status_code == 200
    opened.assert_not_called()
