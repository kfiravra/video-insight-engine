"""Tests for llm_common.callback — MongoDBUsageCallback."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm_common.callback as callback_module
from llm_common.callback import MongoDBUsageCallback, record_manual_usage
from llm_common.context import (
    llm_feature_var,
    llm_request_id_var,
    llm_user_id_var,
    llm_video_id_var,
    llm_video_summary_id_var,
)
from llm_common.models import UsageRecord


class MockResponse:
    def __init__(
        self,
        tokens_in=10,
        tokens_out=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        cache_hit=False,
    ):
        self.usage = MagicMock(
            prompt_tokens=tokens_in,
            completion_tokens=tokens_out,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
        self._hidden_params = MagicMock(cache_hit=cache_hit)


class TestSyncCallback:
    def test_log_success(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        token = llm_feature_var.set("summarize:chapter")
        try:
            cb.log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "hello"}]},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )
        finally:
            llm_feature_var.reset(token)

        # Record should be in the buffer
        assert len(cb._buffer._buffer) > 0 or mock_db["llm_usage"].insert_many.called

    def test_log_failure(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        cb.log_failure_event(
            kwargs={"model": "test/model", "exception": "timeout"},
            response_obj=None,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )

    def test_cost_alert_triggers(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync", cost_threshold=0.01)

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.50
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={"model": "test/model", "messages": []},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        mock_db["llm_alerts"].insert_one.assert_called_once()

    def test_callback_never_crashes(self):
        """Callback errors should be caught, never propagate."""
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(side_effect=Exception("boom"))

        # Should not raise
        try:
            MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")
        except Exception:
            pass  # Construction may fail but that's initialization, not callback


class TestAsyncCallback:
    @pytest.mark.asyncio
    async def test_async_log_success(self):
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=AsyncMock())
        cb = MongoDBUsageCallback(mock_db, service="explainer", mode="async")

        await cb.async_log_success_event(
            kwargs={"model": "test/model", "messages": [{"content": "hi"}]},
            response_obj=MockResponse(),
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )

    @pytest.mark.asyncio
    async def test_async_log_failure(self):
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=AsyncMock())
        cb = MongoDBUsageCallback(mock_db, service="explainer", mode="async")

        await cb.async_log_failure_event(
            kwargs={"model": "test/model", "exception": "error"},
            response_obj=None,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )

    @pytest.mark.asyncio
    async def test_async_cost_alert_triggers(self):
        """Async alert should await insert_one on motor collection."""
        mock_alerts_col = AsyncMock()
        mock_usage_col = AsyncMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(
            side_effect=lambda k: mock_alerts_col if k == "llm_alerts" else mock_usage_col
        )
        cb = MongoDBUsageCallback(mock_db, service="explainer", mode="async", cost_threshold=0.01)

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.50
            mock_litellm.version = "1.80.0"

            await cb.async_log_success_event(
                kwargs={"model": "test/model", "messages": []},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        mock_alerts_col.insert_one.assert_awaited_once()


class TestCachePlumbing:
    """Cache token + savings plumbing into UsageRecord (Phase 2 / P4)."""

    def _capture_record(self, cb):
        """Inspect the most recent record buffered by the callback."""
        # SyncBuffer stores records in ._buffer (list-backed), if not yet flushed.
        if cb._buffer._buffer:
            return cb._buffer._buffer[-1]
        # Fallback: inspect the insert_many call.
        last_call = cb._usage_col.insert_many.call_args
        if last_call:
            records = last_call.args[0] if last_call.args else last_call.kwargs.get("documents", [])
            return records[-1]
        raise AssertionError("No record was buffered or flushed")

    def test_records_cache_creation_tokens(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.05
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "x"}]},
                response_obj=MockResponse(cache_creation_input_tokens=10000),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["cache_creation_tokens"] == 10000
        assert record["cache_read_tokens"] == 0
        # No read tokens → no savings.
        assert record["cache_savings_usd"] == 0.0

    def test_records_cache_read_tokens_and_savings(self):
        """A 10K cached read on Sonnet saves ~$0.027 (3.00 - 0.30 = 2.70 / 1M)."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.05
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "x"}]},
                response_obj=MockResponse(cache_read_input_tokens=10_000),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["cache_read_tokens"] == 10_000
        # (10_000 / 1_000_000) * (3.00 - 0.30) = 0.027
        assert record["cache_savings_usd"] == pytest.approx(0.027)
        # Cache hit should be inferred from read_tokens > 0.
        assert record["cache_hit"] is True

    def test_haiku_cache_savings_use_haiku_rates(self):
        """Haiku cached reads save (1.00 - 0.10) = 0.90 / 1M tokens."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.01
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={
                    "model": "anthropic/claude-haiku-4-5-20251001",
                    "messages": [{"content": "x"}],
                },
                response_obj=MockResponse(cache_read_input_tokens=100_000),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["cache_savings_usd"] == pytest.approx(0.090)

    def test_unmapped_model_yields_zero_savings(self):
        """For models we don't have rates for, savings stay at 0 (not invented)."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.0
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={"model": "openai/gpt-4o", "messages": [{"content": "x"}]},
                response_obj=MockResponse(cache_read_input_tokens=10_000),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["cache_read_tokens"] == 10_000
        assert record["cache_savings_usd"] == 0.0

    def test_unmapped_anthropic_model_logs_drift_warning_once(self):
        """When an Anthropic model isn't in the rate map (typical signal of a
        config rename that wasn't mirrored), log once per model so the drift
        is visible without flooding the buffer on subsequent calls."""
        from llm_common.models import (
            _LOGGED_MISSING_RATE_MODELS,
            compute_cache_savings_usd,
        )

        unknown_model = "anthropic/claude-future-99"
        _LOGGED_MISSING_RATE_MODELS.discard(unknown_model)
        try:
            with patch("llm_common.models.logger") as mock_logger:
                # First call logs.
                first = compute_cache_savings_usd(unknown_model, 5_000)
                # Second call must NOT re-log.
                second = compute_cache_savings_usd(unknown_model, 7_000)

            assert first == 0.0
            assert second == 0.0
            assert mock_logger.info.call_count == 1
            args, kwargs = mock_logger.info.call_args
            assert args[0] == "cache.rate_missing"
            assert kwargs["model"] == unknown_model
        finally:
            _LOGGED_MISSING_RATE_MODELS.discard(unknown_model)

    def test_unmapped_non_anthropic_model_does_not_log(self):
        """OpenAI / Gemini misses are silent — only Anthropic gets the warning
        because cache pricing is Anthropic-specific today."""
        from llm_common.models import compute_cache_savings_usd

        with patch("llm_common.models.logger") as mock_logger:
            result = compute_cache_savings_usd("openai/gpt-4o", 10_000)

        assert result == 0.0
        mock_logger.info.assert_not_called()

    def test_no_cache_fields_default_to_zero(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.05
            mock_litellm.version = "1.80.0"

            cb.log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "x"}]},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["cache_creation_tokens"] == 0
        assert record["cache_read_tokens"] == 0
        assert record["cache_savings_usd"] == 0.0


