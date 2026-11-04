"""Run attribution in the pipeline entry (billing-roles P0.4 / B2).

``stream_summarization`` stamps the run's user and request id on the cost
ContextVars (``llm_user_id_var`` / ``llm_request_id_var``) before the cache
lookup. Precedence: the queue worker's structlog contextvars name the true
owner of THIS run and win; the cache row's ``userId`` / ``requestId`` (stamped
by the last ``POST /summarize``) are the fallback — the only owner an SSE
producer has. The one exception is the request id on an HTTP-bound context,
where the middleware's fresh UUID must not shadow the dispatching request's id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import structlog
from llm_common.context import llm_request_id_var, llm_user_id_var

from src.routes import pipeline_runner, run_events

_CACHED = {
    "status": "completed",
    "youtubeId": "yt_attribution",
    "tabs": [{"id": "overview"}],
    "meta": {"contentTags": ["learning"]},
}


async def _run_cache_hit(entry: dict[str, Any], **bound: str) -> dict[str, str | None]:
    """Drive the Redis cache-hit path and snapshot the cost ContextVars it set."""
    captured: dict[str, str | None] = {}

    async def fake_get_response(_youtube_id: str) -> dict:
        return _CACHED

    async def capture_then_stream(_vsid: str, _cached: dict):
        captured["user_id"] = llm_user_id_var.get()
        captured["request_id"] = llm_request_id_var.get()
        yield "data: {}\n\n"

    structlog.contextvars.bind_contextvars(**bound)
    try:
        with (
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=capture_then_stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", entry, MagicMock(), MagicMock()
                )
            ]
    finally:
        structlog.contextvars.unbind_contextvars(*bound)
    return captured


def _entry(**row: str) -> dict[str, Any]:
    return {"youtubeId": "yt_attribution", "status": "processing", **row}


class TestUserAttribution:
    async def test_should_prefer_the_worker_user_over_the_row_user(self):
        captured = await _run_cache_hit(_entry(userId="row_user"), user_id="worker_user")

        assert captured["user_id"] == "worker_user"

    async def test_should_fall_back_to_the_row_user_when_none_is_bound(self):
        captured = await _run_cache_hit(_entry(userId="row_user"), request_id="req-sse-1")

        assert captured["user_id"] == "row_user"


class TestRequestIdAttribution:
    async def test_should_prefer_the_worker_request_id_over_the_row_request_id(self):
        captured = await _run_cache_hit(
            _entry(requestId="row-req-1"), request_id="worker-req-1", user_id="worker_user"
        )

        assert captured["request_id"] == "worker-req-1"

    async def test_should_fall_back_to_the_row_request_id_when_none_is_bound(self):
        captured = await _run_cache_hit(_entry(requestId="row-req-1"))

        assert captured["request_id"] == "row-req-1"

    async def test_should_prefer_the_row_request_id_on_an_http_bound_context(self):
        # The SSE GET's middleware binds a fresh UUID plus method/path.
        captured = await _run_cache_hit(
            _entry(requestId="row-req-1"),
            request_id="fresh-sse-uuid",
            method="GET",
            path="/summarize/stream/vsum_attr",
        )

        assert captured["request_id"] == "row-req-1"

    async def test_should_use_the_http_request_id_when_the_row_has_none(self):
        captured = await _run_cache_hit(
            _entry(), request_id="fresh-sse-uuid", method="GET", path="/summarize/stream/x"
        )

        assert captured["request_id"] == "fresh-sse-uuid"


class TestRunIdsOnTheLedger:
    """D9: the attempt's run, trace and session ids reach every ``llm_usage`` row."""

    async def test_should_bind_the_attempt_ids_before_the_cache_lookup(self):
        from llm_common.context import usage_ids
        from llm_common.observability import trace_id_for_run

        captured: dict[str, Any] = {}

        async def fake_get_response(_youtube_id: str) -> dict:
            captured.update(usage_ids())
            return _CACHED

        async def stream(_vsid: str, _cached: dict):
            yield "data: {}\n\n"

        entry = _entry(currentRunId="run-ledger-1")
        entry["runs"] = [
            {
                "runId": "run-ledger-1",
                "status": "dispatched",
                "attempt": 1,
                "chatSessionId": "chat-9",
            }
        ]
        with (
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", entry, MagicMock(), MagicMock()
                )
            ]

        assert captured["run_id"] == "run-ledger-1"
        assert captured["trace_id"] == trace_id_for_run("run-ledger-1")
        assert captured["attempt"] == 1
        assert captured["session_id"] == "chat-9"
        assert captured["pipeline_version"] == pipeline_runner.settings.PIPELINE_VERSION
        assert captured["bypass"] is False

    async def test_should_not_keep_a_previous_runs_user_when_this_run_has_none(self):
        token = llm_user_id_var.set("previous-run-user")
        try:
            captured = await _run_cache_hit(_entry())
        finally:
            llm_user_id_var.reset(token)
        assert captured["user_id"] is None

    def test_should_clear_the_run_ids_between_attempts(self):
        from llm_common.context import usage_ids

        pipeline_runner._bind_run(None)

        ids = usage_ids()
        assert [ids[k] for k in ("run_id", "trace_id", "attempt", "session_id", "bypass")] == [
            None
        ] * 5
        assert "run_id" not in structlog.contextvars.get_contextvars()

    def test_should_bind_the_run_and_chat_session_on_log_lines(self):
        from llm_common.observability import trace_id_for_run

        from src.services.pipeline.run_registry import RunIdentity

        run = RunIdentity(
            run_id="run-log-2",
            trace_id=trace_id_for_run("run-log-2"),
            attempt=1,
            chat_session_id="chat-7",
        )
        try:
            pipeline_runner._bind_run(run)
            bound = structlog.contextvars.get_contextvars()
            assert (bound.get("run_id"), bound.get("session_id")) == ("run-log-2", "chat-7")
        finally:
            pipeline_runner._bind_run(None)
        assert "session_id" not in structlog.contextvars.get_contextvars()


