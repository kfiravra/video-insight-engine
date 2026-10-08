"""Tests for the shared yt-dlp player-client plumbing (media/download_utils.py).

These builders are the one thing that must stay in sync across the stream-URL
fetch, the scene-detection download, the local 720p fallback, and the whisper
audio download — a typo here silently reinstates the 403s everywhere.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.media import download_utils


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", None)

    def _set(value: str) -> None:
        monkeypatch.setattr(download_utils.settings, "YTDLP_PLAYER_CLIENTS", value)

    return _set


@pytest.fixture
def proxy(monkeypatch):
    def _set(value: str | None) -> None:
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", value)

    return _set


class TestCliArgs:
    def test_single_client(self, clients):
        clients("android")
        assert download_utils.ytdlp_client_cli_args() == [
            "--extractor-args",
            "youtube:player_client=android",
        ]

    def test_multiple_clients_preserve_order(self, clients):
        clients("android,web")
        assert download_utils.ytdlp_client_cli_args() == [
            "--extractor-args",
            "youtube:player_client=android,web",
        ]

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_means_ytdlp_defaults(self, clients, value):
        clients(value)
        assert download_utils.ytdlp_client_cli_args() == []


class TestApiOpts:
    def test_single_client(self, clients):
        clients("android")
        assert download_utils.ytdlp_client_api_opts() == {
            "extractor_args": {"youtube": {"player_client": ["android"]}}
        }

    def test_multiple_clients_are_split_and_stripped(self, clients):
        clients("android, web")
        assert download_utils.ytdlp_client_api_opts() == {
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}}
        }

    @pytest.mark.parametrize("value", ["", " , "])
    def test_blank_means_ytdlp_defaults(self, clients, value):
        clients(value)
        assert download_utils.ytdlp_client_api_opts() == {}


class TestProxy:
    URL = "http://user:pass@proxy.example:8080"

    def test_should_keep_proxy_off_the_command_line_when_url_set(self, clients, proxy):
        """The URL carries credentials; argv ends up in Sentry breadcrumbs and ps."""
        clients("android")
        proxy(self.URL)
        assert download_utils.ytdlp_client_cli_args() == [
            "--extractor-args",
            "youtube:player_client=android",
        ]

    def test_should_carry_proxy_in_subprocess_env_when_url_set(self, proxy):
        proxy(self.URL)
        env = download_utils.ytdlp_subprocess_env()
        assert env is not None
        assert {env[name] for name in download_utils._PROXY_ENV_VARS} == {self.URL}

    def test_should_keep_parent_env_in_subprocess_env(self, proxy, monkeypatch):
        """PATH and friends must survive or the child cannot even find yt-dlp."""
        monkeypatch.setenv("VIE_TEST_MARKER", "kept")
        proxy(self.URL)
        env = download_utils.ytdlp_subprocess_env()
        assert env is not None
        assert env["VIE_TEST_MARKER"] == "kept"

    def test_should_override_inherited_lowercase_proxy_var(self, proxy, monkeypatch):
        """urllib prefers lowercase names, so an inherited one must not win."""
        monkeypatch.setenv("https_proxy", "http://other.example:1")
        proxy(self.URL)
        env = download_utils.ytdlp_subprocess_env()
        assert env is not None
        assert env["https_proxy"] == self.URL

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_should_stay_direct_when_url_blank(self, clients, proxy, value):
        clients("android")
        proxy(value)
        assert download_utils.ytdlp_proxy_url() is None
        assert download_utils.ytdlp_subprocess_env() is None
        assert "proxy" not in download_utils.ytdlp_client_api_opts()

    def test_should_strip_whitespace_around_url(self, proxy):
        proxy(f"  {self.URL}\n")
        assert download_utils.ytdlp_proxy_url() == self.URL

    def test_should_add_proxy_to_api_opts_when_url_set(self, clients, proxy):
        clients("android")
        proxy(self.URL)
        assert download_utils.ytdlp_client_api_opts() == {
            "extractor_args": {"youtube": {"player_client": ["android"]}},
            "proxy": self.URL,
        }

    def test_should_default_to_no_proxy(self):
        from src.config import Settings

        assert Settings.model_fields["YOUTUBE_PROXY_URL"].default is None


class TestDefaultClients:
    def test_default_setting_is_android_only(self):
        """Pin the default client (guards accidental "fixes").

        android ONLY. "default"/web DASH URLs 403 from this environment
        (2026-08), and android_vr formats are PO-token-gated — downloads 403
        even when format listings look fine (verified 2026-09-03). SABR gaps
        in android's audio-only formats are handled by the ``bestaudio/best``
        selector, not by adding clients.
        """
        from src.config import Settings

        assert Settings.model_fields["YTDLP_PLAYER_CLIENTS"].default == "android"


class TestDownloadYoutubeAudioClientOpts:
    def _run(self, ydl_opts: dict, tmp_path: Path) -> dict:
        """Run one successful download and return the opts YoutubeDL received."""
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        with patch.object(download_utils.yt_dlp, "YoutubeDL", return_value=ydl) as ctor:
            download_utils.download_youtube_audio("dQw4w9WgXcQ", ydl_opts, tmp_path, "audio")
        return ctor.call_args.args[0]

    def test_injects_configured_clients(self, clients, tmp_path):
        clients("android")
        received = self._run({"format": "bestaudio"}, tmp_path)
        assert received["format"] == "bestaudio"
        assert received["extractor_args"] == {"youtube": {"player_client": ["android"]}}

    def test_does_not_override_caller_supplied_extractor_args(self, clients, tmp_path):
        clients("android")
        custom = {"youtube": {"player_client": ["ios"]}}
        received = self._run({"extractor_args": custom}, tmp_path)
        assert received["extractor_args"] == custom

    def test_blank_setting_leaves_opts_untouched(self, clients, tmp_path):
        clients("")
        received = self._run({"format": "bestaudio"}, tmp_path)
        assert "extractor_args" not in received
        assert "proxy" not in received

    def test_should_inject_proxy_when_url_set(self, clients, proxy, tmp_path):
        clients("android")
        proxy(TestProxy.URL)
        received = self._run({"format": "bestaudio"}, tmp_path)
        assert received["proxy"] == TestProxy.URL
        assert received["extractor_args"] == {"youtube": {"player_client": ["android"]}}

    def test_should_keep_caller_supplied_proxy(self, clients, proxy, tmp_path):
        clients("android")
        proxy(TestProxy.URL)
        received = self._run({"proxy": "http://caller.example:1"}, tmp_path)
        assert received["proxy"] == "http://caller.example:1"


class TestProxyExitUrls:
    """Sticky-exit rotation list for IP-scoped failures (caption 429)."""

    @pytest.fixture
    def exits(self, monkeypatch):
        def _set(count: int) -> None:
            monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", count)

        return _set

    def test_should_be_empty_without_a_proxy(self, proxy, exits):
        proxy(None)
        exits(5)

        assert download_utils.ytdlp_proxy_exit_urls() == []

    def test_should_be_primary_only_when_count_is_one(self, proxy, exits):
        proxy("http://user-2:pass@p.webshare.io:80")
        exits(1)

        assert download_utils.ytdlp_proxy_exit_urls() == ["http://user-2:pass@p.webshare.io:80"]

    def test_should_be_primary_only_without_a_sticky_suffix(self, proxy, exits):
        proxy("http://user:pass@p.webshare.io:80")
        exits(5)

        assert download_utils.ytdlp_proxy_exit_urls() == ["http://user:pass@p.webshare.io:80"]

    def test_should_be_primary_only_without_credentials(self, proxy, exits):
        proxy("http://p.webshare.io:80")
        exits(5)

        assert download_utils.ytdlp_proxy_exit_urls() == ["http://p.webshare.io:80"]

    def test_should_rotate_to_following_exits_and_wrap(self, proxy, exits):
        proxy("http://user-4:pass@p.webshare.io:80")
        exits(5)

        assert download_utils.ytdlp_proxy_exit_urls() == [
            "http://user-4:pass@p.webshare.io:80",
            "http://user-5:pass@p.webshare.io:80",
            "http://user-1:pass@p.webshare.io:80",
        ]

    def test_should_cap_the_number_of_exits(self, proxy, exits):
        proxy("http://user-1:pass@p.webshare.io:80")
        exits(50)

        assert len(download_utils.ytdlp_proxy_exit_urls()) == download_utils._MAX_ROTATED_EXITS

    def test_should_not_exceed_the_pool_when_it_is_small(self, proxy, exits):
        proxy("http://user-1:pass@p.webshare.io:80")
        exits(2)

        assert download_utils.ytdlp_proxy_exit_urls() == [
            "http://user-1:pass@p.webshare.io:80",
            "http://user-2:pass@p.webshare.io:80",
        ]

    def test_should_keep_the_password_verbatim(self, proxy, exits):
        """Only the username is rewritten — special characters in the password survive."""
        proxy("http://user-1:p%40ss:w@rd@p.webshare.io:80")
        exits(2)

        assert (
            download_utils.ytdlp_proxy_exit_urls()[1]
            == "http://user-2:p%40ss:w@rd@p.webshare.io:80"
        )

    def test_should_default_to_a_single_exit(self):
        from src.config import Settings

        assert Settings.model_fields["YOUTUBE_PROXY_EXIT_COUNT"].default == 1


class _RateLimited(Exception):
    pass


class TestTryProxyExits:
    """The one exit-rotation loop shared by the timedtext and caption-API fetches."""

    EXITS = ["http://u-1:p@h:80", "http://u-2:p@h:80", "http://u-3:p@h:80"]

    @staticmethod
    def _run(attempt) -> str:
        return download_utils.try_proxy_exits(
            TestTryProxyExits.EXITS,
            attempt,
            lambda e: isinstance(e, _RateLimited),
            "Test fetch",
        )

    def test_should_return_the_first_exit_that_is_not_rate_limited(self):
        tried: list[str] = []

        def attempt(proxy_url: str) -> str:
            tried.append(proxy_url)
            if proxy_url == self.EXITS[0]:
                raise _RateLimited
            return "ok"

        assert self._run(attempt) == "ok"
        assert tried == self.EXITS[:2]

    def test_should_stop_at_the_exit_that_raised_another_error(self):
        attempt = MagicMock(side_effect=ValueError("boom"))

        with pytest.raises(ValueError):
            self._run(attempt)

        assert attempt.call_count == 1

    def test_should_raise_the_last_error_when_every_exit_is_rate_limited(self):
        errors = [_RateLimited(url) for url in self.EXITS]
        attempt = MagicMock(side_effect=errors)

        with pytest.raises(_RateLimited) as exc_info:
            self._run(attempt)

        assert exc_info.value is errors[-1]

    def test_should_never_log_a_proxy_url(self, caplog):
        attempt = MagicMock(side_effect=[_RateLimited(), "ok"])

        with caplog.at_level("WARNING"):
            self._run(attempt)

        assert "Test fetch blocked on proxy exit 1/3" in caplog.text
        assert "u-1:p@" not in caplog.text

    def test_should_log_the_exit_that_worked_without_credentials(self, caplog):
        attempt = MagicMock(side_effect=[_RateLimited(), "ok"])

        with caplog.at_level("INFO"):
            self._run(attempt)

        assert "Test fetch succeeded on proxy exit 2/3 (session -2)" in caplog.text
        assert ":p@" not in caplog.text


class TestHiresClientAttempts:
    """Client lists for the hi-res 720p download (pass 1 and audio keep YTDLP_PLAYER_CLIENTS)."""

    @pytest.fixture
    def clients(self, monkeypatch):
        def _set(base: str, hires: str) -> None:
            monkeypatch.setattr(download_utils.settings, "YTDLP_PLAYER_CLIENTS", base)
            monkeypatch.setattr(download_utils.settings, "YTDLP_HIRES_PLAYER_CLIENTS", hires)

        return _set

    def test_should_try_hires_clients_then_pass1_clients(self, clients):
        clients("android", "web_embedded,android")
        assert download_utils.ytdlp_hires_client_attempts() == ["web_embedded,android", "android"]

    def test_should_make_one_attempt_when_lists_match(self, clients):
        clients("android", " android ")
        assert download_utils.ytdlp_hires_client_attempts() == ["android"]

    def test_should_fall_back_to_pass1_clients_when_hires_is_blank(self, clients):
        clients("android", "")
        assert download_utils.ytdlp_hires_client_attempts() == ["android"]

    def test_should_build_cli_args_for_an_explicit_client_list(self, clients):
        clients("android", "web_embedded,android")
        assert download_utils.ytdlp_client_cli_args("web_embedded,android") == [
            "--extractor-args",
            "youtube:player_client=web_embedded,android",
        ]

    def test_should_default_to_the_measured_720p_clients(self):
        from src.config import Settings

        field = Settings.model_fields["YTDLP_HIRES_PLAYER_CLIENTS"]
        assert field.default == "web_embedded,android"


BOT_CHECK = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. Use "
    "--cookies-from-browser or --cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
    "for how to manually pass cookies."
)


class TestIsExitBlocked:
    """What counts as a per-exit-IP block worth trying the next proxy exit."""

    @pytest.mark.parametrize(
        "message",
        [
            BOT_CHECK,
            BOT_CHECK.replace("’", "'"),
            "Sign in to confirm you are not a bot",
            "ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
            "429 Client Error: Too Many Requests for url: https://www.youtube.com/api/timedtext",
        ],
    )
    def test_should_be_true_for_a_bot_check_or_a_429(self, message):
        assert download_utils.is_exit_blocked(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "ERROR: [youtube] x: Sign in to confirm your age. "
            "This video may be inappropriate for some users.",
            "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
            "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader",
            "ERROR: unable to download video data: HTTP Error 404: Not Found",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "ERROR: [youtube] x: Requested format is not available",
        ],
    )
    def test_should_be_false_for_an_error_every_exit_would_share(self, message):
        assert download_utils.is_exit_blocked(message) is False

    def test_should_read_an_exception_message(self):
        assert download_utils.is_exit_blocked(RuntimeError(BOT_CHECK)) is True


class TestProxyExitLabel:
    def test_should_name_the_sticky_session(self):
        assert download_utils.proxy_exit_label("http://user-3:pa:ss@h:80") == "session -3"

    @pytest.mark.parametrize("url", ["http://user:pass@h:80", "http://h:80"])
    def test_should_never_echo_the_url_without_a_sticky_suffix(self, url):
        assert download_utils.proxy_exit_label(url) == "unnamed session"


class TestDownloadYoutubeAudioExitRotation:
    """The in-process audio download rotates exits on a bot check or 429."""

    EXITS = ["http://user-1:pass@p.webshare.io:80", "http://user-2:pass@p.webshare.io:80"]

    @pytest.fixture
    def ydl(self, monkeypatch):
        """YoutubeDL stand-in failing per proxy exit; records the proxies tried."""
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", self.EXITS[0])
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 2)
        monkeypatch.setattr(download_utils.settings, "YTDLP_PLAYER_CLIENTS", "android")
        fake = SimpleNamespace(errors={}, tried=[])

        def _build(opts: dict) -> MagicMock:
            fake.tried.append(opts.get("proxy"))
            ydl = MagicMock()
            ydl.__enter__ = MagicMock(return_value=ydl)
            ydl.__exit__ = MagicMock(return_value=False)
            error = fake.errors.get(opts.get("proxy"))
            if error is not None:
                ydl.download.side_effect = download_utils.yt_dlp.utils.DownloadError(error)
            return ydl

        with (
            patch.object(download_utils.yt_dlp, "YoutubeDL", side_effect=_build),
            patch.object(download_utils.time, "sleep"),
        ):
            yield fake

    def test_should_download_on_the_next_exit_after_a_bot_check(self, ydl, tmp_path):
        ydl.errors[self.EXITS[0]] = BOT_CHECK

        download_utils.download_youtube_audio("dQw4w9WgXcQ", {}, tmp_path, "audio")

        assert ydl.tried == self.EXITS

    def test_should_raise_todays_error_when_every_exit_gets_the_bot_check(self, ydl, tmp_path):
        ydl.errors.update(dict.fromkeys(self.EXITS, BOT_CHECK))

        with pytest.raises(download_utils.TranscriptError) as exc_info:
            download_utils.download_youtube_audio(
                "dQw4w9WgXcQ", {}, tmp_path, "audio", max_attempts=1
            )

        assert exc_info.value.code is download_utils.ErrorCode.VIDEO_UNAVAILABLE
        assert ydl.tried == self.EXITS

    def test_should_not_rotate_on_a_private_video(self, ydl, tmp_path):
        ydl.errors[self.EXITS[0]] = "ERROR: [youtube] x: Private video"

        with pytest.raises(download_utils.TranscriptError):
            download_utils.download_youtube_audio(
                "dQw4w9WgXcQ", {}, tmp_path, "audio", max_attempts=1
            )

        assert ydl.tried == self.EXITS[:1]

    def test_should_not_rotate_away_from_a_caller_supplied_proxy(self, ydl, tmp_path):
        ydl.errors["http://caller.example:1"] = BOT_CHECK

        with pytest.raises(download_utils.TranscriptError):
            download_utils.download_youtube_audio(
                "dQw4w9WgXcQ", {"proxy": "http://caller.example:1"}, tmp_path, "audio", 1
            )

        assert ydl.tried == ["http://caller.example:1"]
