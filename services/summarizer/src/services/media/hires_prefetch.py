"""The run's local video files, downloaded ahead of the phases that read them.

Each job downloads two renditions at most once each, both started by the
metadata phase as soon as the video is accepted (``validate_duration``) and
its frames are not cached:

* ``LocalLowresSource`` — the 360p pass-1 file scene detection reads; scene
  extraction closes it as soon as detection is done.
* ``LocalHiresSource`` — the ≤720p file: scene extraction seeks the selected
  frames in it (or, when it is still downloading, a deferred hi-res pass
  does — ``hires_upgrade``), assembly's moment fill seeks the still-frameless
  moments in it later, then closes it.

Detection reads the 720p file instead of pass 1 when it lands first as the
same ≤360p rendition (``first_detection_file``): when YouTube serves no
video-only rendition both downloads fetch the muxed 360p file side by side,
and the slower copy must not hold frames-done. A real 720p file is only read
when pass 1 fails — scene scoring on it costs several times the 360p pass.

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
    PASS1_HEIGHT,
    LocalFile,
    cleanup_local_video,
    download_video_720p,
    download_video_lowres,
)

logger = logging.getLogger(__name__)

# ``pipeline.timing`` label of the run's single 720p download.
DOWNLOAD_PURPOSE = "prefetch"

Download = LocalFile | None


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

    @property
    def finished(self) -> bool:
        """True once the download ended — landed, failed or cancelled; False before it starts."""
        return self._task is not None and self._task.done()

    @property
    def height(self) -> int | None:
        """The landed file's height as yt-dlp picked it; None until it lands, or unknown."""
        task = self._task
        if task is None or not task.done() or task.cancelled() or task.exception() is not None:
            return None
        landed = task.result()
        return landed.height if isinstance(landed, LocalFile) else None

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
    """The run's 360p pass-1 file — scene detection reads it."""

    _label = "Low-res"
    _default_timeout = LOWRES_DOWNLOAD_TIMEOUT

    def _download(self, timeout: float) -> Coroutine[Any, Any, Download]:
        return download_video_lowres(self._video_id, timeout)


async def first_detection_file(
    lowres: LocalLowresSource, hires: LocalHiresSource | None
) -> Path | None:
    """The file scene detection reads: pass 1, or its twin when the 720p download lands first.

    A 720p download that lands first as a ≤360p rendition (the muxed file both
    downloads get when YouTube serves no video-only one) is read instead and
    pass 1 is closed (its download cancelled). A real 720p file waits as the
    fallback for a pass 1 that fails. Pass 1 wins a tie; None when neither lands.
    """
    if hires is None or not hires.started:
        return await lowres.path()
    readers: dict[asyncio.Future[Path | None], LocalVideoDownload] = {
        asyncio.ensure_future(lowres.path()): lowres,
        asyncio.ensure_future(hires.path()): hires,
    }
    fallback: Path | None = None
    try:
        while readers:
            done, _ = await asyncio.wait(readers, return_when=asyncio.FIRST_COMPLETED)
            # Pass 1 first: on a tie its smaller file is the one to read.
            for reader in sorted(done, key=lambda r: readers[r] is not lowres):
                source = readers.pop(reader)
                path = reader.result()
                if path is None:
                    continue
                if source is lowres:
                    return path
                height = hires.height
                if height is not None and height <= PASS1_HEIGHT:
                    logger.info(
                        "720p download landed first as %dp — scene detection reads it", height
                    )
                    await lowres.close()
                    return path
                fallback = path
        return fallback
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
