"""Tests for the shared yt-dlp player-client plumbing (media/download_utils.py).

These builders are the one thing that must stay in sync across the stream-URL
fetch, the scene-detection download, the local 720p fallback, and the whisper
audio download — a typo here silently reinstates the 403s everywhere.
"""

from __future__ import annotations

from pathlib import Path
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
