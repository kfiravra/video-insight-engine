"""YouTube Data API v3 ``videos.list`` for the pre-flight metadata route.

One direct GET per lookup — googleapis.com is not bot-checked, so no proxy —
for the title, channel, duration and thumbnail plus the two signals the route
decides on: the live/upcoming state and an age or region restriction. The key
travels in the ``X-Goog-Api-Key`` header, never in the URL (Sentry records
outbound URLs as breadcrumbs). No exception or log line here carries the URL
or the key, and nothing but :class:`DataApiUnavailable` leaves
:func:`fetch_video`: the key sits in these frames' locals.

Every failure is a :class:`DataApiUnavailable` with a closed ``reason`` the
route logs before falling back to yt-dlp; ``api_reason`` keeps Google's raw
token (``API_KEY_INVALID``, ``quotaExceeded``, ``SERVICE_DISABLED``…).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

DATA_API_URL = "https://www.googleapis.com/youtube/v3/videos"
DATA_API_TIMEOUT_S = 5.0
_PARTS = "snippet,contentDetails"
_FIELDS = (
    "items(id,snippet(title,channelTitle,thumbnails,liveBroadcastContent),"
    "contentDetails(duration,contentRating,regionRestriction))"
)
# Tokens that mean the project's quota, not this key or this video.
_QUOTA_TOKENS = frozenset(
    {
        "quotaExceeded",
        "dailyLimitExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "RESOURCE_EXHAUSTED",
    }
)
# A key Google does not recognise: the One-Platform ``details[].reason`` and
# the legacy ``errors[].reason`` spellings.
_KEY_INVALID_TOKENS = frozenset({"API_KEY_INVALID", "keyInvalid"})
# Largest first, as snippet.thumbnails names them.
_THUMBNAIL_SIZES = ("maxres", "standard", "high", "medium", "default")
# What the yt-dlp path answers without a thumbnail list (youtube._best_thumbnail).
_THUMBNAIL_FALLBACK = "https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
# ISO-8601 as videos.list writes it: PT3M32S, PT1H, P1DT2H3M4S, PT0S, P0D.
_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)
_DURATION_GROUPS = ("d", "h", "m", "s")
_AGE_RESTRICTED_RATING = "ytAgeRestricted"


@dataclass(frozen=True)
class DataApiVideo:
    """What ``videos.list`` says about one video, in the pre-flight's terms."""

    video_id: str
    title: str
    channel: str
    duration: int
    thumbnail_url: str
    live_broadcast: str
    restriction: str | None


class DataApiUnavailable(Exception):
    """The Data API could not answer for this video; the caller falls back to yt-dlp.

    ``reason`` is one of a closed set — ``not_found``, ``quota_exceeded``,
    ``forbidden``, ``key_invalid``, ``bad_request``, ``rate_limited``,
    ``http_<status>``, ``timeout``, ``network``, ``bad_payload``,
    ``internal`` — and ``api_reason`` is Google's own token when the body
    had one (or the exception class for a transport failure).
    """

    def __init__(self, reason: str, api_reason: str | None = None) -> None:
        self.reason = reason
        self.api_reason = api_reason
        super().__init__(reason if api_reason is None else f"{reason} ({api_reason})")


# ─── Response parsing ───


def parse_iso8601_duration(value: object) -> int | None:
    """Seconds for a ``PT3M32S``-style duration; None for anything else (weeks, fractions, blanks)."""
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.fullmatch(value)
    if match is None or not any(match.group(name) for name in _DURATION_GROUPS):
        return None
    days, hours, minutes, seconds = (int(match.group(name) or 0) for name in _DURATION_GROUPS)
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _best_thumbnail(video_id: str, thumbnails: object) -> str:
    if isinstance(thumbnails, dict):
        for size in _THUMBNAIL_SIZES:
            entry = thumbnails.get(size)
            if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"]:
                return entry["url"]
    return _THUMBNAIL_FALLBACK.format(video_id=video_id)


def _restriction(content_details: dict[str, Any]) -> str | None:
    """``age`` for YouTube's age gate, ``region`` for an allow-list; a blocked-country list is served."""
    rating = content_details.get("contentRating")
    if isinstance(rating, dict) and rating.get("ytRating") == _AGE_RESTRICTED_RATING:
        return "age"
    region = content_details.get("regionRestriction")
    if isinstance(region, dict) and region.get("allowed") is not None:
        return "region"
    return None


