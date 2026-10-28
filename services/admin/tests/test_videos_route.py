"""Tests for /videos proxy route — forwards global deletes to vie-api."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.services.vie_api_client import ACTOR_HEADER
from tests.conftest import ADMIN_USER_ID

YT = "dQw4w9WgXcQ"
URL = f"/videos/{YT}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ADMIN_API_KEY}"}


def _mock_async_client(response: MagicMock | None = None, error: Exception | None = None):
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


async def _delete(headers: dict[str, str], json: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request("DELETE", URL, headers=headers, json=json)


@pytest.mark.anyio
async def test_should_require_auth():
    resp = await _delete({})

    assert resp.status_code == 401


@pytest.mark.anyio
async def test_should_refuse_the_shared_machine_key():
    """A global purge is a mutation: it needs a named admin, never the shared key."""
    client_factory, instance = _mock_async_client(_upstream(200, {}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers())

    assert resp.status_code == 403
    instance.request.assert_not_called()


@pytest.mark.anyio
async def test_should_reject_a_malformed_youtube_id(admin_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request("DELETE", "/videos/not-an-id", headers=admin_session)

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_should_forward_admin_key_and_reason_to_vie_api(admin_session):
    body = {"youtubeId": YT, "summaryIds": ["a" * 24], "counts": {"userVideos": 2}, "warnings": []}
    client_factory, instance = _mock_async_client(_upstream(200, body))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session, json={"reason": "dup"})

    assert resp.status_code == 200
    assert resp.json() == body
    method, url = instance.request.call_args.args
    assert (method, url) == ("DELETE", f"{settings.VIE_API_URL}/api/admin/videos/{YT}")
    headers = instance.request.call_args.kwargs["headers"]
    assert headers["X-Admin-Key"] == settings.ADMIN_API_KEY
    assert headers["Content-Type"] == "application/json"
    assert instance.request.call_args.kwargs["content"] == b'{"reason":"dup"}'


@pytest.mark.anyio
async def test_should_carry_the_session_admin_as_a_signed_actor(admin_session):
    """vie-api learns who purged from the signature over the exact body bytes sent."""
    client_factory, instance = _mock_async_client(_upstream(200, {"warnings": []}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        await _delete(admin_session, json={"reason": "dup"})

    kwargs = instance.request.call_args.kwargs
    user_id, issued_at, signature = kwargs["headers"][ACTOR_HEADER].split(".")
    message = f"{user_id}|{issued_at}|{hashlib.sha256(kwargs['content']).hexdigest()}".encode()
    expected = hmac.new(settings.ADMIN_SESSION_SECRET.encode(), message, hashlib.sha256).hexdigest()
    assert (user_id, signature) == (ADMIN_USER_ID, expected)


@pytest.mark.anyio
async def test_should_sign_the_empty_json_object_when_no_reason_is_given(admin_session):
    client_factory, instance = _mock_async_client(_upstream(200, {"warnings": []}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        await _delete(admin_session)

    assert instance.request.call_args.kwargs["content"] == b"{}"


@pytest.mark.anyio
async def test_should_ignore_an_admin_id_in_the_body(admin_session):
    """Who purged is the session's admin; a body field can no longer say otherwise."""
    client_factory, instance = _mock_async_client(_upstream(200, {"warnings": []}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        await _delete(admin_session, json={"reason": "dup", "adminId": "someone-else"})

    kwargs = instance.request.call_args.kwargs
    assert kwargs["content"] == b'{"reason":"dup"}'
    assert "X-Admin-Id" not in kwargs["headers"]
    assert kwargs["headers"][ACTOR_HEADER].startswith(f"{ADMIN_USER_ID}.")


@pytest.mark.anyio
async def test_should_answer_504_when_the_purge_times_out(admin_session):
    client_factory, _ = _mock_async_client(error=httpx.ReadTimeout("timed out"))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session)

    assert resp.status_code == 504


@pytest.mark.anyio
async def test_should_answer_502_when_vie_api_is_unreachable(admin_session):
    client_factory, _ = _mock_async_client(error=httpx.ConnectError("refused"))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session)

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_should_answer_500_when_vie_api_rejects_the_admin_credentials(admin_session):
    client_factory, _ = _mock_async_client(_upstream(401, {"error": "UNAUTHORIZED"}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session)

    assert resp.status_code == 500


@pytest.mark.anyio
async def test_should_pass_a_permission_refusal_through(admin_session):
    """vie-api re-checks the permission; a demoted admin's 403 reaches the operator."""
    detail = {"error": "ADMIN_PERMISSION_DENIED", "message": "Missing permission: videos:purge"}
    client_factory, _ = _mock_async_client(_upstream(403, detail))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session)

    assert resp.status_code == 403
    assert resp.json()["detail"] == detail


@pytest.mark.anyio
async def test_should_pass_through_a_purge_failure_detail(admin_session):
    detail = {"error": "SUMMARIZER_PURGE_FAILED", "message": "Video purge failed: down"}
    client_factory, _ = _mock_async_client(_upstream(502, detail))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(admin_session)

    assert resp.status_code == 502
    assert resp.json()["detail"] == detail
