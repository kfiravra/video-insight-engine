"""Tests for hi-res refinement of selected scene frames (seeks into the run's 720p file)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media.hires_refiner import refine_selected_frames

VIDEO_ID = "dQw4w9WgXcQ"
REFINER = "src.services.media.hires_refiner"


def _frame(tmp_path: Path, index: int, timestamp: float) -> dict:
    path = tmp_path / f"scene_{index:04d}.jpg"
    path.write_bytes(b"lowres")
    return {"index": index, "path": str(path), "timestamp": timestamp}


def _handle(path: Path | None) -> MagicMock:
    """A LocalHiresSource stand-in whose download resolves to ``path``."""
    handle = MagicMock()
    handle.path = AsyncMock(return_value=path)
    return handle


@pytest.fixture
def local_video(tmp_path: Path) -> Path:
    video = tmp_path / f"{VIDEO_ID}.mp4"
    video.write_bytes(b"720p-video")
    return video


@pytest.fixture
def hires_settings():
    with patch(f"{REFINER}.settings") as mock_settings:
        mock_settings.SCENE_HIRES_ENABLED = True
        mock_settings.SCENE_HIRES_CONCURRENCY = 4
        mock_settings.SCENE_HIRES_FALLBACK_TIMEOUT = 5.0
        yield mock_settings


class TestRefineSelectedFrames:
    async def test_should_seek_every_frame_in_the_local_file(
        self, hires_settings, local_video, tmp_path
    ):
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]

        async def fake_extract(source: str, ts: int) -> bytes | None:
            return b"hiresbytes" if source == str(local_video) else None

        with patch(f"{REFINER}.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames(VIDEO_ID, frames, _handle(local_video))

        assert (result, [Path(f["path"]).read_bytes() for f in frames]) == (
            2,
            [b"hiresbytes", b"hiresbytes"],
        )

    async def test_should_leave_the_runs_file_on_disk(self, hires_settings, local_video, tmp_path):
        frames = [_frame(tmp_path, 1, 5.0)]

        with patch(f"{REFINER}.extract_frame", AsyncMock(return_value=b"hires")):
            await refine_selected_frames(VIDEO_ID, frames, _handle(local_video))

        assert local_video.exists()

    async def test_should_do_nothing_when_disabled(self, hires_settings, local_video, tmp_path):
        hires_settings.SCENE_HIRES_ENABLED = False
        handle = _handle(local_video)

        result = await refine_selected_frames(VIDEO_ID, [_frame(tmp_path, 1, 5.0)], handle)

        assert (result, handle.path.await_count) == (0, 0)

    async def test_should_keep_lowres_without_a_run_file(self, hires_settings, tmp_path):
        frames = [_frame(tmp_path, 1, 5.0)]

        result = await refine_selected_frames(VIDEO_ID, frames, None)

        assert (result, frames[0]["path"].endswith("scene_0001.jpg")) == (0, True)

    async def test_should_not_wait_for_the_download_without_seekable_frames(
        self, hires_settings, local_video, tmp_path
    ):
        """Frames without a parsed timestamp (0.0) cannot be seeked to."""
        handle = _handle(local_video)

        await refine_selected_frames(VIDEO_ID, [_frame(tmp_path, 1, 0.0)], handle)

        assert handle.path.await_count == 0

    async def test_should_keep_lowres_when_the_download_failed(self, hires_settings, tmp_path):
        frames = [_frame(tmp_path, 1, 5.0)]
        extract = AsyncMock(return_value=b"hires")

        with patch(f"{REFINER}.extract_frame", extract):
            result = await refine_selected_frames(VIDEO_ID, frames, _handle(None))

        assert (result, extract.await_count) == (0, 0)

    async def test_should_keep_the_original_path_of_a_failed_seek(
        self, hires_settings, local_video, tmp_path
    ):
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]

        async def fake_extract(source: str, ts: int) -> bytes | None:
            return b"hires" if ts == 5 else None

        with patch(f"{REFINER}.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames(VIDEO_ID, frames, _handle(local_video))

        assert (result, frames[1]["path"].endswith("scene_0002.jpg")) == (1, True)

    async def test_should_keep_partial_results_when_the_seek_budget_expires(
        self, hires_settings, local_video, tmp_path
    ):
        """Frames refined before the deadline survive; the rest stay low-res."""
        hires_settings.SCENE_HIRES_FALLBACK_TIMEOUT = 0.05
        frames = [_frame(tmp_path, 1, 5.0), _frame(tmp_path, 2, 12.0)]

        async def fake_extract(source: str, ts: int) -> bytes | None:
            if ts == 12:
                await asyncio.sleep(10)
            return b"hires"

        with patch(f"{REFINER}.extract_frame", side_effect=fake_extract):
            result = await refine_selected_frames(VIDEO_ID, frames, _handle(local_video))

        assert (result, frames[1]["path"].endswith("scene_0002.jpg")) == (1, True)
