"""Tests for frame extraction service (yt-dlp + ffmpeg + S3 upload pipeline)."""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media import download_utils
from src.services.media.frame_extractor import (
    _MAX_TIMESTAMP_SECONDS,
    _MIN_FRAME_BYTES,
    _compute_frame_hash,
    _frame_s3_key,
    _resolve_timestamp,
    _spread_clustered_timestamps,
    _upload_frame,
    extract_frame,
    extract_frames_for_blocks,
)
from src.services.media.stream_url import (
    _STREAM_URL_CACHE_MAX,
    _STREAM_URL_TTL,
    _stream_url_cache,
    clear_stream_url_cache,
    get_video_stream_url,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear stream URL cache before each test."""
    clear_stream_url_cache()
    yield
    clear_stream_url_cache()


def _mock_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock asyncio subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestFrameS3Key:
    """Tests for _frame_s3_key helper."""

    def test_generates_correct_key(self):
        assert _frame_s3_key("dQw4w9WgXcQ", 30) == "videos/dQw4w9WgXcQ/frames/30.jpg"

    def test_generates_key_for_zero_timestamp(self):
        assert _frame_s3_key("abc12345678", 0) == "videos/abc12345678/frames/0.jpg"


class TestGetVideoStreamUrl:
    """Tests for get_video_stream_url."""

    @pytest.mark.asyncio
    async def test_proxy_travels_in_env_never_on_the_command_line(self, monkeypatch):
        proxy_url = "http://user:pass@203.0.113.7:8080"
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", proxy_url)
        proc = _mock_process(returncode=0, stdout=b"https://stream.example.com/video\n")
        spawn = AsyncMock(return_value=proc)

        with patch("src.services.media.stream_url.asyncio.create_subprocess_exec", spawn):
            await get_video_stream_url("dQw4w9WgXcQ")

        assert all(proxy_url not in call.args for call in spawn.await_args_list)
        assert all(call.kwargs["env"]["HTTPS_PROXY"] == proxy_url for call in spawn.await_args_list)

    @pytest.mark.asyncio
    async def test_returns_stream_url_on_success(self):
        proc = _mock_process(returncode=0, stdout=b"https://stream.example.com/video\n")

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.stream_url.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await get_video_stream_url("dQw4w9WgXcQ")

        assert result == "https://stream.example.com/video"

    @pytest.mark.asyncio
    async def test_returns_cached_url(self):
        _stream_url_cache["abc12345678"] = ("https://cached.url/video", time.monotonic())

        result = await get_video_stream_url("abc12345678")
        assert result == "https://cached.url/video"

    @pytest.mark.asyncio
    async def test_expired_cache_is_not_used(self):
        _stream_url_cache["abc12345678"] = (
            "https://old.url/video",
            time.monotonic() - _STREAM_URL_TTL - 1,
        )

        proc = _mock_process(returncode=0, stdout=b"https://new.url/video\n")

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.stream_url.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await get_video_stream_url("abc12345678")

        assert result == "https://new.url/video"

    @pytest.mark.asyncio
    async def test_rejects_invalid_youtube_id(self):
        result = await get_video_stream_url("too-short")
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_youtube_id_with_special_chars(self):
        result = await get_video_stream_url("abc123!@#$%")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_ytdlp_fails(self):
        proc = _mock_process(returncode=1, stdout=b"", stderr=b"Error")

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.stream_url.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            result = await get_video_stream_url("dQw4w9WgXcQ")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        proc = _mock_process(returncode=0)

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.stream_url.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            with patch(
                "src.services.media.stream_url.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                result = await get_video_stream_url("dQw4w9WgXcQ")

        assert result is None

    @pytest.mark.asyncio
    async def test_lru_eviction_at_capacity(self):
        """When cache is at capacity, oldest entry should be evicted (not all)."""
        now = time.monotonic()
        for i in range(_STREAM_URL_CACHE_MAX):
            vid_id = f"vid{i:08d}xx"  # 11-char IDs
            _stream_url_cache[vid_id] = (f"https://url/{i}", now + i)

        assert len(_stream_url_cache) == _STREAM_URL_CACHE_MAX

        proc = _mock_process(returncode=0, stdout=b"https://new-url.com/video\n")

        async def _create_subprocess(*args, **kwargs):
            return proc

        with patch(
            "src.services.media.stream_url.asyncio.create_subprocess_exec",
            side_effect=_create_subprocess,
        ):
            await get_video_stream_url("newvideo1234"[:11])

        # Oldest entry should have been evicted, not all
        assert len(_stream_url_cache) <= _STREAM_URL_CACHE_MAX
        assert "vid00000000xx" not in _stream_url_cache


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


class TestUploadFrame:
    """Tests for _upload_frame helper."""

    @pytest.mark.asyncio
    async def test_upload_success(self):
        with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
            mock_s3.put_bytes = AsyncMock()
            result = await _upload_frame("videos/abc/frames/30.jpg", b"jpeg-data")

        assert result == "videos/abc/frames/30.jpg"
        mock_s3.put_bytes.assert_awaited_once_with(
            "videos/abc/frames/30.jpg", b"jpeg-data", content_type="image/jpeg"
        )

    @pytest.mark.asyncio
    async def test_upload_failure_returns_none(self):
        with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
            mock_s3.put_bytes = AsyncMock(side_effect=Exception("S3 error"))
            result = await _upload_frame("videos/abc/frames/30.jpg", b"jpeg-data")

        assert result is None


class TestExtractFramesForBlocks:
    """Tests for extract_frames_for_blocks (full S3 pipeline)."""

    @pytest.fixture(autouse=True)
    def _mock_brightness(self):
        """Mock is_mostly_black so fake-jpeg bytes don't fail PIL parsing."""
        with patch("src.services.media.frame_extractor.is_mostly_black", return_value=False):
            yield

    @pytest.mark.asyncio
    async def test_returns_unchanged_when_disabled(self):
        content = [{"type": "visual", "timestamp": 30}]
        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = False
            result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)
        assert result is content

    @pytest.mark.asyncio
    async def test_returns_unchanged_for_invalid_youtube_id(self):
        content = [{"type": "visual", "timestamp": 30}]
        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            result = await extract_frames_for_blocks("bad", content)
        assert result is content

    @pytest.mark.asyncio
    async def test_returns_unchanged_when_no_visual_blocks(self):
        content = [
            {"type": "paragraph", "text": "Hello"},
            {"type": "bullets", "items": ["a", "b"]},
        ]
        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)
        assert result is content

    @pytest.mark.asyncio
    async def test_skips_visual_blocks_with_existing_imageurl(self):
        content = [
            {"type": "visual", "timestamp": 30, "imageUrl": "https://existing.url/img.jpg"},
        ]
        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)
        assert result is content

    @pytest.mark.asyncio
    async def test_skips_visual_blocks_without_timestamp_and_no_chapter_range(self):
        """Visual block with no timestamp AND no chapter range is skipped."""
        content = [
            {"type": "visual", "description": "Some visual"},
        ]

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    return_value=b"fake-jpeg",
                ) as mock_extract:
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        # No chapter range provided, so block is skipped
        mock_extract.assert_not_called()
        assert "imageUrl" not in result[0]

    @pytest.mark.asyncio
    async def test_estimates_midpoint_when_timestamp_missing_with_chapter_range(self):
        """Visual block with no timestamp uses chapter midpoint when range is provided."""
        content = [
            {"type": "visual", "description": "A diagram"},
        ]

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    return_value=b"fake-jpeg",
                ) as mock_extract:
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/url"
                        result = await extract_frames_for_blocks(
                            "dQw4w9WgXcQ",
                            content,
                            chapter_start=100,
                            chapter_end=200,
                        )

        # Should use midpoint (150) as timestamp
        mock_extract.assert_called_once_with("https://stream.url", 150)
        assert result[0]["s3_key"] == "videos/dQw4w9WgXcQ/frames/150.jpg"
        assert result[0]["imageUrl"] == "https://signed/url"

    @pytest.mark.asyncio
    async def test_populates_imageurl_and_s3_key_on_success(self):
        content = [
            {"type": "paragraph", "text": "Hello"},
            {"type": "visual", "timestamp": 30, "description": "A diagram"},
        ]

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url/video",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    return_value=b"fake-jpeg-data",
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = (
                            "https://s3.amazonaws.com/signed/url"
                        )
                        result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        assert result[0] == content[0]  # paragraph unchanged
        assert result[1]["s3_key"] == "videos/dQw4w9WgXcQ/frames/30.jpg"
        assert result[1]["imageUrl"] == "https://s3.amazonaws.com/signed/url"
        assert result[1]["description"] == "A diagram"  # original fields preserved

    @pytest.mark.asyncio
    async def test_returns_unchanged_when_no_stream_url(self):
        content = [
            {"type": "visual", "timestamp": 30},
        ]

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value=None,
            ):
                result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        # No imageUrl added since stream URL failed
        assert "imageUrl" not in result[0]

    @pytest.mark.asyncio
    async def test_partial_failure_still_populates_successful_blocks(self):
        content = [
            {"type": "visual", "timestamp": 10},
            {"type": "visual", "timestamp": 20},
            {"type": "visual", "timestamp": 30},
        ]

        async def mock_extract(url, ts):
            if ts == 20:
                return None  # Fail for timestamp 20
            return b"fake-jpeg"

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    side_effect=mock_extract,
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/url"
                        result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        assert "s3_key" in result[0]  # ts=10 succeeded
        assert "imageUrl" not in result[1]  # ts=20 failed
        assert "s3_key" in result[2]  # ts=30 succeeded

    @pytest.mark.asyncio
    async def test_s3_cache_hit_skips_extraction(self):
        """When frame already exists in S3, extraction is skipped."""
        content = [
            {"type": "visual", "timestamp": 30},
        ]

        extract_called = False

        async def mock_extract(url, ts):
            nonlocal extract_called
            extract_called = True
            return b"should-not-be-called"

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    side_effect=mock_extract,
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=True)  # Cache hit
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/cached"
                        result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        assert not extract_called  # Extraction should have been skipped
        assert result[0]["s3_key"] == "videos/dQw4w9WgXcQ/frames/30.jpg"
        assert result[0]["imageUrl"] == "https://signed/cached"
        mock_s3.put_bytes.assert_not_awaited()  # No upload needed

    @pytest.mark.asyncio
    async def test_does_not_mutate_input(self):
        content = [
            {"type": "visual", "timestamp": 30},
        ]
        original_block = dict(content[0])

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    return_value=b"fake-jpeg",
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/url"
                        result = await extract_frames_for_blocks("dQw4w9WgXcQ", content)

        # Original content should not be mutated
        assert content[0] == original_block
        assert "imageUrl" not in content[0]
        assert "s3_key" not in content[0]
        # But result should have both
        assert "imageUrl" in result[0]
        assert "s3_key" in result[0]


