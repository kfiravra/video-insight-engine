"""Tests for llm_common.alerts — webhook delivery for llm_alerts."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_common.alerts import build_webhook_body, deliver_alert
from llm_common.callback import MongoDBUsageCallback

WEBHOOK_URL = "http://alert-catcher.local/hook"


def _mock_urlopen(status: int = 200) -> MagicMock:
    """Mock for urllib.request.urlopen used as a context manager."""
    opener = MagicMock()
    opener.return_value.__enter__.return_value = MagicMock(status=status)
    return opener


class TestDeliverAlert:
    def test_no_op_when_webhook_url_unset(self, monkeypatch):
        monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
        with patch("llm_common.alerts.urllib.request.urlopen", _mock_urlopen()) as opener:
            assert deliver_alert({"type": "high_cost_call"}) is False
        opener.assert_not_called()

    def test_posts_alert_json_to_webhook(self, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
        alert = {
            "type": "high_cost_call",
            "cost_usd": 0.91,
            "model": "anthropic/claude-sonnet-4-6",
            "timestamp": datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        }
        with patch("llm_common.alerts.urllib.request.urlopen", _mock_urlopen()) as opener:
            assert deliver_alert(alert) is True

        request = opener.call_args[0][0]
        assert request.full_url == WEBHOOK_URL
        assert request.get_header("Content-type") == "application/json"
        body = json.loads(request.data.decode("utf-8"))
        assert body["type"] == "high_cost_call"
        assert body["cost_usd"] == 0.91
        # datetimes survive as strings, not a serialization crash
        assert "2026-07-08" in body["timestamp"]

    def test_rejects_non_http_scheme(self, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", "file:///etc/passwd")
        with patch("llm_common.alerts.urllib.request.urlopen", _mock_urlopen()) as opener:
            assert deliver_alert({"type": "high_cost_call"}) is False
        opener.assert_not_called()

    def test_returns_false_on_non_2xx(self, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
        with patch("llm_common.alerts.urllib.request.urlopen", _mock_urlopen(status=500)):
            assert deliver_alert({"type": "high_cost_call"}) is False

    def test_never_raises_on_network_failure(self, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
        with patch(
            "llm_common.alerts.urllib.request.urlopen",
            MagicMock(side_effect=OSError("connection refused")),
        ):
            assert deliver_alert({"type": "high_cost_call"}) is False


class TestCallbackWebhookWiring:
    """High-cost alerts written by the callback must also hit the webhook."""

    def test_sync_cost_alert_delivers_webhook(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync", cost_threshold=0.10)
        with patch("llm_common.callback.deliver_alert") as deliver:
            cb._check_cost_alert_sync({"cost_usd": 0.50, "model": "m", "feature": "f"})

        mock_db["llm_alerts"].insert_one.assert_called_once()
        deliver.assert_called_once()
        assert deliver.call_args[0][0]["type"] == "high_cost_call"

    def test_sync_below_threshold_no_webhook(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync", cost_threshold=0.10)
        with patch("llm_common.callback.deliver_alert") as deliver:
            cb._check_cost_alert_sync({"cost_usd": 0.05, "model": "m", "feature": "f"})

        deliver.assert_not_called()

    def test_sync_delivers_webhook_even_when_mongo_write_fails(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync", cost_threshold=0.10)
        mock_db["llm_alerts"].insert_one.side_effect = OSError("mongo down")
        with patch("llm_common.callback.deliver_alert") as deliver:
            cb._check_cost_alert_sync({"cost_usd": 0.50, "model": "m", "feature": "f"})

        deliver.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_cost_alert_delivers_webhook(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="assistant", mode="async", cost_threshold=0.10)
        cb._alerts_col = MagicMock(insert_one=AsyncMock())
        with patch("llm_common.callback.deliver_alert") as deliver:
            await cb._check_cost_alert_async({"cost_usd": 0.50, "model": "m", "feature": "f"})

        cb._alerts_col.insert_one.assert_awaited_once()
        deliver.assert_called_once()


class TestTelegramReceiver:
    TELEGRAM_URL = "https://api.telegram.org/bot123:abc/sendMessage?chat_id=4242"

    def test_should_send_chat_id_and_text_when_url_is_telegram(self, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", self.TELEGRAM_URL)
        with patch("llm_common.alerts.urllib.request.urlopen", _mock_urlopen()) as opener:
            alert = {"type": "backup_stale", "severity": "critical", "age_hours": 30}
            assert deliver_alert(alert) is True
        sent = json.loads(opener.call_args[0][0].data)
        assert sent["chat_id"] == "4242"
        assert sent["text"].startswith("VIE critical: backup_stale")
        assert "age_hours: 30" in sent["text"]

    def test_should_keep_raw_document_for_other_receivers(self):
        body = json.loads(build_webhook_body("https://hooks.example/x", {"type": "x", "cost": 1.5}))
        assert body == {"type": "x", "cost": 1.5}
