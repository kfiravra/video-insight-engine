"""Tests for the exact-timestamp frame fill (moment_track image guarantee).

Every seek reads the run's one local 720p file (``LocalHiresSource``); the fill
starts that download only when nothing did (manifest cache hit), never a second one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline.assembly import moment_frame_fill as mff

LOCAL_VIDEO = Path("/tmp/vie-hires-yt123/yt123.mp4")


def _moment_tab(items: list[dict]) -> dict:
    return {"component": "moment_track", "props": {"items": items}}


def _patched_s3(exists: bool = False):
    s3 = MagicMock()
    s3.is_available = MagicMock(return_value=True)
    s3.exists = AsyncMock(return_value=exists)
    s3.put_bytes = AsyncMock(return_value=None)
    s3.generate_presigned_url = MagicMock(side_effect=lambda key: f"https://s3/{key}?sig")
    return s3


def _handle(path: Path | None = LOCAL_VIDEO, *, started: bool = True) -> MagicMock:
    """A LocalHiresSource stand-in: ``started`` = scene extraction already began the download."""
    handle = MagicMock()
    handle.started = started
    handle.path = AsyncMock(return_value=path)
    return handle


async def test_should_fill_only_frameless_items_from_the_run_file():
    items = [
        {"label": "has", "seconds": 100, "thumbnailUrl": "existing"},
        {"label": "needs", "seconds": 200},
    ]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg-bytes")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert filled == 1
    mock_extract.assert_awaited_once_with(str(LOCAL_VIDEO), 200)
    assert items[1]["thumbnailUrl"] == "https://s3/videos/yt123/frames/200.jpg?sig"
    assert items[1]["s3Key"] == "videos/yt123/frames/200.jpg"
    assert items[0]["thumbnailUrl"] == "existing"


async def test_should_reuse_an_existing_s3_frame_without_extraction():
    items = [{"label": "needs", "seconds": 42}]
    s3 = _patched_s3(exists=True)
    with (
        patch.object(mff, "s3_client", s3),
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert filled == 1
    mock_extract.assert_not_awaited()
    s3.put_bytes.assert_not_awaited()
    assert items[0]["s3Key"] == "videos/yt123/frames/42.jpg"


async def test_should_dedupe_same_second_targets_within_a_tab():
    """Two frameless moments flooring to the same second would extract the
    identical frame — only the first claims it (no within-tab duplicates)."""
    items = [{"label": "a", "seconds": 100.2}, {"label": "b", "seconds": 100.9}]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert (filled, mock_extract.await_count) == (1, 1)
    assert items[0].get("thumbnailUrl") and not items[1].get("thumbnailUrl")


async def test_should_fill_a_single_target_when_the_file_is_already_downloading():
    items = [{"label": "one", "seconds": 10}]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert filled == 1


async def test_should_not_start_a_download_for_fewer_than_three_targets():
    items = [{"label": "a", "seconds": 100}, {"label": "b", "seconds": 200}]
    handle = _handle(started=False)
    with patch.object(mff, "s3_client", _patched_s3()):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", handle)

    assert (filled, handle.path.await_count) == (0, 0)


async def test_should_start_the_download_for_three_or_more_targets():
    """A manifest cache hit skipped scene extraction — the fill fetches the file itself."""
    items = [{"label": f"m{i}", "seconds": 100 + i * 60} for i in range(3)]
    handle = _handle(started=False)
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", handle)

    assert (filled, handle.path.await_count) == (3, 1)


async def test_should_be_a_clean_noop_when_the_download_failed():
    items = [{"label": "needs", "seconds": 10}]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle(None))

    assert filled == 0
    mock_extract.assert_not_awaited()
    assert "thumbnailUrl" not in items[0]


async def test_should_skip_everything_without_a_run_file():
    items = [{"label": "needs", "seconds": 10}]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock()) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", None)

    assert filled == 0
    mock_extract.assert_not_awaited()


async def test_should_leave_an_item_frameless_when_its_seek_fails():
    items = [{"label": "needs", "seconds": 10}]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=None)),
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert filled == 0
    assert "thumbnailUrl" not in items[0]


async def test_should_cap_the_extraction_count():
    items = [{"label": f"m{i}", "seconds": i * 30} for i in range(1, 16)]  # 15 targets
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")) as mock_extract,
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert (filled, mock_extract.await_count) == (mff._FILL_MAX_FRAMES, mff._FILL_MAX_FRAMES)


async def test_should_keep_partial_fills_when_the_budget_expires(monkeypatch):
    monkeypatch.setattr(mff, "_FILL_TIMEOUT", 0.05)
    items = [{"label": "fast", "seconds": 10}, {"label": "slow", "seconds": 20}]

    async def fake_extract(source: str, second: int) -> bytes:
        if second == 20:
            await asyncio.sleep(10)
        return b"jpeg"

    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(side_effect=fake_extract)),
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", _handle())

    assert filled == 1


async def test_should_skip_everything_when_s3_is_unavailable():
    items = [{"label": "needs", "seconds": 10}]
    s3 = _patched_s3()
    s3.is_available = MagicMock(return_value=False)
    handle = _handle()
    with patch.object(mff, "s3_client", s3):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", handle)

    assert (filled, handle.path.await_count) == (0, 0)


async def test_should_short_circuit_without_targets():
    tabs = [_moment_tab([{"label": "has", "seconds": 5, "thumbnailUrl": "u"}])]
    s3 = _patched_s3()
    with patch.object(mff, "s3_client", s3):
        filled = await mff.fill_moment_frames(tabs, "yt123", _handle())

    assert filled == 0
    s3.is_available.assert_not_called()


def _run_source(monkeypatch, tmp_path: Path, *, started: bool):
    """A real LocalHiresSource whose one yt-dlp download is faked."""
    from src.services.media import hires_prefetch

    local_dir = tmp_path / "vie-hires"
    local_dir.mkdir()
    video = local_dir / "yt123.mp4"
    video.write_bytes(b"720p")
    download = AsyncMock(return_value=(video, str(local_dir)))
    monkeypatch.setattr(hires_prefetch, "download_video_720p", download)
    source = hires_prefetch.LocalHiresSource("yt123")
    if started:
        source.start()
    return source, download


@pytest.mark.parametrize("started", [True, False])
async def test_should_download_the_run_file_at_most_once(started, monkeypatch, tmp_path):
    """The fill reads the run's file only — no second 720p download, ever."""
    source, download = _run_source(monkeypatch, tmp_path, started=started)
    items = [{"label": f"m{i}", "seconds": 100 + i * 60} for i in range(3)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        filled = await mff.fill_moment_frames([_moment_tab(items)], "yt123", source)

    assert (filled, download.await_count) == (3, 1)


async def test_should_leave_seek_time_when_it_starts_the_download_itself(monkeypatch, tmp_path):
    """Regression: a fill-started download ran under the 180 s download timeout,
    longer than the whole 150 s fill budget — it could fill nothing."""
    source, download = _run_source(monkeypatch, tmp_path, started=False)
    items = [{"label": f"m{i}", "seconds": 100 + i * 60} for i in range(3)]
    with (
        patch.object(mff, "s3_client", _patched_s3()),
        patch.object(mff, "extract_frame", AsyncMock(return_value=b"jpeg")),
    ):
        await mff.fill_moment_frames([_moment_tab(items)], "yt123", source)

    download.assert_awaited_once_with(
        "yt123", mff._FILL_TIMEOUT - mff._FILL_SEEK_RESERVE, purpose="prefetch"
    )
