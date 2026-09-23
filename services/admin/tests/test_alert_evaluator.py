"""Tests for the aggregate alert evaluator (spike, failures, backup staleness).

The two thresholds configured via POST /alerts/config used to be decorative —
nothing evaluated them. These tests pin the decision logic (pure functions)
and the orchestration (write to llm_alerts + webhook delivery + cooldown).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import alert_evaluator as ev

NOW = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_backup_dir(monkeypatch, tmp_path: Path) -> None:
    # Hermetic default: BACKUP_DIR points at a path that does not exist, so the
    # backup-stale dead-man's switch is skipped unless a test opts in.
    monkeypatch.setattr(ev.settings, "BACKUP_DIR", str(tmp_path / "no-backups-here"))


def _write_manifest(backup_root: Path, stamp: str) -> Path:
    dest = backup_root / stamp
    dest.mkdir(parents=True)
    manifest = dest / "manifest.json"
    manifest.write_text(json.dumps({"timestamp": stamp, "created_by": "scripts/backup.sh"}))
    return manifest


# ─── Pure decision logic ───


class TestBuildSpikeAlert:
    def test_no_alert_when_under_multiplier(self) -> None:
        assert (
            ev.build_spike_alert(today_usd=3.0, baseline_daily_usd=2.0, multiplier=2.0, now=NOW)
            is None
        )

    def test_alert_when_today_exceeds_multiplier_times_baseline(self) -> None:
        alert = ev.build_spike_alert(today_usd=5.0, baseline_daily_usd=2.0, multiplier=2.0, now=NOW)
        assert alert is not None
        assert alert["type"] == "daily_spend_spike"
        assert alert["cost_usd"] == 5.0
        assert alert["threshold"] == 2.0
        assert alert["timestamp"] == NOW

    def test_no_alert_on_cold_baseline(self) -> None:
        # $0.30 today vs $0.01/day average is a 30x "spike" — but meaningless.
        assert (
            ev.build_spike_alert(today_usd=0.30, baseline_daily_usd=0.01, multiplier=2.0, now=NOW)
            is None
        )

    def test_prorated_baseline_catches_early_day_spike(self) -> None:
        # Half a day in: $3 vs a prorated threshold of 2.0*2.0*0.5 = $2.
        # Against the full-day baseline ($4) this would NOT alert — the
        # under-alerting bug this proration fixes.
        alert = ev.build_spike_alert(
            today_usd=3.0, baseline_daily_usd=2.0, multiplier=2.0, now=NOW, day_fraction=0.5
        )
        assert alert is not None
        assert alert["type"] == "daily_spend_spike"
        assert alert["day_fraction"] == 0.5

    def test_no_alert_when_under_prorated_threshold(self) -> None:
        # $1.50 at half-day vs prorated threshold $2 — proportionate spend.
        assert (
            ev.build_spike_alert(
                today_usd=1.5, baseline_daily_usd=2.0, multiplier=2.0, now=NOW, day_fraction=0.5
            )
            is None
        )

    def test_midnight_fraction_clamped_to_one_hour_floor(self) -> None:
        # Just after midnight day_fraction ~0; the clamp compares against one
        # hour's worth of baseline (2.0*2.0/24 ≈ $0.167), so trivial spend
        # does not register as a spike.
        assert (
            ev.build_spike_alert(
                today_usd=0.05, baseline_daily_usd=2.0, multiplier=2.0, now=NOW, day_fraction=0.0
            )
            is None
        )

    def test_cold_start_guard_uses_full_day_baseline_not_prorated(self) -> None:
        # baseline $0.40/day is below MIN_BASELINE_DAILY_USD pre-proration →
        # guard wins regardless of how large the prorated exceedance looks.
        assert (
            ev.build_spike_alert(
                today_usd=50.0, baseline_daily_usd=0.40, multiplier=2.0, now=NOW, day_fraction=0.5
            )
            is None
        )


class TestBuildBackupStaleAlert:
    def test_no_alert_when_backup_fresh(self) -> None:
        latest = NOW - timedelta(hours=5)
        assert ev.build_backup_stale_alert(latest, max_age_hours=26.0, now=NOW) is None

    def test_alert_when_backup_older_than_max_age(self) -> None:
        latest = NOW - timedelta(hours=30)
        alert = ev.build_backup_stale_alert(latest, max_age_hours=26.0, now=NOW)
        assert alert is not None
        assert alert["type"] == "backup_stale"
        assert alert["age_hours"] == 30.0
        assert alert["latest_backup"] == latest

    def test_alert_when_no_backup_exists_at_all(self) -> None:
        # Dead-man's switch: "never backed up" is the worst case, not a pass.
        alert = ev.build_backup_stale_alert(None, max_age_hours=26.0, now=NOW)
        assert alert is not None
        assert alert["type"] == "backup_stale"
        assert alert["latest_backup"] is None
        assert alert["age_hours"] is None


class TestLatestBackupTime:
    def test_returns_newest_manifest_timestamp(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, "20260706T120000Z")
        _write_manifest(tmp_path, "20260707T120000Z")

        assert ev._latest_backup_time(tmp_path) == datetime(2026, 7, 7, 12, 0, tzinfo=UTC)

    def test_none_when_no_manifests(self, tmp_path: Path) -> None:
        assert ev._latest_backup_time(tmp_path) is None

    def test_malformed_manifest_falls_back_to_mtime(self, tmp_path: Path) -> None:
        dest = tmp_path / "20260707T120000Z"
        dest.mkdir()
        (dest / "manifest.json").write_text("{not json")

        taken = ev._latest_backup_time(tmp_path)

        assert taken is not None  # mtime fallback, roughly "now"
        assert abs((datetime.now(UTC) - taken).total_seconds()) < 60


class TestBuildFailureAlert:
    def test_no_alert_below_min_sample(self) -> None:
        assert ev.build_failure_alert(failures=3, total=4, threshold=0.20, now=NOW) is None

    def test_no_alert_when_rate_at_or_below_threshold(self) -> None:
        assert ev.build_failure_alert(failures=2, total=10, threshold=0.20, now=NOW) is None

    def test_alert_when_rate_exceeds_threshold(self) -> None:
        alert = ev.build_failure_alert(failures=5, total=10, threshold=0.20, now=NOW)
        assert alert is not None
        assert alert["type"] == "high_failure_rate"
        assert alert["failure_rate"] == 0.5
        assert alert["sample_size"] == 10


# ─── Orchestration ───


def _db_with(insert_one: AsyncMock | None = None) -> MagicMock:
    db = MagicMock()
    db.llm_alerts.insert_one = insert_one or AsyncMock()
    return db


class TestEvaluateOnce:
    @pytest.mark.anyio
    async def test_writes_and_delivers_alerts_when_thresholds_tripped(self) -> None:
        db = _db_with()
        with (
            patch.object(ev, "_load_config", AsyncMock(return_value=dict(ev.DEFAULT_CONFIG))),
            patch.object(
                ev, "_sum_cost", AsyncMock(side_effect=[70.0, 25.0])
            ),  # baseline 7d, today
            patch.object(ev, "_failure_counts", AsyncMock(return_value=(6, 12))),
            patch.object(ev, "_in_cooldown", AsyncMock(return_value=False)),
            patch.object(ev, "_deliver_webhook", AsyncMock(return_value=True)) as webhook,
        ):
            alerts = await ev.evaluate_once(db, now=NOW)

        types = {a["type"] for a in alerts}
        assert types == {"daily_spend_spike", "high_failure_rate"}
        assert db.llm_alerts.insert_one.await_count == 2
        assert webhook.await_count == 2

    @pytest.mark.anyio
    async def test_quiet_when_all_healthy(self) -> None:
        db = _db_with()
        with (
            patch.object(ev, "_load_config", AsyncMock(return_value=dict(ev.DEFAULT_CONFIG))),
            patch.object(ev, "_sum_cost", AsyncMock(side_effect=[70.0, 10.0])),
            patch.object(ev, "_failure_counts", AsyncMock(return_value=(0, 50))),
            patch.object(ev, "_in_cooldown", AsyncMock(return_value=False)),
            patch.object(ev, "_deliver_webhook", AsyncMock()) as webhook,
        ):
            alerts = await ev.evaluate_once(db, now=NOW)

        assert alerts == []
        db.llm_alerts.insert_one.assert_not_awaited()
        webhook.assert_not_awaited()

    @pytest.mark.anyio
    async def test_cooldown_suppresses_repeat_alerts(self) -> None:
        db = _db_with()
        with (
            patch.object(ev, "_load_config", AsyncMock(return_value=dict(ev.DEFAULT_CONFIG))),
            patch.object(ev, "_sum_cost", AsyncMock(side_effect=[70.0, 25.0])),
            patch.object(ev, "_failure_counts", AsyncMock(return_value=(6, 12))),
            patch.object(ev, "_in_cooldown", AsyncMock(return_value=True)),
            patch.object(ev, "_deliver_webhook", AsyncMock()) as webhook,
        ):
            alerts = await ev.evaluate_once(db, now=NOW)

        assert alerts == []
        db.llm_alerts.insert_one.assert_not_awaited()
        webhook.assert_not_awaited()

    @pytest.mark.anyio
    async def test_webhook_failure_does_not_lose_the_mongo_write(self) -> None:
        db = _db_with()
        with (
            patch.object(ev, "_load_config", AsyncMock(return_value=dict(ev.DEFAULT_CONFIG))),
            patch.object(ev, "_sum_cost", AsyncMock(side_effect=[70.0, 25.0])),
            patch.object(ev, "_failure_counts", AsyncMock(return_value=(0, 50))),
            patch.object(ev, "_in_cooldown", AsyncMock(return_value=False)),
            patch.object(ev, "_deliver_webhook", AsyncMock(side_effect=OSError("down"))),
        ):
            alerts = await ev.evaluate_once(db, now=NOW)

        assert len(alerts) == 1  # spike still recorded
        db.llm_alerts.insert_one.assert_awaited_once()

    @pytest.mark.anyio
    async def test_backup_stale_alert_flows_through_pipeline(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        # Newest manifest 3 days older than NOW → stale; other checks healthy.
        _write_manifest(tmp_path, "20260705T150000Z")
        monkeypatch.setattr(ev.settings, "BACKUP_DIR", str(tmp_path))
        db = _db_with()
        with (
            patch.object(ev, "_load_config", AsyncMock(return_value=dict(ev.DEFAULT_CONFIG))),
            patch.object(ev, "_sum_cost", AsyncMock(side_effect=[70.0, 10.0])),
            patch.object(ev, "_failure_counts", AsyncMock(return_value=(0, 50))),
            patch.object(ev, "_in_cooldown", AsyncMock(return_value=False)),
            patch.object(ev, "_deliver_webhook", AsyncMock(return_value=True)) as webhook,
        ):
            alerts = await ev.evaluate_once(db, now=NOW)

        assert [a["type"] for a in alerts] == ["backup_stale"]
        assert alerts[0]["age_hours"] == 72.0
        db.llm_alerts.insert_one.assert_awaited_once()
        webhook.assert_awaited_once()


class TestDeliverWebhook:
    @pytest.mark.anyio
    async def test_no_op_when_url_unset(self, monkeypatch) -> None:
        monkeypatch.setattr(ev.settings, "ALERT_WEBHOOK_URL", "")
        with patch("src.services.alert_evaluator.httpx.AsyncClient") as client:
            assert await ev._deliver_webhook({"type": "x"}) is False
        client.assert_not_called()

    @pytest.mark.anyio
    async def test_posts_json_when_url_set(self, monkeypatch) -> None:
        monkeypatch.setattr(ev.settings, "ALERT_WEBHOOK_URL", "http://catcher.local/hook")
        response = MagicMock(status_code=200)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=response)
        with patch(
            "src.services.alert_evaluator.httpx.AsyncClient", MagicMock(return_value=client)
        ):
            ok = await ev._deliver_webhook({"type": "daily_spend_spike", "timestamp": NOW})

        assert ok is True
        assert client.post.call_args[0][0] == "http://catcher.local/hook"
        assert b"daily_spend_spike" in client.post.call_args.kwargs["content"]

    @pytest.mark.anyio
    async def test_should_send_chat_id_and_text_when_url_is_telegram(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ev.settings,
            "ALERT_WEBHOOK_URL",
            "https://api.telegram.org/bot1:x/sendMessage?chat_id=77",
        )
        response = MagicMock(status_code=200)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=response)
        with patch(
            "src.services.alert_evaluator.httpx.AsyncClient", MagicMock(return_value=client)
        ):
            alert = {"type": "backup_stale", "severity": "critical", "age_hours": 30}
            ok = await ev._deliver_webhook(alert)

        assert ok is True
        sent = json.loads(client.post.call_args.kwargs["content"])
        assert sent["chat_id"] == "77"
        assert sent["text"].startswith("VIE critical: backup_stale")
        assert "age_hours: 30" in sent["text"]


class TestAlertSeverity:
    """Every evaluator alert carries an explicit severity (admin UI files by it)."""

    def test_spike_should_be_warning(self):
        alert = ev.build_spike_alert(today_usd=5.0, baseline_daily_usd=2.0, multiplier=2.0, now=NOW)
        assert alert["severity"] == "warning"

    def test_failure_rate_should_be_critical(self):
        alert = ev.build_failure_alert(failures=5, total=10, threshold=0.2, now=NOW)
        assert alert["severity"] == "critical"

    def test_backup_stale_should_be_critical(self):
        alert = ev.build_backup_stale_alert(None, 26.0, NOW)
        assert alert["severity"] == "critical"
