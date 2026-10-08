"""Shared YouTube audio download utilities.

Common retry logic and error classification used by both
Gemini and Whisper transcription services.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import OrderedDict
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
    "is_exit_blocked",
    "proxy_exit_label",
    "record_blocked_exit",
    "record_working_exit",
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

# A rotation visits at most this many exits, the job's own included — the
# caption API layer runs under TRANSCRIPT_FETCH_TIMEOUT and every timedtext
# attempt delays the transcript, so more attempts would only cost time.
_MAX_ROTATED_EXITS = 3
_STICKY_SUFFIX_RE = re.compile(r"^(?P<base>.*)-(?P<n>\d+)$")

# YouTube's "Sign in to confirm you’re not a bot" wall, as yt-dlp passes it
# through (YouTube's apostrophe is U+2019). Anchored on "not a bot" because the
# age gate's "Sign in to confirm your age" shares the prefix and must not match.
_BOT_CHECK_RE = re.compile(r"confirm\s+you(?:['’]re|\s+are)\s+not\s+a\s+bot", re.IGNORECASE)
# A throttled request as yt-dlp ("HTTP Error 429: Too Many Requests") and
# requests ("429 Client Error: Too Many Requests") word it.
_HTTP_429_RE = re.compile(r"\bHTTP Error 429\b|\bToo Many Requests\b", re.IGNORECASE)
# yt-dlp's other session-scoped blocks: "This content isn't available, try
# again later. The current session has been rate-limited by YouTube…" and
# "…YouTube is requiring a captcha challenge before playback".
_SESSION_BLOCK_RE = re.compile(
    r"rate-limited by YouTube|\btry again later\b|captcha challenge", re.IGNORECASE
)
_EXIT_BLOCK_RES = (_BOT_CHECK_RE, _HTTP_429_RE, _SESSION_BLOCK_RE)

# A blocked exit is skipped by later rotations for this long. YouTube's session
# rate limit lasts "up to an hour" and the 2026-10-08 bot check held for hours,
# but a longer mark piles every job onto the remaining exits; after the window
# one attempt re-tests the exit (~2.5 s when it is still blocked).
_BLOCKED_EXIT_TTL_SECONDS = 15 * 60
# Round-robin starts remembered per job key — bounded for a long-lived worker.
_MAX_TRACKED_JOBS = 512


def is_exit_blocked(error: BaseException | str) -> bool:
    """True when a YouTube error (exception or yt-dlp stderr) blocks the proxy exit's IP.

    The bot check, a 429, the session rate limit and the captcha wall are all
    scoped to the exit IP — 2026-10-08 the main Webshare exit got the bot
    check on every video while exits 2 and 3 still worked — so each is worth
    the next exit. Anything else (private, removed, age gate, 404, format
    unavailable) fails alike on every exit.
    """
    text = str(error)
    return any(pattern.search(text) is not None for pattern in _EXIT_BLOCK_RES)


def proxy_exit_label(proxy_url: str) -> str:
    """Loggable name of one exit — its sticky ``-N`` session, never the URL."""
    username = proxy_url.partition("://")[2].rpartition("@")[0].partition(":")[0]
    match = _STICKY_SUFFIX_RE.match(username)
    return f"session -{match.group('n')}" if match else "unnamed session"


class _ExitMemory:
    """Per-process proxy-exit bookkeeping, shared by every thread (pipeline-1min 1a.6).

    * Round-robin start: each job key (a youtube id, a playlist id) gets the
      next exit of the pool the first time it is seen and keeps it for every
      later call, so concurrent jobs spread over the exits instead of all
      starting on the configured one.
    * Blocked exits: an exit that got a bot check, 429 or session block is
      moved behind the others by every rotation for _BLOCKED_EXIT_TTL_SECONDS,
      so it costs one failed attempt per window per process instead of one per
      YouTube call (~2.5 s each on 2026-10-08, when two of three exits were
      bot-checked). An exit that works again is unmarked at once.

    This replaces "the exit that last worked starts every rotation" (e925e2b):
    that funnelled every job onto one exit until it, too, got blocked.
    Holds credential-bearing URLs in memory only — they are never logged.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._next_offset = 0
        self._job_offsets: OrderedDict[str, int] = OrderedDict()
        self._blocked_at: dict[str, float] = {}

    def job_offset(self, job_key: str | None) -> int:
        """The job's round-robin offset into the exit pool; 0 (the configured exit) without a key."""
        if job_key is None:
            return 0
        with self._lock:
            offset = self._job_offsets.get(job_key)
            if offset is None:
                offset = self._next_offset
                self._next_offset += 1
                self._job_offsets[job_key] = offset
                if len(self._job_offsets) > _MAX_TRACKED_JOBS:
                    self._job_offsets.popitem(last=False)
            else:
                self._job_offsets.move_to_end(job_key)
            return offset

    def order(self, exit_urls: list[str]) -> list[str]:
        """``exit_urls`` with the exits blocked within the TTL moved behind the others."""
        now = self._clock()
        with self._lock:
            blocked = {
                url for url, at in self._blocked_at.items() if now - at < _BLOCKED_EXIT_TTL_SECONDS
            }
        fresh = [url for url in exit_urls if url not in blocked]
        return fresh + [url for url in exit_urls if url in blocked]

    def mark_blocked(self, proxy_url: str) -> None:
        with self._lock:
            self._blocked_at[proxy_url] = self._clock()

    def mark_working(self, proxy_url: str) -> None:
        with self._lock:
            self._blocked_at.pop(proxy_url, None)


