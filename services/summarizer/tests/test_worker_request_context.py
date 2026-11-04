"""Worker binds request_id (+ videoId) to structlog contextvars per job.

The runner is in the request-id propagation chain: API publishes with
``requestId``, the worker pulls the payload, and every log line and Langfuse
trace emitted during pipeline execution must surface that id so support can
follow a single user-reported failure across services.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from src.worker.payload import VideoJobPayload
from src.worker.runner import JobOutcome, WorkerRunner


VALID_PAYLOAD = {
    "videoSummaryId": "abc123def456789012345678",
    "youtubeId": "dQw4w9WgXcQ",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "userId": "user-42",
    "plan": "free",
    "priority": 1,
    "providers": None,
    "bypassCache": False,
    "requestId": "ctx-binding-test",
    "attempt": 1,
    "createdAt": "2026-05-18T12:00:00Z",
}


def _make_message(body: bytes):
    msg = MagicMock()
    msg.body = body
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.reject = AsyncMock()
    return msg


@pytest.fixture(autouse=True)
def _clear_contextvars():
    """Guarantee a clean slate per test — bound vars survive across tests otherwise."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


class TestRequestContextBinding:
    async def test_should_bind_request_id_during_pipeline_call(self):
        captured: dict[str, object] = {}

        async def capturing_pipeline(_payload: VideoJobPayload) -> None:
            captured.update(structlog.contextvars.get_contextvars())

        runner = WorkerRunner(
            run_pipeline=capturing_pipeline,
            republish=AsyncMock(),
            max_retries=3,
        )
        message = _make_message(json.dumps(VALID_PAYLOAD).encode("utf-8"))

        outcome = await runner.process_message(message)

        assert outcome == JobOutcome.SUCCESS
        assert captured.get("request_id") == "ctx-binding-test"
        assert captured.get("video_summary_id") == "abc123def456789012345678"
        assert captured.get("youtube_id") == "dQw4w9WgXcQ"

    async def test_should_bind_run_and_session_ids_on_log_lines(self):
        captured: dict[str, object] = {}

        async def capturing_pipeline(_payload: VideoJobPayload) -> None:
            captured.update(structlog.contextvars.get_contextvars())

        runner = WorkerRunner(run_pipeline=capturing_pipeline, republish=AsyncMock(), max_retries=3)
        body = {**VALID_PAYLOAD, "runId": "run-log-1", "chatSessionId": "chat-log-1"}

        await runner.process_message(_make_message(json.dumps(body).encode("utf-8")))

        assert (captured.get("run_id"), captured.get("session_id")) == ("run-log-1", "chat-log-1")

    async def test_should_bind_no_session_for_a_run_not_started_from_chat(self):
        captured: dict[str, object] = {}

        async def capturing_pipeline(_payload: VideoJobPayload) -> None:
            captured.update(structlog.contextvars.get_contextvars())

        runner = WorkerRunner(run_pipeline=capturing_pipeline, republish=AsyncMock(), max_retries=3)

        await runner.process_message(_make_message(json.dumps(VALID_PAYLOAD).encode("utf-8")))

        assert "session_id" not in captured

    async def test_should_clear_contextvars_after_message(self):
        runner = WorkerRunner(
            run_pipeline=AsyncMock(),
            republish=AsyncMock(),
            max_retries=3,
        )
        message = _make_message(json.dumps(VALID_PAYLOAD).encode("utf-8"))

        await runner.process_message(message)

        # Next job must start clean — otherwise a long-running worker process
        # would leak ids from the previous request into subsequent log lines.
        assert structlog.contextvars.get_contextvars() == {}

    async def test_should_clear_contextvars_after_pipeline_failure(self):
        async def failing_pipeline(_payload: VideoJobPayload) -> None:
            raise RuntimeError("kaboom")

        runner = WorkerRunner(
            run_pipeline=failing_pipeline,
            republish=AsyncMock(),
            max_retries=3,
        )
        message = _make_message(json.dumps(VALID_PAYLOAD).encode("utf-8"))

        await runner.process_message(message)

        assert structlog.contextvars.get_contextvars() == {}


class TestUsageIdScope:
    """D9: a job's ledger ids are its own and do not outlive it (the worker inheritance bug)."""

    async def test_should_bind_the_job_ids_for_the_ledger_during_the_pipeline(self):
        from llm_common.context import bind_usage_ids, usage_ids

        captured: dict[str, object] = {}

        async def capturing_pipeline(_payload: VideoJobPayload) -> None:
            captured.update(usage_ids())

        runner = WorkerRunner(run_pipeline=capturing_pipeline, republish=AsyncMock(), max_retries=3)
        tokens = bind_usage_ids(run_id="previous-job-run")
        try:
            await runner.process_message(_make_message(json.dumps(VALID_PAYLOAD).encode("utf-8")))
        finally:
            from llm_common.context import llm_run_id_var

            llm_run_id_var.reset(tokens[0])

        assert captured["request_id"] == "ctx-binding-test"
        assert captured["video_id"] == "dQw4w9WgXcQ"
        assert captured["video_summary_id"] == "abc123def456789012345678"
        assert captured["user_id"] == "user-42"
        # The attempt's ids are the runner's to bind; the job starts with none.
        assert captured["run_id"] is None

    async def test_should_restore_the_ledger_ids_after_the_job(self):
        from llm_common.context import bind_usage_ids, usage_ids

        async def pipeline_that_rebinds(_payload: VideoJobPayload) -> None:
            # The runner re-binds mid-run without reset tokens.
            bind_usage_ids(user_id="row-owner", run_id="fresh-run")

        runner = WorkerRunner(
            run_pipeline=pipeline_that_rebinds, republish=AsyncMock(), max_retries=3
        )

        await runner.process_message(_make_message(json.dumps(VALID_PAYLOAD).encode("utf-8")))

        assert set(usage_ids().values()) == {None}

    async def test_should_restore_the_ledger_ids_after_a_failed_job(self):
        from llm_common.context import bind_usage_ids, usage_ids

        async def failing_pipeline(_payload: VideoJobPayload) -> None:
            bind_usage_ids(run_id="fresh-run")
            raise RuntimeError("kaboom")

        runner = WorkerRunner(run_pipeline=failing_pipeline, republish=AsyncMock(), max_retries=3)

        await runner.process_message(_make_message(json.dumps(VALID_PAYLOAD).encode("utf-8")))

        assert set(usage_ids().values()) == {None}