class TestRunEvents:
    """D12: one `pipeline_run_finished` line per attempt (and per cache hit) for Grafana."""

    @staticmethod
    def _finished(events: MagicMock) -> dict[str, Any]:
        calls = [c for c in events.info.call_args_list if c.args[0] == "pipeline_run_finished"]
        assert len(calls) == 1
        return calls[0].kwargs

    async def _run(self, entry: dict[str, Any], phases, events: MagicMock) -> list[str]:
        """Drive a registered attempt with every phase and side effect stubbed."""
        from contextlib import asynccontextmanager

        from llm_common.observability import trace_id_for_run

        from src.services.pipeline.run_registry import RunIdentity

        run = RunIdentity(run_id="run-full-1", trace_id=trace_id_for_run("run-full-1"), attempt=1)

        @asynccontextmanager
        async def no_trace(**_kwargs):
            yield None

        with (
            patch.object(run_events, "_events", events),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
            patch.object(pipeline_runner, "_register_run", AsyncMock(return_value=run)),
            patch.object(pipeline_runner, "run_trace", no_trace),
            patch.object(pipeline_runner, "_mark_processing", AsyncMock()),
            patch.object(pipeline_runner, "PipelineContext", MagicMock()),
            patch.object(pipeline_runner, "LocalLowresSource", MagicMock()),
            patch.object(pipeline_runner, "LocalHiresSource", MagicMock()),
            patch.object(pipeline_runner, "run_pipeline_phases", phases),
            patch.object(pipeline_runner, "send_video_status", AsyncMock()),
            patch.object(pipeline_runner, "clear_override", MagicMock()),
        ):
            return [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", entry, MagicMock(), MagicMock(), run_path="worker"
                )
            ]

    async def test_should_report_a_completed_run_after_its_start(self):
        events = MagicMock()

        async def phases(*_args):
            yield "data: {}\n\n"

        await self._run(_entry(), phases, events)

        names = [c.args[0] for c in events.info.call_args_list]
        assert names == ["pipeline_run_started", "pipeline_run_finished"]
        fields = self._finished(events)
        assert (fields["status"], fields["run_id"], fields["path"], fields["error_code"]) == (
            "completed",
            "run-full-1",
            "worker",
            None,
        )

    async def test_should_report_a_failed_run_with_its_error_code(self):
        events = MagicMock()

        async def phases(*_args):
            raise RuntimeError("extraction blew up")
            yield  # pragma: no cover — makes this an async generator

        await self._run(_entry(), phases, events)

        fields = self._finished(events)
        assert fields["status"] == "failed"
        assert fields["error_code"]

    async def test_should_write_no_run_event_when_the_run_fails_before_registering(self):
        events = MagicMock()

        async def phases(*_args):
            yield "data: {}\n\n"  # pragma: no cover

        with patch.object(
            pipeline_runner, "_bind_usage_context", MagicMock(side_effect=RuntimeError("mongo"))
        ):
            await self._run(_entry(), phases, events)

        events.info.assert_not_called()

    async def test_should_report_a_cache_hit_as_a_finished_cache_hit(self):
        events = MagicMock()

        async def fake_get_response(_youtube_id: str) -> dict:
            return _CACHED

        async def stream(_vsid: str, _cached: dict):
            yield "data: {}\n\n"

        with (
            patch.object(run_events, "_events", events),
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", _entry(currentRunId="run-hit-1"), MagicMock(), MagicMock()
                )
            ]

        fields = self._finished(events)
        assert (fields["status"], fields["cache_hit"], fields["run_id"]) == (
            "cache_hit",
            True,
            "run-hit-1",
        )
        assert isinstance(fields["duration_ms"], int)

    async def test_should_write_no_run_event_when_the_row_has_no_video(self):
        events = MagicMock()
        with patch.object(run_events, "_events", events):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_attr", {"status": "processing"}, MagicMock(), MagicMock()
                )
            ]

        events.info.assert_not_called()

    def test_should_name_the_attempt_when_the_run_starts(self):
        from llm_common.observability import trace_id_for_run

        from src.services.pipeline.run_registry import RunIdentity

        events = MagicMock()
        run = RunIdentity(
            run_id="run-s1", trace_id=trace_id_for_run("run-s1"), attempt=2, retry_of="run-s0"
        )
        with patch.object(run_events, "_events", events):
            run_events.log_run_started(
                run, video_summary_id="vs-1", youtube_id="yt-1", run_path="worker", bypass=True
            )

        assert events.info.call_args.args == ("pipeline_run_started",)
        assert events.info.call_args.kwargs == {
            "run_id": "run-s1",
            "video_summary_id": "vs-1",
            "youtube_id": "yt-1",
            "path": "worker",
            "attempt": 2,
            "bypass": True,
            "retry_of": "run-s0",
        }

    def test_should_round_the_duration_to_milliseconds(self):
        events = MagicMock()
        with patch.object(run_events, "_events", events):
            run_events.log_run_finished(
                "failed",
                run_id="run-f1",
                video_summary_id="vs-1",
                youtube_id="yt-1",
                duration_s=12.3456,
                run_path="sse",
                error_code="TRANSCRIPT_UNAVAILABLE",
            )

        fields = events.info.call_args.kwargs
        assert (fields["cache_hit"], fields["duration_ms"], fields["error_code"]) == (
            False,
            12345,
            "TRANSCRIPT_UNAVAILABLE",
        )
