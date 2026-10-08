"""Metadata ``extract_info``: a bot check (or 429) rotates through the sticky proxy exits.

2026-10-08 YouTube answered "Sign in to confirm you're not a bot" on the main
Webshare exit for every video while exits 2 and 3 still worked; without
rotation every job failed at metadata in seconds. Drives
``_extract_video_info_sync`` over real settings, so the exit list comes from
``ytdlp_proxy_exit_urls``. Only ``yt_dlp.YoutubeDL`` is faked: it answers per
proxy exit and records the exits tried, in order.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.media import download_utils
from src.services.video import youtube
from src.services.video.youtube import _extract_video_info_sync

VIDEO_ID = "T1dQhQAm8Tc"
BOT_CHECK = (
    f"ERROR: [youtube] {VIDEO_ID}: Sign in to confirm you’re not a bot. Use "
    "--cookies-from-browser or --cookies for the authentication."
)
INFO = {
    "id": VIDEO_ID,
    "title": "Fetched Title",
    "uploader": "Channel",
    "duration": 300,
    "description": "",
    "thumbnails": [],
    "chapters": [],
    "automatic_captions": {},
    "subtitles": {},
}


def _exit(n: int) -> str:
    return f"http://user-{n}:pass@p.webshare.io:80"


class _FakeYoutubeDL:
    def __init__(self) -> None:
        self.errors: dict[str | None, str] = {}  # proxy -> DownloadError message
        self.tried: list[str | None] = []

    def build(self, opts: dict) -> MagicMock:
        proxy = opts.get("proxy")
        self.tried.append(proxy)
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        if proxy in self.errors:
            ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(self.errors[proxy])
        else:
            ydl.extract_info.return_value = INFO
        return ydl


@pytest.fixture
def exits(monkeypatch):
    def _set(primary: int | None, count: int = 3) -> None:
        proxy = _exit(primary) if primary is not None else None
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", proxy)
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", count)

    return _set


@pytest.fixture
def ydl():
    fake = _FakeYoutubeDL()
    with patch.object(youtube.yt_dlp, "YoutubeDL", side_effect=fake.build):
        yield fake


class TestMetadataExitRotation:
    def test_should_extract_on_the_next_exit_when_the_first_gets_the_bot_check(self, exits, ydl):
        exits(primary=1)
        ydl.errors[_exit(1)] = BOT_CHECK

        video_data, _ = _extract_video_info_sync(VIDEO_ID)

        assert (video_data.title, ydl.tried) == ("Fetched Title", [_exit(1), _exit(2)])

    def test_should_rotate_on_a_429(self, exits, ydl):
        exits(primary=1)
        ydl.errors[_exit(1)] = "ERROR: [youtube] x: HTTP Error 429: Too Many Requests"

        _extract_video_info_sync(VIDEO_ID)

        assert ydl.tried == [_exit(1), _exit(2)]

    def test_should_raise_todays_error_when_every_exit_gets_the_bot_check(self, exits, ydl):
        exits(primary=2)
        ydl.errors.update({_exit(n): BOT_CHECK for n in (1, 2, 3)})

        with pytest.raises(TranscriptError) as exc_info:
            _extract_video_info_sync(VIDEO_ID)

        assert exc_info.value.code is ErrorCode.VIDEO_UNAVAILABLE
        assert "confirm you’re not a bot" in exc_info.value.message
        assert ydl.tried == [_exit(2), _exit(3), _exit(1)]

    @pytest.mark.parametrize(
        "message",
        [
            "ERROR: [youtube] x: Sign in to confirm your age. "
            "This video may be inappropriate for some users.",
            "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
            "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader",
            "ERROR: [youtube] x: HTTP Error 404: Not Found",
        ],
    )
    def test_should_not_rotate_on_an_error_every_exit_would_share(self, exits, ydl, message):
        exits(primary=1)
        ydl.errors[_exit(1)] = message

        with pytest.raises(TranscriptError) as exc_info:
            _extract_video_info_sync(VIDEO_ID)

        assert exc_info.value.code is ErrorCode.VIDEO_UNAVAILABLE
        assert ydl.tried == [_exit(1)]

    def test_should_skip_the_blocked_exit_on_the_next_extract(self, exits, ydl):
        exits(primary=1)
        ydl.errors[_exit(1)] = BOT_CHECK
        _extract_video_info_sync(VIDEO_ID)
        ydl.tried.clear()

        _extract_video_info_sync(VIDEO_ID)

        assert ydl.tried == [_exit(2)]

    def test_should_try_the_blocked_exit_last_when_the_others_fail_too(self, exits, ydl):
        exits(primary=1)
        ydl.errors[_exit(1)] = BOT_CHECK
        _extract_video_info_sync(VIDEO_ID)
        ydl.errors.update({_exit(2): BOT_CHECK, _exit(3): BOT_CHECK})
        ydl.tried.clear()

        with pytest.raises(TranscriptError):
            _extract_video_info_sync(VIDEO_ID)

        assert ydl.tried == [_exit(2), _exit(3), _exit(1)]

    def test_should_start_another_video_on_the_next_exit(self, exits, ydl):
        exits(primary=1)
        _extract_video_info_sync(VIDEO_ID)
        ydl.tried.clear()

        _extract_video_info_sync("jNQXAC9IVRw")

        assert ydl.tried == [_exit(2)]

    def test_should_make_one_attempt_with_a_single_exit(self, exits, ydl):
        exits(primary=1, count=1)
        ydl.errors[_exit(1)] = BOT_CHECK

        with pytest.raises(TranscriptError):
            _extract_video_info_sync(VIDEO_ID)

        assert ydl.tried == [_exit(1)]

    def test_should_extract_directly_without_a_proxy(self, exits, ydl):
        exits(primary=None)

        _extract_video_info_sync(VIDEO_ID)

        assert ydl.tried == [None]

    def test_should_log_the_exit_that_worked_without_credentials(self, exits, ydl, caplog):
        exits(primary=1)
        ydl.errors[_exit(1)] = BOT_CHECK

        with caplog.at_level("INFO"):
            _extract_video_info_sync(VIDEO_ID)

        assert "succeeded on proxy exit 2/3 (session -2)" in caplog.text
        assert "pass@" not in caplog.text
