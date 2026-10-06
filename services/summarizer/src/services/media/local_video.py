"""One-shot local 720p download for hi-res frames (fallback proxyless, primary proxied).

YouTube increasingly binds googlevideo stream URLs to the requesting client
(PO tokens / headers), so a URL resolved by ``yt-dlp --get-url`` can 403 when
plain ffmpeg fetches it — observed as systematic ``rc=8`` failures across
every hi-res seek of a run. yt-dlp itself downloads fine (it speaks the
client protocol), so the robust fallback is: download a 720p rendition ONCE
and extract all needed frames from the local file, where ``-ss`` seeks are
instant and can't 403.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 180.0
# Video-only first (frames don't need audio, halves the download), mp4 for
# ffmpeg-friendliness, hard 720p cap to bound size (~30-80MB for 10 min).
_FORMAT_SPEC = "bestvideo[height<=720][ext=mp4]/best[height<=720][ext=mp4]/best[ext=mp4]"


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
    youtube_id: str, timeout: float = DOWNLOAD_TIMEOUT
) -> tuple[Path, str] | None:
    """Download a ≤720p rendition; returns (video_path, temp_dir) or None.

    Tries YTDLP_HIRES_PLAYER_CLIENTS first (android alone caps at 360p), then
    once more with YTDLP_PLAYER_CLIENTS if that fails, both within ``timeout``.
    The CALLER owns cleanup of temp_dir (``cleanup_local_video``). Best-effort:
    every failure logs and returns None. Cancellation (an outer budget expiring
    mid-download) kills the subprocess and removes the partial file before
    re-raising, so neither leaks out of a long-lived worker.
    """
    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id for local 720p download: %s", youtube_id)
        return None

    from src.services.media.download_utils import ytdlp_hires_client_attempts

    temp_dir = tempfile.mkdtemp(prefix=f"vie-hires-{youtube_id}-")
    video_path = Path(temp_dir) / f"{youtube_id}.mp4"
    deadline = asyncio.get_running_loop().time() + timeout
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
                return video_path, temp_dir
    except asyncio.CancelledError:
        cleanup_local_video(temp_dir)
        raise
    cleanup_local_video(temp_dir)
    return None


async def _run_ytdlp(youtube_id: str, video_path: Path, clients: str, timeout: float) -> bool:
    """One yt-dlp attempt with the given player clients; True when the file landed."""
    from src.services.media.download_utils import ytdlp_client_cli_args, ytdlp_subprocess_env

    # A failed earlier attempt can leave a .part file (another client's
    # format) that yt-dlp would resume or treat as already downloaded.
    for leftover in video_path.parent.iterdir():
        leftover.unlink(missing_ok=True)
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-f",
            _FORMAT_SPEC,
            "--no-playlist",
            "--no-warnings",
            *ytdlp_client_cli_args(clients),
            "-o",
            str(video_path),
            f"https://www.youtube.com/watch?v={youtube_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ytdlp_subprocess_env(),
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Local 720p download timed out for %s (%.0fs)", youtube_id, timeout)
        await _kill_quietly(proc)
        return False
    except FileNotFoundError:
        logger.warning("yt-dlp not found — local 720p fallback unavailable")
        return False
    except asyncio.CancelledError:
        await _kill_quietly(proc)
        raise

    if proc.returncode != 0 or not video_path.exists():
        tail = stderr.decode("utf-8", errors="replace")[:300] if stderr else ""
        logger.warning(
            "Local 720p download failed for %s (clients=%s, rc=%s): %s",
            youtube_id,
            clients,
            proc.returncode,
            tail,
        )
        return False
    return True


def cleanup_local_video(temp_dir: str) -> None:
    """Remove the download's temp dir. Safe to call on partial state."""
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 — cleanup must never propagate
        logger.debug("Local video cleanup failed for %s: %s", temp_dir, e)
