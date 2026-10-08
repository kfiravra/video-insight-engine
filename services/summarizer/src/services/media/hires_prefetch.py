"""The run's local video files, downloaded ahead of the phases that read them.

Each job downloads two renditions at most once each, both started by the
metadata phase as soon as the video is accepted (``validate_duration``) and
its frames are not cached:

* ``LocalLowresSource`` — the worst-quality pass-1 file scene detection
  reads; scene extraction closes it as soon as detection is done.
* ``LocalHiresSource`` — the ≤720p file: scene extraction seeks the selected
  frames in it, assembly's moment fill seeks the still-frameless moments in it
  later, then closes it.

The handles live on the pipeline context (``ctx.lowres_video`` /
``ctx.hires_video``) because their readers sit in different phases.
``path()`` starts a download nothing started yet (a manifest cache hit skips
the early start, yet moment fill may still need the 720p file — it starts it
with its own, shorter timeout). ``close()`` cancels an unfinished download and
deletes a finished one; the runner closes both again when the run ends, so
failed and cancelled runs leave no file behind.

Every hi-res seek is local. Direct stream-URL seeks were dropped: proxied they
leave from the blocked host IP and 403; proxyless, client-bound googlevideo
URLs 403 plain ffmpeg often enough that the local download was the usual
outcome anyway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from src.services.media.local_video import (
    DOWNLOAD_TIMEOUT,
    LOWRES_DOWNLOAD_TIMEOUT,
    cleanup_local_video,
    download_video_720p,
    download_video_lowres,
)

logger = logging.getLogger(__name__)

# ``pipeline.timing`` label of the run's single 720p download.
DOWNLOAD_PURPOSE = "prefetch"

Download = tuple[Path, str] | None


class LocalVideoDownload:
    """One background download of the run's video: started once, shared, closed once."""

    _label = "video"
    _default_timeout = DOWNLOAD_TIMEOUT

    def __init__(self, video_id: str) -> None:
        self._video_id = video_id
        self._task: asyncio.Task[Download] | None = None
        self._closed = False

    def _download(self, timeout: float) -> Coroutine[Any, Any, Download]:
        raise NotImplementedError

    @property
    def started(self) -> bool:
        """True once the download was started (it may still be running or have failed)."""
        return self._task is not None

    def start(self, timeout: float | None = None) -> None:
        """Begin the download in the background. Idempotent; a no-op after ``close()``.

        ``timeout`` caps the download when this call is the one that starts it
        (None = the downloader's own); a later call never changes a running one.
        """
        if self._task is None and not self._closed:
            budget = self._default_timeout if timeout is None else timeout
            self._task = asyncio.create_task(self._download(budget))

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
        except Exception as e:  # noqa: BLE001 — a failed download never fails a phase
            logger.warning("%s download failed for %s: %s", self._label, self._video_id, e)
            return None
        if downloaded is None:
            logger.warning("%s download unavailable for %s", self._label, self._video_id)
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
            # The downloaders kill yt-dlp and remove the partial dir on cancel.
            task.cancel()
        try:
            downloaded = await task
        except asyncio.CancelledError:
            if current is not None and current.cancelling() > pending:
                raise  # close() itself was cancelled from outside
            return
        except Exception as e:  # noqa: BLE001 — cleanup never propagates
            logger.debug("%s download for %s failed: %s", self._label, self._video_id, e)
            return
        if downloaded is not None:
            # rmtree of a 30-80 MB file blocks; keep it off the event loop,
            # which is about to stream `complete` and save.
            await asyncio.to_thread(cleanup_local_video, downloaded[1])


class LocalHiresSource(LocalVideoDownload):
    """The run's ≤720p file — hi-res frames and moment fill both seek it."""

    _label = "720p"

    def _download(self, timeout: float) -> Coroutine[Any, Any, Download]:
        return download_video_720p(self._video_id, timeout, purpose=DOWNLOAD_PURPOSE)


class LocalLowresSource(LocalVideoDownload):
    """The run's worst-quality pass-1 file — scene detection reads it."""

    _label = "Low-res"
    _default_timeout = LOWRES_DOWNLOAD_TIMEOUT

    def _download(self, timeout: float) -> Coroutine[Any, Any, Download]:
        return download_video_lowres(self._video_id, timeout)
