"""The run's two video downloads: the low-res pass-1 file and the ≤720p file.

The low-res file (worst quality) feeds scene detection (scoring reads the
JPEGs detection wrote); the 720p file feeds hi-res frames and moment fill.
Both go through yt-dlp itself — googlevideo stream URLs are bound to the
requesting client (PO tokens / headers) and 403 plain ffmpeg — and every
frame is then seeked out of the local file, where ``-ss`` is instant and
can't 403. Each download lands in its own temp dir, which the CALLER owns
(``cleanup_local_video``).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from enum import Enum
from pathlib import Path

from src.services.pipeline.pipeline_timing import record_download
from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 180.0
LOWRES_DOWNLOAD_TIMEOUT = 120.0
# Video-only first (frames don't need audio, halves the download), mp4 for
# ffmpeg-friendliness, hard 720p cap to bound size (~30-80MB for 10 min).
_FORMAT_SPEC = "bestvideo[height<=720][ext=mp4]/best[height<=720][ext=mp4]/best[ext=mp4]"
# 144p is plenty for scene detection and local scoring and keeps pass 1 fast.
_LOWRES_FORMAT_SPEC = "worstvideo[ext=mp4]/worst[ext=mp4]/worst"


class _Attempt(Enum):
    """Outcome of one yt-dlp process through one proxy exit."""

    OK = "ok"
    FAILED = "failed"
    EXIT_BLOCKED = "exit_blocked"  # bot check or 429: worth the next exit


async def _kill_quietly(proc: asyncio.subprocess.Process | None) -> None:
    """Terminate a still-running yt-dlp without letting cleanup itself raise."""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


async def download_video_720p(
    youtube_id: str, timeout: float = DOWNLOAD_TIMEOUT, *, purpose: str = "hires"
) -> tuple[Path, str] | None:
    """Download a ≤720p rendition; returns (video_path, temp_dir) or None.

    Tries YTDLP_HIRES_PLAYER_CLIENTS first (android alone caps at 360p), then
    once more with YTDLP_PLAYER_CLIENTS if that fails, both within ``timeout``.
    The CALLER owns cleanup of temp_dir (``cleanup_local_video``). Best-effort:
    every failed download logs and returns None. An exception (a cancel when
    an outer budget expires mid-download, or EMFILE/EACCES) kills the
    subprocess and removes the temp dir before re-raising, so neither leaks out
    of a long-lived worker. ``purpose`` labels the download in the run's
    ``pipeline.timing`` (one 720p per job is the goal).
    """
    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id for local 720p download: %s", youtube_id)
        return None

    from src.services.media.download_utils import ytdlp_hires_client_attempts

    temp_dir = tempfile.mkdtemp(prefix=f"vie-hires-{youtube_id}-")
    video_path = Path(temp_dir) / f"{youtube_id}.mp4"
    deadline = asyncio.get_running_loop().time() + timeout
    started = time.monotonic()
    try:
        for clients in ytdlp_hires_client_attempts():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            if await _run_ytdlp(youtube_id, video_path, clients, remaining):
                size_mb = video_path.stat().st_size / 1_048_576
                logger.info(
                    "Local 720p download for %s: %.1fMB (clients=%s)", youtube_id, size_mb, clients
                )
                record_download(
                    kind="720p", purpose=purpose, start_monotonic=started, path=video_path, ok=True
                )
                return video_path, temp_dir
    except BaseException:
        record_download(kind="720p", purpose=purpose, start_monotonic=started, path=None, ok=False)
        cleanup_local_video(temp_dir)
        raise
    record_download(kind="720p", purpose=purpose, start_monotonic=started, path=None, ok=False)
    cleanup_local_video(temp_dir)
    return None


async def download_video_lowres(
    youtube_id: str, timeout: float = LOWRES_DOWNLOAD_TIMEOUT
) -> tuple[Path, str] | None:
    """Download the worst-quality rendition (pass 1); returns (video_path, temp_dir) or None.

    One attempt with YTDLP_PLAYER_CLIENTS. Same contract as
    ``download_video_720p``: the caller owns ``temp_dir``, failed downloads log
    and return None, an exception (a cancel included) kills yt-dlp and removes
    the temp dir before re-raising.
    """
    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id for low-res download: %s", youtube_id)
        return None

    temp_dir = tempfile.mkdtemp(prefix=f"vie-lowres-{youtube_id}-")
    video_path = Path(temp_dir) / f"{youtube_id}.mp4"
    started = time.monotonic()
    try:
        ok = await _run_ytdlp(youtube_id, video_path, None, timeout, _LOWRES_FORMAT_SPEC)
    except BaseException:
        record_download(
            kind="lowres", purpose="scene_detect", start_monotonic=started, path=None, ok=False
        )
        cleanup_local_video(temp_dir)
        raise
    record_download(
        kind="lowres", purpose="scene_detect", start_monotonic=started, path=video_path, ok=ok
    )
    if not ok:
        cleanup_local_video(temp_dir)
        return None
    logger.info(
        "Downloaded low-res video for %s: %.1fMB", youtube_id, video_path.stat().st_size / 1_048_576
    )
    return video_path, temp_dir


async def _run_ytdlp(
    youtube_id: str,
    video_path: Path,
    clients: str | None,
    timeout: float,
    format_spec: str = _FORMAT_SPEC,
) -> bool:
    """One yt-dlp download with the given player clients; True when the file landed.

    ``clients`` None = YTDLP_PLAYER_CLIENTS (``ytdlp_client_cli_args`` default).
    YouTube's bot check and a 429 are scoped to the proxy exit's IP, so either
    moves on to the next sticky exit (``ytdlp_proxy_exit_urls``: the job's
    round-robin exit first, recently blocked exits last) within the same
    ``timeout``; any other failure ends the download.
    """
    from src.services.media.download_utils import (
        record_blocked_exit,
        record_working_exit,
        ytdlp_client_cli_args,
        ytdlp_proxy_exit_urls,
    )

    argv = [
        "yt-dlp",
        "-f",
        format_spec,
        "--no-playlist",
        "--no-warnings",
        *ytdlp_client_cli_args(clients),
        "-o",
        str(video_path),
        f"https://www.youtube.com/watch?v={youtube_id}",
    ]
    label = f"{youtube_id} (format={format_spec.split('/', 1)[0]}, clients={clients})"
    exit_urls: list[str | None] = [*ytdlp_proxy_exit_urls(youtube_id)] or [None]
    deadline = asyncio.get_running_loop().time() + timeout
    for n, proxy_url in enumerate(exit_urls, 1):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        attempt = await _run_ytdlp_once(argv, video_path, remaining, proxy_url, label)
        if attempt is not _Attempt.EXIT_BLOCKED:
            if attempt is _Attempt.OK and proxy_url:
                record_working_exit(proxy_url, f"yt-dlp download for {label}", n, len(exit_urls))
            return attempt is _Attempt.OK
        if proxy_url:
            record_blocked_exit(proxy_url)
        logger.warning(
            "yt-dlp download for %s blocked on proxy exit %d/%d", label, n, len(exit_urls)
        )
    return False


async def _run_ytdlp_once(
    argv: list[str],
    video_path: Path,
    timeout: float,
    proxy_url: str | None,
    label: str,
) -> _Attempt:
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
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("yt-dlp download timed out for %s (%.0fs)", label, timeout)
        await _kill_quietly(proc)
        return _Attempt.FAILED
    except FileNotFoundError:
        logger.warning("yt-dlp not found — video download unavailable")
        return _Attempt.FAILED
    except asyncio.CancelledError:
        await _kill_quietly(proc)
        raise

    if proc.returncode != 0 or not video_path.exists():
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        logger.warning(
            "yt-dlp download failed for %s (rc=%s): %s", label, proc.returncode, err[:300]
        )
        return _Attempt.EXIT_BLOCKED if is_exit_blocked(err) else _Attempt.FAILED
    return _Attempt.OK


def cleanup_local_video(temp_dir: str) -> None:
    """Remove the download's temp dir. Safe to call on partial state."""
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 — cleanup must never propagate
        logger.debug("Local video cleanup failed for %s: %s", temp_dir, e)
