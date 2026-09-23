"""Tests for /videos proxy route — forwards global deletes to vie-api."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app

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
async def test_should_reject_a_malformed_youtube_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request("DELETE", "/videos/not-an-id", headers=_auth_headers())

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_should_forward_admin_key_operator_id_and_reason_to_vie_api():
    body = {"youtubeId": YT, "summaryIds": ["a" * 24], "counts": {"userVideos": 2}, "warnings": []}
    client_factory, instance = _mock_async_client(_upstream(200, body))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers(), json={"reason": "dup", "adminId": "kfir"})

    assert resp.status_code == 200
    assert resp.json() == body
    method, url = instance.request.call_args.args
    assert (method, url) == ("DELETE", f"{settings.VIE_API_URL}/api/admin/videos/{YT}")
    headers = instance.request.call_args.kwargs["headers"]
    assert headers["X-Admin-Key"] == settings.ADMIN_API_KEY
    assert headers["X-Admin-Id"] == "kfir"
    assert instance.request.call_args.kwargs["content"] == '{"reason": "dup"}'


@pytest.mark.anyio
async def test_should_reject_an_admin_id_with_header_control_characters():
    resp = await _delete(_auth_headers(), json={"adminId": "kfir\r\nX-Evil: 1"})

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_should_answer_504_when_the_purge_times_out():
    client_factory, _ = _mock_async_client(error=httpx.ReadTimeout("timed out"))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers())

    assert resp.status_code == 504


@pytest.mark.anyio
async def test_should_answer_502_when_vie_api_is_unreachable():
    client_factory, _ = _mock_async_client(error=httpx.ConnectError("refused"))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers())

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_should_answer_500_when_vie_api_rejects_the_admin_key():
    client_factory, _ = _mock_async_client(_upstream(401, {"error": "UNAUTHORIZED"}))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers())

    assert resp.status_code == 500


@pytest.mark.anyio
async def test_should_pass_through_a_purge_failure_detail():
    detail = {"error": "SUMMARIZER_PURGE_FAILED", "message": "Video purge failed: down"}
    client_factory, _ = _mock_async_client(_upstream(502, detail))

    with patch("src.routes.videos.httpx.AsyncClient", client_factory):
        resp = await _delete(_auth_headers())

    assert resp.status_code == 502
    assert resp.json()["detail"] == detail
