"""Tests for the one-shot local 720p download fallback (media/local_video.py).

The leak path matters most: an outer budget (moment_frame_fill's fallback
wait_for, a client disconnect) cancelling mid-download must kill yt-dlp and
remove the partial temp dir — a long-lived worker otherwise accumulates
orphan processes and /tmp/vie-hires-* directories.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media import local_video

VIDEO_ID = "dQw4w9WgXcQ"


def _fake_proc(returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = None

    async def _communicate() -> tuple[bytes, bytes]:
        proc.returncode = returncode
        return b"", stderr

    proc.communicate = AsyncMock(side_effect=_communicate)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


async def _hang() -> tuple[bytes, bytes]:
    await asyncio.sleep(10)
    return b"", b""


def _capture_temp_dirs(monkeypatch, tmp_path: Path) -> list[str]:
    """Route mkdtemp under tmp_path and record every created dir."""
    created: list[str] = []
    real_mkdtemp = local_video.tempfile.mkdtemp

    def _mkdtemp(prefix: str) -> str:
        path = real_mkdtemp(prefix=prefix, dir=tmp_path)
        created.append(path)
        return path

    monkeypatch.setattr(local_video.tempfile, "mkdtemp", _mkdtemp)
    return created


async def test_invalid_youtube_id_returns_none_without_spawning():
    with patch.object(local_video.asyncio, "create_subprocess_exec", AsyncMock()) as spawn:
        assert await local_video.download_video_720p("not valid!") is None
    spawn.assert_not_awaited()


async def test_success_returns_path_and_temp_dir(monkeypatch, tmp_path):
    created = _capture_temp_dirs(monkeypatch, tmp_path)
    proc = _fake_proc(returncode=0)

    async def _spawn(*args, **_kwargs):
        # yt-dlp writes the output file named by "-o <path>"
        out = Path(args[args.index("-o") + 1])
        out.write_bytes(b"mp4")
        return proc

    with patch.object(local_video.asyncio, "create_subprocess_exec", side_effect=_spawn):
        result = await local_video.download_video_720p(VIDEO_ID)

    assert result is not None
    video_path, temp_dir = result
    assert video_path.exists()
    assert temp_dir == created[0]
    local_video.cleanup_local_video(temp_dir)


def _client_arg(call) -> str:
    argv = call.args
    return argv[argv.index("--extractor-args") + 1]


def _spawn_writing_output(outcomes: list[int]):
    """create_subprocess_exec stand-in: one proc per call, rc from ``outcomes``;
    a zero rc writes the "-o" file like yt-dlp would."""
    calls = iter(outcomes)

    async def _spawn(*args, **_kwargs):
        rc = next(calls)
        if rc == 0:
            Path(args[args.index("-o") + 1]).write_bytes(b"mp4")
        return _fake_proc(returncode=rc)

    return _spawn


async def test_player_client_args_are_injected():
    proc = _fake_proc(returncode=1)
    with (
        patch.object(
            local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ) as spawn,
        patch("src.services.media.download_utils.settings") as settings,
    ):
        settings.YTDLP_PLAYER_CLIENTS = "android"
        settings.YTDLP_HIRES_PLAYER_CLIENTS = "android"
        await local_video.download_video_720p(VIDEO_ID)
    assert _client_arg(spawn.await_args) == "youtube:player_client=android"


class TestHiresClients:
    """The 720p download uses the hi-res clients (android alone caps at 360p),
    falling back once to the pass-1 clients."""

    @pytest.fixture
    def clients(self):
        with patch("src.services.media.download_utils.settings") as settings:
            settings.YOUTUBE_PROXY_URL = None
            settings.YTDLP_PLAYER_CLIENTS = "android"
            settings.YTDLP_HIRES_PLAYER_CLIENTS = "web_embedded,android"
            yield settings

    async def test_should_download_with_hires_clients_first(self, clients, tmp_path, monkeypatch):
        _capture_temp_dirs(monkeypatch, tmp_path)
        spawn = AsyncMock(side_effect=_spawn_writing_output([0]))
        with patch.object(local_video.asyncio, "create_subprocess_exec", spawn):
            result = await local_video.download_video_720p(VIDEO_ID)
        assert result is not None
        assert [_client_arg(c) for c in spawn.await_args_list] == [
            "youtube:player_client=web_embedded,android"
        ]

    async def test_should_retry_with_pass1_clients_when_hires_fails(
        self, clients, tmp_path, monkeypatch
    ):
        _capture_temp_dirs(monkeypatch, tmp_path)
        spawn = AsyncMock(side_effect=_spawn_writing_output([1, 0]))
        with patch.object(local_video.asyncio, "create_subprocess_exec", spawn):
            result = await local_video.download_video_720p(VIDEO_ID)
        assert result is not None
        assert [_client_arg(c) for c in spawn.await_args_list] == [
            "youtube:player_client=web_embedded,android",
            "youtube:player_client=android",
        ]

    async def test_should_make_one_attempt_when_both_lists_match(
        self, clients, tmp_path, monkeypatch
    ):
        clients.YTDLP_HIRES_PLAYER_CLIENTS = "android"
        _capture_temp_dirs(monkeypatch, tmp_path)
        spawn = AsyncMock(side_effect=_spawn_writing_output([1]))
        with patch.object(local_video.asyncio, "create_subprocess_exec", spawn):
            assert await local_video.download_video_720p(VIDEO_ID) is None
        assert spawn.await_count == 1

    async def test_should_clear_a_partial_file_before_the_retry(
        self, clients, tmp_path, monkeypatch
    ):
        created = _capture_temp_dirs(monkeypatch, tmp_path)
        seen_on_retry: list[list[str]] = []
        calls = iter([1, 0])

        async def _spawn(*args, **_kwargs):
            out = Path(args[args.index("-o") + 1])
            rc = next(calls)
            if rc == 1:
                out.with_suffix(".mp4.part").write_bytes(b"half")
            else:
                seen_on_retry.append(sorted(p.name for p in out.parent.iterdir()))
                out.write_bytes(b"mp4")
            return _fake_proc(returncode=rc)

        with patch.object(local_video.asyncio, "create_subprocess_exec", side_effect=_spawn):
            result = await local_video.download_video_720p(VIDEO_ID)
        assert result is not None
        assert seen_on_retry == [[]]
        local_video.cleanup_local_video(created[0])


async def test_proxy_travels_in_env_never_on_the_command_line():
    proxy_url = "http://user:pass@203.0.113.7:8080"
    proc = _fake_proc(returncode=1)
    with (
        patch.object(
            local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ) as spawn,
        patch("src.services.media.download_utils.settings") as settings,
    ):
        settings.YTDLP_PLAYER_CLIENTS = "android"
        settings.YOUTUBE_PROXY_URL = proxy_url
        await local_video.download_video_720p(VIDEO_ID)
    assert proxy_url not in spawn.await_args.args
    assert spawn.await_args.kwargs["env"]["HTTPS_PROXY"] == proxy_url


async def test_nonzero_exit_cleans_temp_dir(monkeypatch, tmp_path):
    created = _capture_temp_dirs(monkeypatch, tmp_path)
    proc = _fake_proc(returncode=1, stderr=b"HTTP Error 403")
    with patch.object(local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        assert await local_video.download_video_720p(VIDEO_ID) is None
    assert not Path(created[0]).exists()


async def test_timeout_kills_process_and_cleans_temp_dir(monkeypatch, tmp_path):
    created = _capture_temp_dirs(monkeypatch, tmp_path)
    proc = _fake_proc()
    proc.communicate = AsyncMock(side_effect=_hang)
    with patch.object(local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        assert await local_video.download_video_720p(VIDEO_ID, timeout=0.01) is None
    proc.kill.assert_called_once()
    assert not Path(created[0]).exists()


async def test_missing_binary_cleans_temp_dir(monkeypatch, tmp_path):
    created = _capture_temp_dirs(monkeypatch, tmp_path)
    with patch.object(
        local_video.asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)
    ):
        assert await local_video.download_video_720p(VIDEO_ID) is None
    assert not Path(created[0]).exists()


async def test_outer_cancellation_kills_process_cleans_and_reraises(monkeypatch, tmp_path):
    """Regression: an outer wait_for expiring mid-download left yt-dlp running
    and the partial mp4 on disk (CancelledError bypassed both cleanups)."""
    created = _capture_temp_dirs(monkeypatch, tmp_path)
    proc = _fake_proc()
    proc.communicate = AsyncMock(side_effect=_hang)
    with patch.object(local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(local_video.download_video_720p(VIDEO_ID), timeout=0.01)
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()
    assert not Path(created[0]).exists()


def test_cleanup_tolerates_missing_dir(tmp_path):
    local_video.cleanup_local_video(str(tmp_path / "never-created"))
