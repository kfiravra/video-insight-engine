"""Caption-fetch proxy selection (transcription/transcript.py, transcript_fetcher._api_label).

YOUTUBE_PROXY_URL is the only proxy setting: when it is set, caption fetches
leave through the same exit as every other YouTube request and the trail label
says so; blank means direct.
"""

from __future__ import annotations

import pytest
from youtube_transcript_api.proxies import GenericProxyConfig

from src.config import settings
from src.services.transcription import transcript, transcript_fetcher

URL = "http://user:pass@proxy.example:8080"


@pytest.fixture
def env(monkeypatch):
    def _set(proxy_url: str | None) -> None:
        monkeypatch.setattr(settings, "YOUTUBE_PROXY_URL", proxy_url)

    return _set


class TestProxyConfig:
    def test_should_use_youtube_proxy_url_when_set(self, env):
        env(URL)

        config = transcript._proxy_config()

        assert type(config) is GenericProxyConfig
        assert config.to_requests_dict() == {"http": URL, "https": URL}

    def test_should_be_direct_when_url_is_whitespace(self, env):
        env("  ")

        assert transcript._proxy_config() is None

    def test_should_be_direct_when_nothing_configured(self, env):
        env(None)

        assert transcript._proxy_config() is None


class TestApiLabel:
    def test_should_label_proxy_when_youtube_proxy_url_set(self, env):
        env(URL)

        assert transcript_fetcher._api_label() == "proxy"

    def test_should_label_api_when_url_blank(self, env):
        env("")

        assert transcript_fetcher._api_label() == "api"

    def test_should_label_api_when_nothing_configured(self, env):
        env(None)

        assert transcript_fetcher._api_label() == "api"