class TestResolveTimestamp:
    """Tests for _resolve_timestamp with chapter-range clamping."""

    def test_clamps_to_chapter_end(self):
        """Timestamp past chapter end should be clamped to chapter_end."""
        result = _resolve_timestamp(
            {"timestamp": 532},
            video_duration=600,
            chapter_start=209,
            chapter_end=448,
        )
        assert result == 448

    def test_clamps_to_chapter_start(self):
        """Timestamp before chapter start should be clamped to chapter_start."""
        result = _resolve_timestamp(
            {"timestamp": 50},
            video_duration=600,
            chapter_start=100,
            chapter_end=200,
        )
        assert result == 100

    def test_within_range_unchanged(self):
        """Timestamp within chapter range should pass through unchanged."""
        result = _resolve_timestamp(
            {"timestamp": 150},
            video_duration=600,
            chapter_start=100,
            chapter_end=200,
        )
        assert result == 150

    def test_chapter_clamp_before_video_duration(self):
        """Chapter range clamping should take priority over video_duration clamping."""
        # ts=600 is beyond both chapter_end=400 and video_duration=500
        # Chapter clamp should fire first: 600 → 400
        # Then video_duration check: 400 < 500 → no change
        result = _resolve_timestamp(
            {"timestamp": 600},
            video_duration=500,
            chapter_start=100,
            chapter_end=400,
        )
        assert result == 400

    def test_no_chapter_range_falls_through_to_video_duration(self):
        """Without chapter range, only video_duration clamping applies."""
        result = _resolve_timestamp(
            {"timestamp": 600},
            video_duration=500,
            chapter_start=None,
            chapter_end=None,
        )
        assert result == 495  # max(500 - 5, 0)

    def test_missing_timestamp_with_chapter_range_uses_midpoint(self):
        """Missing timestamp with chapter range should use midpoint."""
        result = _resolve_timestamp(
            {},
            video_duration=600,
            chapter_start=100,
            chapter_end=200,
        )
        assert result == 150

    def test_missing_timestamp_without_chapter_range_returns_none(self):
        """Missing timestamp without chapter range should return None."""
        result = _resolve_timestamp(
            {},
            video_duration=600,
            chapter_start=None,
            chapter_end=None,
        )
        assert result is None

    def test_at_chapter_boundary_start(self):
        """Timestamp exactly at chapter_start should be unchanged."""
        result = _resolve_timestamp(
            {"timestamp": 100},
            video_duration=600,
            chapter_start=100,
            chapter_end=200,
        )
        assert result == 100

    def test_at_chapter_boundary_end(self):
        """Timestamp exactly at chapter_end should be unchanged."""
        result = _resolve_timestamp(
            {"timestamp": 200},
            video_duration=600,
            chapter_start=100,
            chapter_end=200,
        )
        assert result == 200


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


