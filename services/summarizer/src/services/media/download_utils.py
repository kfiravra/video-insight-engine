"""Shared YouTube audio download utilities.

Common retry logic and error classification used by both
Gemini and Whisper transcription services.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import yt_dlp

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode

__all__ = [
    "classify_download_error",
    "download_youtube_audio",
    "ytdlp_client_api_opts",
    "ytdlp_client_cli_args",
    "ytdlp_hires_client_attempts",
    "ytdlp_proxy_exit_urls",
    "ytdlp_proxy_url",
    "ytdlp_subprocess_env",
    "try_proxy_exits",
    "MAX_DOWNLOAD_ATTEMPTS",
]

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# urllib (and so yt-dlp) prefers the lowercase names, so both spellings are
# set — an inherited lowercase value would otherwise beat ours.
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

# A caption retry (metadata-phase timedtext or the caption API) visits at most
# this many exits, primary included — the caption API layer runs under
# TRANSCRIPT_FETCH_TIMEOUT and every timedtext attempt delays the metadata
# phase, so more attempts would only cost time.
_MAX_ROTATED_EXITS = 3
_STICKY_SUFFIX_RE = re.compile(r"^(?P<base>.*)-(?P<n>\d+)$")


def ytdlp_proxy_url() -> str | None:
    """YOUTUBE_PROXY_URL with whitespace stripped, or None for a direct connection."""
    url = (settings.YOUTUBE_PROXY_URL or "").strip()
    return url or None


def ytdlp_proxy_exit_urls() -> list[str]:
    """Proxy URLs to try in order for an IP-scoped failure (caption 429).

    Empty without a proxy. The configured URL comes first; when
    YOUTUBE_PROXY_EXIT_COUNT > 1 and its username carries a sticky ``-N``
    suffix, the following exits (wrapping within 1..count, capped) follow.
    Only the username is rewritten, in the raw netloc, so the password is
    never re-encoded. Never log the returned URLs — they carry credentials.
    """
    primary = ytdlp_proxy_url()
    if not primary:
        return []
    count = settings.YOUTUBE_PROXY_EXIT_COUNT
    scheme_sep, _, rest = primary.partition("://")
    if not rest or count <= 1 or "@" not in rest:
        return [primary]
    userinfo, _, host = rest.rpartition("@")
    username, colon, password = userinfo.partition(":")
    match = _STICKY_SUFFIX_RE.match(username)
    if match is None:
        return [primary]
    base, current = match.group("base"), int(match.group("n"))
    urls = [primary]
    for step in range(1, min(count, _MAX_ROTATED_EXITS)):
        exit_no = (current - 1 + step) % count + 1
        urls.append(f"{scheme_sep}://{base}-{exit_no}{colon}{password}@{host}")
    return urls


def try_proxy_exits(
    exit_urls: list[str],
    attempt: Callable[[str], _T],
    is_rate_limited: Callable[[Exception], bool],
    label: str,
) -> _T:
    """Run ``attempt`` on each exit of ytdlp_proxy_exit_urls() in turn.

    A 429 is scoped to the exit IP, so a rate-limited attempt moves straight
    on to the next exit instead of waiting on the same one. Any other error
    ends the rotation at once; when every exit is rate limited the last error
    is re-raised. ``label`` is logged — never put a proxy URL in it.
    """
    last_error: Exception | None = None
    for n, proxy_url in enumerate(exit_urls, 1):
        try:
            return attempt(proxy_url)
        except Exception as e:
            if not is_rate_limited(e):
                raise
            last_error = e
            logger.warning("%s rate limited on proxy exit %d/%d", label, n, len(exit_urls))
    if last_error is None:
        raise ValueError("try_proxy_exits needs at least one exit")
    raise last_error


def ytdlp_subprocess_env() -> dict[str, str] | None:
    """Child env that sends a subprocess yt-dlp through YOUTUBE_PROXY_URL.

    The URL carries credentials, so it travels in the environment and never
    on the command line: Sentry records every subprocess argv as a breadcrumb
    and span name, and argv is readable through ``ps``. None (inherit the
    parent env) when no proxy is configured. Pass as ``env=`` next to
    ytdlp_client_cli_args().
    """
    proxy_url = ytdlp_proxy_url()
    if not proxy_url:
        return None
    return {**os.environ, **dict.fromkeys(_PROXY_ENV_VARS, proxy_url)}


def ytdlp_client_cli_args(clients: str | None = None) -> list[str]:
    """--extractor-args flags for subprocess yt-dlp DOWNLOAD invocations.

    YouTube 403s some player clients' download URLs per environment
    (2026-08: web blocked here, android fine) — YTDLP_PLAYER_CLIENTS picks
    the client order without a code change when YouTube shifts again.
    ``clients`` overrides it (the hi-res download passes
    YTDLP_HIRES_PLAYER_CLIENTS). The proxy is deliberately not a flag
    here — see ytdlp_subprocess_env().
    """
    if clients is None:
        clients = settings.YTDLP_PLAYER_CLIENTS
    clients = clients.strip()
    if not clients:
        return []
    return ["--extractor-args", f"youtube:player_client={clients}"]


def ytdlp_hires_client_attempts() -> list[str]:
    """Player-client lists for the hi-res 720p download, in order.

    YTDLP_HIRES_PLAYER_CLIENTS first (android alone caps at 360p); if that
    differs from YTDLP_PLAYER_CLIENTS, the latter is the one retry — the
    clients pass 1 just proved working for this video.
    """
    base = settings.YTDLP_PLAYER_CLIENTS.strip()
    hires = settings.YTDLP_HIRES_PLAYER_CLIENTS.strip() or base
    return [hires] if hires == base else [hires, base]


def ytdlp_client_api_opts() -> dict[str, Any]:
    """Python-API form of ytdlp_client_cli_args plus the proxy, for YoutubeDL opts dicts."""
    opts: dict[str, Any] = {}
    clients = [c.strip() for c in settings.YTDLP_PLAYER_CLIENTS.split(",") if c.strip()]
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    proxy_url = ytdlp_proxy_url()
    if proxy_url:
        opts["proxy"] = proxy_url
    return opts


_UNAVAILABLE_PATTERNS = (
    "private video",
    "removed",
    "unavailable",
    "sign in",
    "not available",
)

MAX_DOWNLOAD_ATTEMPTS = 3


def classify_download_error(error_msg: str) -> ErrorCode:
    """Classify a yt-dlp error as VIDEO_UNAVAILABLE or DOWNLOAD_ERROR."""
    msg_lower = error_msg.lower()
    # "Requested format is not available" would otherwise match the broad
    # "not available" pattern below — but it means the format *selector*
    # failed (e.g. SABR stripped audio-only formats), not that the video is
    # gone. VIDEO_UNAVAILABLE wrongly suppresses audio fallback upstream.
    if "requested format is not available" in msg_lower:
        return ErrorCode.DOWNLOAD_ERROR
    if any(p in msg_lower for p in _UNAVAILABLE_PATTERNS):
        return ErrorCode.VIDEO_UNAVAILABLE
    return ErrorCode.DOWNLOAD_ERROR


def download_youtube_audio(
    video_id: str,
    ydl_opts: dict[str, Any],
    temp_dir: Path,
    file_stem: str,
    max_attempts: int = MAX_DOWNLOAD_ATTEMPTS,
) -> None:
    """Download YouTube audio with retry and exponential backoff.

    **Blocking** — uses ``time.sleep`` between retries. Must be called
    via ``asyncio.to_thread()`` from async contexts to avoid blocking
    the event loop.

    Cleans up stale .part files between attempts and classifies errors
    on final failure.

    Args:
        video_id: YouTube video ID
        ydl_opts: yt-dlp options dict (format, postprocessors, etc.)
        temp_dir: Directory for temporary download files
        file_stem: Base filename stem (used to find .part files)
        max_attempts: Maximum download attempts

    Raises:
        TranscriptError: If download fails after all retries
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    # Route the download through the configured player clients and proxy;
    # a caller's own value for either key wins.
    ydl_opts = {**ytdlp_client_api_opts(), **ydl_opts}
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        for stale in temp_dir.glob(f"{file_stem}.*.part"):
            try:
                stale.unlink()
            except OSError:
                pass
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            last_error = None
            break
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                logger.warning(
                    "Download attempt %s/%s failed for %s: %s",
                    attempt,
                    max_attempts,
                    video_id,
                    e,
                )
                time.sleep(2**attempt)

    if last_error is not None:
        error_msg = str(last_error)
        error_code = classify_download_error(error_msg)
        logger.error("Failed to download audio for %s: %s", video_id, last_error)
        raise TranscriptError(
            f"Failed to download audio: {error_msg}",
            error_code,
        )
