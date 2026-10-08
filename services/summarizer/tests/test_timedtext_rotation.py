"""Metadata-phase timedtext fetch: a 429 or bot check rotates through the sticky proxy exits.

Drives ``_fetch_subtitles_from_url_sync`` over real settings, so the exit list
comes from ``ytdlp_proxy_exit_urls`` (YOUTUBE_PROXY_EXIT_COUNT, wrap-around,
cap). Only ``requests.get`` is faked: it answers per exit and records the
exits tried, in order.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.services.media import download_utils
from src.services.video import youtube
from src.services.video.youtube import (
    _fetch_subtitles_from_url_sync,
    extract_video_data,
    fetch_video_captions,
)

URL = "http://example/timedtext"
BODY = json.dumps(
    {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}
).encode()


def _exit(n: int) -> str:
    return f"http://user-{n}:pass@p.webshare.io:80"


def _response(status: int, reason: str = "error") -> MagicMock:
    response = MagicMock()
    if status >= 400:
        http_response = requests.models.Response()
        http_response.status_code = status
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status} {reason}", response=http_response
        )
    response.iter_content.return_value = [BODY]
    return response


@pytest.fixture
def exits(monkeypatch):
    def _set(primary: int, count: int) -> None:
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", _exit(primary))
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", count)

    return _set


class _FakeTimedtext:
    def __init__(self) -> None:
        self.statuses: dict[str, int] = {}  # exit URL -> HTTP status (default 200)
        self.reasons: dict[str, str] = {}  # exit URL -> error text
        self.tried: list[str | None] = []
        self.sleep = MagicMock()  # tenacity's same-exit wait, patched in by the fixture

    def get(self, _url: str, *, proxies: dict[str, str] | None, **_kwargs: object) -> MagicMock:
        proxy = proxies["https"] if proxies else None
        self.tried.append(proxy)
        return _response(
            self.statuses.get(proxy or "", 200), self.reasons.get(proxy or "", "error")
        )

    def rate_limit(self, *exit_numbers: int) -> None:
        for n in exit_numbers:
            self.statuses[_exit(n)] = 429


@pytest.fixture
def timedtext():
    fake = _FakeTimedtext()
    with (
        patch.object(youtube.requests, "get", side_effect=fake.get),
        patch.object(youtube._fetch_subtitle_single_exit_sync.retry, "sleep") as sleep,
    ):
        fake.sleep = sleep
        yield fake


class TestTimedtextExitRotation:
    def test_should_rotate_to_the_next_exit_on_a_429_without_waiting(self, exits, timedtext):
        exits(primary=1, count=3)
        timedtext.rate_limit(1)

        segments, error = _fetch_subtitles_from_url_sync(URL)

        assert (error, [s.text for s in segments]) == (None, ["hi"])
        assert timedtext.tried == [_exit(1), _exit(2)]
        timedtext.sleep.assert_not_called()

    def test_should_wrap_around_to_the_first_exit(self, exits, timedtext):
        exits(primary=5, count=5)
        timedtext.rate_limit(5)

        _, error = _fetch_subtitles_from_url_sync(URL)

        assert error is None
        assert timedtext.tried == [_exit(5), _exit(1)]

    def test_should_cap_the_exits_tried(self, exits, timedtext):
        exits(primary=1, count=50)
        timedtext.rate_limit(*range(1, 51))

        _fetch_subtitles_from_url_sync(URL)

        assert timedtext.tried == [
            _exit(n) for n in range(1, download_utils._MAX_ROTATED_EXITS + 1)
        ]

    def test_should_report_http_429_when_every_tried_exit_429s(self, exits, timedtext):
        exits(primary=2, count=3)
        timedtext.rate_limit(1, 2, 3)

        assert _fetch_subtitles_from_url_sync(URL) == ([], "http_429")
        assert timedtext.tried == [_exit(2), _exit(3), _exit(1)]

    def test_should_stop_rotating_on_a_non_429_error(self, exits, timedtext):
        """A 403 after a 429 is not 'every exit 429'd' — it surfaces as itself."""
        exits(primary=1, count=3)
        timedtext.rate_limit(1)
        timedtext.statuses[_exit(2)] = 403

        assert _fetch_subtitles_from_url_sync(URL) == ([], "http_403")
        assert timedtext.tried == [_exit(1), _exit(2)]

    def test_should_rotate_to_the_next_exit_on_a_bot_check(self, exits, timedtext):
        exits(primary=1, count=3)
        timedtext.statuses[_exit(1)] = 403
        timedtext.reasons[_exit(1)] = "Sign in to confirm you’re not a bot"

        segments, error = _fetch_subtitles_from_url_sync(URL)

        assert (error, [s.text for s in segments]) == (None, ["hi"])
        assert timedtext.tried == [_exit(1), _exit(2)]

    def test_should_keep_the_same_exit_retry_with_a_single_exit(self, exits, timedtext):
        exits(primary=1, count=1)
        timedtext.rate_limit(1)

        assert _fetch_subtitles_from_url_sync(URL) == ([], "http_429")
        assert timedtext.tried == [_exit(1), _exit(1)]
        timedtext.sleep.assert_called_once()


class TestRateLimitedFlag:
    """``captions_rate_limited`` (what writes the caption negative cache) is set
    only when every exit the timedtext fetch tried returned 429."""

    @pytest.fixture
    def info(self):
        return {
            "id": "test123",
            "title": "Test Video",
            "uploader": "Test Channel",
            "duration": 300,
            "description": "",
            "thumbnails": [],
            "chapters": [],
            "automatic_captions": {"en": [{"ext": "json3", "url": URL}]},
            "subtitles": {},
        }

    @pytest.mark.parametrize(
        ("second_exit_status", "rate_limited"),
        [(429, True), (403, False), (200, False)],
    )
    async def test_should_flag_rate_limited_only_when_every_exit_429s(
        self, exits, timedtext, info, second_exit_status, rate_limited
    ):
        exits(primary=1, count=2)
        timedtext.rate_limit(1)
        timedtext.statuses[_exit(2)] = second_exit_status

        with patch.object(youtube, "_extract_with_retry", return_value=info):
            result = await extract_video_data("test123")
            await fetch_video_captions(result)

        assert result.captions_rate_limited is rate_limited
