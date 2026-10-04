"""Fetch and cache direct video stream URLs via yt-dlp.

Provides get_video_stream_url() which shells out to yt-dlp --get-url to
obtain a short-lived direct stream URL for a YouTube video. Results are
cached in-memory with a 5-minute TTL (YouTube stream URLs expire quickly).

Security:
- YouTube IDs are validated against a strict regex (^[a-zA-Z0-9_-]{11}$)
- yt-dlp is invoked via create_subprocess_exec (no shell injection)
- Stream URLs returned by yt-dlp are validated (scheme, hostname, SSRF blocklist)
"""

import asyncio
import logging
import time
import urllib.parse

from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

# Module-level cache for video stream URLs with TTL.
# YouTube stream URLs are short-lived, so we expire entries after 5 minutes.
# Safety: asyncio is single-threaded, so cache read/write before any await is race-free.
_STREAM_URL_CACHE_MAX = 50
_STREAM_URL_TTL = 300  # 5 minutes
_stream_url_cache: dict[str, tuple[str, float]] = {}  # {youtube_id: (url, timestamp)}

_SSRF_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",  # IPv6-mapped loopback/metadata
        "169.254.169.254",  # AWS EC2 metadata endpoint
        "metadata.google.internal",  # GCP metadata endpoint
    }
)

_SSRF_BLOCKED_PREFIXES = (
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "0177.",
    "0x7f",  # Octal/hex loopback notation
)


def _is_valid_stream_url(url: str) -> bool:
    """Validate a stream URL returned by yt-dlp (defense-in-depth SSRF check)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in _SSRF_BLOCKED_HOSTS:
        return False
    if any(hostname.startswith(p) for p in _SSRF_BLOCKED_PREFIXES):
        return False
    return True


async def get_video_stream_url(youtube_id: str) -> str | None:
    """Get direct video stream URL via yt-dlp (no download).

    Uses --get-url with a 720p cap for reasonable frame quality without
    downloading huge streams. Falls back to 'best[height<=720]' if no
    separate video stream is available.

    Results are cached per youtube_id for the duration of the request.
    """
    now = time.monotonic()
    cached = _stream_url_cache.get(youtube_id)
    if cached and (now - cached[1]) < _STREAM_URL_TTL:
        return cached[0]

    # Evict expired entries
    expired = [k for k, (_, ts) in _stream_url_cache.items() if (now - ts) >= _STREAM_URL_TTL]
    for k in expired:
        del _stream_url_cache[k]

    # Evict oldest entry if at capacity (LRU-style)
    if len(_stream_url_cache) >= _STREAM_URL_CACHE_MAX:
        oldest = min(_stream_url_cache, key=lambda k: _stream_url_cache[k][1])
        del _stream_url_cache[oldest]

    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id for frame extraction: %s", youtube_id)
        return None

    url = f"https://www.youtube.com/watch?v={youtube_id}"

    # Try format specs in parallel — take the first success.
    # Each yt-dlp call can take up to 15s, so parallel halves worst-case latency.
    format_specs = [
        "bestvideo[height<=720]",
        "best[height<=720]",
    ]

    async def _try_format(fmt: str) -> str | None:
        try:
            from src.services.media.download_utils import (
                ytdlp_client_cli_args,
                ytdlp_subprocess_env,
            )

            proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "--get-url",
                "--format",
                fmt,
                "--no-playlist",
                "--no-warnings",
                *ytdlp_client_cli_args(),
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=ytdlp_subprocess_env(),
            )
        except Exception as e:
            logger.warning("yt-dlp --get-url failed for %s (format: %s): %s", youtube_id, fmt, e)
            return None

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()  # drain pipes
            logger.warning("yt-dlp --get-url timed out for %s (format: %s)", youtube_id, fmt)
            return None

        if proc.returncode == 0 and stdout:
            stream_url = stdout.decode().strip().split("\n")[0]
            if _is_valid_stream_url(stream_url):
                return stream_url
        return None

    results = await asyncio.gather(*[_try_format(fmt) for fmt in format_specs])
    # Prefer first format (bestvideo) if both succeeded
    stream_url = next((r for r in results if r), None)

    if stream_url:
        _stream_url_cache[youtube_id] = (stream_url, time.monotonic())
        logger.info("Got video stream URL for %s", youtube_id)
        return stream_url

    logger.warning("Could not get video stream URL for %s", youtube_id)
    return None


def clear_stream_url_cache() -> None:
    """Clear the video stream URL cache. Call between requests."""
    _stream_url_cache.clear()
