"""Tests for the internal pre-flight metadata route (video_metadata.py).

yt-dlp is mocked at ``_extract_info_via_exits``, so ``extract_video_data`` and
its error wrapping run for real. The Data API is mocked at the route's
``fetch_video``; conftest clears YOUTUBE_API_KEY, so only the ``data_api``
fixture turns that path on.
"""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from yt_dlp.utils import DownloadError, ExtractorError

from src.config import settings
from src.main import app
from src.routes import video_metadata
from src.services.video import youtube
from src.services.video.youtube_data_api import DataApiUnavailable, DataApiVideo

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


API_KEY = "AIza-test-key"
API_VIDEO = DataApiVideo(
    video_id=YT,
    title="Never Gonna Give You Up (Official Video)",
    channel="Rick Astley",
    duration=213,
    thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
    live_broadcast="none",
    restriction=None,
)


@pytest.fixture
def data_api(monkeypatch):
    """YOUTUBE_API_KEY set and ``fetch_video`` stubbed: set ``.result`` or ``.error``."""

    class Stub:
        result: DataApiVideo = API_VIDEO
        error: DataApiUnavailable | None = None
        calls: list[tuple[str, str]] = []

        async def __call__(self, video_id: str, *, api_key: str) -> DataApiVideo:
            self.calls.append((video_id, api_key))
            if self.error is not None:
                raise self.error
            return self.result

    stub = Stub()
    stub.calls = []
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", API_KEY)
    monkeypatch.setattr(video_metadata, "fetch_video", stub)
    return stub


@pytest.fixture
def route_logger(monkeypatch) -> MagicMock:
    logger = MagicMock()
    monkeypatch.setattr(video_metadata, "logger", logger)
    return logger


def _fallback_logs(logger: MagicMock) -> list[dict]:
    return [
        call.kwargs
        for call in logger.warning.call_args_list
        if call.args == ("video_metadata_data_api_fallback",)
    ]


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


class TestDataApiFirst:
    @pytest.mark.anyio
    async def test_should_serve_the_pre_flight_from_the_data_api_without_touching_yt_dlp(
        self, data_api, ytdlp, route_logger
    ):
        response = await _get()

        assert response.status_code == 200
        assert response.json() == {
            "youtubeId": YT,
            "title": "Never Gonna Give You Up (Official Video)",
            "channel": "Rick Astley",
            "duration": 213,
            "thumbnailUrl": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        }
        assert data_api.calls == [(YT, API_KEY)]
        assert ytdlp.calls == []
        assert _fallback_logs(route_logger) == []

    @pytest.mark.anyio
    async def test_should_return_422_when_the_data_api_says_the_video_is_live(
        self, data_api, ytdlp
    ):
        data_api.result = replace(API_VIDEO, live_broadcast="live", duration=0)

        response = await _get()

        assert response.status_code == 422
        assert response.json()["detail"] == "Live streams are not supported"
        assert ytdlp.calls == []

    @pytest.mark.anyio
    async def test_should_return_422_when_the_premiere_has_not_started(self, data_api, ytdlp):
        data_api.result = replace(API_VIDEO, live_broadcast="upcoming")

        response = await _get()

        assert response.status_code == 422
        assert response.json()["detail"] == "Premiere has not started yet"
        assert ytdlp.calls == []

    @pytest.mark.anyio
    async def test_should_return_422_when_the_data_api_has_no_duration_yet(self, data_api, ytdlp):
        data_api.result = replace(API_VIDEO, duration=0)

        response = await _get()

        assert response.status_code == 422
        assert response.json()["detail"] == "Video has no duration yet"
        assert ytdlp.calls == []

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("reason", "api_reason"),
        [
            ("not_found", None),
            ("quota_exceeded", "quotaExceeded"),
            ("forbidden", "API_KEY_SERVICE_BLOCKED"),
            ("key_invalid", "API_KEY_INVALID"),
            ("rate_limited", "rateLimitExceeded"),
            ("timeout", "ReadTimeout"),
            ("http_500", None),
        ],
    )
    async def test_should_fall_back_to_yt_dlp_and_log_the_reason_when_the_data_api_is_unavailable(
        self, data_api, ytdlp, route_logger, reason, api_reason
    ):
        data_api.error = DataApiUnavailable(reason, api_reason)

        response = await _get()

        assert response.status_code == 200
        assert response.json()["title"] == "Never Gonna Give You Up"
        assert ytdlp.calls == [YT]
        assert _fallback_logs(route_logger) == [
            {"youtube_id": YT, "reason": reason, "api_reason": api_reason}
        ]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("restriction", "reason"), [("age", "age_restricted"), ("region", "region_restricted")]
    )
    async def test_should_fall_back_to_yt_dlp_when_the_video_is_restricted(
        self, data_api, ytdlp, route_logger, restriction, reason
    ):
        data_api.result = replace(API_VIDEO, restriction=restriction)

        response = await _get()

        assert response.status_code == 200
        assert ytdlp.calls == [YT]
        assert _fallback_logs(route_logger) == [
            {"youtube_id": YT, "reason": reason, "api_reason": None}
        ]

    @pytest.mark.anyio
    async def test_should_return_404_when_neither_the_data_api_nor_yt_dlp_knows_the_video(
        self, data_api, ytdlp
    ):
        data_api.error = DataApiUnavailable("not_found")
        ytdlp.error = _gone_error()

        response = await _get()

        assert response.status_code == 404
        assert ytdlp.calls == [YT]

    @pytest.mark.anyio
    async def test_should_keep_yt_dlp_failures_mapped_as_before_after_a_fallback(
        self, data_api, ytdlp
    ):
        data_api.error = DataApiUnavailable("quota_exceeded", "quotaExceeded")
        message = "Sign in to confirm you're not a bot"
        original = ExtractorError(message, expected=True)
        ytdlp.error = DownloadError(f"ERROR: {message}", (ExtractorError, original, None))

        response = await _get()

        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_should_not_call_the_data_api_when_the_key_is_blank(
        self, data_api, ytdlp, monkeypatch
    ):
        monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "  ")

        response = await _get()

        assert response.status_code == 200
        assert data_api.calls == []
        assert ytdlp.calls == [YT]

    @pytest.mark.anyio
    async def test_should_not_call_the_data_api_for_a_malformed_id(self, data_api, ytdlp):
        response = await _get({"youtubeId": "not-an-id"})

        assert response.status_code == 400
        assert data_api.calls == []
