"""Tests for the run's one local 720p file (hires_prefetch.LocalHiresSource)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from src.services.media import hires_prefetch
from src.services.media.hires_prefetch import (
    DOWNLOAD_PURPOSE,
    LocalHiresSource,
    LocalLowresSource,
)

VIDEO_ID = "dQw4w9WgXcQ"

DownloadFn = Callable[..., Awaitable[tuple[Path, str] | None]]


@pytest.fixture
def downloaded(tmp_path: Path) -> tuple[Path, str]:
    local_dir = tmp_path / "vie-hires"
    local_dir.mkdir()
    video = local_dir / f"{VIDEO_ID}.mp4"
    video.write_bytes(b"720p")
    return video, str(local_dir)


class _Downloads:
    """Records every download the handle starts."""

    def __init__(self, result: DownloadFn) -> None:
        self.calls: list[str] = []
        self.timeouts: list[float] = []
        self._result = result

    async def __call__(
        self, youtube_id: str, timeout: float, *, purpose: str
    ) -> tuple[Path, str] | None:
        self.calls.append(purpose)
        self.timeouts.append(timeout)
        return await self._result()


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> Callable[[DownloadFn], _Downloads]:
    def install(result: DownloadFn) -> _Downloads:
        recorder = _Downloads(result)
        monkeypatch.setattr(hires_prefetch, "download_video_720p", recorder)
        return recorder

    return install


async def _never() -> None:
    await asyncio.sleep(60)


class TestStart:
    async def test_should_download_once_however_many_readers_ask(self, fake_download, downloaded):
        async def done() -> tuple[Path, str]:
            return downloaded

        recorder = fake_download(done)
        source = LocalHiresSource(VIDEO_ID)

        source.start()
        paths = await asyncio.gather(source.path(), source.path())

        assert (recorder.calls, paths) == ([DOWNLOAD_PURPOSE], [downloaded[0], downloaded[0]])

    async def test_should_cap_the_download_with_the_starting_callers_timeout(
        self, fake_download, downloaded
    ):
        """Moment fill starts the file with what is left of its own budget."""

        async def done() -> tuple[Path, str]:
            return downloaded

        recorder = fake_download(done)
        source = LocalHiresSource(VIDEO_ID)

        source.start(timeout=120.0)
        source.start(timeout=5.0)
        await source.path()

        assert recorder.timeouts == [120.0]

    async def test_should_use_the_download_timeout_by_default(self, fake_download, downloaded):
        async def done() -> tuple[Path, str]:
            return downloaded

        recorder = fake_download(done)

        await LocalHiresSource(VIDEO_ID).path()

        assert recorder.timeouts == [hires_prefetch.DOWNLOAD_TIMEOUT]

    async def test_should_start_lazily_on_the_first_path_request(self, fake_download, downloaded):
        async def done() -> tuple[Path, str]:
            return downloaded

        fake_download(done)
        source = LocalHiresSource(VIDEO_ID)

        assert await source.path() == downloaded[0]

    async def test_should_not_start_after_close(self, fake_download):
        recorder = fake_download(_never)
        source = LocalHiresSource(VIDEO_ID)
        await source.close()

        source.start()

        assert (recorder.calls, await source.path()) == ([], None)


class TestPath:
    async def test_should_return_none_when_the_download_raises(self, fake_download):
        async def broken() -> None:
            raise OSError("No space left on device")

        fake_download(broken)

        assert await LocalHiresSource(VIDEO_ID).path() is None

    async def test_should_return_none_when_the_download_failed(self, fake_download):
        async def failed() -> None:
            return None

        fake_download(failed)

        assert await LocalHiresSource(VIDEO_ID).path() is None

    async def test_should_keep_downloading_when_a_readers_deadline_expires(
        self, fake_download, downloaded
    ):
        release = asyncio.Event()

        async def slow() -> tuple[Path, str]:
            await release.wait()
            return downloaded

        fake_download(slow)
        source = LocalHiresSource(VIDEO_ID)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(source.path(), timeout=0.01)

        release.set()

        assert await source.path() == downloaded[0]

    async def test_should_return_none_when_closed_under_a_waiting_reader(self, fake_download):
        fake_download(_never)
        source = LocalHiresSource(VIDEO_ID)
        reader = asyncio.create_task(source.path())
        await asyncio.sleep(0)

        await source.close()

        assert await reader is None


class TestClose:
    async def test_should_cancel_a_running_download(self, fake_download):
        cancelled = asyncio.Event()

        async def slow() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        fake_download(slow)
        source = LocalHiresSource(VIDEO_ID)
        source.start()
        await asyncio.sleep(0)

        await source.close()

        assert cancelled.is_set()

    async def test_should_remove_a_finished_download(self, fake_download, downloaded):
        async def done() -> tuple[Path, str]:
            return downloaded

        fake_download(done)
        source = LocalHiresSource(VIDEO_ID)
        assert await source.path() == downloaded[0]

        await source.close()

        assert not Path(downloaded[1]).exists()

    async def test_should_delete_the_file_off_the_event_loop_thread(
        self, fake_download, downloaded, monkeypatch
    ):
        async def done() -> tuple[Path, str]:
            return downloaded

        threads: list[int] = []
        monkeypatch.setattr(
            hires_prefetch, "cleanup_local_video", lambda _d: threads.append(threading.get_ident())
        )
        fake_download(done)
        source = LocalHiresSource(VIDEO_ID)
        await source.path()

        await source.close()

        assert len(threads) == 1 and threads[0] != threading.get_ident()

    async def test_should_be_idempotent(self, fake_download):
        async def failed() -> None:
            return None

        fake_download(failed)
        source = LocalHiresSource(VIDEO_ID)
        source.start()

        await source.close()
        await source.close()

    async def test_should_propagate_a_cancel_aimed_at_the_caller(self, fake_download):
        """A cancel landing while close() awaits the dying download is the caller's
        own — swallowing it would leave the caller running after a cancel."""
        dying = asyncio.Event()

        async def slow() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                dying.set()
                await asyncio.sleep(0.05)  # yt-dlp kill + wait
                raise

        fake_download(slow)
        source = LocalHiresSource(VIDEO_ID)
        source.start()
        await asyncio.sleep(0)
        caller = asyncio.create_task(source.close())
        await dying.wait()

        caller.cancel()

        with pytest.raises(asyncio.CancelledError):
            await caller

    async def test_should_finish_when_closed_inside_a_cancelled_callers_finally(
        self, fake_download
    ):
        """The caller's cancel is already in flight — close() must not raise it again."""
        fake_download(_never)
        source = LocalHiresSource(VIDEO_ID)
        source.start()
        entered = asyncio.Event()
        after_close: list[bool] = []

        async def caller() -> None:
            try:
                entered.set()
                await asyncio.sleep(60)
            finally:
                await source.close()
                after_close.append(True)

        task = asyncio.create_task(caller())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert after_close == [True]


class TestLocalLowresSource:
    async def test_should_download_the_pass1_rendition(self, monkeypatch, downloaded):
        calls: list[str] = []

        async def lowres(youtube_id: str, timeout: float) -> tuple[Path, str]:
            calls.append(youtube_id)
            assert timeout == hires_prefetch.LOWRES_DOWNLOAD_TIMEOUT
            return downloaded

        monkeypatch.setattr(hires_prefetch, "download_video_lowres", lowres)

        assert (await LocalLowresSource(VIDEO_ID).path(), calls) == (downloaded[0], [VIDEO_ID])
