"""Tests for hi-res refinement of selected scene frames."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.services.media import download_utils
from src.services.media.hires_prefetch import LocalHiresSource
from src.services.media.hires_refiner import refine_selected_frames


def _frame(tmp_path: Path, index: int, timestamp: float) -> dict:
    path = tmp_path / f"scene_{index:04d}.jpg"
    path.write_bytes(b"lowres")
    return {"index": index, "path": str(path), "timestamp": timestamp}


def _configure(mock_settings, enabled: bool = True, timeout: float = 5.0) -> None:
    mock_settings.SCENE_HIRES_ENABLED = enabled
    mock_settings.SCENE_HIRES_CONCURRENCY = 4
    mock_settings.SCENE_HIRES_TIMEOUT = timeout
    mock_settings.SCENE_HIRES_FALLBACK_TIMEOUT = timeout


class TestRefineSelectedFrames:
    """Test the second-pass 720p refinement."""

    @patch("src.services.media.hires_refiner.get_video_stream_url", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.settings")
    async def test_disabled_flag_is_noop(self, mock_settings, mock_stream, tmp_path):
        _configure(mock_settings, enabled=False)
        frames = [_frame(tmp_path, 1, 5.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 0
        mock_stream.assert_not_awaited()
        assert frames[0]["path"].endswith("scene_0001.jpg")

    @patch("src.services.media.hires_refiner.get_video_stream_url", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.settings")
    async def test_zero_timestamps_skip_stream_fetch(self, mock_settings, mock_stream, tmp_path):
        """Frames without a parsed timestamp (0.0) cannot be seeked to."""
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 0.0), _frame(tmp_path, 2, 0.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 0
        mock_stream.assert_not_awaited()

    @patch(
        "src.services.media.local_video.download_video_720p",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.hires_refiner.extract_frame", new_callable=AsyncMock)
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_no_stream_url_keeps_lowres_when_fallback_unavailable(
        self, mock_settings, mock_stream, mock_extract, mock_download, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 0
        mock_download.assert_awaited_once()
        mock_extract.assert_not_awaited()
        assert frames[0]["path"].endswith("scene_0001.jpg")

    @patch(
        "src.services.media.hires_refiner.extract_frame",
        new_callable=AsyncMock,
        return_value=b"hiresbytes",
    )
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_success_swaps_paths_in_place(
        self, mock_settings, mock_stream, mock_extract, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.7)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 2
        for frame in frames:
            assert frame["path"].endswith(".hires.jpg")
            assert Path(frame["path"]).read_bytes() == b"hiresbytes"
        # Seeks use the int-truncated parsed timestamps
        seeked = sorted(call.args[1] for call in mock_extract.await_args_list)
        assert seeked == [5, 12]

    @patch(
        "src.services.media.hires_refiner.extract_frame",
        new_callable=AsyncMock,
        side_effect=[b"hiresbytes", None],
    )
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_failed_frame_keeps_original_path(
        self, mock_settings, mock_stream, mock_extract, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 1
        assert frames[0]["path"].endswith(".hires.jpg")
        assert frames[1]["path"].endswith("scene_0002.jpg")
        assert Path(frames[1]["path"]).read_bytes() == b"lowres"

    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_total_timeout_keeps_partial_results(self, mock_settings, mock_stream, tmp_path):
        """Frames refined before the deadline survive; the rest stay low-res."""
        _configure(mock_settings, timeout=0.3)
        frames = [_frame(tmp_path, 1, 1.0), _frame(tmp_path, 2, 2.0)]

        async def fake_extract(url: str, ts: int) -> bytes:
            if ts == 1:
                return b"hiresbytes"
            await asyncio.sleep(10)
            return b"too-late"

        with patch("src.services.media.hires_refiner.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 1
        assert frames[0]["path"].endswith(".hires.jpg")
        assert frames[1]["path"].endswith("scene_0002.jpg")


class TestLocalDownloadFallback:
    """CDN-403 recovery: 0/N via stream URL → one local 720p download."""

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_all_url_seeks_failing_triggers_local_fallback(
        self, mock_settings, mock_stream, mock_download, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]

        local_dir = tmp_path / "vie-hires-download"
        local_dir.mkdir()
        local_video = local_dir / "dQw4w9WgXcQ.mp4"
        local_video.write_bytes(b"720p-video")
        mock_download.return_value = (local_video, str(local_dir))

        async def fake_extract(source: str, ts: int) -> bytes | None:
            # Plain-ffmpeg CDN seeks 403; the local file works.
            return b"hiresbytes" if source == str(local_video) else None

        with patch("src.services.media.hires_refiner.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 2
        mock_download.assert_awaited_once_with("dQw4w9WgXcQ")
        for frame in frames:
            assert frame["path"].endswith(".hires.jpg")
            assert Path(frame["path"]).read_bytes() == b"hiresbytes"
        # The fallback's temp dir is cleaned up.
        assert not local_dir.exists()

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch(
        "src.services.media.hires_refiner.extract_frame",
        new_callable=AsyncMock,
        return_value=b"hiresbytes",
    )
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_fallback_skipped_when_url_pass_succeeds(
        self, mock_settings, mock_stream, mock_extract, mock_download, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 1
        mock_download.assert_not_awaited()

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_timeout_with_zero_upgrades_triggers_local_fallback(
        self, mock_settings, mock_stream, mock_download, tmp_path
    ):
        """A CDN that stalls every seek must fall back like one that 403s —
        otherwise the manifest stamps hiresCount=0 and re-extracts every run."""
        _configure(mock_settings, timeout=0.2)
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]
        local_dir = tmp_path / "vie-hires-download"
        local_dir.mkdir()
        local_video = local_dir / "dQw4w9WgXcQ.mp4"
        local_video.write_bytes(b"720p-video")
        mock_download.return_value = (local_video, str(local_dir))

        async def fake_extract(source: str, ts: int) -> bytes | None:
            if source == str(local_video):
                return b"hiresbytes"
            await asyncio.sleep(10)  # CDN stalls
            return None

        with patch("src.services.media.hires_refiner.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 2
        mock_download.assert_awaited_once_with("dQw4w9WgXcQ")

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_timeout_with_partial_upgrades_skips_fallback(
        self, mock_settings, mock_stream, mock_download, tmp_path
    ):
        _configure(mock_settings, timeout=0.2)
        frames = [_frame(tmp_path, 1, 1.0), _frame(tmp_path, 2, 2.0)]

        async def fake_extract(url: str, ts: int) -> bytes:
            if ts == 1:
                return b"hiresbytes"
            await asyncio.sleep(10)
            return b"too-late"

        with patch("src.services.media.hires_refiner.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 1
        mock_download.assert_not_awaited()

    @patch(
        "src.services.media.local_video.download_video_720p",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "src.services.media.hires_refiner.extract_frame",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "src.services.media.hires_refiner.get_video_stream_url",
        new_callable=AsyncMock,
        return_value="https://stream.example/video",
    )
    @patch("src.services.media.hires_refiner.settings")
    async def test_fallback_download_failure_keeps_lowres(
        self, mock_settings, mock_stream, mock_extract, mock_download, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0)]

        result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 0
        mock_download.assert_awaited_once()
        assert frames[0]["path"].endswith("scene_0001.jpg")
        assert Path(frames[0]["path"]).read_bytes() == b"lowres"


class TestProxiedLocalSource:
    """With a proxy the stream URL is never looked up — frames come from a local file."""

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.get_video_stream_url", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.settings")
    async def test_prefetched_source_refines_without_stream_lookup(
        self, mock_settings, mock_stream, mock_download, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]
        local_video = tmp_path / "dQw4w9WgXcQ.mp4"
        local_video.write_bytes(b"720p-video")
        source = LocalHiresSource("dQw4w9WgXcQ", reuse_path=local_video)

        async def fake_extract(src: str, ts: int) -> bytes | None:
            return b"hiresbytes" if src == str(local_video) else None

        with patch("src.services.media.hires_refiner.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames, local_source=source)

        assert result == 2
        mock_stream.assert_not_awaited()
        mock_download.assert_not_awaited()
        for frame in frames:
            assert Path(frame["path"]).read_bytes() == b"hiresbytes"
        # The refiner does not own the source's file.
        assert local_video.exists()

    @patch("src.services.media.hires_refiner.get_video_stream_url", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.settings")
    async def test_unavailable_prefetch_keeps_lowres_without_seeking(
        self, mock_settings, mock_stream, tmp_path
    ):
        _configure(mock_settings)
        frames = [_frame(tmp_path, 1, 5.0)]

        async def failed_download() -> None:
            return None

        source = LocalHiresSource("dQw4w9WgXcQ", task=asyncio.create_task(failed_download()))

        result = await refine_selected_frames("dQw4w9WgXcQ", frames, local_source=source)

        assert result == 0
        mock_stream.assert_not_awaited()
        assert frames[0]["path"].endswith("scene_0001.jpg")

    @patch("src.services.media.local_video.download_video_720p", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.get_video_stream_url", new_callable=AsyncMock)
    @patch("src.services.media.hires_refiner.settings")
    async def test_proxy_without_prefetch_downloads_instead_of_seeking(
        self, mock_settings, mock_stream, mock_download, tmp_path, monkeypatch
    ):
        _configure(mock_settings)
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", "http://u-1:p@h:80")
        frames = [_frame(tmp_path, 1, 5.0)]
        local_dir = tmp_path / "vie-hires-download"
        local_dir.mkdir()
        local_video = local_dir / "dQw4w9WgXcQ.mp4"
        local_video.write_bytes(b"720p-video")
        mock_download.return_value = (local_video, str(local_dir))

        with patch(
            "src.services.media.hires_refiner.extract_frame",
            AsyncMock(return_value=b"hiresbytes"),
        ):
            result = await refine_selected_frames("dQw4w9WgXcQ", frames)

        assert result == 1
        mock_stream.assert_not_awaited()
        mock_download.assert_awaited_once_with("dQw4w9WgXcQ")
        assert not local_dir.exists()
