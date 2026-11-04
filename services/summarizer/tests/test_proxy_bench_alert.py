"""One proxy_exits_benched alert when a block tips more than half the exit pool onto the bench.

``download_utils._ExitMemory.mark_blocked`` reports the crossing (benched
before <= half < after, at most once per cooldown, per process);
``proxy_exit_alert`` logs it and posts the webhook + ``llm_alerts`` row on a
daemon thread. The conftest autouse fixture makes delivery a no-op; the
``alerts`` fixture here records instead.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.services.media import download_utils, proxy_exit_alert
from src.services.media.proxy_exit_alert import _deliver as real_deliver

EXITS = [f"http://user-{n}:pass@p.webshare.io:80" for n in (1, 2, 3)]
TTL = download_utils._BLOCKED_EXIT_TTL_SECONDS


class _Blocked(Exception):
    pass


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def three_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", EXITS[0])
    monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 3)


@pytest.fixture(autouse=True)
def service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned: test_logging_shape configures other names in the same process."""
    monkeypatch.setattr(proxy_exit_alert, "configured_service_name", lambda: "vie-summarizer")


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(download_utils, "_EXIT_MEMORY", download_utils._ExitMemory(clock=fake))
    return fake


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(proxy_exit_alert, "_deliver", sent.append)
    return sent


def _block(*urls: str) -> None:
    for url in urls:
        download_utils.record_blocked_exit(url)


