"""Tests for /queue proxy route — forwards to vie-api admin queue endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.services.vie_api_client import ACTOR_HEADER
from tests._actor import assert_signed_for
from tests.conftest import ADMIN_USER_ID


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


def _mock_async_client(response: MagicMock) -> MagicMock:
    """A context-manager mock yielding an AsyncClient whose `.request` returns `response`."""
    instance = MagicMock()
    instance.request = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.anyio
async def test_queue_stats_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue/stats")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_queue_stats_wrong_key():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue/stats", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_queue_stats_proxies_response_body():
    """Happy path: vie-api returns stats JSON, admin returns it verbatim."""
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(
        return_value={
            "main": {"messages": 3, "ready": 2, "inFlight": 1, "consumers": 2},
            "dlq": {"messages": 0},
        }
    )

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/stats", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["main"]["consumers"] == 2
    assert body["dlq"]["messages"] == 0


@pytest.mark.anyio
async def test_queue_stats_returns_502_when_vie_api_unreachable():
    """Connection failures to vie-api must surface as 502, not 500."""

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    instance = MagicMock()
    instance.request = AsyncMock(side_effect=_raise)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.routes.queue.httpx.AsyncClient", MagicMock(return_value=cm)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/stats", headers=_auth_headers())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "vie-api unreachable"


@pytest.mark.anyio
async def test_queue_stats_returns_500_on_admin_key_mismatch():
    """vie-api rejecting our admin key is a server misconfig, not a client error."""
    upstream = MagicMock()
    upstream.status_code = 401
    upstream.json = MagicMock(return_value={"error": "UNAUTHORIZED"})

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/stats", headers=_auth_headers())

    assert resp.status_code == 500


@pytest.mark.anyio
async def test_queue_dlq_validates_limit_lower_bound():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue/dlq?limit=0", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_queue_dlq_validates_limit_upper_bound():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue/dlq?limit=500", headers=_auth_headers())
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_queue_dlq_forwards_limit_param():
    """limit= must be passed through to vie-api as a query string parameter."""
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"messages": []})

    instance = MagicMock()
    instance.request = AsyncMock(return_value=upstream)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.routes.queue.httpx.AsyncClient", MagicMock(return_value=cm)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/dlq?limit=42", headers=_auth_headers())

    assert resp.status_code == 200
    assert instance.request.await_count == 1
    call = instance.request.await_args
    assert str(call.args[1]).endswith("/api/admin/queue/dlq?limit=42")
    assert call.kwargs["headers"]["X-Admin-Key"] == settings.ADMIN_API_KEY


def _mock_async_client_post(response: MagicMock) -> MagicMock:
    instance = MagicMock()
    instance.request = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


@pytest.mark.anyio
async def test_queue_replay_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/queue/replay", json={"max": 5})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_queue_replay_forwards_max_and_returns_count(admin_session):
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"replayed": 3})
    client_factory = _mock_async_client_post(upstream)

    with patch("src.routes.queue.httpx.AsyncClient", client_factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/queue/replay", json={"max": 3}, headers=admin_session)

    assert resp.status_code == 200
    assert resp.json() == {"replayed": 3}
    post = client_factory.return_value.__aenter__.return_value.request
    assert str(post.await_args.args[1]).endswith("/api/admin/queue/replay")
    assert post.await_args.kwargs["content"] == b'{"max":3}'
    assert post.await_args.kwargs["headers"]["X-Admin-Key"] == settings.ADMIN_API_KEY


@pytest.mark.anyio
async def test_queue_replay_defaults_max_to_100(admin_session):
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"replayed": 0})
    client_factory = _mock_async_client_post(upstream)

    with patch("src.routes.queue.httpx.AsyncClient", client_factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/queue/replay", json={}, headers=admin_session)

    assert resp.status_code == 200
    post = client_factory.return_value.__aenter__.return_value.request
    assert post.await_args.kwargs["content"] == b'{"max":100}'


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [0, 501])
async def test_queue_replay_validates_max_bounds(bad, admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/queue/replay", json={"max": bad}, headers=admin_session)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_queue_replay_passes_through_queue_disabled_503(admin_session):
    """vie-api answers 503 QUEUE_DISABLED when USE_QUEUE_PIPELINE is off."""
    upstream = MagicMock()
    upstream.status_code = 503
    upstream.text = '{"error":"QUEUE_DISABLED"}'

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client_post(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/queue/replay", json={"max": 1}, headers=admin_session)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "QUEUE_DISABLED"


@pytest.mark.anyio
async def test_queue_replay_keeps_partial_count_on_mid_drain_failure(admin_session):
    """vie-api's 502 REPLAY_FAILED carries how many messages were already
    re-published; that count must survive the proxy, not collapse to a status."""
    upstream = MagicMock()
    upstream.status_code = 502
    upstream.text = '{"error":"REPLAY_FAILED","message":"channel closed","replayed":7}'

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client_post(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/queue/replay", json={"max": 50}, headers=admin_session)

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error"] == "REPLAY_FAILED"
    assert detail["replayed"] == 7


@pytest.mark.anyio
async def test_queue_stats_does_not_forward_non_json_upstream_error_body():
    """An HTML/proxy error page from upstream is replaced by a generic line."""
    upstream = MagicMock()
    upstream.status_code = 504
    upstream.text = "<html>gateway timeout</html>"

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/stats", headers=_auth_headers())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "vie-api error: 504"


@pytest.mark.anyio
async def test_queue_stats_returns_502_when_upstream_success_body_is_not_json():
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(side_effect=ValueError("Expecting value"))

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/queue/stats", headers=_auth_headers())

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_queue_replay_refuses_the_shared_machine_key():
    """Replaying the DLQ is a mutation: the machine key may only read."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/queue/replay", json={"max": 5}, headers=_auth_headers())
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_queue_replay_names_the_admin_to_vie_api(admin_session):
    """The proxied call carries a signed actor header for the session's user."""
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"replayed": 1})
    client_factory = _mock_async_client_post(upstream)

    with patch("src.routes.queue.httpx.AsyncClient", client_factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/queue/replay", json={"max": 7}, headers=admin_session)

    call = client_factory.return_value.__aenter__.return_value.request.await_args
    assert_signed_for(call, method="POST", target="/api/admin/queue/replay", body=b'{"max":7}')


@pytest.mark.anyio
async def test_queue_stats_for_a_session_names_the_admin_to_vie_api(admin_session):
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"main": {}, "dlq": {}})

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)) as factory:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/queue/stats", headers=admin_session)

    headers = factory.return_value.__aenter__.return_value.request.await_args.kwargs["headers"]
    assert headers[ACTOR_HEADER].startswith(f"{ADMIN_USER_ID}.")


@pytest.mark.anyio
async def test_queue_stats_for_the_machine_key_sends_no_actor():
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json = MagicMock(return_value={"main": {}, "dlq": {}})

    with patch("src.routes.queue.httpx.AsyncClient", _mock_async_client(upstream)) as factory:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/queue/stats", headers=_auth_headers())

    headers = factory.return_value.__aenter__.return_value.request.await_args.kwargs["headers"]
    assert ACTOR_HEADER not in headers