def _parse_item(video_id: str, item: dict[str, Any]) -> DataApiVideo:
    snippet = item.get("snippet")
    content_details = item.get("contentDetails")
    if not isinstance(snippet, dict) or not isinstance(content_details, dict):
        raise DataApiUnavailable("bad_payload", "parts")
    duration = parse_iso8601_duration(content_details.get("duration"))
    if duration is None:
        raise DataApiUnavailable("bad_payload", "duration")
    return DataApiVideo(
        video_id=video_id,
        title=snippet.get("title") or "Unknown Title",
        channel=snippet.get("channelTitle") or "Unknown Channel",
        duration=duration,
        thumbnail_url=_best_thumbnail(video_id, snippet.get("thumbnails")),
        live_broadcast=snippet.get("liveBroadcastContent") or "none",
        restriction=_restriction(content_details),
    )


# ─── Error classification ───


def _error_tokens(body: object) -> list[str]:
    """Google's error tokens, most specific first: ``details[].reason``, ``errors[].reason``, ``status``."""
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return []
    error = body["error"]
    tokens: list[str] = []
    for key in ("details", "errors"):
        entries = error.get(key)
        if isinstance(entries, list):
            tokens.extend(
                entry["reason"]
                for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("reason"), str)
            )
    if isinstance(error.get("status"), str):
        tokens.append(error["status"])
    return tokens


def _reason_for(status: int, body: object) -> tuple[str, str | None]:
    """The closed reason and Google's token for a non-2xx answer (the body may be HTML)."""
    tokens = _error_tokens(body)
    api_reason = tokens[0] if tokens else None
    if status == 429:
        return "rate_limited", api_reason
    quota = next((token for token in tokens if token in _QUOTA_TOKENS), None)
    if quota is not None:
        return "quota_exceeded", quota
    if status == 403:
        return "forbidden", api_reason
    if status == 400:
        invalid = next((token for token in tokens if token in _KEY_INVALID_TOKENS), None)
        return ("key_invalid", invalid) if invalid is not None else ("bad_request", api_reason)
    return f"http_{status}", api_reason


# ─── The call ───


async def _fetch(client: httpx.AsyncClient, video_id: str, api_key: str) -> DataApiVideo:
    try:
        response = await client.get(
            DATA_API_URL,
            params={"part": _PARTS, "id": video_id, "fields": _FIELDS},
            headers={"X-Goog-Api-Key": api_key, "Accept": "application/json"},
        )
    except httpx.TimeoutException as e:
        raise DataApiUnavailable("timeout", type(e).__name__) from None
    except httpx.HTTPError as e:
        raise DataApiUnavailable("network", type(e).__name__) from None
    if response.status_code // 100 != 2:
        try:
            body: object = response.json()
        except ValueError:
            body = None
        reason, api_reason = _reason_for(response.status_code, body)
        raise DataApiUnavailable(reason, api_reason)
    try:
        payload = response.json()
    except ValueError:
        raise DataApiUnavailable("bad_payload", "not_json") from None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DataApiUnavailable("bad_payload", "items")
    if not items:
        # videos.list never 404s: an unknown, private or deleted id is an empty list.
        raise DataApiUnavailable("not_found")
    first = items[0]
    if not isinstance(first, dict):
        raise DataApiUnavailable("bad_payload", "item")
    return _parse_item(video_id, first)


async def fetch_video(
    video_id: str, *, api_key: str, client: httpx.AsyncClient | None = None
) -> DataApiVideo:
    """One ``videos.list`` call for ``video_id``.

    ``client`` lets tests inject an ``httpx.MockTransport``; without one the
    call gets its own client with DATA_API_TIMEOUT_S and no retries (the
    gateway allows 25 s for the whole pre-flight, and the yt-dlp fallback
    may need 15 of them). Raises DataApiUnavailable for every failure.
    """
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=DATA_API_TIMEOUT_S) as own:
                return await _fetch(own, video_id, api_key)
        return await _fetch(client, video_id, api_key)
    except DataApiUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — the key is in these frames' locals; nothing may escape with it
        raise DataApiUnavailable("internal", type(e).__name__) from None
