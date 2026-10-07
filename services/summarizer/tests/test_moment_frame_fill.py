"""Tests for the exact-timestamp frame fill (moment_track image guarantee)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media import download_utils
from src.services.pipeline.assembly import moment_frame_fill as mff


def _moment_tab(items: list[dict]) -> dict:
    return {"component": "moment_track", "props": {"items": items}}


def _patched_s3(exists: bool = False):
    s3 = MagicMock()
    s3.is_available = MagicMock(return_value=True)
    s3.exists = AsyncMock(return_value=exists)
    s3.put_bytes = AsyncMock(return_value=None)
    s3.generate_presigned_url = MagicMock(side_effect=lambda key: f"https://s3/{key}?sig")
    return s3


@pytest.mark.asyncio
async def test_fills_only_frameless_items():
    items = [
        {"label": "has", "seconds": 100, "thumbnailUrl": "existing"},
        {"label": "needs", "seconds": 200},
    ]
    tabs = [_moment_tab(items)]
    s3 = _patched_s3()
    with (
        patch.object(mff, "s3_client", s3),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg-bytes")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 1
    mock_extract.assert_awaited_once_with("https://stream", 200)
    assert items[1]["thumbnailUrl"] == "https://s3/videos/yt123/frames/200.jpg?sig"
    assert items[1]["s3Key"] == "videos/yt123/frames/200.jpg"
    assert items[0]["thumbnailUrl"] == "existing"


@pytest.mark.asyncio
async def test_reuses_existing_s3_frame_without_extraction():
    items = [{"label": "needs", "seconds": 42}]
    tabs = [_moment_tab(items)]
    s3 = _patched_s3(exists=True)
    with (
        patch.object(mff, "s3_client", s3),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 1
    mock_extract.assert_not_awaited()
    s3.put_bytes.assert_not_awaited()
    assert items[0]["s3Key"] == "videos/yt123/frames/42.jpg"


@pytest.mark.asyncio
async def test_same_second_targets_dedupe_within_tab():
    """Two frameless moments flooring to the same second would extract the
    identical frame — only the first claims it (no within-tab duplicates)."""
    items = [
        {"label": "a", "seconds": 100.2},
        {"label": "b", "seconds": 100.9},
    ]
    tabs = [_moment_tab(items)]
    s3 = _patched_s3()
    with (
        patch.object(mff, "s3_client", s3),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 1
    assert mock_extract.await_count == 1
    assert items[0].get("thumbnailUrl")
    assert not items[1].get("thumbnailUrl")


@pytest.mark.asyncio
async def test_no_stream_url_is_a_clean_noop():
    items = [{"label": "needs", "seconds": 10}]
    tabs = [_moment_tab(items)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value=None)),
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    mock_extract.assert_not_awaited()
    assert "thumbnailUrl" not in items[0]


@pytest.mark.asyncio
async def test_extraction_failure_leaves_item_frameless():
    items = [{"label": "needs", "seconds": 10}]
    tabs = [_moment_tab(items)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(return_value=None)),
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    assert "thumbnailUrl" not in items[0]


@pytest.mark.asyncio
async def test_cap_limits_extraction_count():
    items = [{"label": f"m{i}", "seconds": i * 30} for i in range(1, 16)]  # 15 targets
    tabs = [_moment_tab(items)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == mff._FILL_MAX_FRAMES
    assert mock_extract.await_count == mff._FILL_MAX_FRAMES


@pytest.mark.asyncio
async def test_s3_unavailable_skips_everything():
    items = [{"label": "needs", "seconds": 10}]
    tabs = [_moment_tab(items)]
    s3 = _patched_s3()
    s3.is_available = MagicMock(return_value=False)
    with (
        patch.object(mff, "s3_client", s3),
        patch.object(mff, "get_video_stream_url", AsyncMock()) as mock_stream,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    mock_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_targets_short_circuits():
    tabs = [_moment_tab([{"label": "has", "seconds": 5, "thumbnailUrl": "u"}])]
    s3 = _patched_s3()
    with patch.object(mff, "s3_client", s3):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    s3.is_available.assert_not_called()


@pytest.mark.asyncio
async def test_cdn_403_with_three_targets_triggers_local_fallback(tmp_path):
    items = [{"label": f"m{i}", "seconds": 100 + i * 60} for i in range(3)]
    tabs = [_moment_tab(items)]
    local_dir = tmp_path / "dl"
    local_dir.mkdir()
    local_video = local_dir / "yt123.mp4"
    local_video.write_bytes(b"720p")

    async def fake_extract(source, sec):
        # URL seeks 403 (None); the local file works.
        return b"jpeg" if source == str(local_video) else None

    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(side_effect=fake_extract)),
        patch(
            "src.services.media.local_video.download_video_720p",
            AsyncMock(return_value=(local_video, str(local_dir))),
        ) as mock_download,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 3
    # The download gets the fallback budget minus the seek reserve so a slow
    # download exits cleanly instead of being cancelled at the outer deadline.
    mock_download.assert_awaited_once_with(
        "yt123",
        timeout=mff._FILL_FALLBACK_TIMEOUT - mff._FILL_FALLBACK_SEEK_RESERVE,
        purpose="moment_fill",
    )
    assert all(it.get("thumbnailUrl") for it in items)
    assert not local_dir.exists()  # temp dir cleaned up


@pytest.mark.asyncio
async def test_fewer_than_three_failures_skip_the_download_fallback():
    items = [{"label": "a", "seconds": 100}, {"label": "b", "seconds": 200}]
    tabs = [_moment_tab(items)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock(return_value="https://stream")),
        patch.object(mff, "extract_frame", AsyncMock(return_value=None)),
        patch("src.services.media.local_video.download_video_720p", AsyncMock()) as mock_download,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    mock_download.assert_not_awaited()
    assert not any(it.get("thumbnailUrl") for it in items)


@pytest.mark.asyncio
async def test_proxy_skips_the_stream_url_pass_and_fills_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", "http://u-1:p@h:80")
    items = [{"label": f"m{i}", "seconds": 100 + i * 60} for i in range(3)]
    tabs = [_moment_tab(items)]
    local_dir = tmp_path / "dl"
    local_dir.mkdir()
    local_video = local_dir / "yt123.mp4"
    local_video.write_bytes(b"720p")

    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock()) as mock_stream,
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")) as mock_extract,
        patch(
            "src.services.media.local_video.download_video_720p",
            AsyncMock(return_value=(local_video, str(local_dir))),
        ) as mock_download,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 3
    mock_stream.assert_not_awaited()
    mock_download.assert_awaited_once()
    assert all(call.args[0] == str(local_video) for call in mock_extract.await_args_list)
    assert not local_dir.exists()


@pytest.mark.asyncio
async def test_proxy_with_too_few_targets_downloads_nothing(monkeypatch):
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", "http://u-1:p@h:80")
    items = [{"label": "a", "seconds": 100}, {"label": "b", "seconds": 200}]
    tabs = [_moment_tab(items)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "get_video_stream_url", AsyncMock()) as mock_stream,
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
        patch("src.services.media.local_video.download_video_720p", AsyncMock()) as mock_download,
    ):
        filled = await mff.fill_moment_frames(tabs, "yt123")

    assert filled == 0
    mock_stream.assert_not_awaited()
    mock_extract.assert_not_awaited()
    mock_download.assert_not_awaited()