class TestClearStreamUrlCache:
    """Tests for clear_stream_url_cache."""

    def test_clears_all_entries(self):
        _stream_url_cache["abc12345678"] = ("https://url", time.monotonic())
        _stream_url_cache["def12345678"] = ("https://url2", time.monotonic())

        clear_stream_url_cache()

        assert len(_stream_url_cache) == 0


class TestSpreadClusteredTimestamps:
    """Tests for _spread_clustered_timestamps."""

    def test_single_frame_blocks_pass_through(self):
        """Single-frame jobs (frame_idx=None) should not be modified."""
        jobs = [(0, None, 100), (1, None, 105), (2, None, 110)]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=0, chapter_end=300, min_spacing=20
        )
        assert result == jobs

    def test_well_spaced_multi_frame_not_modified(self):
        """Multi-frame block with timestamps already spaced beyond min_spacing should pass through."""
        jobs = [(0, 0, 100), (0, 1, 150), (0, 2, 200)]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=0, chapter_end=300, min_spacing=20
        )
        assert result == jobs

    def test_clustered_timestamps_get_spread(self):
        """Multi-frame block with clustered timestamps should be redistributed."""
        # 3 frames at 100, 103, 106 — spread is 6, required is 40 (20*2)
        jobs = [(0, 0, 100), (0, 1, 103), (0, 2, 106)]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=0, chapter_end=300, min_spacing=20
        )

        # Timestamps should be spread across chapter range with 10% inset (30-270)
        new_ts = [r[2] for r in result]
        assert new_ts[0] == 30  # range_start = 0 + 30
        assert new_ts[1] == 150  # midpoint
        assert new_ts[2] == 270  # range_end = 300 - 30

    def test_no_chapter_range_returns_unchanged(self):
        """Without chapter start/end, jobs should pass through unchanged."""
        jobs = [(0, 0, 100), (0, 1, 103)]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=None, chapter_end=None, min_spacing=20
        )
        assert result == jobs

    def test_mixed_single_and_multi_frame(self):
        """Single-frame jobs should be untouched while multi-frame gets spread."""
        jobs = [
            (0, None, 50),  # single-frame block 0
            (1, 0, 100),  # multi-frame block 1, frame 0
            (1, 1, 103),  # multi-frame block 1, frame 1
        ]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=0, chapter_end=200, min_spacing=20
        )

        # Single-frame untouched
        assert result[0] == (0, None, 50)
        # Multi-frame should be spread (inset: 20, range: 20-180)
        assert result[1][2] == 20  # range_start
        assert result[2][2] == 180  # range_end

    def test_multiple_multi_frame_blocks(self):
        """Each multi-frame block should be spread independently."""
        jobs = [
            (0, 0, 100),
            (0, 1, 102),  # block 0: clustered
            (1, 0, 200),
            (1, 1, 250),  # block 1: well-spaced (50 >= 20)
        ]
        result = _spread_clustered_timestamps(
            jobs, chapter_start=0, chapter_end=300, min_spacing=20
        )

        # Block 0 should be spread
        assert result[0][2] != 100 or result[1][2] != 102
        # Block 1 should be unchanged
        assert result[2] == (1, 0, 200)
        assert result[3] == (1, 1, 250)

    def test_tiny_chapter_range_falls_back(self):
        """When inset makes range invalid, should fall back to full chapter range."""
        # Chapter range is only 10s, so 10% inset (1s each side) → 1-9
        jobs = [(0, 0, 5), (0, 1, 6)]
        result = _spread_clustered_timestamps(jobs, chapter_start=0, chapter_end=10, min_spacing=20)

        new_ts = [r[2] for r in result]
        # With 10% inset: range_start=1, range_end=9 → both valid
        assert new_ts[0] == 1
        assert new_ts[1] == 9


