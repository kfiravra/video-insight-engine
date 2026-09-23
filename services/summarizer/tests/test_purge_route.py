"""Tests for the internal video purge route (purge.py)."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.services.video_purge import PurgeOutcome

YT = "dQw4w9WgXcQ"
URL = f"/internal/videos/{YT}/purge"
SUMMARY_ID = "6a7b2f6abacdb32871507996"


@pytest.fixture
def purge_calls(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    async def fake_purge(youtube_id: str, video_summary_ids: list[str]) -> PurgeOutcome:
        calls.append((youtube_id, video_summary_ids))
        return PurgeOutcome(qdrant_points=12, s3_objects=34, redis_keys=2, warnings=["s3: boom"])

    monkeypatch.setattr("src.routes.purge.purge_video", fake_purge)
    return calls


async def _post(path: str, headers: dict[str, str] | None = None, json: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=json if json is not None else {}, headers=headers or {})


class TestPurgeRoute:
    @pytest.mark.anyio
    async def test_should_reject_without_internal_secret(self, purge_calls):
        response = await _post(URL)

        assert response.status_code == 401
        assert purge_calls == []

    @pytest.mark.anyio
    async def test_should_reject_a_malformed_youtube_id(self, purge_calls):
        response = await _post(
            "/internal/videos/not-an-id/purge",
            headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
        )

        assert response.status_code == 422
        assert purge_calls == []

    @pytest.mark.anyio
    async def test_should_reject_a_malformed_summary_id(self, purge_calls):
        response = await _post(
            URL,
            headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
            json={"videoSummaryIds": ["nope"]},
        )

        assert response.status_code == 422
        assert purge_calls == []

    @pytest.mark.anyio
    async def test_should_return_counts_and_warnings_when_purged(self, purge_calls):
        response = await _post(
            URL,
            headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
            json={"videoSummaryIds": [SUMMARY_ID]},
        )

        assert response.status_code == 200
        assert response.json() == {
            "youtubeId": YT,
            "qdrantPoints": 12,
            "s3Objects": 34,
            "redisKeys": 2,
            "warnings": ["s3: boom"],
        }
        assert purge_calls == [(YT, [SUMMARY_ID])]