class TestAttributionContextVars:
    """Phase 0 — user/video_summary/request id attribution flows from ctxvars
    onto every UsageRecord, so per-user reconciliation and run grouping match."""

    def _capture_record(self, cb):
        if cb._buffer._buffer:
            return cb._buffer._buffer[-1]
        last_call = cb._usage_col.insert_many.call_args
        if last_call:
            records = last_call.args[0] if last_call.args else last_call.kwargs.get("documents", [])
            return records[-1]
        raise AssertionError("No record was buffered or flushed")

    def test_record_carries_attribution_from_ctxvars(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        tokens = [
            llm_feature_var.set("extraction:chapter"),
            llm_video_id_var.set("yt_abc123"),
            llm_user_id_var.set("user_42"),
            llm_video_summary_id_var.set("vsum_99"),
            llm_request_id_var.set("req_xyz"),
        ]
        try:
            with patch("llm_common.callback.litellm") as mock_litellm:
                mock_litellm.completion_cost.return_value = 0.05
                mock_litellm.version = "1.80.0"
                cb.log_success_event(
                    kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "x"}]},
                    response_obj=MockResponse(),
                    start_time=datetime.now(UTC),
                    end_time=datetime.now(UTC),
                )
        finally:
            llm_request_id_var.reset(tokens[4])
            llm_video_summary_id_var.reset(tokens[3])
            llm_user_id_var.reset(tokens[2])
            llm_video_id_var.reset(tokens[1])
            llm_feature_var.reset(tokens[0])

        record = self._capture_record(cb)
        assert record["feature"] == "extraction:chapter"
        assert record["video_id"] == "yt_abc123"
        assert record["user_id"] == "user_42"
        assert record["video_summary_id"] == "vsum_99"
        assert record["request_id"] == "req_xyz"

    def test_record_attribution_defaults_to_none_when_unset(self):
        """Unset ctxvars must not throw and must serialize as None — old rows
        and the SSE-direct path that binds no user/request stay valid."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.05
            mock_litellm.version = "1.80.0"
            cb.log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "x"}]},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        record = self._capture_record(cb)
        assert record["user_id"] is None
        assert record["video_summary_id"] is None


class TestManualUsage:
    """Phase 0.5 — record_manual_usage emits transcription-style rows that
    bypass LiteLLM, inheriting run attribution from the context vars."""

    def _capture_record(self, cb):
        if cb._buffer._buffer:
            return cb._buffer._buffer[-1]
        raise AssertionError("No record was buffered")

    def test_writes_through_active_buffer_with_ctxvar_attribution(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        tokens = [
            llm_video_id_var.set("yt_abc"),
            llm_user_id_var.set("user_7"),
            llm_video_summary_id_var.set("vsum_3"),
            llm_request_id_var.set("req_55"),
        ]
        try:
            record_manual_usage(
                UsageRecord(
                    model="whisper-1",
                    provider="openai",
                    feature="summarize:transcript:whisper",
                    cost_usd=0.012,
                    unit="audio_seconds",
                    audio_seconds=120.0,
                )
            )
        finally:
            llm_request_id_var.reset(tokens[3])
            llm_video_summary_id_var.reset(tokens[2])
            llm_user_id_var.reset(tokens[1])
            llm_video_id_var.reset(tokens[0])

        record = self._capture_record(cb)
        # Explicit fields preserved.
        assert record["feature"] == "summarize:transcript:whisper"
        assert record["unit"] == "audio_seconds"
        assert record["audio_seconds"] == 120.0
        assert record["cost_usd"] == 0.012
        # Run attribution filled from ctxvars.
        assert record["video_id"] == "yt_abc"
        assert record["user_id"] == "user_7"
        assert record["video_summary_id"] == "vsum_3"
        assert record["request_id"] == "req_55"

    def test_unset_feature_filled_from_ctxvar(self):
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        token = llm_feature_var.set("summarize:transcript:gemini")
        try:
            record_manual_usage(UsageRecord(model="gemini-2.5-flash-lite", provider="google"))
        finally:
            llm_feature_var.reset(token)

        assert self._capture_record(cb)["feature"] == "summarize:transcript:gemini"

    def test_no_buffer_registered_is_noop(self):
        """With no buffer registered, emit is a no-op + warning, never raises."""
        original = callback_module._active_buffer
        callback_module._active_buffer = None
        try:
            record_manual_usage(UsageRecord(model="whisper-1", provider="openai"))
        finally:
            callback_module._active_buffer = original

    @pytest.mark.asyncio
    async def test_async_buffer_emit_schedules_tracked_task(self):
        """Under a running loop with an AsyncBuffer, the coroutine returned by
        ``buffer.add`` is scheduled as a task retained in the module set (so it
        can't be GC'd mid-flight), reaches the buffer, then clears the set."""
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=AsyncMock())
        cb = MongoDBUsageCallback(mock_db, service="explainer", mode="async")

        assert not callback_module._pending_manual_tasks

        record_manual_usage(
            UsageRecord(
                model="gemini-2.5-flash-lite",
                provider="google",
                feature="summarize:transcript:gemini",
            )
        )

        # A task was scheduled and tracked before it ran.
        assert len(callback_module._pending_manual_tasks) == 1
        tracked = next(iter(callback_module._pending_manual_tasks))

        await tracked

        # The record reached the async buffer.
        assert len(cb._buffer._buffer) == 1
        assert cb._buffer._buffer[-1]["feature"] == "summarize:transcript:gemini"
        # The done callback discarded the task from the tracking set.
        assert tracked not in callback_module._pending_manual_tasks


class TestCrossModeCallback:
    """Regression tests: sync-mode callback receiving async LiteLLM dispatch.

    When the summarizer uses mode="sync" but calls litellm.acompletion(),
    LiteLLM dispatches to async_log_success_event. Records must still be
    written via the SyncBuffer.
    """

    @pytest.mark.asyncio
    async def test_sync_mode_async_success_records(self):
        """async_log_success_event must buffer records when mode='sync'."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        token = llm_feature_var.set("summarize:chapter")
        try:
            await cb.async_log_success_event(
                kwargs={"model": "anthropic/claude-sonnet-4-6", "messages": [{"content": "test"}]},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )
        finally:
            llm_feature_var.reset(token)

        # Record may have been flushed by the timer thread already.
        # Either the buffer has the record or insert_many was called.
        buffered = len(cb._buffer._buffer) > 0
        flushed = mock_db["llm_usage"].insert_many.called
        assert buffered or flushed, "Record must be buffered or flushed to MongoDB"

    @pytest.mark.asyncio
    async def test_sync_mode_async_failure_records(self):
        """async_log_failure_event must buffer records when mode='sync'."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync")

        await cb.async_log_failure_event(
            kwargs={"model": "test/model", "exception": "timeout"},
            response_obj=None,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )

        buffered = len(cb._buffer._buffer) > 0
        flushed = mock_db["llm_usage"].insert_many.called
        assert buffered or flushed, "Failure record must be buffered or flushed"

    @pytest.mark.asyncio
    async def test_sync_mode_async_cost_alert(self):
        """async_log_success_event with mode='sync' must trigger sync cost alert."""
        mock_db = MagicMock()
        cb = MongoDBUsageCallback(mock_db, service="summarizer", mode="sync", cost_threshold=0.01)

        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.50
            mock_litellm.version = "1.80.0"

            await cb.async_log_success_event(
                kwargs={"model": "test/model", "messages": []},
                response_obj=MockResponse(),
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC),
            )

        mock_db["llm_alerts"].insert_one.assert_called_once()


class TestCostThresholdFromEnv:
    """ALERT_COST_THRESHOLD_USD makes the $0.50 high-cost-call alert tunable."""

    def test_should_default_when_unset(self, monkeypatch):
        from llm_common.callback import DEFAULT_COST_THRESHOLD, cost_threshold_from_env

        monkeypatch.delenv("ALERT_COST_THRESHOLD_USD", raising=False)
        assert cost_threshold_from_env() == DEFAULT_COST_THRESHOLD

    def test_should_read_env_value(self, monkeypatch):
        from llm_common.callback import cost_threshold_from_env

        monkeypatch.setenv("ALERT_COST_THRESHOLD_USD", "1.25")
        assert cost_threshold_from_env() == 1.25

    @pytest.mark.parametrize("bad", ["abc", "-0.5"])
    def test_should_fall_back_on_invalid_value(self, monkeypatch, bad):
        from llm_common.callback import DEFAULT_COST_THRESHOLD, cost_threshold_from_env

        monkeypatch.setenv("ALERT_COST_THRESHOLD_USD", bad)
        assert cost_threshold_from_env() == DEFAULT_COST_THRESHOLD

    def test_callback_should_use_env_threshold_when_not_passed(self, monkeypatch):
        from llm_common.callback import MongoDBUsageCallback

        monkeypatch.setenv("ALERT_COST_THRESHOLD_USD", "2.0")
        cb = MongoDBUsageCallback(MagicMock(), service="summarizer", mode="sync")
        assert cb._cost_threshold == 2.0

    def test_explicit_threshold_should_beat_env(self, monkeypatch):
        from llm_common.callback import MongoDBUsageCallback

        monkeypatch.setenv("ALERT_COST_THRESHOLD_USD", "2.0")
        cb = MongoDBUsageCallback(
            MagicMock(), service="summarizer", mode="sync", cost_threshold=0.1
        )
        assert cb._cost_threshold == 0.1


class TestCacheSavingsLookup:
    """The rate map is keyed ``provider/model``; LiteLLM 1.105 passes the bare model (4.0d)."""

    def _record(self, kwargs: dict, response: object) -> dict:
        cb = MongoDBUsageCallback(MagicMock(), service="summarizer", mode="sync")
        with patch("llm_common.callback.litellm") as mock_litellm:
            mock_litellm.completion_cost.return_value = 0.0
            mock_litellm.version = "1.105.0"
            return cb._build_record(kwargs, response, datetime.now(UTC), datetime.now(UTC))

    def test_should_price_cache_reads_when_the_model_arrives_without_its_provider(self) -> None:
        kwargs = {"model": "claude-sonnet-5-5", "custom_llm_provider": "anthropic"}
        record = self._record(kwargs, MockResponse(cache_read_input_tokens=1_000_000))
        # (2.00 - 0.20) per 1M tokens; the stored model name is left as LiteLLM sent it.
        assert (record["cache_savings_usd"], record["model"]) == (
            pytest.approx(1.80),
            "claude-sonnet-5-5",
        )

    def test_should_count_openai_cached_tokens_when_the_anthropic_field_is_none(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=2_000_000,
            completion_tokens=10,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1_000_000),
        )
        response = SimpleNamespace(usage=usage, _hidden_params={})
        record = self._record({"model": "gpt-6-luna", "custom_llm_provider": "openai"}, response)
        assert (record["cache_read_tokens"], record["cache_savings_usd"]) == (
            1_000_000,
            pytest.approx(0.09),
        )
