"""Tests for the proxied-mode local hi-res source (hires_prefetch)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media import download_utils, hires_prefetch
from src.services.media.hires_prefetch import (
    LocalHiresSource,
    probe_video_height,
    start_local_hires,
)

VIDEO_ID = "dQw4w9WgXcQ"
PROXY = "http://user-2:pass@p.webshare.io:80"


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", PROXY)


@pytest.fixture
def pass1_video(tmp_path: Path) -> Path:
    path = tmp_path / f"{VIDEO_ID}.mp4"
    path.write_bytes(b"pass1")
    return path


def _fake_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


class TestProbeVideoHeight:
    async def test_should_parse_height_from_ffprobe_output(self, pass1_video):
        with patch.object(
            hires_prefetch.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(b"144\n")),
        ):
            assert await probe_video_height(pass1_video) == 144

    async def test_should_return_none_when_ffprobe_fails(self, pass1_video):
        with patch.object(
            hires_prefetch.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(b"", returncode=1)),
        ):
            assert await probe_video_height(pass1_video) is None

    async def test_should_return_none_when_output_is_not_a_number(self, pass1_video):
        with patch.object(
            hires_prefetch.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(b"N/A\n")),
        ):
            assert await probe_video_height(pass1_video) is None

    async def test_should_return_none_when_ffprobe_is_missing(self, pass1_video):
        with patch.object(
            hires_prefetch.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError),
        ):
            assert await probe_video_height(pass1_video) is None

    async def test_should_return_none_when_ffprobe_cannot_start(self, pass1_video):
        with patch.object(
            hires_prefetch.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=PermissionError),
        ):
            assert await probe_video_height(pass1_video) is None


class TestStartLocalHires:
    async def test_should_return_none_without_a_proxy(self, monkeypatch, pass1_video):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", None)
        with patch.object(hires_prefetch, "download_video_720p", AsyncMock()) as mock_download:
            assert await start_local_hires(VIDEO_ID, pass1_video) is None
        mock_download.assert_not_called()

    async def test_should_return_none_when_hires_is_disabled(self, monkeypatch, proxy, pass1_video):
        monkeypatch.setattr(hires_prefetch.settings, "SCENE_HIRES_ENABLED", False)
        with patch.object(hires_prefetch, "download_video_720p", AsyncMock()) as mock_download:
            assert await start_local_hires(VIDEO_ID, pass1_video) is None
        mock_download.assert_not_called()

    async def test_should_reuse_pass1_file_when_it_is_720p_or_better(self, proxy, pass1_video):
        with (
            patch.object(hires_prefetch, "probe_video_height", AsyncMock(return_value=1080)),
            patch.object(hires_prefetch, "download_video_720p", AsyncMock()) as mock_download,
        ):
            source = await start_local_hires(VIDEO_ID, pass1_video)

        assert source is not None and source.reuses_pass1
        assert await source.path() == pass1_video
        mock_download.assert_not_called()

    async def test_should_start_download_when_pass1_is_low_res(self, proxy, pass1_video, tmp_path):
        local_dir = tmp_path / "vie-hires"
        local_dir.mkdir()
        local_video = local_dir / f"{VIDEO_ID}.mp4"
        local_video.write_bytes(b"720p")
        with (
            patch.object(hires_prefetch, "probe_video_height", AsyncMock(return_value=144)),
            patch.object(
                hires_prefetch,
                "download_video_720p",
                AsyncMock(return_value=(local_video, str(local_dir))),
            ) as mock_download,
        ):
            source = await start_local_hires(VIDEO_ID, pass1_video)
            assert source is not None and not source.reuses_pass1
            assert await source.path() == local_video

        mock_download.assert_awaited_once_with(VIDEO_ID)

    async def test_should_start_download_when_height_is_unknown(self, proxy, pass1_video):
        with (
            patch.object(hires_prefetch, "probe_video_height", AsyncMock(return_value=None)),
            patch.object(hires_prefetch, "download_video_720p", AsyncMock(return_value=None)),
        ):
            source = await start_local_hires(VIDEO_ID, pass1_video)
            assert source is not None
            assert await source.path() is None


class TestLocalHiresSourcePath:
    async def test_should_return_none_when_the_download_raises(self):
        """An optional upgrade never fails the scene extraction."""

        async def broken_download() -> None:
            raise OSError("No space left on device")

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(broken_download()))

        assert await source.path() is None


class TestLocalHiresSourceClose:
    async def test_should_cancel_a_running_download(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_download() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(slow_download()))
        await started.wait()

        await source.close()

        assert cancelled.is_set()

    async def test_should_remove_a_finished_download(self, tmp_path):
        local_dir = tmp_path / "vie-hires"
        local_dir.mkdir()
        local_video = local_dir / f"{VIDEO_ID}.mp4"
        local_video.write_bytes(b"720p")

        async def done_download() -> tuple[Path, str]:
            return local_video, str(local_dir)

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(done_download()))
        assert await source.path() == local_video

        await source.close()

        assert not local_dir.exists()

    async def test_should_leave_the_pass1_file_alone(self, pass1_video):
        source = LocalHiresSource(VIDEO_ID, reuse_path=pass1_video)

        await source.close()

        assert pass1_video.exists()

    async def test_should_be_idempotent(self, tmp_path):
        async def failed_download() -> None:
            return None

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(failed_download()))

        await source.close()
        await source.close()

    async def test_should_propagate_a_cancel_aimed_at_the_caller(self):
        """A cancel landing while close() awaits the dying download is the caller's
        own — swallowing it would leave the caller running after a cancel."""
        started = asyncio.Event()
        dying = asyncio.Event()

        async def slow_download() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                dying.set()
                await asyncio.sleep(0.05)  # yt-dlp kill + wait
                raise

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(slow_download()))
        await started.wait()
        caller = asyncio.create_task(source.close())
        await dying.wait()

        caller.cancel()

        with pytest.raises(asyncio.CancelledError):
            await caller

    async def test_should_finish_when_closed_inside_a_cancelled_callers_finally(self):
        """The caller's cancel is already in flight — close() must not raise it again."""
        started = asyncio.Event()
        entered = asyncio.Event()
        after_close: list[bool] = []

        async def slow_download() -> None:
            started.set()
            await asyncio.sleep(60)

        source = LocalHiresSource(VIDEO_ID, task=asyncio.create_task(slow_download()))
        await started.wait()

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
