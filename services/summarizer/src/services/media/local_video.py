"""The run's two video downloads: the 360p pass-1 file and the ≤720p file.

The pass-1 file feeds scene detection (scoring and the HIGH tier's vision
read the JPEGs detection wrote); the 720p file feeds hi-res frames and moment
fill. Both go through yt-dlp itself — googlevideo stream URLs are bound to the
requesting client (PO tokens / headers) and 403 plain ffmpeg — and every
frame is then seeked out of the local file, where ``-ss`` is instant and
can't 403. Each download lands in its own temp dir, which the CALLER owns
(``cleanup_local_video``).

Both try the video clients first (``ytdlp_hires_client_attempts``): android
alone serves no video-only rendition, only the muxed 360p one (format 18,
audio included) — 2-4x the bytes of a 360p video-only file and often bigger
than the 720p one (g4 §8, 2026-10-14). Each download's ``pipeline.timing``
entry records the rendition yt-dlp picked, the clients and the proxy exit
that delivered it, and on failure why (``download_timing``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from src.services.pipeline.download_timing import record_download
from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 180.0
LOWRES_DOWNLOAD_TIMEOUT = 120.0
# Video-only first (frames don't need audio, halves the download), mp4 for
# ffmpeg-friendliness, hard 720p cap to bound size (~30-80MB for 10 min).
_FORMAT_SPEC = "bestvideo[height<=720][ext=mp4]/best[height<=720][ext=mp4]/best[ext=mp4]"
# Pass 1: the smallest video-only rendition of at least 360p, H.264 first (the
# cheapest to decode for scene detection). Detection JPEGs stay 360p because
# the HIGH tier's vision always reads them, and vision + OCR do when the hi-res
# pass is deferred. A video with nothing that tall takes its best video-only
# file, a client with no video-only rendition (android alone) the muxed worst.
PASS1_HEIGHT = 360
_LOWRES_FORMAT_SPEC = (
    "worstvideo[height>=360][ext=mp4][vcodec^=avc1]/worstvideo[height>=360][ext=mp4]"
    "/bestvideo[ext=mp4]/worst[ext=mp4]/worst"
)
# yt-dlp prints the rendition it picked once the file is in place (one stdout line).
_PICKED_MARK = "vie-picked"
_PRINT_PICKED = f"after_move:{_PICKED_MARK} %(format_id)s %(height)s"
_ERROR_CHARS = 160
_CREDENTIALS_RE = re.compile(r"(\w+://)[^\s/@]+@")
_ERROR_PREFIX_RE = re.compile(r"^ERROR:\s*(\[\w+\]\s*[\w-]+:\s*)?")


class LocalFile(NamedTuple):
    """A landed download: the file, its temp dir (the caller's to clean) and its height."""

    path: Path
    temp_dir: str
    height: int | None = None  # as yt-dlp picked it; None = unknown


class _Attempt(Enum):
    """Outcome of one yt-dlp process through one proxy exit."""

    OK = "ok"
    FAILED = "failed"
    EXIT_BLOCKED = "exit_blocked"  # bot check or 429: worth the next exit


@dataclass(frozen=True)
class _Once:
    """One yt-dlp process: its outcome, its stdout and, on failure, why."""

    attempt: _Attempt
    stdout: str = ""
    error: str | None = None


@dataclass(frozen=True)
class _Fetched:
    """How a download ended — what its ``pipeline.timing`` entry records."""

    ok: bool
    clients: str | None = None
    proxy_exit: str | None = None  # "2/3": the sticky exit that answered; None = direct
    format_id: str | None = None
    height: int | None = None
    error: str | None = None  # the last attempt's failure, short and credential-free

    def timing_detail(self) -> dict[str, object]:
        return {
            "format": self.format_id,
            "height": self.height,
            "clients": self.clients,
            "proxyExit": self.proxy_exit,
            "error": self.error,
        }


@dataclass(frozen=True)
class _Rendition:
    """What one of the run's two downloads fetches and how it is labelled."""

    kind: str  # pipeline.timing ``kind``
    format_spec: str
    dir_prefix: str


_HIRES = _Rendition("720p", _FORMAT_SPEC, "vie-hires")
_LOWRES = _Rendition("lowres", _LOWRES_FORMAT_SPEC, "vie-lowres")


def _short_error(stderr: str) -> str:
    """The last yt-dlp ERROR line, without its ``[youtube] <id>:`` prefix or proxy credentials."""
    lines = [line for line in stderr.splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    text = (errors or lines or ["no output"])[-1]
    text = _ERROR_PREFIX_RE.sub("", text.strip())
    return _CREDENTIALS_RE.sub(r"\1***@", text)[:_ERROR_CHARS]


def _picked(stdout: str) -> tuple[str | None, int | None]:
    """(format id, height) from yt-dlp's ``--print`` line; (None, None) when absent."""
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == _PICKED_MARK:
            height = int(parts[2]) if parts[2].isdigit() else None
            return parts[1], height
    return None, None


async def _kill_quietly(proc: asyncio.subprocess.Process | None) -> None:
    """Terminate a still-running yt-dlp without letting cleanup itself raise."""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


def _record(
    rendition: _Rendition, purpose: str, started: float, video_path: Path, fetched: _Fetched
) -> None:
    record_download(
        kind=rendition.kind,
        purpose=purpose,
        start_monotonic=started,
        path=video_path,
        ok=fetched.ok,
        detail=fetched.timing_detail(),
    )


async def _download_rendition(
    youtube_id: str, rendition: _Rendition, timeout: float, purpose: str
) -> LocalFile | None:
    """Download one rendition; returns the landed ``LocalFile`` or None.

    The CALLER owns cleanup of temp_dir (``cleanup_local_video``). Best-effort:
    a failed download logs and returns None. An exception (a cancel when an
    outer budget expires mid-download, or EMFILE/EACCES) kills the subprocess
    and removes the temp dir before re-raising, so neither leaks out of a
    long-lived worker. Every outcome lands in the run's ``pipeline.timing``.
    """
    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id for %s download: %s", rendition.kind, youtube_id)
        return None

    temp_dir = tempfile.mkdtemp(prefix=f"{rendition.dir_prefix}-{youtube_id}-")
    video_path = Path(temp_dir) / f"{youtube_id}.mp4"
    started = time.monotonic()
    try:
        fetched = await _download_with_clients(
            youtube_id, video_path, timeout, rendition.format_spec
        )
    except BaseException as exc:
        fetched = _Fetched(ok=False, error=type(exc).__name__)
        _record(rendition, purpose, started, video_path, fetched)
        cleanup_local_video(temp_dir)
        raise
    _record(rendition, purpose, started, video_path, fetched)
    if not fetched.ok:
        cleanup_local_video(temp_dir)
        return None
    logger.info(
        "Local %s download for %s: %.1fMB (format=%s, clients=%s)",
        rendition.kind,
        youtube_id,
        video_path.stat().st_size / 1_048_576,
        fetched.format_id,
        fetched.clients,
    )
    return LocalFile(video_path, temp_dir, fetched.height)


async def download_video_720p(
    youtube_id: str, timeout: float = DOWNLOAD_TIMEOUT, *, purpose: str = "hires"
) -> LocalFile | None:
    """Download a ≤720p rendition; returns the landed ``LocalFile`` or None.

    Tries YTDLP_HIRES_PLAYER_CLIENTS first (android alone caps at 360p), then
    once more with YTDLP_PLAYER_CLIENTS if that fails, both within ``timeout``.
    ``purpose`` labels the download in the run's ``pipeline.timing`` (one 720p
    per job is the goal). Contract: ``_download_rendition``.
    """
    return await _download_rendition(youtube_id, _HIRES, timeout, purpose)


async def download_video_lowres(
    youtube_id: str, timeout: float = LOWRES_DOWNLOAD_TIMEOUT
) -> LocalFile | None:
    """Download the pass-1 rendition scene detection reads; the landed ``LocalFile`` or None.

    The smallest ≥360p video-only file (``_LOWRES_FORMAT_SPEC``), through the
    same client attempts as the 720p download within ``timeout``. Contract:
    ``_download_rendition``.
    """
    return await _download_rendition(youtube_id, _LOWRES, timeout, "scene_detect")


async def _download_with_clients(
    youtube_id: str, video_path: Path, timeout: float, format_spec: str
) -> _Fetched:
    """Each client set in turn (the video clients first) within one ``timeout``."""
    from src.services.media.download_utils import ytdlp_hires_client_attempts

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    fetched = _Fetched(ok=False, error="no time left")
    for clients in ytdlp_hires_client_attempts():
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        fetched = await _run_ytdlp(youtube_id, video_path, clients, remaining, format_spec)
        if fetched.ok:
            break
    return fetched


async def _run_ytdlp(
    youtube_id: str,
    video_path: Path,
    clients: str | None,
    timeout: float,
    format_spec: str = _FORMAT_SPEC,
) -> _Fetched:
    """One yt-dlp download with the given player clients.

    ``clients`` None = YTDLP_PLAYER_CLIENTS (``ytdlp_client_cli_args`` default).
    YouTube's bot check and a 429 are scoped to the proxy exit's IP, so either
    moves on to the next sticky exit (``ytdlp_proxy_exit_urls``: the job's
    round-robin exit first, recently blocked exits last) within the same
    ``timeout``; any other failure ends the download.
    """
    from src.services.media.download_utils import (
        record_blocked_exit,
        record_working_exit,
        ytdlp_proxy_exit_urls,
    )

    argv = _ytdlp_argv(youtube_id, video_path, clients, format_spec)
    label = f"{youtube_id} (format={format_spec.split('/', 1)[0]}, clients={clients})"
    exit_urls: list[str | None] = [*ytdlp_proxy_exit_urls(youtube_id)] or [None]
    deadline = asyncio.get_running_loop().time() + timeout
    error: str | None = "no time left"
    for n, proxy_url in enumerate(exit_urls, 1):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        once = await _run_ytdlp_once(argv, video_path, remaining, proxy_url, label)
        exit_label = f"{n}/{len(exit_urls)}" if proxy_url else None
        if once.attempt is _Attempt.EXIT_BLOCKED:
            if proxy_url:
                record_blocked_exit(proxy_url)
            logger.warning(
                "yt-dlp download for %s blocked on proxy exit %d/%d", label, n, len(exit_urls)
            )
            error = once.error
            continue
        if once.attempt is _Attempt.OK and proxy_url:
            record_working_exit(proxy_url, f"yt-dlp download for {label}", n, len(exit_urls))
        format_id, height = _picked(once.stdout)
        ok = once.attempt is _Attempt.OK
        return _Fetched(ok, clients, exit_label, format_id, height, None if ok else once.error)
    return _Fetched(ok=False, clients=clients, error=error)


def _ytdlp_argv(
    youtube_id: str, video_path: Path, clients: str | None, format_spec: str
) -> list[str]:
    from src.services.media.download_utils import ytdlp_client_cli_args

    return [
        "yt-dlp",
        "-f",
        format_spec,
        "--no-playlist",
        "--no-warnings",
        "--no-simulate",
        "--print",
        _PRINT_PICKED,
        *ytdlp_client_cli_args(clients),
        "-o",
        str(video_path),
        f"https://www.youtube.com/watch?v={youtube_id}",
    ]


async def _run_ytdlp_once(
    argv: list[str],
    video_path: Path,
    timeout: float,
    proxy_url: str | None,
    label: str,
) -> _Once:
    """One yt-dlp process through one proxy exit (None = YOUTUBE_PROXY_URL / direct)."""
    from src.services.media.download_utils import is_exit_blocked, ytdlp_subprocess_env

    # A failed earlier attempt can leave a .part file (another client's
    # format or another exit's) that yt-dlp would resume or treat as done.
    for leftover in video_path.parent.iterdir():
        leftover.unlink(missing_ok=True)
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ytdlp_subprocess_env(proxy_url),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("yt-dlp download timed out for %s (%.0fs)", label, timeout)
        await _kill_quietly(proc)
        return _Once(_Attempt.FAILED, error=f"timed out after {timeout:.0f}s")
    except FileNotFoundError:
        logger.warning("yt-dlp not found — video download unavailable")
        return _Once(_Attempt.FAILED, error="yt-dlp not found")
    except asyncio.CancelledError:
        await _kill_quietly(proc)
        raise

    out = stdout.decode("utf-8", errors="replace") if stdout else ""
    if proc.returncode != 0 or not video_path.exists():
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        logger.warning(
            "yt-dlp download failed for %s (rc=%s): %s", label, proc.returncode, err[:300]
        )
        attempt = _Attempt.EXIT_BLOCKED if is_exit_blocked(err) else _Attempt.FAILED
        return _Once(attempt, out, _short_error(err))
    return _Once(_Attempt.OK, out)


def cleanup_local_video(temp_dir: str) -> None:
    """Remove the download's temp dir. Safe to call on partial state."""
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 — cleanup must never propagate
        logger.debug("Local video cleanup failed for %s: %s", temp_dir, e)
