"""Proxied-mode hi-res source: one local 720p file instead of stream-URL seeks.

With ``YOUTUBE_PROXY_URL`` set, the host IP is the blocked one — ffmpeg seeks
on a looked-up googlevideo URL leave from it unproxied and 403 every time, so
the refiner would burn its whole budget before reaching the local download
anyway. This module skips that dead path: it starts the proxied 720p download
right after the pass-1 download, so it runs CONCURRENTLY with scene detection
and scoring, and hands the refiner a ready local file. If pass 1 already came
down at >=720p (a video with no smaller rendition) the pass-1 file is reused
and nothing is downloaded.

Proxyless runs get ``None`` from ``start_local_hires`` and keep the seek path.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import settings
from src.services.media.download_utils import ytdlp_proxy_url
from src.services.media.local_video import cleanup_local_video, download_video_720p

logger = logging.getLogger(__name__)

_HIRES_MIN_HEIGHT = 720
_PROBE_TIMEOUT = 15.0

Download = tuple[Path, str] | None


async def probe_video_height(video_path: Path) -> int | None:
    """Height in pixels of the first video stream, or None when unknown."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "csv=p=0",
            str(video_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("ffprobe timed out for %s", video_path)
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    except OSError as e:
        # Missing binary, PermissionError, EMFILE: an unknown height just means
        # "download 720p" — the probe must never fail the scene extraction.
        logger.warning("ffprobe unavailable — cannot measure pass-1 height: %s", e)
        return None

    if proc.returncode != 0:
        return None
    try:
        return int(stdout.decode("utf-8", errors="replace").strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


class LocalHiresSource:
    """A local video file for hi-res seeks: the pass-1 file or an in-flight 720p download.

    ``path()`` resolves to the file (awaiting the download if needed); ``close()``
    cancels an unfinished download and removes a finished one. The pass-1 file
    is owned by scene extraction and is never touched here.
    """

    def __init__(
        self,
        video_id: str,
        reuse_path: Path | None = None,
        task: asyncio.Task[Download] | None = None,
    ) -> None:
        self._video_id = video_id
        self._reuse_path = reuse_path
        self._task = task

    @property
    def reuses_pass1(self) -> bool:
        return self._reuse_path is not None

    async def path(self) -> Path | None:
        if self._reuse_path is not None:
            return self._reuse_path
        if self._task is None:
            return None
        try:
            downloaded = await self._task
        except Exception as e:  # noqa: BLE001 — an optional upgrade never fails extraction
            logger.warning("Prefetched 720p download failed for %s: %s", self._video_id, e)
            return None
        if downloaded is None:
            logger.warning("Prefetched 720p download unavailable for %s", self._video_id)
            return None
        return downloaded[0]

    async def close(self) -> None:
        if self._task is None:
            return
        task, self._task = self._task, None
        # A cancel aimed at our caller also cancels the awaited download, so
        # task.cancelled() can't tell it from our own cancel — a rise in the
        # caller's pending-cancel count can.
        current = asyncio.current_task()
        pending = current.cancelling() if current is not None else 0
        if not task.done():
            # download_video_720p kills yt-dlp and removes the partial dir on cancel.
            task.cancel()
        try:
            downloaded = await task
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > pending:
                raise  # close() itself was cancelled from outside
            return
        except Exception as e:  # noqa: BLE001 — cleanup never propagates
            logger.debug("Prefetched 720p download for %s failed: %s", self._video_id, e)
            return
        if downloaded is not None:
            cleanup_local_video(downloaded[1])


async def start_local_hires(video_id: str, pass1_video: Path) -> LocalHiresSource | None:
    """Prepare the local hi-res source for a proxied run; None keeps the seek path."""
    if not settings.SCENE_HIRES_ENABLED or not ytdlp_proxy_url():
        return None

    height = await probe_video_height(pass1_video)
    if height is not None and height >= _HIRES_MIN_HEIGHT:
        logger.info("Pass-1 video for %s is already %dp — reusing it for hi-res", video_id, height)
        return LocalHiresSource(video_id, reuse_path=pass1_video)

    logger.info("Proxied run for %s: starting 720p download alongside scene detection", video_id)
    return LocalHiresSource(video_id, task=asyncio.create_task(download_video_720p(video_id)))