_EXIT_MEMORY = _ExitMemory()


def record_blocked_exit(proxy_url: str) -> None:
    """Keep ``proxy_url`` behind the other exits for _BLOCKED_EXIT_TTL_SECONDS."""
    _EXIT_MEMORY.mark_blocked(proxy_url)


def record_working_exit(proxy_url: str, label: str, position: int, tried_of: int) -> None:
    """Clear any blocked mark on ``proxy_url``; log it when earlier exits were blocked.

    ``position`` is the exit's 1-based place among the ``tried_of`` exits of
    this rotation. ``label`` is logged — never put a proxy URL in it.
    """
    _EXIT_MEMORY.mark_working(proxy_url)
    if position > 1:
        logger.info(
            "%s succeeded on proxy exit %d/%d (%s)",
            label,
            position,
            tried_of,
            proxy_exit_label(proxy_url),
        )


def ytdlp_proxy_url() -> str | None:
    """YOUTUBE_PROXY_URL with whitespace stripped, or None for a direct connection."""
    url = (settings.YOUTUBE_PROXY_URL or "").strip()
    return url or None


def _exit_pool(primary: str, count: int) -> list[str]:
    """Every sticky exit, the configured one first, then the next ones wrapping within 1..count.

    Only the username is rewritten, in the raw netloc, so the password is
    never re-encoded. Just ``[primary]`` without a sticky ``-N`` username.
    """
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
    for step in range(1, count):
        exit_no = (current - 1 + step) % count + 1
        urls.append(f"{scheme_sep}://{base}-{exit_no}{colon}{password}@{host}")
    return urls


def ytdlp_proxy_exit_urls(job_key: str | None = None) -> list[str]:
    """Proxy URLs to try in order for an IP-scoped failure (bot check, 429, session block).

    Empty without a proxy; just YOUTUBE_PROXY_URL when YOUTUBE_PROXY_EXIT_COUNT
    is 1 or its username has no sticky ``-N`` suffix. Otherwise the pool is
    the count sticky exits: ``job_key`` (the youtube id; a playlist id) picks
    the job's first exit round-robin — the first job starts on the configured
    exit, the next job on the one after, and so on — and every call of that
    job starts there again; no key starts on the configured exit. Exits
    blocked in the last _BLOCKED_EXIT_TTL_SECONDS move behind the others, and
    at most _MAX_ROTATED_EXITS are returned. Never log the returned URLs —
    they carry credentials.
    """
    primary = ytdlp_proxy_url()
    if not primary:
        return []
    pool = _exit_pool(primary, settings.YOUTUBE_PROXY_EXIT_COUNT)
    if len(pool) == 1:
        return pool
    start = _EXIT_MEMORY.job_offset(job_key) % len(pool)
    return _EXIT_MEMORY.order(pool[start:] + pool[:start])[:_MAX_ROTATED_EXITS]


