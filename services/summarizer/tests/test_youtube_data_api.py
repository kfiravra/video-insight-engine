"""youtube_data_api: one videos.list call, every failure a closed reason, the key never in the URL."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from src.services.video.youtube_data_api import (
    DATA_API_URL,
    DataApiUnavailable,
    DataApiVideo,
    fetch_video,
    parse_iso8601_duration,
)

YT = "dQw4w9WgXcQ"
KEY = "AIza-test-key-000000000000000000000000"

ITEM: dict[str, Any] = {
    "id": YT,
    "snippet": {
        "title": "Never Gonna Give You Up",
        "channelTitle": "Rick Astley",
        "thumbnails": {
            "default": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"},
            "maxres": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"},
        },
        "liveBroadcastContent": "none",
    },
    "contentDetails": {"duration": "PT3M32S", "contentRating": {}},
}


def _item(
    snippet: dict[str, Any] | None = None, content: dict[str, Any] | None = None
) -> dict[str, Any]:
    item = copy.deepcopy(ITEM)
    item["snippet"].update(snippet or {})
    item["contentDetails"].update(content or {})
    return item


def _ok(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"items": list(items)})


def _google_error(
    status: int,
    *,
    reason: str | None = None,
    rpc_status: str | None = None,
    detail_reason: str | None = None,
) -> httpx.Response:
    """A Google error body: legacy ``errors[]`` plus the One-Platform ``status`` / ``details[]``."""
    error: dict[str, Any] = {"code": status, "message": "x"}
    if reason is not None:
        error["errors"] = [{"message": "x", "domain": "global", "reason": reason}]
    if rpc_status is not None:
        error["status"] = rpc_status
    if detail_reason is not None:
        error["details"] = [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": detail_reason,
                "domain": "googleapis.com",
            }
        ]
    return httpx.Response(status, json={"error": error})


class _Google:
    """MockTransport handler: answers ``response`` or raises ``error``; records every request."""

    def __init__(
        self, response: httpx.Response | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


async def _fetch(google: _Google) -> DataApiVideo:
    async with httpx.AsyncClient(transport=httpx.MockTransport(google)) as client:
        return await fetch_video(YT, api_key=KEY, client=client)


async def _failure(google: _Google) -> DataApiUnavailable:
    with pytest.raises(DataApiUnavailable) as info:
        await _fetch(google)
    return info.value


class TestParseIso8601Duration:
    @pytest.mark.parametrize(
        ("value", "seconds"),
        [
            ("PT3M32S", 212),
            ("PT1H", 3600),
            ("PT45S", 45),
            ("P1DT2H3M4S", 93784),
            ("P0D", 0),
            ("PT0S", 0),
        ],
    )
    def test_should_parse_the_shapes_videos_list_writes(self, value: str, seconds: int) -> None:
        assert parse_iso8601_duration(value) == seconds

    @pytest.mark.parametrize("value", ["", "P", "PT", "P1W", "3:32", "PT1.5S", "212", None, 212])
    def test_should_reject_anything_else(self, value: object) -> None:
        assert parse_iso8601_duration(value) is None


class TestFetchVideo:
    async def test_should_map_the_item_to_the_pre_flight_facts(self) -> None:
        video = await _fetch(_Google(_ok(ITEM)))

        assert video == DataApiVideo(
            video_id=YT,
            title="Never Gonna Give You Up",
            channel="Rick Astley",
            duration=212,
            thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            live_broadcast="none",
            restriction=None,
        )

    async def test_should_send_the_key_in_the_header_and_never_in_the_url(self) -> None:
        google = _Google(_ok(ITEM))

        await _fetch(google)

        (request,) = google.requests
        assert request.method == "GET"
        assert str(request.url).startswith(DATA_API_URL)
        assert request.headers["x-goog-api-key"] == KEY
        assert "key" not in request.url.params
        assert KEY not in str(request.url)
        assert request.url.params["id"] == YT
        assert request.url.params["part"] == "snippet,contentDetails"
        assert request.url.params["fields"].startswith("items(id,snippet(")

    async def test_should_prefer_the_largest_thumbnail(self) -> None:
        thumbnails = {
            "default": {"url": "https://i.ytimg.com/vi/x/default.jpg"},
            "medium": {"url": "https://i.ytimg.com/vi/x/mqdefault.jpg"},
            "standard": {"url": "https://i.ytimg.com/vi/x/sddefault.jpg"},
        }

        video = await _fetch(_Google(_ok(_item(snippet={"thumbnails": thumbnails}))))

        assert video.thumbnail_url == "https://i.ytimg.com/vi/x/sddefault.jpg"

    async def test_should_fall_back_to_the_maxresdefault_url_without_thumbnails(self) -> None:
        video = await _fetch(_Google(_ok(_item(snippet={"thumbnails": {}}))))

        assert video.thumbnail_url == f"https://img.youtube.com/vi/{YT}/maxresdefault.jpg"

    async def test_should_default_a_missing_title_and_channel_like_the_yt_dlp_path(self) -> None:
        item = _item()
        del item["snippet"]["title"]
        item["snippet"]["channelTitle"] = ""

        video = await _fetch(_Google(_ok(item)))

        assert (video.title, video.channel) == ("Unknown Title", "Unknown Channel")

    @pytest.mark.parametrize("state", ["live", "upcoming"])
    async def test_should_report_live_and_upcoming_broadcasts(self, state: str) -> None:
        item = _item(snippet={"liveBroadcastContent": state}, content={"duration": "P0D"})

        video = await _fetch(_Google(_ok(item)))

        assert (video.live_broadcast, video.duration) == (state, 0)

    async def test_should_flag_an_age_restricted_video(self) -> None:
        item = _item(content={"contentRating": {"ytRating": "ytAgeRestricted"}})

        video = await _fetch(_Google(_ok(item)))

        assert video.restriction == "age"

    async def test_should_flag_an_allow_listed_region_restriction(self) -> None:
        item = _item(content={"regionRestriction": {"allowed": ["JP"]}})

        video = await _fetch(_Google(_ok(item)))

        assert video.restriction == "region"

    async def test_should_not_flag_a_blocked_country_list(self) -> None:
        item = _item(content={"regionRestriction": {"blocked": ["DE", "FR"]}})

        video = await _fetch(_Google(_ok(item)))

        assert video.restriction is None

    async def test_should_raise_bad_payload_when_the_duration_is_missing_or_malformed(self) -> None:
        missing = _item()
        del missing["contentDetails"]["duration"]

        assert (await _failure(_Google(_ok(missing)))).reason == "bad_payload"
        assert (await _failure(_Google(_ok(_item(content={"duration": "3:32"}))))).reason == (
            "bad_payload"
        )

    async def test_should_raise_not_found_when_items_is_empty(self) -> None:
        error = await _failure(_Google(_ok()))

        assert (error.reason, error.api_reason) == ("not_found", None)

    async def test_should_raise_quota_exceeded_on_a_403_quota_error(self) -> None:
        response = _google_error(403, reason="quotaExceeded", rpc_status="RESOURCE_EXHAUSTED")

        error = await _failure(_Google(response))

        assert (error.reason, error.api_reason) == ("quota_exceeded", "quotaExceeded")

    async def test_should_raise_forbidden_on_a_403_without_a_quota_reason(self) -> None:
        unregistered = _google_error(403, reason="forbidden", rpc_status="PERMISSION_DENIED")
        referrer = _google_error(
            403,
            reason="forbidden",
            rpc_status="PERMISSION_DENIED",
            detail_reason="API_KEY_HTTP_REFERRER_BLOCKED",
        )

        first = await _failure(_Google(unregistered))
        second = await _failure(_Google(referrer))

        assert (first.reason, first.api_reason) == ("forbidden", "forbidden")
        assert (second.reason, second.api_reason) == ("forbidden", "API_KEY_HTTP_REFERRER_BLOCKED")

    async def test_should_raise_key_invalid_on_a_400_api_key_invalid(self) -> None:
        response = _google_error(
            400, reason="badRequest", rpc_status="INVALID_ARGUMENT", detail_reason="API_KEY_INVALID"
        )

        error = await _failure(_Google(response))

        assert (error.reason, error.api_reason) == ("key_invalid", "API_KEY_INVALID")

    async def test_should_raise_bad_request_on_any_other_400(self) -> None:
        error = await _failure(_Google(_google_error(400, reason="invalidParameter")))

        assert (error.reason, error.api_reason) == ("bad_request", "invalidParameter")

    async def test_should_raise_rate_limited_on_a_429(self) -> None:
        response = _google_error(429, reason="rateLimitExceeded", rpc_status="RESOURCE_EXHAUSTED")

        error = await _failure(_Google(response))

        assert (error.reason, error.api_reason) == ("rate_limited", "rateLimitExceeded")

    async def test_should_raise_the_http_status_on_a_500_with_an_html_body(self) -> None:
        error = await _failure(_Google(httpx.Response(500, text="<html>Internal error</html>")))

        assert (error.reason, error.api_reason) == ("http_500", None)

    async def test_should_raise_bad_payload_on_a_200_with_an_html_body(self) -> None:
        error = await _failure(_Google(httpx.Response(200, text="<html>Sign in</html>")))

        assert (error.reason, error.api_reason) == ("bad_payload", "not_json")

    async def test_should_raise_timeout_when_the_request_times_out(self) -> None:
        error = await _failure(_Google(error=httpx.ReadTimeout("timed out")))

        assert (error.reason, error.api_reason) == ("timeout", "ReadTimeout")

    async def test_should_raise_network_when_the_connection_fails(self) -> None:
        error = await _failure(_Google(error=httpx.ConnectError("connection refused")))

        assert (error.reason, error.api_reason) == ("network", "ConnectError")

    async def test_should_raise_internal_instead_of_letting_anything_else_escape(self) -> None:
        error = await _failure(_Google(error=RuntimeError("transport bug")))

        assert (error.reason, error.api_reason) == ("internal", "RuntimeError")

    @pytest.mark.parametrize(
        "google",
        [
            _Google(error=httpx.ConnectError(f"{DATA_API_URL}?key={KEY} refused")),
            _Google(httpx.Response(500, text=f"<html>{KEY}</html>")),
            _Google(_google_error(403, reason="forbidden")),
        ],
    )
    async def test_should_never_put_the_url_or_the_key_in_the_error_text(
        self, google: _Google
    ) -> None:
        error = await _failure(google)

        assert KEY not in str(error)
        assert "googleapis" not in str(error)
        assert error.__cause__ is None
