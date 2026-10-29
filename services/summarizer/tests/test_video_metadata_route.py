"""Tests for the internal pre-flight metadata route (video_metadata.py).

yt-dlp is mocked at ``_extract_info_via_exits``, so ``extract_video_data`` and
its error wrapping run for real.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from yt_dlp.utils import DownloadError, ExtractorError

from src.config import settings
from src.main import app
from src.services.video import youtube

YT = "dQw4w9WgXcQ"
URL = "/video/metadata"
HEADERS = {"X-Internal-Secret": settings.INTERNAL_SECRET}

INFO = {
    "title": "Never Gonna Give You Up",
    "uploader": "Rick Astley",
    "duration": 212,
    "thumbnails": [{"url": "https://i.ytimg.com/low.jpg"}, {"url": "https://i.ytimg.com/hi.jpg"}],
    "description": "",
}


def _gone_error() -> DownloadError:
    """What yt-dlp raises for a private / removed / unknown video."""
    original = ExtractorError("Video unavailable", expected=True)
    return DownloadError("ERROR: [youtube] x: Video unavailable", (ExtractorError, original, None))


@pytest.fixture
def ytdlp(monkeypatch):
    """Stand-in for the yt-dlp extraction: set ``.result`` or ``.error``; ``.calls`` records ids."""

    class Stub:
        result: dict | None = INFO
        error: BaseException | None = None
        calls: list[str] = []

        def __call__(self, video_id: str):
            self.calls.append(video_id)
            if self.error is not None:
                raise self.error
            return self.result

    stub = Stub()
    stub.calls = []
    monkeypatch.setattr(youtube, "_extract_info_via_exits", stub)
    return stub


async def _get(params: dict[str, str] | None = None, headers: dict[str, str] | None = HEADERS):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(URL, params=params or {"youtubeId": YT}, headers=headers or {})


class TestVideoMetadataRoute:
    @pytest.mark.anyio
    async def test_should_return_the_pre_flight_facts_when_the_video_exists(self, ytdlp):
        response = await _get()

        assert response.status_code == 200
        assert response.json() == {
            "youtubeId": YT,
            "title": "Never Gonna Give You Up",
            "channel": "Rick Astley",
            "duration": 212,
            "thumbnailUrl": "https://i.ytimg.com/hi.jpg",
        }

    @pytest.mark.anyio
    async def test_should_ask_yt_dlp_once_for_the_requested_id(self, ytdlp):
        await _get()

        assert ytdlp.calls == [YT]

    @pytest.mark.anyio
    async def test_should_reject_without_internal_secret(self, ytdlp):
        response = await _get(headers=None)

        assert response.status_code == 401
        assert ytdlp.calls == []

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad_id", ["not-an-id", "short", "dQw4w9WgXcQ1", "dQw4w9WgXc!"])
    async def test_should_return_400_when_the_id_is_malformed(self, ytdlp, bad_id):
        response = await _get({"youtubeId": bad_id})

        assert response.status_code == 400
        assert ytdlp.calls == []

    @pytest.mark.anyio
    async def test_should_return_404_when_yt_dlp_says_the_video_is_unavailable(self, ytdlp):
        ytdlp.error = _gone_error()

        response = await _get()

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_should_return_404_when_yt_dlp_returns_no_info(self, ytdlp):
        ytdlp.result = None

        response = await _get()

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_should_return_503_when_youtube_bot_checks_every_exit(self, ytdlp):
        message = "Sign in to confirm you're not a bot"
        original = ExtractorError(message, expected=True)
        ytdlp.error = DownloadError(f"ERROR: {message}", (ExtractorError, original, None))

        response = await _get()

        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_should_return_503_when_the_transport_fails(self, ytdlp):
        ytdlp.error = ConnectionError("Connection reset by peer")

        response = await _get()

        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_should_return_422_when_the_video_is_a_live_stream(self, ytdlp):
        ytdlp.result = {**INFO, "is_live": True}

        response = await _get()

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_should_return_422_when_yt_dlp_has_no_duration(self, ytdlp):
        ytdlp.result = {**INFO, "duration": None}

        response = await _get()

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_should_not_leak_the_yt_dlp_message_in_the_error_detail(self, ytdlp):
        ytdlp.error = ConnectionError("proxy http://user:secret@proxy.example:8080 refused")

        response = await _get()

        assert "secret" not in response.text