def try_proxy_exits(
    exit_urls: list[str],
    attempt: Callable[[str], _T],
    is_blocked: Callable[[Exception], bool],
    label: str,
) -> _T:
    """Run ``attempt`` on each exit of ytdlp_proxy_exit_urls() in turn.

    A 429 or a bot check is scoped to the exit IP, so a blocked attempt moves
    straight on to the next exit instead of waiting on the same one, and the
    exit stays behind the others for later rotations (record_blocked_exit).
    Any other error ends the rotation at once; when every exit is blocked the
    last error is re-raised. ``label`` is logged — never put a proxy URL in it.
    """
    last_error: Exception | None = None
    for n, proxy_url in enumerate(exit_urls, 1):
        try:
            result = attempt(proxy_url)
        except Exception as e:
            if not is_blocked(e):
                raise
            last_error = e
            record_blocked_exit(proxy_url)
            logger.warning("%s blocked on proxy exit %d/%d", label, n, len(exit_urls))
            continue
        record_working_exit(proxy_url, label, n, len(exit_urls))
        return result
    if last_error is None:
        raise ValueError("try_proxy_exits needs at least one exit")
    raise last_error


def ytdlp_subprocess_env(proxy_url: str | None = None) -> dict[str, str] | None:
    """Child env that sends a subprocess yt-dlp through ``proxy_url``.

    ``proxy_url`` is one exit of ytdlp_proxy_exit_urls(); omitted, it is
    YOUTUBE_PROXY_URL. The URL carries credentials, so it travels in the
    environment and never on the command line: Sentry records every
    subprocess argv as a breadcrumb and span name, and argv is readable
    through ``ps``. None (inherit the parent env) when no proxy is
    configured. Pass as ``env=`` next to ytdlp_client_cli_args().
    """
    proxy_url = proxy_url or ytdlp_proxy_url()
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


def _ydl_download(url: str, ydl_opts: dict[str, Any]) -> None:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def _remove_stale_parts(temp_dir: Path, file_stem: str) -> None:
    """Drop a failed attempt's .part files so yt-dlp starts the next one clean."""
    for stale in temp_dir.glob(f"{file_stem}.*.part"):
        try:
            stale.unlink()
        except OSError as e:
            logger.debug("Could not remove stale part file %s: %s", stale.name, e)


def _download_via_exits(
    url: str, ydl_opts: dict[str, Any], exit_urls: list[str], label: str
) -> None:
    """One in-process download; a bot check or 429 moves on to the next proxy exit."""
    if len(exit_urls) <= 1:
        _ydl_download(url, ydl_opts)
        return
    try_proxy_exits(
        exit_urls,
        lambda proxy_url: _ydl_download(url, {**ydl_opts, "proxy": proxy_url}),
        is_exit_blocked,
        label,
    )


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
    # A caller's own proxy pins the download to it — no exit rotation.
    exit_urls = [] if "proxy" in ydl_opts else ytdlp_proxy_exit_urls(video_id)
    # Route the download through the configured player clients and proxy;
    # a caller's own value for either key wins.
    ydl_opts = {**ytdlp_client_api_opts(), **ydl_opts}
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        _remove_stale_parts(temp_dir, file_stem)
        try:
            _download_via_exits(url, ydl_opts, exit_urls, f"Audio download for {video_id}")
            last_error = None
            break
        except Exception as e:
            last_error = e
            if len(exit_urls) > 1 and is_exit_blocked(e):
                # Every exit of the rotation was just blocked; another round
                # would only repeat the same blocks plus the backoff sleeps.
                break
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
