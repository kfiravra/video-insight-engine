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
