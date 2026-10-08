"""Tests for the run's video downloads — 720p and low-res pass 1 (media/local_video.py).

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

from src.services.media import download_utils, local_video

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _fresh_exit_memory(monkeypatch):
    """Each test starts with no remembered proxy exit (it is per-process state)."""
    monkeypatch.setattr(download_utils, "_EXIT_MEMORY", download_utils._ExitMemory())


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
        settings.YOUTUBE_PROXY_URL = None
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
        settings.YOUTUBE_PROXY_EXIT_COUNT = 1
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


class TestDownloadVideoLowres:
    """Pass 1 — the worst-quality file scene detection reads, one attempt."""

    async def test_should_download_the_worst_rendition(self, monkeypatch, tmp_path):
        _capture_temp_dirs(monkeypatch, tmp_path)
        formats: list[str] = []

        async def _spawn(*args, **_kwargs):
            formats.append(args[args.index("-f") + 1])
            Path(args[args.index("-o") + 1]).write_bytes(b"mp4")
            return _fake_proc(returncode=0)

        with patch.object(local_video.asyncio, "create_subprocess_exec", side_effect=_spawn):
            result = await local_video.download_video_lowres(VIDEO_ID)

        assert result is not None and formats == ["worstvideo[ext=mp4]/worst[ext=mp4]/worst"]
        local_video.cleanup_local_video(result[1])

    async def test_should_remove_its_dir_when_yt_dlp_fails(self, monkeypatch, tmp_path):
        created = _capture_temp_dirs(monkeypatch, tmp_path)

        with patch.object(
            local_video.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(returncode=1, stderr=b"HTTP Error 403")),
        ):
            result = await local_video.download_video_lowres(VIDEO_ID)

        assert (result, Path(created[0]).exists()) == (None, False)

    async def test_should_kill_yt_dlp_and_clean_up_when_cancelled(self, monkeypatch, tmp_path):
        created = _capture_temp_dirs(monkeypatch, tmp_path)
        proc = _fake_proc()
        proc.communicate = AsyncMock(side_effect=_hang)

        with patch.object(
            local_video.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(local_video.download_video_lowres(VIDEO_ID), timeout=0.01)

        assert (proc.kill.called, Path(created[0]).exists()) == (True, False)


BOT_CHECK = (
    b"ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you\xe2\x80\x99re not a bot. "
    b"Use --cookies-from-browser or --cookies for the authentication."
)


def _exit(n: int) -> str:
    return f"http://user-{n}:pass@p.webshare.io:80"


class _FakeYtdlp:
    """create_subprocess_exec stand-in that answers per proxy exit (the env's
    HTTPS_PROXY) and records the exits tried, in order."""

    def __init__(self) -> None:
        self.stderr_by_exit: dict[str, bytes] = {}  # exit URL -> failing stderr
        self.tried: list[str] = []
        self.argvs: list[tuple[str, ...]] = []

    async def spawn(self, *args: str, env: dict[str, str], **_kwargs: object) -> MagicMock:
        proxy_url = env["HTTPS_PROXY"]
        self.tried.append(proxy_url)
        self.argvs.append(args)
        stderr = self.stderr_by_exit.get(proxy_url)
        if stderr is None:
            Path(args[args.index("-o") + 1]).write_bytes(b"mp4")
            return _fake_proc(returncode=0)
        return _fake_proc(returncode=1, stderr=stderr)


class TestProxyExitRotation:
    """YouTube's bot check (and a 429) blocks one proxy exit's IP, not the
    video — the download moves on to the next sticky exit."""

    @pytest.fixture
    def ytdlp(self, monkeypatch, tmp_path):
        _capture_temp_dirs(monkeypatch, tmp_path)
        fake = _FakeYtdlp()
        with (
            patch("src.services.media.download_utils.settings") as settings,
            patch.object(local_video.asyncio, "create_subprocess_exec", side_effect=fake.spawn),
        ):
            settings.YOUTUBE_PROXY_URL = _exit(1)
            settings.YOUTUBE_PROXY_EXIT_COUNT = 3
            settings.YTDLP_PLAYER_CLIENTS = "android"
            settings.YTDLP_HIRES_PLAYER_CLIENTS = "android"
            yield fake

    async def test_should_download_on_the_next_exit_when_the_first_gets_the_bot_check(self, ytdlp):
        ytdlp.stderr_by_exit[_exit(1)] = BOT_CHECK

        result = await local_video.download_video_lowres(VIDEO_ID)

        assert result is not None and ytdlp.tried == [_exit(1), _exit(2)]
        local_video.cleanup_local_video(result[1])

    async def test_should_rotate_the_720p_download_on_the_bot_check(self, ytdlp):
        ytdlp.stderr_by_exit[_exit(1)] = BOT_CHECK

        result = await local_video.download_video_720p(VIDEO_ID)

        assert result is not None and ytdlp.tried == [_exit(1), _exit(2)]
        local_video.cleanup_local_video(result[1])

    async def test_should_rotate_on_a_429(self, ytdlp):
        ytdlp.stderr_by_exit[_exit(1)] = b"ERROR: HTTP Error 429: Too Many Requests"

        result = await local_video.download_video_lowres(VIDEO_ID)

        assert result is not None and ytdlp.tried == [_exit(1), _exit(2)]
        local_video.cleanup_local_video(result[1])

    async def test_should_fail_after_every_exit_gets_the_bot_check(self, ytdlp):
        for n in (1, 2, 3):
            ytdlp.stderr_by_exit[_exit(n)] = BOT_CHECK

        assert await local_video.download_video_lowres(VIDEO_ID) is None
        assert ytdlp.tried == [_exit(1), _exit(2), _exit(3)]

    @pytest.mark.parametrize(
        "stderr",
        [
            b"ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm your age. "
            b"This video may be inappropriate for some users.",
            b"ERROR: [youtube] dQw4w9WgXcQ: Private video. Sign in if you've been granted access",
            b"ERROR: [youtube] dQw4w9WgXcQ: Video unavailable",
            b"ERROR: unable to download video data: HTTP Error 404: Not Found",
        ],
    )
    async def test_should_not_rotate_on_an_error_every_exit_would_share(self, ytdlp, stderr):
        ytdlp.stderr_by_exit[_exit(1)] = stderr

        assert await local_video.download_video_lowres(VIDEO_ID) is None
        assert ytdlp.tried == [_exit(1)]

    async def test_should_keep_every_exit_off_the_command_line(self, ytdlp):
        ytdlp.stderr_by_exit[_exit(1)] = BOT_CHECK

        result = await local_video.download_video_lowres(VIDEO_ID)

        assert result is not None
        assert not any("pass@" in arg for argv in ytdlp.argvs for arg in argv)
        local_video.cleanup_local_video(result[1])

    async def test_should_start_the_next_download_from_the_exit_that_worked(self, ytdlp):
        ytdlp.stderr_by_exit[_exit(1)] = BOT_CHECK
        first = await local_video.download_video_lowres(VIDEO_ID)
        ytdlp.tried.clear()

        second = await local_video.download_video_lowres(VIDEO_ID)

        assert first is not None and second is not None and ytdlp.tried == [_exit(2)]
        for result in (first, second):
            local_video.cleanup_local_video(result[1])

    async def test_should_log_the_exit_that_worked_without_credentials(self, ytdlp, caplog):
        ytdlp.stderr_by_exit[_exit(1)] = BOT_CHECK

        with caplog.at_level("INFO"):
            result = await local_video.download_video_lowres(VIDEO_ID)

        assert result is not None
        assert "succeeded on proxy exit 2/3 (session -2)" in caplog.text
        assert "pass@" not in caplog.text
        local_video.cleanup_local_video(result[1])
