"""Tests for single-frame ffmpeg extraction (frame_extractor)."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media.frame_extractor import (
    _MAX_TIMESTAMP_SECONDS,
    _MIN_FRAME_BYTES,
    extract_frame,
    frame_s3_key,
)


def _mock_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock asyncio subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestFrameS3Key:
    """Tests for frame_s3_key helper."""

    def test_generates_correct_key(self):
        assert frame_s3_key("dQw4w9WgXcQ", 30) == "videos/dQw4w9WgXcQ/frames/30.jpg"

    def test_generates_key_for_zero_timestamp(self):
        assert frame_s3_key("abc12345678", 0) == "videos/abc12345678/frames/0.jpg"


class TestExtractFrame:
    """Tests for extract_frame (temp file + bytes return)."""

    @pytest.mark.asyncio
    async def test_rejects_negative_timestamp(self):
        result = await extract_frame("https://url", -1)
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_timestamp_over_max(self):
        result = await extract_frame("https://url", _MAX_TIMESTAMP_SECONDS + 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bytes_on_ffmpeg_success(self, tmp_path):
        fake_jpeg = b"x" * (_MIN_FRAME_BYTES + 100)  # Must exceed min frame size

        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return b"", b""

        proc.communicate = _communicate

        async def _create_subprocess(*args, **kwargs):
            # Write fake data to the temp file path (last positional arg)
            output_path = args[-1]
            with open(output_path, "wb") as f:
                f.write(fake_jpeg)
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result == fake_jpeg

    @pytest.mark.asyncio
    async def test_returns_none_on_ffmpeg_failure(self):
        proc = _mock_process(returncode=1, stderr=b"ffmpeg error output")

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        proc = _mock_process(returncode=0)

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            with patch(
                "src.services.media.frame_extractor.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await extract_frame("https://url", 30)

        assert result is None

    @pytest.mark.asyncio
    async def test_should_kill_ffmpeg_and_reraise_when_an_outer_budget_cancels_it(self):
        """Regression: an outer cancel left ffmpeg running to write a stray jpg."""
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        temp_files_created: list[str] = []

        async def _hang() -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        async def _create_subprocess(*args, **kwargs):
            temp_files_created.append(args[-1])
            return proc

        proc.communicate = AsyncMock(side_effect=_hang)
        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(extract_frame("/tmp/v.mp4", 30), timeout=0.01)

        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
        assert not any(os.path.exists(path) for path in temp_files_created)

    @pytest.mark.asyncio
    async def test_returns_none_on_unexpected_exception(self):
        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=OSError("no ffmpeg"),
        ):
            result = await extract_frame("https://url", 30)

        assert result is None

    @pytest.mark.asyncio
    async def test_cleans_up_temp_file_on_success(self):
        fake_jpeg = b"x" * (_MIN_FRAME_BYTES + 100)  # Must exceed min frame size
        temp_files_created: list[str] = []

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def _create_subprocess(*args, **kwargs):
            output_path = args[-1]
            temp_files_created.append(output_path)
            with open(output_path, "wb") as f:
                f.write(fake_jpeg)
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result == fake_jpeg
        # Temp file should be cleaned up
        for path in temp_files_created:
            assert not os.path.exists(path)

    @pytest.mark.asyncio
    async def test_cleans_up_temp_file_on_failure(self):
        proc = _mock_process(returncode=1, stderr=b"error")
        temp_files_created: list[str] = []

        async def _create_subprocess(*args, **kwargs):
            output_path = args[-1]
            temp_files_created.append(output_path)
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result is None
        for path in temp_files_created:
            assert not os.path.exists(path)


class TestExtractFrameMinSize:
    """Tests for minimum file size check in extract_frame."""

    @pytest.mark.asyncio
    async def test_rejects_tiny_file(self):
        """Frame smaller than _MIN_FRAME_BYTES should be rejected."""
        tiny_data = b"x" * 100  # 100 bytes, well under 5KB

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def _create_subprocess(*args, **kwargs):
            output_path = args[-1]
            with open(output_path, "wb") as f:
                f.write(tiny_data)
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_large_enough_file(self):
        """Frame at or above _MIN_FRAME_BYTES should be accepted."""
        normal_data = b"x" * (_MIN_FRAME_BYTES + 1)

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def _create_subprocess(*args, **kwargs):
            output_path = args[-1]
            with open(output_path, "wb") as f:
                f.write(normal_data)
            return proc

        with patch(
            "src.services.media.frame_extractor.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await extract_frame("https://url", 30)

        assert result == normal_data
