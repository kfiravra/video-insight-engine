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
    first_detection_file,
)
from src.services.media.local_video import LocalFile

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


def _file(tmp_path: Path, name: str, height: int | None = 360) -> LocalFile:
    folder = tmp_path / name
    folder.mkdir()
    video = folder / f"{VIDEO_ID}.mp4"
    video.write_bytes(name.encode())
    return LocalFile(video, str(folder), height)


class _Gate:
    """A download that lands (or fails) when the test opens it; records a cancel."""

    def __init__(self, result: LocalFile | None) -> None:
        self.result = result
        self.opened = asyncio.Event()
        self.cancelled = False

    async def __call__(self, *_args: object, **_kwargs: object) -> LocalFile | None:
        try:
            await self.opened.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


class TestFirstDetectionFile:
    """Scene detection reads whichever local file lands first (g4 §8: both
    downloads can be the same muxed 360p file, side by side)."""

    @pytest.fixture
    async def sources(self, monkeypatch, tmp_path):
        low = _Gate(_file(tmp_path, "pass1"))
        high = _Gate(_file(tmp_path, "720p"))
        monkeypatch.setattr(hires_prefetch, "download_video_lowres", low)
        monkeypatch.setattr(hires_prefetch, "download_video_720p", high)
        lowres, hires = LocalLowresSource(VIDEO_ID), LocalHiresSource(VIDEO_ID)
        lowres.start()
        hires.start()
        yield lowres, hires, low, high
        high.opened.set()
        low.opened.set()
        await lowres.close()
        await hires.close()

    async def test_should_read_pass1_when_it_lands_first(self, sources):
        lowres, hires, low, _ = sources
        low.opened.set()

        path = await first_detection_file(lowres, hires)

        assert path is not None and path.parent.name == "pass1"
        await hires.close()

    async def test_should_read_the_720p_download_when_it_lands_first_as_360p(self, sources):
        lowres, hires, _, high = sources
        high.opened.set()

        path = await first_detection_file(lowres, hires)

        assert path is not None and path.parent.name == "720p"
        await hires.close()

    async def test_should_wait_for_pass1_when_a_real_720p_file_lands_first(self, sources, tmp_path):
        """Scene scoring on a real 720p file costs several times the 360p pass."""
        lowres, hires, low, high = sources
        high.result = _file(tmp_path, "real720", height=720)
        high.opened.set()
        asyncio.get_running_loop().call_later(0.01, low.opened.set)

        path = await first_detection_file(lowres, hires)

        assert path is not None and path.parent.name == "pass1"
        await hires.close()

    async def test_should_read_a_real_720p_file_when_pass1_fails(self, sources, tmp_path):
        lowres, hires, low, high = sources
        high.result = _file(tmp_path, "real720", height=720)
        high.opened.set()
        low.result = None
        asyncio.get_running_loop().call_later(0.01, low.opened.set)

        path = await first_detection_file(lowres, hires)

        assert path is not None and path.parent.name == "real720"
        await hires.close()

    async def test_should_cancel_pass1_when_the_720p_file_lands_first(self, sources):
        lowres, hires, low, high = sources
        high.opened.set()

        await first_detection_file(lowres, hires)

        assert low.cancelled is True
        await hires.close()

    async def test_should_keep_the_720p_download_when_pass1_lands_first(self, sources):
        lowres, hires, low, high = sources
        low.opened.set()

        await first_detection_file(lowres, hires)

        assert (high.cancelled, hires.finished) == (False, False)
        await hires.close()

    async def test_should_read_the_other_file_when_the_first_fails(self, sources):
        lowres, hires, low, high = sources
        low.result = None
        low.opened.set()
        await asyncio.sleep(0)
        high.opened.set()

        path = await first_detection_file(lowres, hires)

        assert path is not None and path.parent.name == "720p"
        await hires.close()

    async def test_should_read_pass1_alone_when_no_720p_download_started(
        self, monkeypatch, tmp_path
    ):
        low = _Gate(_file(tmp_path, "pass1"))
        low.opened.set()
        monkeypatch.setattr(hires_prefetch, "download_video_lowres", low)

        path = await first_detection_file(LocalLowresSource(VIDEO_ID), LocalHiresSource(VIDEO_ID))

        assert path is not None and path.parent.name == "pass1"

    async def test_should_answer_none_when_neither_file_lands(self, sources):
        lowres, hires, low, high = sources
        low.result = high.result = None
        low.opened.set()
        high.opened.set()

        assert await first_detection_file(lowres, hires) is None


class TestHeight:
    async def test_should_report_the_landed_files_height(self, monkeypatch, tmp_path):
        gate = _Gate(_file(tmp_path, "720p", height=720))
        gate.opened.set()
        monkeypatch.setattr(hires_prefetch, "download_video_720p", gate)
        source = LocalHiresSource(VIDEO_ID)

        await source.path()

        assert source.height == 720
        await source.close()

    async def test_should_report_no_height_before_the_file_lands(self):
        assert LocalHiresSource(VIDEO_ID).height is None


class TestFinished:
    async def test_should_not_be_finished_before_the_download_starts(self):
        assert LocalHiresSource(VIDEO_ID).finished is False

    async def test_should_not_be_finished_while_downloading(self, monkeypatch, tmp_path):
        gate = _Gate(_file(tmp_path, "720p"))
        monkeypatch.setattr(hires_prefetch, "download_video_720p", gate)
        source = LocalHiresSource(VIDEO_ID)
        source.start()
        await asyncio.sleep(0)

        assert source.finished is False
        gate.opened.set()
        await source.close()

    async def test_should_be_finished_once_the_file_landed(self, monkeypatch, tmp_path):
        gate = _Gate(_file(tmp_path, "720p"))
        gate.opened.set()
        monkeypatch.setattr(hires_prefetch, "download_video_720p", gate)
        source = LocalHiresSource(VIDEO_ID)

        await source.path()

        assert source.finished is True
        await source.close()


class TestPurpose:
    async def test_should_label_a_download_started_for_moment_fill(self, fake_download):
        async def landed() -> None:
            return None

        downloads = fake_download(landed)
        source = LocalHiresSource(VIDEO_ID)

        source.start(timeout=5.0, purpose="moment_fill")
        await source.path()

        assert downloads.calls == ["moment_fill"]

    async def test_should_label_the_prefetch_by_default(self, fake_download):
        async def landed() -> None:
            return None

        downloads = fake_download(landed)

        await LocalHiresSource(VIDEO_ID).path()

        assert downloads.calls == [DOWNLOAD_PURPOSE]