class TestGalleryCollapse:
    """Tests for gallery-to-single conversion in extract_frames_for_blocks."""

    @pytest.fixture(autouse=True)
    def _mock_brightness(self):
        """Mock is_mostly_black so fake-jpeg bytes don't fail PIL parsing."""
        with patch("src.services.media.frame_extractor.is_mostly_black", return_value=False):
            yield

    @pytest.mark.asyncio
    async def test_gallery_with_one_surviving_frame_becomes_screenshot(self):
        """A slideshow where only 1 frame gets an imageUrl should collapse to screenshot."""
        content = [
            {
                "type": "visual",
                "variant": "slideshow",
                "timestamp": 100,
                "frames": [
                    {"timestamp": 100, "caption": "Frame 1"},
                    {"timestamp": 105, "caption": "Frame 2"},
                    {"timestamp": 110, "caption": "Frame 3"},
                ],
            },
        ]

        call_count = 0

        async def mock_extract(url, ts):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"fake-jpeg-data-unique"
            return None  # frames 2,3 fail

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            mock_settings.FRAME_MIN_SPACING_SECONDS = 20
            mock_settings.FRAME_WITHIN_BLOCK_DEDUP_THRESHOLD = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    side_effect=mock_extract,
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/url"
                        result = await extract_frames_for_blocks(
                            "dQw4w9WgXcQ",
                            content,
                            chapter_start=0,
                            chapter_end=300,
                        )

        block = result[0]
        assert block["variant"] == "screenshot"
        assert "frames" not in block
        assert block["imageUrl"] == "https://signed/url"

    @pytest.mark.asyncio
    async def test_gallery_with_zero_frames_collapses_without_url(self):
        """A gallery where all frames fail should remove frames[] and change variant."""
        content = [
            {
                "type": "visual",
                "variant": "gallery",
                "timestamp": 100,
                "frames": [
                    {"timestamp": 100, "caption": "Frame 1"},
                    {"timestamp": 120, "caption": "Frame 2"},
                ],
            },
        ]

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            mock_settings.FRAME_MIN_SPACING_SECONDS = 20
            mock_settings.FRAME_WITHIN_BLOCK_DEDUP_THRESHOLD = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    return_value=None,  # all frames fail
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        result = await extract_frames_for_blocks(
                            "dQw4w9WgXcQ",
                            content,
                            chapter_start=0,
                            chapter_end=300,
                        )

        block = result[0]
        assert block["variant"] == "screenshot"
        assert "frames" not in block

    @pytest.mark.asyncio
    async def test_gallery_with_two_frames_preserved(self):
        """A gallery where 2+ frames succeed should keep its frames[] array."""
        content = [
            {
                "type": "visual",
                "variant": "gallery",
                "timestamp": 100,
                "frames": [
                    {"timestamp": 100, "caption": "Frame 1"},
                    {"timestamp": 200, "caption": "Frame 2"},
                ],
            },
        ]

        frame_data = {100: b"unique-frame-1-xxxxxx", 200: b"unique-frame-2-yyyyyy"}

        async def mock_extract(url, ts):
            return frame_data.get(ts, None)

        with patch("src.services.media.frame_extractor.settings") as mock_settings:
            mock_settings.FRAME_EXTRACTION_ENABLED = True
            mock_settings.MAX_FRAMES_PER_VISUAL = 6
            mock_settings.MAX_FRAMES_PER_CHAPTER = 12
            mock_settings.FRAME_MIN_SPACING_SECONDS = 20
            mock_settings.FRAME_WITHIN_BLOCK_DEDUP_THRESHOLD = 12

            with patch(
                "src.services.media.frame_extractor.get_video_stream_url",
                return_value="https://stream.url",
            ):
                with patch(
                    "src.services.media.frame_extractor.extract_frame",
                    side_effect=mock_extract,
                ):
                    with patch("src.services.media.frame_extractor.s3_client") as mock_s3:
                        mock_s3.exists = AsyncMock(return_value=False)
                        mock_s3.put_bytes = AsyncMock()
                        mock_s3.generate_presigned_url.return_value = "https://signed/url"
                        # Disable image dedup to let both frames pass through
                        with patch(
                            "src.services.media.frame_extractor._compute_frame_hash",
                            return_value=None,
                        ):
                            result = await extract_frames_for_blocks(
                                "dQw4w9WgXcQ",
                                content,
                                chapter_start=0,
                                chapter_end=300,
                            )

        block = result[0]
        assert block["variant"] == "gallery"
        assert "frames" in block
        assert len([f for f in block["frames"] if f.get("imageUrl")]) == 2


class TestComputeFrameHash:
    """Tests for _compute_frame_hash helper."""

    def test_returns_int_for_valid_jpeg(self):
        from io import BytesIO

        from PIL import Image

        img = Image.new("RGB", (64, 64), (128, 128, 128))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        result = _compute_frame_hash(buf.getvalue())
        assert isinstance(result, int)

    def test_returns_none_on_invalid_input(self):
        result = _compute_frame_hash(b"not-an-image")
        assert result is None