class TestBenchCrossing:
    def test_should_stay_quiet_while_half_or_less_of_the_pool_is_benched(self, alerts):
        _block(EXITS[0])

        assert alerts == []

    def test_should_alert_once_when_a_block_tips_the_pool_past_half(self, alerts):
        _block(EXITS[0], EXITS[1])

        (alert,) = alerts
        assert {k: v for k, v in alert.items() if k not in ("message", "timestamp")} == {
            "type": "proxy_exits_benched",
            "severity": "critical",
            "service": "vie-summarizer",
            "benched": "2/3",
            "exits": "session -1, session -2",
            "window_minutes": 15,
        }
        assert "in the last 15 min" in alert["message"]
        assert alert["timestamp"].tzinfo == UTC

    def test_should_not_alert_again_while_the_pool_stays_benched(self, alerts):
        _block(*EXITS)

        assert len(alerts) == 1

    def test_should_not_alert_when_an_already_benched_exit_is_blocked_again(
        self, alerts, clock, monkeypatch
    ):
        monkeypatch.setattr(download_utils, "_BENCH_ALERT_COOLDOWN_S", 0)
        _block(EXITS[0], EXITS[1])
        clock.now += 60

        _block(EXITS[1], EXITS[0])

        assert len(alerts) == 1

    def test_should_alert_again_after_the_window_when_a_new_wave_tips_the_pool(self, alerts, clock):
        _block(EXITS[0], EXITS[1])
        clock.now += TTL

        _block(EXITS[2], EXITS[0])

        assert [alert["exits"] for alert in alerts] == [
            "session -1, session -2",
            "session -1, session -3",
        ]

    def test_should_hold_a_second_alert_within_the_cooldown_when_an_exit_recovers_and_tips_again(
        self, alerts, clock
    ):
        _block(EXITS[0], EXITS[1])
        download_utils.record_working_exit(EXITS[1], "Test fetch", 1, 3)
        clock.now += 60

        _block(EXITS[1])
        assert len(alerts) == 1

        clock.now += TTL
        _block(EXITS[0], EXITS[1])
        assert len(alerts) == 2

    def test_should_alert_on_the_first_block_when_the_pool_is_one_exit(self, alerts, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 1)

        _block(EXITS[0])

        assert [alert["benched"] for alert in alerts] == ["1/1"]

    def test_should_never_alert_without_a_proxy(self, alerts, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", None)

        _block(*EXITS)

        assert alerts == []

    def test_should_ignore_a_url_outside_the_configured_pool(self, alerts):
        _block("http://other-1:pass@q.example:80", "http://other-2:pass@q.example:80")

        assert alerts == []

    def test_should_alert_exactly_once_when_a_rotation_blocks_every_exit(self, alerts):
        def attempt(proxy_url: str) -> str:
            raise _Blocked

        with pytest.raises(_Blocked):
            download_utils.try_proxy_exits(
                download_utils.ytdlp_proxy_exit_urls("video1", limit=None),
                attempt,
                lambda e: isinstance(e, _Blocked),
                "Test fetch",
            )

        assert len(alerts) == 1

    def test_should_name_the_worker_process_in_the_alert(self, alerts, monkeypatch):
        monkeypatch.setattr(
            proxy_exit_alert, "configured_service_name", lambda: "vie-summarizer-worker"
        )

        _block(EXITS[0], EXITS[1])

        assert alerts[0]["service"] == "vie-summarizer-worker"

    def test_should_log_the_crossing_at_error_level_without_credentials(self, alerts, caplog):
        with caplog.at_level(logging.INFO):
            _block(EXITS[0], EXITS[1])

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert errors == [
            "proxy_exits_benched service=vie-summarizer benched=2/3 exits=session -1, session -2"
        ]
        assert "pass@" not in caplog.text
        assert "@" not in json.dumps(alerts[0], default=str)

    def test_should_never_raise_into_the_youtube_call_when_alerting_fails(
        self, monkeypatch, caplog
    ):
        def broken(alert: dict[str, Any]) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(proxy_exit_alert, "_deliver", broken)

        with caplog.at_level(logging.WARNING):
            _block(EXITS[0], EXITS[1])

        assert "proxy_exit_alert_failed error=RuntimeError" in caplog.text


class TestDelivery:
    ALERT = {"type": "proxy_exits_benched", "severity": "critical", "benched": "2/3"}

    def test_should_post_the_webhook_then_write_the_llm_alerts_row(self, monkeypatch):
        order: list[str] = []
        collection = MagicMock()

        def insert_one(doc: dict[str, Any]) -> None:
            order.append("row")
            doc["_id"] = "oid"  # what pymongo does to its argument

        collection.insert_one.side_effect = insert_one
        webhook = MagicMock(side_effect=lambda alert: order.append("webhook") or False)
        monkeypatch.setattr(proxy_exit_alert, "deliver_alert", webhook)
        monkeypatch.setattr(proxy_exit_alert, "_alerts_collection", lambda: collection)
        alert = dict(self.ALERT)

        thread = real_deliver(alert)
        thread.join(2)

        assert order == ["webhook", "row"]
        webhook.assert_called_once_with(alert)
        assert collection.insert_one.call_args.args[0] is not alert
        assert "_id" not in alert

    def test_should_still_post_the_webhook_when_the_row_write_fails(self, monkeypatch, caplog):
        webhook = MagicMock(return_value=True)

        def no_mongo() -> MagicMock:
            raise RuntimeError("mongo down")

        monkeypatch.setattr(proxy_exit_alert, "deliver_alert", webhook)
        monkeypatch.setattr(proxy_exit_alert, "_alerts_collection", no_mongo)

        with caplog.at_level(logging.WARNING):
            proxy_exit_alert._post(dict(self.ALERT))

        webhook.assert_called_once()
        assert "proxy_exit_alert_write_failed error=RuntimeError" in caplog.text

    def test_should_dispatch_on_a_daemon_thread(self, monkeypatch):
        thread_class = MagicMock()
        monkeypatch.setattr(proxy_exit_alert.threading, "Thread", thread_class)
        alert = dict(self.ALERT)

        real_deliver(alert)

        thread_class.assert_called_once_with(
            target=proxy_exit_alert._post, args=(alert,), name="proxy-exit-alert", daemon=True
        )
        thread_class.return_value.start.assert_called_once_with()
