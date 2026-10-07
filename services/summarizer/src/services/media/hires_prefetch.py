"""The run's one local 720p file — hi-res frames and moment fill both seek it.

A job downloads the ≤720p rendition at most once. The handle lives on the
pipeline context (``ctx.hires_video``) because its readers sit in different
phases: scene extraction starts the download while it detects scenes and
seeks the selected frames in it; assembly's moment fill seeks the
still-frameless moments in it later. ``path()`` starts the download when
nothing has yet — a manifest cache hit skips scene extraction, yet moment fill
may still need frames. ``close()`` cancels an unfinished download and deletes
a finished one: assembly calls it after moment fill, the runner again when the
run ends, so failed and cancelled runs leave no file behind.

Every hi-res seek is local. Direct stream-URL seeks were dropped: proxied they
leave from the blocked host IP and 403; proxyless, client-bound googlevideo
URLs 403 plain ffmpeg often enough that the local download was the usual
outcome anyway.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.services.media.local_video import cleanup_local_video, download_video_720p

logger = logging.getLogger(__name__)

# ``pipeline.timing`` label of the run's single 720p download.
DOWNLOAD_PURPOSE = "prefetch"

Download = tuple[Path, str] | None


class LocalHiresSource:
    """The run's 720p download: started once, shared by every reader, closed once."""

    def __init__(self, video_id: str) -> None:
        self._video_id = video_id
        self._task: asyncio.Task[Download] | None = None
        self._closed = False

    @property
    def started(self) -> bool:
        """True once the download was started (it may still be running or have failed)."""
        return self._task is not None

    def start(self) -> None:
        """Begin the download in the background. Idempotent; a no-op after ``close()``."""
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(
                download_video_720p(self._video_id, purpose=DOWNLOAD_PURPOSE)
            )

    async def path(self) -> Path | None:
        """The local file, awaiting (and if needed starting) the download; None on failure.

        The download is shielded: a reader's own deadline expiring cancels the
        reader, never the file the next reader needs.
        """
        self.start()
        task = self._task
        if task is None:
            return None
        current = asyncio.current_task()
        pending = current.cancelling() if current is not None else 0
        try:
            downloaded = await asyncio.shield(task)
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > pending:
                raise  # the reader itself was cancelled
            return None  # close() cancelled the download under this reader
        except Exception as e:  # noqa: BLE001 — an optional upgrade never fails a phase
            logger.warning("720p download failed for %s: %s", self._video_id, e)
            return None
        if downloaded is None:
            logger.warning("720p download unavailable for %s", self._video_id)
            return None
        return downloaded[0]

    async def close(self) -> None:
        """Cancel an unfinished download, delete a finished one. Safe to call twice."""
        self._closed = True
        task, self._task = self._task, None
        if task is None:
            return
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
            logger.debug("720p download for %s failed: %s", self._video_id, e)
            return
        if downloaded is not None:
            cleanup_local_video(downloaded[1])
