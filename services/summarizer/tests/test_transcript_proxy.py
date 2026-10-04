"""Caption-fetch proxy selection (transcription/transcript.py, transcript_fetcher._api_label).

YOUTUBE_PROXY_URL must win over the Webshare pair so every YouTube request
leaves through the same exit, and the trail label must follow the same rule.
"""

from __future__ import annotations

import pytest
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from src.services.transcription import transcript, transcript_fetcher

URL = "http://user:pass@proxy.example:8080"


@pytest.fixture
def env(monkeypatch):
    def _set(proxy_url: str | None, webshare_user: str | None, webshare_pass: str | None) -> None:
        monkeypatch.setattr(transcript.settings, "YOUTUBE_PROXY_URL", proxy_url)
        monkeypatch.setattr(transcript.settings, "WEBSHARE_PROXY_USERNAME", webshare_user)
        monkeypatch.setattr(transcript.settings, "WEBSHARE_PROXY_PASSWORD", webshare_pass)

    return _set


class TestProxyConfig:
    def test_should_use_youtube_proxy_url_even_when_webshare_pair_set(self, env):
        env(URL, "wsuser", "wspass")

        config = transcript._proxy_config()

        assert type(config) is GenericProxyConfig
        assert config.to_requests_dict() == {"http": URL, "https": URL}

    def test_should_fall_back_to_webshare_when_url_blank(self, env):
        env("  ", "wsuser", "wspass")

        config = transcript._proxy_config()

        assert isinstance(config, WebshareProxyConfig)
        assert config.proxy_username == "wsuser"

    def test_should_be_direct_when_nothing_configured(self, env):
        env(None, None, None)

        assert transcript._proxy_config() is None

    def test_should_be_direct_when_only_webshare_username_set(self, env):
        env(None, "wsuser", None)

        assert transcript._proxy_config() is None


class TestApiLabel:
    def test_should_label_proxy_when_youtube_proxy_url_set(self, env):
        env(URL, None, None)

        assert transcript_fetcher._api_label() == "proxy"

    def test_should_label_proxy_when_webshare_pair_set(self, env):
        env(None, "wsuser", "wspass")

        assert transcript_fetcher._api_label() == "proxy"

    def test_should_label_api_when_webshare_pair_incomplete(self, env):
        env("", "wsuser", None)

        assert transcript_fetcher._api_label() == "api"

    def test_should_label_api_when_nothing_configured(self, env):
        env(None, None, None)

        assert transcript_fetcher._api_label() == "api"
