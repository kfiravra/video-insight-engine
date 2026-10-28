"""Auth + validation tests for the per-user cost endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


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


@pytest.mark.anyio
async def test_grant_credit_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/users/" + "a" * 24 + "/grant-credit",
            json={"amountUsd": 1, "reason": "test", "adminId": "a" * 24},
        )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_grant_credit_rejects_out_of_range_amount(admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/users/" + "a" * 24 + "/grant-credit",
            headers=admin_session,
            json={"amountUsd": 10_000, "reason": "ok", "adminId": "a" * 24},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_rejects_empty_reason(admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/users/" + "a" * 24 + "/grant-credit",
            headers=admin_session,
            json={"amountUsd": 1.0, "reason": "", "adminId": "a" * 24},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_rejects_invalid_date_format(admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/users/" + "a" * 24 + "/grant-credit",
            headers=admin_session,
            json={"amountUsd": 1.0, "reason": "ok", "adminId": "a" * 24, "date": "2026-1-1"},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_grant_credit_refuses_the_shared_machine_key():
    """The machine key reads; crediting a user needs a named admin session."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/users/" + "a" * 24 + "/grant-credit",
            headers={"Authorization": "Bearer test-admin-key"},
            json={"amountUsd": 1.0, "reason": "ok", "adminId": "a" * 24},
        )
    assert resp.status_code == 403
