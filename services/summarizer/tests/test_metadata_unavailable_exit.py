"""A plain "Video unavailable" on a round-robin exit is confirmed on the configured exit.

2026-11-04 prod incident: YouTube answered several sticky Webshare exits with
"Video unavailable" (no bot-check or rate-limit wording, so no rotation), while
YOUTUBE_PROXY_URL's own exit served the same videos; every metadata pre-flight
on another exit became a 404.
"""

from __future__ import annotations

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from src.exceptions import TranscriptError
from src.services.media import download_utils
from src.services.video import youtube
from src.services.video.youtube import extract_video_data

INFO = {
    "title": "Found",
    "uploader": "Channel",
    "duration": 120,
    "description": "",
    "thumbnails": [],
}


def _exit(n: int) -> str:
    return f"http://user-{n}:pass@p.webshare.io:80"


def _unavailable() -> DownloadError:
    original = ExtractorError("Video unavailable", expected=True)
    return DownloadError("ERROR: [youtube] x: Video unavailable", (ExtractorError, original, None))


@pytest.fixture
def three_exits(monkeypatch):
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", _exit(1))
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 3)


@pytest.fixture
def ytdlp(monkeypatch):
    """``_extract_with_retry`` by exit: an exit in ``unavailable`` raises, others return INFO."""

    class Fake:
        unavailable: set[str] = set()
        calls: list[str] = []

        def __call__(self, url, opts):
            self.calls.append(opts["proxy"])
            if opts["proxy"] in self.unavailable:
                raise _unavailable()
            return dict(INFO)

    fake = Fake()
    fake.unavailable = set()
    fake.calls = []
    monkeypatch.setattr(youtube, "_extract_with_retry", fake)
    return fake


def _start_on_second_exit() -> None:
    """The first job key takes the configured exit; the next one starts on exit 2."""
    download_utils.ytdlp_proxy_exit_urls("warm-up", limit=None)


class TestUnavailableOnRoundRobinExit:
    async def test_should_serve_the_video_from_the_configured_exit(self, three_exits, ytdlp):
        _start_on_second_exit()
        ytdlp.unavailable = {_exit(2)}

        video = await extract_video_data("video2")

        assert video.title == "Found"
        assert ytdlp.calls == [_exit(2), _exit(1)]

    async def test_should_move_the_flagged_exit_behind_the_others(self, three_exits, ytdlp):
        _start_on_second_exit()
        ytdlp.unavailable = {_exit(2)}

        await extract_video_data("video2")

        assert download_utils.ytdlp_proxy_exit_urls("video2", limit=None)[-1] == _exit(2)

    async def test_should_fail_when_the_configured_exit_also_says_unavailable(
        self, three_exits, ytdlp
    ):
        _start_on_second_exit()
        ytdlp.unavailable = {_exit(1), _exit(2), _exit(3)}

        with pytest.raises(TranscriptError):
            await extract_video_data("video2")

        assert ytdlp.calls == [_exit(2), _exit(1)]

    async def test_should_not_retry_when_the_job_already_started_on_the_configured_exit(
        self, three_exits, ytdlp
    ):
        ytdlp.unavailable = {_exit(1), _exit(2), _exit(3)}

        with pytest.raises(TranscriptError):
            await extract_video_data("first-job")

        assert ytdlp.calls == [_exit(1)]

    async def test_should_not_retry_other_errors_on_the_configured_exit(
        self, three_exits, monkeypatch
    ):
        _start_on_second_exit()
        calls: list[str] = []

        def private(url, opts):
            calls.append(opts["proxy"])
            original = ExtractorError("Private video", expected=True)
            raise DownloadError(
                "ERROR: [youtube] x: Private video", (ExtractorError, original, None)
            )

        monkeypatch.setattr(youtube, "_extract_with_retry", private)

        with pytest.raises(TranscriptError):
            await extract_video_data("video2")

        assert calls == [_exit(2)]
